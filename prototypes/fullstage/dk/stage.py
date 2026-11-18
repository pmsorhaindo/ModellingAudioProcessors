"""One 12AX7 common-cathode stage, nodal DK method, with Drive / Bias / Output and a quality setting.

Parameters (all our choices, documented here):
  drive   : linear gain applied to the input voltage before the stage (1.0 = signal as given, in volts).
  bias    : scale factor on the cathode resistor Rk (1.0 = 1.5 kOhm). Larger -> colder bias (more
            negative Vgk). Changing it rebuilds the matrices and re-solves the operating point, so it
            is a "set before processing" control in this prototype (no smoothing yet).
  output  : linear gain applied to the stage output.
  quality : QUALITY key (#17), or an (os, prewarp_hz) pair for experiments. Factors stay fixed at every host
            rate (#74, RESULTS.md); numerical examples here are the 48 kHz baseline:
              "hq"       16x                         ~15 dB less aliasing than standard
              "standard" 8x (default)                trapezoidal warping 0.05 dB at 20 kHz
              "eco"      4x + small-cap pre-warp     plain 4x misses the 0.1 dB small-signal target at 20 kHz
                                                     (0.21 dB, bilinear warping); pre-warping the valve
                                                     capacitances (circuit.py) fixes that at no runtime cost
            PREWARP_HZ is the frequency that minimises the worst small-signal deviation 20 Hz-20 kHz at 4x
            (validate.prewarp_minimax; see RESULTS.md).
  filt    : resampler, orthogonal to quality: "fir" (default) linear-phase half-band FIR, "iir" polyphase
            allpass (HIIR design), ~5 samples latency and ~1/5 of the multiplies, non-linear phase near 20 kHz.
  triode_math : "libm" (default) or "fast" (#18): the triode's exp / log1p / pow from libm or from dk/fastmath.py
            (the arithmetic of the C fast path, dk/fastmath.h; slower in Python). Both pass the same criteria.
The stage runs at os*fs between a streaming half-band cascade (dk/resample.py). process() keeps the filter
state, so it can be called block by block; the output is delayed by `latency_samples` (base rate) instead of
being trimmed (#14). `resampler_macs` is the resampler's multiplies per base-rate sample (up, down).
"""
import warnings
import numpy as np
from . import circuit
from .solver import solve, MAX_IT, TOL_V, TOL_F
from .triode import DZ_12AX7_RSD1, EVAL
from .resample import Resampler

PREWARP_HZ = 17300.0
QUALITY = {"hq": (16, None), "standard": (8, None), "eco": (4, PREWARP_HZ)}   # (os, prewarp_hz)


class OperatingPointError(RuntimeError):
    """The DC operating-point solve failed (#7). Raised, not asserted, so it also fires under python -O."""


class NonConvergenceWarning(RuntimeWarning):
    """Newton did not converge on some samples of a block (#7); those samples kept the solver's best finite
    iterate (dk/solver.py). Counted in Stage.nonconv / nonconv_block and iteration_stats()."""


