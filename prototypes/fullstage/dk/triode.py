"""Dempwolf-Zoelzer triode model with grid current.

K. Dempwolf, U. Zoelzer, "A physically-motivated triode model for circuit simulations", DAFx-11,
https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf :
    Ik = G  * ( ln(1 + exp(C  * (Vak/mu + Vgk))) / C  ) ** gamma      (cathode current)
    Ig = Gg * ( ln(1 + exp(Cg * Vgk))            / Cg ) ** xi  + Ig0  (grid current)
    Ia = Ik - Ig                                                     (plate current)
Parameters: Table 1, column RSD-1 (one measured 12AX7), as in prototypes/valvelab/valvelab/params.py.

Numerics: softplus(x) = max(x,0) + log1p(exp(-|x|)) never overflows, is > 0 for all x (so the power
law is always defined) and is smooth, so Newton's Jacobian is continuous everywhere.
"""
import math

DZ_12AX7_RSD1 = dict(G=2.242e-3, mu=103.2, gamma=1.26, C=3.40,
                     Gg=6.177e-4, xi=1.314, Cg=9.901, Ig0=8.025e-8)


def softplus(x):
    return (x if x > 0.0 else 0.0) + math.log1p(math.exp(-abs(x)))


def sigmoid(x):
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def currents(vgk, vak, p=DZ_12AX7_RSD1):
    """Return (Ig, Ia) and the Jacobian d(Ig, Ia)/d(Vgk, Vak) as a 2x2 tuple-of-tuples."""
    G, mu, gam, C = p["G"], p["mu"], p["gamma"], p["C"]
    Gg, xi, Cg, Ig0 = p["Gg"], p["xi"], p["Cg"], p["Ig0"]
    zk = C * (vak / mu + vgk)
    sk = softplus(zk) / C
    ik = G * sk ** gam
    dik_dz = G * gam * sk ** (gam - 1.0) * sigmoid(zk)       # d Ik / d(vak/mu + vgk)
    zg = Cg * vgk
    sg = softplus(zg) / Cg
    ig = Gg * sg ** xi + Ig0
    dig = Gg * xi * sg ** (xi - 1.0) * sigmoid(zg)
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
