"""Small-signal error budget (#38). Matrix computations only (no time stepping).

The 20 Hz-20 kHz small-signal check (validate.CRIT["ac_db"], 0.1 dB) has the least headroom of every criterion
(~1.9x at standard). These tests pin down where its error comes from (dk/RESULTS.md, "Small-signal error budget";
dk/budget.py):
  * the reference covers the band edges, where the error peaks;
  * the analog small-signal response of the netlist (circuit.analog_response) is what ngspice's .ac computed,
    so the reference contributes nothing beyond its 7-decimal rounding;
  * the discretised stage is exactly the analog stage at the bilinear-warped frequency (fs/pi) tan(pi f/fs):
    its error is the trapezoidal warping alone, it falls as 1/fs^2, and extrapolated to fs -> inf it vanishes;
  * the budget's sources add up to the measured deviation, and everything but the warping is < 0.0015 dB;
  * the resampler is counted once (validate.small_signal squared chain_response before #38).
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage, QUALITY
from dk import validate as V
from dk import linear, budget

FS = V.FS
band = budget.band


def test_reference_has_the_band_edges():
    f, _ = band()
    assert f[0] == V.CRIT["ac_band"][0] and f[-1] == V.CRIT["ac_band"][1]


def test_analog_response_is_the_ngspice_ac_reference():
    r = V.ref("dz")["ac"]
    st = Stage(FS)
    dev = 20 * np.log10(np.abs(linear.analog_response(st, r["f"]))) - np.array(r["db"])
    assert np.max(np.abs(dev)) < 1e-6          # 1 Hz-100 kHz; the JSON holds 7 decimals (measured 7e-8 dB)


@pytest.mark.parametrize("os_", [2, 4, 8, 16])
def test_stage_is_the_bilinear_warped_analog_response(os_):
    """Every capacitor is trapezoidal at the same step, so the linearised stage is the bilinear transform of the
    analog one: H_d(f) = H_a((fs/pi) tan(pi f/fs)) (measured to 4e-9 dB, 1e-8 deg)."""
    f = np.geomspace(20.0, 20000.0, 61)
    st = Stage(FS, quality=(os_, None))
    ratio = linear.stage_response(st, f) / linear.analog_response(st, st.fs / np.pi * np.tan(np.pi * f / st.fs))
    assert np.max(np.abs(20 * np.log10(np.abs(ratio)))) < 1e-7
    assert np.max(np.abs(np.degrees(np.angle(ratio)))) < 1e-6


def test_stage_error_is_trapezoidal_warping():
    """Error at 20 kHz falls 4x per doubling of the internal rate, and the 1/fs^2 extrapolation leaves nothing."""
    f, db = band()
    err = {os_: V.small_signal(Stage(FS, quality=(os_, None)), f)[0] - db for os_ in (8, 16, 64, 128)}
    assert err[8][-1] / err[16][-1] == pytest.approx(4.0, rel=0.05)
    assert err[64][-1] / err[128][-1] == pytest.approx(4.0, rel=0.001)
    assert np.max(np.abs((4 * err[128] - err[64]) / 3)) < 1e-5


@pytest.mark.parametrize("filt", ["fir", "iir"])
@pytest.mark.parametrize("quality", list(QUALITY))
def test_budget_adds_up_and_warping_dominates(quality, filt):
    _, o = budget.rows(quality, filt)
    assert np.max(np.abs(sum(o[s] for s in budget.SOURCES) - o["total"])) < 1e-9
    rest = o["total"] - o["warping"]       # reference rounding + op point + resampler ripple
    assert np.max(np.abs(rest)) < 1.5e-3   # measured 1.1e-3 dB worst (hq FIR ripple); the warping is 0.013-0.054


@pytest.mark.parametrize("filt", ["fir", "iir"])
def test_chain_counts_the_resampler_once(filt):
    f, _ = band()
    st = Stage(FS, filt=filt)
    stage_db, chain_db = V.small_signal(st, f)
    assert np.allclose(chain_db - stage_db, 20 * np.log10(np.abs(st.rs.chain_response(f))), rtol=0, atol=1e-12)
