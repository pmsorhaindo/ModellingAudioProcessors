"""One 6AS6 stage, nodal DK method (issues #26, #102): dk/stage.py's stage with the pentode.

Same controls and streaming resampler as the 12AX7 stage (dk/stage.py, which this subclasses for process(),
latency, the resampler and iteration_stats()):
  drive, output : linear gains before / after the stage
  bias          : target DC cathode current Ik = Ip + Ig2, in mA: what the unit's meter reads (default BIAS_MA = 0.45,
                  the manuals' range is 0.1-0.6 mA; #102). The cathode is an unbypassed rheostat to ground:
                  cathode_resistor() solves the Rk that gives this current at the DC operating point, and Rk is then
                  a fixed resistor for the run (Stage.rk; live bias changes are #115). The panel knob maps to it by
                  knob_bias() (#110); bias_point() reports Rk, Vk, Ip, Ig2 and Ik for a setting (a bias meter).
  quality       : QUALITY key, or an (os, prewarp_hz) pair for experiments. Validated here: "standard" = 8x (the
                  12AX7 stage's default) and "hq" = 16x. No eco setting yet: the 12AX7's PREWARP_HZ was derived for
                  its circuit, and a 6AS6 one would need its own minimax derivation and validation.
  filt          : resampler, "fir" (default) or "iir" (dk/resample.py)
  pentode       : valve parameter set (default pentode.DUAL_CONTROL, issue #55; pentode.REFIT_51 for comparison)
  mode          : the Distortion switch (#111), circuit.MODES: "P1" (default; g3 on the cathode, screen at the stiff
                  EC2 = 120 V), "T" (g2 + g3 strapped to the plate) or "P2" (g3 to ground, our hypothesis). Fixed
                  for the run (smooth switching is #115). Rk is solved for the target current in each mode.
  experimental  : False (default) accepts only the SUPPORTED envelope and raises UnsupportedSettingError outside it;
                  True runs any setting with an ExperimentalWarning (Stage.supported records which it is).
  grid_hardness : the g1 grid current (#112), pentode.grid(): 1 (default, pentode.HARDNESS) = Dempwolf & Zoelzer's
                  12AX7 grid law scaled by the fitted cathode-perveance ratio, 0 = no grid current (the #111 stage
                  exactly: same 2-port circuit and solver). UNVALIDATED: no 6AS6 grid-current data exists; the value
                  is our stated guess, the knob the control for it (dk6as6/RESULTS.md, "Grid current (#112)").
circuit.py has the values and the wiring.
"""
import warnings
import numpy as np
from scipy.optimize import brentq
from dk.stage import Stage as _Stage12AX7, OperatingPointError, NonConvergenceWarning
from dk.resample import Resampler
from dk.solver import MAX_IT, TOL_V, TOL_F
from . import circuit
from .solver import solve, solve_grid
from .pentode import DUAL_CONTROL, HARDNESS, grid as grid_law

