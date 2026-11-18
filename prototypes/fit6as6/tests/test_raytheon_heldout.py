"""Issue #48: Raytheon p.8 bottom and the pp. 11-13 gm graphs, and the checks of the refit against them.
Since #51 the p.8 bottom Ib curves are in the fit; the gm graphs and p.8 bottom Ic2 are still held out.
Values at the time of writing in brackets, #55 refit / #51 refit unless stated (see README)."""
import json
import os
import sys
from collections import Counter
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
COMMITTED = json.load(open(os.path.join(HERE, "results", "heldout_checks.json")))
import heldout as H  # noqa: E402  (recomputes the checks from data/ and the committed parameters; < 1 s)

FIT = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
CAL = json.load(open(os.path.join(HERE, "data", "calibration.json")))["raytheon_5725"]
P8_CURVES = {**{f"Ib_Ec3_{e}": n for e, n in zip(range(-1, -15, -1),
                                                   (71, 68, 74, 73, 69, 66, 61, 61, 54, 46, 41, 33, 26, 20))},
             "Ic2_Ec3_-14": 16, "Ic2_Ec3_-4": 12, "Ic3_Ec3_2": 45, "Ic3_Ec3_1": 50, "Ic3_Ec3_0.5": 44}
GM_GRAPHS = {"p11_gm1_vs_Ec3_Ec2": 363, "p12_gm1_vs_Ec1_Ec2": 504, "p12_gm1_vs_Ec3_Ec1": 313,
             "p13_gm3_vs_Ec3_Ec2": 300, "p13_gm3_vs_Ec3_Ec1": 287}


def test_new_data_point_counts_and_calibration():
    assert Counter(r["curve"] for r in H.P8) == P8_CURVES
    assert Counter(r["graph"] for r in H.GM) == GM_GRAPHS
    assert CAL["p8_plate_vs_Eb_Ec3"]["points"] == len(H.P8) == 930
    for g, n in GM_GRAPHS.items():
        assert CAL[g]["points"] == n
    for g in ["p8_plate_vs_Eb_Ec3", *GM_GRAPHS]:
        assert CAL[g]["affine_rms_residual_px"] < 2.5 and CAL[g]["affine_max_residual_px"] < 6.5, g
    assert all(r["quantity"] in ("gm1", "gm3") and r["Eb_V"] == 120 for r in H.GM)
    assert all(r["Ec1_V"] == -2 and r["Ec2_V"] == 120 for r in H.P8)


def test_p8_bottom_agrees_with_the_transfer_graphs():
    """Ib at Eb = 120 V read off p.8 bottom vs p.10 bottom and p.11 top (same conditions) [<= 4.3 % / 9.5 %]."""
    for c in H.p8_vs_transfer:
        assert c["p8_bottom"] == pytest.approx(c["p10_bottom"], rel=0.06), c
        if c["p11_top"] is not None:
            assert c["p8_bottom"] == pytest.approx(c["p11_top"], rel=0.12), c


def test_heldout_results_reproduce():
    """heldout.py, rerun from data/ and the committed parameters, gives the committed results."""
    for q in ("gm1", "gm3"):
        for k, v in COMMITTED["gm_check"]["summary"][q].items():
            assert H.gm_summary[q][k] == pytest.approx(v, rel=1e-6, abs=1e-6), (q, k)
    for k, v in COMMITTED["p8_bottom"]["summary"].items():
        for m, x in v.items():
            assert H.p8[k][m] == pytest.approx(x, rel=1e-6, abs=1e-6), (k, m)
    assert H.P == pytest.approx(FIT["dual_control"]["params"], rel=1e-6, abs=1e-6)
    for key, got, fk in (("before_refit_25", H.before_25, "refit_25"), ("before_refit_51", H.before_51, "refit_51")):
        b = COMMITTED[key]
        assert b["params"] == pytest.approx(FIT[fk]["params"], rel=1e-6, abs=1e-6)
        for q in ("gm1", "gm3"):
            for k, v in b["gm_summary"][q].items():
                assert got["gm_summary"][q][k] == pytest.approx(v, rel=1e-6, abs=1e-6), (key, q, k)
        for k, v in b["p8_bottom_summary"].items():
            for m, x in v.items():
                assert got["p8_bottom_summary"][k][m] == pytest.approx(x, rel=1e-6, abs=1e-6), (key, k, m)
    # fit.py's p.8 bottom metrics are the same numbers as heldout.py's
    for k, j in (("all", "Ib_all"), ("Ec3_m10_to_m14", "Ib_Ec3_-10_to_-14")):
        assert (FIT["dual_control"]["p8_bottom_Ib"][k]["rms_log"]
                == pytest.approx(H.p8[j]["rms_log"], rel=1e-6, abs=1e-6))


