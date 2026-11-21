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

SWEEP = Q.records() + json.load(open(Q.OUT_JSON))["band_edge_scan"]   # the grid, and the band-edge scan


def full_sweep(fs, mode, bias, setting, drive):
    return [r for r in SWEEP if (r["fs"], r["mode"], r["bias_ma"], r["setting"], r["drive"])
            == (fs, mode, bias, setting, drive)]


@pytest.mark.parametrize("fs", AG.RATES)
def test_every_limit_is_the_full_sweeps_worst_plus_margin(fs):
    assert AG.CI[fs]
    for (m, b, s, d), (lim, tones) in AG.CI[fs].items():
        assert supported(m, b) and s in AG.QUALITY
        rs = full_sweep(fs, m, b, s, d)
        assert rs, (fs, m, b, s, d)
        w = max(r["off"]["total_dbc"] for r in rs)
        assert lim == math.ceil((w + MARGIN_DB) * 10) / 10, (fs, m, b, s, d, w, lim)


@pytest.mark.parametrize("fs", AG.RATES)
def test_ci_tones_are_the_worst_and_every_tone_within_margin_of_it(fs):
    for (m, b, s, d), (_, tones) in AG.CI[fs].items():
        rs = full_sweep(fs, m, b, s, d)
        w = max(r["off"]["total_dbc"] for r in rs)
        near = {r["tone"] for r in rs if r["off"]["total_dbc"] >= w - MARGIN_DB}
        assert set(tones) == near, (fs, m, b, s, d, tones, near)


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


@pytest.mark.parametrize("fs", AG.RATES)
def test_a_regressed_non_worst_tone_fails_under_the_real_limit(fs):
    """Every key whose baseline tones spread by more than the tolerance (there is at least one per rate): its best
    (non-worst) tone degraded by 1.51 dB stays under the key's real limit and leaves the key's maximum unchanged, yet
    fails as a regression (a per-tone ratchet, not a per-key maximum)."""
    base = G.load_baseline(AG.baseline_path(fs))["values"]
    lim = AG.limits_by_key(fs)
    def spread(k, tones):
        v = [base[k]["tones"][str(t)]["total_dbc"] for t in tones]
        return max(v) - min(v)
    multi = {k: t for k, t in AG.tones_by_key(fs).items() if len(t) > 1 and spread(k, t) > G.TOLERANCE_DB + 0.01}
    assert multi
    for k, tones in multi.items():
        vals = {t: base[k]["tones"][str(t)]["total_dbc"] for t in tones}
        t = min(vals, key=vals.get)
        meas = json.loads(json.dumps(base))
        meas[k]["tones"][str(t)]["total_dbc"] += G.TOLERANCE_DB + 0.01
        after = {u: meas[k]["tones"][str(u)]["total_dbc"] for u in tones}
        assert max(after.values()) == max(vals.values()) and after[t] < lim[k], (k, t, after)
        bad = G.failed(G.compare(meas, base, lim, AG.tones_by_key(fs)))
        assert [(r["key"], r["tone"], r["status"]) for r in bad] == [(k, t, "FAIL regression")]


def test_values_by_key_uses_the_6as6_keys():
    r = dict(mode="T", bias_ma=0.3, setting="standard", drive=8.0, tone=19037, total_dbc=-120.0, f_hz=19040.0)
    assert AG.values_by_key([r]) == {"T/0.3mA/standard/fir/8V": {"tones": {"19037": {"total_dbc": -120.0,
                                                                                         "f_hz": 19040.0}}}}
    with pytest.raises(ValueError):
        AG.values_by_key([r, r])
