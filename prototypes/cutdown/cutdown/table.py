"""Precomputed 2-D lookup table of the nonlinear DK solution (p0, p1) -> (Ig, Ia) (#23).

At each internal sample, the DK nonlinear equation is
    F(v) = p - K i(v) - v = 0,   v = (Vgk, Vak),   i(v) = (Ig, Ia),
where p = p_dc + G x + H0 * vin. Instead of running Newton-Raphson per sample, the table
pre-solves (Ig(p0, p1), Ia(p0, p1)) on a uniform 2-D grid over p = (p0, p1) using the
Newton solver from prototypes/fullstage/dk/solver.py at build / bias-swap time.

Memory layout: shape (n0, n1) per port -> 2 * n0 * n1 floats. Default is (128, 32):
2 * 128 * 32 = 8,192 words (8 K words, matching 64 * 64 * 2 = 8,192 words, well within
the DSP56300's 32 K Y-private memory). Placing 128 points along p0 (the strongly nonlinear
grid/cutoff axis) and 32 points along p1 (the nearly linear plate axis) with Catmull-Rom
cubic interpolation along p0 and linear along p1 ("cubic0_lin1") eliminates the
piecewise-linear slope kinks of bilinear interpolation at small signal levels while
costing 8 grid reads per port instead of 16 for full bicubic. "bilinear" (e.g. 64x64) is
also supported for comparison.
"""
import numpy as np
import cutdown  # noqa: F401 -- ensures FULLSTAGE_DIR is on sys.path
from dk.solver import solve  # noqa: E402 -- after cutdown path setup
from dk.triode import DZ_12AX7_RSD1, currents, currents_only  # noqa: E402 -- after cutdown path setup

DEFAULT_SHAPE = (128, 32)          # 128 * 32 * 2 = 8,192 words (same budget as 64 * 64 * 2)
DEFAULT_P0_SPAN = (-15.0, 10.0)    # V relative to p_dc[0] (at bias=1: [-16.51, +8.49] V)
DEFAULT_P1_SPAN = (-20.0, 20.0)    # V relative to p_dc[1] (at bias=1: [269.89, 309.89] V)
INTERP_MODES = ("cubic0_lin1", "bilinear")


def _catmull_rom_weights(t):
    t2 = t * t
    t3 = t2 * t
    return (
        -0.5 * t + t2 - 0.5 * t3,
        1.0 - 2.5 * t2 + 1.5 * t3,
        0.5 * t + 2.0 * t2 - 1.5 * t3,
        -0.5 * t2 + 0.5 * t3,
    )


def _catmull_rom_derivs(t):
    t2 = t * t
    return (
        -0.5 + 2.0 * t - 1.5 * t2,
        -5.0 * t + 4.5 * t2,
        0.5 + 4.0 * t - 4.5 * t2,
        -t + 1.5 * t2,
    )


