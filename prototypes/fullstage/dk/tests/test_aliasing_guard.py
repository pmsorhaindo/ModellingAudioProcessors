"""Per-tone aliasing limits/ratchet on synthetic numbers, baseline format I/O and committed coverage."""
import json
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import aliasing_guard as G

TOL = G.TOLERANCE_DB
KEY = "s/fir/8V"
LIM = {KEY: -50.0}
TONES = {KEY: (19037,)}


def point(value, tone=19037):
    return dict(total_dbc=value, f_hz=float(tone))


def one(measured, baseline=-60.0, limits=LIM):
    m = {KEY: dict(tones={"19037": point(measured)})}
    b = {} if baseline is None else {KEY: dict(tones={"19037": point(baseline)})}
    (row,) = G.compare(m, b, limits, TONES)
    return row


@pytest.mark.parametrize("measured,status", [
    (-60.0, "ok"),
    (-60.0 + TOL, "ok"),                    # exactly at the tolerance: still ok
    (-60.0 + TOL + 0.01, "FAIL regression"),
    (-60.0 - TOL, "ok"),
    (-60.0 - TOL - 0.01, "improved"),
    (-50.0, "FAIL regression"),             # at the limit, but 10 dB worse than baseline
    (-49.99, "FAIL limit"),
])
def test_ratchet_and_limit(measured, status):
    r = one(measured)
    assert r["status"] == status
    assert r["delta_db"] == pytest.approx(measured + 60.0)
    assert r["margin_db"] == pytest.approx(-50.0 - measured)


def test_regression_fails_even_well_under_the_limit():
    r = one(-80.0, baseline=-90.0)
    assert r["status"] == "FAIL regression" and r["margin_db"] == pytest.approx(30.0)


def test_over_limit_fails_even_if_baseline_was_worse():
    assert one(-45.0, baseline=-44.0)["status"] == "FAIL limit"


@pytest.mark.parametrize("fs,k,other", [
    (44100, "hq/fir/2V", 19037),
    (44100, "standard/fir/2V", 19037),
    (48000, "hq/fir/8V", 10037),
])
def test_non_worst_tone_regression_fails_without_changing_the_setting_worst(fs, k, other):
    """The old reduction to max(setting) hid this >1.5 dB regression; these are synthetic levels."""
    tones = G.tones_by_key(fs)[k]
    worst = next(t for t in tones if t != other)
    limit = G.limits_by_key(fs)[k]
    baseline = {k: dict(tones={str(worst): point(limit-4, worst), str(other): point(limit-9, other)})}
    measured = {k: dict(tones={str(worst): point(limit-4, worst),
                              str(other): point(limit-9+TOL+0.01, other)})}
    assert max(p["total_dbc"] for p in baseline[k]["tones"].values()) == max(
        p["total_dbc"] for p in measured[k]["tones"].values())
    rows = G.compare(measured, baseline, {k: limit}, {k: tones})
    (bad,) = G.failed(rows)
    assert bad["tone"] == other and bad["status"] == "FAIL regression"
    assert bad["delta_db"] == pytest.approx(TOL+0.01) and bad["margin_db"] > 0
    assert next(r for r in rows if r["tone"] == worst)["status"] == "ok"


def test_missing_tone_baseline_and_measurement_fail_individually():
    tones = {KEY: (10037, 19037)}
    m = {KEY: dict(tones={"10037": point(-70.0, 10037), "19037": point(-60.0)})}
    b = {KEY: dict(tones={"19037": point(-60.0)})}
    rows = G.compare(m, b, LIM, tones)
    assert [r["status"] for r in rows] == ["FAIL no baseline", "ok"]
    assert rows[0]["baseline_dbc"] is None
    rows = G.compare({}, {}, LIM, tones)  # even if BOTH sources omit a configured tone, it must fail
    assert [r["status"] for r in rows] == ["FAIL not measured", "FAIL not measured"]
    assert one(-60.0, baseline=None)["status"] == "FAIL no baseline"


def test_changed_coherent_frequency_fails():
    m = {KEY: dict(tones={"19037": point(-60.0, 19040)})}
    b = {KEY: dict(tones={"19037": point(-60.0, 19030)})}
    (r,) = G.compare(m, b, LIM, TONES)
    assert r["status"] == "FAIL frequency"


def test_values_keep_every_tone_and_reject_duplicates():
    def record(t, value):
        return dict(setting="s", filt="fir", drive=8, tone=t, total_dbc=value, f_hz=float(t))
    records = [record(10037, -70.0), record(19037, -60.0)]
    assert G.values_by_key(records) == {KEY: dict(tones={"10037": point(-70.0, 10037),
                                                       "19037": point(-60.0)})}
    with pytest.raises(ValueError, match="duplicate measurement"):
        G.values_by_key(records + records[:1])


