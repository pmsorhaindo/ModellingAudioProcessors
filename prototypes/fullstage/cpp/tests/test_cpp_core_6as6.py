"""The C++ 6AS6 core (#104, cpp/dk6as6_core.h) against the Python 6AS6 DK stage. Needs a C++17 compiler ($CXX, else
g++, c++ or clang++): skipped without one, except under CI (CI set), where a missing compiler fails.
  test_core_6as6_vectors       cpp/test_core_6as6.cpp on cpp/vectors_6as6.txt, -O2: every C++ test, including no
                               allocation in process(), FTZ/DAZ scoping, block-size invariance, the #7 / #112 fallbacks
  test_core_6as6_no_contract   the same with -ffp-contract=off (the valve law and the solvers then equal vectors written
                               with the same libm bit for bit, reported as *_bit_equal_frac; test_bit_exact_* asserts
                               bits against the live Python functions)
  test_core_6as6_fma           the same with -O2 -march=x86-64-v3 (FMA contraction; x86-64 only)
  test_bit_exact_*             the valve law (including where Python raises) and the three solvers against the live
                               Python functions on this machine, bit for bit, in a -O2 -ffp-contract=off build
  test_vectors_6as6_fresh      the committed cpp/vectors_6as6.txt still matches what the Python stage produces now
  test_k140_derivation         the k140 FIR lengths and beta re-derived from #113's rule (cpp/dk_core.h K140_FIR)
  test_validate_k140_6as6      the same 44 settings with the core's default resampler (k140): every supported setting
                               passes every CRIT criterion against the ngspice references
  test_validate_reproduced_6as6  dk6as6.validate.run on the C++ core reproduces every committed dk6as6/results*.json
                               (44 settings: both models, every mode and reference current, standard and hq) within
                               validate_cpp_6as6.LIMITS; every criterion reads as Python's; supported settings pass
Run from prototypes/fullstage:  python -m pytest -q cpp/tests/test_cpp_core_6as6.py
"""
import math
import os
import platform
import signal
import subprocess
import sys
import json
import warnings
import numpy as np
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from cpp import build as B, export_vectors_6as6 as EV, validate_cpp_6as6 as VC  # noqa: E402 -- after sys.path
from dk.resample import _FirHB, PASS_HZ  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode, solver  # noqa: E402 -- after the sys.path setup

VECTORS = os.path.join(ROOT, "cpp", "vectors_6as6.txt")
X86 = platform.machine().lower() in ("x86_64", "amd64")


def _exe(flags=("-O2",), defines=()):
    if B.compiler() is None:
        if os.environ.get("CI"):
            pytest.fail("no C++ compiler on CI (g++ expected on the runner)")
        pytest.skip("no C++ compiler (set CXX)")
    return B.build(flags, defines, "test_core_6as6")


def _run_vectors(exe):
    p = subprocess.run([exe, VECTORS], capture_output=True, text=True)
    if p.returncode == -signal.SIGILL:
        pytest.skip("this CPU cannot run the build")
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 0 and res["failures"] == 0, p.stderr[-4000:]
    return res


def test_core_6as6_vectors():
    r = _run_vectors(_exe())
    assert r["alloc_hooks"] == 1 and r["process_allocations"] == 0 and r["block_size_diff_samples"] == 0
    assert r["cases"] >= 45 and r["packs"] >= 28 and r["restart_rows_recovered"] >= 4


def test_core_6as6_no_contract():
    _run_vectors(_exe(("-O2", "-ffp-contract=off")))    # bit-equal to the vectors only with their libm: reported


@pytest.mark.skipif(not X86, reason="x86-64 only")
def test_core_6as6_fma():
    _run_vectors(_exe(("-O2", "-march=x86-64-v3")))


def _pipe(exe, mode, rows):
    p = subprocess.run([exe, mode], input=np.ascontiguousarray(rows, dtype=np.float64).tobytes(), capture_output=True)
    p.check_returncode()
    return np.frombuffer(p.stdout, dtype=np.float64)


def _same(a, b):
    return a == b or (math.isnan(a) and math.isnan(b))


def _raises(f, *a):
    try:
        return f(*a), False
    except (OverflowError, ValueError, ZeroDivisionError):
        return None, True


