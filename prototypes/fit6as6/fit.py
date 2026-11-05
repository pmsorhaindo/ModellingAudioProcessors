"""Fit the 6AS6 models to digitised datasheet curves (see README.md).

BEFORE (kept for comparison, issue #8 fit): dual-control model fitted to the GE sheet (Ec2 = 120 V
only) with a relative residual (model - data) / max(data, 0.3 mA), which gives the 0.1-0.6 mA
region almost no weight.
Issue #25 refit (`refit_25`): the same dual-control model fitted to the Raytheon 1960 families
(Ec2 = 60..140 V, Ec1 down to -5 V, Ec3 -12..+12 V) with a log residual
ln((I_model + I0) / (I_data + I0)), I0 = 0.02 mA (about 1-2 px of digitising resolution), so a
10 % miss at 0.2 mA costs as much as a 10 % miss at 10 mA.
Issue #51 refit (current, `dual_control`): the #25 data plus the Raytheon p.8 bottom plate family (Ib vs Eb
at Ec3 = -1..-14 V, digitised in #48), the only curves that vary the plate voltage at fixed negative Ec3 and so
pin MU3. Same model, residual and bounds; each p.8 bottom point has weight W_P8 = 1, like every other Raytheon
point (one point per 5 V Eb step, as on the p.8 top and p.9 top plate families). The #25 refit (no p.8
bottom) is kept as `refit_25`, and a refit with weight 0.25 on p.8 bottom as a sensitivity check.
Held out of every fit: the Raytheon screen-current-vs-Ec3 curves (p.10 bottom, p.11 top), which
check the model's plate/screen split under suppressor control, and the p.8 bottom Ic2 curves (heldout.py).
"""
import csv, json, os
import numpy as np
from scipy.optimize import least_squares
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
FLOOR = 0.3e-3          # BEFORE residual denominator floor
I0 = 0.02e-3            # AFTER log-residual offset
LOW = (0.1e-3, 0.6e-3)  # the Culture Vulture's metered operating range (research/culture-vulture-controls.md)
RAY_URL = "https://frank.pocnet.net/sheets/138/5/5725.pdf"
GE_URL = "https://frank.pocnet.net/sheets/084/6/6AS6.pdf"


def load(fn):
    with open(os.path.join(HERE, "data", fn)) as f:
        rows = list(csv.DictReader(l for l in f if not l.startswith("#")))
    for r in rows:
        for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", "I_mA"):
            r[k] = float(r[k])
    return rows


def arr(rows):
    g = lambda k: np.array([r[k] for r in rows])
    return g("Ec1_V"), g("Ec2_V"), g("Ec3_V"), g("Eb_V"), g("I_mA") * 1e-3


p3 = load("plate_characteristics_p3.csv"); p4 = load("suppressor_characteristics_p4.csv")
IB3 = [r for r in p3 if r["quantity"] == "Ib"]; IC2 = [r for r in p3 if r["quantity"] == "Ic2"]
a_ib3, a_ic2, a_ib4 = arr(IB3), arr(IC2), arr(p4)
w = lambda i: 1.0 / np.maximum(i, FLOOR)

RAY = load("raytheon_5725_p8-11.csv")
SPLIT_GRAPHS = ("p10_vs_Ec3_Ec2", "p11_vs_Ec3_Ec1")
R_SPLIT = [r for r in RAY if r["graph"] in SPLIT_GRAPHS and r["quantity"] == "Ic2"]   # held out
R_FIT = [r for r in RAY if r not in R_SPLIT]
GE = p3 + [dict(r, quantity="Ib") for r in p4]
# issue #51: p.8 bottom Ib curves join the fit. Its two Ic2 curves stay held out (screen current under g3 control,
# like R_SPLIT; checked in heldout.py) and Ic3 is not modelled.
R_P8 = [r for r in load("raytheon_5725_p8bottom.csv") if r["quantity"] == "Ib"]
R_P8_DEEP = [r for r in R_P8 if r["Ec3_V"] <= -10]
W_P8 = 1.0              # per-point weight of p.8 bottom in the #51 fit (sensitivity check: 0.25)


