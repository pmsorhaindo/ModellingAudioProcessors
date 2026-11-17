"""Issue #55 (option A of #53): dual_control is the KR + XA fit with p.11 top Ic2 vs Ec3 in the fit; the held-out
screen check is p.10 bottom Ic2 vs Ec3 plus p.8 bottom Ic2. Values at the time of writing in brackets (README)."""
import json
import os
import sys
import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import model6as6 as M  # noqa: E402
import bench_eval as B  # noqa: E402

FIT = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
STUDY = json.load(open(os.path.join(HERE, "results", "ik_g3_study.json")))
BENCH = json.load(open(os.path.join(HERE, "results", "bench_eval.json")))
D, B51 = FIT["dual_control"], FIT["refit_51"]


def test_dual_control_is_the_53_diagnostic_minimum():
    """Same data, residual and bounds as #53's diagnostic_p11_ic2_in_fit, so the same minimum (other starts)."""
    diag = STUDY["diagnostic_p11_ic2_in_fit"]
    assert D["restarts"]["cost"] == pytest.approx(diag["cost"], rel=1e-8)            # [27.3407]
    for k, v in diag["params"].items():
        assert D["params"][k] == pytest.approx(v, rel=1e-3, abs=1e-6), k


def test_restarts_reach_the_same_minimum():
    r = D["restarts"]
    assert r["seeded"] == r["seeded_same_minimum"] == 8                              # [8 / 8]
    assert r["random"] == 12 and r["random_same_minimum"] >= 10                      # [12 of 12]
    assert min(r["lowest_cost_by_start"]) == pytest.approx(r["cost"], rel=1e-12)
    assert r["params_at_bound"] == ["KVB"]                                           # 0.5 V, as every #53 XA fit
    assert 0.2 < D["params"]["KR"] < 0.35 and 0.5 < D["params"]["XA"] < 0.65          # [0.274, 0.574]


def test_split_51_numbers_are_the_documented_51_numbers():
    """refit_51 on the #25-#53 split (split_51) is what #51/#53 reported (ik_g3.py, before_refit_51)."""
    old = STUDY["before_refit_51"]["checks"]
    for k in ("raytheon_fit_set", "split_check_heldout"):
        for sub in ("all", "low_current"):
            for m, v in old[k][sub].items():
                assert B51["split_51"][k][sub][m] == pytest.approx(v, rel=1e-6, abs=1e-9), (k, sub, m)
    assert B51["split_51"]["split_check_heldout"]["all"]["rms_log"] == pytest.approx(0.184, abs=5e-4)


def test_heldout_screen_by_graph():
    """[#55 / #51] p.10 bottom Ic2 0.199 / 0.206 (bias +9 / +17 %); p.8 bottom Ic2 0.177 / 0.185."""
    s, b = D["split_by_graph"], B51["split_by_graph"]
    assert s["p10_bottom_Ic2_vs_Ec3"]["all"]["n"] == 327 and s["p8_bottom_Ic2"]["all"]["n"] == 28
    assert s["p10_bottom_Ic2_vs_Ec3"]["all"]["rms_log"] < b["p10_bottom_Ic2_vs_Ec3"]["all"]["rms_log"]
    assert s["p10_bottom_Ic2_vs_Ec3"]["all"]["mean_rel"] < 0.6 * b["p10_bottom_Ic2_vs_Ec3"]["all"]["mean_rel"]
    assert s["p8_bottom_Ic2"]["all"]["rms_log"] < 0.2
    # p.11 top Ic2 is fitted since #55 [0.101 / 0.158 held out]
    assert s["p11_top_Ic2_vs_Ec3_fitted_since_55"]["all"]["rms_log"] < 0.12


def test_kr_term_reuses_the_share_exp():
    """dual_ip_ig2 with KR > 0 (one z, one exp shared by the share and the KR term) equals the #53 code path, which
    recomputed z and took its own softplus and exp; and the KR = 0 path equals dual_ip / dual_ig2."""
    rng = np.random.default_rng(55)
    n = 5000
    v = (rng.uniform(-5, 0, n), rng.uniform(60, 140, n), rng.uniform(-300, 300, n), rng.uniform(0, 1e4, n))
    ip, ig2 = M.dual_ip_ig2(*v, D["params"])
    jp, jg2 = B.ip_ig2_unshared(*v, D["params"])
    assert (np.all(np.isfinite(ip)) and np.allclose(ip, jp, rtol=1e-12, atol=0)
            and np.allclose(ig2, jg2, rtol=1e-12, atol=0))
    ip, ig2 = M.dual_ip_ig2(*v, B51["params"])
    assert np.allclose(ip, M.dual_ip(*v, B51["params"]), rtol=1e-14, atol=0)
    assert np.allclose(ig2, M.dual_ig2(*v, B51["params"]), rtol=1e-12, atol=0)


def test_benchmark_record():
    """bench_eval.json is a timing record of one run on one machine; only its structure is checked."""
    r = BENCH["results"]
    assert set(r) == {"form_51", "dual_control_55", "dual_control_55_unshared_exp"}
    assert BENCH["ratio_55_to_51"]["vectorised"] == pytest.approx(
        r["dual_control_55"]["vectorised_ns_per_point"] / r["form_51"]["vectorised_ns_per_point"])
    assert len(B.GRID[0]) == 5200 and "5200 operating points" in BENCH["method"]