QUALITY = {"hq": (16, None), "standard": (8, None)}   # (os, prewarp_hz)
BIAS_MA = 0.45                     # default target cathode current, mA (S6's default; #102)
BIAS_RANGE_MA = (0.6, 0.1)         # target Ik at the knob's two ends, mA (#110; knob_bias)
KNOB = (1.0, 11.0)                 # the panel's Bias scale (SOS 2003: "calibrated from one to 11")
RK_LOG10 = (2.7, 6.0)              # cathode_resistor's bracket, log10(Rk / ohm): ~0.5 kOhm to 1 MOhm
NEWTON = dict(max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F)
# The supported envelope (#111 review; #112), target cathode current in mA per mode, per grid hardness: SUPPORTED_MA for
# the default (pentode.HARDNESS, with the g1 grid current, #112) and SUPPORTED_MA_NO_GRID for grid_hardness=0 (the #111
# stage). Any other hardness has no reference, so it is experimental only. A mode / bias is supported only where all
# three hold (dk6as6/RESULTS.md, "Supported envelope" and "Grid current (#112)"):
#   in the law   for host signals bounded by 8 V peak (tones over 20 Hz-20 kHz from silence, step and slope onsets and
#                step offsets, standard and hq: dk6as6/envelope.py), read on the interpolated internal-rate voltages:
#                without grid current g1 never goes positive (that law has no grid current) and the plate never goes
#                below the cathode (envelope.json); with grid current the plate never goes below the cathode, the
#                plate and screen currents never go negative (Ik >= Ig1, the grid law's limit) and Newton converges
#                (envelope_grid.json): g1 may go positive, which the (unvalidated) grid law models;
#   validated    passing same-model ngspice references (dk/validate.CRIT) at or bracketing each end;
#   aliasing     within the shipped 12AX7 stage's budget for the same setting (aliasing.QUALITY_GATE_DBC).
# P1 holds all three over the whole documented 0.1-0.6 mA, with or without grid current. T without grid current only to
# 0.3 mA (at 0.35 mA an 8 V step onset at 100 Hz takes g1 to +0.30 V; its top end lies between the passing 0.25 and
# 0.35 mA references); with it to 0.5 mA (dk6as6/RESULTS.md, "Grid current (#112)"). P2 nowhere (our wiring
# hypothesis on the fit's weakest g3 region, #123, and its resampler aliasing fails the gate at every current).
# Outside the derivation's stimulus class the runtime guard (Stage.out_of_law, OutOfLawWarning) still counts every
# internal sample outside the law in force (Stage.process_internal).
SUPPORTED_MA_NO_GRID = {"P1": (0.1, 0.6), "T": (0.1, 0.3)}
SUPPORTED_MA = {"P1": (0.1, 0.6), "T": (0.1, 0.5)}
WHY_UNSUPPORTED = {      # with grid current (the default); without it, WHY_UNSUPPORTED_NO_GRID
    "T": "above 0.5 mA g1 rests within ~1.3 V of the cathode and a 2 V drive's grid-current onset aliases above the "
         "quality gate (aliasing.QUALITY_GATE_DBC) at 8x",
    "P2": "P2 (g3 to ground) is our hypothesis on the fit's weakest g3 region, and its aliasing fails the quality gate",
}
__all__ = ["Stage", "QUALITY", "BIAS_MA", "BIAS_RANGE_MA", "KNOB", "SUPPORTED_MA", "SUPPORTED_MA_NO_GRID", "envelope",
           "knob_bias", "bias_knob",
           "bias_point", "cathode_resistor", "operating_point", "supported", "OperatingPointError",
           "NonConvergenceWarning", "UnsupportedSettingError", "ExperimentalWarning", "OutOfLawWarning"]


WHY_UNSUPPORTED_NO_GRID = dict(WHY_UNSUPPORTED, T="without grid current, above 0.3 mA an 8 V step onset takes g1 "
                                                  "positive (outside the valve law; dk6as6/envelope.py)")


class UnsupportedSettingError(ValueError):
    """A mode / bias outside SUPPORTED_MA, without experimental=True."""


class ExperimentalWarning(UserWarning):
    """A Stage built outside SUPPORTED_MA with experimental=True."""


class OutOfLawWarning(UserWarning):
    """A processed block had internal samples outside the valve law in force: without grid current g1 positive or the
    plate below the cathode; with it the plate below the cathode or a negative plate / screen current (Ik < Ig1)."""


def envelope(grid_hardness=HARDNESS):
    """The supported envelope {mode: (lo, hi) mA} at a grid hardness: SUPPORTED_MA at the default, SUPPORTED_MA_NO_GRID
    at 0, none at any other (no reference runs it)."""
    return {HARDNESS: SUPPORTED_MA, 0.0: SUPPORTED_MA_NO_GRID}.get(grid_hardness, {})


