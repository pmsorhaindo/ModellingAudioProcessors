"""End-to-end small-signal response through Stage.process() (issue #3).

test_small_signal_within_0p1dB_20Hz_20kHz derives the response from the DK matrices and the FIR response,
so a bug in the per-sample loop or in the resampling would not show there. This test pushes a real
multitone through process() at each quality setting (#17) and measures what comes out:
  * gain vs the committed ngspice AC reference (same DZ valve law), within CRIT["ac_db"] (0.1 dB);
  * gain and phase vs the exact linearisation of the discretised chain (stage and resampler; the ngspice JSON
    stores no phase). The phase catches a latency / group-delay error in the resampler that magnitude alone
    would miss; the gain, to LIN_DB, a per-sample loop that departs from the matrices the analysis uses (#38).
Tones sit on exact FFT bins of a 0.1 s window (rectangular window, no leakage) at 1 mV each, so the stage is
linear to well under 0.01 dB: second-order intermodulation of the multitone lands on some tone bins (e.g.
20 + 20 = 40 Hz, 8 + 12 = 20 kHz) and moves them by up to 0.0008 dB, proportional to the level (#38; 1e-4 dB at
0.1 mV). The window starts after 0.3 s of settling (coupling caps, ~33 ms slowest). The output is shifted back
by the latency the resampler reports (#14), not by a hard-coded trim, so a wrong latency_samples shows up as a
phase error that grows with frequency.
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage, QUALITY
from dk import validate as V
from dk import linear

FS = V.FS
WIN_S, SETTLE_S, AMP = 0.1, 0.3, 1e-3
TONES = np.array([20, 40, 80, 150, 300, 600, 1000, 2000, 4000, 8000, 12000, 16000, 18000, 20000.0])
PHASE_DEG = 0.5
LIN_DB = 0.002      # measured vs linearised chain: the 1 mV intermodulation above (<= 0.0008 dB), 2.5x margin


def linear_chain(stage, f):
    """Exact small-signal response of the discretised chain, without the resampler's pure delay (the linear-phase
    FIR is removed by shifting the output by latency_samples): stage x |resampler|."""
    return linear.stage_response(stage, f) * np.abs(stage.rs.chain_response(f))


@pytest.fixture(scope="module", params=list(QUALITY))
def measured(request):
    n0, N = int(SETTLE_S * FS), int(WIN_S * FS)
    st = Stage(FS, quality=request.param)
    t = np.arange(n0 + N + st.latency_samples) / FS
    ph = np.random.default_rng(0).uniform(0, 2 * np.pi, len(TONES))   # random phases: low crest factor
    x = sum(AMP * np.sin(2 * np.pi * f * t + p) for f, p in zip(TONES, ph))
    y = V.aligned(st, x)["out"]
    k = np.rint(TONES * WIN_S).astype(int)
    Hm = np.fft.rfft(y[n0:n0 + N])[k] / np.fft.rfft(x[n0:n0 + N])[k]
    return st, Hm


def test_e2e_gain_matches_ngspice_within_0p1dB(measured):
    _, Hm = measured
    r = V.ref("dz")["ac"]
    ref_db = np.interp(np.log(TONES), np.log(r["f"]), r["db"])     # 50 pts/decade + band edges, smooth curve
    dev = 20 * np.log10(np.abs(Hm)) - ref_db
    assert np.max(np.abs(dev)) < V.CRIT["ac_db"], dict(zip(TONES, np.round(dev, 4)))


def test_e2e_gain_matches_linearised_chain(measured):
    st, Hm = measured
    dev = 20 * np.log10(np.abs(Hm / linear_chain(st, TONES)))
    assert np.max(np.abs(dev)) < LIN_DB, dict(zip(TONES, np.round(dev, 5)))


def test_e2e_phase_matches_linearised_stage(measured):
    st, Hm = measured
    dev = np.degrees(np.angle(Hm / linear_chain(st, TONES)))
    assert np.max(np.abs(dev)) < PHASE_DEG, dict(zip(TONES, np.round(dev, 4)))
