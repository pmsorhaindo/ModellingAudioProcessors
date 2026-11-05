"""Issue #53: g3-dependent cathode current (KR) and plate power (XA) in model6as6.py, and the study in ik_g3.py.
The #51 model (dual_control) is unchanged. Values at the time of writing in brackets (README, issue #53)."""
import json, os, sys
import numpy as np
import pytest
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import model6as6 as M  # noqa: E402
import ik_g3 as S  # noqa: E402  (imports fit.py, which reruns its fits; the #53 refits are not rerun here)
import heldout as H  # noqa: E402
from test_fit6as6 import _spice_eval  # noqa: E402

R = json.load(open(os.path.join(HERE, "results", "ik_g3_study.json")))
FIT = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
HO = json.load(open(os.path.join(HERE, "results", "heldout_checks.json")))
KEYS = ("refit_53", "refit_53_kr_only", "refit_53_xa_only", "diagnostic_p11_ic2_in_fit")
RNG = np.random.default_rng(53)
V = dict(g1=RNG.uniform(-5, 0, 400), g2=RNG.uniform(60, 140, 400), g3=RNG.uniform(-14, 12, 400), va=RNG.uniform(0, 420, 400))


def test_defaults_are_the_51_model():
    """KR = 0, XA = 1 (absent or explicit) gives the #51 equations exactly; dual_control is still the #51 fit."""
    p = FIT["dual_control"]["params"]
    assert p["MU3"] == pytest.approx(43.2167, rel=1e-5) and "KR" not in p and "XA" not in p
    g1, g2, g3, va = V["g1"], V["g2"], V["g3"], V["va"]
    ik = M.dual_ik(g1, g2, p["MU"], p["EX"], p["KG1"], p["KP"])
    z = (g3 + va / p["MU3"] - p["V3C"] - p["RI"] * ik) / p["S3"]
    f = p["FMAX"] * 2 / np.pi * np.arctan(va / p["KVB"]) * (1 + np.exp(-z)) ** -p["P3"]
    for q in (p, dict(p, KR=0.0, XA=1.0)):
        assert np.allclose(M.dual_ip(g1, g2, g3, va, q), ik * f, rtol=1e-12, atol=0)
        assert np.allclose(M.dual_ig2(g1, g2, g3, va, q), ik * (1 - f), rtol=1e-12, atol=0)
        assert np.array_equal(M.dual_cathode(g1, g2, g3, va, q), ik)


def test_extension_is_bounded_and_finite():
    """Ik stays in [(1 - KR) Ik0, Ik0]; no overflow at extreme g3 / plate voltages; finite slope at Va -> 0."""
    p = R["refit_53"]["params"]
    g3 = np.array([-1e4, -200, -14, 0, 12, 200, 1e4]); va = np.array([0, 1e-9, 1, 120, 420, 1e4, 1e5])
    for x in va:
        ik = M.dual_cathode(-2.0, 120.0, g3, x, p); ik0 = M.dual_ik(-2.0, 120.0, p["MU"], p["EX"], p["KG1"], p["KP"])
        assert np.all(np.isfinite(ik)) and np.all(ik <= ik0 * (1 + 1e-12)) and np.all(ik >= ik0 * (1 - p["KR"]) * (1 - 1e-12))
        assert np.all(np.isfinite(M.dual_ip(-2.0, 120.0, g3, x, p))) and np.all(M.dual_ip(-2.0, 120.0, g3, x, p) >= 0)
    d = (M.plate_term(2e-3, p["MU3"], p["XA"]) - M.plate_term(1e-3, p["MU3"], p["XA"])) / 1e-3
    assert np.isfinite(d) and d < 10


@pytest.mark.parametrize("key", ["refit_53", "diagnostic_p11_ic2_in_fit"])
def test_study_refit_reproduces_from_its_best_start(key):
    """One least_squares run from the committed best start gives the committed optimum (the full study, 19 starts
    per fit, is `python ik_g3.py`, about 45 s)."""
    names = [n for n in ("KR", "XA") if n in R[key]["params"]]
    dn, lo, hi = S.space(names)
    extra = (S.P11_IC2,) if key.startswith("diagnostic") else ()
    sol = least_squares(S.resid, R[key]["best_start"], bounds=(lo, hi), x_scale="jac", max_nfev=40000, args=(dn, extra))
    assert sol.cost == pytest.approx(R[key]["cost"], rel=1e-6)
    for n, v in zip(dn, sol.x):
        assert v == pytest.approx(R[key]["params"][n], rel=1e-4, abs=1e-6), n


@pytest.mark.parametrize("key", ("before_refit_51",) + KEYS)
def test_study_checks_reproduce(key):
    """Every reported metric reproduces from the committed parameters."""
    got, rep = S.checks(R[key]["params"]), R[key]["checks"]

    def walk(a, b, path):
        if isinstance(b, dict):
            for k in b:
                walk(a[k], b[k], path + (k,))
        elif isinstance(b, (int, float)) and b is not None:
            assert a == pytest.approx(b, rel=1e-6, abs=1e-9), path
        else:
            assert a == b, path
    walk(got, rep, (key,))


def test_restarts_reach_the_same_minimum():
    for key, need in (("refit_53", 10), ("diagnostic_p11_ic2_in_fit", 10)):
        r = R[key]["restarts"]
        assert r["seeded_same_minimum"] == r["seeded"] == 7                    # [7 / 7]
        assert r["random"] == 12 and r["random_same_minimum"] >= need           # [11, 10 of 12]
        assert min(r["lowest_cost_by_start"]) == pytest.approx(R[key]["cost"], rel=1e-12)


