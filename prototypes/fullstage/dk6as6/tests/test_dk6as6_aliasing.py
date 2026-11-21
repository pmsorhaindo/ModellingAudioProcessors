"""Aliasing of the #102 6AS6 stage at standard 8x / FIR / 48 kHz (dk6as6/aliasing.py; the 12AX7 stage's metric).
Two separate checks (#111 review):
  regression  the worst tone of the full sweep in the short CI window, against LIMIT_DBC (that sweep's worst + 3 dB),
              per Distortion-switch mode: without grid current (the #111 records) T and P2 at CI_CASES_MODES; with
              the default g1 grid current (#112) P1 and T at CI_CASES_GRID, against LIMIT_DBC_GRID. These baselines
              are chosen from the measurements, so they show reproducibility only.
  quality     every SUPPORTED setting (stage.envelope of the sweep's grid hardness) within QUALITY_GATE_DBC, the
              shipped 12AX7 stage's committed budget, which no 6AS6 result chose.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import json
import math
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.aliasing import MARGIN_DB, LIMIT_DBC as LIMIT_DBC_12AX7
from dk6as6 import aliasing as A
from dk6as6.stage import supported, envelope
from dk6as6.pentode import HARDNESS

# Experimental (unsupported) settings are run on purpose here; their ExperimentalWarning is expected.
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


CI_CASES = ([(m, b, d, f, 0.0) for m, b, d, f in A.CI_CASES_MODES]
            + [(m, b, d, f, HARDNESS) for m, b, d, f in A.CI_CASES_GRID])
SWEEPS = [(m, False) for m in A.MODES] + [(m, True) for m in A.GRID_MODES]     # (mode, with grid current)


@pytest.mark.parametrize("mode, bias, drive, tone, hardness", CI_CASES)
def test_worst_tone_within_limit(mode, bias, drive, tone, hardness):
    r = A.measure(drive, tone, bias=bias, window=A.CI_WINDOW_S, mode=mode, grid_hardness=hardness)
    assert r["total_dbc"] <= (A.LIMIT_DBC_GRID if hardness else A.LIMIT_DBC)[mode][bias, drive], r


@pytest.mark.parametrize("mode, grid", SWEEPS)
def test_limits_are_the_full_sweep_plus_margin(mode, grid):
    """LIMIT_DBC[_GRID][mode] is the committed sweep's worst per (bias, drive) + MARGIN_DB, rounded up to 0.1 dB, for
    every (bias, drive) of the sweep."""
    rec = json.load(open(A.json_file(mode, grid)))
    assert all(bool(r.get("grid_hardness")) == grid for r in rec)
    lims = (A.LIMIT_DBC_GRID if grid else A.LIMIT_DBC)[mode]
    assert set(lims) == {(r["bias_ma"], r["drive"]) for r in rec}
    for (b, d), lim in lims.items():
        w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == d)
        assert lim == math.ceil((w + MARGIN_DB) * 10) / 10, (mode, b, d, w, lim)


@pytest.mark.parametrize("mode, bias, drive, tone, hardness", CI_CASES)
def test_ci_tone_is_the_sweeps_worst(mode, bias, drive, tone, hardness):
    rec = [r for r in json.load(open(A.json_file(mode, bool(hardness))))
           if r["bias_ma"] == bias and r["drive"] == drive]
    assert max(rec, key=lambda r: r["total_dbc"])["tone"] == tone


def test_quality_gate_is_the_12ax7_stages_budget():
    """The gate is not derived from any 6AS6 result: it is dk/aliasing.LIMIT_DBC for standard / FIR at 48 kHz."""
    assert A.QUALITY_GATE_DBC is LIMIT_DBC_12AX7[("standard", "fir")] and set(A.QUALITY_GATE_DBC) == {2.0, 8.0}


@pytest.mark.parametrize("mode, grid", SWEEPS)
def test_every_supported_setting_meets_the_quality_gate(mode, grid):
    """Every supported (mode, bias) of the committed full sweeps (of the sweep's grid hardness) is within the gate at
    both drives; the ends of each supported range are in the sweep."""
    rec = json.load(open(A.json_file(mode, grid)))
    gh = HARDNESS if grid else 0.0
    biases = {r["bias_ma"] for r in rec}
    assert all(b in biases for b in envelope(gh).get(mode, ()))
    for b in biases:
        for d, gate in A.QUALITY_GATE_DBC.items():
            w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == d)
            if supported(mode, b, gh):
                assert w <= gate, (mode, b, d, w, gate)
    if grid:        # the grid sweeps record Newton: no supported setting may leave a sample unconverged
        assert all(r["nonconverged"] == 0 for r in rec if supported(mode, r["bias_ma"], gh))


def test_p2_fails_the_quality_gate():
    """Why P2 is experimental only: at 2 V its resampler aliasing is above the gate at every current."""
    rec = json.load(open(A.json_file("P2")))
    for b in {r["bias_ma"] for r in rec}:
        w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == 2.0)
        assert w > A.QUALITY_GATE_DBC[2.0], (b, w)


def test_t_above_its_grid_envelope_fails_the_quality_gate():
    """Why T stops at 0.5 mA with grid current (#112): at 0.55 and 0.6 mA g1 rests within ~1.3 V of the cathode, and a
    2 V drive's grid-current onset aliases above the gate at 8x (internal aliasing); at 0.5 mA it is within it."""
    rec = json.load(open(A.json_file("T", True)))
    def worst(b):
        return max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == 2.0)
    assert worst(0.5) <= A.QUALITY_GATE_DBC[2.0]
    for b in (0.55, 0.6):
        assert worst(b) > A.QUALITY_GATE_DBC[2.0] and not supported("T", b), (b, worst(b))
