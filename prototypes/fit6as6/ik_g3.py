"""Issue #53: g3-dependent cathode current. Refits of the extended dual-control model (model6as6.py: KR, XA) and
their checks against the #51 model (refit_51 in fit_results.json; dual_control until #55). Writes
results/ik_g3_study.json. Every fit and check here uses the #25-#53 data split (fit.py's R_FIT_51, R_SPLIT_51).
Issue #55 adopted the diagnostic's data split (option A): fit.py now fits it as dual_control.

Fits (all with fit.py's log residual, I0 = 0.02 mA, and its bounds; KR in [0, 0.95], XA in [0.2, 1.5]):
- refit_53         : KR + XA on the #51 fit set (Raytheon p.8-11 without the Ic2-vs-Ec3 curves, plus p.8 bottom Ib).
- refit_53_kr_only : KR only (XA = 1), same data: the smallest change that lets Ik depend on Ec3.
- refit_53_xa_only : XA only (KR = 0), same data: the plate-term change on its own.
- diagnostic_p11_ic2_in_fit : KR + XA, the #51 fit set plus the p.11 top Ic2-vs-Ec3 curves. NOT a candidate under
  the #53 data split (it fits half of the held-out screen check); it shows whether KR is pinned once screen
  current under g3 control is in the data. p.10 bottom Ic2 and p.8 bottom Ic2 stay held out.
Each fit: the 7 seeded starts of fit.py (KR = 0.15, XA = 0.7 appended where fitted) plus, for refit_53 and the
diagnostic, 12 random starts (log-uniform in bounds; uniform for V3C, RI, KR). Nothing is re-digitised.
"""
import json
import os
import numpy as np
from scipy.optimize import least_squares
import fit as F
import heldout as H
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
EXTRA = dict(KR=(0.0, 0.95, 0.15), XA=(0.2, 1.5, 0.7))
P11_IC2, P10_IC2 = F.R_P11_IC2, F.R_P10_IC2
N_RANDOM = 12
SAME = 1e-6            # relative cost difference counted as "the same minimum"


def space(names):
    return F.DN + names, F.d_lo + [EXTRA[n][0] for n in names], F.d_hi + [EXTRA[n][1] for n in names]


def resid(x, dn, extra_rows=()):
    p = dict(zip(dn, x))
    out = [np.log((np.maximum(F.predict(F.R_FIT_51, p), 0) + F.I0) / (F.I_FIT_51 + F.I0)),
           np.sqrt(F.W_P8) * np.log((np.maximum(F.predict(F.R_P8, p), 0) + F.I0) / (F.I_P8 + F.I0))]
    for rows in extra_rows:
        i = F.arr(rows)[4]
        out.append(np.log((np.maximum(F.predict(rows, p), 0) + F.I0) / (i + F.I0)))
    return np.concatenate(out)


random_starts = F.random_starts


def refit(names, extra_rows=(), n_random=0, seed=53):
    dn, lo, hi = space(names)
    starts = [list(s) + [EXTRA[n][2] for n in names] for s in F.SEEDS] + random_starts(lo, hi, n_random, seed)
    sols = [least_squares(resid, np.clip(s, lo, hi), bounds=(lo, hi), x_scale="jac", max_nfev=40000,
                          args=(dn, extra_rows)) for s in starts]
    k = int(np.argmin([s.cost for s in sols]))
    best = sols[k]
    def same(ss):
        return int(sum(abs(s.cost - best.cost) <= SAME * best.cost for s in ss))
    at_bound = [n for n, v, a, b in zip(dn, best.x, lo, hi)
                if np.isclose(v, a, rtol=1e-6) or np.isclose(v, b, rtol=1e-6)]
    return dict(params={n: float(v) for n, v in zip(dn, best.x)}, cost=float(best.cost),
                best_start=list(map(float, np.clip(starts[k], lo, hi))),
                restarts=dict(seeded=len(F.SEEDS), seeded_same_minimum=same(sols[:len(F.SEEDS)]),
                              random=n_random, random_same_minimum=same(sols[len(F.SEEDS):]),
                              lowest_cost_by_start=[float(s.cost) for s in sols]),
                params_at_bound=at_bound)


# Raytheon p.11: cathode current Ib + Ic2 (sheet, from fit.py's IK3) and the model's, at Eb = Ec2 = 120 V
def cathode_table(p):
    out = {}
    for ec1 in (0, -1, -2, -3):
        ik = {e3: float(M.dual_cathode(ec1, 120.0, e3, 120.0, p) * 1e3) for e3 in (-10, -4, 0, 4, 10)}
        sheet = F.IK3[f"Ec1_{ec1}"]
        out[f"Ec1_{ec1}"] = dict(model_mA={f"Ec3_{e}": v for e, v in ik.items()},
                                 model_ratio_m10_to_0=ik[-10] / ik[0], model_ratio_p10_to_0=ik[10] / ik[0],
                                 sheet_ratio_m10_to_0=sheet["Ec3_-10_Ic2_only"] / sheet["Ec3_0"],
                                 sheet_ratio_p10_to_0=sheet["Ec3_10"] / sheet["Ec3_0"])
    return out


