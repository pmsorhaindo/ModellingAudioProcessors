"""The C++ core (#19, cpp/dk_core.h) against the Python DK stage. Needs a C++17 compiler ($CXX, else g++, c++ or
clang++): skipped without one, except under CI (CI set in the environment), where a missing compiler fails.
  test_core_vectors        cpp/test_core.cpp on cpp/vectors.txt, -O2: every C++ test, including no allocation in
                           process(), FTZ/DAZ scoping, the lock-free swap and the #7 fallback
  test_core_vectors_fma    the same with -O2 -march=x86-64-v3 (FMA contraction; x86-64 only)
  test_swap_tsan           the threaded swap test under -fsanitize=thread (skipped if TSan cannot run here)
  test_bit_exact_*         solve() and the triode currents against the live Python functions on this machine (same
                           libm), bit for bit, in a -O2 -ffp-contract=off build: catches a changed path (pnjlim, line
                           search) even where it converges to the same root
  test_stereo_parity       StereoStage (#21) against two independent mono Stages (test_core stereo), per build: SIMD
                           and forced scalar fallback, with and without FP contraction; bit-exact where the build
                           guarantees it (_stereo_exact_expected), else within the test_core.cpp tolerance. Also the
                           vector maths (#67, VecFastMath, LaneMath::vector) against two mono fast Stages, the same
                           rule, and its accuracy against libm on grids that cover dk/fastmath_domain.json
  test_vectors_are_fresh   the committed cpp/vectors.txt still matches what the Python stage produces now
  test_validate_reproduced dk.validate.run on the C++ core against dk/results.json, every setting (cpp/validate_cpp.py)
  test_bench_smoke         cpp/bench.cpp (#20, #21) builds and runs every configuration once on 512 samples, output
                           deterministic, stereo SIMD rows bit-identical to their two-mono twins; no timing is
                           checked (cpp/BENCHMARK.md)
  test_bench_records       the committed cpp/bench_*.json results files are complete and well formed (#20 72-row,
                           #21 108-row and #67 126-row schemas)
  test_bias_options_smoke  cpp/bias_options.cpp (#73, the bias-update options of cpp/RESULTS-smoothing.md) builds
                           and runs one move; the bias line beats every other option on its worst step (no timing)
Run from prototypes/fullstage:  python -m pytest -q cpp/tests   (~15 s)
"""
import glob
import json
import os
import platform
import signal
import subprocess
import sys
import numpy as np
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from cpp import validate_cpp as VC, export_vectors as EV  # noqa: E402 -- after the sys.path setup
from dk import solver, circuit  # noqa: E402 -- after the sys.path setup
from dk.stage import Stage, QUALITY  # noqa: E402 -- after the sys.path setup
from dk.triode import currents, currents_only, currents_fast  # noqa: E402 -- after the sys.path setup

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
    assert r["alloc_hooks"] == 1 and r["process_allocations"] == 0 and r["stereo_process_allocations"] == 0
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


ARM64 = platform.machine().lower() in ("arm64", "aarch64")


def _stereo_exact_expected(flags, scalar, compiler, fma):
    """Bit-exact stereo vs mono is guaranteed when scalar and vector code are contracted (FMA) at the same places
    (cpp/README.md, "Stereo"):
    - no contraction at all: -ffp-contract=off, or a build without FMA instructions (fma, as the binary reports it);
    - clang's per-expression contraction on the native NEON / SSE2 types.
    It is not guaranteed for the struct fallback with contraction, nor for GCC's -ffp-contract=fast with FMA, which
    fuses after optimisation and so treats scalar and vector code differently."""
    if "-ffp-contract=off" in flags or not fma:
        return True
    return not scalar and "clang" in compiler.lower()


STEREO_BUILDS = [(("-O2",), False), (("-O3",), False), (("-O2", "-ffp-contract=off"), False),
                 (("-O2",), True), (("-O2", "-ffp-contract=off"), True), (("-O2", "-march=x86-64-v3"), False)]


@pytest.mark.parametrize("flags,scalar", STEREO_BUILDS, ids=lambda v: "scalar" if v is True else
                         "simd" if v is False else " ".join(v))