def test_failed_and_markdown():
    rows = [one(-60.0), one(-55.0), one(-70.0)]
    assert [r["status"] for r in rows] == ["ok", "FAIL regression", "improved"]
    assert G.failed(rows) == [rows[1]]
    md = G.markdown(rows)
    assert md.startswith("## Aliasing guard: FAIL")
    assert md.count("\n| s/fir/8V |") == 3 and "**FAIL regression**" in md and "--update-baseline" in md
    assert "| -55.00 | -60.00 | +5.00 | -50.0 | 5.00 |" in md
    assert "requested tone Hz" in md and "s/fir/8V at 19037 Hz" in md
    assert G.markdown([one(-60.0)]).startswith("## Aliasing guard: pass")
    assert "dk.aliasing_guard --fs 44100 --update-baseline" in G.markdown([one(-70.0)], fs=44100)


def test_update_baseline_requires_a_reason(capsys):
    with pytest.raises(SystemExit):
        G.main(["--update-baseline"])
    assert "--reason" in capsys.readouterr().err


def test_baseline_roundtrip_preserves_unrounded_per_tone_values(tmp_path):
    path = tmp_path / "baseline.json"
    values = {KEY: dict(tones={"19037": point(-60.12345678901234, 19040)})}
    G.write_baseline(path, values, "roundtrip fixture", fs=44100)
    base = G.load_baseline(path)
    assert base["values"] == values
    assert base["format_version"] == G.FORMAT_VERSION and base["tolerance_db"] == TOL
    assert base["method"] == json.loads(json.dumps(G.method(44100)))


@pytest.mark.parametrize("version", [None, 1, 999])
def test_legacy_or_unknown_format_is_rejected_before_measurement(tmp_path, monkeypatch, capsys, version):
    path = tmp_path / "old.json"
    old = dict(values={KEY: dict(total_dbc=-60.0, tone=19037, tones=[19037])})
    if version is not None:
        old["format_version"] = version
    path.write_text(json.dumps(old))
    monkeypatch.setattr(G, "run_subset", lambda *args: pytest.fail("must reject before DSP"))
    with pytest.raises(SystemExit):
        G.main(["--baseline", str(path)])
    error = capsys.readouterr().err
    assert "unsupported baseline format" in error and "--baseline NEW_FILE" in error


def test_old_value_shape_cannot_bypass_version_check_or_comparison(tmp_path):
    old_values = {KEY: dict(total_dbc=-60.0, tone=19037, tones=[19037])}
    path = tmp_path / "old.json"
    path.write_text(json.dumps(dict(format_version=G.FORMAT_VERSION, tolerance_db=TOL, values=old_values)))
    with pytest.raises(ValueError, match="expected per-tone"):
        G.load_baseline(path)
    with pytest.raises(ValueError, match="expected per-tone"):
        G.compare({}, old_values, LIM, TONES)


def test_baseline_cannot_claim_a_different_tolerance(tmp_path):
    path = tmp_path / "baseline.json"
    G.write_baseline(path, {KEY: dict(tones={"19037": point(-60.0)})}, "unit fixture")
    base = json.loads(path.read_text())
    base["tolerance_db"] = TOL + 1
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="tolerance_db must remain 1.5"):
        G.load_baseline(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), None, True, "-60.0"])
def test_invalid_point_values_cannot_silently_pass(value):
    with pytest.raises(ValueError, match="finite total_dbc"):
        G.compare({KEY: dict(tones={"19037": point(value)})}, {}, LIM, TONES)


@pytest.mark.parametrize("missing", [False, True])
def test_baseline_update_refuses_an_over_limit_or_missing_point(tmp_path, monkeypatch, missing):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    path = tmp_path / "new.json"
    k = "hq/fir/2V"
    records = [] if missing else [dict(setting="hq", filt="fir", drive=2, tone=19037,
                                      f_hz=19040.0, total_dbc=-100.0)]
    monkeypatch.setattr(G, "run_subset", lambda *args: records)
    monkeypatch.setattr(G, "limits_by_key", lambda fs: {k: -118.2})
    monkeypatch.setattr(G, "tones_by_key", lambda fs: {k: (19037,)})
    with pytest.raises(SystemExit, match="not updating the baseline"):
        G.main(["--baseline", str(path), "--update-baseline", "--reason", "unit fixture"])
    assert not path.exists()


@pytest.mark.parametrize("fs", [48000, 44100])
def test_committed_baseline_covers_each_configured_tone(fs):
    base = G.load_baseline(G.baseline_path(fs))
    assert base["reason"].strip() and base["tolerance_db"] == TOL
    assert base["method"] == json.loads(json.dumps(G.method(fs)))
    assert set(base["values"]) == set(G.limits_by_key(fs))
    for k, tones in G.tones_by_key(fs).items():
        v = base["values"][k]["tones"]
        assert set(v) == {str(t) for t in tones}
        assert all(p["total_dbc"] <= G.limits_by_key(fs)[k] for p in v.values())
    rows = G.compare(base["values"], base["values"], G.limits_by_key(fs), G.tones_by_key(fs))
    assert len(rows) == sum(map(len, G.tones_by_key(fs).values())) and not G.failed(rows)


def test_44100_rejects_other_baseline_rate(capsys):
    with pytest.raises(SystemExit):
        G.main(["--fs", "44100", "--baseline", G.BASELINE_JSON])
    assert "host rate differs" in capsys.readouterr().err