class Stage:
    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, triode=DZ_12AX7_RSD1, quality="standard", filt="fir",
                 newton=None, triode_math="libm"):
        # newton: optional solver overrides {max_it, tol_v, tol_f} (dk/solver.py defaults), for tests and experiments
        nw = dict(max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F)
        if set(newton or {}) - set(nw):
            raise TypeError(f"unknown newton option(s) {sorted(set(newton) - set(nw))}, expected {sorted(nw)}")
        nw.update(newton or {})
        self.newton = (nw["max_it"], nw["tol_v"], nw["tol_f"])
        if triode_math not in EVAL:
            raise ValueError(f"triode_math must be one of {sorted(EVAL)}, got {triode_math!r}")
        self.triode_math, self.ev = triode_math, EVAL[triode_math]
        self.fs_base = fs
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        self.rs = Resampler(fs, self.os, filt)
        self.fs = fs * self.os
        self.drive, self.output, self.bias, self.par = drive, output, bias, triode
        self.m = circuit.build(self.fs, rk_scale=bias, prewarp_hz=self.prewarp_hz)
        self.K = self.m["K"].tolist()
        self.dc = self.operating_point()
        self.reset()

    # ---- DC operating point: capacitors open, same NR solver, started from cutoff-ish guess
    def operating_point(self):
        m0 = circuit.build(self.fs, rk_scale=self.bias, dc=True)
        u = np.array([0.0, circuit.VALUES["HT"]])
        p = m0["H"] @ u
        K0 = m0["K"].tolist()
        v0, v1 = -1.0, 200.0
        # source-stepping on HT for robustness: ramp the supply in 10 steps
        for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
            v0, v1, ig, ia, it, ok = solve(p[0] * s, p[1] * s, K0, v0, v1, self.par, *self.newton, ev=self.ev)
            if not ok:
                raise OperatingPointError(
                    f"DC operating point did not converge at source step {k}/10 (HT x {s:.1f}): "
                    f"{it} Newton iterations (max_it, tol_v, tol_f = {self.newton}), "
                    f"best iterate Vgk={v0:.6g} V, Vak={v1:.6g} V, bias={self.bias}")
        inl = np.array([ig, ia])
        w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nnp"].T @ inl)
        nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
        return dict(vgk=v0, vak=v1, ig=ig, ia=ia, nodes=nodes, w=w)

    def _rest(self):
        """The operating-point constants the deviation-form recursion runs around (#70), from self.m and self.dc.
        The state runs as the DEVIATION from the DC operating point: capacitor history X = x0 + x, valve currents
        i = i0 + di, and only vin enters the recursion (HT and the bias currents are folded into p_dc and y_dc).
        Silence then keeps x = 0, di at the rounding of the rest problem, and out at y_dc = the DC solve's out node
        (0 V). With the absolute history state the constant HT = 300 V and the bias currents go through the
        discretised map's near-singular DC path ((I - A)^-1 ~ fs * tau): the ~1e-13 relative rounding of the built
        matrices left a residual DC gain HT -> out of ~8e-9 at hq, so silence settled to a rest point ~1.6 uV off
        the operating point (#70, measured in the PR). In exact arithmetic the two forms are the same recursion."""
        m, dc = self.m, self.dc
        vn = dc["w"][:len(circuit.NODES)]
        self.x0 = m["Gx"] @ m["Nx"] @ vn         # absolute capacitor history so that every i_c = 0 at t=0
        self.i0 = np.array([dc["ig"], dc["ia"]])
        self.p_dc = np.array([dc["vgk"], dc["vak"]]) + m["K"] @ self.i0   # the DC point solves p_dc exactly
        self.y_dc = np.array([dc["nodes"][n] for n in m["out_names"]])     # the DC node voltages (out = 0 V)

    def reset(self):
        self._rest()
        self.x = np.zeros_like(self.x0)        # deviation from x0 (see _rest)
        self.v = [self.dc["vgk"], self.dc["vak"]]
        self.v_prev = list(self.v)              # solution one sample before self.v, for the predictor
        self.iters = []
        self.nonconv = 0                        # non-converged internal-rate samples since reset()
        self.nonconv_block = 0                  # ... in the last process() / process_internal() call
        self.rs.reset()

    def swap_bias(self, bias):
        """The C++ core's live bias swap (cpp/dk_core.h request_bias): new matrices and DC operating point for the new
        Rk scale, the circuit state kept. The absolute history x0 + x stays where it is and is re-expressed as the
        deviation from the new x0 (the same expression as the core). The DC solve uses the current Newton settings, as
        request_bias does (it builds with the active pack's). Raises OperatingPointError, state unchanged, if it
        fails."""
        m = circuit.build(self.fs, rk_scale=bias, prewarp_hz=self.prewarp_hz)
        bias_old, self.bias = self.bias, bias
        try:
            dc = self.operating_point()
        except OperatingPointError:
            self.bias = bias_old
            raise
        x0_old = self.x0
        self.m, self.K, self.dc = m, m["K"].tolist(), dc
        self._rest()
        self.x = self.x + (x0_old - self.x0)

    @property
    def latency_samples(self):
        """Base-rate delay of process() output vs input (the resampler's; fir exact, iir nominal)."""
        return self.rs.latency_samples

    @property
    def resampler_macs(self):
        """Resampler multiplies per base-rate sample, (up, down)."""
        return self.rs.macs()

    def process_internal(self, vin, record=("out",)):
        """Run the stage at the internal rate on an input already at self.fs. Returns dict of signals.
        A sample on which Newton hits the cap keeps the solver's best finite iterate and is counted: see
        nonconv, nonconv_block and iteration_stats(). A NonConvergenceWarning is issued once per call."""
        m = self.m
        A, C, D, F, G = (m[k] for k in "ACDFG")
        B0, E0, H0 = m["B"][:, 0], m["E"][:, 0], m["H"][:, 0]      # the vin columns; HT is in p_dc, y_dc (_rest)
        p_dc, y_dc, (ig0, ia0) = self.p_dc, self.y_dc, self.i0
        rec = {n: np.empty(len(vin)) for n in record}
        idx = {n: m["out_names"].index(n) for n in record if n in m["out_names"]}
        x, (v0, v1), K, par, nw, ev = self.x, self.v, self.K, self.par, self.newton, self.ev
        q0, q1 = self.v_prev
        iters = np.empty(len(vin), dtype=np.int16)
        bad = 0
        for n, s in enumerate(vin):
            p = G @ x + H0 * s + p_dc
            # warm start from the linear extrapolation of the last two solutions (#15): at 8x the
            # signal is smooth per sample, so this lands within ~1e-6 V more often than v[n-1] does
            e0, e1 = 2.0 * v0 - q0, 2.0 * v1 - q1
            q0, q1 = v0, v1
            v0, v1, ig, ia, it, ok = solve(p[0], p[1], K, e0, e1, par, *nw, ev=ev)
            iters[n] = it
            if not ok:
                bad += 1
            inl = np.array([ig - ig0, ia - ia0])        # deviation from the operating point
            if idx:
                y = D @ x + E0 * s + F @ inl + y_dc
                for nm, j in idx.items():
                    rec[nm][n] = y[j]
            if "vgk" in rec:
                rec["vgk"][n] = v0
            x = A @ x + B0 * s + C @ inl
        self.x, self.v, self.v_prev = x, [v0, v1], [q0, q1]
        self.iters.append(iters)
        self.nonconv += bad
        self.nonconv_block = bad
        if bad:
            warnings.warn(f"Newton did not converge on {bad} of {len(vin)} samples at {self.fs:g} Hz "
                          f"(max_it, tol_v, tol_f = {self.newton}); kept the best finite iterate",
                          NonConvergenceWarning, stacklevel=2)
        return rec

    def process(self, vin, record=("out",)):
        """Base-rate in, base-rate out (with self.os-times oversampling around the stage). Stateful: call it
        on consecutive blocks of any length; the output lags the input by self.latency_samples."""
        vin = self.drive * np.asarray(vin, dtype=float)
        rec = self.process_internal(self.rs.up(vin), record)
        out = {}
        for k, v in rec.items():
            y = self.rs.down(v, channel=k)
            out[k] = self.output * y if k == "out" else y
        out["_internal"] = rec
        return out

    def iteration_stats(self):
        it = np.concatenate(self.iters) if self.iters else np.zeros(0, int)
        hist = np.bincount(it) if len(it) else np.zeros(0)
        return dict(samples=int(len(it)), mean=float(it.mean()) if len(it) else 0.0,
                    max=int(it.max()) if len(it) else 0, nonconverged=int(self.nonconv),
                    converged=self.nonconv == 0,
                    histogram={int(i): int(c) for i, c in enumerate(hist) if c})
