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
    # the only P1 standard points stored here are the band-edge tone, which #119's grid does not have
    assert {r["tone"] for r in AQ["records"] if r["setting"] == "standard" and r["mode"] == "P1"} == {Q.BAND_EDGE_HZ}


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
    assert len(gt) == 14 and {k[1] for k in gt} == set(Q.RATES)
    for k in gt:
        assert abs(idx[("hq",) + k[1:]]["off"]["total_dbc"] - idx[k]["off"]["total_dbc"]) <= 0.02, k


def test_2v_is_the_resampler_floor():
    """At 2 V, 8x and 16x have internal shares of at most -197.0 dBc at every point above the analysis floor; at 4x
    internal folding is at least 10 dB under the total at every point above -160 dBc (closest: 10.5 dB, P1 0.25 mA,
    44.1 kHz, 19937 Hz, -143.2 dBc), so every reported 2 V maximum is the resampler's."""
    for r in Q.records():
        if r["drive"] != 2.0 or r["setting"] == Q.GROUND_TRUTH or r["off"]["total_dbc"] <= AL.ANALYSIS_FLOOR_DBC:
            continue
        if r["setting"] in ("standard", "hq"):
            assert r["off"]["internal_dbc"] <= -197.0, Q.point_key(r)
        else:
            if r["off"]["total_dbc"] > -160.0:
                assert r["off"]["internal_dbc"] <= r["off"]["total_dbc"] - 10.0, Q.point_key(r)


@pytest.mark.parametrize("fs, rise_db", [(48000, 37.5), (44100, 34.8)])
def test_the_eco_trade_quoted_in_results(fs, rise_db):
    """RESULTS.md: plain 4x raises the worst 8 V total over standard by 37.5 dB (48 kHz) / 34.8 dB (44.1 kHz), on the
    grid with the band edge, and stays inside the 12AX7 standard budget at both drives."""
    assert round(_worst("4x", fs, 8.0) - _worst("standard", fs, 8.0), 1) == rise_db
    for d in (2.0, 8.0):
        assert _worst("4x", fs, d) <= gate("standard", fs, d)


def test_every_maximum_is_on_the_full_grid_or_a_validated_bound():
    """Every reported row covers GRID_HZ (the #6 tones and the band edge), except hq, whose rows carry the bound."""
    for r in AQ["summary"]:
        if r["setting"] == Q.GROUND_TRUTH:
            continue
        assert r["full_grid"] or (r["setting"] == "hq" and "grid_bound_dbc" in r["off"]), r


