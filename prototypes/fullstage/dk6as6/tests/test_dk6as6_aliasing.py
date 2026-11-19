"""Aliasing of the #102 6AS6 stage at standard 8x / FIR / 48 kHz (dk6as6/aliasing.py; the 12AX7 stage's metric).
The worst tone of the full sweep (19037 Hz at every bias and drive) in the short CI window, against LIMIT_DBC.
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


@pytest.mark.parametrize("bias, drive", [(0.25, 8.0), (A.BIAS_MA, 2.0), (A.BIAS_MA, 8.0)])
def test_worst_tone_within_limit(bias, drive):
    r = A.measure(drive, 19037, bias=bias, window=A.CI_WINDOW_S)
    assert r["total_dbc"] <= A.LIMIT_DBC[bias, drive], r


def test_limits_are_the_full_sweep_plus_margin():
    """LIMIT_DBC is the committed sweep's worst per (bias, drive) + MARGIN_DB, rounded up to 0.1 dB."""
    rec = json.load(open(A.JSON))
    for (b, d), lim in A.LIMIT_DBC.items():
        w = max(r["total_dbc"] for r in rec if r["bias_ma"] == b and r["drive"] == d)
        assert lim == math.ceil((w + MARGIN_DB) * 10) / 10, (b, d, w, lim)