def test_gm_check_bounds():
    """Model gm (central differences on the #55 parameters) vs the sheet's gm curves, never fitted."""
    s, b = COMMITTED["gm_check"]["summary"], COMMITTED["before_refit_51"]["gm_summary"]
    assert s["gm1"]["n"] >= 1100 and s["gm3"]["n"] >= 550
    assert s["gm1"]["rms_log"] < 0.3 and s["gm1"]["median_abs_rel"] < 0.15     # [0.239, 0.118 / 0.242, 0.130]
    assert abs(s["gm1"]["mean_rel"]) < 0.1                                     # [+0.008 / -0.007]
    # gm(g3) got worse with #51 (#25: 0.419) and partly recovers with #55; still too low for Ec3 > 0 (README)
    assert s["gm3"]["rms_log"] < 0.6 and s["gm3"]["median_abs_rel"] < 0.3      # [0.507, 0.257 / 0.625, 0.303]
    assert s["gm3"]["rms_log"] < b["gm3"]["rms_log"] - 0.05
    assert -0.2 < s["gm3"]["mean_rel"] < 0                                     # [-0.106 / -0.161]
    # at the GE/Raytheon operating point family (Ec2 = 120 V on p.11 bottom) gm1 is close [0.071, 2 % / 0.094, 3 %]
    c = COMMITTED["gm_check"]["per_graph"]["p11_gm1_vs_Ec3_Ec2"]["per_curve"]["gm1_Ec2_120"]
    assert c["rms_log"] < 0.15 and c["median_abs_rel"] < 0.08


def test_sheet_gm_self_consistency():
    """The sheet's gm curves vs the slopes of its own current curves: the floor for any model."""
    s = COMMITTED["sheet_self_consistency"]
    assert s["p12_gm1_vs_Ec1_Ec2"]["all"]["rms_log"] < 0.25                    # [0.179]
    for g in ("p13_gm3_vs_Ec3_Ec2", "p13_gm3_vs_Ec3_Ec1"):
        assert s[g]["all"]["rms_log"] < 0.4 and s[g]["all"]["median_abs_rel"] < 0.2   # [0.32-0.34, 12 %]


def test_ge_typical_gm_against_raytheon_curves():
    """GE p.1 typical gm values vs the Raytheon gm curves at the same point (Eb = Ec2 = 120 V, Ec1 = -2 V)."""
    for c in COMMITTED["ge_typical_vs_raytheon_gm_umho"]:
        assert c["raytheon_gm1_p11"] == pytest.approx(c["ge_gm1"], rel=0.1)       # [+6 %, -1 %]
        for k in ("raytheon_gm3_p13_top", "raytheon_gm3_p13_bottom"):
            assert c[k] == pytest.approx(c["ge_gm3"], rel=0.2)                    # [-14 %, -18 %]
            assert c["model_gm3"] == pytest.approx(c[k], rel=0.2)   # [+12 % at -3 V, -5 / 0 % at
                                                                    # 0 V; #51 +17 %, -6 / -1 %]
        assert c["model_gm1"] == pytest.approx(c["raytheon_gm1_p11"], rel=0.1)


