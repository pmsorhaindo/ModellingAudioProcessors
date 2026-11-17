"""Fit the 6AS6 models to digitised datasheet curves (see README.md).

BEFORE (kept for comparison, issue #8 fit): dual-control model fitted to the GE sheet (Ec2 = 120 V
only) with a relative residual (model - data) / max(data, 0.3 mA), which gives the 0.1-0.6 mA
region almost no weight.
Issue #25 refit (`refit_25`): the same dual-control model fitted to the Raytheon 1960 families
(Ec2 = 60..140 V, Ec1 down to -5 V, Ec3 -12..+12 V) with a log residual
ln((I_model + I0) / (I_data + I0)), I0 = 0.02 mA (about 1-2 px of digitising resolution), so a
10 % miss at 0.2 mA costs as much as a 10 % miss at 10 mA.
Issue #51 refit (`refit_51`): the #25 data plus the Raytheon p.8 bottom plate family (Ib vs Eb at Ec3 = -1..-14 V,
digitised in #48), the only curves that vary the plate voltage at fixed negative Ec3 and so pin MU3. Same model,
residual and bounds; each p.8 bottom point has weight W_P8 = 1, like every other Raytheon point. A refit with
weight 0.25 on p.8 bottom is kept as a sensitivity check. #8/#25/#51 are fitted on the #25-#53 data split, which
held out the Raytheon screen-current-vs-Ec3 curves (p.10 bottom, p.11 top) and the p.8 bottom Ic2 curves.
Issue #55 refit (current, `dual_control`; option A of #53): the issue #53 model (KR: g3-dependent cathode current,
XA: power-law plate term; model6as6.py) with KR and XA free, on a new data split: the p.11 top Ic2-vs-Ec3 curves
join the fit (they are what pins KR), and the held-out screen check is p.10 bottom Ic2 vs Ec3 plus the p.8 bottom
Ic2 curves. Same residual, I0 and W_P8; seeded and random restarts (restart counts in fit_results.json).
"""
import csv
import json
import os
import numpy as np
from scipy.optimize import least_squares
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
FLOOR = 0.3e-3          # BEFORE residual denominator floor
I0 = 0.02e-3            # AFTER log-residual offset
LOW = (0.1e-3, 0.6e-3)  # the Culture Vulture's metered operating range (research/culture-vulture-controls.md)
RAY_URL = "https://frank.pocnet.net/sheets/138/5/5725.pdf"
GE_URL = "https://frank.pocnet.net/sheets/084/6/6AS6.pdf"


def load(fn):
    with open(os.path.join(HERE, "data", fn)) as f:
        rows = list(csv.DictReader(ln for ln in f if not ln.startswith("#")))
    for r in rows:
        for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", "I_mA"):
            r[k] = float(r[k])
    return rows


def arr(rows):
    def g(k):
        return np.array([r[k] for r in rows])
    return g("Ec1_V"), g("Ec2_V"), g("Ec3_V"), g("Eb_V"), g("I_mA") * 1e-3


p3 = load("plate_characteristics_p3.csv")
p4 = load("suppressor_characteristics_p4.csv")
IB3 = [r for r in p3 if r["quantity"] == "Ib"]
IC2 = [r for r in p3 if r["quantity"] == "Ic2"]
a_ib3, a_ic2, a_ib4 = arr(IB3), arr(IC2), arr(p4)
def w(i):
    return 1.0 / np.maximum(i, FLOOR)

RAY = load("raytheon_5725_p8-11.csv")
GE = p3 + [dict(r, quantity="Ib") for r in p4]
P8B = load("raytheon_5725_p8bottom.csv")
# issue #51: p.8 bottom Ib curves join the fit (Ic3 is not modelled)
R_P8 = [r for r in P8B if r["quantity"] == "Ib"]
R_P8_DEEP = [r for r in R_P8 if r["Ec3_V"] <= -10]
R_P8_IC2 = [r for r in P8B if r["quantity"] == "Ic2"]
# #25-#53 split: every screen-current-under-g3 curve held out (p.10 bottom, p.11 top Ic2 vs Ec3; p.8 bottom Ic2)
SPLIT_GRAPHS_51 = ("p10_vs_Ec3_Ec2", "p11_vs_Ec3_Ec1")
R_SPLIT_51 = [r for r in RAY if r["graph"] in SPLIT_GRAPHS_51 and r["quantity"] == "Ic2"]
R_FIT_51 = [r for r in RAY if r not in R_SPLIT_51]
# issue #55 split (option A): p.11 top Ic2 vs Ec3 joins the fit; held out: p.10 bottom Ic2 vs Ec3 and p.8 bottom Ic2
R_P10_IC2 = [r for r in R_SPLIT_51 if r["graph"] == "p10_vs_Ec3_Ec2"]
R_P11_IC2 = [r for r in R_SPLIT_51 if r["graph"] == "p11_vs_Ec3_Ec1"]
SPLIT_GRAPHS = ("p10_vs_Ec3_Ec2", "p8_plate_vs_Eb_Ec3")
R_SPLIT = R_P10_IC2 + R_P8_IC2                                                         # held out
R_FIT = [r for r in RAY if r not in R_P10_IC2]                                         # = R_FIT_51 + R_P11_IC2
W_P8 = 1.0              # per-point weight of p.8 bottom in the #51 and #55 fits (#51 sensitivity check: 0.25)