def test_hq_bound_from_the_8x_resampler_share():
    """hq's total is the 8x point's resampler share to within -0.15 / +0.05 dB at every common point; each key's
    predicted maximum is on a measured hq tone and the bound is within 0.15 dB above the measured maximum, so the subset
    maxima are the grid's to that accuracy."""
    hb = AQ["hq_bound"]
    assert -0.15 <= hb["excess_db"] <= 0.05
    sh = Q.standard_resampler_shares(Q.records())
    for r in Q.records():
        k = (r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"])
        if r["setting"] == "hq" and k in sh:
            assert -0.15 <= r["off"]["total_dbc"] - sh[k] <= hb["excess_db"] + 1e-12, k
    assert len(hb["keys"]) == 2 * 2 * len(Q.CASES)
    for k in hb["keys"]:
        assert k["bound_dbc"] >= k["measured_dbc"] and k["bound_dbc"] - k["measured_dbc"] <= 0.15, k
        tones = {r["tone"] for r in Q.records() if r["setting"] == "hq" and (r["fs"], r["mode"], r["bias_ma"],
                                                                                 r["drive"]) == (k["fs"], k["mode"],
                                                                                                 k["bias_ma"],
                                                                                                 k["drive"])}
        assert k["bound_tone"] in tones and k["measured_tone"] == k["bound_tone"], k


def _k140(setting=None):
    return [r for r in RSJ["records"] if r["candidate"] == r["up"] == r["down"] == "k140" and r["mode"] != "P2"
            and (setting is None or r["setting"] == setting)]


@pytest.mark.parametrize("setting", ["4x", "standard", "hq"])
def test_k140_checkpoints_at_every_factor(setting):
    """Supported-mode k140 checkpoints (P1 0.1 / 0.6 mA, T 0.3 mA; 2 and 8 V; 16037 / 19037 / 19937 Hz; both rates):
    never above the shipped FIR's total by more than 0.01 dB, and every 2 V point at least MARGIN_DB inside the gate."""
    idx = {(r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]): r for r in Q.records()}
    pts = _k140(setting)
    assert {(r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]) for r in pts} >= {
        (fs, m, b, d, f) for fs in RS.RATES for m, b in (("P1", 0.1), ("P1", 0.6), ("T", 0.3)) for d in (2.0, 8.0)
        for f in (16037, 19037, 19937)}
    for r in pts:
        s = idx.get((setting, r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]))
        if s is not None:
            assert r["off"]["total_dbc"] <= s["off"]["total_dbc"] + 0.01, (r["fs"], r["mode"], r["tone"])
        if r["drive"] == 2.0:
            assert r["off"]["total_dbc"] <= gate(setting, r["fs"], 2.0) - AL.MARGIN_DB


def _scan(fs, setting, drive, tone, mode="P1", bias=0.6):
    return next(r for r in AQ["band_edge_scan"] if (r["fs"], r["setting"], r["drive"], r["tone"], r["mode"],
                                                     r["bias_ma"]) == (fs, setting, drive, tone, mode, bias))


def test_shipped_fir_exceeds_the_gate_near_20khz_at_441_and_k140_does_not():
    """RESULTS.md, "The band edge": P1 0.6 mA (supported), 2 V, 44.1 kHz, 19987 Hz reads above the standard gate with
    the shipped FIR at every factor, and the image-rejection bound says so for the whole 15-20 kHz band; k140 is far
    inside it, measured and bounded."""
    for s in ("4x", "standard", "hq"):
        assert _scan(44100, s, 2.0, 19987)["off"]["total_dbc"] > gate("standard", 44100, 2.0)
    k = next(r for r in RSJ["records"] if r["candidate"] == r["up"] == "k140" and r["fs"] == 44100
             and r["setting"] == "standard" and (r["mode"], r["bias_ma"], r["drive"], r["tone"]) == ("P1", 0.6, 2.0,
                                                                                                    19987))
    assert k["off"]["total_dbc"] <= gate("standard", 44100, 2.0) - AL.MARGIN_DB
    key = [r for r in Q.records() + AQ["band_edge_scan"] if (r["fs"], r["setting"], r["mode"], r["bias_ma"],
                                                              r["drive"]) == (44100, "standard", "P1", 0.6, 2.0)]
    b = RS.band_edge_bound(key, 44100, 8)
    assert b["k_max"] - b["k_min"] <= 5.0 and b["bound_dbc"] > gate("standard", 44100, 2.0)
    assert b["k_max"] - float(np.min(RS.image_rejection_db(44100, 8, np.linspace(15000, 20000, 20001),
                                                           RS.CANDIDATES["k140"][44100]))) \
        <= gate("standard", 44100, 2.0) - AL.MARGIN_DB


def test_longer_settle_reads_the_same():
    """The deepest floors re-measured after 2.5 s instead of SETTLE_S: the total moves by less than 0.1 dB."""
    chk = AQ["settle_check"]
    assert len(chk) == 3
    idx = {Q.point_key(r): r for r in Q.records()}
    for r in chk:
        assert r["settle_s"] == 2.5
        assert abs(r["off"]["total_dbc"] - idx[Q.point_key(r)]["off"]["total_dbc"]) < 0.1, Q.point_key(r)