def supported(mode, bias_ma, grid_hardness=HARDNESS):
    """True if `bias_ma` mA in `mode` is inside envelope(grid_hardness) (to 1e-9 mA)."""
    lo, hi = envelope(grid_hardness).get(mode, (np.inf, -np.inf))
    return lo - 1e-9 <= bias_ma <= hi + 1e-9


def _grid(grid_hardness, par):
    """pentode.grid() at grid_hardness, or None for no grid current (hardness 0: the #111 2-port stage)."""
    return grid_law(grid_hardness, par) if grid_hardness else None


def operating_point(rk, par=DUAL_CONTROL, newton=tuple(NEWTON.values()), mode=circuit.MODE, grid_hardness=HARDNESS):
    """DC operating point with cathode resistor rk, wired as `mode`: capacitors open, the stage's Newton solver from
    zero valve current, source-stepping all supplies (HT, and EC2 where there is one) in 10 steps as the 12AX7 stage
    does on HT. With grid current (grid_hardness > 0) ik = ip + ig2 + ig1, the cathode current."""
    g = _grid(grid_hardness, par)
    m0 = circuit.build(1.0, rk, dc=True, mode=mode, grid=g is not None)
    u = np.r_[0.0, circuit.sources(mode=mode)]
    p = m0["H"] @ u
    K0 = m0["K"].tolist()
    i = [0.0, 0.0, 0.0]
    for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
        if g is None:
            v, i0, i1, it, ok = solve((p * s).tolist(), K0, i[0], i[1], par, *newton)
            i = [i0, i1, 0.0]
        else:
            v, i, it, ok = solve_grid((p * s).tolist(), K0, i, par, g, *newton)
        if not ok:
            raise OperatingPointError(
                f"DC operating point did not converge at source step {k}/10 (supplies x {s:.1f}): "
                f"{it} Newton iterations (max_it, tol_v, tol_f = {newton}), best iterate v={v}, "
                f"Ip={i[0]:.6g} A, Ig2={i[1]:.6g} A, Ig1={i[2]:.6g} A, Rk={rk:.6g} ohm, mode {mode}")
    i0, i1, i2 = i
    inl = np.array([i0, i1] if g is None else [i0, i1, i2])
    w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nip"].T @ inl)
    nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
    return dict(vg1k=v[0], vg2k=v[1], vg3k=v[2], vak=v[3], vgk=v[0], ip=i0, ig2=i1, ig1=i2,
                ik=i0 + i1 if g is None else i0 + i1 + i2, ia=i0, nodes=nodes, w=w, rk=rk)


def cathode_resistor(bias_ma=BIAS_MA, par=DUAL_CONTROL, newton=tuple(NEWTON.values()), mode=circuit.MODE,
                     grid_hardness=HARDNESS):
    """The cathode resistor (ohms) whose DC operating point in `mode` draws bias_ma mA of cathode current (Ip + Ig2,
    + Ig1 with grid current; at rest Ig1 < 1e-19 A except in T at 0.6 mA, 0.17 nA at Vg1k = -0.95 V, where Rk moves
    by 1.8e-4 from the no-grid one):
    Brent's method on log10(Rk) over RK_LOG10 (Ik falls monotonically with Rk). Raises OperatingPointError outside the
    bracket. Converged to 1e-13 in log10(Rk), i.e. Ik to ~1e-12 relative."""
    ik = bias_ma * 1e-3
    def err(lg):
        return operating_point(10.0 ** lg, par, newton, mode, grid_hardness)["ik"] / ik - 1.0
    lo, hi = RK_LOG10
    if not err(lo) > 0.0 > err(hi):
        raise OperatingPointError(f"bias {bias_ma} mA is outside what Rk = 10^{lo}..10^{hi} ohm can set in {mode}")
    return 10.0 ** brentq(err, lo, hi, xtol=1e-13, rtol=4 * np.finfo(float).eps)


