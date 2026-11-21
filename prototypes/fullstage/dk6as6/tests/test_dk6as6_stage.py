"""6AS6 DK stage tests (#26, #102, #110, #111, #112). Pass criteria are dk/validate.CRIT, unchanged, tested against
reference (ii):
ngspice running the same fit6as6 dual_control equations and parameters (#55) on the same netlist, with the stage's
default g1 grid current (#112; model "dualg", spice6as6/reference_dualg_*.json) or without it (grid_hardness=0, model
"dual", reference_dual_*.json), so a failure here is a METHOD error, not a valve-model difference. The default stage
(dualg) at standard 8x is tested at every P1 reference bias (0.1, 0.25, 0.45, 0.6 mA), at hq 16x at the range's ends
and in T at the top of its envelope; the no-grid stage at standard at the default bias; `python -m dk6as6.validate
[--grid]` runs every quality at every bias of every mode (results[_grid][_<mode>].json).
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
                           bias_knob, bias_point, SUPPORTED_MA, SUPPORTED_MA_NO_GRID, envelope, supported,
                           UnsupportedSettingError, ExperimentalWarning, OutOfLawWarning)
from dk6as6 import envelope as E
from dk6as6.pentode import HARDNESS
from dk.validate import aligned
from dk6as6 import validate as V, circuit, linear
from spice.run_refs import AMPS
from spice6as6.run_refs import BIASES, MODE_BIASES, GRID_BIASES

C = V.CRIT

# Experimental (unsupported) settings are run on purpose here; their ExperimentalWarning is expected.
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


# CI time (both fullstage jobs have a 20 min timeout; #112 keeps the 6as6 shard near #121's 11 min): the default stage
# (dualg) at standard at the ends of P1's grid references (0.1, 0.45 mA) plus hq at 0.45 mA, and T at standard at the
# top of its supported envelope. The no-grid stage (grid_hardness=0) is the #111 stage bit for bit; its every-bias
# results are results[_<mode>].json, and test_p1_grid_current_is_negligible ties P1 0.6 mA to the default stage. P2 is
# experimental only (stage.SUPPORTED_MA): its known 0.6 mA burst failure is tested below. Every mode at every bias,
# standard and hq, is in results[_grid][_<mode>].json (python -m dk6as6.validate [--grid]), checked by
# test_committed_validation_every_supported_setting_passes.
CASES = ([("P1", "standard", b, "dualg") for b in (GRID_BIASES["P1"][0], GRID_BIASES["P1"][-1])]
         + [("P1", "hq", GRID_BIASES["P1"][-1], "dualg"), ("T", "standard", SUPPORTED_MA["T"][1], "dualg")])


@pytest.fixture(scope="module", params=CASES, ids=str)
def res(request):
    m, q, b, model = request.param
    return V.run(quality=q, bias=b, mode=m, model=model)


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
    assert abs(d["ik"] / (bias * 1e-3) - 1) < 1e-3 and d["ik"] == d["ip"] + d["ig2"] + d["ig1"]
    assert n["gl"] - n["g"] == pytest.approx(circuit.VALUES["Rgs"] * d["ig1"], rel=1e-6, abs=1e-15)   # Ig1 in Rgs
    assert n["k"] == pytest.approx(st.rk * d["ik"], rel=1e-12)   # unbypassed Rk carries all of Ik
    if mode == "P1":                                             # g3 on the cathode, g2 at EC2
        assert d["vg3k"] == 0.0 and n["g2"] == circuit.VALUES["EC2"]
    elif mode == "P2":                                           # g3 on ground, g2 at EC2
        assert d["vg3k"] == pytest.approx(-n["k"], rel=1e-12) and n["g2"] == circuit.VALUES["EC2"]
    else:                                                        # T: g2 = g3, tied to the plate through Rstrap
        assert d["vg3k"] == d["vg2k"]
        assert n["a"] - n["g2"] == pytest.approx(circuit.VALUES["Rstrap"] * d["ig2"], rel=1e-9)
        assert circuit.VALUES["HT"] - n["a"] == pytest.approx(circuit.VALUES["Ra"] * (d["ip"] + d["ig2"]), rel=1e-9)


REFS = ([("dual", m, b) for m in MODE_BIASES for b in MODE_BIASES[m]]
        + [("dualg", m, b) for m in GRID_BIASES for b in GRID_BIASES[m]])


@pytest.mark.parametrize("model, mode, bias", REFS)
def test_ngspice_reference_hits_the_target_cathode_current(model, mode, bias):
    r = V.ref(model, bias, mode)
    assert abs(r["op"]["ik"] / (bias * 1e-3) - 1) < 1e-3
    assert r.get("mode", "P1") == mode
    assert ("ig1" in r["op"]) == (model == "dualg")


@pytest.mark.parametrize("bias, rk_k", [(0.1, 80), (0.6, 1.6)])
def test_t_cathode_resistor_matches_the_102_research(bias, rk_k):
    """The #102 research's T rheostat range, "80k down to 1.6k", to its 2 digits."""
    assert float(f"{cathode_resistor(bias, mode='T') / 1e3:.2g}") == rk_k


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        Stage(V.FS, mode="P3")


