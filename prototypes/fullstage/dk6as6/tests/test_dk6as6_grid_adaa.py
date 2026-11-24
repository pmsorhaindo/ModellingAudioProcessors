"""Independent integral, Jacobian, state and offline renderer checks for #154."""
import hashlib
from pathlib import Path

import numpy as np
import pytest
from scipy.integrate import quad

from dk6as6 import grid_adaa, inaudible, pentode
from dk6as6.grid_adaa_measure import build_screen, render
from dk6as6.lpf import Stage


def test_criterion_matches_pr_146_and_152():
    source = Path(inaudible.__file__).read_bytes()
    assert hashlib.sha256(source).hexdigest() == "ec60840272522a4016b6899f9d80cef0859ad25dba0499bd31533ea16a6e24ed"


@pytest.fixture(scope="module")
def raw_table():
    return grid_adaa.load_line(0, *(pentode.GRID[k] for k in ("GG", "XI", "CG")))


@pytest.mark.parametrize("order", (1, 2))
@pytest.mark.parametrize("q,p,o", [(-0.15, -0.6, 0.8), (0.37, 0.37, -0.5), (0.37, -0.5, 0.37),
                                  (0.37, 0.3703, 0.3704), (-0.19, -0.189, -0.191), (0.45, 0.45, 0.45)])
def test_divided_difference_matches_branch_integral(raw_table, order, q, p, o):
    branch = grid_adaa.GridFilter(raw_table, order, o)
    branch.advance(p)

    def line(t, derivative):
        def integrand(u):
            blend = u*p + (1-u)*o if order == 2 else p
            value = pentode.grid_current(t*q + (1-t)*blend)[derivative]
            return t * value if derivative else value

        if order == 1:
            return integrand(0)
        return 2*(1-t)*quad(integrand, 0, 1, epsabs=1e-14)[0]

    expected = [quad(line, 0, 1, args=(k,), epsabs=1e-14)[0] for k in (0, 1)]
    value, derivative = branch.evaluate(q)
    np.testing.assert_allclose((value, derivative), expected, rtol=2e-6, atol=2e-13)


@pytest.mark.parametrize("order", (1, 2))
def test_reduced_jacobian_and_conservation(order):
    s = grid_adaa.Stage(48000, bias=0.5, mode="T", order=order)
    s.filter.advance(-0.3)
    s.filter.advance(0.1)
    q = [2.0, 80.0, 80.0, 81.0]
    voltage, ip, ig2, ig1, jac = s._port_law(q)
    assert ip + ig2 + ig1 == pytest.approx(pentode.cathode_share(*voltage, s.par)[0], rel=2e-15)
    np.testing.assert_allclose(np.array(voltage) + np.array(s.K)[:, 2]*ig1, q, rtol=0, atol=2e-12)
    h = 1e-4
    basis = np.eye(4) * h
    forward = np.array([s._port_law(q + delta)[1:3] for delta in basis])
    backward = np.array([s._port_law(q - delta)[1:3] for delta in basis])
    np.testing.assert_allclose((forward - backward).T/(2*h), jac, rtol=2e-5, atol=2e-12)


def test_split_without_adaa_matches_original_solver():
    x = 8*np.sin(2*np.pi*19037*np.arange(1200)/(48000*8))
    options = dict(bias=0.5, mode="T")
    split = grid_adaa.Stage(48000, order=0, **options)
    record = ("out", "vgk", "ig1")
    reference = Stage(48000, **options).process_internal(x, record)
    result = split.process_internal(x, record)
    for key, tol in (("out", 2e-8), ("vgk", 2e-9), ("ig1", 5e-14)):
        np.testing.assert_allclose(result[key], reference[key], atol=tol, rtol=0)
    assert split.nonconv == split.out_of_law == 0


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    path = tmp_path_factory.mktemp("grid_adaa") / "grid_adaa.so"
    build_screen(path)
    return path


@pytest.mark.parametrize("fs,order,mode,bias,tone", [(44100, 1, "T", 0.5, 19437), (48000, 2, "T", 0.5, 19037),
                                                     (48000, 1, "P1", 0.6, 19997), (44100, 2, "P1", 0.6, 19037)])
def test_offline_render_matches_streaming_python(native, fs, order, mode, bias, tone):
    signal = 8*np.sin(2*np.pi*tone*np.arange(420)/fs)
    s = grid_adaa.Stage(fs, mode=mode, bias=bias, order=order)
    rec = ("out", "vgk", "vg2k")
    full = s.process(signal, rec)
    s.reset()
    chunks = [s.process(block, rec) for block in np.array_split(signal, 7)]
    y, info, vgk, vg2k = render(native, fs, 8, order, mode, bias, signal, voltages=True)
    assert info["latency"] == s.latency_samples
    assert info["nonconverged"] == s.nonconv == 0
    assert info["out_of_law"] == s.out_of_law
    for key, reference in zip(rec, (y, vgk, vg2k)):
        channel = (lambda r: r[key]) if key == "out" else (lambda r: r["_internal"][key])
        np.testing.assert_allclose(np.concatenate([channel(p) for p in chunks]), channel(full), atol=2e-9, rtol=0)
        np.testing.assert_allclose(channel(full), reference, atol=8e-8, rtol=0)


@pytest.mark.parametrize("order", (1, 2))
def test_exact_dc_is_a_fixed_point(order):
    s = grid_adaa.Stage(48000, order=order, bias=0.5, mode="T")
    rec = s.process_internal(np.zeros(64), ("out", "vgk", "ig1"))
    np.testing.assert_allclose(rec["vgk"], s.dc["vg1k"], atol=2e-12, rtol=0)
    np.testing.assert_allclose(rec["ig1"], s.dc["ig1"], atol=2e-20, rtol=0)
    np.testing.assert_allclose(s.x, 0, atol=2e-12, rtol=0)