def knob_bias(knob):
    """The Bias knob law (#110): target cathode current in mA for a knob position on the panel's 1-11 scale.
    Reverse acting, as the sources describe (SOS 2003: "As the Bias control is advanced, the amount of current flowing
    through the 6AS6 valve falls"; UA: clockwise decreases current), and logarithmic in Ik between the documented
    range's ends, 0.6 mA at 1 and 0.1 mA at 11:  Ik = 0.6 mA * (0.1 / 0.6) ** ((knob - 1) / 10).
    No source gives the taper or the current at the ends of the travel: putting the documented 0.1-0.6 mA range on the
    ends and the log taper are OUR choices (dk6as6/RESULTS.md, "Bias control"). Log because the manuals describe the
    control only by its meter reading, and their named settings (starved < 0.1, frequency doubling ~0.1-0.2,
    lowest distortion 0.25-0.3, normal 0.2-0.4, warm ~0.6 mA) sit at roughly equal current ratios, crowded at the
    low end; equal knob travel per ratio gives them comparable travel. It does NOT reproduce the UA plug-in's default
    (knob 6 = 0.45 mA, UA manual, Bias section): here knob 6 is 0.245 mA.
    UA manual: https://help.uaudio.com/hc/en-us/articles/33542576197908-Thermionic-Culture-Vulture-Manual"""
    lo, hi = KNOB
    if not lo <= knob <= hi:
        raise ValueError(f"Bias knob {knob} is outside the panel's {lo:g}-{hi:g} scale")
    a, b = BIAS_RANGE_MA
    return a * (b / a) ** ((knob - lo) / (hi - lo))


def bias_knob(bias_ma):
    """The inverse of knob_bias(): the knob position (1-11) that sets bias_ma mA."""
    a, b = BIAS_RANGE_MA
    if not b <= bias_ma <= a:
        raise ValueError(f"bias {bias_ma} mA is outside the knob's {b:g}-{a:g} mA range")
    lo, hi = KNOB
    return lo + (hi - lo) * np.log(bias_ma / a) / np.log(b / a)


def bias_point(bias_ma=BIAS_MA, par=DUAL_CONTROL, newton=tuple(NEWTON.values()), mode=circuit.MODE,
               grid_hardness=HARDNESS):
    """What a bias meter needs for a setting: the DC operating point at the Rk that sets bias_ma mA in `mode`. Returns
    the solved rheostat rk (ohm), the cathode voltage vk = Rk * Ik (V, positive: the "positive voltage on its cathode"
    the manuals describe), the plate voltage va (V), ip, ig2, ig1 and ik = ip + ig2 + ig1 (A; ig1 = 0 without grid
    current)."""
    dc = operating_point(cathode_resistor(bias_ma, par, newton, mode, grid_hardness), par, newton, mode, grid_hardness)
    return dict(bias_ma=bias_ma, rk=float(dc["rk"]), vk=float(dc["nodes"]["k"]), va=float(dc["nodes"]["a"]),
                ip=float(dc["ip"]), ig2=float(dc["ig2"]), ig1=float(dc["ig1"]), ik=float(dc["ik"]))


