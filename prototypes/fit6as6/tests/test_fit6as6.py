import json
import os
import re
import sys
import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import model6as6 as M  # noqa: E402 -- after the sys.path setup
import fit as F  # noqa: E402  (re-runs every fit; deterministic)

R = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
P = R["dual_control"]["params"]


@pytest.mark.parametrize("key",
                         ["dual_control", "refit_51", "refit_25", "before", "ablation_raytheon_relative_residual",
                                 "sensitivity_p8_weight_0_25"])
def test_fit_is_reproducible(key):
    """Re-running fit.py gives the committed parameters (before = the GE fit, refit_25 = the #25 refit, refit_51 = the
    #51 refit with p.8 bottom, dual_control = the #55 refit with KR, XA and p.11 top Ic2)."""
    p = dict(dual_control=F.dual, refit_51=F.refit_51, refit_25=F.refit_25, before=F.before,
             ablation_raytheon_relative_residual=F.ray_rel, sensitivity_p8_weight_0_25=F.p8_w025)[key]
    for k, v in R[key]["params"].items():
        assert p[k] == pytest.approx(v, rel=1e-4), k


def test_data_sets():
    """Raytheon points: fit set and held-out split set are disjoint and cover the low-current region. Issue #55 split:
    p.11 top Ic2 vs Ec3 is fitted; p.10 bottom Ic2 vs Ec3 and p.8 bottom Ic2 are held out."""
    assert len(F.R_FIT) == 3107 and len(F.R_SPLIT) == 355 == 327 + 28
    assert all(r["quantity"] == "Ic2" and r["graph"] in F.SPLIT_GRAPHS for r in F.R_SPLIT)
    assert not any(r["quantity"] == "Ic2" and r["graph"] in F.SPLIT_GRAPHS for r in F.R_FIT + F.R_P8)
    assert len(F.R_P11_IC2) == 328 and all(r in F.R_FIT for r in F.R_P11_IC2)
    assert len(F.low(F.R_FIT)) >= 400 and len(F.low(F.R_SPLIT)) == 74       # p.8 bottom Ic2 has no point in 0.1-0.6 mA
    assert {r["Ec2_V"] for r in F.R_FIT} >= {60, 80, 100, 120, 140}
    # the #25-#53 split, still used by refit_25 / refit_51 and ik_g3.py
    assert len(F.R_FIT_51) == 2779 and len(F.R_SPLIT_51) == 655 and len(F.low(F.R_SPLIT_51)) == 111
    assert sorted(map(id, F.R_FIT)) == sorted(map(id, F.R_FIT_51 + F.R_P11_IC2))
    # issue #51: the p.8 bottom Ib curves (Ec3 = -1..-14 V) are fitted; its Ic2 curves stay held out
    assert len(F.R_P8) == 763 and len(F.R_P8_DEEP) == 166 and F.W_P8 == 1.0
    assert all(r["quantity"] == "Ib" and r["graph"] == "p8_plate_vs_Eb_Ec3" for r in F.R_P8)
    assert {r["Ec3_V"] for r in F.R_P8} == set(range(-14, 0))


@pytest.mark.parametrize("key", ["per_curve", "per_curve_split", "per_curve_p8_bottom"])
def test_reported_per_curve_errors_reproduce(key):
    rows = dict(per_curve=F.R_FIT, per_curve_split=F.R_SPLIT, per_curve_p8_bottom=F.R_P8)[key]
    for graph, curves in R["dual_control"][key].items():
        for curve, rep in curves.items():
            rr = [r for r in rows if r["graph"] == graph and r["curve"] == curve]
            got = F.summary(rr, P)
            assert got["n"] == rep["n"] >= 12             # p.8 bottom Ic2: 16 and 12 points
            for m in ("rms_log", "rms_rel", "max_abs_mA"):
                assert got[m] == pytest.approx(rep[m], rel=1e-6, abs=1e-12)
            assert F.summary(F.low(rr), P)["n"] == rep["low_current"]["n"]


def test_refit_error_bounds():
    """Sane bounds on the #55 refit, #55 split (values at the time of writing in brackets, #51 refit after the slash;
    README)."""
    d = R["dual_control"]
    assert d["raytheon_fit_set"]["all"]["rms_log"] < 0.12                 # [0.106 / 0.110]
    assert d["raytheon_fit_set"]["low_current"]["rms_log"] < 0.25         # [0.180 / 0.175]
    assert d["raytheon_fit_set"]["low_current"]["median_abs_rel"] < 0.15  # [0.115 / 0.101]
    for curves in d["per_curve"].values():
        for c, e in curves.items():
            assert e["rms_log"] < 0.27, c        # worst [0.242, p.10 bottom Ib at Ec2 = 140 V / 0.250]
    assert d["p8_bottom_Ib"]["all"]["rms_log"] < 0.2                       # [0.161 / 0.233]
    for curves in d["per_curve_p8_bottom"].values():
        for c, e in curves.items():
            assert e["rms_log"] < 0.3, c         # worst [0.247, Ec3 = -13 V / 0.395, Ec3 = -3 V, low-Eb toe]


def test_refit_beats_before_in_low_current_region():
    a, b = R["dual_control"]["raytheon_fit_set"], R["before"]["raytheon_fit_set"]
    assert a["low_current"]["n"] == b["low_current"]["n"]
    assert a["low_current"]["rms_log"] < 0.6 * b["low_current"]["rms_log"]          # [0.180 vs 0.327]
    assert a["low_current"]["median_abs_rel"] < 0.5 * b["low_current"]["median_abs_rel"]
    assert a["all"]["rms_log"] < 0.6 * b["all"]["rms_log"]                          # [0.106 vs 0.181]