def predict(rows, p):
    """Model current (A) for each digitised point: Ib rows -> plate, Ic2 rows -> screen."""
    g1, g2, g3, va, _ = arr(rows)
    ib = np.array([r["quantity"] == "Ib" for r in rows])
    return np.where(ib, *M.dual_ip_ig2(g1, g2, g3, va, p))


# ---- 1) plain Koren on page-3 plate data (g3 = 0) + Koren screen eq on page-3 screen data (baseline)
KN = ["MU", "EX", "KG1", "KP", "KVB"]
k_lo = [3, 1.0, 50, 5, 0.5]
k_hi = [200, 2.0, 1e5, 2000, 500]
k_x0 = [30, 1.35, 1500, 200, 15]
def rk(x):
    g1, g2, g3, va, i = a_ib3
    return (M.koren_ip(g1, g2, va, *x) - i) * w(i)
sol_k = least_squares(rk, k_x0, bounds=(k_lo, k_hi), x_scale="jac", max_nfev=20000)
koren = dict(zip(KN, sol_k.x))
def rg2(x):
    g1, g2, g3, va, i = a_ic2
    return (M.koren_ig2(g1, g2, koren["MU"], x[0], x[1]) - i) * w(i)
sol_g2 = least_squares(rg2, [1.35, 4000], bounds=([1.0, 10], [2.0, 1e6]), x_scale="jac")
koren.update(EX_G2=sol_g2.x[0], KG2=sol_g2.x[1])

# ---- 2) dual-control model
DN = ["MU", "EX", "KG1", "KP", "FMAX", "MU3", "V3C", "S3", "P3", "RI", "KVB"]
d_lo = [3, 1.0, 50, 5, 0.3, 1, -30, 0.2, 0.05, 0, 0.5]
d_hi = [200, 2.0, 1e5, 2000, 1.0, 1000, 30, 20, 50, 2000, 60]
STARTS = [[30, 1.35, 1000, 100, 0.8, 30, -5, 2, 1, 100, 5], [20, 1.4, 500, 50, 0.9, 10, -3, 1.5, 2, 300, 15],
          [50, 1.3, 2000, 300, 0.7, 100, -8, 3, 0.5, 0, 10], [24, 1.8, 1600, 160, 0.72, 56, -12, 2.7, 30, 50, 3]]
# issue #55: KR and XA (model6as6.py) free, with the #53 bounds; appended to each seeded start as KR = 0.15, XA = 0.7
DN55 = DN + ["KR", "XA"]
d_lo55, d_hi55, X55 = d_lo + [0.0, 0.2], d_hi + [0.95, 1.5], [0.15, 0.7]
N_RANDOM = 12           # random restarts of the #55 fit (seed 55)
SAME = 1e-6             # relative cost difference counted as "the same minimum" (as ik_g3.py)
def P(x, names=DN):
    return dict(zip(names, x))


def fit_dual(resid, starts, names=DN, lo=d_lo, hi=d_hi, full=False):
    sols = [least_squares(resid, np.clip(s, lo, hi), bounds=(lo, hi), x_scale="jac", max_nfev=40000) for s in starts]
    k = int(np.argmin([s.cost for s in sols]))
    best = P(sols[k].x, names)
    return (best, sols, k) if full else best


def random_starts(lo, hi, n, seed):
    """log-uniform in bounds; uniform where the lower bound is <= 0"""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        out.append([rng.uniform(a, b) if a <= 0 else float(np.exp(rng.uniform(np.log(a), np.log(b))))
                    for a, b in zip(lo, hi)])
    return out


