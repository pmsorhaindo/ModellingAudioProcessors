"""End-to-end small-signal response through CutdownStage.process() (#23).

Pushes a 1 mV multitone (20 Hz - 20 kHz) through CutdownStage.process() and checks that the
measured gain and phase match the linearised 3-state LUT chain (cutdown.linear.stage_response *
|resampler|), verifying the per-sample LUT loop, 3-state matrices, LUT Jacobian, and reported
IIR latency_samples. Note that for IIR half-band resampling the phase near 20 kHz is non-linear
(unlike the linear-phase FIR), so phase is compared against the full complex chain_response
shifted back by latency_samples.
"""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutdown import linear  # noqa: E402 -- after sys.path setup
from cutdown.stage import CutdownStage, PRESETS  # noqa: E402 -- after sys.path setup
from cutdown.validate import FS, aligned  # noqa: E402 -- after sys.path setup

WIN_S, SETTLE_S, AMP = 0.1, 0.3, 1e-3
TONES = np.array([20, 40, 80, 150, 300, 600, 1000, 2000, 4000, 8000, 12000, 16000, 18000, 20000.0])
LIN_DB = 0.01
PHASE_DEG = 1.0


def linear_chain_shifted(stage, f):
    """Exact complex linearised chain response with the integer latency_samples shift removed."""
    L = stage.latency_samples
    return linear.stage_response(stage, f) * stage.rs.chain_response(f) * np.exp(2j * np.pi * f * L / stage.fs_base)


VARIANTS = [pytest.param(dict(preset=name), id=name) for name in PRESETS]


@pytest.fixture(scope="module", params=VARIANTS)
def measured(request):
    n0, N = int(SETTLE_S * FS), int(WIN_S * FS)
    st = CutdownStage(FS, **request.param)
    t = np.arange(n0 + N + st.latency_samples) / FS
    ph = np.random.default_rng(0).uniform(0, 2 * np.pi, len(TONES))
    x = sum(AMP * np.sin(2 * np.pi * f * t + p) for f, p in zip(TONES, ph))
    y = aligned(st, x)["out"]
    k = np.rint(TONES * WIN_S).astype(int)
    Hm = np.fft.rfft(y[n0:n0 + N])[k] / np.fft.rfft(x[n0:n0 + N])[k]
    return st, Hm


def test_e2e_gain_matches_linearised_chain(measured):
    st, Hm = measured
    dev = 20 * np.log10(np.abs(Hm / linear_chain_shifted(st, TONES)))
    assert np.max(np.abs(dev)) < LIN_DB, dict(zip(TONES, np.round(dev, 5)))


def test_e2e_phase_matches_linearised_chain(measured):
    st, Hm = measured
    dev = np.degrees(np.angle(Hm / linear_chain_shifted(st, TONES)))
    assert np.max(np.abs(dev)) < PHASE_DEG, dict(zip(TONES, np.round(dev, 4)))