def test_split_check_heldout():
    """Screen current under g3 control that no fit uses (#55 split: p.10 bottom Ic2 vs Ec3, p.8 bottom Ic2) is predicted
    from the cathode current and the plate/screen split. [#55 / #51 on the same points]"""
    s, b = R["dual_control"]["split_check_heldout"], R["refit_51"]["split_check_heldout"]
    assert s["all"]["n"] == 355 and s["all"]["rms_log"] < 0.22                    # [0.197 / 0.205]
    assert s["all"]["rms_log"] < b["all"]["rms_log"]
    assert 0 < s["all"]["mean_rel"] < 0.12 and s["all"]["mean_rel"] < 0.5 * b["all"]["mean_rel"]   # [+7 % / +16 %]
    # the low-current part (all p.10 bottom) got worse with #55 (README, option A)
    assert s["low_current"]["n"] == 74 and s["low_current"]["rms_log"] < 0.27      # [0.236 / 0.206]


def test_cathode_current_falls_at_deep_g3_cutoff():
    """Measured (Raytheon p.11): Ib + Ic2 stays within 10 % for Ec3 = -4..+10 V but is 13-25 % lower at
    Ec3 = -10 V. Since #55 the model's cathode current falls past g3 cut-off by the sheet's amount (KR)."""
    for ec1 in ("Ec1_0", "Ec1_-1", "Ec1_-2"):
        ik = R["raytheon_p11_cathode_current_mA"][ec1]
        for e3 in ("Ec3_-4", "Ec3_4", "Ec3_10"):
            assert ik[e3] == pytest.approx(ik["Ec3_0"], rel=0.1)
        assert 0.7 < ik["Ec3_-10_Ic2_only"] / ik["Ec3_0"] < 0.9
    for ec1, v in R["dual_control"]["p11_cathode_current_ratio"].items():
        assert v["model_m10_to_0"] == pytest.approx(v["sheet_m10_to_0"], abs=0.07), ec1   # [0.79-0.81 vs 0.75-0.87]
        assert v["model_p10_to_0"] == pytest.approx(1.0, abs=0.03), ec1                   # [1.01-1.02]
        assert R["refit_51"]["p11_cathode_current_ratio"][ec1]["model_m10_to_0"] == 1.0


def test_raytheon_graphs_agree_with_each_other():
    """The same operating point read off different Raytheon graphs (digitising + drafting scatter)."""
    for c in R["raytheon_internal_consistency"]:
        assert c["spread"] < (0.05 if "-2 V" in c["point"] else 0.3), c


def test_datasheet_typical_operation_crosscheck():
    """GE sheet p.1, class A1 columns (Eb=Ec2=120 V, Ec1=-2 V). NOT used in the fit. The refit follows Raytheon,
    which reads Ib 3.32 (p.11) / 3.45 (p.10) mA at Ec3=-3 V where GE states 3.6. #55 gives 3.11 mA (#51: 3.04,
    #25: 3.13): -14 % vs GE, so Ib at -3 V is checked at 20 % against GE and 15 % against both Raytheon readings."""
    for src in ("p10_vs_Ec3_Ec2", "p11_vs_Ec3_Ec1"):
        ray = F._at(src, "Ib_Ec2_120" if src.startswith("p10") else "Ib_Ec1_-2", "Ec3_V", -3) * 1e-3
        assert M.dual_ip(-2, 120, -3, 120, P) == pytest.approx(ray, rel=0.15), src
    for g3, ib, ic2, gm in ((-3, 3.6e-3, 4.8e-3, 1850e-6), (0, 5.2e-3, 3.5e-3, 3200e-6)):
        assert M.dual_ip(-2, 120, g3, 120, P) == pytest.approx(ib, rel=0.2 if g3 == -3 else 0.15)
        assert M.dual_ig2(-2, 120, g3, 120, P) == pytest.approx(ic2, rel=0.15)
        d = (M.dual_ip(-1.99, 120, g3, 120, P) - M.dual_ip(-2.01, 120, g3, 120, P)) / 0.02
        assert d == pytest.approx(gm, rel=0.15)


def _spice_eval(expr, v):
    s = expr.replace("1m", "1e-3")
    s = (re.sub(r"V\((\w+),K\)", lambda m: f"v['{m.group(1)}']", s)
         .replace("V(ik)", "v['ik']").replace("V(ik0)", "v['ik0']"))
    env = dict(ln=np.log, exp=np.exp, atan=np.arctan, max=np.maximum, pwr=lambda x, y: np.abs(x) ** y, v=v)
    return eval(s, env)


def test_spice_subcircuit_matches_python_model():
    from emit_spice import EXPR
    rng = np.random.default_rng(0)
    assert set(EXPR) == {"ik0", "ik", "share"}          # #55: the KR/XA form (node ik0 = Ik0)
    for _ in range(200):
        v = dict(G1=rng.uniform(-5, 0), G2=rng.uniform(60, 140), G3=rng.uniform(-14, 12), P=rng.uniform(20, 420))
        v["ik0"] = _spice_eval(EXPR["ik0"], v)
        v["ik"] = _spice_eval(EXPR["ik"], v)
        ip = v["ik"] * _spice_eval(EXPR["share"], v)
        assert ip == pytest.approx(M.dual_ip(v["G1"], v["G2"], v["G3"], v["P"], P), rel=1e-4)