def r_before(x):      # issue #8: GE p.3 + p.4, relative residual with a 0.3 mA floor
    p = P(x)
    out = []
    for (g1, g2, g3, va, i), f in ((a_ib3, M.dual_ip), (a_ic2, M.dual_ig2), (a_ib4, M.dual_ip)):
        out.append((f(g1, g2, g3, va, p) - i) * w(i))
    return np.concatenate(out)


I_FIT, I_FIT_51, I_P8 = arr(R_FIT)[4], arr(R_FIT_51)[4], arr(R_P8)[4]
def r_log(rows, i, x, names=DN):
    return np.log((np.maximum(predict(rows, P(x, names)), 0) + I0) / (i + I0))
def r_ray_rel(x):
    return (predict(R_FIT_51, P(x)) - I_FIT_51) * w(I_FIT_51)          # ablation
def r_ray_log(x):
    return r_log(R_FIT_51, I_FIT_51, x)                                 # issue #25
def r_p8_log(x, wt=W_P8):
    return np.concatenate([r_ray_log(x), np.sqrt(wt) * r_log(R_P8, I_P8, x)])   # issue #51
def r_55(x):
    return np.concatenate([r_log(R_FIT, I_FIT, x, DN55), np.sqrt(W_P8) * r_log(R_P8, I_P8, x, DN55)])  # #55

before = fit_dual(r_before, STARTS)
ray_rel = fit_dual(r_ray_rel, STARTS + [list(before.values())])
refit_25 = fit_dual(r_ray_log, STARTS + [list(before.values()), list(ray_rel.values())])
SEEDS = STARTS + [list(before.values()), list(ray_rel.values()), list(refit_25.values())]
refit_51 = fit_dual(r_p8_log, SEEDS)
p8_w025 = fit_dual(lambda x: r_p8_log(x, 0.25), SEEDS)
SEEDS55 = [s + X55 for s in SEEDS + [list(refit_51.values())]]
STARTS55 = SEEDS55 + random_starts(d_lo55, d_hi55, N_RANDOM, 55)
dual, sols55, k55 = fit_dual(r_55, STARTS55, DN55, d_lo55, d_hi55, full=True)
def _same(ss):
    return int(sum(abs(s.cost - sols55[k55].cost) <= SAME * sols55[k55].cost for s in ss))
restarts55 = dict(seeded=len(SEEDS55), seeded_same_minimum=_same(sols55[:len(SEEDS55)]), random=N_RANDOM,
                  random_same_minimum=_same(sols55[len(SEEDS55):]), random_seed=55, cost=float(sols55[k55].cost),
                  best_start=list(map(float, np.clip(STARTS55[k55], d_lo55, d_hi55))),
                  lowest_cost_by_start=[float(s.cost) for s in sols55],
                  params_at_bound=[n for n, v, a, b in zip(DN55, sols55[k55].x, d_lo55, d_hi55)
                                   if np.isclose(v, a, rtol=1e-6) or np.isclose(v, b, rtol=1e-6)])


# ---- metrics
def errs(rows, p):
    i = arr(rows)[4]
    m = predict(rows, p)
    return dict(i=i, m=m, rel=(m - i) / i, log=np.log((np.maximum(m, 0) + I0) / (i + I0)))


def summary(rows, p):
    """rms_log: RMS of the fit's log residual; rms_rel: RMS of (model - data)/data; mA errors absolute."""
    if not rows:
        return dict(n=0)
    e = errs(rows, p)
    return dict(n=len(rows), rms_log=float(np.sqrt(np.mean(e["log"] ** 2))),
                rms_rel=float(np.sqrt(np.mean(e["rel"] ** 2))), median_abs_rel=float(np.median(np.abs(e["rel"]))),
                max_abs_rel=float(np.max(np.abs(e["rel"]))), mean_rel=float(np.mean(e["rel"])),
                rms_mA=float(np.sqrt(np.mean((e["m"] - e["i"]) ** 2)) * 1e3),
                max_abs_mA=float(np.max(np.abs(e["m"] - e["i"])) * 1e3))


def low(rows):
    return [r for r in rows if LOW[0] <= r["I_mA"] * 1e-3 <= LOW[1]]


