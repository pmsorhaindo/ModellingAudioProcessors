"""The grid-current path of the 6AS6 DK stage (#112): the 3-current Newton solve with restarts and its non-convergence
contract, and the runtime guard of the grid law (PR #128 review). Short live runs; no references needed.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import math
import os
import sys
import warnings
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6 import solver as S, stage as stg, envelope as E, validate as V
from dk6as6.stage import Stage, OutOfLawWarning, NonConvergenceWarning, QUALITY_RECORDS
from dk6as6.pentode import currents_grid

FS = 48000.0
# The solver-path tests below were calibrated at 8x (counts of rescued samples, the 120 ms burst): they test the solver
# and the recovery measure, not a quality, so they keep the factor they were written for (#113 rework).
Q8 = QUALITY_RECORDS["standard"]
pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")


def _captured(monkeypatch, st, x, poison=None):
    """Run st.process_internal(x), recording every solve_grid_restarts call (args, result); poison replaces the first
    start (the extrapolated warm start)."""
    calls, orig = [], stg.solve_grid_restarts
    def wrap(p, K, starts, *a):
        if poison is not None:
            starts = (poison,) + tuple(starts[1:])
        r = orig(p, K, starts, *a)
        calls.append((p, K, starts, a, r))
        return r
    monkeypatch.setattr(stg, "solve_grid_restarts", wrap)
    out = st.process_internal(np.asarray(x, float), record=("out", "vgk", "vak", "ipk"))
    return calls, out


def test_restart_rescues_a_failed_start(monkeypatch):
    """Live: T at 0.5 mA (its top before the #113 rework; experimental since), an 8 V 19 kHz tone at the internal rate,
    with every sample's first start poisoned
    (1 A in each current) and an 8-iteration cap per start, so on many samples the first start fails and a restart
    (from the previous sample's currents, then the operating point) must do the work. Every sample converges; each
    rescued sample's accepted iterate is a root (its residual under TOL_F) that agrees with an independent long solve
    from zero current; the output matches the unpoisoned run."""
    st0 = Stage(FS, bias=0.5, mode="T", quality=Q8, experimental=True)
    x = 8.0 * np.sin(2 * np.pi * 19037.0 * np.arange(800) / st0.fs)
    ref = st0.process_internal(x, record=("out",))["out"]
    st = Stage(FS, bias=0.5, mode="T", quality=Q8, experimental=True)
    st.newton = (8,) + st.newton[1:]                    # after the DC solve
    calls, out = _captured(monkeypatch, st, x, poison=[1.0, 1.0, 1.0])
    rescued = [c for c in calls if c[4][5] > 1]
    assert st.nonconv == 0 and st.restarted == len(rescued) > 100
    for p, K, starts, a, (v, i, it, ok, fv, attempts) in rescued[::10]:
        assert ok and fv < S.TOL_F
        v2, i2, _, ok2, _ = S.solve_grid(p, K, [0.0, 0.0, 0.0], st.par, st.grid, 200)
        assert ok2 and max(abs(a_ - b_) for a_, b_ in zip(v, v2)) < 1e-9, (v, v2)
    assert np.max(np.abs(out["out"] - ref)) < 1e-6 * np.max(np.abs(ref))


def test_failed_samples_keep_the_best_candidate_over_every_start(monkeypatch):
    """The non-convergence contract with restarts (PR #128 review's case): T 0.5 mA, the iteration cap lowered to one
    after the DC solve, three internal samples [8, 8, -8] V. Where no start converges, the stage keeps the candidate
    with the smallest finite residual over all three starts (re-solved here one by one) and counts the sample."""
    st = Stage(FS, bias=0.5, mode="T", quality=Q8, experimental=True)
    st.newton = (1,) + st.newton[1:]
    with pytest.warns(NonConvergenceWarning), warnings.catch_warnings():
        warnings.simplefilter("ignore", OutOfLawWarning)    # an unconverged candidate may be outside the law
        calls, _ = _captured(monkeypatch, st, [8.0, 8.0, -8.0])
    failed = [c for c in calls if not c[4][3]]
    assert failed and st.nonconv == len(failed)
    for p, K, starts, a, (v, i, it, ok, fv, attempts) in failed:
        each = [S.solve_grid(p, K, s0, *a) for s0 in starts]
        res = [r[4] for r in each]
        assert attempts == 3 and fv == min(res) and math.isfinite(fv)
        assert v == each[int(np.argmin(res))][0]
        ip, ig2, ig1, _ = currents_grid(*v, st.par, st.grid)
        assert list(i) == [ip, ig2, ig1]                 # the currents are the law at the kept voltages


def test_singular_or_overflowing_steps_end_an_attempt_cleanly():
    """solve_grid never raises or returns NaN on a wild start: an overflowing or singular attempt reports
    converged=False with the best finite iterate (or an infinite residual), so the restarts can run."""
    st = Stage(FS, bias=0.5, mode="T", quality=Q8, experimental=True)
    p = (st.m["G"] @ st.x + st.p_dc).tolist()
    for start in ([1e30, -1e30, 1e30], [math.inf, 0.0, 0.0], [1.0, 1.0, 1.0]):
        v, i, it, ok, fv = S.solve_grid(p, st.K, start, st.par, st.grid, 5)
        assert fv == math.inf or (math.isfinite(fv) and all(math.isfinite(x) for x in v)), (start, v, fv)


def test_grid_guard_stays_quiet_on_a_positive_g1_inside_the_law():
    """With grid current a positive g1 is modelled: T 0.5 mA (its top before the #113 rework), the 8 V 20 Hz step onset
    (envelope.py) takes g1
    to about +0.16 V with the plate above the cathode and Ip + Ig2 > 0, and the guard counts nothing and warns
    nothing."""
    st = Stage(FS, bias=0.5, mode="T", quality=Q8, experimental=True)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        r = st.process(E.stimulus(20, "cos", st.latency_samples), record=("vgk", "vak", "ipk"))["_internal"]
    assert r["vgk"].max() > 0.1 and r["vak"].min() > 0.0 and r["ipk"].min() > 0.0
    assert st.out_of_law == 0 and not [x for x in w if issubclass(x.category, OutOfLawWarning)]


def test_grid_guard_counts_every_sample_outside_the_grid_law():
    """And outside it (T 0.6 mA, experimental, a 60 V 1 kHz drive, far outside the supported 8 V class): the plate is
    driven below the cathode, the guard's count equals an independent count of the samples with Vak < 0 or
    Ip + Ig2 < 0, and OutOfLawWarning is emitted."""
    st = Stage(FS, bias=0.6, mode="T", quality=Q8, experimental=True)
    x = 60.0 * np.sin(2 * np.pi * 1000.0 * np.arange(int(0.02 * FS)) / FS)
    with pytest.warns(OutOfLawWarning, match="grid-current law's limit"):
        r = st.process(x, record=("vak", "ipk"))["_internal"]
    count = int(np.sum((r["vak"] < 0.0) | (r["ipk"] < 0.0)))
    assert count > 0 and st.out_of_law == count


def test_burst_recovery_measure_is_not_the_padded_trace_end():
    """#124 / PR #128 review: the shared burst_metrics crossing tau read the zero-padded end of the 120 ms trace. At T
    0.5 mA with grid current the same DK burst captured for 250 ms crosses 1/e (full-window cycle-mean samples, from
    31 ms) at ~106 ms, where the 120 ms trace's padded crossing says 89 ms. The tail tau that CRIT now compares (a fit
    over 31-118 ms of the 120 ms trajectory) agrees with that observed crossing to 1 %; the padded one does not."""
    from analysis import cycle_mean
    b120, _ = V.burst(Q8, model="dualg", bias=0.5, mode="T")
    b250, _ = V.burst(Q8, model="dualg", bias=0.5, mode="T", t_stop=0.25, raw=True)
    t, vg, fs = b250["raw"]
    n = int(round(fs / 1000.0))
    cm = cycle_mean(vg, fs, 1000.0)
    full = (t >= V.TAIL_T0) & (t <= t[-1] - n / fs)                    # whole one-cycle windows only
    dev = cm[full] - b250["rest_vgk"]
    j = np.nonzero(dev / dev[0] < np.exp(-1))[0][0]
    observed = t[full][j] - t[full][0]
    assert 0.10 < observed < 0.11, observed
    assert abs(V.tail_tau(b120) / observed - 1) < 0.01, (V.tail_tau(b120), observed)
    assert np.isnan(V.cross_tau(b120))                                 # not observable within the 120 ms trace
    assert abs(b120["recovery_tau_s"] / observed - 1) > 0.1           # the padded crossing (89 ms) is the artefact
