"""The 6AS6 CI aliasing guard (#113, dk6as6/aliasing_guard.py): its limits and tones come from the committed full
sweeps, its baselines are the #98 per-tone format, and its comparison is dk.aliasing_guard's. No stage runs here (the
CI job `aliasing (6as6)` runs the guard itself).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_aliasing_guard.py
"""
import json
import math
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import aliasing_guard as G
from dk.aliasing import MARGIN_DB, CI_WINDOW_S, tone_bin
from dk6as6 import aliasing_guard as AG, aliasing_quality as Q
from dk6as6.stage import supported

GRID = json.load(open(AG.GRID_JSON))          # the k140 full grid at the guard's keys (cpp/aliasing_6as6.py guardgrid)


def full_sweep(fs, mode, bias, setting, drive):
    return [dict(r, off=dict(total_dbc=r["total_dbc"])) for r in GRID["records"]
            if (r["fs"], r["mode"], r["bias_ma"], r["quality"], r["drive"]) == (fs, mode, bias, setting, drive)]


def test_the_grid_is_the_guards_design_and_keys():
    """The grid was measured with the guard's design (k140), grid current on, every key at every grid tone (#113's grid
    and band-edge scan, plus 19957 / 19977 / 19997 Hz)."""
    m = GRID["method"]
    assert m["fir"] == AG.FIR and m["grid_hardness"] == AG.GRID_HARDNESS and m["lpf"] == "off"
    assert set(m["tones"]) >= set(Q.GRID_HZ) | set(Q.SCAN_HZ) | {19957, 19977, 19997}
    keys = {(fs, *k) for fs in AG.RATES for k in AG.CI[fs]}
    assert {tuple(k) for k in m["keys"]} == keys
    for k in keys:
        assert {r["tone"] for r in full_sweep(*k)} == set(m["tones"]), k
    assert all(r["fir"] == AG.FIR and r["nonconverged"] == 0 for r in GRID["records"] + GRID["ci_window"])


@pytest.mark.parametrize("fs", AG.RATES)
def test_committed_baseline_equals_the_cpp_core_in_the_ci_window(fs):
    """Python / C++ parity at the guard's own points: the committed baselines (this stage, CI window) against the C++
    core at the same keys, tones and window (the grid's ci_window), to PARITY_DB."""
    base = G.load_baseline(AG.baseline_path(fs))["values"]
    n = 0
    for r in GRID["ci_window"]:
        if r["fs"] != fs:
            continue
        b = base[AG.key(r["mode"], r["bias_ma"], r["quality"], r["drive"])]["tones"][str(r["tone"])]
        assert b["f_hz"] == r["f_hz"] and abs(b["total_dbc"] - r["total_dbc"]) <= AG.PARITY_DB, (r, b)
        n += 1
    assert n == sum(len(t) for t in AG.tones_by_key(fs).values())


def test_44k1_band_edge_is_guarded():
    """#104 / #134: the 44.1 kHz band edge where the shipped FIR fails the gate (P1 0.6 mA, 2 V, >= 19957 Hz) is a
    guarded key at both qualities, with CI tones there, and its limit is inside the quality gate (the shipped 12AX7
    stage's 44.1 kHz budget for that quality) with k140."""
    from dk.aliasing import limits_for_rate
    for setting in ("standard", "hq"):
        lim, tones = AG.CI[44100][("P1", 0.6, setting, 2.0)]
        gate = limits_for_rate(44100)[(setting, "fir")][2.0]
        assert any(t >= 19957 for t in tones) and lim <= gate, (setting, lim, tones, gate)


@pytest.mark.parametrize("fs", AG.RATES)
def test_every_limit_is_the_full_sweeps_worst_plus_margin(fs):
    """Each key's limit is derive()'s: the worst over its k140 grid and its CI tones' CI-window readings, + 3 dB."""
    assert AG.CI[fs]
    for (m, b, s, d), (lim, tones) in AG.CI[fs].items():
        assert supported(m, b) and s in AG.QUALITY
        rs = full_sweep(fs, m, b, s, d)
        assert rs, (fs, m, b, s, d)
        ci = [(r["tone"], r["total_dbc"]) for r in GRID["ci_window"]
              if (r["fs"], r["mode"], r["bias_ma"], r["quality"], r["drive"]) == (fs, m, b, s, d)]
        w = max(r["off"]["total_dbc"] for r in rs)
        assert (lim, tones) == AG.derive([(r["tone"], r["off"]["total_dbc"]) for r in rs], ci), (fs, m, b, s, d)
        assert lim >= math.ceil((w + MARGIN_DB) * 10) / 10, (fs, m, b, s, d, w, lim)


