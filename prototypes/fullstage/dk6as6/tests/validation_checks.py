"""The live validation checks of the 6AS6 DK stage against its same-model ngspice references (dk/validate.CRIT), shared
by test_dk6as6_stage.py and test_dk6as6_validation.py, which each define the `res` fixture over their own cases (two
CI shards; PR #128 review, #127). Not collected itself (no test_ prefix).
"""
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6 import validate as V
from spice.run_refs import AMPS

C = V.CRIT


def mode_of(res):
    return res.get("mode", "P1")


def test_mode_harmonic_character(res):
    """The manuals' character of each mode where this model shows it: T even-dominant (H2 over 10 dB above H3, "even
    harmonics") at every drive inside the valve law without grid current (validate.exp_capped marks the drives outside
    it), and with grid current (#112) up to 2 V, below where g1 conducts; P2 frequency doubling (H2 >= H1) at 2 V. P1's
    "odd harmonics" holds only in a narrow band and is not asserted (dk6as6/RESULTS.md, Modes). T at 8 V with grid
    current is test_t_grid_clipping_at_8v_is_not_even_dominant."""
    m = mode_of(res)
    h = {a: res["sine"][a]["H_dBc"] for a in AMPS if not res["sine"][a].get("spice_exp_capped")   # in the law
         and (res["model"] != "dualg" or float(a) <= 2.0)}
    if m == "T":
        assert all(v[1] > v[2] + 10 for v in h.values()), h


def test_newton_always_converges(res):
    for k, st in res["iterations"].items():
        assert st["nonconverged"] == 0 and st["converged"], k


@pytest.mark.parametrize("q", V.OP_KEYS)
def test_operating_point_within_0p1pct(res, q):
    assert res["vs"][res["model"]]["op_rel"][q] < C["op_rel"]


def test_small_signal_within_0p1dB_20Hz_20kHz(res):
    o = res["vs"][res["model"]]
    print(f"{res['quality']}: worst {o['ac_maxdev_db_chain']:.5f} dB at {o['ac_worst_f_chain']:.0f} Hz")
    assert o["ac_maxdev_db_stage"] < C["ac_db"]
    assert o["ac_maxdev_db_chain"] < C["ac_db"]


@pytest.mark.parametrize("amp", AMPS)
def test_harmonics_H2_H5_within_0p1dB_above_m90dBc(res, amp):
    rows = res["vs"][res["model"]]["harm"][amp]
    if rows.get("spice_exp_capped"):   # the reference is not the same law there (validate.SPICE_EXP_CAP)
        assert mode_of(res) == "T", "only T's positive-g1 / zero-screen region reaches ngspice's exp() cap"
        pytest.skip(f"{mode_of(res)} {res['bias_ma']} mA, {amp} V: the DK run passes ngspice's B-source exp() cap, so "
                    "the reference is not the same law (outside the valve law too: RESULTS.md, Modes)")
    tested = {k: v for k, v in rows.items() if isinstance(v, dict) and v["tested"]}
    if not tested:   # 0.25 mA at 0.05 V: H2 -95.9 dBc, the stage is that linear (RESULTS.md)
        pytest.skip(f"every reference H2-H5 is below {C['harm_floor_dbc']} dBc at {res['bias_ma']} mA, {amp} V: "
                    + str({k: round(v["ref_dBc"], 1) for k, v in rows.items() if isinstance(v, dict)}))
    for k, v in tested.items():
        assert abs(v["dev_db"]) < C["harm_db"], (amp, k, v)


def test_burst_bias_shift_and_recovery(res):
    """Shift, trajectory and the recovery tau: the full-window tail tau (validate.burst_compare, #124), defined in every
    case (the coupling-cap tail without grid current, grid blocking with it)."""
    b = res["vs"][res["model"]]["burst"]
    if b.get("spice_exp_capped"):
        assert mode_of(res) == "T"
        pytest.skip("the 4 V burst passes ngspice's B-source exp() cap: the reference is not the same law")
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]
    assert b["tau_tested"] and b["tau_rel"] < C["burst_tau_rel"], b      # the full-window tail tau (#124)


def test_all_passed(res):
    assert V.status(res) == "passed" and res.get("supported", True), res["passed"]
