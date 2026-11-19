"""Lean host-rate checks (#74). Full harmonics/burst/aliasing measurements: dk.sample_rates, serial offline run."""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import validate as V
from dk.sample_rates import RATES, rest
from dk.stage import Stage, QUALITY, PREWARP_HZ
from dk.resample import Resampler


@pytest.mark.parametrize("fs", RATES)
@pytest.mark.parametrize("quality", QUALITY)
def test_host_rate_small_signal_and_rest(fs, quality):
    r = V.ref("dz")["ac"]
    f = np.array(r["f"])
    sel = (f >= V.CRIT["ac_band"][0]) & (f <= V.CRIT["ac_band"][1])
    s = Stage(fs, quality=quality)
    stage_db, chain_db = V.small_signal(s, f[sel])
    for db in (stage_db, chain_db):
        assert np.max(np.abs(db - np.array(r["db"])[sel])) < V.CRIT["ac_db"]
    # The full offline run measures 100 ms; keep this unit test at the existing #70 10 ms duration.
    y = s.process(np.zeros(round(fs * 0.01)))["out"]
    assert np.max(np.abs(y)) < 1e-9
    assert np.max(np.abs(s.x)) < 1e-9 * np.max(np.abs(s.x0))
    assert s.iteration_stats()["nonconverged"] == 0
    assert s.os == QUALITY[quality][0]  # no implicit host-rate-dependent factor
    assert s.latency_samples == Resampler(fs, QUALITY[quality][0]).latency_samples


def test_eco_prewarp_at_176400_internal():
    m = V.prewarp_minimax(fs=44100)
    assert abs(PREWARP_HZ - m["prewarp_hz"]) < 100.0  # same minimax test tolerance as at 48 kHz
    assert m["ac_maxdev_db_chain"] < V.CRIT["ac_db"] < m["without_prewarp_db"]


def test_rest_reports_startup_as_well_as_tail():
    class OffsetStage:
        def __init__(self, fs, quality):
            pass

        def process(self, x):
            return {"out": np.r_[2e-9, np.zeros(len(x) - 1)]}

        def iteration_stats(self):
            return {"nonconverged": 0}

    r = rest(44100, "hq", OffsetStage)
    assert not r["passed"] and r["last_10ms_max_abs_v"] == 0


@pytest.mark.parametrize("fs", RATES)
def test_offset_sweep_metric_at_host_rate(fs):
    from dk import aliasing as A
    # Preserve the existing offset/coherence safeguard at every rate; inspect the actual f_hz in sweep records.
    assert all(A.collision_order(A.tone_bin(f, fs, fs), fs) >= A.MIN_ORDER for f in A.SWEEP_HZ)
    m = A.tone_bin(7037, fs, fs)
    t = np.arange(fs)
    y = np.sin(2 * np.pi * m * t / fs) + 1e-3 * np.sin(2 * np.pi * 3260 * t / fs)
    assert A.alias_dbc(y, m, fs)["total_dbc"] == pytest.approx(-60.0, abs=1e-6)
