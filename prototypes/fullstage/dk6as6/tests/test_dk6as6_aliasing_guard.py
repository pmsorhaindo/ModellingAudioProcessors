"""The 6AS6 CI aliasing guard (#113 rework, dk6as6/aliasing_guard.py) and the inaudibility assessment it is drawn from
(dk6as6/inaudible.json): every offered quality passes the criterion at every supported mode and bias, both drives and
both rates, on the full 1 s grid; the guard's keys and tones are derive()'s; its baselines pass the criterion and its
comparison fails on a criterion failure, a ratchet regression and a missing point. No stage runs here (the CI jobs
`aliasing (6as6, <quality>, <rate> Hz)` run the guard).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_aliasing_guard.py
"""
import json
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.aliasing import CI_WINDOW_S, tone_bin
from dk6as6 import aliasing_guard as AG, inaudible as IN, inaudible_sweep as SW
from dk6as6.stage import QUALITY, SUPPORTED_MA, supported

DATA = json.load(open(SW.OUT_JSON))
CPP, PY = DATA["records_cpp"], DATA["records_python"]
QUALITIES = sorted(QUALITY, key=AG.os_of)


def grid(recs, fs, os_, mode, bias, drive, lpf="off"):
    return [r for r in recs if r["in_scope"] and (r["fs"], r["os"], r["lpf"], r["mode"], r["bias_ma"], r["drive"])
            == (fs, os_, lpf, mode, bias, drive)]


def supported_cases():
    out = [c for c in SW.CASES if supported(*c)]
    assert {m for m, _ in out} == set(SUPPORTED_MA)
    for m, (lo, hi) in SUPPORTED_MA.items():          # every envelope end is a measured case
        assert (m, lo) in out and (m, hi) in out, (m, lo, hi)
    return out


def test_the_method_is_the_criterion_as_decided():
    m = DATA["method"]
    assert (m["floor_dbfs"], m["under_threshold_db"], m["spl_fs_db"]) == (-100.0, 10.0, IN.SPL_FS_DB)
    assert m["fir"] == AG.DESIGN == "k140" and m["grid_hardness"] == AG.GRID_HARDNESS
    assert m["window_s"] == 1.0 and m["grid_hz"] == list(SW.GRID_HZ) and max(SW.GRID_HZ) < 20000
    assert m["metric"] == SW.METRIC == 2 and m["lpfs"] == list(SW.LPFS)
    assert all(r["metric"] == SW.METRIC for r in CPP + PY)


def test_committed_support_flags_are_the_stages_envelope():
    """PR #146 review round 2: every committed record's and summary's `supported` is stage.supported now (the flags are
    recomputed at collect time, never kept from when a point ran), and `in_scope` is the input below 20 kHz."""
    for r in CPP + PY:
        assert r["supported"] == supported(r["mode"], r["bias_ma"]), r
        assert r["in_scope"] == (r["tone"] < 20000), r
    for s in DATA["summary_cpp"] + DATA["summary_python"]:
        assert s["supported"] == supported(s["mode"], s["bias_ma"]) and s["worst"]["supported"] == s["supported"]
    assert any(not r["passed"] for r in CPP if not r["supported"])         # the cap's evidence is in the records


@pytest.mark.parametrize("quality", QUALITIES)
@pytest.mark.parametrize("fs", AG.RATES)
def test_every_offered_quality_is_inaudible_on_the_whole_supported_envelope(fs, quality):
    """The acceptance criterion itself, in scope (inputs below 20 kHz): every supported mode / bias, 2 and 8 V, every
    grid tone, every post-filter position, on the C++ screen, and every python confirmation at this quality and rate."""
    os_ = AG.os_of(quality)
    for mode, bias in supported_cases():
        for d in IN.DRIVES:
            for lp in SW.LPFS:
                rs = grid(CPP, fs, os_, mode, bias, d, lp)
                assert {r["tone"] for r in rs} == set(SW.GRID_HZ), (fs, quality, mode, bias, d, lp)
                bad = [(r["tone"], round(r["margin_db"], 2), r["worst_hz"]) for r in rs if not r["passed"]]
                assert not bad, (fs, quality, mode, bias, d, lp, bad)
                assert all(r["nonconverged"] == 0 for r in rs)
    py = [r for r in PY if r["fs"] == fs and r["os"] == os_ and r["in_scope"] and supported(r["mode"], r["bias_ma"])]
    assert py and all(r["passed"] for r in py), [(r["mode"], r["bias_ma"], r["drive"], r["tone"]) for r in py
                                                if not r["passed"]]


def test_python_confirms_the_screen():
    """Every python point reads the C++ screen's margin to PARITY_DB and finds the same worst product."""
    assert DATA["parity"]
    for p in DATA["parity"]:
        assert abs(p["diff_db"]) <= SW.PARITY_DB, p
        assert p["python_worst_hz"] == p["cpp_worst_hz"], p