def per_curve(rows, p):
    out = {}
    for g in dict.fromkeys(r.get("graph", f"GE_p{r['page']}") for r in rows):
        rg = [r for r in rows if r.get("graph", f"GE_p{r['page']}") == g]
        out[g] = {c: dict(summary(rr, p), low_current=summary(low(rr), p))
                  for c in dict.fromkeys(r["curve"] for r in rg)
                  for rr in [[r for r in rg if r["curve"] == c]]}
    return out


def evaluate(p, fit_rows=R_FIT, split_rows=R_SPLIT):
    """Current errors on a data split (default the #55 split; ik_g3.py passes the #25-#53 split)."""
    return dict(params={k: float(v) for k, v in p.items()},
                raytheon_fit_set=dict(all=summary(fit_rows, p), low_current=summary(low(fit_rows), p)),
                split_check_heldout=dict(all=summary(split_rows, p), low_current=summary(low(split_rows), p)),
                ge_crosscheck=dict(all=summary(GE, p), low_current=summary(low(GE), p)),
                p8_bottom_Ib=dict(all=summary(R_P8, p),
                                  Ec3_m1_to_m9=summary([r for r in R_P8 if r not in R_P8_DEEP], p),
                                  Ec3_m10_to_m14=summary(R_P8_DEEP, p), low_current=summary(low(R_P8), p)))


# Internal consistency of the Raytheon sheet: the same operating point read off different graphs.
def _at(graph, curve, sweep, x):
    rr = sorted((r for r in RAY if r["graph"] == graph and r["curve"] == curve), key=lambda r: r[sweep])
    xs = np.array([r[sweep] for r in rr])
    ys = np.array([r["I_mA"] for r in rr])
    return float(np.interp(x, xs, ys)) if len(xs) and xs.min() <= x <= xs.max() else None


CONSIST = []
for q, ec1, reads in (
        ("Ib", -2, [("p9_plate_vs_Eb_Ec1", "Ib_Ec1_-2", "Eb_V", 120), ("p9_Ib_vs_Ec1_Ec2", "Ib_Ec2_120", "Ec1_V", -2),
                    ("p10_vs_Ec3_Ec2", "Ib_Ec2_120", "Ec3_V", 0), ("p11_vs_Ec3_Ec1", "Ib_Ec1_-2", "Ec3_V", 0)]),
        ("Ib", -4, [("p9_plate_vs_Eb_Ec1", "Ib_Ec1_-4", "Eb_V", 120), ("p9_Ib_vs_Ec1_Ec2", "Ib_Ec2_120", "Ec1_V", -4),
                    ("p11_vs_Ec3_Ec1", "Ib_Ec1_-4", "Ec3_V", 0)]),
        ("Ic2", -2,
         [("p9_plate_vs_Eb_Ec1", "Ic2_Ec1_-2", "Eb_V", 120), ("p10_Ic2_vs_Ec1_Ec2", "Ic2_Ec2_120", "Ec1_V", -2),
                     ("p10_vs_Ec3_Ec2", "Ic2_Ec2_120", "Ec3_V", 0), ("p11_vs_Ec3_Ec1", "Ic2_Ec1_-2", "Ec3_V", 0)]),
        ("Ic2", -4,
         [("p9_plate_vs_Eb_Ec1", "Ic2_Ec1_-4", "Eb_V", 120), ("p10_Ic2_vs_Ec1_Ec2", "Ic2_Ec2_120", "Ec1_V", -4),
                     ("p11_vs_Ec3_Ec1", "Ic2_Ec1_-4", "Ec3_V", 0)])):
    v = {g: _at(g, c, s, x) for g, c, s, x in reads}
    vals = [x for x in v.values() if x is not None]
    CONSIST.append(dict(point=f"{q} at Eb = Ec2 = 120 V, Ec1 = {ec1} V, Ec3 = 0", mA=v,
                        spread=(max(vals) - min(vals)) / np.mean(vals)))
# Raytheon p.11: cathode current Ib + Ic2 vs Ec3 (constant in the model up to #51; KR > 0 since #55)
IK3 = {}
for ec1 in (0, -1, -2, -3):
    s = {e3: _at("p11_vs_Ec3_Ec1", f"Ib_Ec1_{ec1}", "Ec3_V", e3) for e3 in (-4, 0, 4, 10)}
    IK3[f"Ec1_{ec1}"] = {f"Ec3_{e3}": (s[e3] + _at("p11_vs_Ec3_Ec1", f"Ic2_Ec1_{ec1}", "Ec3_V", e3)) if s[e3] else None
                         for e3 in s}
    IK3[f"Ec1_{ec1}"]["Ec3_-10_Ic2_only"] = _at("p11_vs_Ec3_Ec1", f"Ic2_Ec1_{ec1}", "Ec3_V", -10)



