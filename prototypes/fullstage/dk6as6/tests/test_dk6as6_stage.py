"""6AS6 DK stage tests (#26). Pass criteria are dk/validate.CRIT, unchanged, tested against reference (ii): ngspice running
the same fit6as6 dual_control equations and parameters (#55) on the same netlist (spice6as6/reference_dual.json), so a
failure here is a METHOD error, not a valve-model difference. Every 6AS6 quality setting must pass.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6.stage import Stage, QUALITY
from dk6as6 import validate as V, circuit, linear
from dk import circuit as c12
from spice.run_refs import AMPS

C = V.CRIT


@pytest.fixture(scope="module", params=list(QUALITY))
def res(request):
    return V.run(quality=request.param)


def test_criteria_are_the_12ax7_stages():
    from dk.validate import CRIT
    assert C is CRIT and C == dict(op_rel=0.001, ac_db=0.1, ac_band=(20.0, 20000.0), harm_db=0.1,
                                   harm_floor_dbc=-90.0, burst_min_rel=0.005, burst_tau_rel=0.01, burst_traj_rel=0.005)
    assert QUALITY["standard"] == (8, None)


def test_passives_are_the_12ax7_stages():
    """No invented values: the coupling / load passives are dk/circuit.VALUES; the rest is EC2/EC3 + GE caps."""
    for k in circuit.PASSIVES:
        assert circuit.VALUES[k] == c12.VALUES[k], k
    assert set(circuit.VALUES) == set(circuit.PASSIVES) | {"EC2", "EC3"} | set(circuit.VALVE_CAPS)
    assert (circuit.VALUES["EC2"], circuit.VALUES["EC3"]) == (120.0, 0.0)
    assert {k: circuit.VALUES[k] for k in circuit.VALVE_CAPS} == dict(
        CG1K=4e-12, CG3K=3.4e-12, CPK=3e-12, CG1P=0.02e-12, CG1G3=0.15e-12)


def test_analog_response_is_what_ngspice_computes():
    """circuit.analog_response at the DK op point reproduces reference (ii)'s .ac (to its 7-decimal rounding)."""
    st = Stage(V.FS)
    r = V.ref("dual")
    f = np.array(r["ac"]["f"])
    db = 20 * np.log10(np.abs(linear.analog_response(st, f)))
    assert np.max(np.abs(db - np.array(r["ac"]["db"]))) < 1e-5


@pytest.mark.parametrize("quality", QUALITY)
def test_silence_has_no_startup_thump(quality):
    s = Stage(48000.0, quality=quality)
    y = s.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6          # volts at the output, first 100 ms of silence


def test_newton_always_converges(res):
    for k, st in res["iterations"].items():
        assert st["nonconverged"] == 0 and st["converged"], k


@pytest.mark.parametrize("q", V.OP_KEYS)
def test_operating_point_within_0p1pct(res, q):
    assert res["vs"]["dual"]["op_rel"][q] < C["op_rel"]


def test_small_signal_within_0p1dB_20Hz_20kHz(res):
    o = res["vs"]["dual"]
    print(f"{res['quality']}: worst {o['ac_maxdev_db_chain']:.5f} dB at {o['ac_worst_f_chain']:.0f} Hz")
    assert o["ac_maxdev_db_stage"] < C["ac_db"]
    assert o["ac_maxdev_db_chain"] < C["ac_db"]


@pytest.mark.parametrize("amp", AMPS)
def test_harmonics_H2_H5_within_0p1dB_above_m90dBc(res, amp):
    rows = res["vs"]["dual"]["harm"][amp]
    tested = {k: v for k, v in rows.items() if isinstance(v, dict) and v["tested"]}
    assert tested, "no harmonic above the reference's numerical floor"
    for k, v in tested.items():
        assert abs(v["dev_db"]) < C["harm_db"], (amp, k, v)


def test_burst_bias_shift_and_recovery(res):
    b = res["vs"]["dual"]["burst"]
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["tau_rel"] < C["burst_tau_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]


def test_all_passed(res):
    assert all(res["passed"].values()), res["passed"]
