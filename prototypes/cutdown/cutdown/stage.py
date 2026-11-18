"""Cut-down 12AX7 common-cathode stage (#23): 3 states, 2-D lookup table, 2x IIR half-band resampling.

Compared to prototypes/fullstage/dk/stage.py:
  * 3 capacitor states (Cin, Ck, Cout) instead of 6: the three pF valve capacitances
    (CCG, CGP, CCP) are dropped.
  * 2-D lookup table (default 128 x 32 x 2 ports = 8,192 words, cubic in p0 / linear in p1)
    replaces per-sample Newton-Raphson. solver_mode="newton" is kept as an ablation switch.
  * 2x oversampling with the streaming polyphase allpass IIR half-band resampler from
    prototypes/fullstage/dk/resample.py (6 first-order allpasses per way = 12 MACs/base sample,
    >= 96 dB stopband rejection, 3 base-rate samples nominal latency).
"""
import math
import numpy as np
import cutdown  # noqa: F401 -- ensures FULLSTAGE_DIR is on sys.path
from dk.resample import Resampler  # noqa: E402 -- after cutdown path setup
from dk.solver import solve, MAX_IT, TOL_V, TOL_F  # noqa: E402 -- after cutdown path setup
from dk.stage import OperatingPointError  # noqa: E402 -- after cutdown path setup
from dk.triode import DZ_12AX7_RSD1, currents, currents_only  # noqa: E402 -- after cutdown path setup
from . import circuit  # noqa: E402 -- after cutdown path setup
from .table import (  # noqa: E402 -- after cutdown path setup
    ADAA_TOL_1,
    ADAA_TOL_2,
    DEFAULT_P0_SPAN,
    DEFAULT_P1_SPAN,
    DEFAULT_SHAPE,
    SolutionTable,
    _catmull_rom_weights,
)

OVERSAMPLING = 2
RESAMPLER_KIND = "iir"
SOLVER_MODES = ("lut", "newton")
ADAA_ORDERS = (0, 1, 2)


