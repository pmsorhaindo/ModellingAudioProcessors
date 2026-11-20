"""Aliasing of the #102 6AS6 stage at standard 8x / FIR / 48 kHz (dk6as6/aliasing.py; the 12AX7 stage's metric).
The worst tone of the full sweep in the short CI window, against LIMIT_DBC, per Distortion-switch mode (#111): P1 at
19037 Hz (its worst tone at every bias and drive), T and P2 at CI_CASES.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import json
import math
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.aliasing import MARGIN_DB
from dk6as6 import aliasing as A


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
