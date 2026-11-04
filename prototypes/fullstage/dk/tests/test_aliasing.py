"""Aliasing limit per quality setting (#17): 10 kHz at 8 V. Limits are the figures measured in
experiments/aliasing/ALIASING.md and quoted in #9 / #17: 16x -74.8 dBc (variant F), 8x -59.7 dBc (the old 511-tap
Kaiser, variant A; the #14 guard), 4x with the small-cap pre-warp -44.1 dBc. The settings use the FIR resampler;
the IIR option is held to the standard limit as in #14 (at 16x / 4x it measures 0.05 / 0.02 dB above the FIR-based
limits, see dk/RESULTS.md).
Metric as in ALIASING.md: total non-harmonic energy 20 Hz-20 kHz relative to the fundamental, 1 s Hann window,
harmonic bins +-3 masked. The window starts after 0.25 s of settling, aligned by the reported latency.
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage

FS = 48000
LIMIT_DBC = {"hq": -74.8, "standard": -59.7, "eco": -44.1}


def alias_dbc(y, f, n=FS):
    P = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(n))) ** 2
    fb = np.arange(len(P))
    harm = np.zeros(len(P), bool)
    for k in range(1, 40):
        if k * f < FS / 2:
            harm[max(k * f - 3, 0):k * f + 4] = True
    mask = (fb >= 20) & (fb <= 20000) & ~harm
    return 10 * np.log10(P[mask].sum() / P[f - 3:f + 4].sum())


@pytest.mark.parametrize("quality,kind", [(q, "fir") for q in LIMIT_DBC] + [("standard", "iir")])
def test_alias_10kHz_8V_within_setting_limit(quality, kind):
    st = Stage(FS, quality=quality, filt=kind)
    settle, L = FS // 4, st.latency_samples
    x = 8.0 * np.sin(2 * np.pi * 10000 * np.arange(settle + FS + L) / FS)
    y = st.process(x)["out"][settle + L:settle + L + FS]
    a = alias_dbc(y, 10000)
    print(f"{quality}/{kind}: {a:.2f} dBc")
    assert a <= LIMIT_DBC[quality]
