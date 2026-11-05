"""Checks of the refit against the Raytheon curves digitised in issue #48 (see README.md).

Nothing here is fitted: the model is dual_control from results/fit_results.json (the #51 refit). The same checks
are repeated for the #25 parameters (refit_25) under `before_refit_25`. Since #51 the p.8 bottom Ib curves are in
the fit, so item 3 is a fit-quality check for them; its Ic2 curves and the gm graphs are still held out.
1) gm check: model gm(g1) = dIb/dEc1 and gm(g3) = dIb/dEc3 by central differences (+-H V) at the sheet
   conditions of the five gm graphs on pp. 11-13, against the digitised gm curves.
2) Sheet self-consistency: the same gm taken as the slope of the sheet's own current curves (p.9 bottom ->
   p.12 top, p.10 bottom -> p.13 top, p.11 top -> p.13 bottom), so model error can be told from sheet error.
3) p.8 bottom: model Ib (and Ic2) vs Eb at Ec3 = -1..-14 V, Ec1 = -2 V, Ec2 = 120 V, and the same points read
   off p.10 bottom / p.11 top at Eb = 120 V.
4) GE p.1 typical-operation gm values against the Raytheon gm curves (two makers' sheets).
Writes results/heldout_checks.json and plots/heldout_gm.png (not committed).
"""
import csv, json, os
import numpy as np
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
H = 0.01                  # V, central-difference step (as the GE cross-check in the tests)
G0 = 10.0                 # umho, offset in the log error (1-2 px on the gm graphs), like I0 in fit.py
LOW = (0.1, 0.6)          # mA, the low-current region used throughout fit.py
FIT = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
P, P25 = FIT["dual_control"]["params"], FIT["refit_25"]["params"]


def load(fn, val):
    with open(os.path.join(HERE, "data", fn)) as f:
        rows = list(csv.DictReader(l for l in f if not l.startswith("#")))
    for r in rows:
        for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V", val):
            r[k] = float(r[k])
    return rows


GM = load("raytheon_5725_gm_p11-13.csv", "gm_umho")
P8 = load("raytheon_5725_p8bottom.csv", "I_mA")
RAY = load("raytheon_5725_p8-11.csv", "I_mA")


def model_gm(q, g1, g2, g3, va, p=P):
    """umho; q = gm1 (d/dEc1) or gm3 (d/dEc3) of the plate current."""
    g1, g2, g3, va = (np.asarray(v, float) for v in (g1, g2, g3, va))
    if q == "gm1":
        d = M.dual_ip(g1 + H, g2, g3, va, p) - M.dual_ip(g1 - H, g2, g3, va, p)
    else:
        d = M.dual_ip(g1, g2, g3 + H, va, p) - M.dual_ip(g1, g2, g3 - H, va, p)
    return d / (2 * H) * 1e6


def stats(m, d, floor):
    """RMS of ln((m + floor)/(d + floor)), median |relative error|, mean relative error (bias), RMS abs error."""
    m, d = np.asarray(m, float), np.asarray(d, float)
    if not len(d):
        return dict(n=0)
    rel = (m - d) / d; lg = np.log((np.maximum(m, 0) + floor) / (d + floor))
    return dict(n=int(len(d)), rms_log=float(np.sqrt(np.mean(lg ** 2))), median_abs_rel=float(np.median(np.abs(rel))),
                mean_rel=float(np.mean(rel)), rms_abs=float(np.sqrt(np.mean((m - d) ** 2))),
                max_abs_rel=float(np.max(np.abs(rel))))


def arr(rows, *keys):
    return [np.array([r[k] for r in rows]) for k in keys]


# ---- 1) gm check
def gm_rows_model(rows, p=P):
    g1, g2, g3, va = arr(rows, "Ec1_V", "Ec2_V", "Ec3_V", "Eb_V")
    q = rows[0]["quantity"]
    return model_gm(q, g1, g2, g3, va, p)


def gm_graphs(p):
    out = {}
    for g in dict.fromkeys(r["graph"] for r in GM):
        rg = [r for r in GM if r["graph"] == g]
        curves = {}
        for c in dict.fromkeys(r["curve"] for r in rg):
            rc = [r for r in rg if r["curve"] == c]
            curves[c] = stats(gm_rows_model(rc, p), [r["gm_umho"] for r in rc], G0)
        m = np.concatenate([gm_rows_model([r for r in rg if r["curve"] == c], p) for c in curves])
        d = np.concatenate([[r["gm_umho"] for r in rg if r["curve"] == c] for c in curves])
        out[g] = dict(all=stats(m, d, G0), per_curve=curves)
    return out