def test_bit_exact_law():
    exe = _exe(("-O2", "-ffp-contract=off"))
    rng = np.random.default_rng(1041)
    n = 4000
    pts = np.column_stack([rng.uniform(-14.0, 3.0, n), rng.uniform(-5.0, 250.0, n), rng.uniform(-30.0, 30.0, n),
                           rng.uniform(-10.0, 450.0, n)])
    pts[:200, 1] = rng.uniform(-1e-3, 2e-3, 200)              # the Vg2 floor
    pts[200:400, 3] = rng.uniform(-1e-3, 2e-3, 200)           # the Va floor and the knee
    edge = np.array([[0.0, 120.0, 0.0, 1e160], [1e5, 1e-3, 0.0, 100.0], [600.0, 120.0, 0.0, 150.0],
                     [-800.0, 120.0, 0.0, 150.0], [0.0, 0.0, 0.0, 0.0], [2.0, -5.0, -50.0, -5.0]])
    pts = np.vstack([pts, edge])
    got = _pipe(exe, "law", pts).reshape(-1, 28)
    for p, g in zip(pts, got):
        v = [float(x) for x in p]
        ref, raised = _raises(pentode.currents, *v)
        if raised:                         # the C++ reports where Python's grid law raises; currents() raises there too
            _, rg = _raises(pentode.currents_grid, *v)
            assert rg and g[27] == 1.0, v
            continue
        flat = [ref[0], ref[1], *ref[2][0], *ref[2][1]]
        assert all(_same(a, b) for a, b in zip(g[:10], flat)), v
        assert all(_same(a, b) for a, b in zip(g[10:12], pentode.currents_only(*v))), v
        rg, raised_g = _raises(pentode.currents_grid, *v)
        assert raised_g == (g[27] == 1.0), v
        if not raised_g:
            flat = [rg[0], rg[1], rg[2], *rg[3][0], *rg[3][1], *rg[3][2]]
            assert all(_same(a, b) for a, b in zip(g[12:27], flat)), v


