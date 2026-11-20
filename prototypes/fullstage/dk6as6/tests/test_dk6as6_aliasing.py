"""Aliasing of the #102 6AS6 stage at standard 8x / FIR / 48 kHz (dk6as6/aliasing.py; the 12AX7 stage's metric).
Two separate checks (#111 review):
  regression  the worst tone of the full sweep in the short CI window, against LIMIT_DBC (that sweep's worst + 3 dB),
              per Distortion-switch mode: P1 at 19037 Hz (its worst tone at every bias and drive), T and P2 at
              CI_CASES_MODES. These baselines are chosen from the measurements, so they show reproducibility only.
  quality     every SUPPORTED setting (stage.SUPPORTED_MA) within QUALITY_GATE_DBC, the shipped 12AX7 stage's
              committed budget, which no 6AS6 result chose.
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
from dk6as6.stage import supported, SUPPORTED_MA

# Experimental (unsupported) settings are run on purpose here; their ExperimentalWarning is expected.
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


CI_CASES = [("P1", 0.25, 8.0, 19037), ("P1", A.BIAS_MA, 2.0, 19037), ("P1", A.BIAS_MA, 8.0, 19037),
            ("P1", 0.6, 8.0, 19037)] + A.CI_CASES_MODES


@pytest.mark.parametrize("mode, bias, drive, tone", CI_CASES)
def test_worst_tone_within_limit(mode, bias, drive, tone):
    r = A.measure(drive, tone, bias=bias, window=A.CI_WINDOW_S, mode=mode)
    assert r["total_dbc"] <= A.LIMIT_DBC[mode][bias, drive], r


@pytest.mark.parametrize("mode", list(A.MODES))
def test_limits_are_the_full_sweep_plus_margin(mode):
    """LIMIT_DBC[mode] is the committed sweep's worst per (bias, drive) + MARGIN_DB, rounded up to 0.1 dB, for every
    (bias, drive) of the sweep."""
    rec = json.load(open(A.json_file(mode)))
    lims = A.LIMIT_DBC[mode]
    assert set(lims) == {(r["bias_ma"], r["drive"]) for r in rec}
    for (b, d), lim in lims.items():
        w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == d)
        assert lim == math.ceil((w + MARGIN_DB) * 10) / 10, (mode, b, d, w, lim)


@pytest.mark.parametrize("mode, bias, drive, tone", A.CI_CASES_MODES)
def test_ci_tone_is_the_sweeps_worst(mode, bias, drive, tone):
    rec = [r for r in json.load(open(A.json_file(mode))) if r["bias_ma"] == bias and r["drive"] == drive]
    assert max(rec, key=lambda r: r["total_dbc"])["tone"] == tone


def test_quality_gate_is_the_12ax7_stages_budget():
    """The gate is not derived from any 6AS6 result: it is dk/aliasing.LIMIT_DBC for standard / FIR at 48 kHz."""
    assert A.QUALITY_GATE_DBC is LIMIT_DBC_12AX7[("standard", "fir")] and set(A.QUALITY_GATE_DBC) == {2.0, 8.0}


@pytest.mark.parametrize("mode", list(A.MODES))
def test_every_supported_setting_meets_the_quality_gate(mode):
    """Every supported (mode, bias) of the committed full sweeps is within the gate at both drives; the T 0.35 mA top of
    the envelope is in the sweep."""
    rec = json.load(open(A.json_file(mode)))
    biases = {r["bias_ma"] for r in rec}
    assert all(b in biases for b in SUPPORTED_MA.get(mode, ()))
    for b in biases:
        for d, gate in A.QUALITY_GATE_DBC.items():
            w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == d)
            if supported(mode, b):
                assert w <= gate, (mode, b, d, w, gate)


def test_p2_fails_the_quality_gate():
    """Why P2 is experimental only: at 2 V its resampler aliasing is above the gate at every current."""
    rec = json.load(open(A.json_file("P2")))
    for b in {r["bias_ma"] for r in rec}:
        w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == 2.0)
        assert w > A.QUALITY_GATE_DBC[2.0], (b, w)