def pooled(q, p=P):
    rows = [r for r in GM if r["quantity"] == q]
    cs = dict.fromkeys((r["graph"], r["curve"]) for r in rows)
    m = np.concatenate([gm_rows_model([r for r in rows if (r["graph"], r["curve"]) == k], p) for k in cs])
    d = np.concatenate([[r["gm_umho"] for r in rows if (r["graph"], r["curve"]) == k] for k in cs])
    return stats(m, d, G0)


gm_check = gm_graphs(P)
gm_summary = dict(gm1=pooled("gm1"), gm3=pooled("gm3"))


# ---- 2) sheet self-consistency: slope of the sheet's own current curves (local quadratic over +-W)
def sheet_slope(graph, curve, sweep, x, w):
    rr = sorted((r for r in RAY if r["graph"] == graph and r["curve"] == curve), key=lambda r: r[sweep])
    xs, ys = arr(rr, sweep, "I_mA")
    out = []
    for x0 in x:
        k = np.abs(xs - x0) <= w
        # need points on both sides of x0, spanning most of the window
        if k.sum() < 5 or xs[k].min() > x0 - 0.6 * w or xs[k].max() < x0 + 0.6 * w:
            out.append(np.nan); continue
        c = np.polyfit(xs[k] - x0, ys[k], 2)
        out.append(c[1] * 1e3)            # mA/V -> umho
    return np.array(out)


SELF = [  # (gm graph, gm curve label key, current graph, current curve, sweep, window V)
    ("p12_gm1_vs_Ec1_Ec2", "Ec2", "p9_Ib_vs_Ec1_Ec2", "Ec1_V", 0.25),
    ("p13_gm3_vs_Ec3_Ec2", "Ec2", "p10_vs_Ec3_Ec2", "Ec3_V", 0.75),
    ("p13_gm3_vs_Ec3_Ec1", "Ec1", "p11_vs_Ec3_Ec1", "Ec3_V", 0.75)]
self_check = {}
for gg, lab, cg, sw, w in SELF:
    ms, ds, per = [], [], {}
    for c in dict.fromkeys(r["curve"] for r in GM if r["graph"] == gg):
        rc = [r for r in GM if r["graph"] == gg and r["curve"] == c]
        cur = "Ib_" + c.split("_", 1)[1]
        s = sheet_slope(cg, cur, sw, [r[sw] for r in rc], w)
        k = np.isfinite(s); d = np.array([r["gm_umho"] for r in rc])[k]
        per[c] = stats(s[k], d, G0); ms.append(s[k]); ds.append(d)
    self_check[gg] = dict(from_current_graph=cg, window_V=w, all=stats(np.concatenate(ms), np.concatenate(ds), G0),
                          per_curve=per)

# ---- 3) p.8 bottom: plate family at fixed Ec3 (in the fit since #51) and the two Ic2 curves (held out)
def p8_model(rows, p=P):
    g1, g2, g3, va = arr(rows, "Ec1_V", "Ec2_V", "Ec3_V", "Eb_V")
    ib = np.array([r["quantity"] == "Ib" for r in rows])
    return np.where(ib, M.dual_ip(g1, g2, g3, va, p), M.dual_ig2(g1, g2, g3, va, p)) * 1e3


I0_MA = 0.02
P8_SETS = (("Ib_all", lambda r: r["quantity"] == "Ib"),
           ("Ib_Ec3_-1_to_-9", lambda r: r["quantity"] == "Ib" and r["Ec3_V"] >= -9),
           ("Ib_Ec3_-10_to_-14", lambda r: r["quantity"] == "Ib" and r["Ec3_V"] <= -10),
           ("Ib_0.1-0.6_mA", lambda r: r["quantity"] == "Ib" and LOW[0] <= r["I_mA"] <= LOW[1]),
           ("Ic2_Ec3_-14", lambda r: r["curve"] == "Ic2_Ec3_-14"),
           ("Ic2_Ec3_-4", lambda r: r["curve"] == "Ic2_Ec3_-4"))


def p8_summary(p):
    return {name: stats(p8_model(rows, p), [r["I_mA"] for r in rows], I0_MA)
            for name, sel in P8_SETS for rows in [[r for r in P8 if sel(r)]]}


p8 = p8_summary(P)
p8_per_curve = {c: stats(p8_model(rc), [r["I_mA"] for r in rc], I0_MA)
                for c in dict.fromkeys(r["curve"] for r in P8 if r["quantity"] != "Ic3")
                for rc in [[r for r in P8 if r["curve"] == c]]}


def at(rows, sweep, x, val="I_mA", tol=None):
    rr = sorted(rows, key=lambda r: r[sweep]); xs, ys = arr(rr, sweep, val)
    if not len(xs) or not xs.min() <= x <= xs.max():
        return None
    if tol is not None and np.min(np.abs(xs - x)) > tol:
        return None
    return float(np.interp(x, xs, ys))