class SolutionTable:
    """2-D lookup table mapping DK port Thevenin voltage p = (p0, p1) to valve currents (Ig, Ia)."""

    def __init__(self, K, p_dc, v_dc, i_dc, par=DZ_12AX7_RSD1, shape=DEFAULT_SHAPE,
                 p0_span=DEFAULT_P0_SPAN, p1_span=DEFAULT_P1_SPAN, interp="cubic0_lin1"):
        if interp not in INTERP_MODES:
            raise ValueError(f"interp must be one of {INTERP_MODES}, got {interp!r}")
        n0, n1 = int(shape[0]), int(shape[1])
        if n0 < 4 or n1 < 2:
            raise ValueError(f"table shape must be at least (4, 2), got {shape}")
        self.n0, self.n1 = n0, n1
        self.interp = interp
        self.p_dc = np.asarray(p_dc, float)
        self.i_dc = np.asarray(i_dc, float)
        self.p0_min = float(self.p_dc[0] + p0_span[0])
        self.p0_max = float(self.p_dc[0] + p0_span[1])
        self.p1_min = float(self.p_dc[1] + p1_span[0])
        self.p1_max = float(self.p_dc[1] + p1_span[1])
        self.p0_grid = np.linspace(self.p0_min, self.p0_max, n0)
        self.p1_grid = np.linspace(self.p1_min, self.p1_max, n1)
        self.inv_dp0 = (n0 - 1) / (self.p0_max - self.p0_min)
        self.inv_dp1 = (n1 - 1) / (self.p1_max - self.p1_min)

        K_list = np.asarray(K, float).tolist()
        tbl_ig = np.empty((n0, n1), dtype=float)
        tbl_ia = np.empty((n0, n1), dtype=float)
        ev = (currents, currents_only)
        for i, pp0 in enumerate(self.p0_grid):
            v_init0 = min(float(pp0), 0.0)
            for j, pp1 in enumerate(self.p1_grid):
                # Start from (min(p0, 0), p1) rather than reusing exact previous v0: reusing exact v0
                # at constant p0 can trigger float64 underflow of (v0 + d0) - v0 in dk.solver.solve.
                _, _, cig, cia, _, ok = solve(
                    float(pp0), float(pp1), K_list, v_init0, float(pp1), par, max_it=100, ev=ev)
                if not ok:
                    _, _, cig, cia, _, ok = solve(
                        float(pp0), float(pp1), K_list, float(v_dc[0]), float(v_dc[1]), par, max_it=200, ev=ev)
                if not ok:
                    raise RuntimeError(f"LUT build failed to converge at p0={pp0:.6g}, p1={pp1:.6g}")
                tbl_ig[i, j] = cig
                tbl_ia[i, j] = cia
        self.tbl_ig = tbl_ig
        self.tbl_ia = tbl_ia

        # Exact DC-rest offset: shift raw table output by the interpolation residual at p_dc
        # so silence produces di_nl = (0, 0) exactly (no startup thump or DC drift).
        ig_raw, ia_raw = self.lookup_raw(float(self.p_dc[0]), float(self.p_dc[1]))
        self.dig_off = float(ig_raw - self.i_dc[0])
        self.dia_off = float(ia_raw - self.i_dc[1])

    @property
    def word_count(self):
        """Total scalar table entries across both ports (Ig, Ia)."""
        return 2 * self.n0 * self.n1

    def _cell(self, p0, p1):
        u = (p0 - self.p0_min) * self.inv_dp0
        w = (p1 - self.p1_min) * self.inv_dp1
        if u <= 0.0:
            i, fu = 0, 0.0
        elif u >= self.n0 - 1:
            i, fu = self.n0 - 2, 1.0
        else:
            i = int(u)
            fu = u - i
        if w <= 0.0:
            j, fw = 0, 0.0
        elif w >= self.n1 - 1:
            j, fw = self.n1 - 2, 1.0
        else:
            j = int(w)
            fw = w - j
        return i, fu, j, fw

    def lookup_raw(self, p0, p1):
        """Interpolate (Ig, Ia) at (p0, p1) without the DC-rest offset subtraction."""
        i, fu, j, fw = self._cell(p0, p1)
        b0, b1 = 1.0 - fw, fw
        tig, tia = self.tbl_ig, self.tbl_ia
        if self.interp == "bilinear":
            a0, a1 = 1.0 - fu, fu
            ig = a0 * (b0 * tig[i, j] + b1 * tig[i, j + 1]) + a1 * (b0 * tig[i + 1, j] + b1 * tig[i + 1, j + 1])
            ia = a0 * (b0 * tia[i, j] + b1 * tia[i, j + 1]) + a1 * (b0 * tia[i + 1, j] + b1 * tia[i + 1, j + 1])
            return ig, ia

        im1 = i - 1 if i > 0 else 0
        ip1 = i + 1
        ip2 = i + 2 if i + 2 < self.n0 else self.n0 - 1
        w0, w1, w2, w3 = _catmull_rom_weights(fu)
        ig = (w0 * (b0 * tig[im1, j] + b1 * tig[im1, j + 1])
              + w1 * (b0 * tig[i, j] + b1 * tig[i, j + 1])
              + w2 * (b0 * tig[ip1, j] + b1 * tig[ip1, j + 1])
              + w3 * (b0 * tig[ip2, j] + b1 * tig[ip2, j + 1]))
        ia = (w0 * (b0 * tia[im1, j] + b1 * tia[im1, j + 1])
              + w1 * (b0 * tia[i, j] + b1 * tia[i, j + 1])
              + w2 * (b0 * tia[ip1, j] + b1 * tia[ip1, j + 1])
              + w3 * (b0 * tia[ip2, j] + b1 * tia[ip2, j + 1]))
        return ig, ia

    def lookup(self, p0, p1):
        """Interpolate (Ig, Ia) at (p0, p1) with the DC-rest correction applied."""
        ig, ia = self.lookup_raw(p0, p1)
        return ig - self.dig_off, ia - self.dia_off

    def jacobian_p(self, p0, p1):
        """Exact analytical 2x2 Jacobian d(Ig, Ia)/d(p0, p1) of the interpolant at (p0, p1)."""
        i, fu, j, fw = self._cell(p0, p1)
        b0, b1 = 1.0 - fw, fw
        M = np.empty((2, 2), dtype=float)
        if self.interp == "bilinear":
            a0, a1 = 1.0 - fu, fu
            for r, tbl in enumerate((self.tbl_ig, self.tbl_ia)):
                M[r, 0] = (b0 * (tbl[i + 1, j] - tbl[i, j])
                           + b1 * (tbl[i + 1, j + 1] - tbl[i, j + 1])) * self.inv_dp0
                M[r, 1] = (a0 * (tbl[i, j + 1] - tbl[i, j])
                           + a1 * (tbl[i + 1, j + 1] - tbl[i + 1, j])) * self.inv_dp1
            return M

        im1 = i - 1 if i > 0 else 0
        ip1 = i + 1
        ip2 = i + 2 if i + 2 < self.n0 else self.n0 - 1
        wt = _catmull_rom_weights(fu)
        dw = _catmull_rom_derivs(fu)
        idxs = (im1, i, ip1, ip2)
        for r, tbl in enumerate((self.tbl_ig, self.tbl_ia)):
            M[r, 0] = sum(dw[k] * (b0 * tbl[idxs[k], j] + b1 * tbl[idxs[k], j + 1]) for k in range(4)) * self.inv_dp0
            M[r, 1] = sum(wt[k] * (tbl[idxs[k], j + 1] - tbl[idxs[k], j]) for k in range(4)) * self.inv_dp1
        return M
