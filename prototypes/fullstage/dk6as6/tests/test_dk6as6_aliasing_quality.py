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


def test_16x_against_the_64x_ground_truth():
    """No-grid cases: hq reads the 64x total to 0.02 dB at every ground-truth point (no measurable internal folding
    left at 16x). T 0.5 mA with grid current: hq is 23.5-30.4 dB above the 64x total at 8 V (internal folding of the
    grid-current law that 16x does not remove)."""
    idx = {(r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]): r for r in Q.records()}
    gt = [k for k in idx if k[0] == Q.GROUND_TRUTH]
    assert len(gt) == 18 and {k[1] for k in gt} == set(Q.RATES)
    for k in gt:
        d = idx[("hq",) + k[1:]]["off"]["total_dbc"] - idx[k]["off"]["total_dbc"]
        if Q.hardness(idx[k]) == 0.0:
            assert abs(d) <= 0.02, k
        else:
            assert 23.0 <= d <= 31.0, (k, d)


def test_2v_is_the_resampler_floor():
    """At 2 V, 8x and 16x have internal shares of at most -197.0 dBc at every point above the analysis floor; at 4x
    internal folding is at least 10 dB under the total at every point above -160 dBc (closest: 10.5 dB, P1 0.25 mA,
    44.1 kHz, 19937 Hz, -143.2 dBc), so every reported 2 V maximum is the resampler's."""
    for r in Q.records():
        if (r["drive"] != 2.0 or r["setting"] == Q.GROUND_TRUTH or r["off"]["total_dbc"] <= AL.ANALYSIS_FLOOR_DBC
                or Q.hardness(r) > 0):           # the no-grid cases; grid-current T: test_grid_current_t_...
            continue
        if r["setting"] in ("standard", "hq"):
            assert r["off"]["internal_dbc"] <= -197.0, Q.point_key(r)
        else:
            if r["off"]["total_dbc"] > -160.0:
                assert r["off"]["internal_dbc"] <= r["off"]["total_dbc"] - 10.0, Q.point_key(r)


def _case_worst(setting, fs, drive, mode, bias):
    return max(r["off"]["total_dbc"] for r in Q.records() if (r["setting"], r["fs"], r["drive"], r["mode"],
                                                                  r["bias_ma"]) == (setting, fs, drive, mode, bias))


@pytest.mark.parametrize("fs, p1_rise, t_rise", [(48000, 37.5, 19.6), (44100, 34.8, 16.6)])
def test_the_eco_trade_quoted_in_results(fs, p1_rise, t_rise):
    """RESULTS.md: plain 4x raises the worst 8 V total over standard by 37.5 / 34.8 dB for P1 0.6 mA and 19.6 / 16.6 dB
    for T 0.5 mA with grid current (48 / 44.1 kHz); every 4x maximum is inside the 12AX7 eco budget, and T 0.5 mA with
    grid current at 4x is above the 12AX7 standard budget at both drives."""
    for (m, b), rise in ((("P1", 0.6), p1_rise), (("T", 0.5), t_rise)):
        assert round(_case_worst("4x", fs, 8.0, m, b) - _case_worst("standard", fs, 8.0, m, b), 1) == rise
    for d in (2.0, 8.0):
        assert _worst("4x", fs, d) <= gate("4x", fs, d)
        assert _case_worst("4x", fs, d, "T", 0.5) > gate("standard", fs, d)


def test_grid_current_t_is_internal_folding_at_8v_at_every_factor():
    """T 0.35-0.5 mA with grid current, 8 V: the worst point of every factor and rate is internal folding (internal
    share within 1 dB of the total), so no resampler change moves it; at 4x the same holds at 2 V for T 0.5 mA."""
    for fs in Q.RATES:
        for s in ("4x", "standard", "hq"):
            for b in (0.45, 0.5):
                rs = [r for r in Q.records() if (r["fs"], r["setting"], r["mode"], r["bias_ma"], r["drive"])
                      == (fs, s, "T", b, 8.0)]
                w = max(rs, key=lambda r: r["off"]["total_dbc"])
                assert w["off"]["internal_dbc"] >= w["off"]["total_dbc"] - 1.0, (fs, s, b)
        rs = [r for r in Q.records() if (r["fs"], r["setting"], r["mode"], r["bias_ma"], r["drive"])
              == (fs, "4x", "T", 0.5, 2.0)]
        w = max(rs, key=lambda r: r["off"]["total_dbc"])
        assert w["off"]["internal_dbc"] >= w["off"]["total_dbc"] - 1.0, fs


def test_every_maximum_is_on_the_full_grid():
    """Every reported row (every case, setting, rate, drive; 64x is the ground truth, a few points) covers GRID_HZ."""
    for r in AQ["summary"]:
        if r["setting"] != Q.GROUND_TRUTH:
            assert r["full_grid"] and r["tones"] == len(Q.GRID_HZ), r


