"""The supported envelopes of the 6AS6 DK stage, live (#111, PR #121 review; #112): envelope.py's worst stimuli at
both ends of every supported range, with and without grid current, the runtime guard, and the committed envelope
records. Its own CI shard (PR #128 review, #127).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import json
import os
import sys
import warnings
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6.stage import (Stage, QUALITY, SUPPORTED_MA, SUPPORTED_MA_NO_GRID, envelope,  # noqa: F401
                           OutOfLawWarning)
from dk6as6 import envelope as E, validate as V
from dk6as6.pentode import HARDNESS
from dk.validate import aligned

pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


# Live re-checks of both supported envelopes (with and without grid current) at both ends of every range, with
# envelope.py's worst stimuli, at both qualities; envelope[_grid].json has every stimulus and
# test_envelope_record_is_the_supported_range checks them.
STAYS = [(m, b, f, ph, gh, q) for gh in (HARDNESS, 0.0) for m, ends in envelope(gh).items() for b in ends
         for f, ph in ((100, "cos"), (20, "cos"), (18000, "sin")) for q in QUALITY]


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


@pytest.mark.parametrize("quality", QUALITY)
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