def test_stereo_parity(flags, scalar):
    if "-march=x86-64-v3" in flags and not X86:
        pytest.skip("x86-64 only")
    exe = _exe(flags, ("DKCORE_SIMD_SCALAR",) if scalar else ())
    p = subprocess.run([exe, "stereo", VECTORS], capture_output=True, text=True)
    if p.returncode == -signal.SIGILL:
        pytest.skip("this CPU cannot run the build")
    r = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 0 and r["failures"] == 0, p.stderr[-3000:]     # every check within tolerance
    assert r["simd"] == ("scalar" if scalar else "neon" if ARM64 else "sse2" if X86 else r["simd"])
    assert r["alloc_hooks"] == 1 and r["stereo_process_allocations"] == 0
    assert r["stereo_runs"] == 43 and r["stereo_lanes_differ_runs"] >= 20 and r["stereo_capped_nonconv_l"] > 0
    assert r["vector_runs"] == 43 and r["vector_lanes_differ_runs"] >= 20 and r["vector_solve_lanes"] == 8292
    _vecmath_ok(r)
    if _stereo_exact_expected(flags, scalar, r["compiler"], r["fma"]):
        assert r["stereo_exact"] == 1, f"{r['compiler']} fma={r['fma']}\n" + p.stderr[-3000:]
        assert r["vector_exact"] == 1, f"{r['compiler']} fma={r['fma']}\n" + p.stderr[-3000:]


# VecFastMath (#67) error bounds against libm, relative: dk/fastmath.py BOUND (the scalar kernels' tested bounds;
# test_core.cpp asserts them too). ulp: measured 3474 (exp), 800 (log1p), 3531 (pow g=1.26), 3588 (pow g=1.314) on
# Apple clang 17 / macOS libm, 13 Nov 2026; asserted with the margin below.
VECMATH_ULP = 8000


def _vecmath_ok(r):
    from dk import fastmath as fm
    rng = json.load(open(os.path.join(ROOT, "dk", "fastmath_domain.json")))["ranges"]
    cover = {"exp": ("exp_x",), "log1p": ("log1p_e_pos",), "pow_g1_26": ("pow_s_pos_g1.26",),
             "pow_g1_314": ("pow_s_pos_g1.314",)}
    bound = {"exp": fm.BOUND["exp"], "exp_full": fm.BOUND["exp"], "log1p": fm.BOUND["log1p"],
             "pow_g1_26": fm.BOUND["pow_g1_26"], "pow_g1_314": fm.BOUND["pow_g1_314"]}
    for k, b in bound.items():
        rel, ulp = r[f"vecmath_{k}_max_rel"], r[f"vecmath_{k}_max_ulp"]
        assert rel <= b and ulp <= VECMATH_ULP, (k, rel, ulp)
        for d in cover.get(k, ()):          # the grid covers every argument the stage passed (dk.fastmath_domain)
            assert r[f"vecmath_{k}_lo"] <= rng[d]["min"] and r[f"vecmath_{k}_hi"] >= rng[d]["max"], (k, d)
    assert r["vecmath_exp_full_lo"] == fm.EXP_LO and r["vecmath_exp_full_hi"] == fm.EXP_HI
    assert r["vecmath_edge_cases"] > 10000 and r["vecmath_pnjlim_limited"] > 1000


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
    p_rest = s.m["G"] @ s.x0 + s.m["H"] @ np.array([0.0, circuit.VALUES["HT"]])
    extra = [[*(p_rest + rng.uniform([-12, -80], [3, 80])), rng.uniform(-3, 3), rng.uniform(0, 450), *solver_default()]
             for _ in range(2000)]
    rows = np.vstack([rows, np.array(extra)])
    cin = np.hstack([rows[:, :4], np.tile(np.ravel(K), (len(rows), 1)), rows[:, 4:]])
    got = _pipe(exe, "solve", cin).reshape(-1, 6)
    for r, g in zip(rows, got):
        ref = solver.solve(float(r[0]), float(r[1]), K, float(r[2]), float(r[3]), s.par, int(r[4]), float(r[5]),
                           float(r[6]))
        assert tuple(g) == (ref[0], ref[1], ref[2], ref[3], float(ref[4]), 1.0 if ref[5] else 0.0), (r, g, ref)


def solver_default():
    return [solver.MAX_IT, solver.TOL_V, solver.TOL_F]


def test_bit_exact_triode():
    exe = _exe(("-O2", "-ffp-contract=off"))
    rng = np.random.default_rng(8)
    pts = np.column_stack([rng.uniform(-16.0, 2.0, 3000), rng.uniform(0.0, 450.0, 3000)])
    got = _pipe(exe, "triode", pts).reshape(-1, 14)
    def flat(r):
        return [r[0], r[1], r[2][0][0], r[2][0][1], r[2][1][0], r[2][1][1]]
    for (vg, va), g in zip(pts, got):
        vg, va = float(vg), float(va)
        ref = flat(currents(vg, va)) + list(currents_only(vg, va)) + flat(currents_fast(vg, va))
        assert list(g) == ref, (vg, va)