class CutdownStage:
    """Cut-down 12AX7 stage running at 2x oversampling with a 3-state DK core and 2-D LUT."""

    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, triode=DZ_12AX7_RSD1,
                 shape=DEFAULT_SHAPE, p0_span=DEFAULT_P0_SPAN, p1_span=DEFAULT_P1_SPAN,
                 interp="cubic0_lin1", oversampling=OVERSAMPLING, filt=RESAMPLER_KIND,
                 solver_mode="lut", miller_lpf=True, adaa=1, treble_comp=True):
        if solver_mode not in SOLVER_MODES:
            raise ValueError(f"solver_mode must be one of {SOLVER_MODES}, got {solver_mode!r}")
        adaa = int(adaa)
        if adaa not in ADAA_ORDERS:
            raise ValueError(f"adaa must be one of {ADAA_ORDERS}, got {adaa!r}")
        if adaa > 0 and (solver_mode != "lut" or interp != "cubic0_lin1"):
            raise ValueError("ADAA requires solver_mode='lut' and interp='cubic0_lin1'")
        self.fs_base = float(fs)
        self.os = int(oversampling)
        self.filt = filt
        self.rs = Resampler(self.fs_base, self.os, self.filt)
        self.fs = self.fs_base * self.os
        self.drive = float(drive)
        self.output = float(output)
        self.bias = float(bias)
        self.par = triode
        self.shape = tuple(shape)
        self.p0_span = tuple(p0_span)
        self.p1_span = tuple(p1_span)
        self.interp = interp
        self.solver_mode = solver_mode
        self.miller_lpf = bool(miller_lpf)
        self.adaa = adaa
        self.treble_comp = bool(treble_comp)
        self.ev = (currents, currents_only)

        self.m = circuit.build(self.fs, rk_scale=self.bias)
        self.K = self.m["K"].tolist()
        self.dc = self.operating_point()
        self._rest()
        self._build_table()
        self._build_miller_filter()
        self.treble_comp_coefs = circuit.treble_comp_coefs(self.fs_base, self.fs)
        self.reset()

    def operating_point(self):
        """Solve the DC operating point (all capacitors open) via source-stepping Newton."""
        m0 = circuit.build(self.fs, rk_scale=self.bias, dc=True)
        u = np.array([0.0, circuit.VALUES["HT"]])
        p = m0["H"] @ u
        K0 = m0["K"].tolist()
        v0, v1 = -1.0, 200.0
        for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
            v0, v1, ig, ia, it, ok = solve(
                p[0] * s, p[1] * s, K0, v0, v1, self.par, MAX_IT, TOL_V, TOL_F, ev=self.ev)
            if not ok:
                raise OperatingPointError(
                    f"DC operating point did not converge at source step {k}/10 (HT x {s:.1f}): "
                    f"{it} Newton iterations, best iterate Vgk={v0:.6g} V, Vak={v1:.6g} V, bias={self.bias}")
        inl = np.array([ig, ia])
        w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nnp"].T @ inl)
        nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
        return dict(vgk=v0, vak=v1, ig=ig, ia=ia, nodes=nodes, w=w)

    def _rest(self):
        """Deviation-form constants around the DC operating point (matches dk.stage.Stage._rest)."""
        m, dc = self.m, self.dc
        vn = dc["w"][:len(circuit.NODES)]
        self.x0 = m["Gx"] @ m["Nx"] @ vn
        self.i0 = np.array([dc["ig"], dc["ia"]])
        self.p_dc = np.array([dc["vgk"], dc["vak"]]) + m["K"] @ self.i0
        self.y_dc = np.array([dc["nodes"][n] for n in m["out_names"]])

    def _build_table(self):
        self.table = SolutionTable(
            self.m["K"], self.p_dc, (self.dc["vgk"], self.dc["vak"]), self.i0,
            par=self.par, shape=self.shape, p0_span=self.p0_span, p1_span=self.p1_span,
            interp=self.interp,
        )

    def _build_miller_filter(self):
        J = currents(self.dc["vgk"], self.dc["vak"], self.par)[2]
        self.miller_fc_hz = circuit.miller_cutoff_hz(J, rk_scale=self.bias)
        self.miller_coefs = circuit.miller_filter_coefs(self.miller_fc_hz, self.fs)

    def reset(self):
        self._rest()
        self.x = np.zeros_like(self.x0)
        self.v = [self.dc["vgk"], self.dc["vak"]]
        self.v_prev = list(self.v)
        self.lp_x = 0.0
        self.lp_y = 0.0
        self.tc_x = 0.0
        self.tc_y = 0.0
        self.p_prev = [float(self.p_dc[0]), float(self.p_dc[1])]
        self.p_prev2 = list(self.p_prev)
        self.j_prev = -1
        self.pg0 = self.pg1 = self.pa0 = self.pa1 = 0.0
        self.rs.reset()

    def swap_bias(self, bias):
        """Rebuild matrices, DC operating point, and 2-D LUT for a new Rk scale; keep capacitor state."""
        m = circuit.build(self.fs, rk_scale=bias)
        bias_old, self.bias = self.bias, float(bias)
        try:
            dc = self.operating_point()
        except OperatingPointError:
            self.bias = bias_old
            raise
        x0_old = self.x0
        self.m, self.K, self.dc = m, m["K"].tolist(), dc
        self._rest()
        self._build_table()
        self._build_miller_filter()
        self.j_prev = -1
        self.x = self.x + (x0_old - self.x0)

    @property
    def latency_samples(self):
        """Base-rate delay of process() output vs input (nominal 1 kHz group delay for IIR)."""
        return self.rs.latency_samples

    @staticmethod
    def _first_order_group_delay(coefs, f, fs):
        """Group delay (samples at rate fs) of H(z) = (b0 + b1 z^-1) / (1 - p z^-1) at frequency f."""
        b0, b1, p = coefs
        df = 1.0
        w1 = 2.0 * math.pi * (float(f) - df) / float(fs)
        w2 = 2.0 * math.pi * (float(f) + df) / float(fs)
        z1 = complex(math.cos(w1), -math.sin(w1))
        z2 = complex(math.cos(w2), -math.sin(w2))
        h1 = (b0 + b1 * z1) / (1.0 - p * z1)
        h2 = (b0 + b1 * z2) / (1.0 - p * z2)
        dphi = math.atan2((h2 * h1.conjugate()).imag, (h2 * h1.conjugate()).real)
        return -dphi / (w2 - w1)

    def miller_group_delay_base(self, f=1000.0):
        """Group delay (base-rate samples) of the Method A 1-pole IIR LPF at frequency f (0 if disabled)."""
        if not self.miller_lpf:
            return 0.0
        return self._first_order_group_delay(self.miller_coefs, f, self.fs) / self.os

    def treble_comp_group_delay_base(self, f=1000.0):
        """Group delay (base-rate samples) of the base-rate treble compensator at f (0 if disabled)."""
        if not self.treble_comp:
            return 0.0
        return self._first_order_group_delay(self.treble_comp_coefs, f, self.fs_base)

    @property
    def adaa_delay_base(self):
        """Nominal small-signal fractional delay (base-rate samples) added by ADAA."""
        return 0.5 * self.adaa / self.os

    @property
    def resampler_macs(self):
        """Resampler multiplies per base-rate sample, (up, down)."""
        return self.rs.macs()

    def _process_internal_lut(self, vin, record):
        m = self.m
        A, C, G = m["A"], m["C"], m["G"]
        D, F = m["D"], m["F"]
        B0 = m["B"][:, 0]
        E0 = m["E"][:, 0]
        H0 = m["H"][:, 0]
        p_dc, y_dc, (ig0, ia0) = self.p_dc, self.y_dc, self.i0
        K00, K01 = float(self.K[0][0]), float(self.K[0][1])

        a00, a01, a02 = float(A[0, 0]), float(A[0, 1]), float(A[0, 2])
        a10, a11, a12 = float(A[1, 0]), float(A[1, 1]), float(A[1, 2])
        a20, a21, a22 = float(A[2, 0]), float(A[2, 1]), float(A[2, 2])
        b00, b10, b20 = float(B0[0]), float(B0[1]), float(B0[2])
        c00, c01 = float(C[0, 0]), float(C[0, 1])
        c10, c11 = float(C[1, 0]), float(C[1, 1])
        c20, c21 = float(C[2, 0]), float(C[2, 1])
        g00, g01, g02 = float(G[0, 0]), float(G[0, 1]), float(G[0, 2])
        g10, g11, g12 = float(G[1, 0]), float(G[1, 1]), float(G[1, 2])
        h00, h10 = float(H0[0]), float(H0[1])
        pdc0, pdc1 = float(p_dc[0]), float(p_dc[1])

        tbl = self.table
        n0, n1 = tbl.n0, tbl.n1
        n0m1, n0m2 = n0 - 1, n0 - 2
        n1m1, n1m2 = n1 - 1, n1 - 2
        p0_min, inv_dp0 = tbl.p0_min, tbl.inv_dp0
        p1_min, inv_dp1 = tbl.p1_min, tbl.inv_dp1
        tig_flat = tbl.tbl_ig.ravel().tolist()
        tia_flat = tbl.tbl_ia.ravel().tolist()
        dig_off, dia_off = tbl.dig_off, tbl.dia_off
        is_bilinear = tbl.interp == "bilinear"

        rec = {n: np.empty(len(vin)) for n in record}
        out_only = tuple(record) == ("out",)
        want_vgk = "vgk" in rec
        if out_only:
            d00, d01, d02 = float(D[0, 0]), float(D[0, 1]), float(D[0, 2])
            e00 = float(E0[0])
            f00, f01 = float(F[0, 0]), float(F[0, 1])
            ydc0 = float(y_dc[0])
            out_arr = rec["out"]
        else:
            idx_map = [(n, m["out_names"].index(n)) for n in record if n in m["out_names"]]

        x0, x1, x2 = float(self.x[0]), float(self.x[1]), float(self.x[2])
        use_lpf = self.miller_lpf
        lp_b0, lp_b1, lp_p = self.miller_coefs
        lp_x, lp_y = self.lp_x, self.lp_y
        adaa = self.adaa
        p0_prev, p1_prev = self.p_prev
        p0_prev2, p1_prev2 = self.p_prev2
        u_prev = (p0_prev - p0_min) * inv_dp0
        u_prev2 = (p0_prev2 - p0_min) * inv_dp0
        j_prev = self.j_prev
        pg0, pg1, pa0, pa1 = self.pg0, self.pg1, self.pa0, self.pa1

        for n, s_val in enumerate(vin):
            s = float(s_val)
            if use_lpf:
                lp_y = lp_b0 * s + lp_b1 * lp_x + lp_p * lp_y
                lp_x = s
                s = lp_y
            p0 = g00 * x0 + g01 * x1 + g02 * x2 + h00 * s + pdc0
            p1 = g10 * x0 + g11 * x1 + g12 * x2 + h10 * s + pdc1

            if adaa == 0:
                u = (p0 - p0_min) * inv_dp0
                w = (p1 - p1_min) * inv_dp1
                if u <= 0.0:
                    i, fu = 0, 0.0
                elif u >= n0m1:
                    i, fu = n0m2, 1.0
                else:
                    i = int(u)
                    fu = u - i
                if w <= 0.0:
                    j, fw = 0, 0.0
                elif w >= n1m1:
                    j, fw = n1m2, 1.0
                else:
                    j = int(w)
                    fw = w - j

                b0, b1 = 1.0 - fw, fw
                if is_bilinear:
                    a0, a1 = 1.0 - fu, fu
                    k0 = i * n1 + j
                    k1 = k0 + n1
                    ig = a0 * (b0 * tig_flat[k0] + b1 * tig_flat[k0 + 1]) + a1 * (
                        b0 * tig_flat[k1] + b1 * tig_flat[k1 + 1])
                    ia = a0 * (b0 * tia_flat[k0] + b1 * tia_flat[k0 + 1]) + a1 * (
                        b0 * tia_flat[k1] + b1 * tia_flat[k1 + 1])
                else:
                    im1 = (i - 1) if i > 0 else 0
                    ip1 = i + 1
                    ip2 = (i + 2) if (i + 2) < n0 else n0m1
                    w0, w1, w2, w3 = _catmull_rom_weights(fu)
                    km1, k0, kp1, kp2 = im1 * n1 + j, i * n1 + j, ip1 * n1 + j, ip2 * n1 + j
                    ig = (w0 * (b0 * tig_flat[km1] + b1 * tig_flat[km1 + 1])
                          + w1 * (b0 * tig_flat[k0] + b1 * tig_flat[k0 + 1])
                          + w2 * (b0 * tig_flat[kp1] + b1 * tig_flat[kp1 + 1])
                          + w3 * (b0 * tig_flat[kp2] + b1 * tig_flat[kp2 + 1]))
                    ia = (w0 * (b0 * tia_flat[km1] + b1 * tia_flat[km1 + 1])
                          + w1 * (b0 * tia_flat[k0] + b1 * tia_flat[k0 + 1])
                          + w2 * (b0 * tia_flat[kp1] + b1 * tia_flat[kp1 + 1])
                          + w3 * (b0 * tia_flat[kp2] + b1 * tia_flat[kp2 + 1]))

                cig = ig - dig_off
                cia = ia - dia_off
            elif adaa == 1:
                u = (p0 - p0_min) * inv_dp0
                p1m = 0.5 * (p1 + p1_prev)
                w = (p1m - p1_min) * inv_dp1
                if w <= 0.0:
                    j, fw = 0, 0.0
                elif w >= n1m1:
                    j, fw = n1m2, 1.0
                else:
                    j = int(w)
                    fw = w - j
                if j != j_prev:
                    pg0, pg1, pa0, pa1 = tbl.f1_slices(u_prev, j)
                g0, g1, a0, a1 = tbl.f1_slices(u, j)
                du = u - u_prev
                if abs(du) < ADAA_TOL_1:
                    cig, cia = tbl.lookup(0.5 * (p0 + p0_prev), p1m)
                else:
                    cig = ((g0 + fw * (g1 - g0)) - (pg0 + fw * (pg1 - pg0))) / du
                    cia = ((a0 + fw * (a1 - a0)) - (pa0 + fw * (pa1 - pa0))) / du
                p0_prev, p1_prev = p0, p1
                u_prev = u
                j_prev, pg0, pg1, pa0, pa1 = j, g0, g1, a0, a1
            else:
                u = (p0 - p0_min) * inv_dp0
                p1m = (p1 + p1_prev + p1_prev2) / 3.0
                w = (p1m - p1_min) * inv_dp1
                if w <= 0.0:
                    j, fw = 0, 0.0
                elif w >= n1m1:
                    j, fw = n1m2, 1.0
                else:
                    j = int(w)
                    fw = w - j
                b0, b1 = 1.0 - fw, fw
                du2 = 0.5 * (u - u_prev2)
                if abs(du2) < ADAA_TOL_2:
                    u_bar = 0.5 * (u + u_prev2)
                    du_a = u_bar - u_prev
                    if abs(du_a) < ADAA_TOL_2:
                        cig, cia = tbl.lookup(p0_min + 0.5 * (u_bar + u_prev) / inv_dp0, p1m)
                    else:
                        f1g_b, f1a_b, f2g_b, f2a_b = tbl.f1_f2(u_bar, j, b0, b1)
                        _, _, f2g_1, f2a_1 = tbl.f1_f2(u_prev, j, b0, b1)
                        cig = 2.0 * (f1g_b - (f2g_b - f2g_1) / du_a) / du_a
                        cia = 2.0 * (f1a_b - (f2a_b - f2a_1) / du_a) / du_a
                else:
                    du_n = u - u_prev
                    if abs(du_n) < ADAA_TOL_2:
                        d1g_n, d1a_n, _, _ = tbl.f1_f2(0.5 * (u + u_prev), j, b0, b1)
                    else:
                        _, _, f2g_n, f2a_n = tbl.f1_f2(u, j, b0, b1)
                        _, _, f2g_1, f2a_1 = tbl.f1_f2(u_prev, j, b0, b1)
                        d1g_n, d1a_n = (f2g_n - f2g_1) / du_n, (f2a_n - f2a_1) / du_n
                    du_p = u_prev - u_prev2
                    if abs(du_p) < ADAA_TOL_2:
                        d1g_p, d1a_p, _, _ = tbl.f1_f2(0.5 * (u_prev + u_prev2), j, b0, b1)
                    else:
                        _, _, f2g_1, f2a_1 = tbl.f1_f2(u_prev, j, b0, b1)
                        _, _, f2g_2, f2a_2 = tbl.f1_f2(u_prev2, j, b0, b1)
                        d1g_p, d1a_p = (f2g_1 - f2g_2) / du_p, (f2a_1 - f2a_2) / du_p
                    cig = (d1g_n - d1g_p) / du2
                    cia = (d1a_n - d1a_p) / du2
                p0_prev2, p1_prev2 = p0_prev, p1_prev
                p0_prev, p1_prev = p0, p1
                u_prev2, u_prev = u_prev, u

            di0 = cig - ig0
            di1 = cia - ia0

            if out_only:
                out_arr[n] = d00 * x0 + d01 * x1 + d02 * x2 + e00 * s + f00 * di0 + f01 * di1 + ydc0
            else:
                for nm, r_idx in idx_map:
                    rec[nm][n] = (float(D[r_idx, 0]) * x0 + float(D[r_idx, 1]) * x1 + float(D[r_idx, 2]) * x2
                                  + float(E0[r_idx]) * s + float(F[r_idx, 0]) * di0 + float(F[r_idx, 1]) * di1
                                  + float(y_dc[r_idx]))
                if want_vgk:
                    rec["vgk"][n] = p0 - K00 * cig - K01 * cia

            nx0 = a00 * x0 + a01 * x1 + a02 * x2 + b00 * s + c00 * di0 + c01 * di1
            nx1 = a10 * x0 + a11 * x1 + a12 * x2 + b10 * s + c10 * di0 + c11 * di1
            nx2 = a20 * x0 + a21 * x1 + a22 * x2 + b20 * s + c20 * di0 + c21 * di1
            x0, x1, x2 = nx0, nx1, nx2

        self.x[0], self.x[1], self.x[2] = x0, x1, x2
        self.lp_x, self.lp_y = lp_x, lp_y
        self.p_prev = [p0_prev, p1_prev]
        self.p_prev2 = [p0_prev2, p1_prev2]
        self.j_prev = j_prev
        self.pg0, self.pg1, self.pa0, self.pa1 = pg0, pg1, pa0, pa1
        return rec

    def _process_internal_newton(self, vin, record):
        """Exact Newton solver on the 3-state circuit (ablation reference for isolating LUT error)."""
        m = self.m
        A, C, D, F, G = (m[k] for k in "ACDFG")
        B0, E0, H0 = m["B"][:, 0], m["E"][:, 0], m["H"][:, 0]
        p_dc, y_dc, (ig0, ia0) = self.p_dc, self.y_dc, self.i0
        rec = {n: np.empty(len(vin)) for n in record}
        idx = {n: m["out_names"].index(n) for n in record if n in m["out_names"]}
        x, (v0, v1), K, par, ev = self.x, self.v, self.K, self.par, self.ev
        q0, q1 = self.v_prev
        for n, s in enumerate(vin):
            p = G @ x + H0 * s + p_dc
            e0, e1 = 2.0 * v0 - q0, 2.0 * v1 - q1
            q0, q1 = v0, v1
            v0, v1, ig, ia, _, _ = solve(p[0], p[1], K, e0, e1, par, MAX_IT, TOL_V, TOL_F, ev=ev)
            inl = np.array([ig - ig0, ia - ia0])
            if idx:
                y = D @ x + E0 * s + F @ inl + y_dc
                for nm, j in idx.items():
                    rec[nm][n] = y[j]
            if "vgk" in rec:
                rec["vgk"][n] = v0
            x = A @ x + B0 * s + C @ inl
        self.x, self.v, self.v_prev = x, [v0, v1], [q0, q1]
        return rec

    def process_internal(self, vin, record=("out",)):
        """Run the 3-state stage at internal rate self.fs on an input buffer already at self.fs."""
        if self.solver_mode == "lut":
            return self._process_internal_lut(vin, record)
        return self._process_internal_newton(vin, record)

    def _apply_treble_comp(self, y):
        b0, b1, p = self.treble_comp_coefs
        tc_x, tc_y = self.tc_x, self.tc_y
        out = np.empty_like(y)
        for n, x_val in enumerate(y):
            xv = float(x_val)
            yv = b0 * xv + b1 * tc_x + p * tc_y
            tc_x, tc_y = xv, yv
            out[n] = yv
        self.tc_x, self.tc_y = tc_x, tc_y
        return out

    def process(self, vin, record=("out",)):
        """Base-rate in, base-rate out (2x IIR oversampling around the 3-state stage)."""
        vin = self.drive * np.asarray(vin, dtype=float)
        rec = self.process_internal(self.rs.up(vin), record)
        out = {}
        for k, v in rec.items():
            y = self.rs.down(v, channel=k)
            if k == "out":
                if self.treble_comp:
                    y = self._apply_treble_comp(y)
                out[k] = self.output * y
            else:
                out[k] = y
        out["_internal"] = rec
        return out
