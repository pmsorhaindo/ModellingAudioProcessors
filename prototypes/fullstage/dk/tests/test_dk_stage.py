"""DK stage tests. Pass criteria are validate.CRIT (first-pass targets, tightened 10-100x in #5), tested
against reference (ii): ngspice running the same Dempwolf-Zoelzer equations, so a failure here is a METHOD error,
not a valve-model difference. Every quality setting (#17) must pass the same criteria.
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage, QUALITY, PREWARP_HZ
from dk import validate as V
from spice.run_refs import AMPS

C = V.CRIT


@pytest.fixture(scope="module", params=list(QUALITY))
def res(request):
    return V.run(quality=request.param)


@pytest.mark.parametrize("quality", QUALITY)
def test_silence_has_no_startup_thump(quality):
    s = Stage(48000.0, quality=quality)
    y = s.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6          # volts at the output, first 100 ms of silence


@pytest.mark.parametrize("quality", QUALITY)
def test_silence_stays_at_the_operating_point(quality):
    """#70: the state runs as the deviation from the DC operating point (Stage._rest), so silence is the rest point
    itself: out stays at the DC solve's out node (0 V) up to the per-sample rounding of the rest problem (measured 0 V
    in Python, 1.2e-13 V in the C++ core, Apple M1). 1e-9 V is the C++ pinned per-sample tolerance (TOL_PINNED_ABS).
    The absolute-state form drifted 5.4e-8 / 1.8e-7 / 5.0e-7 V (eco / standard / hq, M1) within these 10 ms, towards
    a rest point set by the matrix rounding (1.6e-6 V at hq on the M1)."""
    s = Stage(48000.0, quality=quality)
    y = s.process(np.zeros(480))["out"]
    assert np.max(np.abs(y)) < 1e-9
    assert np.max(np.abs(s.x)) < 1e-9 * np.max(np.abs(s.x0))


def test_newton_always_converges(res):
    for k, st in res["iterations"].items():
        assert st["nonconverged"] == 0 and st["converged"], k


@pytest.mark.parametrize("q", ["va", "vk", "ia", "vgk"])
def test_operating_point_within_0p1pct(res, q):
    assert res["vs"]["dz"]["op_rel"][q] < C["op_rel"]


def test_small_signal_within_0p1dB_20Hz_20kHz(res):
    # Plain 4x fails (0.21 dB at 20 kHz, trapezoidal warping); eco passes with the small-cap pre-warp. dk/RESULTS.md
    o = res["vs"]["dz"]
    print(f"{res['quality']}: worst {o['ac_maxdev_db_chain']:.4f} dB at {o['ac_worst_f_chain']:.0f} Hz, "
          f"{o['ac_chain_dev_at']['20000']:+.4f} dB at 20 kHz")
    assert o["ac_maxdev_db_chain"] < C["ac_db"]


def test_prewarp_frequency_is_the_minimax_optimum():
    """PREWARP_HZ is the derived optimum (rounded), not a free parameter; without it 4x fails the target."""
    m = V.prewarp_minimax()
    print(m)
    assert abs(PREWARP_HZ - m["prewarp_hz"]) < 100.0
    assert m["ac_maxdev_db_chain"] < C["ac_db"] < m["without_prewarp_db"]


@pytest.mark.parametrize("amp", AMPS)
def test_harmonics_H2_H5_within_0p1dB_above_m90dBc(res, amp):
    rows = res["vs"]["dz"]["harm"][amp]
    tested = {k: v for k, v in rows.items() if isinstance(v, dict) and v["tested"]}
    assert tested, "no harmonic above the reference's numerical floor"
    for k, v in tested.items():
        assert abs(v["dev_db"]) < C["harm_db"], (amp, k, v)


def test_burst_bias_shift_and_recovery(res):
    b = res["vs"]["dz"]["burst"]
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["tau_rel"] < C["burst_tau_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]
