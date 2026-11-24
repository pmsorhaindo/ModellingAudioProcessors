"""Per-sample Newton-Raphson for the 6AS6 DK stage (dk/solver.py's method, for a 2-current / 4-voltage valve).

Solve  v = p - K i(v),  p = G x + H u,  v = (Vg1k, Vg2k, Vg3k, Vak),  i = (Ip, Ig2)   (see circuit.py)
The valve injects 2 currents but depends on 4 voltages, so the unknowns are the currents: with v(i) = p - K i,
  R(i) = i - i(v(i)) = 0,   dR/di = I + J K   (J = d i / d v, 2x4; a 2x2 Newton step).
This is the same Newton iteration as dk/solver.py's voltage form F(v) = p - K i(v) - v restricted to the affine set
v = p - K i that every iterate lies in (F(p - K i) = K R(i)), so the tolerances keep their meaning:
  converged when ||K di||inf < TOL_V (voltage step) and ||K R||inf < TOL_F (residual of the voltage equations);
  hard cap MAX_IT; backtracking line search halving the step up to LS_MAX times until ||K R||inf decreases.
No step limiter: the plate and screen laws are power laws of softplus terms, and so is the optional g1 current
(#112, DZ's softplus form, slope at most CG XI ~ 13 /V, against a junction's 39 /V): no exponential for pnjlim.
solve_grid() is the same iteration with the g1 current as a third current, i = (Ip, Ig2, Ig1) (3x3 step); solve() is
the two-current law without it, unchanged.
Warm start: supplied by the caller (Stage extrapolates the currents, 2 i[n-1] - i[n-2]).
Non-convergence contract as dk/solver.py (#7): on the cap, return the iterate with the smallest finite residual
(the warm start counts) and converged=False; NaN/inf never wins.
"""
import math
from dk.solver import TOL_V, TOL_F, MAX_IT, LS_MAX  # noqa: F401  (the 12AX7 stage's tolerances, unchanged)
from .pentode import currents, currents_only, currents_grid, currents_grid_only, DUAL_CONTROL, GRID


def _volts(p, K, i0, i1):
    return [p[r] - K[r][0] * i0 - K[r][1] * i1 for r in range(4)]


def _resid(p, K, i0, i1, par, jac=True, law=None):
    v = _volts(p, K, i0, i1)
    if law is not None:
        ip, ig2, J = law(v)
    elif jac:
        ip, ig2, J = currents(v[0], v[1], v[2], v[3], par)
    else:
        (ip, ig2), J = currents_only(v[0], v[1], v[2], v[3], par), None
    r0, r1 = i0 - ip, i1 - ig2
    fv = max(abs(K[r][0] * r0 + K[r][1] * r1) for r in range(4))
    return r0, r1, fv, v, J


def solve(p, K, i0, i1, par=DUAL_CONTROL, max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F, *, law=None):
    """Return (v, Ip, Ig2, iterations, converged): v = p - K i at the last current iterate i, v = [Vg1k, Vg2k, Vg3k,
    Vak], and Ip, Ig2 = the valve law at v (they differ from i by the residual R, as dk/solver.py returns i(v)).
    An offline caller may substitute a two-port law (arguments and output as currents())."""
    r0, r1, fv, v, J = _resid(p, K, i0, i1, par, law=law)
    fb, best = fv, (v, i0 - r0, i1 - r1)
    for it in range(1, max_it + 1):
        # dR/di = I + J K
        a = 1.0 + sum(J[0][r] * K[r][0] for r in range(4))
        b = sum(J[0][r] * K[r][1] for r in range(4))
        c = sum(J[1][r] * K[r][0] for r in range(4))
        d = 1.0 + sum(J[1][r] * K[r][1] for r in range(4))
        det = a * d - b * c
        s0 = -(d * r0 - b * r1) / det
        s1 = -(-c * r0 + a * r1) / det
        lam = 1.0
        g0, g1, gv, gvolt, gJ = _resid(p, K, i0 + s0, i1 + s1, par, law=law)
        if not (gv < fv or fv < tol_f):
            for _ in range(LS_MAX - 1):
                lam *= 0.5
                g0, g1, gv, gvolt, gJ = _resid(p, K, i0 + lam * s0, i1 + lam * s1, par, jac=False, law=law)
                if gv < fv:
                    break
            if law is None:
                gJ = currents(*gvolt, par)[2]
        dv = max(abs(K[r][0] * lam * s0 + K[r][1] * lam * s1) for r in range(4))
        i0, i1 = i0 + lam * s0, i1 + lam * s1
        r0, r1, fv, v, J = g0, g1, gv, gvolt, gJ
        if dv < tol_v and fv < tol_f:
            return v, i0 - r0, i1 - r1, it, True
        if (fv < fb or not fb < math.inf) and abs(r0) + abs(r1) < math.inf:
            fb, best = fv, (v, i0 - r0, i1 - r1)
    return best + (max_it, False)


def _resid3(p, K, i, par, g, jac=True):
    a, b, c = i
    v = [p[r] - K[r][0] * a - K[r][1] * b - K[r][2] * c for r in range(4)]
    if jac:
        ip, ig2, ig1, J = currents_grid(v[0], v[1], v[2], v[3], par, g)
    else:
        (ip, ig2, ig1), J = currents_grid_only(v[0], v[1], v[2], v[3], par, g), None
    r0, r1, r2 = a - ip, b - ig2, c - ig1
    fv = max(abs(K[q][0] * r0 + K[q][1] * r1 + K[q][2] * r2) for q in range(4))
    return (r0, r1, r2), fv, v, J