def checks(p):
    """Every #53 metric for parameter set p (current errors as fit.py, gm/p.8 as heldout.py)."""
    e = F.evaluate(p, F.R_FIT_51, F.R_SPLIT_51)
    return dict(**e,
                split_by_graph=dict(p10_bottom_Ic2_vs_Ec3=dict(all=F.summary(P10_IC2, p),
                                                               low_current=F.summary(F.low(P10_IC2), p)),
                                    p11_top_Ic2_vs_Ec3=dict(all=F.summary(P11_IC2, p),
                                                            low_current=F.summary(F.low(P11_IC2), p))),
                gm_summary=dict(gm1=H.pooled("gm1", p), gm3=H.pooled("gm3", p)),
                gm_by_Ec3_band=dict(gm1=H.gm_bands("gm1", p), gm3=H.gm_bands("gm3", p)),
                gm_per_graph={g: v["all"] for g, v in H.gm_graphs(p).items()},
                p8_bottom_heldout=H.p8_summary(p), g3_cutoff_vs_plate_voltage=H.g3_cutoff_check(p),
                ge_typical_vs_raytheon_gm_umho=H.ge_gm_check(p), cathode_current_p11=cathode_table(p),
                local_plate_mu_at_Eb_V={str(v): float(p["MU3"] * (v / M.VA0) ** (1 - p.get("XA", 1.0)))
                                        for v in (40.0, 120.0, 250.0, 400.0)})


def kr_profile(p53, krs=(0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)):
    """Cost of the #53 fit set with KR held fixed (all other parameters refitted from the refit_53 optimum), and the
    held-out split check and p.11 cathode-current ratio at each: how well the fit set pins KR."""
    dn, lo, hi = space(["XA"])
    out = []
    for kr in krs:
        x0 = np.clip([p53[n] for n in dn], lo, hi)
        def r(x):
            return resid(np.append(x, kr), dn + ["KR"])
        s = least_squares(r, x0, bounds=(lo, hi), x_scale="jac", max_nfev=40000)
        p = dict(zip(dn, map(float, s.x)), KR=kr)
        out.append(dict(KR=kr, cost=float(s.cost), split_rms_log=F.summary(F.R_SPLIT_51, p)["rms_log"],
                        split_mean_rel=F.summary(F.R_SPLIT_51, p)["mean_rel"],
                        ik_ratio_m10_to_0_Ec1_m2=cathode_table(p)["Ec1_-2"]["model_ratio_m10_to_0"]))
    return out


FITS = {}
if __name__ == "__main__" or os.environ.get("IK_G3_REFIT"):
    FITS = dict(refit_53=refit(["KR", "XA"], n_random=N_RANDOM),
                refit_53_kr_only=refit(["KR"]),
                refit_53_xa_only=refit(["XA"]),
                diagnostic_p11_ic2_in_fit=refit(["KR", "XA"], extra_rows=(P11_IC2,), n_random=N_RANDOM))
    res = dict(
        note="issue #53 study, on the #25-#53 data split; before_refit_51 is refit_51 in fit_results.json. Issue #55 "
             "made the diagnostic's split and fit dual_control (README, 'g3-dependent cathode current (issue #53)', "
             "'Option A: p.11 top Ic2 in the fit (issue #55)')",
        model="model6as6.py with KR (Ik = Ik0*(1 - KR/(1+exp(z)))) and XA (plate term "
              f"(VA0/MU3)*(1+((Va/VA0)^XA-1)/XA), VA0 = {M.VA0} V); KR = 0, XA = 1 is the #51 model",
        fit_set="Raytheon p.8-11 except the Ic2-vs-Ec3 curves (p.10 bottom, p.11 top), plus p.8 bottom Ib (weight "
                f"{F.W_P8}); residual ln((I_model + I0)/(I_data + I0)), I0 = {F.I0 * 1e3} mA",
        diagnostic_fit_set="the fit set plus the p.11 top Ic2-vs-Ec3 curves (not a candidate; see docstring)",
        restart_rule=f"start counted as reaching the same minimum if its cost is within {SAME} (relative) of the best",
        before_refit_51=dict(params={k: float(v) for k, v in F.refit_51.items()}, checks=checks(F.refit_51)),
        kr_profile=kr_profile(FITS["refit_53"]["params"]),
        **{k: dict(v, checks=checks(v["params"])) for k, v in FITS.items()})
    with open(os.path.join(HERE, "results", "ik_g3_study.json"), "w") as f:
        json.dump(res, f, indent=1)
    for r in res["kr_profile"]:
        print({k: round(v, 4) for k, v in r.items()})
    for k in ("before_refit_51", *FITS):
        c = res[k]["checks"]
        print(k, {n: round(v, 4) for n, v in res[k]["params"].items()},
              res[k].get("restarts", {}).get("seeded_same_minimum"),
              res[k].get("restarts", {}).get("random_same_minimum"), res[k].get("params_at_bound"))
        print("   fit", round(c["raytheon_fit_set"]["all"]["rms_log"], 3), "split",
              round(c["split_check_heldout"]["all"]["rms_log"], 3),
              "p8", round(c["p8_bottom_Ib"]["all"]["rms_log"], 3), "gm3", round(c["gm_summary"]["gm3"]["rms_log"], 3),
              "Ik ratio", [round(v["model_ratio_m10_to_0"], 3) for v in c["cathode_current_p11"].values()])
