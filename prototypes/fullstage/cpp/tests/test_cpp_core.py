"""The C++ core (#19, cpp/dk_core.h) against the Python DK stage. Needs a C++17 compiler ($CXX, else g++, c++ or
clang++): skipped without one, except under CI (CI set in the environment), where a missing compiler fails.
  test_core_vectors        cpp/test_core.cpp on cpp/vectors.txt, -O2: every C++ test, including no allocation in
                           process(), FTZ/DAZ scoping, the lock-free swap and the #7 fallback
  test_core_vectors_fma    the same with -O2 -march=x86-64-v3 (FMA contraction; x86-64 only)
  test_swap_tsan           the threaded swap test under -fsanitize=thread (skipped if TSan cannot run here)
  test_bit_exact_*         solve() and the triode currents against the live Python functions on this machine (same
                           libm), bit for bit, in a -O2 -ffp-contract=off build: catches a changed path (pnjlim, line
                           search) even where it converges to the same root
  test_vectors_are_fresh   the committed cpp/vectors.txt still matches what the Python stage produces now
  test_validate_reproduced dk.validate.run on the C++ core against dk/results.json, every setting (cpp/validate_cpp.py)
  test_bench_smoke         cpp/bench.cpp (#20) builds and runs every configuration once on 512 samples, output
                           deterministic; no timing is checked (cpp/BENCHMARK.md)
  test_bench_records       the committed cpp/bench_*.json results files (if any yet) are complete and well formed
Run from prototypes/fullstage:  python -m pytest -q cpp/tests   (~15 s)
"""
import glob, json, os, platform, signal, subprocess, sys
import numpy as np
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from cpp import validate_cpp as VC, export_vectors as EV
from dk import solver, circuit
from dk.stage import Stage, QUALITY
from dk.triode import currents, currents_only, currents_fast

VECTORS = os.path.join(ROOT, "cpp", "vectors.txt")
X86 = platform.machine().lower() in ("x86_64", "amd64")


def _exe(flags=("-O2",), defines=(), name="test_core"):
    if VC.compiler() is None:
        if os.environ.get("CI"):
            pytest.fail("no C++ compiler on CI (g++ expected on the runner)")
        pytest.skip("no C++ compiler (set CXX)")
    return VC.build(flags, defines, name)


def _run_vectors(exe):
    p = subprocess.run([exe, VECTORS], capture_output=True, text=True)
    if p.returncode == -signal.SIGILL:
        pytest.skip("this CPU cannot run the build")
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 0 and res["failures"] == 0, p.stderr
    return res


def test_core_vectors():
    r = _run_vectors(_exe())
    assert r["alloc_hooks"] == 1 and r["process_allocations"] == 0
    assert r["swap_adopted"] > 0


@pytest.mark.skipif(not X86, reason="x86-64 only")
def test_core_vectors_fma():
    _run_vectors(_exe(("-O2", "-march=x86-64-v3")))


def test_swap_tsan():
    try:
        exe = _exe(("-O1", "-g", "-fsanitize=thread"), ("DKCORE_NO_ALLOC_HOOKS",))
    except subprocess.CalledProcessError as e:
        pytest.skip(f"no TSan build here: {e.stderr[-200:]}")
    p = subprocess.run([exe, "swap"], capture_output=True, text=True)
    assert "WARNING: ThreadSanitizer" not in p.stderr, p.stderr[-3000:]
    if p.returncode != 0 and "FATAL: ThreadSanitizer" in p.stderr:    # e.g. TSan vs the kernel's mmap layout
        pytest.skip("TSan cannot run here: " + p.stderr[-200:])
    assert p.returncode == 0 and json.loads(p.stdout.strip().splitlines()[-1])["failures"] == 0, p.stderr[-2000:]


def _pipe(exe, mode, rows):
    p = subprocess.run([exe, mode], input=np.ascontiguousarray(rows, dtype=np.float64).tobytes(), capture_output=True)
    p.check_returncode()
    return np.frombuffer(p.stdout, dtype=np.float64)


def test_bit_exact_solver():
    exe = _exe(("-O2", "-ffp-contract=off"))
    V = EV.read()
    s = Stage(48000.0)
    K = s.K
    rows = V["solve.in"].reshape(-1, 7)
    rng = np.random.default_rng(7)
    p_rest = s.m["G"] @ s.x + s.m["H"] @ np.array([0.0, circuit.VALUES["HT"]])
    extra = [[*(p_rest + rng.uniform([-12, -80], [3, 80])), rng.uniform(-3, 3), rng.uniform(0, 450), *solver_default()]
             for _ in range(2000)]
    rows = np.vstack([rows, np.array(extra)])
    cin = np.hstack([rows[:, :4], np.tile(np.ravel(K), (len(rows), 1)), rows[:, 4:]])
    got = _pipe(exe, "solve", cin).reshape(-1, 6)
    for r, g in zip(rows, got):
        ref = solver.solve(float(r[0]), float(r[1]), K, float(r[2]), float(r[3]), s.par, int(r[4]), float(r[5]), float(r[6]))
        assert tuple(g) == (ref[0], ref[1], ref[2], ref[3], float(ref[4]), 1.0 if ref[5] else 0.0), (r, g, ref)


