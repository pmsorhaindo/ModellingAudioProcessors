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