# staleness check tolerances, per entry kind: Python on another machine (BLAS / LAPACK kernels) rounds the matrices
# differently, which moves the stage outputs by up to ~1e-6 V (cpp/README.md); a real change to the Python stage
# moves them by far more
def _tol(name):
    if name == "solve.in":                    # not a rounded input: see test_vectors_are_fresh (#66)
        return "rel", 1e-9
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
    # solve.in (#66): p0, p1 (p_rest + dp) and the DC-point starts are computed from the matrices, so they round like
    # the matrices (the "rel" 1e-9 of coef.*), not like the 10-digit inputs. The Newton settings (max_it, tol_v,
    # tol_f) are exact.
    def settings(V):
        return V["solve.in"].reshape(-1, 7)[:, 4:]
    assert np.array_equal(settings(now), settings(old)), "solve.in settings"


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


def _bench_rows_ok(rows):
    """#20 records: 72 rows, mono only (x1, x2), no "path". #21 records: 108 rows, x1 / x2 / s2, with "path", and every
    s2 row says whether it is bit-identical to its x2 twin (only reported, not required here: see test_bench_smoke).
    #67 records: 126 rows, plus 18 v2 rows (vector maths, fast only), each with matches_x2, and the Newton means of
    both channels (newton_mean_r) and of the 2-lane kernel (newton_mean_max, s2 and v2)."""
    paths = {r.get("path") for r in rows}
    vec = "stereo_simd_vecmath" in paths
    n_expected = 126 if vec else 108 if "stereo_simd" in paths else 72
    assert len(rows) == n_expected and len({r["name"] for r in rows}) == n_expected
    for r in rows:
        assert BENCH_KEYS <= set(r), r["name"]
        assert r["deterministic"] and r["ns_per_sample"] > 0 and r["newton_mean"] >= 1.0, r
    if n_expected == 72:
        return
    s2 = [r for r in rows if r["path"] == "stereo_simd"]
    assert len(s2) == 36 and all(r["name"].endswith("_s2") and r["instances"] == 2 for r in s2)
    assert all(r["matches_x2"] in (True, False) for r in s2)      # the full matrix: every twin is in the run
    assert paths == ({"mono", "stereo_simd", "stereo_simd_vecmath"} if vec else {"mono", "stereo_simd"})
    if vec:
        v2 = [r for r in rows if r["path"] == "stereo_simd_vecmath"]
        assert len(v2) == 18
        assert all(r["name"].endswith("_v2") and r["instances"] == 2 and r["math"] == "fast" for r in v2)
        assert all(r["matches_x2"] in (True, False) for r in v2)
        for r in rows:
            two = r["instances"] == 2
            assert (r["newton_mean_r"] is not None) == two
            assert (r["newton_mean_max"] is not None) == (r["path"] != "mono")
            if r["path"] != "mono":                             # max(itL, itR) per sample: >= each mean, <= their sum
                assert max(r["newton_mean"], r["newton_mean_r"]) <= r["newton_mean_max"] + 1e-4
                assert r["newton_mean_max"] <= r["newton_mean"] + r["newton_mean_r"] + 1e-4


