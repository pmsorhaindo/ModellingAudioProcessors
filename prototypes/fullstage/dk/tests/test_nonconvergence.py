"""Non-convergence is explicit (#7): the DC solve raises, runtime failures are counted, warned and keep a
finite best iterate (the C++-core contract in dk/solver.py). Failures are forced through the solver settings
(iteration cap, zero tolerance) or a bad start, never through circuit values.
Run from prototypes/fullstage:  python -m pytest -q dk/tests/test_nonconvergence.py
"""
import math, os, subprocess, sys, warnings
import numpy as np
import pytest
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
from dk import circuit, solver
from dk.stage import Stage, OperatingPointError, NonConvergenceWarning

FS = 48000.0


def sine(a, n=480, f=1000.0):
    return a * np.sin(2 * np.pi * f * np.arange(n) / FS)


def resid(p, K, v):
    f0, f1, _, _, _ = solver._resid(p[0], p[1], K, v[0], v[1], solver.DZ_12AX7_RSD1)
    return max(abs(f0), abs(f1))


@pytest.fixture(scope="module")
def rest():
    """The runtime 2-port problem at rest (zero input), from a converged stage."""
    s = Stage(FS)
    p = s.m["G"] @ s.x + s.m["H"] @ np.array([0.0, circuit.VALUES["HT"]])
    return p, s.K, (s.dc["vgk"], s.dc["vak"])


# ---- (a) DC operating point
def test_dc_nonconvergence_raises():
    with pytest.raises(OperatingPointError, match="DC operating point did not converge"):
        Stage(FS, newton=dict(max_it=1))


def test_dc_nonconvergence_raises_under_python_O():
    """The old check was an assert, which -O strips."""
    code = "from dk.stage import Stage; Stage(48000.0, newton=dict(max_it=1))"
    r = subprocess.run([sys.executable, "-O", "-c", code], cwd=HERE, capture_output=True, text=True)
    assert r.returncode != 0 and "OperatingPointError" in r.stderr, r.stderr


def test_unknown_newton_option_is_rejected():
    with pytest.raises(TypeError, match="max_iter"):
        Stage(FS, newton=dict(max_iter=1))


# ---- solver fallback: best finite iterate, flagged
def test_solver_returns_best_iterate_on_failure(rest):
    p, K, _ = rest
    start = (2.0, 50.0)                      # bad start: grid well forward-biased, plate far from rest
    r0, prev = resid(p, K, start), math.inf
    for k in range(1, 6):
        v0, v1, ig, ia, it, ok = solver.solve(p[0], p[1], K, *start, max_it=k, tol_v=0.0, tol_f=0.0)
        assert not ok and it == k
        assert all(map(math.isfinite, (v0, v1, ig, ia)))
        r = resid(p, K, (v0, v1))
        assert r <= r0 and r <= prev         # never worse than the start or than a shorter run
        prev = r


def test_solver_nan_iterate_never_wins(rest, monkeypatch):
    """If every iterate after the start evaluates to NaN, the (finite) start is returned, flagged."""
    p, K, v = rest
    start = (v[0] + 0.5, v[1] - 5.0)
    calls, real = [0], solver._resid

    def nan_after_first(*a):
        calls[0] += 1
        return real(*a) if calls[0] == 1 else (math.nan, math.nan) + real(*a)[2:]
    monkeypatch.setattr(solver, "_resid", nan_after_first)
    monkeypatch.setattr(solver, "_resid_only", lambda *a: (math.nan, math.nan, math.nan, math.nan))
    v0, v1, ig, ia, it, ok = solver.solve(p[0], p[1], K, *start, max_it=5)
    assert not ok and (v0, v1) == start and math.isfinite(ig) and math.isfinite(ia)


# ---- (b) runtime: forced non-convergence is reported and the state stays finite
def test_forced_runtime_nonconvergence_is_reported_and_finite():
    s = Stage(FS)
    s.newton = (1, 0.0, 0.0)                 # (max_it, tol_v, tol_f): zero tolerance, one iteration
    with pytest.warns(NonConvergenceWarning, match="did not converge"):
        y = s.process(sine(4.0))["out"]
    n = 480 * s.os
    st = s.iteration_stats()
    assert s.nonconv == s.nonconv_block == st["nonconverged"] == st["samples"] == n
    assert st["converged"] is False
    assert np.isfinite(y).all() and np.isfinite(s.x).all() and np.isfinite(s.v).all()

    # back to the default settings: it carries on from the kept iterate and converges again
    s.newton = (solver.MAX_IT, solver.TOL_V, solver.TOL_F)
    with warnings.catch_warnings():
        warnings.simplefilter("error", NonConvergenceWarning)
        y = s.process(sine(4.0))["out"]
    assert s.nonconv_block == 0 and s.nonconv == n
    assert np.isfinite(y).all()


def test_bad_runtime_start_is_flagged_and_finite():
    s = Stage(FS)
    s.v = s.v_prev = [3.0, 20.0]             # inject a bad warm start
    s.newton = (1, solver.TOL_V, solver.TOL_F)
    with pytest.warns(NonConvergenceWarning):
        s.process_internal(np.zeros(1))
    assert s.nonconv == 1 and np.isfinite(s.v).all() and np.isfinite(s.x).all()


def test_reset_clears_the_report():
    s = Stage(FS)
    s.newton = (1, 0.0, 0.0)
    with pytest.warns(NonConvergenceWarning):
        s.process(sine(1.0, n=48))
    s.reset()
    assert s.nonconv == s.nonconv_block == 0 and s.iteration_stats()["converged"]


# ---- (c) happy path
@pytest.mark.parametrize("amp", [0.05, 2.0, 8.0])
def test_default_settings_converge_and_report_zero(amp):
    s = Stage(FS)
    with warnings.catch_warnings():
        warnings.simplefilter("error", NonConvergenceWarning)
        y = s.process(sine(amp))["out"]
    st = s.iteration_stats()
    assert st["nonconverged"] == 0 and st["converged"] is True and s.nonconv_block == 0
    assert st["max"] < solver.MAX_IT and np.isfinite(y).all()
