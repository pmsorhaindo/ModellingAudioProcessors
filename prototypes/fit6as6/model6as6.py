"""6AS6 models fitted to the GE datasheet curves (see fit.py, README.md).

Voltages in volts w.r.t. cathode, currents in AMPS.

koren_ip     : Koren (1996) pentode plate current, eq. (5); g3 assumed at cathode.
koren_ig2    : Koren screen current, eq. (3) form (independent of Va).
dual_control : OUR empirical extension (not from a publication):
    Ik0 = 2*E1^EX/KG1,  E1 = Vg2/KP*ln(1+exp(KP*(1/MU + Vg1/Vg2)))   (Koren E1, cathode current with g3 conducting)
    z   = (Vg3 + Up(Va) - V3C - RI*Ik0)/S3                          (how far g3 + plate are above the g3 cut-off;
          RI*Ik0: share falls as cathode current rises, an empirical space-charge term)
    Up  = (VA0/MU3)*(1 + ((Va/VA0)^XA - 1)/XA),  VA0 = 120 V        (plate action on the g3 cut-off; issue #53.
          XA = 1 gives Va/MU3. MU3 is the local plate-to-g3 mu at Va = VA0; Va floored at 1 mV in the power)
    s   = (1 + exp(-z))^(-P3)                                       (g3 acceptance, 0..1)
    Ik  = Ik0 * (1 - KR/(1 + exp(z)))                               (issue #53: cathode current falls by up to KR
          once g3 cuts the plate off; Raytheon p.11, Ib + Ic2 is 13-25 % lower at Ec3 = -10 V than at 0)
    f   = FMAX * (2/pi)atan(Va/KVB) * s                             (plate share, 0..FMAX)
    Ip  = Ik*f,   Ig2 = Ik*(1-f)
  i.e. cathode current set by g1/g2, split between plate and screen by g3 and the plate (current-division
  picture). KR = 0 and XA = 1 (the defaults, and every parameter set up to #51) is the earlier model, with
  cathode current independent of g3 and a linear plate term.
"""
import numpy as np

VA0 = 120.0   # V, reference plate voltage of the plate term (Eb of the Raytheon/GE Ec3 transfer curves)


def _sp(x):
    return np.logaddexp(0.0, x)


def koren_e1(vg1, vg2, MU, EX, KP):
    vg2 = np.maximum(vg2, 1e-3)
    return vg2 / KP * _sp(KP * (1.0 / MU + vg1 / vg2))


def koren_ip(vg1, vg2, va, MU, EX, KG1, KP, KVB):
    return 2.0 * koren_e1(vg1, vg2, MU, EX, KP) ** EX / KG1 * np.arctan(np.maximum(va, 0) / KVB)


def koren_ig2(vg1, vg2, MU, EX, KG2):
    return np.maximum(vg2 / MU + vg1, 0.0) ** EX / KG2


def plate_term(va, MU3, XA=1.0):
    if XA == 1.0:
        return np.maximum(va, 0) / MU3
    x = np.maximum(va, 1e-3) / VA0                       # floor: finite slope at Va -> 0 (SPICE Jacobian)
    return VA0 / MU3 * (1.0 + (x ** XA - 1.0) / XA)


def dual_z(vg3, va, MU3, V3C, S3, RI=0.0, ik=0.0, XA=1.0):
    return (vg3 + plate_term(va, MU3, XA) - V3C - RI * ik) / S3


def dual_share(vg3, va, FMAX, MU3, V3C, S3, P3, RI=0.0, ik=0.0, KVB=1e-9, XA=1.0):
    z = dual_z(vg3, va, MU3, V3C, S3, RI, ik, XA)
    knee = (2.0 / np.pi) * np.arctan(np.maximum(va, 0) / KVB)   # Koren/Reynolds-style knee
    return FMAX * knee * np.exp(-P3 * _sp(-z))   # = FMAX*knee*(1+e^-z)^-P3, overflow-safe


def dual_ik(vg1, vg2, MU, EX, KG1, KP):
    """Koren cathode current Ik0 (g3 conducting)."""
    return 2.0 * koren_e1(vg1, vg2, MU, EX, KP) ** EX / KG1


def _f(vg1, vg2, vg3, va, p):
    ik0 = dual_ik(vg1, vg2, p["MU"], p["EX"], p["KG1"], p["KP"])
    xa, kr = p.get("XA", 1.0), p.get("KR", 0.0)
    f = dual_share(vg3, va, p["FMAX"], p["MU3"], p["V3C"], p["S3"], p["P3"], p.get("RI", 0.0), ik0, p.get("KVB", 1e-9), xa)
    if kr == 0.0:
        return ik0, f
    z = dual_z(vg3, va, p["MU3"], p["V3C"], p["S3"], p.get("RI", 0.0), ik0, xa)
    return ik0 * (1.0 - kr * np.exp(-_sp(z))), f   # exp(-sp(z)) = 1/(1+e^z), overflow-safe


def dual_cathode(vg1, vg2, vg3, va, p):
    """Cathode current Ik = Ip + Ig2 (depends on g3 when KR > 0)."""
    return _f(vg1, vg2, vg3, va, p)[0]


def dual_ip(vg1, vg2, vg3, va, p):
    ik, f = _f(vg1, vg2, vg3, va, p); return ik * f


def dual_ig2(vg1, vg2, vg3, va, p):
    ik, f = _f(vg1, vg2, vg3, va, p); return ik * (1.0 - f)
