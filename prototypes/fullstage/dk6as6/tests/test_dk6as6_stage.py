"""6AS6 DK stage tests (#26, #102, #110, #111). Pass criteria are dk/validate.CRIT, unchanged, tested against
reference (ii):
ngspice running the same fit6as6 dual_control equations and parameters (#55) on the same netlist
(spice6as6/reference_dual_<bias>mA.json), so a failure here is a METHOD error, not a valve-model difference. The
default quality (standard 8x) is tested at every reference bias (spice6as6/run_refs.BIASES["dual"]: 0.1, 0.25, 0.45,
0.6 mA) and hq 16x at the range's ends; the T and P2 modes (#111) at standard at MODE_CASES; `python -m
dk6as6.validate` runs every quality at every bias of every mode (results[_<mode>].json).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import json
import warnings
from dk6as6.stage import (Stage, QUALITY, BIAS_MA, cathode_resistor, operating_point, OperatingPointError, knob_bias,
                           bias_knob, bias_point, SUPPORTED_MA, supported, UnsupportedSettingError, ExperimentalWarning)
from dk.validate import aligned
from dk6as6 import validate as V, circuit, linear
from spice.run_refs import AMPS
from spice6as6.run_refs import BIASES, MODE_BIASES

C = V.CRIT

# Experimental (unsupported) settings are run on purpose here; their ExperimentalWarning is expected.
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


# CI time (the fullstage job must stay well inside its timeout): standard at every P1 bias plus hq at the ends; T (#111)
# at standard at the top of its supported envelope (0.35 mA). P2 is experimental only (stage.SUPPORTED_MA): its known
# 0.6 mA burst failure is tested below. Every mode at every bias, standard and hq, is in results[_<mode>].json
# (python -m dk6as6.validate), checked by test_committed_validation_every_supported_setting_passes.
CASES = ([("P1", "standard", b) for b in BIASES["dual"]]
         + [("P1", "hq", b) for b in (BIASES["dual"][0], BIASES["dual"][-1])]
         + [("T", "standard", SUPPORTED_MA["T"][1])])


@pytest.fixture(scope="module", params=CASES, ids=str)
def res(request):
    m, q, b = request.param
    return V.run(quality=q, bias=b, mode=m)


def mode_of(res):
    return res.get("mode", "P1")


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
                                  CG1K=4e-12, CG3=3.4e-12, CPK=3e-12, CG1P=0.02e-12, CG1G3=0.15e-12,
                                  Rstrap=100.0)                           # T's g2/g3 -> plate stopper (#102)
    assert circuit.MODE == "P1" and set(circuit.MODES) == {"P1", "T", "P2"}
    assert BIAS_MA == 0.45 and BIASES["dual"] == (0.1, 0.25, 0.45, 0.6)


@pytest.mark.parametrize("bias, rk_k", [(0.1, 52.8), (0.25, 19.1), (0.3, 15.6), (0.6, 7.1)])
def test_cathode_resistor_matches_the_102_research(bias, rk_k):
    """The research comment's P1 rheostat values (independent static solve, same valve law), to their 3 digits."""
    assert round(cathode_resistor(bias) / 1e3, 1) == rk_k


@pytest.mark.parametrize("mode", list(circuit.MODES))
@pytest.mark.parametrize("bias", [0.1, 0.25, 0.45, 0.6])
def test_operating_point_hits_the_target_cathode_current(bias, mode):
    st = Stage(V.FS, bias=bias, mode=mode, experimental=True)
    d, n = st.dc, st.dc["nodes"]
    assert abs(d["ik"] / (bias * 1e-3) - 1) < 1e-3
    assert n["k"] == pytest.approx(st.rk * d["ik"], rel=1e-12)   # unbypassed Rk carries all of Ik
    if mode == "P1":                                             # g3 on the cathode, g2 at EC2
        assert d["vg3k"] == 0.0 and n["g2"] == circuit.VALUES["EC2"]
    elif mode == "P2":                                           # g3 on ground, g2 at EC2
        assert d["vg3k"] == pytest.approx(-n["k"], rel=1e-12) and n["g2"] == circuit.VALUES["EC2"]
    else:                                                        # T: g2 = g3, tied to the plate through Rstrap
        assert d["vg3k"] == d["vg2k"]
        assert n["a"] - n["g2"] == pytest.approx(circuit.VALUES["Rstrap"] * d["ig2"], rel=1e-9)
        assert circuit.VALUES["HT"] - n["a"] == pytest.approx(circuit.VALUES["Ra"] * d["ik"], rel=1e-9)