def p11_cathode_ratio(p):
    """Ik(Ec3 = -10 V)/Ik(0) and Ik(+10 V)/Ik(0) at Eb = Ec2 = 120 V: model and Raytheon p.11 (Ic2 alone at -10 V)."""
    out = {}
    for ec1 in (0, -1, -2, -3):
        ik = {e3: float(M.dual_cathode(ec1, 120.0, e3, 120.0, p)) for e3 in (-10, 0, 10)}
        s = IK3[f"Ec1_{ec1}"]
        out[f"Ec1_{ec1}"] = dict(model_m10_to_0=ik[-10] / ik[0], sheet_m10_to_0=s["Ec3_-10_Ic2_only"] / s["Ec3_0"],
                                 model_p10_to_0=ik[10] / ik[0], sheet_p10_to_0=s["Ec3_10"] / s["Ec3_0"])
    return out


def evaluate_55(p):
    """evaluate() on the #55 split, plus the held-out screen check by graph, p.11 top Ic2 (fitted since #55), the
    same errors on the #25-#53 split (for comparison with the #25/#51/#53 tables) and the p.11 cathode-current drop."""
    def by(rows):
        return dict(all=summary(rows, p), low_current=summary(low(rows), p))
    return dict(evaluate(p), split_by_graph=dict(p10_bottom_Ic2_vs_Ec3=by(R_P10_IC2), p8_bottom_Ic2=by(R_P8_IC2),
                                                 p11_top_Ic2_vs_Ec3_fitted_since_55=by(R_P11_IC2)),
                split_51=dict(raytheon_fit_set=by(R_FIT_51), split_check_heldout=by(R_SPLIT_51)),
                p11_cathode_current_ratio=p11_cathode_ratio(p))


res = dict(
    sources=dict(raytheon=f"{RAY_URL} (Raytheon 5725/6AS6W, 15 Dec 1960), pages 8-11; data/raytheon_5725_p8-11.csv "
                          "and (p.8 bottom Ib, issue #51) data/raytheon_5725_p8bottom.csv",
                 ge=f"{GE_URL} (GE ET-T706 12-49), pages 3 and 4"),
    low_current_region_mA=[LOW[0] * 1e3, LOW[1] * 1e3],
    split_check="issue #55 split. Held out (in no fit): Raytheon p.10 bottom Ic2 vs Ec3 and p.8 bottom Ic2 vs Eb. "
                "Fitted since #55: p.11 top Ic2 vs Ec3 (held out #25-#53). raytheon_fit_set / split_check_heldout of "
                "every entry below use this split; split_51 has the #25-#53 split (p.10 bottom + "
                "p.11 top Ic2 held out)",
    dual_control=dict(
        fit="issue #55 (option A of #53): model with KR and XA free; Raytheon p.8-11 except p.10 bottom Ic2 vs Ec3 "
            f"(so incl. p.11 top Ic2 vs Ec3), plus the p.8 bottom Ib curves (weight {W_P8} per point); residual "
            f"ln((I_model + I0)/(I_data + I0)), I0 = {I0 * 1e3} mA; KR in [0, 0.95], XA in [0.2, 1.5]",
        **evaluate_55(dual), restarts=restarts55, per_curve=per_curve(R_FIT, dual),
        per_curve_split=per_curve(R_SPLIT, dual), per_curve_p8_bottom=per_curve(R_P8, dual)),
    refit_51=dict(
        fit="issue #51 (dual_control until #55, BEFORE of #55): KR = 0, XA = 1; Raytheon p.8-11 except p.10 bottom and "
            "p.11 top Ic2 vs Ec3, plus the p.8 bottom Ib curves; same residual",
        **evaluate_55(refit_51), per_curve=per_curve(R_FIT, refit_51), per_curve_split=per_curve(R_SPLIT, refit_51),
        per_curve_p8_bottom=per_curve(R_P8, refit_51)),
    refit_25=dict(
        fit="issue #25: Raytheon p.8-11 except p.10 bottom and p.11 top Ic2 vs Ec3 (no p.8 bottom); same residual",
        **evaluate_55(refit_25), per_curve=per_curve(R_FIT, refit_25), per_curve_split=per_curve(R_SPLIT, refit_25),
        per_curve_p8_bottom=per_curve(R_P8, refit_25)),
    sensitivity_p8_weight_0_25=dict(fit="as refit_51 (#51), but weight 0.25 per p.8 bottom point",
                                    **evaluate_55(p8_w025)),
    before=dict(fit=f"GE p.3 + p.4 (Ec2 = 120 V); residual (model - data)/max(data, {FLOOR * 1e3} mA)",
                **evaluate_55(before), per_curve=per_curve(R_FIT, before)),
    ablation_raytheon_relative_residual=dict(
        fit=f"same Raytheon data as refit_25, BEFORE's residual (model - data)/max(data, {FLOOR * 1e3} mA)",
        **evaluate_55(ray_rel)),
    raytheon_internal_consistency=CONSIST,
    raytheon_p11_cathode_current_mA=IK3,
    koren=dict(params={k: float(v) for k, v in koren.items()},
               note="GE p.3 baseline only; g3 is not an input, so it cannot represent any suppressor data"),
)
with open(os.path.join(HERE, "results", "fit_results.json"), "w") as f:
    json.dump(res, f, indent=1)