def predict(rows, p):
    """Model current (A) for each digitised point: Ib rows -> plate, Ic2 rows -> screen."""
    g1, g2, g3, va, _ = arr(rows); ib = np.array([r["quantity"] == "Ib" for r in rows])
    return np.where(ib, M.dual_ip(g1, g2, g3, va, p), M.dual_ig2(g1, g2, g3, va, p))


# ---- 1) plain Koren on page-3 plate data (g3 = 0) + Koren screen eq on page-3 screen data (baseline)
KN = ["MU", "EX", "KG1", "KP", "KVB"]
k_lo = [3, 1.0, 50, 5, 0.5]; k_hi = [200, 2.0, 1e5, 2000, 500]; k_x0 = [30, 1.35, 1500, 200, 15]
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
d_lo = [3, 1.0, 50, 5, 0.3, 1, -30, 0.2, 0.05, 0, 0.5]; d_hi = [200, 2.0, 1e5, 2000, 1.0, 1000, 30, 20, 50, 2000, 60]
STARTS = [[30, 1.35, 1000, 100, 0.8, 30, -5, 2, 1, 100, 5], [20, 1.4, 500, 50, 0.9, 10, -3, 1.5, 2, 300, 15],
          [50, 1.3, 2000, 300, 0.7, 100, -8, 3, 0.5, 0, 10], [24, 1.8, 1600, 160, 0.72, 56, -12, 2.7, 30, 50, 3]]
P = lambda x: dict(zip(DN, x))


def fit_dual(resid, starts):
    best = None
    for s in starts:
        sol = least_squares(resid, np.clip(s, d_lo, d_hi), bounds=(d_lo, d_hi), x_scale="jac", max_nfev=40000)
        if best is None or sol.cost < best.cost:
            best = sol
    return P(best.x)


def r_before(x):      # issue #8: GE p.3 + p.4, relative residual with a 0.3 mA floor
    p = P(x); out = []
    for (g1, g2, g3, va, i), f in ((a_ib3, M.dual_ip), (a_ic2, M.dual_ig2), (a_ib4, M.dual_ip)):
        out.append((f(g1, g2, g3, va, p) - i) * w(i))
    return np.concatenate(out)


I_FIT, I_P8 = arr(R_FIT)[4], arr(R_P8)[4]
r_log = lambda rows, i, x: np.log((np.maximum(predict(rows, P(x)), 0) + I0) / (i + I0))
r_ray_rel = lambda x: (predict(R_FIT, P(x)) - I_FIT) * w(I_FIT)                    # ablation
r_ray_log = lambda x: r_log(R_FIT, I_FIT, x)                                       # issue #25
r_p8_log = lambda x, wt=W_P8: np.concatenate([r_ray_log(x), np.sqrt(wt) * r_log(R_P8, I_P8, x)])   # issue #51

before = fit_dual(r_before, STARTS)
ray_rel = fit_dual(r_ray_rel, STARTS + [list(before.values())])
refit_25 = fit_dual(r_ray_log, STARTS + [list(before.values()), list(ray_rel.values())])
SEEDS = STARTS + [list(before.values()), list(ray_rel.values()), list(refit_25.values())]
dual = fit_dual(r_p8_log, SEEDS)
p8_w025 = fit_dual(lambda x: r_p8_log(x, 0.25), SEEDS)


# ---- metrics
def errs(rows, p):
    i = arr(rows)[4]; m = predict(rows, p)
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
        out[g] = {c: dict(summary(rr, p), low_current=summary(low(rr), p)) for c in dict.fromkeys(r["curve"] for r in rg)
                  for rr in [[r for r in rg if r["curve"] == c]]}
    return out


def evaluate(p):
    return dict(params={k: float(v) for k, v in p.items()},
                raytheon_fit_set=dict(all=summary(R_FIT, p), low_current=summary(low(R_FIT), p)),
                split_check_heldout=dict(all=summary(R_SPLIT, p), low_current=summary(low(R_SPLIT), p)),
                ge_crosscheck=dict(all=summary(GE, p), low_current=summary(low(GE), p)),
                p8_bottom_Ib=dict(all=summary(R_P8, p), Ec3_m1_to_m9=summary([r for r in R_P8 if r not in R_P8_DEEP], p),
                                  Ec3_m10_to_m14=summary(R_P8_DEEP, p), low_current=summary(low(R_P8), p)))