@pytest.mark.parametrize("mode, bias", [(m, b) for m in MODE_BIASES for b in MODE_BIASES[m]])
def test_ngspice_reference_hits_the_target_cathode_current(mode, bias):
    r = V.ref("dual", bias, mode)
    assert abs(r["op"]["ik"] / (bias * 1e-3) - 1) < 1e-3
    assert r.get("mode", "P1") == mode


@pytest.mark.parametrize("bias, rk_k", [(0.1, 80), (0.6, 1.6)])
def test_t_cathode_resistor_matches_the_102_research(bias, rk_k):
    """The #102 research's T rheostat range, "80k down to 1.6k", to its 2 digits."""
    assert float(f"{cathode_resistor(bias, mode='T') / 1e3:.2g}") == rk_k


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        Stage(V.FS, mode="P3")


@pytest.mark.parametrize("mode, bias", [("T", 0.4), ("T", BIAS_MA), ("T", 0.6), ("P2", 0.25), ("P2", BIAS_MA),
                                        ("P1", 0.05), ("P1", 0.7)])
def test_unsupported_settings_need_an_explicit_opt_in(mode, bias):
    """#111 review: outside stage.SUPPORTED_MA the Stage raises; experimental=True runs it with a warning and records
    it (Stage.supported False)."""
    with pytest.raises(UnsupportedSettingError):
        Stage(V.FS, bias=bias, mode=mode)
    with pytest.warns(ExperimentalWarning):
        st = Stage(V.FS, bias=bias, mode=mode, experimental=True)
    assert st.supported is False


def test_supported_envelope():
    assert SUPPORTED_MA == {"P1": (0.1, 0.6), "T": (0.1, 0.35)} and "P2" not in SUPPORTED_MA
    for mode, b in [("P1", 0.1), ("P1", BIAS_MA), ("P1", 0.6), ("T", 0.1), ("T", 0.25), ("T", 0.35)]:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert Stage(V.FS, bias=b, mode=mode).supported and supported(mode, b)


def _swing_8v(mode, bias):
    """(max Vg1k, min Vak, exp-capped) over the last 20 cycles of a 30 ms, 8 V peak 1 kHz sine (standard 8x)."""
    st = Stage(V.FS, bias=bias, mode=mode, experimental=True)
    t = np.arange(int(0.03 * V.FS) + st.latency_samples) / V.FS
    r = aligned(st, 8.0 * np.sin(2 * np.pi * 1000 * t), record=("out", "vgk", "vg2k", "a", "k"))["_internal"]
    keep = slice(len(r["a"]) - int(20 * st.fs / 1000), None)
    return float(r["vgk"][keep].max()), float((r["a"] - r["k"])[keep].min()), V.exp_capped(st, r)


@pytest.mark.parametrize("mode, bias", [(m, b) for m, ends in SUPPORTED_MA.items() for b in ends])
def test_supported_envelope_stays_in_the_law(mode, bias):
    """At both ends of each supported range an 8 V drive (validate.py's largest) keeps g1 negative and the plate above
    the cathode, and never reaches ngspice's exp() cap."""
    g1, vak, capped = _swing_8v(mode, bias)
    assert g1 < 0.0 and vak > 0.0 and not capped, (g1, vak, capped)


def test_t_leaves_the_law_just_above_its_envelope():
    """Why T stops at 0.35 mA: at 0.4 mA an 8 V drive takes g1 positive and the strapped plate below the cathode."""
    g1, vak, capped = _swing_8v("T", 0.4)
    assert g1 > 0.0 and vak < 0.0, (g1, vak)


def test_status_never_counts_a_non_pass():
    """validate.status: a criterion that is not compared (None) makes a case inconclusive, never passed."""
    ok = dict(op=True, ac_stage=True, ac_chain=True, harmonics=True, burst=True, newton=True)
    assert V.status(dict(passed=ok)) == "passed"
    assert V.status(dict(passed=dict(ok, burst=None))) == "inconclusive"
    assert V.status(dict(passed=dict(ok, harmonics=None, burst=False))) == "failed"


