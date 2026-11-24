"""The inaudibility criterion (dk6as6/inaudible.py, #113 rework): the threshold curve, the per-product limit, and the
per-product reading on synthetic signals whose alias lines are known exactly. No stage runs here.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_inaudible.py
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.aliasing import tone_bin
from dk6as6 import inaudible as IN

FS, N = 48000, 48000


def test_terhardt_curve_at_known_points():
    """Terhardt's formula evaluated by hand (module docstring): 1 kHz 3.64 - 6.5 e^-3.174 + 1e-3 = 3.369 dB SPL; its
    minimum near 3.3 kHz (3.64 * 3.3^-0.8 - 6.5 + 1e-3 * 3.3^4 = -4.98); 10 kHz 0.578 + ~0 + 10 = 10.58."""
    assert IN.threshold_db_spl(1000.0) == pytest.approx(3.64 - 6.5 * np.exp(-0.6 * 2.3 ** 2) + 1e-3, abs=1e-12)
    assert IN.threshold_db_spl(3300.0) == pytest.approx(3.64 * 3.3 ** -0.8 - 6.5 + 1e-3 * 3.3 ** 4, abs=1e-12)
    f = np.linspace(20, 20000, 199801)
    t = IN.threshold_db_spl(f)
    assert 3200 < f[np.argmin(t)] < 3500 and -5.1 < t.min() < -4.9
    assert IN.threshold_db_spl(10000.0) == pytest.approx(10.58, abs=0.01)


def test_limit_is_the_tighter_of_the_floor_and_the_threshold_term():
    f = np.geomspace(20, 20000, 4000)
    lim = IN.limit_dbfs(f)
    assert np.all(lim <= IN.FLOOR_DBFS)
    assert np.allclose(lim, np.minimum(-100.0, IN.threshold_db_spl(f) - 10.0 - 105.0))
    assert lim.min() == pytest.approx(-4.98 - 115.0, abs=0.05)          # about -120 dBFS near 3.3 kHz
    # the threshold term binds only between ~170 Hz and ~10.97 kHz; outside, the -100 dBFS floor (module docstring)
    mid = f[lim < IN.FLOOR_DBFS]
    assert mid.min() == pytest.approx(170, rel=0.01) and mid.max() == pytest.approx(10966, rel=0.01)
    assert np.all(lim[(f < mid.min()) | (f > mid.max())] == IN.FLOOR_DBFS)
    # a quieter calibration only loosens the threshold term
    assert np.all(IN.limit_dbfs(f, spl_fs_db=90.0) >= lim)


def lines(tone_hz, alias):
    """A full-scale-peak tone on a coherent bin plus exact alias lines {Hz: dBFS} on their own bins."""
    m = tone_bin(tone_hz, N, FS)
    t = np.arange(N) / FS
    y = np.sin(2 * np.pi * m * FS / N * t)
    for f, db in alias.items():
        y += 10 ** (db / 20) * np.sin(2 * np.pi * f * t + 0.3)
    return m, y / np.max(np.abs(y))


def test_reads_each_alias_line_at_its_level_in_dbfs():
    m, y = lines(19037, {3301: -125.0, 15003: -98.0, 811: -140.0})
    f, lv, fund, peak = IN.products(y, m, FS)
    got = {int(round(ff)): v for ff, v in zip(f, lv) if v > -200}
    for hz, db in {3301: -125.0, 15003: -98.0, 811: -140.0}.items():
        assert got[hz] == pytest.approx(db, abs=1e-3)
    assert fund == pytest.approx(0.0, abs=1e-3) and peak == pytest.approx(1.0)


def test_pass_and_fail_on_each_term():
    # -125 dBFS at 3.3 kHz passes (limit -119.98); -98 dBFS at 15 kHz fails the -100 dBFS floor by 2 dB
    m, y = lines(19037, {3301: -125.0, 15003: -98.0})
    a = IN.assess(y, m, FS)
    assert not a["passed"] and a["n_failing"] == 1 and a["worst_hz"] == 15003.0
    assert a["margin_db"] == pytest.approx(-2.0, abs=1e-3) and a["floor_margin_db"] == pytest.approx(-2.0, abs=1e-3)
    # -112 dBFS at 3.3 kHz is under -100 dBFS but only ~3 dB under the threshold: fails the threshold term by 8 dB
    m, y = lines(19037, {3301: -112.0})
    a = IN.assess(y, m, FS)
    assert not a["passed"] and a["worst_hz"] == 3301.0 and a["floor_margin_db"] > 0
    assert a["margin_db"] == pytest.approx(float(IN.limit_dbfs(3301.0)) + 112.0, abs=1e-3)
    assert a["spl_headroom_db"] == pytest.approx(a["margin_db"], abs=1e-9)
    m, y = lines(19037, {3301: -125.0, 15003: -101.0})
    assert IN.assess(y, m, FS)["passed"]


def test_harmonics_are_not_products():
    m = tone_bin(1037, N, FS)
    t = np.arange(N)
    y = sum(10 ** (-20 * k / 20) * np.sin(2 * np.pi * k * m * t / N) for k in range(1, 15))
    a = IN.assess(y, m, FS)
    assert a["passed"] and a["loudest_dbfs"] < -250


def test_nan_inf_audio_and_silence_are_refused():
    m = tone_bin(1037, N, FS)
    for y in (np.zeros(N), np.full(N, np.nan), np.r_[np.inf, np.zeros(N - 1)]):
        with pytest.raises(ValueError):
            IN.assess(y, m, FS)


def test_a_folded_line_next_to_a_harmonic_is_measured():
    """PR #146 review round 2: at 44.1 kHz in the 0.1 s CI window the 19987 Hz tone sits on 19990 Hz and its 288th
    harmonic folds to 19980 Hz, one bin from the fundamental. The earlier +-3-bin harmonic mask hid that line; a
    -95 dBFS line there must now fail the -100 dBFS floor, and lines one, two and three bins from any harmonic read at
    their own level."""
    fs, n = 44100, 4410
    m = tone_bin(19987, n, fs)
    assert m * fs / n == 19990.0
    k = 288
    r = (k * m) % n
    fold = min(r, n - r)
    assert fold == m - 1 and fold * fs / n == 19980.0
    t = np.arange(n)
    for db, passed in ((-95.0, False), (-125.0, True)):
        y = np.sin(2 * np.pi * m * t / n) + 10 ** (db / 20) * np.sin(2 * np.pi * fold * t / n + 0.7)
        a = IN.assess(y, m, fs)
        assert a["passed"] is passed and a["worst_hz"] == 19980.0, a
        assert a["worst_dbfs"] == pytest.approx(db - 20 * np.log10(np.max(np.abs(y - y.mean()))), abs=1e-6)
    m = tone_bin(1037, N, FS)
    for d in (1, 2, 3):
        for h in (m, 5 * m):
            b = h + d
            y = np.sin(2 * np.pi * m * np.arange(N) / N) + 0.1 * np.sin(2 * np.pi * 5 * m * np.arange(N) / N)
            y = y + 10 ** (-130 / 20) * np.sin(2 * np.pi * b * np.arange(N) / N + 1.1)
            f, lv, _, peak = IN.products(y, m, FS)
            got = lv[np.argmin(np.abs(f - b * FS / N))]
            assert got == pytest.approx(-130 - 20 * np.log10(peak), abs=1e-6), (d, h)


def test_the_hidden_order_is_reported():
    """Only an alias folded exactly onto a harmonic's own bin is unmeasurable; assess reports the lowest order where
    that can happen, collision_order(m, n, 0) - n // (2 m)."""
    m = tone_bin(19987, 4410, 44100)
    y = np.sin(2 * np.pi * m * np.arange(4410) / 4410)
    a = IN.assess(y, m, 44100)
    assert a["first_hidden_order"] == 4410 // np.gcd(m, 4410) - 4410 // (2 * m) >= 288


