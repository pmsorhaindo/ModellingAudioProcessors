import numpy as np
import pytest

from adaptive_os.run import WARM_S, render
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
    bias[half:half + half // 2] = 0.351
    bias[3 * half:7 * half] = 0.351
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
    bias[half:2 * half + half // 2] = 0.351
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
