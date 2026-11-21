"""#113: the 6AS6 aliasing assessment at every oversampling factor and the FIR stopband study. These tests read the
committed results (dk6as6/aliasing_quality.json, resampler_study.json, results_4x.json) and check what
dk6as6/RESULTS.md ("Aliasing at every quality (#113)") claims from them; the measurements themselves are too slow for CI
(the guard, dk6as6/aliasing_guard.py, re-measures a lean subset). The design checks recompute the candidates.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_aliasing_quality.py
"""
import json
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import aliasing as AL
from dk.resample import Resampler
from dk6as6 import aliasing_quality as Q, resampler_study as RS, lpf
from dk6as6 import validate as V
from dk6as6.stage import supported

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AQ = json.load(open(Q.OUT_JSON))
RSJ = json.load(open(RS.OUT_JSON))
R4 = json.load(open(os.path.join(HERE, "results_4x.json")))
GATE_SETTING = {"4x": "eco", "standard": "standard", "hq": "hq"}     # the 12AX7 budget each factor is compared with


def gate(setting, fs, drive):
    """The quality gate: the shipped 12AX7 stage's committed budget for the same setting / FIR / rate
    (dk/aliasing.limits_for_rate; 4x is compared with eco's, the 12AX7's 4x setting)."""
    return AL.limits_for_rate(fs)[GATE_SETTING[setting], "fir"][drive]


def test_4x_passes_validate_crit_at_every_reference():
    cases = {(m, float(b)) for m, rb in R4.items() for b in rb}
    assert cases == {(m, float(b)) for m, b in __import__("dk6as6.validate_4x", fromlist=["CASES"]).CASES}
    for m, rb in R4.items():
        for b, r in rb.items():
            assert r["oversampling"] == 4 and r["prewarp_hz"] is None
            assert V.status(r) == "passed", (m, b, r["passed"])


def test_reused_records_are_lpf_aliasing_json_and_not_duplicated():
    """P1 standard is #119's lpf_aliasing.json (same function and arguments): read from there, stored once."""
    reused = Q.reused_records()
    assert len(reused) == 304 and {(r["mode"], r["setting"]) for r in reused} == {Q.REUSED}
    assert not {Q.point_key(r) for r in reused} & {Q.point_key(r) for r in AQ["records"]}
    assert all(r["setting"] != "standard" or r["mode"] != "P1" for r in AQ["records"])


@pytest.mark.parametrize("row", AQ["summary"], ids=lambda r: f"{r['mode']}-{r['bias_ma']}-{r['setting']}-{r['fs']}-"
                                                              f"{r['drive']:g}V")
def test_every_supported_setting_meets_its_quality_gate(row):
    """Filter off, dBc (the repo metric). 64x is the ground truth, not a setting."""
    if row["setting"] == Q.GROUND_TRUTH:
        return
    assert supported(row["mode"], row["bias_ma"])
    assert row["off"]["worst_dbc"] <= gate(row["setting"], row["fs"], row["drive"]), row["off"]


def test_post_filter_lowers_the_absolute_alias_total_above_the_analysis_floor():
    """With the filter on, the total re the UNFILTERED fundamental is below the filter-off total at every point above
    dk.aliasing.ANALYSIS_FLOOR_DBC (at the floor both read numerical noise)."""
    for r in Q.records():
        if r["off"]["total_dbc"] > AL.ANALYSIS_FLOOR_DBC:
            for p in lpf.F0_HZ:
                assert r[p]["abs_db_re_off_fund"] < r["off"]["total_dbc"], (Q.point_key(r), p)


def test_hq_tones_cover_every_worst_tone_of_the_full_4x_and_8x_sweeps():
    """hq (16x) is swept on a tone subset (RESULTS.md says why); it must include the worst tone of the full 4x and 8x
    sweeps at every case, rate and drive."""
    full = {}
    for r in Q.records():
        if r["setting"] in ("4x", "standard"):
            full.setdefault((r["mode"], r["bias_ma"], r["fs"], r["drive"], r["setting"]), []).append(r)
    hq = {}
    for r in Q.records():
        if r["setting"] == "hq":
            hq.setdefault((r["mode"], r["bias_ma"], r["fs"], r["drive"]), set()).add(r["tone"])
    for (m, b, fs, d, s), rs in full.items():
        if len(rs) == len(AL.SWEEP_HZ):
            w = max(rs, key=lambda r: r["off"]["total_dbc"])["tone"]
            assert w in hq[m, b, fs, d], (m, b, fs, d, s, w)