@pytest.mark.parametrize("quality", QUALITIES)
@pytest.mark.parametrize("fs", AG.RATES)
def test_ci_keys_and_tones_are_derived_from_the_assessment(fs, quality):
    assert AG.CI[fs][quality] == AG.derive(CPP, fs, quality)
    keys = AG.CI[fs][quality]
    assert all(supported(m, b) and lp in SW.LPFS for m, b, lp, _ in keys)
    for d in IN.DRIVES:                                   # each mode at each drive, and each mode's top
        assert {m for m, _, _, dd in keys if dd == d} == set(SUPPORTED_MA)
        assert all(any((m, hi, lp, d) in keys for lp in SW.LPFS) for m, (_, hi) in SUPPORTED_MA.items())


@pytest.mark.parametrize("fs", AG.RATES)
def test_committed_baseline_covers_the_subset_and_passes(fs):
    base = AG.load_baseline(AG.baseline_path(fs))
    assert base["format"] == AG.FORMAT and base["tolerance_db"] == 1.5
    n = round(CI_WINDOW_S * fs)
    want = {AG.key(m, b, q, d, lp): {str(t) for t in ts} for q in QUALITIES
            for (m, b, lp, d), ts in AG.CI[fs][q].items()}
    assert {k: set(v) for k, v in base["values"].items()} == want
    for q in QUALITIES:
        assert base["method"][q] == json.loads(json.dumps(AG.method(fs, q)))
    for k, tones in base["values"].items():
        for t, v in tones.items():
            assert v["margin_db"] >= 0.0, (k, t, v)                  # the criterion, in the CI window
            assert v["f_hz"] == tone_bin(int(t), n, fs) * fs / n


def rec(key, tone, margin, passed=None):
    return dict(key=key, tone=tone, f_hz=float(tone), margin_db=margin,
                passed=margin >= 0 if passed is None else passed, nonconverged=0, worst_hz=3000.0, worst_dbfs=-130.0,
                worst_limit_dbfs=-119.0, worst_source="internal")


def test_compare_fails_on_the_criterion_the_ratchet_and_a_missing_point():
    base = dict(values={"T/0.35mA/standard/k140/8V": {"19637": dict(margin_db=10.0)},
                        "P1/0.6mA/standard/k140/8V": {"18037": dict(margin_db=40.0)}})
    k1, k2 = "T/0.35mA/standard/k140/8V", "P1/0.6mA/standard/k140/8V"
    ok = AG.compare([rec(k1, 19637, 9.0), rec(k2, 18037, 40.2)], base)
    assert not AG.failed(ok) and [r["status"] for r in ok] == ["ok", "ok"]
    rows = AG.compare([rec(k1, 19637, 8.0), rec(k2, 18037, 40.0)], base)            # 2 dB worse than its baseline
    assert [r["status"] for r in AG.failed(rows)] == ["FAIL ratchet"]
    rows = AG.compare([rec(k1, 19637, -0.1), rec(k2, 18037, 40.0)], base)           # over its limit
    assert [r["status"] for r in AG.failed(rows)] == ["FAIL criterion"]
    rows = AG.compare([rec(k1, 19637, 10.0)], base)
    assert [r["status"] for r in AG.failed(rows)] == ["FAIL not measured"]
    rows = AG.compare([rec(k1, 19637, 13.0), rec(k2, 18037, 40.0)], base)
    assert not AG.failed(rows) and rows[0]["status"].startswith("ok (better")
    rows = AG.compare([dict(rec(k1, 19637, 10.0), nonconverged=3), rec(k2, 18037, 40.0)], base)
    assert [r["status"] for r in AG.failed(rows)] == ["FAIL Newton"]


def test_inputs_above_20khz_are_measured_and_kept_out_of_the_guarantee():
    """Inputs above 20 kHz, below Nyquist (PR #146 review round 2), are in the records for every rate, offered factor,
    supported case, drive and post-filter position, marked out of scope, never in a guard key, and some fail: the
    k140 interpolator's image of such a tone (RESULTS.md). They are reported, not guaranteed."""
    hi = [r for r in CPP if not r["in_scope"]]
    for fs, tones in SW.ABOVE_20K_HZ.items():
        for q in QUALITIES:
            for mode, bias in supported_cases():
                for d in IN.DRIVES:
                    for lp in SW.LPFS:
                        got = {r["tone"] for r in hi if (r["fs"], r["os"], r["mode"], r["bias_ma"], r["drive"],
                                                          r["lpf"]) == (fs, AG.os_of(q), mode, bias, d, lp)}
                        assert got == set(tones), (fs, q, mode, bias, d, lp)
        assert all(max(t) < 20000 for keys in AG.CI[fs].values() for t in keys.values())
    assert any(not r["passed"] for r in hi if r["supported"])