@pytest.mark.parametrize("mode, bias, hardness", [
    ("P2", 0.25, HARDNESS), ("P2", BIAS_MA, HARDNESS), ("P1", 0.05, HARDNESS), ("P1", 0.7, HARDNESS),
    ("T", 0.55, HARDNESS), ("T", 0.6, HARDNESS),
    ("T", 0.35, 0.0), ("T", BIAS_MA, 0.0), ("T", 0.6, 0.0), ("P2", 0.25, 0.0),
    ("P1", BIAS_MA, 0.5), ("T", 0.25, 2.0)])
def test_unsupported_settings_need_an_explicit_opt_in(mode, bias, hardness):
    """#111 review: outside the supported envelope of its grid hardness (stage.envelope) the Stage raises;
    experimental=True runs it with a warning and records it (Stage.supported False). Only the default hardness and 0
    are validated, so any other is experimental at every setting (#112)."""
    with pytest.raises(UnsupportedSettingError):
        Stage(V.FS, bias=bias, mode=mode, grid_hardness=hardness)
    with pytest.warns(ExperimentalWarning):
        st = Stage(V.FS, bias=bias, mode=mode, experimental=True, grid_hardness=hardness)
    assert st.supported is False


def test_supported_envelope():
    """With the default grid current (#112) P1 is supported over the documented 0.1-0.6 mA and T to 0.5 mA (above, its
    2 V aliasing fails the gate); without it (hardness 0, the #111 stage) T only to 0.3 mA; P2 and other hardnesses
    nowhere."""
    assert SUPPORTED_MA == {"P1": (0.1, 0.6), "T": (0.1, 0.5)} and "P2" not in SUPPORTED_MA
    assert SUPPORTED_MA_NO_GRID == {"P1": (0.1, 0.6), "T": (0.1, 0.3)}
    assert envelope() is envelope(HARDNESS) is SUPPORTED_MA and envelope(0.0) is SUPPORTED_MA_NO_GRID
    assert envelope(0.5) == {} and not supported("P1", BIAS_MA, 0.5)
    for gh, env in ((HARDNESS, SUPPORTED_MA), (0.0, SUPPORTED_MA_NO_GRID)):
        for mode, (lo, hi) in env.items():
            for b in (lo, hi):
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    assert Stage(V.FS, bias=b, mode=mode, grid_hardness=gh).supported and supported(mode, b, gh)


# Live re-checks, cut for CI time (envelope[_grid].json has every stimulus at every end, and
# test_envelope_record_is_the_supported_range checks them): the default stage at both ends of T at both qualities
# and of P1 at standard (>= 3.17 V of g1 margin there), and the no-grid stage at T's top with its worst stimulus (the
# 100 Hz step onset).
STAYS = ([(m, b, f, ph, HARDNESS, q) for m, ends in SUPPORTED_MA.items() for b in ends
          for f, ph in ((100, "cos"), (20, "cos"), (18000, "sin")) for q in (QUALITY if m == "T" else ("standard",))]
         + [("T", SUPPORTED_MA_NO_GRID["T"][1], 100, "cos", 0.0, "standard")])


