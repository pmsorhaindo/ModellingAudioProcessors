"""Per-sample Newton-Raphson for the 6AS6 DK stage (dk/solver.py's method, for a 2-current / 4-voltage valve).

Solve  v = p - K i(v),  p = G x + H u,  v = (Vg1k, Vg2k, Vg3k, Vak),  i = (Ip, Ig2)   (see circuit.py)
The valve injects 2 currents but depends on 4 voltages, so the unknowns are the currents: with v(i) = p - K i,
  R(i) = i - i(v(i)) = 0,   dR/di = I + J K   (J = d i / d v, 2x4; a 2x2 Newton step).
This is the same Newton iteration as dk/solver.py's voltage form F(v) = p - K i(v) - v restricted to the affine set
v = p - K i that every iterate lies in (F(p - K i) = K R(i)), so the tolerances keep their meaning:
  converged when ||K di||inf < TOL_V (voltage step) and ||K R||inf < TOL_F (residual of the voltage equations);
  hard cap MAX_IT; backtracking line search halving the step up to LS_MAX times until ||K R||inf decreases.
No step limiter: there is no grid-current exponential (Ig1 = Ig3 = 0); the plate and screen laws are power laws
of softplus terms. Warm start: supplied by the caller (Stage extrapolates the currents, 2 i[n-1] - i[n-2]).
Non-convergence contract as dk/solver.py (#7): on the cap, return the iterate with the smallest finite residual
(the warm start counts) and converged=False; NaN/inf never wins.
"""
import math
from dk.solver import TOL_V, TOL_F, MAX_IT, LS_MAX  # noqa: F401  (the 12AX7 stage's tolerances, unchanged)
from .pentode import currents, currents_only, DUAL_CONTROL


def _volts(p, K, i0, i1):
    return [p[r] - K[r][0] * i0 - K[r][1] * i1 for r in range(4)]


def _resid(p, K, i0, i1, par, jac=True):
    v = _volts(p, K, i0, i1)
    if jac:
        ip, ig2, J = currents(v[0], v[1], v[2], v[3], par)
    else:
        (ip, ig2), J = currents_only(v[0], v[1], v[2], v[3], par), None
    r0, r1 = i0 - ip, i1 - ig2
    fv = max(abs(K[r][0] * r0 + K[r][1] * r1) for r in range(4))
    return r0, r1, fv, v, J


def solve(p, K, i0, i1, par=DUAL_CONTROL, max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F):
    """Return (v, Ip, Ig2, iterations, converged): v = p - K i at the last current iterate i, v = [Vg1k, Vg2k, Vg3k,
    Vak], and Ip, Ig2 = the valve law at v (they differ from i by the residual R, as dk/solver.py returns i(v))."""
    r0, r1, fv, v, J = _resid(p, K, i0, i1, par)
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
        g0, g1, gv, gvolt, gJ = _resid(p, K, i0 + s0, i1 + s1, par)
        if not (gv < fv or fv < tol_f):
            for _ in range(LS_MAX - 1):
                lam *= 0.5
                g0, g1, gv, gvolt, _ = _resid(p, K, i0 + lam * s0, i1 + lam * s1, par, jac=False)
                if gv < fv:
                    break
            gJ = currents(*gvolt, par)[2]
        dv = max(abs(K[r][0] * lam * s0 + K[r][1] * lam * s1) for r in range(4))
        i0, i1 = i0 + lam * s0, i1 + lam * s1
        r0, r1, fv, v, J = g0, g1, gv, gvolt, gJ
        if dv < tol_v and fv < tol_f:
            return v, i0 - r0, i1 - r1, it, True
        if (fv < fb or not fb < math.inf) and abs(r0) + abs(r1) < math.inf:
            fb, best = fv, (v, i0 - r0, i1 - r1)
    return best + (max_it, False)
