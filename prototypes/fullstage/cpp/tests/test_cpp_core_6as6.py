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
  test_validate_shipped_6as6   the same 44 settings with the shipped FIR (the #102-#113 resampler): every supported
                               setting passes every CRIT criterion against the ngspice references
  test_k140_design_is_one_design  the Python stage's K140 = #113's derived k140 = dk_core.h's K140_FIR
  test_validate_reproduced_6as6  dk6as6.validate.run on the C++ core reproduces every committed dk6as6/results*.json
                               (44 settings: both models, every mode and reference current, standard and hq) within
                               validate_cpp_6as6.LIMITS; every criterion reads as Python's; supported settings pass
  #135 (cpp/README-6as6.md, "CPU (#135)"):
  test_validate_fast_6as6      the same reproduction with the fast valve law (LawMath::fast), mono and on the two-lane
                               kernel (vector lane maths): the same LIMITS, every criterion as Python's
  test_stereo_parity_6as6      StereoStage (cpp/dk6as6_stereo.h) against two mono Stages (test_core_6as6 stereo), per
                               build; bit-exact where the build guarantees it (as the 12AX7's, cpp/README.md "Stereo")
  test_fastlaw_kernel_bounds   dk/fastmath.h's exp / log1p / pow (EX, XA, XI) against libm on grids covering the 6AS6's
                               recorded domain (cpp/fastlaw_domain_6as6.json), within FASTLAW_BOUND
  test_fastlaw_law_bounds      the fast valve law against the libm law (currents, Jacobian, where Python raises) on a
                               box around the domain, within FASTLAW_BOUND
  test_fastlaw_records         the committed domain record (the grids cover it; no converging trial whose skipped
                               Jacobian would have been non-finite) and the committed fast-vs-libm deviation (dBc)
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
    # #135: the fast law on every case: within FASTLAW_BOUND["stage_rel_peak"] of the libm stage's output
    assert r["fast_cases"] == r["cases"] and r["fast_vs_libm_max_rel_peak"] <= FASTLAW_BOUND["stage_rel_peak"]


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


def test_validate_shipped_6as6():
    """The #102-#113 resampler (the shipped FIR, which the committed Python results no longer use) against the
    same-model ngspice references: every supported setting passes every dk/validate.CRIT criterion, standard and hq."""
    _exe()
    out, bad = VC.main(workers=os.cpu_count(), write=False, fir="shipped")
    assert not bad, [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    assert len(out["rows"]) == 44 and all(r["fir"] == "shipped" for r in out["rows"])


def test_k140_design_is_one_design():
    """dk6as6.stage.K140 (the Python stage's default since #134) is #113's derived k140 (resampler_study.CANDIDATES)
    and cpp/dk_core.h's K140_FIR, and the C++ core's DEFAULT_FIR is k140."""
    from dk6as6 import stage as S, resampler_study as RS
    assert S.FIR == "k140" and {float(k): v for k, v in RS.CANDIDATES["k140"].items()} == S.K140
    src = open(os.path.join(ROOT, "cpp", "dk_core.h")).read()
    assert "K140_FIRST_BY_RATE[] = {{48000.0, 123}, {44100.0, 219}}" in src and "K140_BETA = 14.47" in src
    assert "K140_FIR{123, K140_BETA, 47, K140_BETA" in src
    assert "DEFAULT_FIR = dkcore::K140_FIR" in open(os.path.join(ROOT, "cpp", "dk6as6_core.h")).read()


def test_cpp_stage_takes_the_nominal_grid_shape_only():
    """dk6as6.validate.stage names grid_shape (#139); the C++ core has the nominal law only, so CppStage accepts
    pentode.GRID_SHAPE and refuses #131's bounds before running anything."""
    for s in pentode.GRID_BOUNDS:
        with pytest.raises(NotImplementedError, match="nominal grid shape"):
            VC.CppStage(48000.0, bias=0.5, mode="T", experimental=True, grid_shape=s)


def test_validate_reproduced_6as6():
    _exe()
    out, bad = VC.main(workers=os.cpu_count(), write=False)
    assert not bad, [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    assert len(out["rows"]) == 44


@pytest.mark.parametrize("stereo", [False, True], ids=["mono", "stereo_vector"])
def test_validate_fast_6as6(stereo):
    """#135: the fast valve law (and the two-lane kernel with vector lane maths) reproduces every committed Python
    result within the libm path's LIMITS, every criterion as Python's, every supported setting passing."""
    _exe()
    out, bad = VC.main(workers=os.cpu_count(), write=False, math="fast", stereo=stereo)
    assert not bad, [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    assert len(out["rows"]) == 44 and all(r["math"] == "fast" and r["stereo"] == stereo for r in out["rows"])


# ---- #135: the two-lane kernel and the fast valve law ---------------------------------------------------------------

ARM64 = platform.machine().lower() in ("arm64", "aarch64")


def _stereo_exact_expected(flags, scalar, compiler, fma):
    """As cpp/tests/test_cpp_core.py (cpp/README.md, "Stereo"): bit-exact when nothing is contracted (-ffp-contract=off,
    or no FMA instructions), or with clang's per-expression contraction on the native NEON / SSE2 types; not
    guaranteed for the struct fallback with contraction, nor for GCC's -ffp-contract=fast with FMA."""
    if "-ffp-contract=off" in flags or not fma:
        return True
    return not scalar and "clang" in compiler.lower()


STEREO_BUILDS = [(("-O2",), False), (("-O3",), False), (("-O2", "-ffp-contract=off"), False),
                 (("-O2",), True), (("-O2", "-ffp-contract=off"), True), (("-O2", "-march=x86-64-v3"), False)]


@pytest.mark.parametrize("flags,scalar", STEREO_BUILDS, ids=lambda v: "scalar" if v is True else
                         "simd" if v is False else " ".join(v))
def test_stereo_parity_6as6(flags, scalar):
    """Every vector case on StereoStage (left: the case's input, right: 0.6 x it reversed) against two mono Stages,
    for libm, per-lane fast and vector fast lane maths; the solvers on 32 400 fuzzed lanes (lanes diverging in
    iterations and restarts, non-finite starts, max_it 1..50), each lane's internal record and the kernel's iteration
    count; per-lane recovery, float I/O, the FP mode."""
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
    assert r["stereo_runs"] == 90 and r["vector_runs"] == 45 and r["stereo_lanes_differ_runs"] >= 100
    assert r["stereo_solve_lanes"] == 32400 and r["stereo_solve_lanes_differ"] >= 5000
    assert r["stereo_solve_restart_differ"] >= 500
    # #135 review: each lane's internal record (Vg1k, Vg2k, Vak, Ig1, Ip + Ig2, iterations) is its mono Stage's; the
    # kernel's iteration count is per start the longer lane's, summed, also where the longer lane changes
    assert r["stereo_record_runs"] == 135 and r["stereo_record_err"] <= 1e-9
    assert r["stereo_kernel_longest_changes"] > 0
    if r["stereo_solve_flag_mismatch"] == 0:
        assert r["stereo_kernel_mismatch"] == 0
    if _stereo_exact_expected(flags, scalar, r["compiler"], r["fma"]):
        assert r["stereo_exact"] == 1 and r["vector_exact"] == 1, f"{r['compiler']} fma={r['fma']}\n" + p.stderr[-3000:]


# The fast law's error bounds (cpp/RESULTS-cpu-6as6.md, "Accuracy evidence"): measured worst (Apple clang 17 -O2,
# macOS libm, 28 Nov 2026) in the comment, asserted with margin. Kernels: relative to libm, where libm's result is a
# positive normal (below the fast domain exp / log1p call libm: bit-equal). Law: each current against its terms (#18's
# convention): the grid law's Ip, Ig2 against Ik + Ig1 (they are (Ik - Ig1) f and (Ik - Ig1)(1 - f); a plain relative
# error has no bound where Ig1 ~ Ik), the two-port law's against Ik; Ig1 relative; each Jacobian entry against its
# row's L1 norm. LAW_I_CUTOFF is a test cutoff, not the subnormal range: below it (the two-port law only: Ik
# underflowing towards E1^EX's subnormal range, where E1^EX / E1 loses relative precision; 0 in the stage under
# FTZ / DAZ below 2.2e-308) the absolute error stays below it. "raised" (where Python raises) identical everywhere.
FASTLAW_BOUND = dict(
    exp=1e-12,            # 3.87e-13 (dk/fastmath.py BOUND["exp"], #18)
    log1p=3e-13,          # 9.24e-14 (dk/fastmath.py BOUND["log1p"])
    pow_EX=1e-12,         # 5.17e-13, bases to 1e-300 (|EX log s| to 1029); 3.93e-13 where |EX log s| <= 50
    pow_XA=1e-12,         # 3.88e-13
    pow_XI=1e-12,         # 4.13e-13
    law_i=3e-12,          # grid law vs Ik + Ig1 1.17e-12, two-port vs Ik 1.21e-12 (#18's triode: 3e-12)
    law_ig1=3e-12,        # 1.01e-12
    law_j=3e-11,          # 8.4e-12
    stage_rel_peak=1e-10,  # test_core_6as6 fast_vs_libm_max_rel_peak: 3.68e-12 (3.46e-11 V, case P1_g_0.6_s8)
    deviation_dbc=-180.0,  # cpp/results_fastlaw_6as6.json: peak -205.4 dBc, RMS -210.4, largest line -211.8
)
LAW_I_CUTOFF = 1e-290
DBL_MIN = 2.2250738585072014e-308


def _fastlaw_domain():
    path = os.path.join(ROOT, "cpp", "fastlaw_domain_6as6.json")
    return json.load(open(path))


def _kernel_grids(rng):
    """(name, fn, x, y) grids over the recorded domain with margin (exp to EXP_LO and below, log1p on (0, 1] and 0,
    each pow on (0, 1.25 x the largest base])."""
    from dk import fastmath as fm
    d = _fastlaw_domain()["ranges"]
    n = 100000
    out = [("exp", 0, np.concatenate([np.linspace(fm.EXP_LO, 0.0, n), -np.logspace(-40, np.log10(-fm.EXP_LO), n),
                                      np.linspace(d["exp"]["min"] * 1.25, fm.EXP_LO, 2000)]), 0.0),
           ("log1p", 1, np.concatenate([np.logspace(-300, 0, n), np.linspace(1e-6, 1.0, n), [0.0, 1.0]]), 0.0)]
    names = {pentode.DUAL_CONTROL["EX"]: "pow_EX", pentode.DUAL_CONTROL["XA"]: "pow_XA", pentode.GRID["XI"]: "pow_XI"}
    for k, v in d.items():
        if k.startswith("pow_y") and not k.startswith("pow_ylog"):
            g = float(k[len("pow_y"):])
            hi = v["max"] * 1.25
            lo = max(v["min"], 1e-300) if v["min"] > 0 else 1e-300
            out.append((names[g], 2, np.concatenate([np.logspace(np.log10(lo), np.log10(hi), n),
                                                     np.linspace(0.0, hi, n)[1:], rng.uniform(0.0, hi, n)]), g))
    return out


def test_fastlaw_kernel_bounds():
    exe = _exe()
    grids = _kernel_grids(np.random.default_rng(135))
    assert sorted(g[0] for g in grids) == ["exp", "log1p", "pow_EX", "pow_XA", "pow_XI"]
    for name, fn, x, y in grids:
        o = _pipe(exe, "fm", np.column_stack([np.full_like(x, fn), x, np.full_like(x, y)])).reshape(-1, 2)
        normal = o[:, 0] >= DBL_MIN
        err = np.max(np.abs(o[normal, 1] / o[normal, 0] - 1))
        assert err <= FASTLAW_BOUND[name], (name, err)
        if fn < 2:      # exp / log1p outside the fast domain call libm: its bits
            assert np.array_equal(o[~normal, 0], o[~normal, 1]), name
        else:           # pow with a subnormal result (libm exp of the fast g log s; 0 in the stage, FTZ): absolute
            assert np.max(np.abs(o[~normal, 1] - o[~normal, 0]), initial=0.0) <= 1e-12 * DBL_MIN, name


def test_fastlaw_law_bounds():
    exe = _exe()
    rng = np.random.default_rng(1351)
    n = 200000
    pts = np.column_stack([rng.uniform(-30.0, 5.0, n), rng.uniform(-5.0, 250.0, n), rng.uniform(-30.0, 30.0, n),
                           rng.uniform(-10.0, 450.0, n)])
    pts[:10000, 1] = rng.uniform(-1e-3, 2e-3, 10000)          # the Vg2 floor
    pts[10000:20000, 3] = rng.uniform(-1e-3, 2e-3, 10000)     # the Va floor and the knee
    edge = np.array([[0.0, 120.0, 0.0, 1e160], [1e5, 1e-3, 0.0, 100.0], [600.0, 120.0, 0.0, 150.0],
                     [-800.0, 120.0, 0.0, 150.0], [0.0, 0.0, 0.0, 0.0], [2.0, -5.0, -50.0, -5.0]])
    pts = np.vstack([pts, edge])
    o = _pipe(exe, "fastlaw", pts).reshape(-1, 2, 26)
    L, F = o[:, 0], o[:, 1]
    assert np.array_equal(L[:, 15], F[:, 15])                 # where Python would raise
    L, F = L[L[:, 15] == 0], F[L[:, 15] == 0]
    ik = L[:, 16] + L[:, 17]          # the cathode current Ik = Ip + Ig2 of the two-port law at the same point (libm)
    ig1 = np.abs(L[:, 2])
    # the grid law: Ip = (Ik - Ig1) f and Ig2 = (Ik - Ig1)(1 - f), so their terms are Ik and Ig1 (#18's convention,
    # dk/fastmath.py BOUND["currents"]): the error against Ik + Ig1 where that is >= LAW_I_CUTOFF (every random point:
    # Ig1 never underflows in the box), absolute below it (the edge rows, e.g. Vg1k = -800 V or all-zero voltages)
    terms = np.abs(ik) + ig1
    tb = terms >= LAW_I_CUTOFF
    di = np.max(np.abs(F[:, 0:2] - L[:, 0:2]), axis=1)
    assert np.mean(tb) > 0.9999 and np.max(di[tb] / terms[tb]) <= FASTLAW_BOUND["law_i"]
    assert np.max(di[~tb], initial=0.0) <= LAW_I_CUTOFF
    g1 = ig1 >= LAW_I_CUTOFF
    assert np.max(np.abs(F[g1, 2] / L[g1, 2] - 1)) <= FASTLAW_BOUND["law_ig1"]
    J, JF = L[:, 3:15].reshape(-1, 3, 4), F[:, 3:15].reshape(-1, 3, 4)
    rs = np.sum(np.abs(J), axis=2, keepdims=True)
    assert np.max((np.abs(JF - J) / np.where(rs > 0, rs, 1.0))[terms >= LAW_I_CUTOFF]) <= FASTLAW_BOUND["law_j"]
    # the two-port law (no grid current): the error against Ik where Ik >= LAW_I_CUTOFF; below it, absolute
    big = np.abs(ik) >= LAW_I_CUTOFF
    d2 = np.max(np.abs(F[:, 16:18] - L[:, 16:18]), axis=1)
    assert np.max(d2[big] / np.abs(ik[big])) <= FASTLAW_BOUND["law_i"]
    assert np.max(d2[~big], initial=0.0) <= LAW_I_CUTOFF
    J2, J2F = L[:, 18:26].reshape(-1, 2, 4), F[:, 18:26].reshape(-1, 2, 4)
    rs2 = np.sum(np.abs(J2), axis=2, keepdims=True)
    assert np.max((np.abs(J2F - J2) / np.where(rs2 > 0, rs2, 1.0))[big]) <= FASTLAW_BOUND["law_j"]


@pytest.mark.parametrize("cmd", ["sweep", "scan", "guardgrid"])
def test_aliasing_path_selectors_guard_only(cmd):
    """#135 review: --math / --stereo would mix paths in sweep / scan's shared cache and write them to the canonical
    (libm, mono) records, so only the guard takes them."""
    for opt in (["--math", "fast"], ["--stereo"]):
        p = subprocess.run([sys.executable, "-m", "cpp.aliasing_6as6", cmd, *opt], cwd=ROOT, capture_output=True,
                           text=True)
        assert p.returncode == 2 and "guard command only" in p.stderr, (cmd, opt, p.stderr[-500:])


def test_cpp_stage_stereo_records_its_own_lane():
    """#135 review: with STEREO, CppStage takes the output and the internal signals (the burst criteria's Vg1k, Vg2k)
    from one two-lane run, the left lane's; an output-only call works. The stereo lane equals the mono stage bit for
    bit on builds that guarantee it, else within the vectors' TOL_PINNED_ABS (1e-9 V)."""
    _exe()
    x = 4.0 * np.sin(2 * np.pi * 1000.0 * np.arange(4800) / 48000.0)
    saved = (VC.STEREO, VC.MATH)
    try:
        out = {}
        for stereo in (False, True):
            VC.STEREO, VC.MATH = stereo, "fast"
            st = VC.CppStage(48000.0, mode="P1")
            out[stereo] = st.process(x, record=("out", "vgk", "vg2k"))
            y = VC.CppStage(48000.0, mode="P1").process(np.zeros(1))["out"]     # output only
            assert y.shape == (1,) and np.isfinite(y).all()
        for k in ("vgk", "vg2k"):
            assert out[True]["_internal"][k].shape == (4800 * 8,)
            assert np.max(np.abs(out[True]["_internal"][k] - out[False]["_internal"][k])) <= 1e-9
        assert np.max(np.abs(out[True]["out"] - out[False]["out"])) <= 1e-9
    finally:
        VC.STEREO, VC.MATH = saved


def test_fastlaw_records():
    """cpp/fastlaw_domain_6as6.json (python -m cpp.fastlaw_6as6 domain) and cpp/results_fastlaw_6as6.json (deviation):
    every validation setting ran and passed with both maths; the lazier Jacobian skipped the Jacobian of many
    converging trials and none of them would have been non-finite (so skipping it changed no result, #135); the
    kernel grids above cover every recorded argument range; the fast path's deviation from libm is within
    FASTLAW_BOUND["deviation_dbc"] at every point, mono and stereo, with libm's Newton iterations."""
    from dk import fastmath as fm
    d = _fastlaw_domain()
    assert d["validation"] == {"libm": {"rows": 44, "failed": 0}, "fast": {"rows": 44, "failed": 0}}
    for m in ("libm", "fast"):
        lj = d["lazy_jacobian"][m]
        assert lj["skipped_jacobians"] > 1e8 and lj["skipped_nonfinite"] == 0, (m, lj)
    grids = {g[0]: g for g in _kernel_grids(np.random.default_rng(0))}
    r = d["ranges"]
    assert grids["exp"][2].min() <= r["exp"]["min"] and r["exp"]["max"] <= 0.0
    assert 0.0 <= r["log1p"]["min"] and r["log1p"]["max"] <= 1.0
    for k, v in r.items():
        if k.startswith("pow_y") and not k.startswith("pow_ylog"):
            g = float(k[len("pow_y"):])
            name = {pentode.DUAL_CONTROL["EX"]: "pow_EX", pentode.DUAL_CONTROL["XA"]: "pow_XA",
                    pentode.GRID["XI"]: "pow_XI"}[g]
            assert grids[name][2].max() >= v["max"] and v["min"] >= 0.0, k
    assert fm.EXP_LO <= 0.0
    dev = json.load(open(os.path.join(ROOT, "cpp", "results_fastlaw_6as6.json")))
    assert len(dev["rows"]) == 8 * 2 * 4 * 3
    for row in dev["rows"]:
        for tag in ("fast", "fast_stereo"):
            q = row[tag]
            assert q["peak_dbc"] <= FASTLAW_BOUND["deviation_dbc"] and q["nonconverged"] == 0, (row, tag)
            assert abs(q["iterations"] - q["iterations_libm"]) <= 1e-3 * q["iterations_libm"], (row, tag)


BENCH_KEYS = {"name", "quality", "os", "variant", "mode", "bias_ma", "grid_hardness", "lpf", "fir", "level_v", "block",
              "instances", "ns_per_sample", "ns_min", "ns_max", "pct_core", "block_pct_p50", "block_pct_p99",
              "block_pct_max", "cpu_on_fraction", "cycles_per_sample", "instr_per_sample", "ghz_on_cpu", "newton_mean",
              "newton_max", "nonconverged", "restarted", "deterministic"}


def _bench_rows_ok(rows, meta=None):
    # 60 rows before #135; #135 adds 60 (x1 / x2 fast, s2, s2 fast per-lane, v2 at the default setting)
    assert len(rows) in (60, 120) and len({r["name"] for r in rows}) == len(rows)
    for r in rows:
        assert BENCH_KEYS <= set(r), r["name"]
        assert r["deterministic"] and r["ns_per_sample"] > 0 and r["newton_mean"] >= 1.0, r
        assert r["nonconverged"] == 0 and r["newton_max"] <= 3 * 50, r
        if r.get("path", "mono") != "mono":     # #135: the two-lane kernel against two mono Stages of the same maths:
            # bit-identical where the build guarantees it (as the 12AX7 bench test); else test_stereo_parity_6as6's
            # tolerance holds for that build
            assert r["newton_mean_max"] >= max(r["newton_mean"], r["newton_mean_r"]), r
            if meta and _stereo_exact_expected(("-O2",), False, meta["compiler"], meta["fma"]):
                assert r["matches_x2"] is True, r


def test_bench_6as6_smoke():
    if B.compiler() is None:
        pytest.skip("no C++ compiler")
    exe = B.build(name="bench_6as6")
    names = subprocess.run([exe, "--list"], capture_output=True, text=True, check=True).stdout.split()
    assert len(names) == 2 * 3 * 2 * 2 + 6 * 2 * 3 + 2 * 3 * 2 * 5   # default, 6 variants, #135's 5 paths
    p = subprocess.run([exe, "--seconds", "0.011", "--reps", "1"], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]           # 1: an output differed between passes
    d = json.loads(p.stdout)
    assert d["meta"]["samples_per_pass"] == 512 and d["meta"]["core"] == "cpp/dk6as6_core.h"
    _bench_rows_ok(d["results"], d["meta"])


def test_bench_6as6_records():
    import glob
    files = sorted(glob.glob(os.path.join(ROOT, "cpp", "bench_6as6_*.json")))
    if not files:
        pytest.skip("no cpp/bench_6as6_*.json committed")
    for f in files:
        d = json.load(open(f))
        assert {"host", "build", "state_before", "state_after", "meta", "results"} <= set(d), f
        assert d["meta"]["reps"] >= 5 and not d["build"]["dirty"] and d["build"]["source"] == "cpp/bench_6as6.cpp", f
        _bench_rows_ok(d["results"], d["meta"])


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