@pytest.mark.parametrize("mode, bias, f, phase, hardness, quality", STAYS)
def test_supported_envelope_stays_in_the_law(mode, bias, f, phase, hardness, quality):
    """At both ends of each supported range, 8 V peak tones from silence (envelope.py's worst stimuli: the step onset
    at 100 Hz and 20 Hz, the slope onset at 18 kHz) stay in the law in force on the interpolated internal-rate voltages
    (envelope.in_law): without grid current g1 negative and the plate above the cathode; with it (#112) the plate above
    the cathode, Ip + Ig2 >= 0 and Newton converged (g1 may go positive: the grid law models it). The runtime guard
    counts nothing and warns nothing."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", OutOfLawWarning)
        r = E.excursion(mode, bias, quality, f, phase, hardness)
    assert E.in_law(r) and r["out_of_law"] == 0, r


@pytest.mark.parametrize("quality", QUALITY)
@pytest.mark.parametrize("hardness", [HARDNESS, 0.0])
def test_tone_onset_from_silence_at_the_top_of_t(quality, hardness):
    """The PR #121 re-review reproduction: an 8 V peak, zero-phase 18 kHz sine from silence through Stage.process.
    The interpolator overshoots (8.63 V); at T's supported top (of each hardness) the runtime guard sees nothing;
    without grid current g1 stays negative."""
    st = Stage(48000, mode="T", bias=envelope(hardness)["T"][1], quality=quality, grid_hardness=hardness)
    x = 8 * np.sin(2 * np.pi * 18000 * np.arange(480) / 48000)
    with warnings.catch_warnings():
        warnings.simplefilter("error", OutOfLawWarning)
        r = st.process(x, record=("vgk",))["_internal"]
    assert st.supported and st.out_of_law == 0 and (hardness or max(r["vgk"]) <= 0.0)


@pytest.mark.parametrize("quality", QUALITY)
def test_t_leaves_the_law_just_above_its_envelope(quality):
    """Why T stops at 0.3 mA without grid current: at 0.35 mA an 8 V step onset at 100 Hz takes g1 positive (+0.30 V at
    hq), and the runtime guard sees it: Stage.out_of_law counts the samples and OutOfLawWarning is raised."""
    st = Stage(48000.0, bias=0.35, mode="T", quality=quality, experimental=True, grid_hardness=0.0)
    with pytest.warns(OutOfLawWarning):
        r = st.process(E.stimulus(100, "cos", st.latency_samples), record=("vgk",))["_internal"]
    assert max(r["vgk"]) > 0.0 and st.out_of_law > 0 and not st.supported


@pytest.mark.parametrize("quality", ["standard"])     # hq: envelope_grid.json (-0.0779 V); CI time
def test_grid_current_keeps_that_onset_in_the_law(quality):
    """#112: the same T 0.35 mA / 8 V / 100 Hz step onset with the default grid current: the grid current clamps g1, so
    it stays below the cathode (-0.08 V at worst over envelope.py's class), the plate above it, and the guard counts
    nothing."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", OutOfLawWarning)
        r = E.excursion("T", 0.35, quality, 100, "cos", HARDNESS)
    assert r["vg1k_max"] < 0.0 and E.in_law(r) and r["out_of_law"] == 0, r


def test_envelope_record_is_the_supported_range():
    """envelope.json (no grid current, #111) and envelope_grid.json (#112): both ends of every supported range are in
    the law over the whole stimulus class. Without grid current T's top is the highest scanned T bias that is, and the
    next one (0.35 mA) is not; with it every scanned T bias up to T's top is."""
    for fn, gh in ((E.JSON, 0.0), (E.JSON_GRID, HARDNESS)):
        s = json.load(open(fn))["summary"]
        for m, ends in envelope(gh).items():
            assert all(s[m][f"{b:g}"]["in_law"] for b in ends), (fn, m)
        assert all(w["in_law"] for b, w in s["T"].items() if float(b) <= envelope(gh)["T"][1] + 1e-9), fn
    s = json.load(open(E.JSON))["summary"]
    t_in = [float(b) for b, w in s["T"].items() if w["in_law"]]
    assert max(t_in) == SUPPORTED_MA_NO_GRID["T"][1] and not s["T"]["0.35"]["in_law"]


def _swing_8v(mode, bias, hardness=HARDNESS, f0=1000.0):
    """(max Vg1k, min Vak, min Ip + Ig2, exp-capped) over the last 20 cycles of a 30 ms, 8 V peak sine at f0 (1 kHz)
    (standard 8x), at `hardness`."""
    st = Stage(V.FS, bias=bias, mode=mode, experimental=True, grid_hardness=hardness)
    t = np.arange(int(0.03 * V.FS) + st.latency_samples) / V.FS
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OutOfLawWarning)
        r = aligned(st, 8.0 * np.sin(2 * np.pi * f0 * t), record=("out", "vgk", "vg2k", "a", "k", "ipk"))["_internal"]
    keep = slice(len(r["a"]) - int(20 * st.fs / 1000), None)
    return (float(r["vgk"][keep].max()), float((r["a"] - r["k"])[keep].min()), float(r["ipk"][keep].min()),
            V.exp_capped(st, r))


def test_p1_grid_current_is_negligible():
    """P1 at 0.6 mA (the top of its envelope, the least cathode feedback) with and without grid current, an 8 V 1 kHz
    sine: g1 stays below -3 V, Ig1 < 1e-20 A, and the outputs agree to rounding (1e-12 relative). So the dual
    reference at 0.6 mA (which passes, results.json) is the dualg reference too, to far below ngspice's abstol of
    1e-14 A: run_refs.BIASES["dualg"] stops at 0.45 mA (RESULTS.md, "Grid current (#112)")."""
    r = {}
    for gh in (HARDNESS, 0.0):
        st = Stage(V.FS, bias=0.6, grid_hardness=gh)
        t = np.arange(int(0.03 * V.FS) + st.latency_samples) / V.FS
        r[gh] = aligned(st, 8.0 * np.sin(2 * np.pi * 1000 * t), record=("out", "vgk", "ig1"))
    g = r[HARDNESS]["_internal"]
    assert g["vgk"].max() < -3.0 and g["ig1"].max() < 1e-20
    y0, y1 = r[0.0]["out"], r[HARDNESS]["out"]
    assert np.max(np.abs(y1 - y0)) < 1e-12 * np.max(np.abs(y0))