# Internal consistency of the Raytheon sheet: the same operating point read off different graphs.
def _at(graph, curve, sweep, x):
    rr = sorted((r for r in RAY if r["graph"] == graph and r["curve"] == curve), key=lambda r: r[sweep])
    xs = np.array([r[sweep] for r in rr]); ys = np.array([r["I_mA"] for r in rr])
    return float(np.interp(x, xs, ys)) if len(xs) and xs.min() <= x <= xs.max() else None


CONSIST = []
for q, ec1, reads in (
        ("Ib", -2, [("p9_plate_vs_Eb_Ec1", "Ib_Ec1_-2", "Eb_V", 120), ("p9_Ib_vs_Ec1_Ec2", "Ib_Ec2_120", "Ec1_V", -2),
                    ("p10_vs_Ec3_Ec2", "Ib_Ec2_120", "Ec3_V", 0), ("p11_vs_Ec3_Ec1", "Ib_Ec1_-2", "Ec3_V", 0)]),
        ("Ib", -4, [("p9_plate_vs_Eb_Ec1", "Ib_Ec1_-4", "Eb_V", 120), ("p9_Ib_vs_Ec1_Ec2", "Ib_Ec2_120", "Ec1_V", -4),
                    ("p11_vs_Ec3_Ec1", "Ib_Ec1_-4", "Ec3_V", 0)]),
        ("Ic2", -2, [("p9_plate_vs_Eb_Ec1", "Ic2_Ec1_-2", "Eb_V", 120), ("p10_Ic2_vs_Ec1_Ec2", "Ic2_Ec2_120", "Ec1_V", -2),
                     ("p10_vs_Ec3_Ec2", "Ic2_Ec2_120", "Ec3_V", 0), ("p11_vs_Ec3_Ec1", "Ic2_Ec1_-2", "Ec3_V", 0)]),
        ("Ic2", -4, [("p9_plate_vs_Eb_Ec1", "Ic2_Ec1_-4", "Eb_V", 120), ("p10_Ic2_vs_Ec1_Ec2", "Ic2_Ec2_120", "Ec1_V", -4),
                     ("p11_vs_Ec3_Ec1", "Ic2_Ec1_-4", "Ec3_V", 0)])):
    v = {g: _at(g, c, s, x) for g, c, s, x in reads}
    vals = [x for x in v.values() if x is not None]
    CONSIST.append(dict(point=f"{q} at Eb = Ec2 = 120 V, Ec1 = {ec1} V, Ec3 = 0", mA=v,
                        spread=(max(vals) - min(vals)) / np.mean(vals)))
# Raytheon p.11: cathode current Ib + Ic2 vs Ec3 (the dual-control model holds it constant)
IK3 = {}
for ec1 in (0, -1, -2, -3):
    s = {e3: _at("p11_vs_Ec3_Ec1", f"Ib_Ec1_{ec1}", "Ec3_V", e3) for e3 in (-4, 0, 4, 10)}
    IK3[f"Ec1_{ec1}"] = {f"Ec3_{e3}": (s[e3] + _at("p11_vs_Ec3_Ec1", f"Ic2_Ec1_{ec1}", "Ec3_V", e3)) if s[e3] else None
                         for e3 in s}
    IK3[f"Ec1_{ec1}"]["Ec3_-10_Ic2_only"] = _at("p11_vs_Ec3_Ec1", f"Ic2_Ec1_{ec1}", "Ec3_V", -10)