@pytest.mark.parametrize("mode", list(MODE_BIASES))
def test_committed_validation_every_supported_setting_passes(mode):
    """results[_<mode>].json: every supported setting passes every criterion; every other one is marked unsupported
    and reported as whatever it is (T 0.45 / 0.6 mA inconclusive, P2 0.6 mA failed), never as a pass."""
    res = json.load(open(V.results_file(mode)))
    assert set(res) == {f"{b:g}" for b in MODE_BIASES[mode]}
    for b, rb in res.items():
        for q in QUALITY:
            r = rb[q]
            assert r.get("supported", True) == supported(mode, float(b)), (mode, b, q)
            if supported(mode, float(b)):
                assert V.status(r) == "passed", (mode, b, q, r["passed"])
    if mode == "T":
        assert {V.status(res[b]["standard"]) for b in ("0.45", "0.6")} == {"inconclusive"}
    if mode == "P2":
        assert V.status(res["0.6"]["standard"]) == "failed" and res["0.6"]["standard"]["passed"]["burst"] is False


@pytest.fixture(scope="module")
def p2_06_burst():
    """The P2 0.6 mA burst against its reference (validate.run's burst comparison, without the sines)."""
    b, _ = V.burst(bias=0.6, mode="P2")
    r = V.ref("dual", 0.6, "P2")["burst"]
    shift = r["min_vgk"] - r["rest_vgk"]
    tr, rr = np.array(b["traj_vgk"]) - b["rest_vgk"], np.array(r["traj_vgk"]) - r["rest_vgk"]
    return dict(min_rel=abs((b["min_vgk"] - b["rest_vgk"]) / shift - 1), traj_rel=float(np.sqrt(np.mean((tr - rr) ** 2))
                / abs(shift)), tau_rel=abs(b["recovery_tau_s"] / r["recovery_tau_s"] - 1),
                tail_rel=abs(V.tail_tau(b) / V.tail_tau(r) - 1))


def test_p2_0p6mA_burst_shift_trajectory_and_tail(p2_06_burst):
    """P2 0.6 mA (experimental): the burst's shift and trajectory pass CRIT, and the recovery tail's fitted decay
    constant (31-118 ms) agrees with ngspice to 0.1 %."""
    b = p2_06_burst
    assert b["min_rel"] < C["burst_min_rel"] and b["traj_rel"] < C["burst_traj_rel"] and b["tail_rel"] < 1e-3, b


@pytest.mark.xfail(strict=True, reason="known failure (#111): P2 0.6 mA recovery tau 52.5 % off against CRIT's 1 %; "
                   "the metric starts on the 30.5 ms sample, which straddles the burst's end")
def test_p2_0p6mA_burst_recovery_tau_crit(p2_06_burst):
    assert p2_06_burst["tau_rel"] < C["burst_tau_rel"], p2_06_burst


def test_p2_frequency_doubling():
    """P2 (experimental): H2 >= H1 at 2 V (the documented frequency doubling; dk6as6/RESULTS.md, Modes)."""
    st = Stage(V.FS, bias=0.25, mode="P2", experimental=True)
    t = np.arange(int(0.3 * V.FS) + st.latency_samples) / V.FS
    h = V.harmonics(aligned(st, 2.0 * np.sin(2 * np.pi * 1000 * t))["out"], V.FS, 1000.0, 100)
    assert h[1] >= h[0], h[:3]


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


def test_knob_law_is_reverse_acting_and_log_in_ik():
    """#110: knob 1 = 0.6 mA, 11 = 0.1 mA (the documented range on the ends), each knob step the same current ratio."""
    ik = [knob_bias(k) for k in range(1, 12)]
    assert ik[0] == pytest.approx(0.6, rel=1e-15) and ik[-1] == pytest.approx(0.1, rel=1e-15)
    assert all(a > b for a, b in zip(ik, ik[1:]))                       # advancing the knob lowers the current
    assert np.allclose(np.array(ik[1:]) / ik[:-1], (0.1 / 0.6) ** 0.1, rtol=1e-13, atol=0)
    assert knob_bias(6) == pytest.approx(0.6 / 6 ** 0.5, rel=1e-15)     # mid-travel: the geometric mean, 0.245 mA
    for b in (0.1, 0.25, 0.3, 0.45, 0.6):
        assert knob_bias(bias_knob(b)) == pytest.approx(b, rel=1e-13)


@pytest.mark.parametrize("knob", [0.5, 11.5])
def test_knob_outside_the_scale_raises(knob):
    with pytest.raises(ValueError):
        knob_bias(knob)


