"""Static valve models (vectorised NumPy). Volts in, amps out.

All voltages are relative to the cathode.
"""
import numpy as np

from . import params as P


def _softplus(x):
    # log(1+exp(x)) without overflow
    return np.logaddexp(0.0, x)


class KorenTriode:
    r"""Koren (1996) eq. (4):
    E1 = (Vp/KP) ln(1 + exp(KP (1/MU + Vg / sqrt(KVB + Vp^2))))
    Ip = (E1^EX / KG1) (1 + sgn(E1))
    Grid current: Koren's SPICE subcircuit puts RGI in series with diode DX
    (IS=1n, RS=1) from grid to cathode; we solve that series pair exactly.
    """
    kind = "triode"

    def __init__(self, name):
        p = P.KOREN_TRIODE[name]
        self.name = name
        self.MU, self.EX, self.KG1, self.KP, self.KVB = (p[k] for k in ("MU", "EX", "KG1", "KP", "KVB"))
        self.source = p["source"]

    def ip(self, vg, vp):
        vg = np.asarray(vg, float); vp = np.asarray(vp, float)
        vpc = np.maximum(vp, 0.0)
        e1 = vpc / self.KP * _softplus(self.KP * (1.0 / self.MU + vg / np.sqrt(self.KVB + vpc * vpc)))
        # E1 >= 0 always for Vp >= 0, so (1+sgn) = 2
        return 2.0 * np.power(e1, self.EX) / self.KG1

    def ik(self, vg, vp):
        return self.ip(vg, vp) + self.ig(vg)

    @staticmethod
    def ig(vg):
        """Grid current of RGI + diode(IS, RS) in series, solved by Newton."""
        g = P.KOREN_GRID
        R = g["RGI"] + g["RS"]; Is = g["IS"]; Vt = 0.025852 * g["N"]
        vg = np.asarray(vg, float)
        i = np.zeros_like(vg)
        pos = vg > 0
        if np.any(pos):
            v = vg[pos]
            # f(i) = Is(exp((v - R i)/Vt) - 1) - i = 0 ; monotone decreasing in i
            ii = np.maximum(v - 0.5, 0.0) / R
            for _ in range(60):
                e = np.exp(np.minimum((v - R * ii) / Vt, 80.0))
                f = Is * (e - 1.0) - ii
                df = -Is * e * R / Vt - 1.0
                ii = np.clip(ii - f / df, 0.0, v / R)
            i[pos] = ii
        return i


class KorenPentode:
    r"""Koren (1996) eq. (5) and (3) (with the errata's /MU fix):
    E1  = (Vg2/KP) ln(1 + exp(KP (1/MU + Vg1/Vg2)))
    Ip  = (E1^EX / KG1) (1 + sgn(E1)) atan(Vp / KVB)
    Ig2 = (Vg2/MU + Vg1)^EX / KG2
    """
    kind = "pentode"

    def __init__(self, name):
        p = P.KOREN_PENTODE[name]
        self.name = name
        self.MU, self.EX, self.KG1, self.KG2, self.KP, self.KVB = (
            p[k] for k in ("MU", "EX", "KG1", "KG2", "KP", "KVB"))
        self.source = p["source"]

    def ip(self, vg1, vg2, vp):
        vg1 = np.asarray(vg1, float); vp = np.asarray(vp, float)
        vg2 = np.maximum(vg2, 1e-3)   # screen must be positive for eq. (5)
        e1 = vg2 / self.KP * _softplus(self.KP * (1.0 / self.MU + vg1 / vg2))
        return 2.0 * np.power(e1, self.EX) / self.KG1 * np.arctan(np.maximum(vp, 0.0) / self.KVB)

    def ig2(self, vg1, vg2):
        x = np.maximum(vg2 / self.MU + np.asarray(vg1, float), 0.0)
        return np.power(x, self.EX) / self.KG2

    def ik(self, vg1, vg2, vp):
        return self.ip(vg1, vg2, vp) + self.ig2(vg1, vg2) + KorenTriode.ig(vg1)

    ig = staticmethod(KorenTriode.ig)


class DempwolfTriode:
    r"""Dempwolf & Zoelzer (DAFx-11) eqs. (10)-(12):
    Ik = G (ln(1+exp(C (Va/mu + Vg))) / C)^gamma
    Ig = Gg (ln(1+exp(Cg Vg)) / Cg)^xi + Ig0
    Ia = Ik - Ig
    """
    kind = "triode"

    def __init__(self, name="12AX7_RSD1"):
        p = P.DEMPWOLF_TRIODE[name]
        self.name = name
        for k in ("G", "mu", "gamma", "C", "Gg", "xi", "Cg", "Ig0"):
            setattr(self, k, p[k])
        self.source = p["source"]

    def ik(self, vg, vp):
        vg = np.asarray(vg, float); vp = np.asarray(vp, float)
        return self.G * np.power(_softplus(self.C * (vp / self.mu + vg)) / self.C, self.gamma)

    def ig(self, vg):
        vg = np.asarray(vg, float)
        return self.Gg * np.power(_softplus(self.Cg * vg) / self.Cg, self.xi) + self.Ig0

    def ip(self, vg, vp):
        return self.ik(vg, vp) - self.ig(vg)