# Ib at Eb = 120 V read off p.8 bottom (interpolated in Eb) vs p.10 bottom (Ec2 = 120) and p.11 top (Ec1 = -2)
p8_vs_transfer = []
for e3 in (-1, -2, -3, -4, -5, -6, -7):
    a = at([r for r in P8 if r["curve"] == f"Ib_Ec3_{e3}"], "Eb_V", 120.0)
    b = at([r for r in RAY if r["graph"] == "p10_vs_Ec3_Ec2" and r["curve"] == "Ib_Ec2_120"], "Ec3_V", e3)
    c = at([r for r in RAY if r["graph"] == "p11_vs_Ec3_Ec1" and r["curve"] == "Ib_Ec1_-2"], "Ec3_V", e3)
    p8_vs_transfer.append(dict(Ec3_V=e3, p8_bottom=a, p10_bottom=b, p11_top=c,
                               model=float(M.dual_ip(-2.0, 120.0, e3, 120.0, P) * 1e3)))

# How far the g3 cut-off moves with plate voltage on the sheet: Eb at which each Ib curve reaches a fixed current,
# and the effective plate-to-g3 mu = -dEb/dEc3 from a straight-line fit (the model's counterpart is MU3).
# The same is done on the model's curves (Eb 1-450 V), at the Ec3 values where the sheet reaches the level.
def eb_at(curve, level):
    rc = sorted((r for r in P8 if r["curve"] == curve), key=lambda r: r["Eb_V"])
    eb, i = arr(rc, "Eb_V", "I_mA")
    if not len(i) or not i.min() <= level <= i.max():
        return None
    k = int(np.argmax(i >= level))
    return float(np.interp(level, i[:k + 1], eb[:k + 1]))


EB_GRID = np.linspace(1.0, 450.0, 4491)


def model_eb_at(e3, level, p):
    i = M.dual_ip(-2.0, 120.0, e3, EB_GRID, p) * 1e3
    if not i.max() >= level:
        return None
    k = int(np.argmax(i >= level))
    return float(np.interp(level, i[k - 1:k + 1], EB_GRID[k - 1:k + 1])) if k else float(EB_GRID[0])


def plate_mu(pts):
    pts = {e: v for e, v in pts.items() if v is not None}
    return float(-1 / np.polyfit(list(pts.values()), list(pts), 1)[0]) if len(pts) >= 2 else None


def g3_cutoff_check(p):
    out = {}
    for level in (0.3, 1.0):
        pts = {e: eb_at(f"Ib_Ec3_{e}", level) for e in range(-5, -15, -1)}
        pts = {e: v for e, v in pts.items() if v is not None}
        mpts = {e: model_eb_at(e, level, p) for e in pts}
        out[f"Ib_{level}_mA"] = dict(Eb_V_by_Ec3={str(e): v for e, v in pts.items()}, plate_mu=plate_mu(pts),
                                     model_Eb_V_by_Ec3={str(e): v for e, v in mpts.items()}, model_plate_mu=plate_mu(mpts))
    out["model_MU3"] = p["MU3"]
    return out


g3_cutoff = g3_cutoff_check(P)

# ---- 4) GE p.1 typical operation (Eb = Ec2 = 120 V, Ec1 = -2 V) against the Raytheon gm curves.
# The Ec3 = 0 point itself is hidden by the heavy zero line, so it is interpolated across that gap (<= 0.3 V).
def gm_at(graph, curve, sweep, x):
    return at([r for r in GM if r["graph"] == graph and r["curve"] == curve], sweep, x, "gm_umho", tol=0.3)


def ge_gm_check(p):
    out = []
    for e3, g1s, g3s in ((-3, 1850, 810), (0, 3200, 470)):
        out.append(dict(
            Ec3_V=e3, ge_gm1=g1s, ge_gm3=g3s,
            raytheon_gm1_p11=gm_at("p11_gm1_vs_Ec3_Ec2", "gm1_Ec2_120", "Ec3_V", e3),
            raytheon_gm1_p12_top=gm_at("p12_gm1_vs_Ec1_Ec2", "gm1_Ec2_120", "Ec1_V", -2.0) if e3 == 0 else None,
            raytheon_gm3_p13_top=gm_at("p13_gm3_vs_Ec3_Ec2", "gm3_Ec2_120", "Ec3_V", e3),
            raytheon_gm3_p13_bottom=gm_at("p13_gm3_vs_Ec3_Ec1", "gm3_Ec1_-2", "Ec3_V", e3),
            model_gm1=float(model_gm("gm1", -2, 120, e3, 120, p)), model_gm3=float(model_gm("gm3", -2, 120, e3, 120, p))))
    return out


