"""One 6AS6 common-cathode stage, nodal DK method (issue #26): dk/stage.py's stage with the pentode.

Same controls and streaming resampler as the 12AX7 stage (dk/stage.py, which this subclasses for process(),
latency, the resampler and iteration_stats()):
  drive, output : linear gains before / after the stage
  bias          : scale factor on Rk (1.0 = 1.5 kOhm)
  quality       : QUALITY key, or an (os, prewarp_hz) pair for experiments. Validated here: "standard" = 8x (the
                  12AX7 stage's default) and "hq" = 16x. No eco setting yet: the 12AX7's PREWARP_HZ was derived for
                  its circuit, and a 6AS6 one would need its own minimax derivation and validation.
  filt          : resampler, "fir" (default) or "iir" (dk/resample.py)
  pentode       : valve parameter set (default pentode.DUAL_CONTROL, issue #55; pentode.REFIT_51 for comparison)
Screen and suppressor are ideal sources (circuit.py: EC2 = 120 V, EC3 = 0 V).
"""
import warnings
import numpy as np
from dk.stage import Stage as _Stage12AX7, OperatingPointError, NonConvergenceWarning
from dk.resample import Resampler
from dk.solver import MAX_IT, TOL_V, TOL_F
from . import circuit
from .solver import solve
from .pentode import DUAL_CONTROL

QUALITY = {"hq": (16, None), "standard": (8, None)}   # (os, prewarp_hz)
__all__ = ["Stage", "QUALITY", "OperatingPointError", "NonConvergenceWarning"]


class Stage(_Stage12AX7):
    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, pentode=DUAL_CONTROL, quality="standard", filt="fir",
                 newton=None):
        nw = dict(max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F)
        if set(newton or {}) - set(nw):
            raise TypeError(f"unknown newton option(s) {sorted(set(newton) - set(nw))}, expected {sorted(nw)}")
        nw.update(newton or {})
        self.newton = (nw["max_it"], nw["tol_v"], nw["tol_f"])
        self.fs_base = fs
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        self.rs = Resampler(fs, self.os, filt)
        self.fs = fs * self.os
        self.drive, self.output, self.bias, self.par = drive, output, bias, pentode
        self.m = circuit.build(self.fs, rk_scale=bias, prewarp_hz=self.prewarp_hz)
        self.K = self.m["K"].tolist()
        self.supplies = circuit.sources()
        self.dc = self.operating_point()
        self.reset()

    # ---- DC operating point: capacitors open, same NR solver, from zero valve current
    def operating_point(self):
        m0 = circuit.build(self.fs, rk_scale=self.bias, dc=True)
        u = np.r_[0.0, self.supplies]
        p = m0["H"] @ u
        K0 = m0["K"].tolist()
        i0 = i1 = 0.0
        # source-stepping on all supplies (HT, EC2, EC3) in 10 steps, as the 12AX7 stage does on HT
        for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
            v, i0, i1, it, ok = solve((p * s).tolist(), K0, i0, i1, self.par, *self.newton)
            if not ok:
                raise OperatingPointError(
                    f"DC operating point did not converge at source step {k}/10 (supplies x {s:.1f}): "
                    f"{it} Newton iterations (max_it, tol_v, tol_f = {self.newton}), best iterate v={v}, "
                    f"Ip={i0:.6g} A, Ig2={i1:.6g} A, bias={self.bias}")
        inl = np.array([i0, i1])
        w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nip"].T @ inl)
        nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
        return dict(vg1k=v[0], vg2k=v[1], vg3k=v[2], vak=v[3], vgk=v[0], ip=i0, ig2=i1, ik=i0 + i1, ia=i0,
                    nodes=nodes, w=w)

    def reset(self):
        m, dc = self.m, self.dc
        vn = dc["w"][:len(circuit.NODES)]
        # The state runs as the DEVIATION from the operating point: X = x_dc + x, i = i_dc + di. Silence is then x = 0
        # exactly. With the absolute history state (as dk/stage.py), the large DC entries (Ck's ~113 A at hq) make the
        # per-sample rounding a constant error that (I - A)^-1 ~ fs*tau amplifies: 1.2 uV at the output after 100 ms of
        # silence at hq, 0.14 uV at standard. p_dc = v_dc + K i_dc makes the operating point the exact fixed point.
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