class Stage(_Stage12AX7):
    def __init__(self, fs, bias=BIAS_MA, drive=1.0, output=1.0, pentode=DUAL_CONTROL, quality="standard", filt="fir",
                 newton=None, mode=circuit.MODE, experimental=False, grid_hardness=HARDNESS):
        if mode not in circuit.MODES:
            raise ValueError(f"unknown mode {mode!r}, expected one of {sorted(circuit.MODES)}")
        self.mode, self.supported = mode, supported(mode, bias, grid_hardness)
        if not self.supported:
            env = envelope(grid_hardness)
            rng = "%g-%g mA" % env[mode] if mode in env else "none"
            why = (f"{mode} at {bias:g} mA with grid hardness {grid_hardness:g} is outside the supported envelope "
                   f"({mode}: {rng}): " + ((WHY_UNSUPPORTED if grid_hardness else WHY_UNSUPPORTED_NO_GRID).get(
                       mode, "outside the documented 0.1-0.6 mA") if env else
                                          f"only hardness {HARDNESS:g} (the default) and 0 are validated"))
            if not experimental:
                raise UnsupportedSettingError(why + " (experimental=True runs it anyway)")
            warnings.warn(why, ExperimentalWarning, stacklevel=2)
        nw = dict(NEWTON)
        if set(newton or {}) - set(nw):
            raise TypeError(f"unknown newton option(s) {sorted(set(newton) - set(nw))}, expected {sorted(nw)}")
        nw.update(newton or {})
        self.newton = (nw["max_it"], nw["tol_v"], nw["tol_f"])
        self.fs_base = fs
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        self.rs = Resampler(fs, self.os, filt)
        self.fs = fs * self.os
        self.drive, self.output, self.bias, self.par = drive, output, bias, pentode
        self.grid_hardness, self.grid = grid_hardness, _grid(grid_hardness, pentode)
        self.rk = cathode_resistor(bias, pentode, self.newton, mode, grid_hardness)
        self.m = circuit.build(self.fs, self.rk, prewarp_hz=self.prewarp_hz, mode=mode, grid=self.grid is not None)
        self.K = self.m["K"].tolist()
        self.supplies = circuit.sources(mode=mode)
        self.dc = self.operating_point()
        self.reset()

    def operating_point(self):
        """The DC operating point at this stage's Rk, valve parameters, Newton settings and mode (module
        operating_point(); overrides dk.stage.Stage's, which solves the 12AX7 circuit)."""
        return operating_point(self.rk, self.par, self.newton, self.mode, self.grid_hardness)

    def swap_bias(self, bias):
        raise NotImplementedError("the 6AS6 stage's Rk is fixed for the run; live bias changes are #115")

    def reset(self):
        m, dc = self.m, self.dc
        vn = dc["w"][:len(circuit.NODES)]
        # The state runs as the DEVIATION from the operating point: X = x_dc + x, i = i_dc + di. Silence is then x = 0
        # exactly. With the absolute history state (as dk/stage.py), the large DC entries (Ck's ~113 A at hq) make the
        # per-sample rounding a constant error that (I - A)^-1 ~ fs*tau amplifies: 1.2 uV at the output after 100 ms of
        # silence at hq, 0.14 uV at standard (#26, with the placeholder's 22 uF bypass cap; there is none now, #102).
        # p_dc = v_dc + K i_dc makes the operating point the exact fixed point.
        x_dc = m["Gx"] @ m["Nx"] @ vn            # capacitor history so that every i_c = 0 at t=0
        self.n_nl = 2 if self.grid is None else 3          # valve current ports: (Ip, Ig2[, Ig1])
        self.i_dc = np.array([dc["ip"], dc["ig2"], dc["ig1"]][:self.n_nl])
        v_dc = np.array([dc["vg1k"], dc["vg2k"], dc["vg3k"], dc["vak"]])
        self.p_dc = v_dc + m["K"] @ self.i_dc
        self.y_dc = m["D"] @ x_dc + m["E"][:, 1:] @ self.supplies + m["F"] @ self.i_dc
        self.x = np.zeros_like(x_dc)
        self.i = [dc["ip"], dc["ig2"], dc["ig1"]][:self.n_nl]
        self.i_prev = list(self.i)              # currents one sample before self.i, for the predictor
        self.iters = []
        self.nonconv = 0
        self.nonconv_block = 0
        self.out_of_law = 0                     # internal samples with Vg1k > 0 or Vak < 0 (the runtime guard)
        self.out_of_law_block = 0
        self.rs.reset()

    def process_internal(self, vin, record=("out",)):
        """Run the stage at the internal rate on an input already at self.fs. Returns dict of signals
        ('vgk' = Vg1k, 'vg2k' = Vg2k, 'ig1' = the g1 current, zero without it, 'ipk' = Ip + Ig2 = Ik - Ig1, the
        current the fit's plate and screen share, 'vak' = Vak). Non-convergence handling as
        dk/stage.Stage.process_internal (#7). Without grid current the arithmetic is the #111 stage's, unchanged."""
        m = self.m
        A, C, D, F, G = (m[k] for k in "ACDFG")
        B0, E0, H0 = m["B"][:, 0], m["E"][:, 0], m["H"][:, 0]
        p_dc, y_dc, i_dc = self.p_dc, self.y_dc, self.i_dc
        rec = {n: np.empty(len(vin)) for n in record}
        idx = {n: m["out_names"].index(n) for n in record if n in m["out_names"]}
        x, i, K, par, nw, g = self.x, list(self.i), self.K, self.par, self.newton, self.grid
        q = list(self.i_prev)
        if g is None:
            def step(p, e, prev):
                v, a, b, it, ok = solve(p, K, e[0], e[1], par, *nw)
                return v, [a, b], it, ok
        else:
            i_op = self.i_dc.tolist()
            def step(p, e, prev):
                # #112: if the extrapolated warm start does not converge (T near the top of its range, steady 6-8 V at
                # 13-19 kHz: Newton stalls where Ik < Ig1 although a root with every current positive exists), restart
                # from the previous sample's currents, then from the operating point. Iterations are summed; a sample
                # that converges first time (every one inside the supported envelope) is unchanged.
                v, i, it, ok = solve_grid(p, K, e, par, g, *nw)
                for start in (prev, i_op):
                    if ok:
                        break
                    v2, i2, it2, ok2 = solve_grid(p, K, start, par, g, *nw)
                    it += it2
                    if ok2:
                        v, i, ok = v2, i2, True
                return v, i, it, ok
        iters = np.empty(len(vin), dtype=np.int16)
        bad = law = 0
        for n, s in enumerate(vin):
            p = (G @ x + H0 * s + p_dc).tolist()
            # warm start: linear extrapolation of the last two current solutions (as dk/stage.py does on voltages)
            e = [2.0 * a - b for a, b in zip(i, q)]
            q = i
            v, i, it, ok = step(p, e, q)
            iters[n] = it
            if not ok:
                bad += 1
            # the runtime guard: outside the law in force. Without grid current, g1 positive or the plate below the
            # cathode; with it (g1 positive is then modelled), the plate below the cathode or Ip + Ig2 < 0 (Ik < Ig1)
            if (v[0] > 0.0 if g is None else i[0] + i[1] < 0.0) or v[3] < 0.0:
                law += 1
            inl = np.array(i) - i_dc                     # deviation from the operating point
            if idx:
                y = D @ x + E0 * s + F @ inl + y_dc
                for nm, j in idx.items():
                    rec[nm][n] = y[j]
            if "vgk" in rec:
                rec["vgk"][n] = v[0]
            if "vg2k" in rec:
                rec["vg2k"][n] = v[1]
            if "vak" in rec:
                rec["vak"][n] = v[3]
            if "ig1" in rec:
                rec["ig1"][n] = i[2] if g is not None else 0.0
            if "ipk" in rec:
                rec["ipk"][n] = i[0] + i[1]
            x = A @ x + B0 * s + C @ inl
        self.x, self.i, self.i_prev = x, i, q
        self.iters.append(iters)
        self.nonconv += bad
        self.nonconv_block = bad
        self.out_of_law += law
        self.out_of_law_block = law
        if law:
            what = ("g1 positive or the plate below the cathode: this law has no grid current, grid_hardness=0"
                    if self.grid is None else "the plate below the cathode or Ik < Ig1, the grid-current law's limit")
            warnings.warn(f"{law} of {len(vin)} internal samples outside the valve law ({what}) in {self.mode} at "
                          f"{self.bias:g} mA", OutOfLawWarning, stacklevel=2)
        if bad:
            warnings.warn(f"Newton did not converge on {bad} of {len(vin)} samples at {self.fs:g} Hz "
                          f"(max_it, tol_v, tol_f = {self.newton}); kept the best finite iterate",
                          NonConvergenceWarning, stacklevel=2)
        return rec
