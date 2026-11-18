"""CI aliasing guard (#35): the limit / ratchet comparison on synthetic numbers (no DSP), and the committed baseline's
coverage of every setting, filter and drive."""
import json
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import aliasing as A
from dk import aliasing_guard as G

TOL = G.TOLERANCE_DB
LIM = {"s/fir/8V": -50.0}


def one(measured, baseline=-60.0, tones=(19037,), base_tones=None, limits=LIM):
    m = {"s/fir/8V": dict(total_dbc=measured, tone=tones[-1], tones=list(tones))}
    b = {} if baseline is None else {"s/fir/8V": dict(total_dbc=baseline, tones=list(base_tones or tones))}
    (row,) = G.compare(m, b, limits)
    return row


@pytest.mark.parametrize("measured,status", [
    (-60.0, "ok"),
    (-60.0 + TOL, "ok"),                    # exactly at the tolerance: still ok
    (-60.0 + TOL + 0.01, "FAIL regression"),
    (-60.0 - TOL, "ok"),
    (-60.0 - TOL - 0.01, "improved"),       # passes, suggests a baseline update
    (-50.0, "FAIL regression"),             # at the limit, but 10 dB worse than baseline
    (-49.99, "FAIL limit"),                 # the limit takes precedence over the ratchet
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


def test_missing_or_stale_baseline_and_missing_measurement_fail():
    assert one(-60.0, baseline=None)["status"] == "FAIL no baseline"
    r = one(-60.0, tones=(10037, 19037), base_tones=(19037,))       # CI tones changed since the baseline
    assert r["status"] == "FAIL no baseline" and r["baseline_dbc"] is None
    (r,) = G.compare({}, {}, LIM)
    assert r["status"] == "FAIL not measured"


def test_failed_and_markdown():
    rows = [one(-60.0), one(-55.0), one(-70.0)]
    assert [r["status"] for r in rows] == ["ok", "FAIL regression", "improved"]
    assert G.failed(rows) == [rows[1]]
    md = G.markdown(rows)
    assert md.startswith("## Aliasing guard: FAIL")
    assert md.count("\n| s/fir/8V |") == 3 and "**FAIL regression**" in md and "--update-baseline" in md
    assert "| -55.00 | -60.00 | +5.00 | -50.0 | 5.00 |" in md
    assert G.markdown([one(-60.0)]).startswith("## Aliasing guard: pass")
    assert "dk.aliasing_guard --fs 44100 --update-baseline" in G.markdown([one(-70.0)], fs=44100)


def test_update_baseline_requires_a_reason(capsys):
    with pytest.raises(SystemExit):
        G.main(["--update-baseline"])
    assert "--reason" in capsys.readouterr().err


def test_committed_baseline_covers_every_limit_on_the_ci_tones():
    base = json.load(open(G.BASELINE_JSON))
    assert base["reason"].strip() and base["tolerance_db"] == TOL
    assert set(base["values"]) == set(G.limits_by_key()) == {G.key(q, f, d) for q, f in A.LIMIT_DBC for d in A.DRIVES}
    for (q, f), tones in A.CI_TONES.items():
        for d, t in tones.items():
            v = base["values"][G.key(q, f, d)]
            assert v["tones"] == sorted(t) and v["total_dbc"] <= A.LIMIT_DBC[(q, f)][d]


def test_44100_baseline_covers_fir_limits_and_rejects_other_rate(capsys):
    base = json.load(open(G.BASELINE_44100_JSON))
    assert base["reason"].strip() and base["tolerance_db"] == TOL
    assert base["method"] == json.loads(json.dumps(G.method(44100)))  # JSON represents the band tuple as a list
    assert set(base["values"]) == set(G.limits_by_key(44100))
    for (q, f), tones in A.CI_TONES_44100.items():
        for d, t in tones.items():
            v = base["values"][G.key(q, f, d)]
            assert v["tones"] == sorted(t) and v["total_dbc"] <= A.LIMIT_DBC_44100[(q, f)][d]
    with pytest.raises(SystemExit):
        G.main(["--fs", "44100", "--baseline", G.BASELINE_JSON])
    assert "host rate differs" in capsys.readouterr().err
