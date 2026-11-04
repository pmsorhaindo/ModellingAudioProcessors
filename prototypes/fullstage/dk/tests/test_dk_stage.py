"""DK stage tests. Pass criteria are the PROPOSED FIRST-PASS TARGETS (Michael approved them as defaults,
to be tightened later), tested against reference (ii): ngspice running the same Dempwolf-Zoelzer
equations, so a failure here is a METHOD error, not a valve-model difference.
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage
from dk import validate as V
from spice.run_refs import AMPS

C = V.CRIT


@pytest.fixture(scope="module")
def res():
    return V.run()


def test_silence_has_no_startup_thump():
    s = Stage(48000.0)
    y = s.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6          # volts at the output, first 100 ms of silence


def test_newton_always_converges(res):
    for k, st in res["iterations"].items():
        assert st["nonconverged"] == 0, k


@pytest.mark.parametrize("q", ["va", "vk", "ia", "vgk"])
def test_operating_point_within_1pct(res, q):
    assert res["vs"]["dz"]["op_rel"][q] < C["op_rel"]


def test_small_signal_within_0p1dB_20Hz_20kHz(res):
    # KNOWN FAILURE at 4x (0.21 dB at 20 kHz): trapezoidal frequency warping, see dk/RESULTS.md.
    assert res["vs"]["dz"]["ac_maxdev_db_chain"] < C["ac_db"]


@pytest.mark.parametrize("amp", AMPS)
def test_harmonics_H2_H5_within_1dB(res, amp):
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