def test_grid_current_keeps_t_in_the_law_where_it_was_not():
    """#112: the same T 0.4 mA / 8 V with the default grid current: g1 clamped below +0.05 V, the plate above the
    cathode (> 10 V), Ip + Ig2 > 0."""
    g1, vak, ipk, capped = _swing_8v("T", 0.4)
    assert g1 < 0.05 and vak > 10.0 and ipk > 0.0 and not capped, (g1, vak, ipk)


def test_status_never_counts_a_non_pass():
    """validate.status: a criterion that is not compared (None) makes a case inconclusive, never passed."""
    ok = dict(op=True, ac_stage=True, ac_chain=True, harmonics=True, burst=True, newton=True)
    assert V.status(dict(passed=ok)) == "passed"
    assert V.status(dict(passed=dict(ok, burst=None))) == "inconclusive"
    assert V.status(dict(passed=dict(ok, harmonics=None, burst=False))) == "failed"


@pytest.mark.parametrize("model, mode", [("dual", m) for m in MODE_BIASES] + [("dualg", m) for m in GRID_BIASES])
def test_committed_validation_every_supported_setting_passes(model, mode):
    """results[_grid][_<mode>].json: every supported setting (of that model's grid hardness) passes every criterion;
    every other one is marked unsupported and reported as whatever it is (no grid: T 0.45 / 0.6 mA inconclusive, P2
    0.6 mA failed), never as a pass."""
    res = json.load(open(V.results_file(mode, model)))
    gh = V.HARDNESS[model]
    assert set(res) == {f"{b:g}" for b in V.biases_of(model, mode)}
    for b, rb in res.items():
        for q in QUALITY:
            r = rb[q]
            assert r.get("supported", True) == supported(mode, float(b), gh), (model, mode, b, q)
            if supported(mode, float(b), gh):
                assert V.status(r) == "passed", (model, mode, b, q, r["passed"])
    if mode == "T" and model == "dual":
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
    assert m["rk"] == st.rk and m["ik"] == m["ip"] + m["ig2"] + m["ig1"]
    assert (m["vk"], m["va"], m["ip"], m["ig2"]) == (st.dc["nodes"]["k"], st.dc["nodes"]["a"], st.dc["ip"],
                                                     st.dc["ig2"])
    assert m["ik"] == pytest.approx(bias * 1e-3, rel=1e-9)
    assert m["vk"] == pytest.approx(m["rk"] * m["ik"], rel=1e-12)


def test_bias_is_fixed_for_the_run():
    with pytest.raises(NotImplementedError):
        Stage(V.FS).swap_bias(0.25)


@pytest.mark.parametrize("model, mode, bias", REFS)
def test_analog_response_is_what_ngspice_computes(model, mode, bias):
    """circuit.analog_response at the DK op point reproduces reference (ii)'s .ac (to its 7-decimal rounding), with
    and without the g1 grid current (its small-signal conductance at rest is part of the 3x4 Jacobian)."""
    st = V.stage(model, bias=bias, mode=mode)
    r = V.ref(model, bias, mode)
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


def test_t_grid_clipping_at_8v_is_not_even_dominant():
    """#112, a model prediction reported, not a pass criterion: with grid current, T at 8 V clips on the grid from
    0.45 mA (g1 clamped near 0 V), so H3 comes within ~2 dB of H2 (0.45 mA: H2 -22.6, H3 -24.5 dBc; 0.5 mA: -20.4 /
    -21.6), against the manuals' "mainly even harmonics" for T; at 0.35 mA and below, and at 2 V everywhere, T stays
    even by over 10 dB (results_grid_T.json; dk6as6/RESULTS.md, "Grid current (#112)")."""
    g = json.load(open(V.results_file("T", "dualg")))
    for b in ("0.45", "0.5"):
        h = g[b]["standard"]["sine"]["8"]["H_dBc"]
        assert 0.0 < h[1] - h[2] < 3.0, (b, h)
    for b in ("0.1", "0.25", "0.35"):
        h = g[b]["standard"]["sine"]["8"]["H_dBc"]
        assert h[1] > h[2] + 10, (b, h)


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
    """Recovery tau only where the reference defines one (validate.py: none without a bypass cap or grid current)."""
    b = res["vs"][res["model"]]["burst"]
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
