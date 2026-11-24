"""Offline grid-only ADAA prototype for #154.

Eliminate the grid unknown with a scalar load line q = Vgk + K[0,2] Ig1.
Apply ADAA in Vgk to the existing estimated, unvalidated grid law. The
two-current Newton solve sees the eliminated grid stamp through
q = p[0] - K[0,:2] @ (Ip, Ig2).
"""
from functools import lru_cache

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.optimize import brentq

from . import pentode
from .lpf import Stage as OrdinaryStage
from .solver import solve

TABLE_MIN = -4.0
TABLE_STEP = 1 / 256
TABLE_MAX = 32.0
# Volts at the load-line input. Use local Taylor/integral forms below this
# spacing to avoid cancellation of nearby antiderivative values.
EPS = 0.002
ROOT_TOL = 1e-12
ROOT_STEPS = 40
GL = ((0.1127016653792583, 5 / 18), (0.5, 4 / 9), (0.8872983346207417, 5 / 18))


@lru_cache(maxsize=64)
def load_line(resistance, gg, xi, cg):
    """Rows contain f'', f' etc. via the cubic f and its two primitives.

    Coefficients are in descending power of volts from the left knot, with
    the two integration constants (F1 and F2 at the knot) appended.
    """
    g = dict(GG=gg, XI=xi, CG=cg)
    q = np.arange(TABLE_MIN, TABLE_MAX + TABLE_STEP, TABLE_STEP)

    def current(x):
        def residual(v):
            return v + resistance * pentode.grid_current(v, g)[0] - x
        v = brentq(residual, min(TABLE_MIN, x) - 1, x, xtol=1e-14)
        return pentode.grid_current(v, g)[0]

    values = np.array([current(x) if resistance else pentode.grid_current(x, g)[0] for x in q])
    values[0] = 0.0  # below -4 V the borrowed fits are below 1e-25 A
    curve = CubicSpline(q, values, bc_type=((1, 0.0), "not-a-knot"))
    f1 = curve.antiderivative()
    f2 = f1.antiderivative()
    rows = np.column_stack((curve.c.T, f1.c[-1], f2.c[-1]))
    # Linear continuation at the upper knot for offline diagnostics.
    end = [0.0, 0.0, curve(q[-1], 1), values[-1], f1(q[-1]), f2(q[-1])]
    table = np.ascontiguousarray(np.vstack((rows, end)))
    table.setflags(write=False)
    return table


def evaluate(table, q):
    if q < TABLE_MIN:
        return (0.0,) * 6
    k = min(int((q - TABLE_MIN) / TABLE_STEP), len(table) - 1)
    t = q - (TABLE_MIN + k * TABLE_STEP)
    a, b, c, d, e, f = table[k]
    return (((a*t + b)*t + c)*t + d, ((3*a*t + 2*b)*t + c),
            6*a*t + 2*b, 6*a, ((((a/4*t + b/3)*t + c/2)*t + d)*t + e),
            (((((a/20*t + b/12)*t + c/6)*t + d/2)*t + e)*t + f))


def divided(table, x, y, ex, ey, order):
    """First divided difference of F1 or F2 and derivative in x."""
    d = x - y
    if abs(d) >= EPS:
        if order == 1:
            value = (ex[4] - ey[4]) / d
            return value, (ex[0] - value) / d
        value = (ex[5] - ey[5]) / d
        return value, (ex[4] - value) / d
    f, fp, fpp, fppp, f1, _ = evaluate(table, (x + y) / 2)
    # Bilbao et al. (17), retaining the quadratic term in the midpoint
    # expansion. For the cubic f this is exact within one table cell.
    if order == 1:
        return f + d*d*fpp/24, fp/2 + d*fpp/12 + d*d*fppp/48
    return f1 + d*d*fp/24, f/2 + d*fp/12 + d*d*fpp/48


class GridFilter:
    def __init__(self, table, order, dc):
        self.table, self.order = table, order
        self.reset(dc)

    def reset(self, dc):
        self.prev = self.older = dc
        self.ep = self.eo = evaluate(self.table, dc)
        self.slope = self.ep[4]

    def evaluate(self, q):
        cur = evaluate(self.table, q)
        if self.order == 0:
            return cur[:2]
        p, o = self.prev, self.older
        if self.order == 1:
            return divided(self.table, q, p, cur, self.ep, 1)
        ep = self.ep
        # The second divided difference is symmetric. Put the history point
        # farthest from q in the outer denominator (Bilbao et al., eq. 15).
        if abs(q - p) > abs(q - o):
            p, o, ep = o, p, self.eo
        d = q - o
        if abs(d) >= EPS:
            left, slope = divided(self.table, q, p, cur, ep, 2)
            value = 2 * (left - self.slope) / d
            return value, (2 * slope - value) / d
        # Coincident-node limit (eqs. 18–20), evaluated as the integral
        # of f over the barycentric simplex. Its derivative weights f'
        # by q's barycentric coordinate. The rule is exact for a cubic
        # on one table cell and avoids subtracting close slopes.
        value = derivative = 0.0
        for t, wt in GL:
            for u, wu in GL:
                v = t*q + (1-t)*(u*p + (1-u)*o)
                f, fp, *_ = evaluate(self.table, v)
                w = 2 * (1-t) * wt * wu
                value += w * f
                derivative += w * t * fp
        return value, derivative

    def advance(self, q):
        cur = evaluate(self.table, q)
        if self.order == 2:
            self.slope = divided(self.table, q, self.prev, cur, self.ep, 2)[0]
        self.older, self.eo = self.prev, self.ep
        self.prev, self.ep = q, cur

    def load(self, q, resistance, instant):
        """Return Vgk, Ig1, dIg1/dq on the grid load line at frozen history."""
        guess = evaluate(instant, q)[0]
        if self.order == 0:
            v = q - resistance * guess
            slope = evaluate(self.table, v)[1]
            return v, guess, slope / (1 + resistance * slope)
        hi = q  # f >= 0
        v = hi - resistance * guess
        lo = -np.inf
        for _ in range(ROOT_STEPS):
            current, slope = self.evaluate(v)
            r = v + resistance * current - q
            denominator = 1 + resistance * slope
            if abs(r) < ROOT_TOL:
                return v, current, slope / denominator
            if r < 0:
                lo = v
            else:
                hi = v
            nxt = v - r / denominator
            if not lo < nxt < hi:
                nxt = (lo + hi) / 2
            v = nxt
        raise RuntimeError("grid load line did not converge")