def cluster(level_dbfs, fs=44100, n=4410, tone=19987, bins=range(793, 800), weights=(1, 2, 3, 4, 3, 2, 1)):
    """PR #146 review round 3's case, synthetic: the 0.1 s CI window at 44.1 kHz, a 19987 Hz tone (on 19990 Hz) at
    full scale, and seven in-phase folded lines at 7930-7990 Hz with relative amplitudes [1, 2, 3, 4, 3, 2, 1] / 4, the
    centre one at level_dbfs re the output peak."""
    m = tone_bin(tone, n, fs)
    t = np.arange(n)
    y = np.sin(2 * np.pi * m * t / n)
    a = 10 ** (level_dbfs / 20) / max(weights)
    for b, w in zip(bins, weights):
        y = y + a * w * np.cos(2 * np.pi * b * t / n)
    return m, y


def test_neighbouring_in_phase_lines_cannot_cancel_a_product():
    """Metric 2 read these bins through a Hann window, whose estimate subtracts half of each neighbour, so the centre of
    an in-phase cluster read low and passed over its limit. The unwindowed reading is exact for each line."""
    lim = float(IN.limit_dbfs(7960.0))
    m, y = cluster(lim + 1.5)
    peak = np.max(np.abs(y - y.mean()))
    a = IN.assess(y, m, 44100)
    assert not a["passed"] and a["worst_hz"] == 7960.0
    assert a["worst_dbfs"] == pytest.approx(lim + 1.5 - 20 * np.log10(peak), abs=1e-6)
    assert a["margin_hann_db"] > 5.0                     # the windowed reading's false pass, kept as a diagnostic
    f, lv, _, _ = IN.products(y, m, 44100)
    for b, w in zip(range(793, 800), (1, 2, 3, 4, 3, 2, 1)):     # every line at its own level
        assert lv[np.argmin(np.abs(f - b * 10.0))] == pytest.approx(lim + 1.5 + 20 * np.log10(w / 4) -
                                                                    20 * np.log10(peak), abs=1e-6)