@pytest.mark.parametrize("bias", [0.05, 0.7])
def test_bias_outside_the_knob_range_raises(bias):
    with pytest.raises(ValueError):
        bias_knob(bias)


@pytest.mark.parametrize("bias", [0.1, 0.25, 0.6])
def test_bias_point_is_the_meter_reading_of_the_stage(bias):
    """bias_point() reports the stage's own DC point: Ik on target, Vk = Rk Ik (all of Ik in the rheostat)."""
    m, st = bias_point(bias), Stage(V.FS, bias=bias)
    assert m["rk"] == st.rk and m["ik"] == m["ip"] + m["ig2"]
    assert (m["vk"], m["va"], m["ip"], m["ig2"]) == (st.dc["nodes"]["k"], st.dc["nodes"]["a"], st.dc["ip"],
                                                     st.dc["ig2"])
    assert m["ik"] == pytest.approx(bias * 1e-3, rel=1e-9)
    assert m["vk"] == pytest.approx(m["rk"] * m["ik"], rel=1e-12)


def test_bias_is_fixed_for_the_run():
    with pytest.raises(NotImplementedError):
        Stage(V.FS).swap_bias(0.25)


@pytest.mark.parametrize("mode, bias", [(m, b) for m in MODE_BIASES for b in MODE_BIASES[m]])
def test_analog_response_is_what_ngspice_computes(mode, bias):
    """circuit.analog_response at the DK op point reproduces reference (ii)'s .ac (to its 7-decimal rounding)."""
    st = Stage(V.FS, bias=bias, mode=mode, experimental=True)
    r = V.ref("dual", bias, mode)
    f = np.array(r["ac"]["f"])
    db = 20 * np.log10(np.abs(linear.analog_response(st, f)))
    assert np.max(np.abs(db - np.array(r["ac"]["db"]))) < 1e-5


@pytest.mark.parametrize("mode", list(circuit.MODES))
@pytest.mark.parametrize("bias", [0.1, BIAS_MA, 0.6])
@pytest.mark.parametrize("quality", QUALITY)
def test_silence_has_no_startup_thump(quality, bias, mode):
    s = Stage(48000.0, bias=bias, quality=quality, mode=mode, experimental=True)
    y = s.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6          # volts at the output, first 100 ms of silence


@pytest.mark.parametrize("bias", [0.1, 0.25, BIAS_MA, 0.6])
def test_p2_is_quieter_and_inverted(bias):
    """P2's documented "quieter" output (UA, SOS) and, in this model, opposite polarity to P1 / T: the plate current
    follows g3 (on ground, so driven by -Vk) more than g1. Small-signal at 1 kHz."""
    h = {m: linear.analog_response(Stage(V.FS, bias=bias, mode=m, experimental=True), [1000.0])[0]
         for m in circuit.MODES}
    assert 20 * np.log10(abs(h["P1"]) / abs(h["P2"])) > 14.0
    assert h["P1"].real < 0 and h["T"].real < 0 < h["P2"].real


def test_mode_harmonic_character(res):
    """The manuals' character of each mode where this model shows it: T even-dominant at every drive inside the valve
    law (H2 over 10 dB above H3, "even harmonics"; validate.exp_capped marks the drives outside it); P2 frequency
    doubling (H2 >= H1) at 2 V. P1's "odd harmonics" holds only in a narrow band and is not asserted (dk6as6/RESULTS.md,
    Modes)."""
    m = mode_of(res)
    h = {a: res["sine"][a]["H_dBc"] for a in AMPS if not res["sine"][a].get("spice_exp_capped")}   # in the law
    if m == "T":
        assert all(v[1] > v[2] + 10 for v in h.values()), h


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
    """Recovery tau only where the reference defines one (validate.py: none without a bypass cap or grid current)."""
    b = res["vs"]["dual"]["burst"]
    if b.get("spice_exp_capped"):
        assert mode_of(res) == "T"
        pytest.skip("the 4 V burst passes ngspice's B-source exp() cap: the reference is not the same law")
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]
    if b["tau_tested"]:
        assert b["tau_rel"] < C["burst_tau_rel"]
    else:
        assert np.isnan(b["ref"]["tau"])


def test_all_passed(res):
    assert V.status(res) == "passed" and res.get("supported", True), res["passed"]