res = dict(
    sources=dict(raytheon=f"{RAY_URL} (Raytheon 5725/6AS6W, 15 Dec 1960), pages 8-11; data/raytheon_5725_p8-11.csv "
                          "and (p.8 bottom Ib, issue #51) data/raytheon_5725_p8bottom.csv",
                 ge=f"{GE_URL} (GE ET-T706 12-49), pages 3 and 4"),
    low_current_region_mA=[LOW[0] * 1e3, LOW[1] * 1e3],
    split_check="held out: Raytheon Ic2 vs Ec3 (p.10 bottom, p.11 top dashed curves) are in no fit",
    dual_control=dict(
        fit="issue #51: Raytheon p.8-11 except the held-out Ic2-vs-Ec3 curves, plus the p.8 bottom Ib curves "
            f"(weight {W_P8} per point); residual ln((I_model + I0)/(I_data + I0)), I0 = {I0 * 1e3} mA",
        **evaluate(dual), per_curve=per_curve(R_FIT, dual), per_curve_split=per_curve(R_SPLIT, dual),
        per_curve_p8_bottom=per_curve(R_P8, dual)),
    refit_25=dict(
        fit="issue #25: Raytheon p.8-11 except the held-out Ic2-vs-Ec3 curves (no p.8 bottom); same residual",
        **evaluate(refit_25), per_curve=per_curve(R_FIT, refit_25), per_curve_split=per_curve(R_SPLIT, refit_25),
        per_curve_p8_bottom=per_curve(R_P8, refit_25)),
    sensitivity_p8_weight_0_25=dict(fit="as dual_control, but weight 0.25 per p.8 bottom point", **evaluate(p8_w025)),
    before=dict(fit=f"GE p.3 + p.4 (Ec2 = 120 V); residual (model - data)/max(data, {FLOOR * 1e3} mA)",
                **evaluate(before), per_curve=per_curve(R_FIT, before)),
    ablation_raytheon_relative_residual=dict(
        fit=f"same Raytheon data as dual_control, BEFORE's residual (model - data)/max(data, {FLOOR * 1e3} mA)",
        **evaluate(ray_rel)),
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
    rg = [r for r in RAY if r["graph"] == g]; sw = max(SW, key=lambda k: len({r[k] for r in rg}))
    fam = next(k for k in ("Ec2_V", "Ec1_V") if k != sw and len({r[k] for r in rg}) > 1)
    vals = sorted({r[fam] for r in rg})
    for c in dict.fromkeys(r["curve"] for r in rg):
        rc = [r for r in rg if r["curve"] == c]; r0 = rc[0]; held = r0 in R_SPLIT
        col = plt.cm.viridis(0.85 * vals.index(r0[fam]) / max(len(vals) - 1, 1))
        ax.plot([r[sw] for r in rc], [r["I_mA"] for r in rc], "x" if held else ("o" if r0["quantity"] == "Ib" else "^"),
                ms=3, color=col, label=c + (" (held out)" if held else ""))
        v = {k: np.full_like(SW[sw], r0[k]) for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V")}; v[sw] = SW[sw]
        f = M.dual_ip if r0["quantity"] == "Ib" else M.dual_ig2
        for p, ls in ((dual, "-"), (refit_25, ":")):
            ax.plot(SW[sw], f(v["Ec1_V"], v["Ec2_V"], v["Ec3_V"], v["Eb_V"], p) * 1e3, ls, color=col, lw=1)
    ax.set_yscale("log"); ax.set_ylim(0.05, 25); ax.set_xlabel(sw); ax.set_ylabel("mA (log)")
    ax.set_title(g + "\nsolid: refit (#51), dotted: #25 refit; shaded: 0.1-0.6 mA", fontsize=9)
    ax.grid(alpha=.3, which="both"); ax.axhspan(LOW[0] * 1e3, LOW[1] * 1e3, color="0.92", zorder=0)
    ax.legend(fontsize=6, ncol=2)
plt.tight_layout(); plt.savefig(os.path.join(HERE, "plots", "fit_comparison.png"), dpi=100)

if __name__ == "__main__":
    for k in ("dual_control", "refit_25", "before", "ablation_raytheon_relative_residual", "sensitivity_p8_weight_0_25"):
        print(k, json.dumps({a: res[k][a] for a in ("params", "raytheon_fit_set", "split_check_heldout", "ge_crosscheck",
                                                     "p8_bottom_Ib")},
                            indent=1))
    print(json.dumps(CONSIST, indent=1)); print(json.dumps(IK3, indent=1))