def solver_default():
    return [solver.MAX_IT, solver.TOL_V, solver.TOL_F]


def test_bit_exact_triode():
    exe = _exe(("-O2", "-ffp-contract=off"))
    rng = np.random.default_rng(8)
    pts = np.column_stack([rng.uniform(-16.0, 2.0, 3000), rng.uniform(0.0, 450.0, 3000)])
    got = _pipe(exe, "triode", pts).reshape(-1, 14)
    flat = lambda r: [r[0], r[1], r[2][0][0], r[2][0][1], r[2][1][0], r[2][1][1]]
    for (vg, va), g in zip(pts, got):
        vg, va = float(vg), float(va)
        ref = flat(currents(vg, va)) + list(currents_only(vg, va)) + flat(currents_fast(vg, va))
        assert list(g) == ref, (vg, va)


# staleness check tolerances, per entry kind: Python on another machine (BLAS / LAPACK kernels) rounds the matrices
# differently, which moves the stage outputs by up to ~1e-6 V (cpp/README.md); a real change to the Python stage
# moves them by far more
def _tol(name):
    if name.endswith((".in", ".cfg")) or name.startswith("taps."):
        return "abs", 1e-14
    if name.startswith(("internal.", "case.")) and name.endswith((".out", ".vgk")):
        return "abs", 1e-5
    if name.endswith((".stats", ".iters")):
        return "rel", 1e-3
    return "rel", 1e-9


def test_vectors_are_fresh():
    now = dict(EV.entries())
    old = EV.read()
    assert sorted(now) == sorted(old)
    for k, v in now.items():
        kind, tol = _tol(k)
        assert v.shape == old[k].shape, k
        err = np.max(np.abs(v - old[k])) if len(v) else 0.0
        if kind == "rel":
            err /= max(np.max(np.abs(v)), 1e-300)
        assert err <= tol, f"{k}: {err:.3g} > {tol:g}; regenerate with python -m cpp.export_vectors"


_REF = {}


@pytest.mark.parametrize("quality", list(QUALITY))
def test_validate_reproduced(quality):
    exe = _exe()
    if not _REF:
        _REF.update(json.load(open(os.path.join(ROOT, "dk", "results.json"))))
    r = VC.run(quality, exe=exe)
    d = VC.compare(r, _REF[quality])
    assert VC.check(d) == [], d
    assert all(r["pass_vs_dz"].values())


BENCH_KEYS = {"name", "quality", "os", "math", "level_v", "block", "instances", "ns_per_sample", "ns_min", "ns_max",
              "pct_core", "block_pct_p50", "block_pct_p99", "block_pct_max", "cpu_on_fraction", "cycles_per_sample",
              "instr_per_sample", "ghz_on_cpu", "newton_mean", "nonconverged", "deterministic"}


def _bench_rows_ok(rows, n_expected):
    assert len(rows) == n_expected and len({r["name"] for r in rows}) == n_expected
    for r in rows:
        assert BENCH_KEYS <= set(r), r["name"]
        assert r["deterministic"] and r["ns_per_sample"] > 0 and r["newton_mean"] >= 1.0, r


def test_bench_smoke():
    exe = _exe(name="bench")
    names = subprocess.run([exe, "--list"], capture_output=True, text=True, check=True).stdout.split()
    assert len(names) == 3 * 2 * 3 * 2 * 2          # quality x math x peak x block x instances
    p = subprocess.run([exe, "--seconds", "0.011", "--reps", "1"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]      # 1: an output differed between passes
    d = json.loads(p.stdout)
    assert d["meta"]["samples_per_pass"] == 512
    _bench_rows_ok(d["results"], len(names))


def test_bench_records():
    files = sorted(glob.glob(os.path.join(ROOT, "cpp", "bench_*.json")))
    if not files:
        pytest.skip("no cpp/bench_*.json committed yet (cpp/BENCHMARK.md, 'Running the official benchmark')")
    for f in files:
        d = json.load(open(f))
        assert {"host", "build", "state_before", "state_after", "meta", "results"} <= set(d), f
        assert d["meta"]["reps"] >= 5 and not d["build"]["dirty"], f
        _bench_rows_ok(d["results"], 72)