class Stage(OrdinaryStage):
    """Order 0 is a split control for comparison with the ordinary 3x3 solve."""

    def __init__(self, *args, order, **kwargs):
        if order not in (0, 1, 2):
            raise ValueError("grid ADAA order must be 0, 1 or 2")
        self.order = order
        super().__init__(*args, **kwargs)
        if self.grid is None:
            raise ValueError("the split screen requires grid current")
        shape = tuple(self.grid[k] for k in ("GG", "XI", "CG"))
        self.instant = load_line(self.K[0][2], *shape)
        self.filter = GridFilter(load_line(0.0, *shape), order, self.dc["vg1k"])

    def reset(self):
        super().reset()
        if hasattr(self, "filter"):
            self.filter.reset(self.dc["vg1k"])

    def _port_law(self, q):
        vgk, ig, dig = self.filter.load(q[0], self.K[0][2], self.instant)
        v = [vgk, *(q[j] - self.K[j][2]*ig for j in range(1, 4))]
        ik, dik, f, df = pentode.cathode_share(*v, self.par)
        ipk = ik - ig
        dipk, df = list(dik), list(df)
        dipk[0] -= dig * (1 + sum(k[2] * d for k, d in zip(self.K, dik)))
        df[0] -= dig * sum(k[2] * d for k, d in zip(self.K, df))
        ip = ipk * f
        dip = [dipk[j]*f + ipk*df[j] for j in range(4)]
        jac = [dip, [dipk[j] - dip[j] for j in range(4)]]
        return v, ip, ipk - ip, ig, jac

    def _split(self, p, start, previous):
        def law(q):
            _, ip, screen, _, jac = self._port_law(q)
            return ip, screen, jac

        best = None
        total = 0
        for attempt, initial in enumerate((start, previous, self.i_dc)):
            q, ip, screen, iterations, ok = solve(p, self.K, *initial[:2], self.par, *self.newton, law=law)
            total += iterations
            residual = max(abs(p[j] - self.K[j][0]*ip - self.K[j][1]*screen - q[j]) for j in range(4))
            if best is None or residual < best[0] or ok:
                best = residual, q, ip, screen
            if ok:
                break
        self.restarted += attempt > 0
        _, q, ip, screen = best
        # Reconstruct the actual voltages and commit history only after Newton.
        vgk, ig, _ = self.filter.load(q[0], self.K[0][2], self.instant)
        self.filter.advance(vgk)
        v = [vgk, *(q[j] - self.K[j][2]*ig for j in range(1, 4))]
        return v, [ip, screen, ig], total, ok

    def process_internal(self, vin, record=("out",)):
        # The original DK and post-filter matrices run at their original
        # rate. Only the grid branch has history; the other stamps use v[n].
        m = self.m
        rec = {name: np.empty(len(vin)) for name in ("out", *record)}
        nodes = {name: m["out_names"].index(name) for name in rec if name in m["out_names"]}
        iters = np.empty(len(vin), dtype=np.int16)
        x, i, prev = self.x, self.i, self.i_prev
        bad = outside = 0
        for n, u in enumerate(vin):
            p = (m["G"] @ x + m["H"][:, 0]*u + self.p_dc).tolist()
            start = [2*a - b for a, b in zip(i, prev)]
            v, nxt, iters[n], ok = self._split(p, start, i)
            prev, i = i, nxt
            bad += not ok
            outside += v[3] < 0 or i[0] + i[1] < 0
            di = i - self.i_dc
            if nodes:
                out = m["D"] @ x + m["E"][:, 0]*u + m["F"] @ di + self.y_dc
                for name, j in nodes.items():
                    rec[name][n] = out[j]
            for name, value in zip(("vgk", "vg2k", "vak", "ig1", "ipk"), (*v[:2], v[3], i[2], i[0]+i[1])):
                if name in rec:
                    rec[name][n] = value
            x = m["A"] @ x + m["B"][:, 0]*u + m["C"] @ di
        self.x, self.i, self.i_prev = x, i, prev
        self.iters.append(iters)
        self.nonconv += bad
        self.out_of_law += outside
        self.nonconv_block, self.out_of_law_block = bad, outside
        out = self.lpf.process(rec["out"])
        if "out" in record:
            rec["out"] = out
        else:
            del rec["out"]
        return rec
