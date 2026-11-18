"""Streaming half-band resamplers (issue #14): filter specs, latency, and block-size invariance.
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.resample import Resampler, hiir_coefs
from dk.stage import Stage, QUALITY

FS = 48000.0
PASS_DB, STOP_DB = 0.01, 85.0      # STOP_DB: ALIASING.md's half-band variants were measured at ~84-86 dB
KINDS, FACTORS = ("fir", "iir"), (2, 4, 8, 16)


def test_hiir_coefficients_match_published_output():
    # HIIR v1.20 output for compute_coefs_spec_order_tbw(coefs, 8, 2*20/44100), as posted with its source in
    # https://dsp.stackexchange.com/questions/37411 (interleaved: even index = +90 deg path, odd = reference).
    ref = [0.16177741706363166219, 0.47944111608296202665, 0.73306690130335572242, 0.87624358989504858020,
           0.94536301966806279840, 0.97660296916871658368, 0.99060051416704042460, 0.99749940412203375040]
    assert np.allclose(hiir_coefs(8, 2 * 20 / 44100), ref, rtol=0, atol=1e-12)


def measured_up_response(rs, n=1 << 15):
    """Interpolator response from an impulse through the streaming code (not the analytic formula)."""
    x = np.zeros(n)
    x[0] = 1.0
    y = rs.up(x) / rs.os
    return np.fft.rfftfreq(len(y), 1 / (rs.fs * rs.os)), np.abs(np.fft.rfft(y))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("os_", FACTORS)
def test_passband_flat_within_0p01dB_to_20kHz(kind, os_):
    rs = Resampler(FS, os_, kind)
    f, H = measured_up_response(rs)
    up_db = 20 * np.log10(H[f <= 20000])
    assert np.max(np.abs(up_db)) < PASS_DB / 2
    chain = 20 * np.log10(np.abs(rs.chain_response(np.linspace(1, 20000, 2000))))
    assert np.max(np.abs(chain)) < PASS_DB


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("os_", FACTORS)
def test_rejects_every_band_that_folds_onto_0_20kHz(kind, os_):
    """Images of 0-20 kHz (interpolation) and the bands that alias onto it (decimation) are k*fs +- 20 kHz."""
    rs = Resampler(FS, os_, kind)
    f, H = measured_up_response(rs)
    fold = (f >= FS - 20000) & (np.abs((f + FS / 2) % FS - FS / 2) <= 20000)
    assert 20 * np.log10(H[fold].max()) < -STOP_DB


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("f_in", [28000.0, 47000.0, 70000.0, 100000.0])
def test_decimator_rejects_aliasing_tones(kind, f_in):
    rs = Resampler(FS, 8, kind)
    n = 8 * 16384
    t = np.arange(n) / (8 * FS)
    y = rs.down(np.sin(2 * np.pi * f_in * t))[4096:]
    assert 20 * np.log10(np.sqrt(2 * np.mean(y ** 2))) < -STOP_DB


@pytest.mark.parametrize("os_", FACTORS)
def test_fir_latency_is_exact(os_):
    """A base-rate impulse comes back as a symmetric pulse centred exactly latency_samples later."""
    rs = Resampler(FS, os_, "fir")
    x = np.zeros(512)
    x[100] = 1.0
    y = rs.down(rs.up(x))
    c = 100 + rs.latency_samples
    assert np.argmax(np.abs(y)) == c
    assert np.allclose(y[c - 40:c], y[c + 40:c:-1], atol=1e-12)
    assert rs.latency_ms == pytest.approx(1e3 * rs.latency_samples / FS)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("quality", QUALITY)
def test_stage_reports_its_impulse_delay(quality, kind):
    """Each setting's latency_samples is the delay of an impulse through its streaming resampler: the 1 kHz group
    delay of the measured response (fir: exactly, and the pulse peaks there; iir: rounded, as it is defined)."""
    st = Stage(FS, quality=quality, filt=kind)
    x = np.zeros(4800)
    x[100] = 1.0
    y = st.rs.down(st.rs.up(x))
    ph = np.unwrap(np.angle(np.fft.rfft(y)))
    k = 100                                              # 1 kHz bin of a 0.1 s window
    gd = -(ph[k + 1] - ph[k - 1]) / (2 * 2 * np.pi / len(y)) - 100
    L = st.latency_samples
    if kind == "fir":
        assert gd == pytest.approx(L, abs=1e-6) and np.argmax(np.abs(y)) == 100 + L
    else:
        assert round(gd) == L


@pytest.mark.parametrize("kind", KINDS)
def test_resampler_blocks_equal_whole(kind):
    x = np.random.default_rng(3).standard_normal(3000)
    whole = Resampler(FS, 8, kind)
    ref = whole.down(whole.up(x) ** 3)
    rs = Resampler(FS, 8, kind)
    out, i, sizes = [], 0, np.random.default_rng(4).integers(1, 300, 100)
    for n in np.r_[1, 7, 1, sizes]:
        b = x[i:i + n]
        i += len(b)
        out.append(rs.down(rs.up(b) ** 3))
    assert i == len(x)
    assert np.allclose(np.concatenate(out), ref, rtol=0, atol=1e-12)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("quality", QUALITY)
def test_stage_blocks_equal_whole(quality, kind):
    """Stage.process on random block sizes (including 1 and 7) == one call on the whole buffer: no clicks."""
    t = np.arange(2400) / FS
    x = 3.0 * np.sin(2 * np.pi * 997 * t) + 0.5 * np.random.default_rng(5).standard_normal(len(t))
    ref = Stage(FS, quality=quality, filt=kind).process(x, record=("out", "vgk"))
    st = Stage(FS, quality=quality, filt=kind)
    sizes = np.r_[1, 7, 1, 7, np.random.default_rng(6).integers(1, 257, 200)]
    out, vg, i = [], [], 0
    for n in sizes:
        if i >= len(x):
            break
        r = st.process(x[i:i + n], record=("out", "vgk"))
        i += min(n, len(x) - i)
        out.append(r["out"])
        vg.append(r["vgk"])
    assert i == len(x)
    scale = np.max(np.abs(ref["out"]))
    assert np.max(np.abs(np.concatenate(out) - ref["out"])) < 1e-9 * scale
    assert np.max(np.abs(np.concatenate(vg) - ref["vgk"])) < 1e-9


def test_reset_restores_initial_state():
    st = Stage(FS)
    x = np.sin(2 * np.pi * 500 * np.arange(500) / FS)
    a = st.process(x)["out"]
    st.process(np.ones(100))
    st.reset()
    assert np.array_equal(st.process(x)["out"], a)
