"""6AS6 dual-control pentode for the DK stage: the fit6as6 `dual_control` law (issue #55), with its Jacobian.

Equations: prototypes/fit6as6/model6as6.py (dual_ip_ig2; OUR empirical extension of Koren's pentode, not a
published model). Parameters: prototypes/fit6as6/results/fit_results.json, read here (not copied), so the DK stage,
the same-model ngspice subckt (spice6as6/run_refs.py) and the fit always use one parameter set:
  DUAL_CONTROL = fit_results["dual_control"]["params"]   issue #55, KR = 0.2737, XA = 0.5743 (the stage's default)
  REFIT_51     = fit_results["refit_51"]["params"]       issue #51, no KR / XA (KR = 0, XA = 1): comparison only
No g1 or g3 grid current (none in any sheet): Ig1 = Ig3 = 0. The cathode current is Ip + Ig2.

currents() is a scalar (math module) transcription of model6as6._f with the analytic Jacobian, written for the
per-sample Newton solve (numpy on scalars is ~10x slower). dk6as6/tests/test_pentode.py checks it against
model6as6.dual_ip_ig2 (relative 1e-12) and the Jacobian against central differences, for both parameter sets.
Port order: v = (Vg1k, Vg2k, Vg3k, Vak) (circuit.NLV), i = (Ip, Ig2) (circuit.NLI); J = d i / d v, 2x4.
"""
import json, math, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
FIT6AS6 = os.path.normpath(os.path.join(HERE, "..", "..", "fit6as6"))
FIT_RESULTS = os.path.join(FIT6AS6, "results", "fit_results.json")
sys.path.insert(0, FIT6AS6)
import model6as6  # noqa: E402  (the fit's own equations: reference for the tests, VA0)

_FIT = json.load(open(FIT_RESULTS))
DUAL_CONTROL = _FIT["dual_control"]["params"]
REFIT_51 = _FIT["refit_51"]["params"]
VA0 = model6as6.VA0
VG2_FLOOR = 1e-3     # V, model6as6.koren_e1: Vg2 floored at 1 mV
VA_FLOOR = 1e-3      # V, model6as6.plate_term: Va floored at 1 mV in the power (XA != 1)


def softplus(x):
    return (x if x > 0.0 else 0.0) + math.log1p(math.exp(-abs(x)))


def sigmoid(x):
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def currents(vg1, vg2, vg3, va, p=DUAL_CONTROL):
    """Return (Ip, Ig2) and the Jacobian d(Ip, Ig2)/d(Vg1k, Vg2k, Vg3k, Vak) as a 2x4 tuple-of-tuples."""
    MU, EX, KG1, KP = p["MU"], p["EX"], p["KG1"], p["KP"]
    FMAX, MU3, V3C, S3, P3 = p["FMAX"], p["MU3"], p["V3C"], p["S3"], p["P3"]
    RI, KVB, KR, XA = p.get("RI", 0.0), p.get("KVB", 1e-9), p.get("KR", 0.0), p.get("XA", 1.0)
    # Koren E1 and the cathode current with g3 conducting, Ik0 = 2 E1^EX / KG1
    floor = vg2 <= VG2_FLOOR
    g2 = VG2_FLOOR if floor else vg2
    a = KP * (1.0 / MU + vg1 / g2)
    e1 = g2 / KP * softplus(a)
    sa = sigmoid(a)
    de1_g1 = sa
    de1_g2 = 0.0 if floor else softplus(a) / KP - sa * vg1 / g2
    ik0 = 2.0 * e1 ** EX / KG1
    dik0 = 2.0 * EX * e1 ** (EX - 1.0) / KG1
    dik0_g1, dik0_g2 = dik0 * de1_g1, dik0 * de1_g2
    # plate term on the g3 cut-off, Up(Va)
    if XA == 1.0:
        up, dup = (va / MU3, 1.0 / MU3) if va > 0.0 else (0.0, 0.0)
    else:
        x = (va if va > VA_FLOOR else VA_FLOOR) / VA0
        xa = x ** XA
        up = VA0 / MU3 * (1.0 + (xa - 1.0) / XA)
        dup = xa / x / MU3 if va > VA_FLOOR else 0.0
    z = (vg3 + up - V3C - RI * ik0) / S3
    dz_g1, dz_g2, dz_g3, dz_a = -RI * dik0_g1 / S3, -RI * dik0_g2 / S3, 1.0 / S3, dup / S3
    # g3 acceptance s = (1 + e^-z)^-P3 and the KR cathode-current term
    s = math.exp(-P3 * softplus(-z))
    sm = sigmoid(-z)                       # 1/(1 + e^z)
    ds = s * P3 * sm                       # ds/dz
    knee = 2.0 / math.pi * math.atan(va / KVB) if va > 0.0 else 0.0
    dknee = 2.0 / math.pi / KVB / (1.0 + (va / KVB) ** 2) if va > 0.0 else 0.0
    f = FMAX * knee * s
    df_z, df_a = FMAX * knee * ds, FMAX * dknee * s
    kf = 1.0 - KR * sm
    ik = ik0 * kf
    dik_z = ik0 * KR * sm * (1.0 - sm)     # d/dz of -KR/(1 + e^z) is +KR sm (1 - sm)
    dik = (dik0_g1 * kf + dik_z * dz_g1, dik0_g2 * kf + dik_z * dz_g2, dik_z * dz_g3, dik_z * dz_a)
    dzs = (dz_g1, dz_g2, dz_g3, dz_a)
    dfs = tuple(df_z * d for d in dzs[:3]) + (df_z * dz_a + df_a,)
    ip = ik * f
    dip = tuple(dik[j] * f + ik * dfs[j] for j in range(4))
    return ip, ik - ip, (dip, tuple(dik[j] - dip[j] for j in range(4)))


def currents_only(vg1, vg2, vg3, va, p=DUAL_CONTROL):
    """(Ip, Ig2) without the Jacobian, for line-search trials."""
    ip, ig2, _ = currents(vg1, vg2, vg3, va, p)
    return ip, ig2


def reference(vg1, vg2, vg3, va, p=DUAL_CONTROL):
    """(Ip, Ig2) from fit6as6's own model6as6.dual_ip_ig2 (numpy), what currents() must reproduce."""
    ip, ig2 = model6as6.dual_ip_ig2(vg1, vg2, vg3, va, p)
    return float(ip), float(ig2)
