"""Dempwolf-Zoelzer triode model with grid current.

K. Dempwolf, U. Zoelzer, "A physically-motivated triode model for circuit simulations", DAFx-11,
https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf :
    Ik = G  * ( ln(1 + exp(C  * (Vak/mu + Vgk))) / C  ) ** gamma      (cathode current)
    Ig = Gg * ( ln(1 + exp(Cg * Vgk))            / Cg ) ** xi  + Ig0  (grid current)
    Ia = Ik - Ig                                                     (plate current)
Parameters: Table 1, column RSD-1 (one measured 12AX7), as in prototypes/valvelab/valvelab/params.py.

Numerics: softplus(x) = max(x,0) + log1p(exp(-|x|)) never overflows, is > 0 for all x (so the power
law is always defined) and is smooth, so Newton's Jacobian is continuous everywhere.

Cost (#16): currents() makes 6 libm calls -- per softplus/sigmoid pair one exp(-|z|) shared by both plus one
log1p, then one pow each for sk**gamma and sg**xi; the derivative powers are sk**(gamma-1) = sk**gamma / sk and
sg**(xi-1) = sg**xi / sg. Before #16 it made 10 (each sigmoid had its own exp; four pows). Counting as in the
issue (a softplus = 1 transcendental): 8 -> 4. softplus and sigmoid values are bit-identical to the separate
forms; only the two divided powers differ, by a few ulps (dk/tests/test_triode.py). currents_only() was already
2 x (exp + log1p) + 2 pow (no sigmoid, no derivative powers), so it is unchanged.
"""
import math

DZ_12AX7_RSD1 = dict(G=2.242e-3, mu=103.2, gamma=1.26, C=3.40,
                     Gg=6.177e-4, xi=1.314, Cg=9.901, Ig0=8.025e-8)


def softplus(x):
    return (x if x > 0.0 else 0.0) + math.log1p(math.exp(-abs(x)))


def softplus_sigmoid(x):
    """(softplus(x), sigmoid(x)) from one shared exp(-|x|); each equals its separate form bit for bit."""
    e = math.exp(-abs(x))
    if x >= 0.0:
        return x + math.log1p(e), 1.0 / (1.0 + e)
    return math.log1p(e), e / (1.0 + e)


def currents(vgk, vak, p=DZ_12AX7_RSD1):
    """Return (Ig, Ia) and the Jacobian d(Ig, Ia)/d(Vgk, Vak) as a 2x2 tuple-of-tuples."""
    G, mu, gam, C = p["G"], p["mu"], p["gamma"], p["C"]
    Gg, xi, Cg, Ig0 = p["Gg"], p["xi"], p["Cg"], p["Ig0"]
    zk = C * (vak / mu + vgk)
    spk, sigk = softplus_sigmoid(zk)
    sk = spk / C
    pk = sk ** gam
    ik = G * pk
    dik_dz = G * gam * (pk / sk if sk > 0.0 else 0.0) * sigk     # d Ik / d(vak/mu + vgk); sk**(gam-1) = pk/sk
    zg = Cg * vgk
    spg, sigg = softplus_sigmoid(zg)
    sg = spg / Cg
    pg = sg ** xi
    ig = Gg * pg + Ig0
    dig = Gg * xi * (pg / sg if sg > 0.0 else 0.0) * sigg        # sg**(xi-1) = pg/sg; sg = 0 only if exp underflows
    ia = ik - ig
    J = ((dig, 0.0),
         (dik_dz - dig, dik_dz / mu))
    return ig, ia, J


def currents_only(vgk, vak, p=DZ_12AX7_RSD1):
    """(Ig, Ia) without the Jacobian: same expressions as currents(), for line-search trials."""
    G, mu, gam, C = p["G"], p["mu"], p["gamma"], p["C"]
    Gg, xi, Cg, Ig0 = p["Gg"], p["xi"], p["Cg"], p["Ig0"]
    ik = G * (softplus(C * (vak / mu + vgk)) / C) ** gam
    ig = Gg * (softplus(Cg * vgk) / Cg) ** xi + Ig0
    return ig, ik - ig
