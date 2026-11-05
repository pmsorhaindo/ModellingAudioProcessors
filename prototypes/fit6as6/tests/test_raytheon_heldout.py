"""Issue #48: Raytheon p.8 bottom and the pp. 11-13 gm graphs, and the held-out checks of the #25 refit.
Values at the time of writing in brackets (see README)."""
import json, os, sys
from collections import Counter
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
COMMITTED = json.load(open(os.path.join(HERE, "results", "heldout_checks.json")))
import heldout as H  # noqa: E402  (recomputes the checks from data/ and the committed parameters; < 1 s)

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
            assert H.gm_summary[q][k] == pytest.approx(v, rel=1e-9), (q, k)
    for k, v in COMMITTED["p8_bottom"]["summary"].items():
        for m, x in v.items():
            assert H.p8[k][m] == pytest.approx(x, rel=1e-9), (k, m)


def test_gm_check_bounds():
    """Model gm (central differences on the #25 parameters) vs the sheet's gm curves, never fitted."""
    s = COMMITTED["gm_check"]["summary"]
    assert s["gm1"]["n"] >= 1100 and s["gm3"]["n"] >= 550
    assert s["gm1"]["rms_log"] < 0.3 and s["gm1"]["median_abs_rel"] < 0.15     # [0.241, 0.094]
    assert abs(s["gm1"]["mean_rel"]) < 0.1                                     # [+0.032]
    assert s["gm3"]["rms_log"] < 0.5 and s["gm3"]["median_abs_rel"] < 0.3      # [0.419, 0.231]
    assert abs(s["gm3"]["mean_rel"]) < 0.1                                     # [+0.004]
    # at the GE/Raytheon operating point family (Ec2 = 120 V on p.11 bottom) gm1 is close [0.099, 4 %]
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
            assert c["model_gm3"] == pytest.approx(c[k], rel=0.15)                 # [+7..+12 %]
        assert c["model_gm1"] == pytest.approx(c["raytheon_gm1_p11"], rel=0.1)


def test_p8_bottom_g3_cutoff_moves_with_plate_voltage():
    """Known limitation (README): on the sheet the g3 cut-off moves with Eb (Ib reaches 0.3 mA at Eb = 66 V for
    Ec3 = -5 V and at 413 V for -14 V; plate mu about 40); the #25 model (MU3 = 225) barely moves it, so it predicts almost no plate
    current at Ec3 <= -10 V. If a future model fixes this, update this test and the README."""
    c = COMMITTED["p8_bottom"]["g3_cutoff_vs_plate_voltage"]
    for k in ("Ib_0.3_mA", "Ib_1.0_mA"):
        assert H.g3_cutoff[k]["plate_mu"] == pytest.approx(c[k]["plate_mu"], rel=1e-9)
    assert 25 < c["Ib_0.3_mA"]["plate_mu"] < 60 and 25 < c["Ib_1.0_mA"]["plate_mu"] < 60   # [39, 46]
    assert c["model_MU3"] > 3 * c["Ib_1.0_mA"]["plate_mu"]                                # [225]
    assert COMMITTED["p8_bottom"]["summary"]["Ib_Ec3_-10_to_-14"]["mean_rel"] < -0.8   # [-0.94]
    assert COMMITTED["p8_bottom"]["summary"]["Ib_Ec3_-1_to_-9"]["median_abs_rel"] > 0.2  # [0.33]
