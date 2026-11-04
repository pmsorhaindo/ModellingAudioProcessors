"""Fit Koren pentode and the dual-control extension to digitised GE 6AS6 curves.
Weighted relative error: r = (model - data) / max(data, 0.3 mA)."""
import csv, json, os
import numpy as np
from scipy.optimize import least_squares
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
FLOOR = 0.3e-3


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

# ---- 1) plain Koren on page-3 plate data (g3 = 0) + Koren screen eq on page-3 screen data
KN = ["MU", "EX", "KG1", "KP", "KVB"]
k_lo = [3, 1.0, 50, 5, 0.5]; k_hi = [200, 2.0, 1e5, 2000, 500]; k_x0 = [30, 1.35, 1500, 200, 15]
def rk(x):
    g1, g2, g3, va, i = a_ib3
    return (M.koren_ip(g1, g2, va, *x) - i) * w(i)
# fit in log space for scale-like params
sol_k = least_squares(rk, k_x0, bounds=(k_lo, k_hi), x_scale="jac", max_nfev=20000)
koren = dict(zip(KN, sol_k.x))
def rg2(x):
    g1, g2, g3, va, i = a_ic2
    return (M.koren_ig2(g1, g2, koren["MU"], x[0], x[1]) - i) * w(i)
sol_g2 = least_squares(rg2, [1.35, 4000], bounds=([1.0, 10], [2.0, 1e6]), x_scale="jac")
koren.update(EX_G2=sol_g2.x[0], KG2=sol_g2.x[1])

# ---- 2) dual-control extension, joint fit to all three data sets
DN = ["MU", "EX", "KG1", "KP", "FMAX", "MU3", "V3C", "S3", "P3", "RI", "KVB"]
d_lo = [3, 1.0, 50, 5, 0.3, 1, -30, 0.2, 0.05, 0, 0.5]; d_hi = [200, 2.0, 1e5, 2000, 1.0, 1000, 30, 20, 50, 2000, 60]
d_x0 = [30, 1.35, 1000, 100, 0.8, 30, -5, 2, 1, 100, 5]
P = lambda x: dict(zip(DN, x))
def rd(x):
    p = P(x); out = []
    for (g1, g2, g3, va, i), f in ((a_ib3, M.dual_ip), (a_ic2, M.dual_ig2), (a_ib4, M.dual_ip)):
        out.append((f(g1, g2, g3, va, p) - i) * w(i))
    return np.concatenate(out)
best = None
for s in (d_x0, [20, 1.4, 500, 50, 0.9, 10, -3, 1.5, 2, 300, 15], [50, 1.3, 2000, 300, 0.7, 100, -8, 3, 0.5, 0, 10], [24, 1.8, 1600, 160, 0.72, 56, -12, 2.7, 30, 50, 3]):
    sol = least_squares(rd, s, bounds=(d_lo, d_hi), x_scale="jac", max_nfev=40000)
    if best is None or sol.cost < best.cost:
        best = sol
dual = P(best.x)

# ---- metrics per curve
def metrics(rows, fn):
    out = {}
    for c in sorted(set(r["curve"] for r in rows)):
        rr = [r for r in rows if r["curve"] == c]; g1, g2, g3, va, i = arr(rr)
        m = fn(g1, g2, g3, va)
        e = (m - i) * 1e3; rel = (m - i) / np.maximum(i, FLOOR)
        out[c] = dict(n=len(rr), rms_mA=float(np.sqrt(np.mean(e**2))), max_abs_mA=float(np.max(np.abs(e))),
                      rms_rel=float(np.sqrt(np.mean(rel**2))), max_rel=float(np.max(np.abs(rel))))
    return out
kip = lambda g1, g2, g3, va: M.koren_ip(g1, g2, va, *[koren[k] for k in KN])
kig2 = lambda g1, g2, g3, va: M.koren_ig2(g1, g2, koren["MU"], koren["EX_G2"], koren["KG2"])
dip = lambda g1, g2, g3, va: M.dual_ip(g1, g2, g3, va, dual)
dig2 = lambda g1, g2, g3, va: M.dual_ig2(g1, g2, g3, va, dual)
res = dict(
    source="https://frank.pocnet.net/sheets/084/6/6AS6.pdf (GE ET-T706 12-49), pages 3 and 4",
    weighting=f"relative, denominator max(I, {FLOOR*1e3} mA)",
    koren=dict(params={k: float(v) for k, v in koren.items()},
               plate_p3=metrics(IB3, kip), screen_p3=metrics(IC2, kig2),
               note="g3 not an input; cannot represent page-4 data"),
    dual_control=dict(params={k: float(v) for k, v in dual.items()},
                      plate_p3=metrics(IB3, dip), screen_p3=metrics(IC2, dig2), plate_vs_g3_p4=metrics(p4, dip)))
json.dump(res, open(os.path.join(HERE, "results", "fit_results.json"), "w"), indent=1)

# ---- plots
fig, ax = plt.subplots(1, 3, figsize=(18, 5.5))
va = np.linspace(1, 245, 300)
for c in sorted(set(r["curve"] for r in p3)):
    rr = [r for r in p3 if r["curve"] == c]; g1 = rr[0]["Ec1_V"]; q = rr[0]["quantity"]
    g1a, g2a, g3a, vaa, ia = arr(rr); col = "C%d" % int(-g1)
    a = ax[0] if q == "Ib" else ax[1]
    a.plot(vaa, ia * 1e3, "o", ms=3, color=col, label=f"digitised Ec1={g1:g}")
    if q == "Ib":
        a.plot(va, M.dual_ip(g1, 120, 0, va, dual) * 1e3, "-", color=col)
        a.plot(va, M.koren_ip(g1, 120, va, *[koren[k] for k in KN]) * 1e3, ":", color=col)
    else:
        a.plot(va, M.dual_ig2(g1, 120, 0, va, dual) * 1e3, "-", color=col)
        a.plot(va, np.full_like(va, M.koren_ig2(g1, 120, koren["MU"], koren["EX_G2"], koren["KG2"]) * 1e3), ":", color=col)
ax[0].set_title("Plate current, Ec2=120 V, Ec3=0 (page 3)\nsolid: dual-control fit, dotted: Koren")
ax[1].set_title("Screen current, Ec2=120 V, Ec3=0 (page 3)\nsolid: dual-control fit, dotted: Koren eq.3")
v3 = np.linspace(-10, 10, 300)
for c in sorted(set(r["curve"] for r in p4)):
    rr = [r for r in p4 if r["curve"] == c]; g1 = rr[0]["Ec1_V"]; _, _, g3a, _, ia = arr(rr); col = "C%d" % int(-g1)
    ax[2].plot(g3a, ia * 1e3, "o", ms=3, color=col, label=f"digitised Ec1={g1:g}")
    ax[2].plot(v3, M.dual_ip(g1, 120, v3, 120, dual) * 1e3, "-", color=col)
ax[2].set_title("Plate current vs suppressor, Eb=Ec2=120 V (page 4)\nsolid: dual-control fit")
for a, xl in zip(ax, ["Eb (V)", "Eb (V)", "Ec3 (V)"]):
    a.set_xlabel(xl); a.set_ylabel("mA"); a.grid(alpha=.3); a.legend(fontsize=7)
plt.tight_layout(); plt.savefig(os.path.join(HERE, "plots", "fit_comparison.png"), dpi=110)
print(json.dumps(res, indent=1))
