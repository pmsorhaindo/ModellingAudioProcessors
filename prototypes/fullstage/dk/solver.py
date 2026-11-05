"""Per-sample Newton-Raphson for the 2-port nonlinear equation of the DK model, and the DC solve.

Solve  F(v) = p - K i(v) - v = 0,  p = G x + H u,  v = (Vgk, Vak)   (see circuit.py)
  * warm start: supplied by the caller (Stage uses the linear extrapolation 2 v[n-1] - v[n-2], #15);
  * step limiting on Vgk (the exponential grid-current port), SPICE pnjlim style (Nagel 1975):
    above VCRIT a step larger than 2*VT is compressed logarithmically, VT = 1/Cg;
  * backtracking line search: halve the step (up to LS_MAX times) until ||F||inf decreases; the full step
    is evaluated with its Jacobian (it is accepted almost always), halved trials evaluate currents only and
    the Jacobian is computed once at the accepted point (audit finding 8);
  * converged when ||dv||inf < TOL_V and ||F||inf < TOL_F; hard cap MAX_IT iterations (all three can be
    overridden per call: max_it, tol_v, tol_f).
No lookup tables. Iteration counts are returned so callers can histogram them.

Non-convergence (#7). This is also the contract for the C++ core. When the cap is reached, solve() returns
the BEST iterate it saw, i.e. the one with the smallest finite ||F||inf (the warm start counts), and not
the last one. It returns converged=False with it. A NaN/inf iterate never wins, so a finite start always
gives a finite result. The caller keeps that iterate as the sample's state; it does not reset it or NaN
it, so the next sample warm-starts from a finite point. The caller must also count the failure and
report it: Stage adds to nonconv, sets nonconv_block, warns NonConvergenceWarning and shows it in
iteration_stats(). The DC solve is the exception: it has no previous state to fall back on, so it raises
(stage.OperatingPointError).
"""
import math
from .triode import currents, currents_only, DZ_12AX7_RSD1

TOL_V = 1e-6      # V  (was 1e-9: ~5e-12 relative on Vak, below float32 and audio relevance; #15)
TOL_F = 1e-6      # V (residual of a voltage equation)
MAX_IT = 50
LS_MAX = 10
VT = 1.0 / DZ_12AX7_RSD1["Cg"]   # ~0.10 V, the exponential scale of the grid-current law
VCRIT = 0.0


def pnjlim(vnew, vold, vt=VT, vcrit=VCRIT):
    if vnew > vcrit and abs(vnew - vold) > 2.0 * vt:
        if vold > 0.0:
            arg = 1.0 + (vnew - vold) / vt
            return vold + vt * math.log(arg) if arg > 0.0 else vcrit
        return vt * math.log(vnew / vt) if vnew > vt else vnew
    return vnew


def _resid(p0, p1, K, v0, v1, par):
    ig, ia, J = currents(v0, v1, par)
    f0 = p0 - K[0][0] * ig - K[0][1] * ia - v0
    f1 = p1 - K[1][0] * ig - K[1][1] * ia - v1
    return f0, f1, ig, ia, J


def _resid_only(p0, p1, K, v0, v1, par):
    ig, ia = currents_only(v0, v1, par)
    return p0 - K[0][0] * ig - K[0][1] * ia - v0, p1 - K[1][0] * ig - K[1][1] * ia - v1, ig, ia


def solve(p0, p1, K, v0, v1, par=DZ_12AX7_RSD1, max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F):
    """Return (Vgk, Vak, Ig, Ia, iterations, converged)."""
    f0, f1, ig, ia, J = _resid(p0, p1, K, v0, v1, par)
    fb, best = max(abs(f0), abs(f1)), (v0, v1, ig, ia)       # best iterate so far, returned on failure
    for it in range(1, max_it + 1):
        # Jacobian of F: -K J - I
        a = -(K[0][0] * J[0][0] + K[0][1] * J[1][0]) - 1.0
        b = -(K[0][0] * J[0][1] + K[0][1] * J[1][1])
        c = -(K[1][0] * J[0][0] + K[1][1] * J[1][0])
        d = -(K[1][0] * J[0][1] + K[1][1] * J[1][1]) - 1.0
        det = a * d - b * c
        d0 = -(d * f0 - b * f1) / det
        d1 = -(-c * f0 + a * f1) / det
        n0 = pnjlim(v0 + d0, v0)
        s0, s1 = n0 - v0, d1 * ((n0 - v0) / d0 if d0 != 0.0 else 1.0)   # keep the direction
        fn = max(abs(f0), abs(f1))
        lam = 1.0
        g0, g1, gig, gia, gJ = _resid(p0, p1, K, v0 + s0, v1 + s1, par)
        if not (max(abs(g0), abs(g1)) < fn or fn < tol_f):
            for _ in range(LS_MAX - 1):
                lam *= 0.5
                g0, g1, gig, gia = _resid_only(p0, p1, K, v0 + lam * s0, v1 + lam * s1, par)
                if max(abs(g0), abs(g1)) < fn:
                    break
            gJ = currents(v0 + lam * s0, v1 + lam * s1, par)[2]
        dv = max(abs(lam * s0), abs(lam * s1))
        v0, v1 = v0 + lam * s0, v1 + lam * s1
        f0, f1, ig, ia, J = g0, g1, gig, gia, gJ
        fa = max(abs(f0), abs(f1))
        if dv < tol_v and fa < tol_f:
            return v0, v1, ig, ia, it, True
        # NaN/inf never wins (sum, not max: max(1.0, nan) is 1.0); a non-finite start loses to any finite iterate
        if (fa < fb or not fb < math.inf) and abs(f0) + abs(f1) < math.inf:
            fb, best = fa, (v0, v1, ig, ia)
    return best + (max_it, False)
