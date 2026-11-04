"""Aliasing guard for the resampler change (#14): 10 kHz at 8 V, default 8x, must stay at or below the
-59.7 dBc measured for the old 511-tap Kaiser filter (experiments/aliasing/ALIASING.md, variant A).
Metric as in ALIASING.md: total non-harmonic energy 20 Hz-20 kHz relative to the fundamental, 1 s Hann window,
harmonic bins +-3 masked. The window starts after 0.25 s of settling, aligned by the reported latency.
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage

FS = 48000
LIMIT_DBC = -59.7


def alias_dbc(y, f, n=FS):
    P = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(n))) ** 2
    fb = np.arange(len(P))
    harm = np.zeros(len(P), bool)
    for k in range(1, 40):
        if k * f < FS / 2:
            harm[max(k * f - 3, 0):k * f + 4] = True
    mask = (fb >= 20) & (fb <= 20000) & ~harm
    return 10 * np.log10(P[mask].sum() / P[f - 3:f + 4].sum())


@pytest.mark.parametrize("kind", ["fir", "iir"])
def test_alias_10kHz_8V_no_worse_than_before(kind):
    st = Stage(FS, filt=kind)
    settle, L = FS // 4, st.latency_samples
    x = 8.0 * np.sin(2 * np.pi * 10000 * np.arange(settle + FS + L) / FS)
    y = st.process(x)["out"][settle + L:settle + L + FS]
    assert alias_dbc(y, 10000) <= LIMIT_DBC