def test_bench_smoke():
    exe = _exe(name="bench")
    names = subprocess.run([exe, "--list"], capture_output=True, text=True, check=True).stdout.split()
    assert len(names) == 3 * 2 * 3 * 2 * 3 + 3 * 3 * 2   # quality x math x peak x block x (x1, x2, s2), + v2 (fast)
    p = subprocess.run([exe, "--seconds", "0.011", "--reps", "1"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]      # 1: an output differed between passes
    d = json.loads(p.stdout)
    assert d["meta"]["samples_per_pass"] == 512
    assert d["meta"]["simd"] == ("neon" if ARM64 else "sse2" if X86 else d["meta"]["simd"])
    _bench_rows_ok(d["results"])
    m = d["meta"]
    if _stereo_exact_expected(("-O2",), False, m["compiler"], m["fma"]):   # then s2 and v2 equal x2 bit for bit
        assert all(r["matches_x2"] for r in d["results"] if r["path"] != "mono"), (m["compiler"], m["fma"])


def test_bias_options_smoke():
    p = subprocess.run([_exe(name="bias_options"), "--smoke"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    d = json.loads(p.stdout.strip().splitlines()[-1])
    for case in ("up|0", "up|8"):
        line = d[f"bias line|{case}"]
        others = [v for k, v in d.items() if k.endswith("|" + case) and not k.startswith("bias line")]
        assert len(others) == 7 and line * 10 < min(others), (case, line, others)


def test_bench_records():
    files = sorted(f for f in glob.glob(os.path.join(ROOT, "cpp", "bench_*.json"))
                   if not os.path.basename(f).startswith("bench_6as6"))      # the 6AS6 records: test_cpp_core_6as6.py
    if not files:
        pytest.skip("no cpp/bench_*.json committed yet (cpp/BENCHMARK.md, 'Running the official benchmark')")
    for f in files:
        d = json.load(open(f))
        assert {"host", "build", "state_before", "state_after", "meta", "results"} <= set(d), f
        assert d["meta"]["reps"] >= 5 and not d["build"]["dirty"], f
        _bench_rows_ok(d["results"])


def test_bench_flags_argv():
    from cpp.bench import _argv_with_flags
    assert _argv_with_flags(["--flags", "-O3", "--reps", "5"]) == ["--flags=-O3", "--reps", "5"]
    assert _argv_with_flags(["--flags=-O3"]) == ["--flags=-O3"]
    assert _argv_with_flags(["--flags", "-O3 -march=x86-64-v3"]) == ["--flags=-O3 -march=x86-64-v3"]


@pytest.mark.parametrize("fs", (44100, 48000, 88200, 96000, 176400, 192000))
@pytest.mark.parametrize("quality", QUALITY)
def test_host_rate_python_parity(fs, quality):
    """A short input through both complete implementations, including rate-dependent coefficients and FIR delay."""
    VC.EXE = _exe()
    py = Stage(fs, quality=quality)
    cpp = VC.CppStage(fs, quality=quality)
    t = np.arange(1024) / fs
    x = 2 * np.sin(2 * np.pi * 1000 * t) + 0.05 * np.sin(2 * np.pi * 19037 * t)
    a, b = py.process(x)["out"], cpp.process(x)["out"]
    assert np.max(np.abs(a - b)) <= 1e-5  # existing TOL_BUILT_ABS
    assert cpp.fs == fs * QUALITY[quality][0]
    assert cpp.latency_samples == py.latency_samples
    assert cpp.iteration_stats()["nonconverged"] == py.iteration_stats()["nonconverged"] == 0


def test_fir_rate_rule_and_geometry_match_python():
    from dk.resample import Resampler, first_fir_taps
    # Include unswept pull-up/down and low rates: they retain historical geometry, not inferred certification.
    rates = (22050, 32000, 44056, 44100, 44144, 46000, 47952, 48000, 88200, 96000, 176400, 192000)
    cases = [(fs, os_) for fs in rates for os_ in (2, 4, 8, 16)]
    p = subprocess.run([_exe(), "fir-geometry"], input="".join(f"{fs} {os_}\n" for fs, os_ in cases),
                       capture_output=True, text=True, check=True)
    rows = [json.loads(line) for line in p.stdout.splitlines()]
    assert len(rows) == len(cases)
    for (fs, os_), row in zip(cases, rows):
        rs = Resampler(fs, os_)
        assert row == dict(first_taps=first_fir_taps(fs), latency=rs.latency_samples, pad=rs.pad,
                           up_delay=rs.up_delay_internal, macs=rs.macs()[0])


def test_first_fir_capacity_tracks_longest_registry_entry(tmp_path):
    """A future rate longer than 215 must resize the actual first-stage buffers at compile time."""
    _exe()  # apply the suite's missing-compiler policy
    with open(os.path.join(ROOT, "cpp", "dk_core.h")) as f:
        header = f.read()
    start = header.index("inline constexpr FirRate FIRST_FIR_BY_RATE[]")
    end = header.index(";", start) + 1
    registry = ("inline constexpr FirRate FIRST_FIR_BY_RATE[] = "
                "{{44100.0, FIR_FIRST_44100_TAPS}, {50000.0, 319}, {51000.0, 231}};")
    header = header[:start] + registry + header[end:]
    header = header.replace('#include "../dk/fastmath.h"', f'#include "{ROOT}/dk/fastmath.h"')
    (tmp_path / "dk_core.h").write_text(header)
    source = tmp_path / "capacity.cpp"
    source.write_text('''#include "dk_core.h"
using namespace dkcore;
static_assert(FIR_MAX_TAPS == 319, "capacity must cover the longest entry, not the first or last");
constexpr FirRate smaller[] = {{22050.0, 23}};
static_assert(max_first_fir_taps(smaller) == FIR_FIRST_TAPS, "capacity must include the fallback");
int main() {
    Resampler r;
    if (!r.prepare(8, 50000.0) || r.ntaps(0) != 319) return 1;
    double hi[MAX_OS];
    for (int i = 0; i < 256; ++i) {
        r.up(0.1, hi);
        if (!std::isfinite(r.down(hi))) return 2;
    }
}
''')
    exe = tmp_path / "capacity"
    subprocess.run([VC.compiler(), *VC.CXXFLAGS, "-O2", str(source), "-o", str(exe)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(exe)], check=True, capture_output=True)
