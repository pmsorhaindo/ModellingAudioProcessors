"""Per-sample Newton-Raphson for the 2-port nonlinear equation of the DK model, and the DC solve.

Solve  F(v) = p - K i(v) - v = 0,  p = G x + H u,  v = (Vgk, Vak)   (see circuit.py)
  * warm start: previous sample's solution;
  * step limiting on Vgk (the exponential grid-current port), SPICE pnjlim style (Nagel 1975):
    above VCRIT a step larger than 2*VT is compressed logarithmically, VT = 1/Cg;
  * backtracking line search: halve the step (up to LS_MAX times) until ||F||inf decreases; the full step
    is evaluated with its Jacobian (it is accepted almost always), halved trials evaluate currents only and
    the Jacobian is computed once at the accepted point (audit finding 8);
  * converged when ||dv||inf < TOL_V and ||F||inf < TOL_F; hard cap MAX_IT iterations.
No lookup tables. Iteration counts are returned so callers can histogram them.
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


def solve(p0, p1, K, v0, v1, par=DZ_12AX7_RSD1):
    """Return (Vgk, Vak, Ig, Ia, iterations, converged)."""
    f0, f1, ig, ia, J = _resid(p0, p1, K, v0, v1, par)
    for it in range(1, MAX_IT + 1):
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
        if not (max(abs(g0), abs(g1)) < fn or fn < TOL_F):
            for _ in range(LS_MAX - 1):
                lam *= 0.5
                g0, g1, gig, gia = _resid_only(p0, p1, K, v0 + lam * s0, v1 + lam * s1, par)
                if max(abs(g0), abs(g1)) < fn:
                    break
            gJ = currents(v0 + lam * s0, v1 + lam * s1, par)[2]
        dv = max(abs(lam * s0), abs(lam * s1))
        v0, v1 = v0 + lam * s0, v1 + lam * s1
        f0, f1, ig, ia, J = g0, g1, gig, gia, gJ
        if dv < TOL_V and max(abs(f0), abs(f1)) < TOL_F:
            return v0, v1, ig, ia, it, True
    return v0, v1, ig, ia, MAX_IT, False
