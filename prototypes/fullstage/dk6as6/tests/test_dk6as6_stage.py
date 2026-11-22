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
                           UnsupportedSettingError, ExperimentalWarning, QUALITY_RECORDS, SUPPORTED_MA_RECORDS,
                           supported_records)
from dk6as6.pentode import HARDNESS
from dk.validate import aligned
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validation_checks import (test_mode_harmonic_character, test_newton_always_converges,  # noqa: E402,F401
                               test_operating_point_within_0p1pct, test_small_signal_within_0p1dB_20Hz_20kHz,
                               test_harmonics_H2_H5_within_0p1dB_above_m90dBc, test_burst_bias_shift_and_recovery,
                               test_all_passed)
from dk6as6 import validate as V, circuit, linear
from spice6as6.run_refs import BIASES, MODE_BIASES, GRID_BIASES

C = V.CRIT

# Experimental (unsupported) settings are run on purpose here; their ExperimentalWarning is expected.
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


# Live validation cases, split over two CI shards (PR #128 review, #127): the checks are validation_checks.py; this
# file runs the default stage (dualg) at standard at every P1 grid reference bias and the no-grid stage
# (grid_hardness=0, the #111 stage bit for bit) end to end at P1's default bias; test_dk6as6_validation.py runs hq and
# T. test_p1_grid_current_is_negligible ties P1 0.6 mA to the default stage. Every mode at every bias, standard and hq,
# is in results[_grid][_<mode>].json (python -m dk6as6.validate [--grid]), checked by
# test_committed_validation_every_supported_setting_passes.
CASES = [("P1", "standard", b, "dualg") for b in GRID_BIASES["P1"]] + [("P1", "standard", BIAS_MA, "dual")]


@pytest.fixture(scope="module", params=CASES, ids=str)
def res(request):
    m, q, b, model = request.param
    return V.run(quality=q, bias=b, mode=m, model=model)


def test_criteria_are_the_12ax7_stages():
    from dk.validate import CRIT
    assert C is CRIT and C == dict(op_rel=0.001, ac_db=0.1, ac_band=(20.0, 20000.0), harm_db=0.1,
                                   harm_floor_dbc=-90.0, burst_min_rel=0.005, burst_tau_rel=0.01, burst_traj_rel=0.005)
    assert QUALITY == {"standard": (16, None), "hq": (32, None)}          # the #113 rework (proposed)
    assert QUALITY_RECORDS == {"standard": (8, None), "hq": (16, None)}  # every record made before it


def test_default_fir_is_113s_k140_and_records_keep_their_stage():
    """#113 rework: the default resampler is k140, the table resampler_study derives (Kaiser beta 14.47, 123 / 219
    first-stage taps at 48 / 44.1 kHz, 47 later); record_stage() is the stage of every earlier record (shipped FIR,
    standard 8x / hq 16x by name), and supported_records() its envelope (T to 0.5 mA)."""
    from dk6as6.resampler_study import CANDIDATES
    from dk6as6.stage import K140, FIR, FIR_RECORDS, record_stage, fir_design
    from dk.resample import FIR_FIRST, first_fir_taps
    assert FIR == "k140" and FIR_RECORDS == "shipped"
    for fs in (48000, 44100):
        assert K140[float(fs)] == CANDIDATES["k140"][fs] == fir_design("k140", fs)
        assert fir_design("shipped", fs) is None
        st = Stage(fs, bias=0.25)
        assert st.fir == "k140" and st.os == 16 and st.rs.stages[0].h.size == K140[float(fs)][0][0]
        assert st.rs.stages[1].h.size == 47
        for q, f in (("standard", 8), ("hq", 16)):
            r = record_stage(fs, quality=q, bias=0.25)
            assert r.fir == "shipped" and r.os == f and r.rs.stages[0].h.size == first_fir_taps(fs)
    assert FIR_FIRST[1] == 9.0 and Stage(48000, bias=0.25, quality="hq").os == 32
    with pytest.raises(ValueError):
        Stage(48000, fir="k150")
    assert supported_records("T", 0.5) and not supported("T", 0.5) and supported("T", 0.35)
    assert not supported_records("T", 0.55) and supported_records("T", 0.3, 0.0) == supported("T", 0.3, 0.0)


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
    ("T", 0.375, HARDNESS), ("T", 0.5, HARDNESS), ("T", 0.55, HARDNESS), ("T", 0.6, HARDNESS),
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
    """With the default grid current (#112) P1 is supported over the documented 0.1-0.6 mA and T to 0.35 mA since the
    #113 rework (above, an 8 V drive's aliasing fails the inaudibility criterion at standard; 0.5 mA before, under the
    dBc gate: SUPPORTED_MA_RECORDS); without it (hardness 0, the #111 stage) T only to 0.3 mA; P2 and other hardnesses
    nowhere."""
    assert SUPPORTED_MA == {"P1": (0.1, 0.6), "T": (0.1, 0.35)} and "P2" not in SUPPORTED_MA
    assert SUPPORTED_MA_RECORDS == {"P1": (0.1, 0.6), "T": (0.1, 0.5)}
    assert SUPPORTED_MA_NO_GRID == {"P1": (0.1, 0.6), "T": (0.1, 0.3)}
    assert envelope() is envelope(HARDNESS) is SUPPORTED_MA and envelope(0.0) is SUPPORTED_MA_NO_GRID
    assert envelope(0.5) == {} and not supported("P1", BIAS_MA, 0.5)
    for gh, env in ((HARDNESS, SUPPORTED_MA), (0.0, SUPPORTED_MA_NO_GRID)):
        for mode, (lo, hi) in env.items():
            for b in (lo, hi):
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    assert Stage(V.FS, bias=b, mode=mode, grid_hardness=gh).supported and supported(mode, b, gh)