@pytest.mark.parametrize("fs", AG.RATES)
def test_ci_tones_are_the_highest_within_margin_of_the_worst(fs):
    for (m, b, s, d), (_, tones) in AG.CI[fs].items():
        rs = full_sweep(fs, m, b, s, d)
        w = max(r["off"]["total_dbc"] for r in rs)
        near = sorted((r for r in rs if r["off"]["total_dbc"] >= w - MARGIN_DB), key=lambda r: -r["off"]["total_dbc"])
        assert set(tones) == {r["tone"] for r in near[:AG.MAX_TONES]}, (fs, m, b, s, d, tones)


@pytest.mark.parametrize("fs", AG.RATES)
def test_committed_baseline_covers_the_subset_in_the_v2_format(fs):
    base = G.load_baseline(AG.baseline_path(fs))
    assert base["format_version"] == 2 and base["tolerance_db"] == G.TOLERANCE_DB == 1.5
    assert base["method"] == json.loads(json.dumps(AG.method(fs)))
    assert set(base["values"]) == set(AG.limits_by_key(fs))
    n = round(CI_WINDOW_S * fs)
    for k, tones in AG.tones_by_key(fs).items():
        got = base["values"][k]["tones"]
        assert set(got) == {str(t) for t in tones}
        for t in tones:
            assert got[str(t)]["f_hz"] == tone_bin(t, n, fs) * fs / n
            assert got[str(t)]["total_dbc"] <= AG.limits_by_key(fs)[k]


@pytest.mark.parametrize("fs", AG.RATES)
def test_committed_baseline_passes_its_own_comparison(fs):
    base = G.load_baseline(AG.baseline_path(fs))
    rows = G.compare(base["values"], base["values"], AG.limits_by_key(fs), AG.tones_by_key(fs))
    assert rows and not G.failed(rows) and all(r["status"] == "ok" for r in rows)


def _regress_best_tone(fs, k, tones):
    base = G.load_baseline(AG.baseline_path(fs))["values"]
    lim = AG.limits_by_key(fs)
    vals = {t: base[k]["tones"][str(t)]["total_dbc"] for t in tones}
    t = min(vals, key=vals.get)
    meas = json.loads(json.dumps(base))
    meas[k]["tones"][str(t)]["total_dbc"] += G.TOLERANCE_DB + 0.01
    bad = G.failed(G.compare(meas, base, lim, AG.tones_by_key(fs)))
    return t, vals, {u: meas[k]["tones"][str(u)]["total_dbc"] for u in tones}, lim[k], bad


@pytest.mark.parametrize("fs", AG.RATES)
def test_a_regressed_non_worst_tone_fails_under_the_real_limit(fs):
    """Every key with more than one tone: its best (non-worst) tone degraded by 1.51 dB stays under the key's real
    limit, yet fails as a regression on that tone alone (a per-tone ratchet, not a per-key maximum)."""
    multi = {k: t for k, t in AG.tones_by_key(fs).items() if len(t) > 1}
    assert multi
    for k, tones in multi.items():
        t, _, after, lim, bad = _regress_best_tone(fs, k, tones)
        assert after[t] < lim, (k, t)
        assert [(r["key"], r["tone"], r["status"]) for r in bad] == [(k, t, "FAIL regression")]


def test_a_regression_that_leaves_the_key_maximum_unchanged_still_fails():
    """A key whose tones spread by more than the tolerance: degrading its best tone by 1.51 dB leaves the key's maximum
    unchanged and still fails, so a per-key maximum ratchet would miss it. With k140 every committed key is flat across
    its CI tones (<= 1.04 dB), so the spread is made on a copy of a real baseline: one tone moved 3 dB down, both as the
    baseline and as the measurement before the degradation (a property of the comparison, not of the data)."""
    fs = AG.RATES[0]
    k, tones = next((k, t) for k, t in AG.tones_by_key(fs).items() if len(t) > 1)
    base = json.loads(json.dumps(G.load_baseline(AG.baseline_path(fs))["values"]))
    lo = str(tones[-1])
    base[k]["tones"][lo]["total_dbc"] -= 3.0
    meas = json.loads(json.dumps(base))
    meas[k]["tones"][lo]["total_dbc"] += G.TOLERANCE_DB + 0.01
    before = max(v["total_dbc"] for v in base[k]["tones"].values())
    after = max(v["total_dbc"] for v in meas[k]["tones"].values())
    bad = G.failed(G.compare(meas, base, AG.limits_by_key(fs), AG.tones_by_key(fs)))
    assert after == before and [(r["key"], str(r["tone"]), r["status"]) for r in bad] == [(k, lo, "FAIL regression")]


def test_values_by_key_uses_the_6as6_keys():
    r = dict(mode="T", bias_ma=0.3, setting="standard", drive=8.0, tone=19037, total_dbc=-120.0, f_hz=19040.0)
    assert AG.values_by_key([r]) == {"T/0.3mA/standard/k140/8V": {"tones": {"19037": {"total_dbc": -120.0,
                                                                                         "f_hz": 19040.0}}}}
    with pytest.raises(ValueError):
        AG.values_by_key([r, r])