def test_fit_set_does_not_pin_kr():
    """On the #51 fit set (no screen current under g3 control) the cost changes by < 5 % over KR = 0..0.9, and the
    minimum sits where the cathode-current drop is far larger than on the sheet: KR is not identified (README)."""
    prof = R["kr_profile"]; c = [r["cost"] for r in prof]
    assert (max(c) - min(c)) / min(c) < 0.05                                       # [4.4 %]
    for key in ("refit_53", "refit_53_kr_only"):
        ck = R[key]["checks"]
        assert R[key]["params"]["KR"] > 0.6                                       # [0.79, 0.75]
        for e1, v in ck["cathode_current_p11"].items():
            assert v["model_ratio_m10_to_0"] < v["sheet_ratio_m10_to_0"] - 0.3, e1   # [0.36-0.40 vs 0.75-0.87]
        # and the held-out screen check gets much worse than #51 [0.40, 0.42 vs 0.18]
        assert ck["split_check_heldout"]["all"]["rms_log"] > 2 * R["before_refit_51"]["checks"]["split_check_heldout"]["all"]["rms_log"]


def test_diagnostic_with_screen_data_pins_kr():
    """With p.11 top Ic2 in the fit, KR is pinned and the cathode-current drop is the sheet's; the screen curves
    that are still held out (p.10 bottom Ic2 vs Ec3, p.8 bottom Ic2) are predicted no worse than by #51."""
    d, b = R["diagnostic_p11_ic2_in_fit"]["checks"], R["before_refit_51"]["checks"]
    assert 0.15 < R["diagnostic_p11_ic2_in_fit"]["params"]["KR"] < 0.4                 # [0.274]
    for e1, v in d["cathode_current_p11"].items():
        # [model 0.79-0.81 vs sheet 0.75-0.87: the sheet's drop shrinks with current faster than the model's]
        assert v["model_ratio_m10_to_0"] == pytest.approx(v["sheet_ratio_m10_to_0"], abs=0.07), e1
        assert v["model_ratio_p10_to_0"] == pytest.approx(1.0, abs=0.03)              # [1.01]; sheet 0.95-1.02
    p10 = lambda c: c["split_by_graph"]["p10_bottom_Ic2_vs_Ec3"]["all"]["rms_log"]
    assert p10(d) < p10(b) + 0.01                                                     # [0.199 vs 0.199]
    assert abs(d["p8_bottom_heldout"]["Ic2_Ec3_-14"]["mean_rel"]) < abs(b["p8_bottom_heldout"]["Ic2_Ec3_-14"]["mean_rel"])  # [-8 vs +14 %]
    assert d["gm_summary"]["gm3"]["rms_log"] < b["gm_summary"]["gm3"]["rms_log"]       # [0.507 vs 0.625]


@pytest.mark.parametrize("key", ("before_refit_51",) + KEYS)
def test_plate_mu_stays_in_the_sheet_range(key):
    """Issue #53 requirement: the plate-to-g3 mu stays in the sheet's range. The sheet's chord mu over the p.8 bottom
    cut-off lines is 39.0 (0.3 mA) / 45.5 (1 mA); the model's, measured the same way, must be within 15 % of it.
    With XA = 1, MU3 is that mu and must lie in [39, 46]; with XA < 1 MU3 is the local mu at Eb = 120 V [33.4-33.6],
    and the local mu at 250 V must lie in the sheet's chord range (README)."""
    c = R[key]["checks"]["g3_cutoff_vs_plate_voltage"]; p = R[key]["params"]
    for lv in ("Ib_0.3_mA", "Ib_1.0_mA"):
        assert c[lv]["model_plate_mu"] == pytest.approx(c[lv]["plate_mu"], rel=0.15), lv
    lo, hi = c["Ib_0.3_mA"]["plate_mu"], c["Ib_1.0_mA"]["plate_mu"]
    assert 38 < lo < hi < 47                                                    # [39.0, 45.5]
    if p.get("XA", 1.0) == 1.0:
        assert lo <= p["MU3"] <= hi                                              # [43.2, 43.4]
    else:
        assert 0.5 < p["XA"] < 0.65                                              # [0.565-0.576]
        mu250 = R[key]["checks"]["local_plate_mu_at_Eb_V"]["250.0"]
        assert lo * 0.95 <= mu250 <= hi * 1.15                                   # [45.6-46.2]


def test_spice_expressions_match_python_for_the_extension():
    import emit_spice as E
    for key in ("refit_53", "diagnostic_p11_ic2_in_fit"):
        p = R[key]["params"]; e = E.expressions(p)
        for i in range(200):
            v = dict(G1=V["g1"][i], G2=V["g2"][i], G3=V["g3"][i], P=max(V["va"][i], 20.0))
            v["ik0"] = _spice_eval(e["ik0"], v)
            v["ik"] = _spice_eval(e["ik"], v)
            ip = v["ik"] * _spice_eval(e["share"], v)
            assert ip == pytest.approx(M.dual_ip(v["G1"], v["G2"], v["G3"], v["P"], p), rel=1e-4), key
    # the committed include (#51 parameters) still has the old two-node form
    assert set(E.EXPR) == {"ik", "share"} and "Bik0" not in E.text()


def test_heldout_gm_bands_reproduce():
    for q in ("gm1", "gm3"):
        for band, v in HO["gm_check"]["by_Ec3_band"][q].items():
            for m, x in v.items():
                assert H.gm_by_band[q][band][m] == pytest.approx(x, rel=1e-6, abs=1e-9), (q, band, m)
    b = HO["gm_check"]["by_Ec3_band"]["gm3"]
    assert b["Ec3_5_and_above"]["mean_rel"] < -0.7                               # [-76 %], README #51
