"""Representative aliasing checks for the cut-down 2x IIR stage (#23).

Verifies that CutdownStage works cleanly inside dk.aliasing's coherent offset-tone harness,
that 2x internal folding dominates over the 6-coef IIR resampler floor, and that aliasing at
19037 Hz / 8 V matches the expected 2x degradation relative to 4x eco.
"""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutdown.stage import CutdownStage  # noqa: E402 -- after sys.path setup
from dk import aliasing as AL  # noqa: E402 -- after sys.path setup

FS = AL.FS


def measure_short(drive, tone):
    n = int(round(AL.CI_WINDOW_S * FS))
    n0 = int(round(AL.CI_SETTLE_S * FS))
    m = AL.tone_bin(tone, n, FS)
    st = CutdownStage(FS)
    L = st.latency_samples
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return AL.alias_dbc(y, m, FS, st.os)


@pytest.mark.parametrize("drive,tone,lo,hi", [
    (2.0, 1037, -120.0, -90.0),
    (2.0, 19037, -45.0, -25.0),
    (8.0, 10037, -30.0, -15.0),
    (8.0, 19037, -20.0, -5.0),
])
def test_representative_aliasing_levels(drive, tone, lo, hi):
    r = measure_short(drive, tone)
    assert lo < r["total_dbc"] < hi, (drive, tone, r)
    assert r["internal_dbc"] > r["resampler_dbc"] + 20.0
