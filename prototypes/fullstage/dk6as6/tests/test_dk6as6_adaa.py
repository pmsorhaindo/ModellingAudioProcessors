"""State-space integration and research-renderer parity for issue #136."""
from pathlib import Path
import subprocess

import numpy as np
import pytest

from cpp import build
from dk6as6 import adaa
from dk6as6.adaa_measure import render


@pytest.mark.parametrize("order", [1, 2])
def test_fixed_point_and_streaming(order):
    stage = adaa.Stage(44100, adaa=order, quality=(4, None), mode="T", bias=0.5)
    rest = stage.process_internal(np.zeros(32), ("out", "vgk", "ig1"))
    np.testing.assert_allclose(rest["out"], stage.y_dc[0], atol=1e-10, rtol=0)
    np.testing.assert_allclose(rest["vgk"], stage.dc["vg1k"], atol=1e-10, rtol=0)
    np.testing.assert_allclose(rest["ig1"], stage.dc["ig1"], atol=1e-15, rtol=0)

    x = 8 * np.sin(2 * np.pi * 19997 * np.arange(384) / stage.fs)
    record = ("out", "vgk", "ig1")
    stage.reset()
    whole = stage.process_internal(x, record)
    stage.reset()
    parts = [stage.process_internal(y, record) for y in np.split(x, [0, 1, 63, 233])]
    for k in record:
        np.testing.assert_array_equal(whole[k], np.concatenate([p[k] for p in parts]))
    assert stage.nonconv == stage.out_of_law == 0


@pytest.mark.parametrize("order", [1, 2])
def test_small_signal_matches_linearized_feedback(order):
    stage = adaa.Stage(48000, adaa=order, quality=(4, None))
    length, bin_ = 1024, 101
    wave = np.exp(2j * np.pi * bin_ * np.arange(4 * length) / length)
    amplitude = 1e-4
    y = stage.process_internal(amplitude * wave.real)["out"]
    gain = 2 * np.mean(y[-length:] * wave[-length:].conj()) / amplitude
    assert gain == pytest.approx(stage.response(bin_ * stage.fs / length)[0], rel=2e-6)


@pytest.fixture(scope="module")
def native_lib(tmp_path_factory):
    compiler = build.compiler()
    if compiler is None:
        pytest.skip("no native C++ compiler")
    source = Path(adaa.__file__).with_name("adaa_screen.cpp")
    out = tmp_path_factory.mktemp("adaa") / "adaa-screen.so"
    subprocess.run([compiler, *build.CXXFLAGS, "-O2", "-ffp-contract=off",
                    "-shared", "-fPIC", str(source), "-o", str(out)], check=True)
    return str(out)


@pytest.mark.parametrize("order,nodes,mode,bias,hardness", [
    (1, 2, "P1", 0.6, 1.0), (1, 4, "T", 0.5, 1.0),
    (2, 4, "T", 0.5, 1.0), (2, 8, "T", 0.25, 0.0),
])
def test_offline_renderer_matches_python(native_lib, order, nodes, mode, bias, hardness):
    fs, os = 44100, 4
    st = adaa.Stage(fs, adaa=order, nodes=nodes, quality=(os, None), mode=mode,
                    bias=bias, grid_hardness=hardness)
    x = 8 * np.sin(2 * np.pi * 19997 * np.arange(640) / fs)
    expected = st.process(x, ("out", "vgk", "vg2k"))
    y, info, vgk, vg2k = render(native_lib, fs, os, order, nodes, mode, bias, x,
                                hardness=hardness, voltages=True)
    assert info["latency"] == st.latency_samples
    assert info["nonconverged"] == info["out_of_law"] == st.nonconv == st.out_of_law == 0
    np.testing.assert_allclose(y, expected["out"], atol=2e-7, rtol=0)
    np.testing.assert_allclose(vgk, expected["_internal"]["vgk"], atol=2e-7, rtol=0)
    np.testing.assert_allclose(vg2k, expected["_internal"]["vg2k"], atol=2e-7, rtol=0)