def test_p8_bottom_g3_cutoff_moves_with_plate_voltage():
    """On the sheet the g3 cut-off moves with Eb (Ib reaches 0.3 mA at Eb = 66 V for Ec3 = -5 V and at 413 V for
    -14 V; plate mu 39 at 0.3 mA, 46 at 1 mA). The #25 model (MU3 = 225) barely moved it; with p.8 bottom in the
    fit (#51) MU3 = 43 lies in that range, and the model's own cut-off line has the sheet's slope. Since #55 (XA < 1)
    MU3 is the local mu at Eb = 120 V [33.6] and the local mu rises with Eb; the chord mu is the sheet's."""
    c = COMMITTED["p8_bottom"]["g3_cutoff_vs_plate_voltage"]
    for k in ("Ib_0.3_mA", "Ib_1.0_mA"):
        assert H.g3_cutoff[k]["plate_mu"] == pytest.approx(c[k]["plate_mu"], rel=1e-6, abs=1e-6)
        assert H.g3_cutoff[k]["model_plate_mu"] == pytest.approx(c[k]["model_plate_mu"], rel=1e-6, abs=1e-6)
        assert c[k]["model_plate_mu"] == pytest.approx(c[k]["plate_mu"], rel=0.15), k   # [42.2 vs 39.0, 44.2 vs 45.5]
    mu = sorted(c[k]["plate_mu"] for k in ("Ib_0.3_mA", "Ib_1.0_mA"))
    assert 25 < mu[0] and mu[1] < 60                                                     # [39, 46]
    p = FIT["dual_control"]["params"]
    assert c["model_MU3"] == p["MU3"] and 0.5 < p["XA"] < 0.65                           # [33.6, 0.574]
    assert mu[0] * 0.95 <= p["MU3"] * (250 / 120) ** (1 - p["XA"]) <= mu[1] * 1.15       # local mu at 250 V [46.0]
    assert mu[0] <= COMMITTED["before_refit_51"]["g3_cutoff_vs_plate_voltage"]["model_MU3"] <= mu[1]   # #51 [43.2]
    old = COMMITTED["before_refit_25"]["g3_cutoff_vs_plate_voltage"]
    assert old["model_MU3"] > 3 * mu[1]                                                  # [225]


def test_p8_bottom_deep_cut_error_improved_and_bounded():
    """Issue #51: p.8 bottom Ib at Ec3 = -10..-14 V (166 points), where the #25 model gave almost no plate current
    (RMS log 2.50, bias -94 %). With p.8 bottom in the fit it is close to the rest of the fit set [0.162, +0 %;
    #51 0.167, +4 %]."""
    new, old = (COMMITTED["p8_bottom"]["summary"]["Ib_Ec3_-10_to_-14"],
                COMMITTED["before_refit_25"]["p8_bottom_summary"]["Ib_Ec3_-10_to_-14"])
    assert new["n"] == old["n"] == 166
    assert old["rms_log"] > 2 and old["mean_rel"] < -0.8                                 # [2.502, -0.94]
    assert new["rms_log"] < 0.25 and new["rms_log"] < 0.1 * old["rms_log"]               # [0.162]
    assert new["median_abs_rel"] < 0.2 and abs(new["mean_rel"]) < 0.1                    # [0.116, +0.003]
    s, b = COMMITTED["p8_bottom"]["summary"], COMMITTED["before_refit_25"]["p8_bottom_summary"]
    for k in ("Ib_all", "Ib_Ec3_-1_to_-9", "Ib_0.1-0.6_mA"):
        assert s[k]["rms_log"] < 0.5 * b[k]["rms_log"], k                               # [0.161 vs 1.391, ...]
    assert s["Ib_all"]["rms_log"] < 0.2 and s["Ib_0.1-0.6_mA"]["rms_log"] < 0.3         # [0.161, 0.246 / 0.233, 0.395]
