import numpy as np
import pytest

from adaptive_os.run import WARM_S, render, run
from cpp.build import build, compiler
from dk.resample import Resampler
from dk6as6.stage import fir_design


@pytest.fixture(scope="module")
def executable():
    if compiler() is None:
        pytest.skip("no C++ compiler")
    return build(name="adaptive_os_6as6")


@pytest.mark.parametrize("fs", [44100, 48000])
def test_warm_handoff_and_crossfade(executable, fs):
    half = round(fs * WARM_S)
    n = np.arange(12 * half)
    audio = 8 * np.sin(2 * np.pi * 1037 * n / fs)
    bias = np.full_like(audio, 0.349)
    # A canceled warm-up, a complete transfer up and a complete transfer down.
    bias[half:half + half // 2] = 0.35
    bias[3 * half:7 * half] = 0.35
    timeline = np.column_stack((audio, bias, np.ones_like(audio), np.zeros_like(audio)))
    y, latency, _ = render(executable, fs, "RSD-1", "off", timeline)
    expected_latency = max(Resampler(fs, f, "fir", fir=fir_design("k140", fs)).latency_samples for f in (16, 32))
    assert latency == expected_latency

    blend = y["fade"][:, 1]
    hard = y["handoff"][:, 1]
    for method in ("fade", "handoff"):
        np.testing.assert_array_equal(y[method][:3 * half, 0], y["fixed16"][:3 * half, 0])
        np.testing.assert_array_equal(y[method][:4 * half, 1], 0)
    np.testing.assert_array_equal(hard[4 * half:8 * half], 1)
    np.testing.assert_array_equal(hard[8 * half:], 0)
    np.testing.assert_allclose(blend[4 * half:5 * half], np.arange(1, half + 1) / half, atol=0, rtol=0)
    np.testing.assert_allclose(blend[8 * half:9 * half], 1 - np.arange(1, half + 1) / half, atol=0, rtol=0)
    np.testing.assert_array_equal(blend[5 * half:8 * half], 1)
    np.testing.assert_array_equal(blend[9 * half:], 0)


def test_crossfade_reverses_mid_mix(executable):
    fs = 48000
    half = round(fs * WARM_S)
    n = np.arange(6 * half)
    audio = 8 * np.sin(2 * np.pi * 1037 * n / fs)
    bias = np.full_like(audio, 0.349)
    bias[half:2 * half + half // 2] = 0.35
    timeline = np.column_stack((audio, bias, np.ones_like(audio), np.zeros_like(audio)))
    y, _, _ = render(executable, fs, "RSD-1", "off", timeline)
    blend = y["fade"][:, 1]
    first_up = 2 * half
    first_down = first_up + half // 2
    end = first_down + half // 2
    np.testing.assert_allclose(blend[first_up:first_down], np.arange(1, half // 2 + 1) / half)
    np.testing.assert_allclose(blend[first_down:end], np.arange(half // 2 - 1, -1, -1) / half)
    np.testing.assert_array_equal(y["fade"][end:, 0], y["fixed16"][end:, 0])
    np.testing.assert_array_equal(y["fade"][end:, 2], 0)


@pytest.mark.parametrize("fs", [44100, 48000])
@pytest.mark.parametrize("factor", [32, 64])
def test_extended_resampler_impulse(executable, fs, factor):
    raw, _ = run(executable, "resample-impulse", fs, factor)
    frames = raw.reshape(512, factor + 1)
    impulse = np.zeros(len(frames))
    impulse[32] = 1.0
    ref = Resampler(fs, factor, "fir", fir=fir_design("k140", fs))
    up = ref.up(impulse)
    np.testing.assert_allclose(frames[:, :-1].reshape(-1), up, rtol=0, atol=2e-14)
    np.testing.assert_allclose(frames[:, -1], ref.down(up), rtol=0, atol=2e-14)
    assert np.argmax(frames[:, -1]) == 32 + ref.latency_samples


def test_extended_pair_holds_incumbent_until_warm(executable):
    fs = 48000
    warm_s = 0.030
    warm = round(fs * warm_s)
    fade = round(fs * WARM_S)
    n = np.arange(8 * fade)
    audio = 8 * np.sin(2 * np.pi * 1037 * n / fs)
    bias = np.full_like(audio, 0.349)
    bias[fade:2 * fade] = 0.351  # The long fade cancels this short request.
    bias[3 * fade:] = 0.351
    timeline = np.column_stack((audio, bias, np.ones_like(audio), np.zeros_like(audio)))
    y, latency, _ = render(executable, fs, "RSD-1", "off", timeline, high=64, long_warm=warm_s)
    assert latency == Resampler(fs, 64, "fir", fir=fir_design("k140", fs)).latency_samples
    audible = 3 * fade + warm
    np.testing.assert_array_equal(y["long_fade"][:audible, 0], y["fixed16"][:audible, 0])
    np.testing.assert_array_equal(y["long_fade"][:audible, 1], 0)
    np.testing.assert_allclose(y["long_fade"][audible:audible + fade, 1], np.arange(1, fade + 1) / fade)
    np.testing.assert_array_equal(y["long_fade"][audible + fade:, 1], 1)