ge_gm = ge_gm_check(P)

# the same checks for the #25 parameters (fitted without p.8 bottom), for the #51 BEFORE/AFTER comparison
before_25 = dict(params=P25, gm_summary=dict(gm1=pooled("gm1", P25), gm3=pooled("gm3", P25)),
                 gm_per_graph={g: v["all"] for g, v in gm_graphs(P25).items()}, p8_bottom_summary=p8_summary(P25),
                 g3_cutoff_vs_plate_voltage=g3_cutoff_check(P25), ge_typical_vs_raytheon_gm_umho=ge_gm_check(P25))

res = dict(
    model="dual_control params from results/fit_results.json (issue #51 refit, p.8 bottom Ib in the fit); "
          "not refitted here. before_refit_25: the same checks with the #25 parameters",
    gm_definition=f"plate current derivative, central difference +-{H} V; umho",
    log_error=f"ln((model + f)/(data + f)), f = {G0} umho for gm, {I0_MA} mA for currents",
    gm_check=dict(summary=gm_summary, per_graph=gm_check),
    sheet_self_consistency=self_check,
    p8_bottom=dict(summary=p8, per_curve=p8_per_curve, Ib_at_Eb_120_vs_transfer_graphs_mA=p8_vs_transfer,
                   g3_cutoff_vs_plate_voltage=g3_cutoff),
    ge_typical_vs_raytheon_gm_umho=ge_gm,
    before_refit_25=before_25)
with open(os.path.join(HERE, "results", "heldout_checks.json"), "w") as f:
    json.dump(res, f, indent=1)

if __name__ == "__main__":
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    graphs = list(dict.fromkeys(r["graph"] for r in GM))
    fig, axs = plt.subplots(2, 3, figsize=(18, 10))
    for ax, g in zip(axs.flat, graphs):
        rg = [r for r in GM if r["graph"] == g]
        sw = "Ec1_V" if g.startswith("p12_gm1_vs_Ec1") else "Ec3_V"
        cs = list(dict.fromkeys(r["curve"] for r in rg))
        for i, c in enumerate(cs):
            rc = [r for r in rg if r["curve"] == c]; col = plt.cm.viridis(0.85 * i / max(len(cs) - 1, 1))
            ax.plot([r[sw] for r in rc], [r["gm_umho"] for r in rc], "o", ms=3, color=col, label=c)
            v = {k: np.full(300, rc[0][k]) for k in ("Ec1_V", "Ec2_V", "Ec3_V", "Eb_V")}
            v[sw] = np.linspace(-7, 0, 300) if sw == "Ec1_V" else np.linspace(-12, 12, 300)
            ax.plot(v[sw], model_gm(rc[0]["quantity"], v["Ec1_V"], v["Ec2_V"], v["Ec3_V"], v["Eb_V"]), "-", color=col, lw=1)
        ax.set_title(g + " (points: sheet, lines: model)", fontsize=9); ax.set_xlabel(sw); ax.set_ylabel("umho")
        ax.grid(alpha=.3); ax.legend(fontsize=6)
    ax = axs.flat[5]
    for i, c in enumerate(dict.fromkeys(r["curve"] for r in P8 if r["quantity"] == "Ib")):
        rc = [r for r in P8 if r["curve"] == c]; col = plt.cm.viridis(0.85 * i / 13)
        ax.plot([r["Eb_V"] for r in rc], [r["I_mA"] for r in rc], "o", ms=2, color=col)
        eb = np.linspace(1, 440, 300)
        ax.plot(eb, M.dual_ip(-2.0, 120.0, rc[0]["Ec3_V"], eb, P) * 1e3, "-", color=col, lw=1)
    ax.set_yscale("log"); ax.set_ylim(0.05, 10); ax.set_title("p8 bottom Ib, Ec3 = -1..-14 V (in the #51 fit)", fontsize=9)
    ax.grid(alpha=.3, which="both")
    plt.tight_layout(); os.makedirs(os.path.join(HERE, "plots"), exist_ok=True)
    plt.savefig(os.path.join(HERE, "plots", "heldout_gm.png"), dpi=100)
    print(json.dumps(dict(gm=gm_summary, self={k: v["all"] for k, v in self_check.items()}, p8=p8), indent=1))
    print(json.dumps(p8_vs_transfer, indent=1)); print(json.dumps(ge_gm, indent=1))
    for g, v in gm_check.items():
        print(g, {k: round(v["all"][k], 3) for k in ("n", "rms_log", "median_abs_rel", "mean_rel")})
        for c, s in v["per_curve"].items():
            print("   ", c, {k: round(s[k], 3) for k in ("n", "rms_log", "median_abs_rel", "mean_rel")})