def test_status_never_counts_a_non_pass():
    """validate.status: a criterion that is not compared (None) makes a case inconclusive, never passed."""
    ok = dict(op=True, ac_stage=True, ac_chain=True, harmonics=True, burst=True, newton=True)
    assert V.status(dict(passed=ok)) == "passed"
    assert V.status(dict(passed=dict(ok, burst=None))) == "inconclusive"
    assert V.status(dict(passed=dict(ok, harmonics=None, burst=False))) == "failed"


@pytest.mark.parametrize("model, mode", [("dual", m) for m in MODE_BIASES] + [("dualg", m) for m in GRID_BIASES])
def test_committed_validation_every_supported_setting_passes(model, mode):
    """results[_grid][_<mode>].json: every supported setting (of that model's grid hardness) passes every criterion;
    every other one is marked unsupported and reported as whatever it is (no grid: T 0.45 / 0.6 mA inconclusive), never
    as a pass."""
    res = json.load(open(V.results_file(mode, model)))
    gh = V.HARDNESS[model]
    assert set(res) == {f"{b:g}" for b in V.biases_of(model, mode)}
    for b, rb in res.items():
        for q in QUALITY:
            r = rb[q]
            # the records' flags are the envelope they were made under (stage.supported_records)
            assert r.get("supported", True) == supported_records(mode, float(b), gh), (model, mode, b, q)
            if supported_records(mode, float(b), gh):
                assert V.status(r) == "passed", (model, mode, b, q, r["passed"])
    if mode == "T" and model == "dual":
        assert {V.status(res[b]["standard"]) for b in ("0.45", "0.6")} == {"inconclusive"}
    if mode == "P2":
        assert all(V.status(res[b][q]) == "passed" for b in res for q in QUALITY)   # #124 resolved: 0.6 mA too


@pytest.fixture(scope="module")
def p2_06_burst():
    """The P2 0.6 mA burst against its reference (validate.burst_compare, without the sines)."""
    b, _ = V.burst(bias=0.6, mode="P2")
    return V.burst_compare(b, V.ref("dual", 0.6, "P2")["burst"])


def test_p2_0p6mA_burst_passes_crit_with_the_full_window_recovery_measure(p2_06_burst):
    """#124, resolved for the 6AS6 stage (#112): P2 0.6 mA (experimental) failed CRIT's recovery tau (52.5 % off)
    because the shared metric started on the 30.5 ms sample, whose cycle mean straddles the burst's end. With the
    full-window tail tau (validate.burst_compare) it passes with the rest of the burst criteria (0.03 %), and the
    padded crossing it replaced is still recorded and still disagrees."""
    b = p2_06_burst
    assert V.burst_ok(b) and b["tau_tested"] and b["tau_rel"] < 1e-3, b
    pt = b["padded_tau_s"]
    assert abs(pt["dk"] / pt["ref"] - 1) > 0.4, pt


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
