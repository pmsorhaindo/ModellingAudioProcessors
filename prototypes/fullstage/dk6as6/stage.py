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
Screen: an ideal source at EC2 = 120 V; suppressor on the cathode (P1). circuit.py has the values.
"""
import warnings
import numpy as np
from scipy.optimize import brentq
from dk.stage import Stage as _Stage12AX7, OperatingPointError, NonConvergenceWarning
from dk.resample import Resampler
from dk.solver import MAX_IT, TOL_V, TOL_F
from . import circuit
from .solver import solve
from .pentode import DUAL_CONTROL

QUALITY = {"hq": (16, None), "standard": (8, None)}   # (os, prewarp_hz)
BIAS_MA = 0.45                     # default target cathode current, mA (S6's default; #102)
BIAS_RANGE_MA = (0.6, 0.1)         # target Ik at the knob's two ends, mA (#110; knob_bias)
KNOB = (1.0, 11.0)                 # the panel's Bias scale (SOS 2003: "calibrated from one to 11")
RK_LOG10 = (2.7, 6.0)              # cathode_resistor's bracket, log10(Rk / ohm): ~0.5 kOhm to 1 MOhm
NEWTON = dict(max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F)
__all__ = ["Stage", "QUALITY", "BIAS_MA", "BIAS_RANGE_MA", "KNOB", "knob_bias", "bias_knob", "bias_point",
           "cathode_resistor", "operating_point", "OperatingPointError", "NonConvergenceWarning"]


def operating_point(rk, par=DUAL_CONTROL, newton=tuple(NEWTON.values())):
    """DC operating point with cathode resistor rk: capacitors open, the stage's Newton solver from zero valve current,
    source-stepping all supplies (HT, EC2) in 10 steps as the 12AX7 stage does on HT."""
    m0 = circuit.build(1.0, rk, dc=True)
    u = np.r_[0.0, circuit.sources()]
    p = m0["H"] @ u
    K0 = m0["K"].tolist()
    i0 = i1 = 0.0
    for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
        v, i0, i1, it, ok = solve((p * s).tolist(), K0, i0, i1, par, *newton)
        if not ok:
            raise OperatingPointError(
                f"DC operating point did not converge at source step {k}/10 (supplies x {s:.1f}): "
                f"{it} Newton iterations (max_it, tol_v, tol_f = {newton}), best iterate v={v}, "
                f"Ip={i0:.6g} A, Ig2={i1:.6g} A, Rk={rk:.6g} ohm")
    inl = np.array([i0, i1])
    w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nip"].T @ inl)
    nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
    return dict(vg1k=v[0], vg2k=v[1], vg3k=v[2], vak=v[3], vgk=v[0], ip=i0, ig2=i1, ik=i0 + i1, ia=i0,
                nodes=nodes, w=w, rk=rk)


def cathode_resistor(bias_ma=BIAS_MA, par=DUAL_CONTROL, newton=tuple(NEWTON.values())):
    """The cathode resistor (ohms) whose DC operating point draws bias_ma mA of cathode current (Ip + Ig2): Brent's
    method on log10(Rk) over RK_LOG10 (Ik falls monotonically with Rk). Raises OperatingPointError outside the
    bracket. Converged to 1e-13 in log10(Rk), i.e. Ik to ~1e-12 relative."""
    ik = bias_ma * 1e-3
    def err(lg):
        return operating_point(10.0 ** lg, par, newton)["ik"] / ik - 1.0
    lo, hi = RK_LOG10
    if not err(lo) > 0.0 > err(hi):
        raise OperatingPointError(f"bias {bias_ma} mA is outside what Rk = 10^{lo}..10^{hi} ohm can set")
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


def bias_point(bias_ma=BIAS_MA, par=DUAL_CONTROL, newton=tuple(NEWTON.values())):
    """What a bias meter needs for a setting: the DC operating point at the Rk that sets bias_ma mA. Returns the solved
    rheostat rk (ohm), the cathode voltage vk = Rk * Ik (V, positive: the "positive voltage on its cathode" the
    manuals describe), the plate voltage va (V), ip, ig2 and ik = ip + ig2 (A)."""
    dc = operating_point(cathode_resistor(bias_ma, par, newton), par, newton)
    return dict(bias_ma=bias_ma, rk=float(dc["rk"]), vk=float(dc["nodes"]["k"]), va=float(dc["nodes"]["a"]),
                ip=float(dc["ip"]), ig2=float(dc["ig2"]), ik=float(dc["ik"]))


class Stage(_Stage12AX7):
    def __init__(self, fs, bias=BIAS_MA, drive=1.0, output=1.0, pentode=DUAL_CONTROL, quality="standard", filt="fir",
                 newton=None):
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
        self.rk = cathode_resistor(bias, pentode, self.newton)
        self.m = circuit.build(self.fs, self.rk, prewarp_hz=self.prewarp_hz)
        self.K = self.m["K"].tolist()
        self.supplies = circuit.sources()
        self.dc = self.operating_point()
        self.reset()

    def operating_point(self):
        """The DC operating point at this stage's Rk, valve parameters and Newton settings (module operating_point();
        overrides dk.stage.Stage's, which solves the 12AX7 circuit)."""
        return operating_point(self.rk, self.par, self.newton)

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
        self.i_dc = np.array([dc["ip"], dc["ig2"]])
        v_dc = np.array([dc["vg1k"], dc["vg2k"], dc["vg3k"], dc["vak"]])
        self.p_dc = v_dc + m["K"] @ self.i_dc
        self.y_dc = m["D"] @ x_dc + m["E"][:, 1:] @ self.supplies + m["F"] @ self.i_dc
        self.x = np.zeros_like(x_dc)
        self.i = [dc["ip"], dc["ig2"]]
        self.i_prev = list(self.i)              # currents one sample before self.i, for the predictor
        self.iters = []
        self.nonconv = 0
        self.nonconv_block = 0
        self.rs.reset()

    def process_internal(self, vin, record=("out",)):
        """Run the stage at the internal rate on an input already at self.fs. Returns dict of signals
        ('vgk' = Vg1k). Non-convergence handling as dk/stage.Stage.process_internal (#7)."""
        m = self.m
        A, C, D, F, G = (m[k] for k in "ACDFG")
        B0, E0, H0 = m["B"][:, 0], m["E"][:, 0], m["H"][:, 0]
        p_dc, y_dc, (idc0, idc1) = self.p_dc, self.y_dc, self.i_dc
        rec = {n: np.empty(len(vin)) for n in record}
        idx = {n: m["out_names"].index(n) for n in record if n in m["out_names"]}
        x, (i0, i1), K, par, nw = self.x, self.i, self.K, self.par, self.newton
        q0, q1 = self.i_prev
        iters = np.empty(len(vin), dtype=np.int16)
        bad = 0
        for n, s in enumerate(vin):
            p = (G @ x + H0 * s + p_dc).tolist()
            # warm start: linear extrapolation of the last two current solutions (as dk/stage.py does on voltages)
            e0, e1 = 2.0 * i0 - q0, 2.0 * i1 - q1
            q0, q1 = i0, i1
            v, i0, i1, it, ok = solve(p, K, e0, e1, par, *nw)
            iters[n] = it
            if not ok:
                bad += 1
            inl = np.array([i0 - idc0, i1 - idc1])     # deviation from the operating point
            if idx:
                y = D @ x + E0 * s + F @ inl + y_dc
                for nm, j in idx.items():
                    rec[nm][n] = y[j]
            if "vgk" in rec:
                rec["vgk"][n] = v[0]
            x = A @ x + B0 * s + C @ inl
        self.x, self.i, self.i_prev = x, [i0, i1], [q0, q1]
        self.iters.append(iters)
        self.nonconv += bad
        self.nonconv_block = bad
        if bad:
            warnings.warn(f"Newton did not converge on {bad} of {len(vin)} samples at {self.fs:g} Hz "
                          f"(max_it, tol_v, tol_f = {self.newton}); kept the best finite iterate",
                          NonConvergenceWarning, stacklevel=2)
        return rec
