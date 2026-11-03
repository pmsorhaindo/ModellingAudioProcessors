import os
import numpy as np
import pytest
from valvelab.models import KorenPentode
from valvelab.stage import Stage
from valvelab import antialias as AA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def tab():
    s = Stage(KorenPentode("EF86")); s.set_bias_va(0.55 * s.B)
    return AA.Table(s.transfer, xmax=2.5, m=4097), s


def test_table_matches_direct_solve(tab):
    t, s = tab
    x = np.linspace(-2.4, 2.4, 3001)
    assert np.max(np.abs(t.f(x) - s.transfer(x))) < 1e-3     # volts, on a ~200 V swing


def test_antiderivative_is_consistent(tab):
    t, _ = tab
    x = np.linspace(-2.4, 2.4, 501); h = 1e-6
    assert np.allclose((t.F(x + h) - t.F(x - h)) / (2 * h), t.f(x), atol=1e-4)


def test_adaa_matches_naive_at_low_frequency(tab):
    t, _ = tab
    fs, f = 48000.0, 50.0
    x = 2.0 * np.sin(2 * np.pi * f * np.arange(4800) / fs)
    ya, yn = AA.adaa1(t, x), AA.naive(t, x)
    mid = AA.naive(t, 0.5 * (x + np.concatenate([[x[0]], x[:-1]])))
    peak = np.max(np.abs(yn))
    assert np.max(np.abs(ya - mid)) / peak < 1e-4     # ADAA1 ~ f(midpoint): half-sample delay
    assert np.max(np.abs(ya - yn)) / peak < 1e-2      # and close to naive at 50 Hz


def test_oversampling_and_adaa_reduce_aliasing(tab):
    t, _ = tab
    fs, n = 48000.0, 1 << 14
    f0 = AA.coherent_freq(5000.0, fs, n)
    x = 2.0 * np.sin(2 * np.pi * f0 * np.arange(n + 4096) / fs)
    a = {k: AA.alias_metric(y[2048:2048 + n], fs, f0)["alias_to_harm_dB"] for k, y in
         [("naive", AA.naive(t, x)), ("adaa", AA.adaa1(t, x)), ("os4", AA.oversampled(t, x, 4))]}
    assert a["adaa"] < a["naive"] - 3
    assert a["os4"] < a["naive"] - 30


def test_c_bench_matches_python_if_built():
    out = os.path.join(ROOT, "bench", "out_0.f32")
    if not os.path.exists(out):
        pytest.skip("run ./bench/adaa_bench --dump first")
    m, xmax, a, f0 = open(os.path.join(ROOT, "bench", "table_meta.txt")).read().split()
    tf = np.fromfile(os.path.join(ROOT, "bench", "table_f32.bin"), np.float32).astype(float)
    xs = np.linspace(-float(xmax), float(xmax), int(m))
    x = (float(a) * np.sin(2 * np.pi * float(f0) * np.arange(4096) / 48000.0)).astype(np.float32)
    yc = np.fromfile(out, np.float32)[:4096]
    assert np.max(np.abs(yc - np.interp(x, xs, tf))) < 1e-3
