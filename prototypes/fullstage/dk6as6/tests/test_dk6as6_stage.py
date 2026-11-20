"""6AS6 DK stage tests (#26, #102). Pass criteria are dk/validate.CRIT, unchanged, tested against reference (ii):
ngspice running the same fit6as6 dual_control equations and parameters (#55) on the same netlist
(spice6as6/reference_dual_<bias>mA.json), so a failure here is a METHOD error, not a valve-model difference. Every
6AS6 quality setting must pass at every reference bias (spice6as6/run_refs.BIASES["dual"]).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6.stage import Stage, QUALITY, BIAS_MA, cathode_resistor, operating_point, OperatingPointError
from dk6as6 import validate as V, circuit, linear
from spice.run_refs import AMPS
from spice6as6.run_refs import BIASES

C = V.CRIT


@pytest.fixture(scope="module", params=[(q, b) for b in BIASES["dual"] for q in QUALITY], ids=str)
def res(request):
    q, b = request.param
    return V.run(quality=q, bias=b)


def test_criteria_are_the_12ax7_stages():
    from dk.validate import CRIT
    assert C is CRIT and C == dict(op_rel=0.001, ac_db=0.1, ac_band=(20.0, 20000.0), harm_db=0.1,
                                   harm_floor_dbc=-90.0, burst_min_rel=0.005, burst_tau_rel=0.01, burst_traj_rel=0.005)
    assert QUALITY["standard"] == (8, None)


def test_values_are_the_102_table():
    """No invented values: the #102 research table (dk6as6/RESULTS.md, "Circuit values"), the GE capacitances, and
    the two test-bench values kept from the 12AX7 stage (Rs, Rgs). No bypass capacitor; Rk is solved per bias."""
    assert circuit.VALUES == dict(HT=200.0, Ra=270e3, Rg=1e6, Rl=1e6, Cin=100e-9, Cout=100e-9, EC2=120.0,
                                  Rs=1e3, Rgs=68e3,
                                  CG1K=4e-12, CG3=3.4e-12, CPK=3e-12, CG1P=0.02e-12, CG1G3=0.15e-12)
    assert BIAS_MA == 0.45 and BIASES["dual"] == (0.25, 0.45)


@pytest.mark.parametrize("bias, rk_k", [(0.1, 52.8), (0.25, 19.1), (0.3, 15.6), (0.6, 7.1)])
def test_cathode_resistor_matches_the_102_research(bias, rk_k):
    """The research comment's P1 rheostat values (independent static solve, same valve law), to their 3 digits."""
    assert round(cathode_resistor(bias) / 1e3, 1) == rk_k


@pytest.mark.parametrize("bias", [0.1, 0.25, 0.45, 0.6])
def test_operating_point_hits_the_target_cathode_current(bias):
    st = Stage(V.FS, bias=bias)
    assert abs(st.dc["ik"] / (bias * 1e-3) - 1) < 1e-3
    assert st.dc["vg3k"] == 0.0                               # P1: g3 on the cathode
    assert st.dc["nodes"]["k"] == pytest.approx(st.rk * st.dc["ik"], rel=1e-12)   # unbypassed Rk carries all of Ik


@pytest.mark.parametrize("bias", BIASES["dual"])
def test_ngspice_reference_hits_the_target_cathode_current(bias):
    assert abs(V.ref("dual", bias)["op"]["ik"] / (bias * 1e-3) - 1) < 1e-3


@pytest.mark.parametrize("bias", [0.001, 5.0])
def test_unreachable_bias_raises(bias):
    with pytest.raises(OperatingPointError):
        cathode_resistor(bias)


def test_cathode_current_falls_with_rk():
    ik = [operating_point(rk)["ik"] for rk in np.geomspace(500.0, 1e6, 12)]
    assert all(a > b for a, b in zip(ik, ik[1:]))


@pytest.mark.parametrize("bias", [0.25, BIAS_MA])
def test_operating_point_method_is_the_6as6_solve(bias):
    """Stage.operating_point() (the dk.stage.Stage API) re-solves this stage's 6AS6 DC point, not the 12AX7's (PR #118
    review: it fell through to the inherited method and raised AttributeError)."""
    st = Stage(V.FS, bias=bias)
    dc = st.operating_point()
    assert set(dc) == set(st.dc) and dc["rk"] == st.rk
    for k in ("vg1k", "vg2k", "vg3k", "vak", "ip", "ig2", "ik"):
        assert dc[k] == st.dc[k], k
    assert abs(dc["ik"] / (bias * 1e-3) - 1) < 1e-3


def test_bias_is_fixed_for_the_run():
    with pytest.raises(NotImplementedError):
        Stage(V.FS).swap_bias(0.25)


@pytest.mark.parametrize("bias", BIASES["dual"])
def test_analog_response_is_what_ngspice_computes(bias):
    """circuit.analog_response at the DK op point reproduces reference (ii)'s .ac (to its 7-decimal rounding)."""
    st = Stage(V.FS, bias=bias)
    r = V.ref("dual", bias)
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
    if not tested:   # 0.25 mA at 0.05 V: H2 -95.9 dBc, the stage is that linear (RESULTS.md)
        pytest.skip(f"every reference H2-H5 is below {C['harm_floor_dbc']} dBc at {res['bias_ma']} mA, {amp} V: "
                    + str({k: round(v["ref_dBc"], 1) for k, v in rows.items() if isinstance(v, dict)}))
    for k, v in tested.items():
        assert abs(v["dev_db"]) < C["harm_db"], (amp, k, v)


def test_burst_bias_shift_and_recovery(res):
    """Recovery tau only where the reference defines one (validate.py: none without a bypass cap or grid current)."""
    b = res["vs"]["dual"]["burst"]
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]
    if b["tau_tested"]:
        assert b["tau_rel"] < C["burst_tau_rel"]
    else:
        assert np.isnan(b["ref"]["tau"])


def test_all_passed(res):
    assert all(res["passed"].values()), res["passed"]