def test_candidates_are_the_derived_designs():
    for name, by_rate in RS.CANDIDATES.items():
        for fs in RS.RATES:
            assert by_rate[fs] == (RS.shipped(fs) if name == "shipped" else RS.design_for(int(name[1:]), fs))
    assert json.loads(json.dumps(RSJ["method"]["candidates"])) == {
        k: {str(fs): [list(s) for s in v[fs]] for fs in RS.RATES} for k, v in RS.CANDIDATES.items()}


@pytest.mark.parametrize("row", [r for r in RSJ["design"] if r["candidate"] != "shipped"],
                         ids=lambda r: f"{r['candidate']}-{r['fs']}-{r['os']}x")
def test_candidate_meets_its_target(row):
    assert row["stopband_db"] >= int(row["candidate"][1:]) and row["ripple_db"] <= 0.01


def test_explicit_shipped_design_is_the_default_resampler():
    for fs in RS.RATES:
        for os_ in RS.FACTORS:
            a, b = Resampler(fs, os_, "fir"), Resampler(fs, os_, "fir", fir=RS.shipped(fs))
            assert a.latency_samples == b.latency_samples and all(
                np.array_equal(x.h, y.h) for x, y in zip(a.stages, b.stages))


def test_study_shipped_points_reproduce_the_committed_sweeps():
    """The study's shipped candidate at 48 kHz is the stage's own resampler: it reads the committed sweeps exactly."""
    sweeps = {}
    for mode in ("P1", "T", "P2"):
        for r in json.load(open(os.path.join(HERE, "aliasing.json" if mode == "P1" else f"aliasing_{mode}.json"))):
            sweeps[mode, r["bias_ma"], r["drive"], r["tone"]] = r["total_dbc"]
    n = 0
    for r in RSJ["records"]:
        if r["candidate"] == r["up"] == r["down"] == "shipped" and r["fs"] == 48000 and r["setting"] == "standard":
            assert r["off"]["total_dbc"] == sweeps[r["mode"], r["bias_ma"], r["drive"], r["tone"]]
            n += 1
    assert n == len(RS.POINTS)


def _worst(setting, fs, drive):
    return max(r["off"]["total_dbc"] for r in Q.records()
               if r["setting"] == setting and r["fs"] == fs and r["drive"] == drive)


def test_16x_reaches_the_64x_ground_truth():
    """At every ground-truth point hq reads the 64x total to 0.02 dB: no measurable internal folding is left at 16x."""
    idx = {(r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]): r for r in Q.records()}
    gt = [k for k in idx if k[0] == Q.GROUND_TRUTH]
    assert len(gt) == 8
    for k in gt:
        assert abs(idx[("hq",) + k[1:]]["off"]["total_dbc"] - idx[k]["off"]["total_dbc"]) <= 0.02, k


def test_2v_is_the_resampler_floor_at_every_factor():
    for r in Q.records():
        if r["drive"] == 2.0 and r["setting"] != Q.GROUND_TRUTH:
            assert r["off"]["internal_dbc"] <= -160.0, Q.point_key(r)


@pytest.mark.parametrize("fs, rise_db", [(48000, 38.9), (44100, 31.2)])
def test_the_eco_trade_quoted_in_results(fs, rise_db):
    """RESULTS.md: plain 4x raises the worst 8 V total over standard by 38.9 dB (48 kHz) / 31.2 dB (44.1 kHz), and stays
    inside the 12AX7 standard budget at both drives."""
    assert round(_worst("4x", fs, 8.0) - _worst("standard", fs, 8.0), 1) == rise_db
    for d in (2.0, 8.0):
        assert _worst("4x", fs, d) <= gate("standard", fs, d)
