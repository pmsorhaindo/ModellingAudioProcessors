"""6AS6 dual-control pentode for the DK stage: the fit6as6 `dual_control` law (issue #55), with its Jacobian.

Equations: prototypes/fit6as6/model6as6.py (dual_ip_ig2; OUR empirical extension of Koren's pentode, not a
published model). Parameters: prototypes/fit6as6/results/fit_results.json, read here (not copied), so the DK stage,
the same-model ngspice subckt (spice6as6/run_refs.py) and the fit always use one parameter set:
  DUAL_CONTROL = fit_results["dual_control"]["params"]   issue #55, KR = 0.2737, XA = 0.5743 (the stage's default)
  REFIT_51     = fit_results["refit_51"]["params"]       issue #51, no KR / XA (KR = 0, XA = 1): comparison only
g1 grid current (#112): OPTIONAL and UNVALIDATED for the 6AS6 (no 6AS6 / 5725 sheet gives positive-g1 or grid-current
curves; prototypes/research/6as6-datasheets.md). grid() / GRID below: Dempwolf & Zoelzer's 12AX7 grid law (DAFx-11,
eq. (11), https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf), borrowed, not measured on a 6AS6:
  Ig1 = Gg (softplus(Cg Vg1k) / Cg)^XI,  taken out of the cathode current BEFORE the plate / screen split:
  Ip = (Ik - Ig1) f,  Ig2 = (Ik - Ig1) (1 - f),  so the cathode current stays Ik = Ip + Ig2 + Ig1 (the fit's Ik).
  XI, Cg: the 12AX7 RSD-1 values (Table 1; dk/triode.DZ_12AX7_RSD1), i.e. the 12AX7's shape of onset.
  Gg = hardness x PERVEANCE_RATIO x the RSD-1 Gg. PERVEANCE_RATIO = (2 / KG1) / G = 1.281: the 6AS6 fit's cathode
  coefficient (Ik0 = (2 / KG1) E1^EX, A/V^1.49) over the RSD-1 one (Ik = G E^1.26, A/V^1.26), compared at E = 1 V
  (where the different exponents drop out), on the ASSUMPTION that grid current scales with cathode emission at the
  12AX7's geometry. That is our guess, to be replaced by a measurement; "hardness" (0 = no grid current, 1 = this
  guess) is the control for it, not a fitted value. DZ's constant Ig0 (8.025e-8 A, "due to stability reasons") is
  left out: it is not a current law, and through the 1.07 MOhm grid circuit it would shift the grid DC by -86 mV.
  No g3 current (Ig3 = 0): no mode needs one (#111; dk6as6/RESULTS.md, "Modes").
  Bounds on the borrowed shape (#131, still no 6AS6 data): GRID_SHAPES names the three columns of DZ's Table 1, each a
  measured 12AX7. "RSD-1" (GRID_SHAPE) is the nominal law above, unchanged; "RSD-2" and "EHX-1" (GRID_BOUNDS) take XI,
  Cg, Gg and the cathode coefficient G from their own column, scaled the same way (Gg = hardness x (2 / KG1) / G x Gg),
  and are selectable only explicitly (grid(shape=...), Stage(grid_shape=...)): estimated, unvalidated bounds for a
  sensitivity study, not alternative defaults (dk6as6/RESULTS.md, "Grid-current bounds (#131)").
Without grid current (hardness 0, the stage's GRID_OFF) the cathode current is Ip + Ig2 and currents() is the whole law.

currents() is a scalar (math module) transcription of model6as6._f with the analytic Jacobian, written for the
per-sample Newton solve (numpy on scalars is ~10x slower). dk6as6/tests/test_pentode.py checks it against
model6as6.dual_ip_ig2 (relative 1e-12) and the Jacobian against central differences, for both parameter sets.
Port order: v = (Vg1k, Vg2k, Vg3k, Vak) (circuit.NLV), i = (Ip, Ig2) (circuit.NLI); J = d i / d v, 2x4.
currents_grid() is the law with g1 current: i = (Ip, Ig2, Ig1) (circuit.NLI + NLI_G1), J 3x4; reference_grid() its
numpy reference (model6as6._f for Ik and f, and DZ's eq. (11) in numpy).
"""
import json
import math
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
FIT6AS6 = os.path.normpath(os.path.join(HERE, "..", "..", "fit6as6"))
FIT_RESULTS = os.path.join(FIT6AS6, "results", "fit_results.json")
sys.path.insert(0, FIT6AS6)
import model6as6  # noqa: E402  (the fit's own equations: reference for the tests, VA0)
sys.path.insert(0, os.path.dirname(HERE))
from dk.triode import DZ_12AX7_RSD1, DZ_12AX7_RSD2, DZ_12AX7_EHX1  # noqa: E402  (Dempwolf & Zoelzer Table 1)

_FIT = json.load(open(FIT_RESULTS))
DUAL_CONTROL = _FIT["dual_control"]["params"]
REFIT_51 = _FIT["refit_51"]["params"]
VA0 = model6as6.VA0
VG2_FLOOR = 1e-3     # V, model6as6.koren_e1: Vg2 floored at 1 mV
VA_FLOOR = 1e-3      # V, model6as6.plate_term: Va floored at 1 mV in the power (XA != 1)
# g1 grid current (#112, module docstring): ratio of the fitted cathode coefficients, 6AS6 (2 / KG1) over 12AX7 RSD-1 G
PERVEANCE_RATIO = (2.0 / DUAL_CONTROL["KG1"]) / DZ_12AX7_RSD1["G"]
HARDNESS = 1.0       # the default grid hardness (our choice, #112: model the grid current rather than leave it out)
# The borrowed grid-current shape (#131): DZ Table 1's three measured 12AX7s (dk/triode.py), each estimated and
# unvalidated for the 6AS6. RSD-1 is the nominal (#112) law; RSD-2 and EHX-1 are the bounds, explicit opt-ins only.
GRID_SHAPES = {"RSD-1": DZ_12AX7_RSD1, "RSD-2": DZ_12AX7_RSD2, "EHX-1": DZ_12AX7_EHX1}
GRID_SHAPE = "RSD-1"
GRID_BOUNDS = ("RSD-2", "EHX-1")