def _problems(st, rng, n):
    """Rest problem p_dc + dp and starts: the warm-start neighbourhood and wild ones."""
    p = st.p_dc + rng.uniform([-8.0, -8.0, 0.0, -250.0], [8.0, 8.0, 0.0, 200.0], (n, 4))
    s = st.i_dc + rng.normal(0.0, 1.0, (n, len(st.i_dc))) * np.array([1e-3, 3e-4, 1e-4])[:len(st.i_dc)]
    s[: n // 4] = rng.uniform(-0.05, 0.05, (n // 4, len(st.i_dc)))
    return p, s


def test_bit_exact_solvers():
    exe = _exe(("-O2", "-ffp-contract=off"))
    rng = np.random.default_rng(1042)
    settings = [(solver.MAX_IT, solver.TOL_V, solver.TOL_F), (1, 0.0, 0.0), (3, 0.0, 0.0), (6, 1e-6, 1e-6)]
    for mode, h, tag in (("P1", 0.0, "solve2"), ("T", 0.0, "solve2"), ("P1", 1.0, "solve3"), ("T", 1.0, "solve3"),
                         ("P2", 1.0, "solve3")):
        st = EV.stage(48000.0, 8, mode, h, 0.45)
        K = np.zeros((4, 3))
        K[:, :st.n_nl] = st.m["K"]
        P, S = _problems(st, rng, 400)
        rows, ref = [], []
        for k, (p, s) in enumerate(zip(P, S)):
            nw = settings[k % len(settings)]
            s3 = list(s) + [0.0] * (3 - len(s))
            rows.append([*p, *K.ravel(), *s3, *nw])
            pl, sl = [float(x) for x in p], [float(x) for x in s]
            if tag == "solve2":
                v, a, b, it, ok = solver.solve(pl, st.K, sl[0], sl[1], st.par, *nw)
                ref.append([*v, a, b, 0.0, it, float(ok)])
            else:
                v, i, it, ok, fv = solver.solve_grid(pl, st.K, sl, st.par, st.grid, *nw)
                ref.append([*v, *i, it, float(ok), fv])
        got = _pipe(exe, tag, np.array(rows)).reshape(-1, 10)
        w = 9 if tag == "solve2" else 10           # solve2's residual is not part of solver.solve's result
        for r, g, q in zip(rows, got, ref):
            assert all(_same(a, b) for a, b in zip(g[:w], q[:w])), (mode, h, r, list(g), q)
        if tag == "solve3":                         # the restart contract on the same problems
            rows, ref = [], []
            for k, (p, s) in enumerate(zip(P, S)):
                nw = settings[k % len(settings)]
                starts = [list(s), list(st.i_dc + rng.normal(0, 1e-3, 3)), list(st.i_dc)]
                rows.append([*p, *K.ravel(), *[x for t in starts for x in t], *nw])
                v, i, it, ok, fv, att = solver.solve_grid_restarts([float(x) for x in p], st.K,
                                                                   [[float(x) for x in t] for t in starts],
                                                                   st.par, st.grid, *nw)
                ref.append([*v, *i, it, float(ok), fv, att])
            got = _pipe(exe, "restarts", np.array(rows)).reshape(-1, 11)
            for r, g, q in zip(rows, got, ref):
                assert all(_same(a, b) for a, b in zip(g, q)), (mode, r, list(g), q)


# staleness check tolerances, per entry kind: Python on another machine (BLAS / LAPACK, libm) rounds the matrices and
# the transcendental functions differently, which moves the stage outputs by up to ~1e-6 V (cpp/README-6as6.md); a
# real change to the Python stage moves them by far more
def _tol(name):
    if name.startswith(("solve2.in", "solve3.in", "restarts.in")):    # p = p_dc + dp: rounds like the matrices
        return "rel", 1e-9
    if name in ("law.in", "knob.in", "knob.bias_in", "par.dual_control", "par.grid", "lpf.in", "internal.in") or \
            name.endswith((".in", ".cfg")) or name.startswith(("taps.", "rsk140_", "lpf.")):
        return "abs", 1e-13
    if name.startswith(("case.", "internal.")) and name.endswith((".out", ".vgk", ".vg2k", ".vak")):
        return "abs", 1e-5
    if name.endswith((".stats", ".iters", ".hist")):
        return "rel", 1e-3
    if name.startswith(("solve2.out", "solve3.out", "restarts.out")):
        return "rel", 1e-6
    return "rel", 1e-9


def test_vectors_6as6_fresh():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        now = dict(EV.entries())
    old = EV.read()
    assert sorted(now) == sorted(old)
    for k, v in now.items():
        kind, tol = _tol(k)
        o = old[k]
        assert v.shape == o.shape, k
        nan = ~np.isfinite(v) | ~np.isfinite(o)           # NaN / inf entries (the solvers' non-finite starts, fv)
        assert np.array_equal(v[nan], o[nan], equal_nan=True), k
        err = np.max(np.abs(v[~nan] - o[~nan])) if (~nan).any() else 0.0
        if kind == "rel":
            err /= max(np.max(np.abs(v[~nan])) if (~nan).any() else 0.0, 1e-300)
        assert err <= tol, f"{k}: {err:.3g} > {tol:g}; regenerate with python -m cpp.export_vectors_6as6"


def _stage_response(n, beta, R):
    hb = _FirHB(n, beta)
    rip = float(np.max(np.abs(20 * np.log10(np.abs(hb.freq(np.linspace(0, PASS_HZ, 2001), R))))))
    stop = float(-np.max(20 * np.log10(np.abs(hb.freq(np.linspace(R / 2 - PASS_HZ, R / 2, 8001), R)))))
    return rip, stop


def test_k140_derivation():
    """#113's rule (PR #129, resampler_study.design_for): beta = 0.1102 (A - 8.7) for A = 140 dB (Kaiser), and per stage
    the shortest 4m + 3 length whose one-pass stopband over its folding band is >= A with <= 0.005 dB ripple."""
    beta = round(0.1102 * (140 - 8.7), 2)
    assert beta == EV.K140[48000.0][0][1] == 14.47
    for fs, ((n0, b0), (n1, b1)) in EV.K140.items():
        for (n, b), R in (((n0, b0), 2 * fs), ((n1, b1), 4 * fs)):
            rip, stop = _stage_response(n, b, R)
            assert stop >= 140 and rip <= 0.005, (fs, n, stop, rip)
            rip, stop = _stage_response(n - 4, b, R)
            assert not (stop >= 140 and rip <= 0.005), (fs, n - 4, stop, rip)    # the shortest such length


def test_validate_k140_6as6():
    """The core's default resampler (k140, which the committed Python results do not use) against the same-model ngspice
    references: every supported setting passes every dk/validate.CRIT criterion at standard and hq."""
    _exe()
    out, bad = VC.main(workers=os.cpu_count(), write=False, fir="k140")
    assert not bad, [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    assert len(out["rows"]) == 44 and all(r["fir"] == "k140" for r in out["rows"])


def test_validate_reproduced_6as6():
    _exe()
    out, bad = VC.main(workers=os.cpu_count(), write=False)
    assert not bad, [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    assert len(out["rows"]) == 44


BENCH_KEYS = {"name", "quality", "os", "variant", "mode", "bias_ma", "grid_hardness", "lpf", "fir", "level_v", "block",
              "instances", "ns_per_sample", "ns_min", "ns_max", "pct_core", "block_pct_p50", "block_pct_p99",
              "block_pct_max", "cpu_on_fraction", "cycles_per_sample", "instr_per_sample", "ghz_on_cpu", "newton_mean",
              "newton_max", "nonconverged", "restarted", "deterministic"}


def _bench_rows_ok(rows):
    assert len(rows) == 60 and len({r["name"] for r in rows}) == 60
    for r in rows:
        assert BENCH_KEYS <= set(r), r["name"]
        assert r["deterministic"] and r["ns_per_sample"] > 0 and r["newton_mean"] >= 1.0, r
        assert r["nonconverged"] == 0 and r["newton_max"] <= 3 * 50, r


def test_bench_6as6_smoke():
    if B.compiler() is None:
        pytest.skip("no C++ compiler")
    exe = B.build(name="bench_6as6")
    names = subprocess.run([exe, "--list"], capture_output=True, text=True, check=True).stdout.split()
    assert len(names) == 2 * 3 * 2 * 2 + 6 * 2 * 3      # default: quality x peak x block x instances; 6 variants
    p = subprocess.run([exe, "--seconds", "0.011", "--reps", "1"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]           # 1: an output differed between passes
    d = json.loads(p.stdout)
    assert d["meta"]["samples_per_pass"] == 512 and d["meta"]["core"] == "cpp/dk6as6_core.h"
    _bench_rows_ok(d["results"])


def test_bench_6as6_records():
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "cpp", "bench_6as6_*.json")))
    if not files:
        pytest.skip("no cpp/bench_6as6_*.json committed")
    for f in files:
        d = json.load(open(f))
        assert {"host", "build", "state_before", "state_after", "meta", "results"} <= set(d), f
        assert d["meta"]["reps"] >= 5 and not d["build"]["dirty"] and d["build"]["source"] == "cpp/bench_6as6.cpp", f
        _bench_rows_ok(d["results"])


def test_aliasing_records():
    """The committed sweep (cpp/aliasing_6as6.json) and the guard's baselines (#98 format) are consistent: with the
    core's default design every supported setting is within its quality gate at both rates, both qualities and every
    tone up to 19997 Hz; with the shipped FIR only the known band-edge points are over it; the guard runs the default
    design, and its tones and limits are recomputed from the sweep; the baselines cover every guard key and tone;
    parity with the Python sweeps."""
    from cpp import aliasing_6as6 as AQ
    from dk import aliasing_guard as AG
    if not os.path.exists(AQ.SWEEP_JSON):
        pytest.skip("no cpp/aliasing_6as6.json")
    assert AQ.default_fir() == "k140"                      # dk6as6_core.h DEFAULT_FIR, as the CLI reports it
    sw = json.load(open(AQ.SWEEP_JSON))
    recs = sw["records"]
    assert len(recs) == len(AQ.sweep_jobs()) and max(AQ.TONES) == 19997
    for r in recs:
        over = r["total_dbc"] > AQ.gate(r["fs"], r["quality"], r["drive"])
        if r["fir"] == AQ.default_fir():
            assert not over, r
        else:   # #113's finding: the shipped FIR at 44.1 kHz, 2 V, P1 0.6 mA, from 19957 Hz (the resampler's)
            assert over == (r["fs"] == 44100 and r["drive"] == 2.0 and r["mode"] == "P1" and r["bias_ma"] == 0.6
                            and r["tone"] >= 19957), r
        assert r["nonconverged"] == 0 and r["out_of_law"] == 0, r
    now = json.loads(json.dumps(AQ.summary(recs)))        # recomputed here: libm's log10 may differ by an ulp
    assert len(now) == len(sw["summary"])
    for a, b in zip(now, sw["summary"]):
        assert a.keys() == b.keys()
        for k in a:
            assert (a[k] == pytest.approx(b[k], rel=1e-12, abs=1e-12)) if isinstance(a[k], float) else a[k] == b[k], k
    # the band-edge scan (#113's finding, reproduced on the C++ core): with the shipped FIR, P1 0.6 mA at 2 V and
    # 44.1 kHz is over the gate from 19957 Hz up (both qualities: the resampler's, not the stage's); k140 is >= 40 dB
    # inside it at every scanned point; 48 kHz is inside with both
    scan = sw["scan"]
    assert len(scan) == len(AQ.SCAN_JOBS)
    for r in scan:
        g = AQ.gate(r["fs"], r["quality"], r["drive"])
        if r["fir"] == "k140":
            assert r["total_dbc"] <= g - 40, r
        else:
            assert (r["total_dbc"] > g) == (r["fs"] == 44100 and r["tone"] >= 19957), r
    assert all(abs(p["diff_db"]) < 0.01 for p in AQ.parity(recs)) and len(AQ.parity(recs)) > 50
    for fs in AQ.RATES:
        base = AG.load_baseline(AQ.baseline_path(fs))
        tones = AQ.guard_tones(fs, recs)
        assert base["method"]["fs_base"] == fs and base["method"]["tones"] == {k: list(v) for k, v in tones.items()}
        for k, ts in tones.items():
            for t in ts:
                b = base["values"][k]["tones"][str(t)]
                assert b["total_dbc"] <= AQ.guard_limits(fs)[k], (k, t)
