"""6AS6 models fitted to the GE datasheet curves (see fit.py, README.md).

Voltages in volts w.r.t. cathode, currents in AMPS.

koren_ip     : Koren (1996) pentode plate current, eq. (5); g3 assumed at cathode.
koren_ig2    : Koren screen current, eq. (3) form (independent of Va).
dual_control : OUR empirical extension (not from a publication):
    Ik  = 2*E1^EX/KG1,  E1 = Vg2/KP*ln(1+exp(KP*(1/MU + Vg1/Vg2)))   (Koren E1, total cathode current)
    f   = FMAX * (2/pi)atan(Va/KVB) * (1 + exp(-(Vg3 + Va/MU3 - V3C - RI*Ik)/S3))^(-P3)    (plate share, 0..FMAX;
          RI*Ik: share falls as cathode current rises, an empirical space-charge term)
    Ip  = Ik*f,   Ig2 = Ik*(1-f)
  i.e. cathode current set by g1/g2, split between plate and screen by g3 and the plate
  (current-division picture; cathode current ~constant under g3 control).
"""
import numpy as np


def _sp(x):
    return np.logaddexp(0.0, x)


def koren_e1(vg1, vg2, MU, EX, KP):
    vg2 = np.maximum(vg2, 1e-3)
    return vg2 / KP * _sp(KP * (1.0 / MU + vg1 / vg2))


def koren_ip(vg1, vg2, va, MU, EX, KG1, KP, KVB):
    return 2.0 * koren_e1(vg1, vg2, MU, EX, KP) ** EX / KG1 * np.arctan(np.maximum(va, 0) / KVB)


def koren_ig2(vg1, vg2, MU, EX, KG2):
    return np.maximum(vg2 / MU + vg1, 0.0) ** EX / KG2


def dual_share(vg3, va, FMAX, MU3, V3C, S3, P3, RI=0.0, ik=0.0, KVB=1e-9):
    z = (vg3 + np.maximum(va, 0) / MU3 - V3C - RI * ik) / S3
    knee = (2.0 / np.pi) * np.arctan(np.maximum(va, 0) / KVB)   # Koren/Reynolds-style knee
    return FMAX * knee * np.exp(-P3 * _sp(-z))   # = FMAX*knee*(1+e^-z)^-P3, overflow-safe


def dual_ik(vg1, vg2, MU, EX, KG1, KP):
    return 2.0 * koren_e1(vg1, vg2, MU, EX, KP) ** EX / KG1


def _f(vg1, vg2, vg3, va, p):
    ik = dual_ik(vg1, vg2, p["MU"], p["EX"], p["KG1"], p["KP"])
    return ik, dual_share(vg3, va, p["FMAX"], p["MU3"], p["V3C"], p["S3"], p["P3"], p.get("RI", 0.0), ik, p.get("KVB", 1e-9))


def dual_ip(vg1, vg2, vg3, va, p):
    ik, f = _f(vg1, vg2, vg3, va, p); return ik * f


def dual_ig2(vg1, vg2, vg3, va, p):
    ik, f = _f(vg1, vg2, vg3, va, p); return ik * (1.0 - f)