def grid(hardness=HARDNESS, p=DUAL_CONTROL, shape=GRID_SHAPE):
    """The g1 grid-current parameters at `hardness` (0 = none) for the DZ Table 1 column `shape` (GRID_SHAPES): XI and
    CG from that column, GG = hardness x the cathode-coefficient ratio of the valve parameter set p to that column's G
    x its Gg. UNVALIDATED: no 6AS6 grid-current data. The nominal shape's dict is #112's exactly (no "shape" key, so
    its records are unchanged); a bound's carries "shape"."""
    if not hardness >= 0.0:
        raise ValueError(f"grid hardness {hardness} must be >= 0")
    if shape not in GRID_SHAPES:
        raise ValueError(f"unknown grid shape {shape!r}, expected one of {sorted(GRID_SHAPES)}")
    dz = GRID_SHAPES[shape]
    ratio = (2.0 / p["KG1"]) / dz["G"]
    return dict(GG=hardness * ratio * dz["Gg"], XI=dz["xi"], CG=dz["Cg"], hardness=hardness,
                **({} if shape == GRID_SHAPE else dict(shape=shape)))


GRID = grid()


def softplus(x):
    return (x if x > 0.0 else 0.0) + math.log1p(math.exp(-abs(x)))


def sigmoid(x):
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def cathode_share(vg1, vg2, vg3, va, p=DUAL_CONTROL):
    """(Ik, dIk, f, df): the fit's cathode current and plate share and their gradients d/d(Vg1k, Vg2k, Vg3k, Vak), each
    computed directly (never as Ip / Ik, which loses f where Ik underflows: #112 review)."""
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
    return ik, dik, f, dfs


def currents(vg1, vg2, vg3, va, p=DUAL_CONTROL):
    """Return (Ip, Ig2) and the Jacobian d(Ip, Ig2)/d(Vg1k, Vg2k, Vg3k, Vak) as a 2x4 tuple-of-tuples."""
    ik, dik, f, dfs = cathode_share(vg1, vg2, vg3, va, p)
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


def grid_current(vg1, g=GRID):
    """(Ig1, dIg1/dVg1k): Ig1 = GG (softplus(CG Vg1k) / CG)^XI (DZ eq. (11) without Ig0). (0, 0) where softplus
    underflows (CG Vg1k < -745), where the true value is < 1e-400 A."""
    zg = g["CG"] * vg1
    e = math.exp(-abs(zg))
    sp, sg = (zg + math.log1p(e), 1.0 / (1.0 + e)) if zg >= 0.0 else (math.log1p(e), e / (1.0 + e))
    s = sp / g["CG"]
    if s <= 0.0:
        return 0.0, 0.0
    pg = s ** g["XI"]
    return g["GG"] * pg, g["GG"] * g["XI"] * pg / s * sg


def currents_grid(vg1, vg2, vg3, va, p=DUAL_CONTROL, g=GRID):
    """Return (Ip, Ig2, Ig1) and the Jacobian d(Ip, Ig2, Ig1)/d(Vg1k, Vg2k, Vg3k, Vak) as a 3x4 tuple-of-tuples:
    the fit's cathode current Ik less Ig1, split by the fit's plate share f: Ip = (Ik - Ig1) f, Ig2 = (Ik - Ig1)(1 - f),
    with the product rule on each. f and its gradient come straight from the fit (cathode_share), so the law and its
    Jacobian stay finite and continuous into cut-off, where Ik underflows (#112 review). With GG = 0 Ip and Ig2 are
    currents()' to rounding."""
    ik, dik, f, dfs = cathode_share(vg1, vg2, vg3, va, p)
    ig1, dig1 = grid_current(vg1, g)
    ipk = ik - ig1                                                   # Ip + Ig2
    dipk = (dik[0] - dig1, dik[1], dik[2], dik[3])
    ip = ipk * f
    dip = tuple(dipk[j] * f + ipk * dfs[j] for j in range(4))
    return ip, ipk - ip, ig1, (dip, tuple(dipk[j] - dip[j] for j in range(4)), (dig1, 0.0, 0.0, 0.0))


def currents_grid_only(vg1, vg2, vg3, va, p=DUAL_CONTROL, g=GRID):
    """(Ip, Ig2, Ig1) without the Jacobian, for line-search trials."""
    return currents_grid(vg1, vg2, vg3, va, p, g)[:3]


def reference_grid(vg1, vg2, vg3, va, p=DUAL_CONTROL, g=GRID):
    """(Ip, Ig2, Ig1) in numpy from model6as6.dual_ip_ig2 (Ik = Ip + Ig2, f = Ip / Ik) and DZ eq. (11):
    what currents_grid() must reproduce."""
    import numpy as np
    ik, f = model6as6._f(vg1, vg2, vg3, va, p)         # the fit's cathode current and plate share, as dual_ip_ig2
    ig1 = g["GG"] * (np.logaddexp(0.0, g["CG"] * vg1) / g["CG"]) ** g["XI"]
    return float((ik - ig1) * f), float((ik - ig1) * (1.0 - f)), float(ig1)