# ---- plots (diagnostic, not committed): Raytheon graphs on a log current axis. Colour = the family's
# control voltage (sequential), marker = quantity (o Ib, ^ Ic2, x held out), solid = refit, dotted = before.
fig, axs = plt.subplots(2, 3, figsize=(19, 11))
SW = {"Eb_V": np.linspace(1, 400, 300), "Ec1_V": np.linspace(-5, 0, 300), "Ec3_V": np.linspace(-12, 12, 300)}
for ax, g in zip(axs.flat, dict.fromkeys(r["graph"] for r in RAY)):
    rg = [r for r in RAY if r["graph"] == g]
    sw = max(SW, key=lambda k: len({r[k] for r in rg}))
    fam = next(k for k in ("Ec2_V", "Ec1_V") if k != sw and len({r[k] for r in rg}) > 1)
    vals = sorted({r[fam] for r in rg})
    for c in dict.fromkeys(r["curve"] for r in rg):
        rc = [r for r in rg if r["curve"] == c]
        r0 = rc[0]
        held = r0 in R_SPLIT
        col = plt.cm.viridis(0.85 * vals.index(r0[fam]) / max(len(vals) - 1, 1))
        ax.plot([r[sw] for r in rc], [r["I_mA"] for r in rc], "x" if held else ("o" if r0["quantity"] == "Ib" else "^"),
                ms=3, color=col, label=c + (" (held out)" if held else ""))
        v = {k: np.full_like(SW[sw], r0[k]) for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V")}
        v[sw] = SW[sw]
        f = M.dual_ip if r0["quantity"] == "Ib" else M.dual_ig2
        for p, ls in ((dual, "-"), (refit_51, ":")):
            ax.plot(SW[sw], f(v["Ec1_V"], v["Ec2_V"], v["Ec3_V"], v["Eb_V"], p) * 1e3, ls, color=col, lw=1)
    ax.set_yscale("log")
    ax.set_ylim(0.05, 25)
    ax.set_xlabel(sw)
    ax.set_ylabel("mA (log)")
    ax.set_title(g + "\nsolid: refit (#55), dotted: #51 refit; shaded: 0.1-0.6 mA", fontsize=9)
    ax.grid(alpha=.3, which="both")
    ax.axhspan(LOW[0] * 1e3, LOW[1] * 1e3, color="0.92", zorder=0)
    ax.legend(fontsize=6, ncol=2)
plt.tight_layout()
plt.savefig(os.path.join(HERE, "plots", "fit_comparison.png"), dpi=100)

if __name__ == "__main__":
    print("restarts", {k: v for k, v in restarts55.items() if k != "lowest_cost_by_start"})
    for k in ("dual_control", "refit_51", "refit_25", "before", "ablation_raytheon_relative_residual",
              "sensitivity_p8_weight_0_25"):
        print(k,
              json.dumps({a: res[k][a] for a in ("params", "raytheon_fit_set", "split_check_heldout", "ge_crosscheck",
                                                     "p8_bottom_Ib", "split_by_graph", "p11_cathode_current_ratio")},
                            indent=1))
    print(json.dumps(CONSIST, indent=1))
    print(json.dumps(IK3, indent=1))