def _step3(J, K, r):
    """-(I + J K)^-1 r by Cramer's rule (3x3). J's g1 row is (dIg1/dVg1k, 0, 0, 0) (pentode.currents_grid)."""
    (j00, j01, j02, j03), (j10, j11, j12, j13), (j20, _, _, _) = J
    (k00, k01, k02), (k10, k11, k12), (k20, k21, k22), (k30, k31, k32) = K
    m00 = 1.0 + j00 * k00 + j01 * k10 + j02 * k20 + j03 * k30
    m01 = j00 * k01 + j01 * k11 + j02 * k21 + j03 * k31
    m02 = j00 * k02 + j01 * k12 + j02 * k22 + j03 * k32
    m10 = j10 * k00 + j11 * k10 + j12 * k20 + j13 * k30
    m11 = 1.0 + j10 * k01 + j11 * k11 + j12 * k21 + j13 * k31
    m12 = j10 * k02 + j11 * k12 + j12 * k22 + j13 * k32
    m20, m21, m22 = j20 * k00, j20 * k01, 1.0 + j20 * k02
    r0, r1, r2 = r
    c0 = m11 * m22 - m12 * m21
    c1 = m12 * m20 - m10 * m22
    c2 = m10 * m21 - m11 * m20
    det = m00 * c0 + m01 * c1 + m02 * c2
    x0 = (r0 * c0 + m01 * (r2 * m12 - r1 * m22) + m02 * (r1 * m21 - r2 * m11)) / det
    x1 = (m00 * (r1 * m22 - r2 * m12) + r0 * c1 + m02 * (r2 * m10 - r1 * m20)) / det
    x2 = (m00 * (r2 * m11 - r1 * m21) + m01 * (r1 * m20 - r2 * m10) + r0 * c2) / det
    return -x0, -x1, -x2


def _resid3_safe(p, K, i, par, g, jac=True):
    """_resid3, with an evaluation that overflows (or yields NaN) reported as an infinite residual, so that a wild
    trial step is backtracked or ends the attempt instead of raising or poisoning the iterate (#112 review)."""
    try:
        r, fv, v, J = _resid3(p, K, i, par, g, jac)
    except (OverflowError, ValueError, ZeroDivisionError):
        return None, math.inf, None, None
    if not fv < math.inf or (jac and not all(math.isfinite(x) for row in J for x in row)):
        return None, math.inf, None, None
    return r, fv, v, J


def solve_grid(p, K, i, par=DUAL_CONTROL, g=GRID, max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F):
    """solve() with the g1 current (#112): K is 4x3, i = [Ip, Ig2, Ig1] the start. Returns (v, [Ip, Ig2, Ig1],
    iterations, converged, residual): the currents are the valve law at v, as solve(); residual is ||K R||inf there
    (inf if no finite iterate was found). On the cap, the iterate with the smallest finite residual (the start counts).
    A singular or non-finite Newton step, or a step whose every backtracked trial fails to evaluate, ends the attempt
    as not converged (so a caller can restart, solve_grid_restarts)."""
    r, fv, v, J = _resid3_safe(p, K, i, par, g)
    if r is None:
        return [p[q] - K[q][0] * i[0] - K[q][1] * i[1] - K[q][2] * i[2] for q in range(4)], list(i), 0, False, math.inf
    fb, best = fv, (v, [i[k] - r[k] for k in range(3)])
    it = 0
    for it in range(1, max_it + 1):
        try:
            s = _step3(J, K, r)
        except ZeroDivisionError:
            break                                            # singular I + J K
        if not all(math.isfinite(x) for x in s):
            break
        lam = 1.0
        t = [i[k] + s[k] for k in range(3)]
        gr, gv, gvolt, gJ = _resid3_safe(p, K, t, par, g)
        if not (gv < fv or fv < tol_f):
            for _ in range(LS_MAX - 1):
                lam *= 0.5
                t = [i[k] + lam * s[k] for k in range(3)]
                gr, gv, gvolt, _ = _resid3_safe(p, K, t, par, g, jac=False)
                if gv < fv:
                    break
            if gr is not None:
                gr, gv, gvolt, gJ = _resid3_safe(p, K, t, par, g)
        if gr is None:
            break                                            # no finite trial along the step
        dv = max(abs(lam * (K[q][0] * s[0] + K[q][1] * s[1] + K[q][2] * s[2])) for q in range(4))
        i = t
        r, fv, v, J = gr, gv, gvolt, gJ
        if dv < tol_v and fv < tol_f:
            return v, [i[k] - r[k] for k in range(3)], it, True, fv
        if fv < fb:
            fb, best = fv, (v, [i[k] - r[k] for k in range(3)])
    return best + (it, False, fb)


def solve_grid_restarts(p, K, starts, par=DUAL_CONTROL, g=GRID, max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F):
    """solve_grid from each start in turn until one converges (#112: the stage's extrapolated warm start, then the
    previous sample's currents, then the operating point). Returns (v, i, iterations summed over the attempts,
    converged, residual, attempts). If none converges: the candidate with the smallest finite residual over all the
    attempts, converged False."""
    best, total = None, 0
    for k, start in enumerate(starts, 1):
        v, i, it, ok, fv = solve_grid(p, K, start, par, g, max_it, tol_v, tol_f)
        total += it
        if ok:
            return v, i, total, True, fv, k
        if best is None or fv < best[3]:
            best = (v, i, ok, fv)
    v, i, ok, fv = best
    return v, i, total, False, fv, len(starts)