def test_reproduce_plans_exactly_the_committed_points():
    """REPRODUCE (the `reproduce` command) yields exactly the committed records, scan, settle and model points."""
    plan = {}
    for which, args in Q.REPRODUCE:
        plan.setdefault(which, set()).update(Q.plan(args))
    got = dict(grid=AQ["records"], scan=AQ["band_edge_scan"], settle=AQ["settle_check"], model=AQ["model_check"])
    for which, recs in got.items():
        assert {Q.point_key(r) for r in recs} == plan[which], which


def test_every_record_carries_its_model():
    """Each record says which stage it is (grid hardness); each case is measured with its own."""
    own = {(m, b): g for m, b, g in Q.CASES}
    for r in Q.records():
        if (r["mode"], r["bias_ma"]) in own:
            assert Q.hardness(r) == own[r["mode"], r["bias_ma"]], Q.point_key(r)
    assert {g for _, _, g in Q.CASES_NO_GRID} == {0.0} and {g for _, _, g in Q.CASES_GRID} == {Q.HARDNESS}


def test_default_stage_equals_the_no_grid_sweeps():
    """model_check: the production default (grid current) at points of the no-grid cases, every factor and rate,
    against the no-grid record: within 1e-3 dB (largest 4.4e-4 dB, 4x, T 0.3 mA, 8 V), so the no-grid sweeps stand for
    the default stage there."""
    idx = {Q.point_key(dict(r, grid_hardness=0.0)): r for r in Q.records()}
    assert len(AQ["model_check"]) == 48
    for r in AQ["model_check"]:
        assert Q.hardness(r) == Q.HARDNESS
        ref = idx[Q.point_key(dict(r, grid_hardness=0.0))]
        assert abs(r["off"]["total_dbc"] - ref["off"]["total_dbc"]) <= 1e-3, Q.point_key(r)


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
    the shipped FIR at every factor (measured); k140 reads far inside it there. The image-rejection estimate is only
    an estimate: its fitted K does not hold at the 19831 Hz notch."""
    for s in ("4x", "standard", "hq"):
        assert _scan(44100, s, 2.0, 19987)["off"]["total_dbc"] > gate("standard", 44100, 2.0)
    k = next(r for r in RSJ["records"] if r["candidate"] == r["up"] == "k140" and r["fs"] == 44100
             and r["setting"] == "standard" and (r["mode"], r["bias_ma"], r["drive"], r["tone"]) == ("P1", 0.6, 2.0,
                                                                                                    19987))
    assert k["off"]["total_dbc"] <= gate("standard", 44100, 2.0) - AL.MARGIN_DB
    key = [r for r in Q.records() + AQ["band_edge_scan"] if (r["fs"], r["setting"], r["mode"], r["bias_ma"],
                                                              r["drive"]) == (44100, "standard", "P1", 0.6, 2.0)]
    e = RS.band_edge_estimate(key, 44100, 8)
    assert e["k"][19831] > e["k_max"] - 1e-9 and e["k"][19831] > 0.0      # the notch: K far outside the other tones
    rest = RS.band_edge_estimate(key, 44100, 8, k_min_db=0.0)
    assert rest["k_max"] - rest["k_min"] <= 5.0


def test_longer_settle_reads_the_same():
    """The deepest floors re-measured after 2.5 s instead of SETTLE_S: the total moves by less than 0.1 dB."""
    chk = AQ["settle_check"]
    assert len(chk) == 3
    idx = {Q.point_key(r): r for r in Q.records()}
    for r in chk:
        assert r["settle_s"] == 2.5
        ref = idx[Q.point_key(dict(r, settle_s=Q.SETTLE_S))]      # the same point after the standard settle
        assert abs(r["off"]["total_dbc"] - ref["off"]["total_dbc"]) < 0.1, Q.point_key(r)


def test_grid_current_records_reproduce_the_pr128_sweeps():
    """The grid-current T cases at standard 8x / 48 kHz, filter off, against #112's own sweeps of the default stage
    (dk6as6/aliasing_grid_T.json: the same stage and metric through dk6as6.aliasing.measure) at every common point."""
    ref = {(r["bias_ma"], r["drive"], r["tone"]): r["total_dbc"]
           for r in json.load(open(os.path.join(HERE, "aliasing_grid_T.json")))}
    mine = [r for r in Q.records() if Q.hardness(r) == Q.HARDNESS and r["setting"] == "standard" and r["fs"] == 48000]
    common = [r for r in mine if (r["bias_ma"], r["drive"], r["tone"]) in ref]
    assert len(common) >= 3 * 2 * 5
    for r in common:
        assert abs(r["off"]["total_dbc"] - ref[r["bias_ma"], r["drive"], r["tone"]]) <= 1e-5, Q.point_key(r)
