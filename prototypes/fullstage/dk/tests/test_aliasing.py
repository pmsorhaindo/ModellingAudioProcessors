"""Aliasing limit per quality setting and resampler (#6). Each limit in dk.aliasing.LIMIT_DBC is the worst total
non-harmonic energy over the full offset-tone sweep (1037 ... 19037 Hz at 2 V and 8 V, 1 s windows; validate.py
runs it) plus aliasing.MARGIN_DB. The per-setting limit check on the lean subset (aliasing.CI_TONES with CI_SETTLE_S /
CI_WINDOW_S buffers) is enforced by dk.aliasing_guard, the blocking `aliasing` CI job (#35), together with a baseline
ratchet; run it locally with `python -m dk.aliasing_guard`. Here: the limits and CI tones cover every setting, filter
and drive, the metric itself (periodic Hann, coherent bins, masks) is checked on synthetic signals, and the 64x ground
truth sits below every limit.
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import aliasing as A
from dk.stage import QUALITY

FS = A.FS


def harmonic_signal(m, n, levels_db):
    t = np.arange(n)
    return sum(10 ** (lev / 20) * np.sin(2 * np.pi * (k + 1) * m * t / n + k) for k, lev in enumerate(levels_db))


def test_limits_cover_every_setting_filter_and_drive():
    assert set(A.LIMIT_DBC) == set(A.CI_TONES) == {(q, f) for q in QUALITY for f in A.FILTERS}
    for k, lim in A.LIMIT_DBC.items():
        assert set(lim) == set(A.CI_TONES[k]) == set(A.DRIVES) and all(isinstance(v, float) for v in lim.values())
        assert all(set(t) <= set(A.SWEEP_HZ) for t in A.CI_TONES[k].values())


@pytest.mark.parametrize("n", [FS, int(A.CI_WINDOW_S * FS)])
def test_exactly_harmonic_signal_reads_below_the_analysis_floor(n):
    m = A.tone_bin(1037, n)
    y = harmonic_signal(m, n, [-20.0 * k for k in range(n // (2 * m))])
    assert A.alias_dbc(y, m)["total_dbc"] < A.ANALYSIS_FLOOR_DBC
    # the symmetric Hann (np.hanning(n)) is what read ~-113 dBc here at 1 s: its skirt leaks past the +-3 bin mask
    P = np.abs(np.fft.rfft(y * np.hanning(n))) ** 2
    assert 10 * np.log10(P[m + A.MASK_BINS + 1] / P[m]) > -140


def test_injected_alias_lines_are_counted_exactly():
    n = FS
    m = A.tone_bin(7037, n)
    y = harmonic_signal(m, n, [0.0, -30.0, -40.0])           # H1..H3, all below fs/2
    t = np.arange(n)
    y = y + 1e-3 * np.sin(2 * np.pi * 3260 * t / n) + 1e-4 * np.sin(2 * np.pi * 9926 * t / n + 1)
    r = A.alias_dbc(y, m)
    assert r["total_dbc"] == pytest.approx(10 * np.log10(1e-6 + 1e-8), abs=1e-6)
    assert r["worst_dbc"] == pytest.approx(-60.0 - 10 * np.log10(1.5), abs=0.01)   # Hann: the line's peak bin
    assert r["worst_hz"] == 3260


def test_offset_tones_put_every_alias_on_its_own_bin():
    # 10 kHz divides 48 kHz: H22 folds onto H2 (k + j = 24), so its aliases hide under the harmonic masks
    assert A.collision_order(10000, FS) == 24
    assert min(A.collision_order(A.tone_bin(f, FS), FS) for f in A.SWEEP_HZ) == 81      # 13037 Hz; others >= 919
    assert all(A.tone_bin(f, FS) == f for f in A.SWEEP_HZ)                                # exact at 1 s
    n = int(A.CI_WINDOW_S * FS)
    assert all(A.collision_order(A.tone_bin(f, n), n) >= A.MIN_ORDER for f in A.SWEEP_HZ)


def test_ground_truth_covers_the_sweep_and_sits_below_every_limit():
    gt = A.load_ground_truth()["records"]
    for flt in A.FILTERS:
        for d in A.DRIVES:
            sel = A.select(gt, f"{A.GROUND_TRUTH_OS}x", flt, d)
            assert sorted(r["tone"] for r in sel) == list(A.SWEEP_HZ)
            assert all(max(r["total_dbc"] for r in sel) < A.LIMIT_DBC[(q, flt)][d] for q in QUALITY)
