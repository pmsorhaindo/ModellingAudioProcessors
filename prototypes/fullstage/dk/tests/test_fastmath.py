"""#18 / #60: error bounds for dk/fastmath.py (and, when gcc is present, bit-match vs dk/fastmath.h).

Kernel bounds are relative against glibc libm on dense grids that cover the stage's recorded argument ranges
(dk/fastmath_domain.json) and a bit beyond. The currents bound (#60) is componentwise against the magnitude of the
terms each output is formed from (currents_err), so it stays defined where Ia = Ik - Ig or dIa/dVgk = dIk - dIg
cross zero. A test fails if any sample exceeds fastmath.BOUND.
"""
import json, math, os, shutil, signal, struct, subprocess, sys, tempfile
import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
from dk import fastmath as F
from dk import triode as T
from dk.triode import DZ_12AX7_RSD1

DOMAIN = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "fastmath_domain.json")))

# ---- grids (test_bound_covers_recorded_domain checks them against DOMAIN) --------------------------------
# log near 1 (#60): 1 -+ 2^-k and the neighbours of 1. Before #60 the bin [1 - 2^-8, 1) had centre
# 0.998046875, so log x = ln c + P(r) cancelled as x -> 1-: relative error 1.1e-10 at 1 - 2^-20, 0.95 at
# nextafter(1, 0).
NEAR_ONE = ([1.0 - 2.0 ** -k for k in range(1, 54)] + [1.0 + 2.0 ** -k for k in range(1, 53)]
            + [math.nextafter(1.0, 0.0), math.nextafter(1.0, 2.0)])
GRID_EXP = np.concatenate([np.linspace(F.EXP_LO, F.EXP_HI, 20001), np.linspace(F.EXP_LO, F.EXP_LO + 2.0, 2001),
                           [F.EXP_LO, F.EXP_HI, -100.0, -1.0, 0.0, 0.04, 1.0, 100.0]])
GRID_LOG1P = np.concatenate([np.linspace(0.0, 1.0, 20001), np.logspace(-300, 0, 4001), [0.0, 1.0]])
GRID_LOG = np.concatenate([np.logspace(-307, 308, 20001), np.linspace(0.5, 1.5, 4001),
                           1.0 - np.logspace(-16, -2, 4001), 1.0 + np.logspace(-16, -2, 4001), NEAR_ONE,
                           [sys.float_info.min, 0.6875, 1.0, 1.375]]
                          + [np.linspace(lo, hi, 41)[:-1] for lo, hi, c in F.log_bins()])


def grid_pow(g):
    r = DOMAIN["ranges"][f"pow_s_g{g}"]
    lo, hi = r["min"], r["max"]
    return np.concatenate([
        np.logspace(math.log10(max(lo * 0.1, 1e-300)), math.log10(max(hi * 2, 1e-6)), 15001),
        np.linspace(max(lo, 1e-30), max(hi * 1.1, lo + 1e-12), 4001),
        [s for s in NEAR_ONE if lo * 0.1 <= s <= hi * 2],
    ])


# (Vgk, Vak) box for the currents: covers DOMAIN's recorded vgk / vak (fastmath_domain.py) with margin.
CUR_VGK, CUR_VAK = (-16.0, 2.0), (0.0, 450.0)
CUR_N = 121


def _max_rel(f, ref, xs):
    w = 0.0
    for x in xs:
        x = float(x)
        a, b = f(x), ref(x)
        if b == 0.0:
            assert a == 0.0, x
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        w = max(w, abs(a - b) / abs(b))
    return w


def test_bound_exp():
    assert _max_rel(F.fast_exp, math.exp, GRID_EXP) <= F.BOUND["exp"]


def test_bound_log1p():
    assert _max_rel(F.fast_log1p, math.log1p, GRID_LOG1P) <= F.BOUND["log1p"]
    assert math.copysign(1.0, F.fast_log1p(-0.0)) == -1.0          # e <= 0 goes to libm (#60)


def test_bound_log():
    assert _max_rel(F.fast_log, math.log, GRID_LOG) <= F.BOUND["log"]


def test_log_near_one_is_relative():
    """#60: a true relative bound as x -> 1 from either side (the pre-#60 tables gave 0.95 at nextafter(1, 0))."""
    assert _max_rel(F.fast_log, math.log, NEAR_ONE) <= F.BOUND["log"]
    assert F.fast_log(1.0) == 0.0
    for x in (math.nextafter(1.0, 0.0), 1.0 - 2.0 ** -40, 1.0 - 2.0 ** -20):
        assert F.fast_log(x) == math.log(x)          # P(r) with r = x - 1 exact: r - r^2/2 rounds to libm here


@pytest.mark.parametrize("g,key", [(1.26, "pow_g1_26"), (1.314, "pow_g1_314")])
def test_bound_pow(g, key):
    assert _max_rel(lambda s: F.fast_pow(s, g), lambda s: s ** g, grid_pow(g)) <= F.BOUND[key]


def test_bound_covers_recorded_domain():
    """The dense grids above cover the ranges recorded by dk/fastmath_domain.py on hq/standard/eco."""
    r = DOMAIN["ranges"]
    assert min(GRID_EXP) <= r["exp_x"]["min"] and r["exp_x"]["max"] <= max(GRID_EXP)
    assert min(GRID_LOG1P) <= r["log1p_e"]["min"] and r["log1p_e"]["max"] <= max(GRID_LOG1P)
    assert 0.0 < min(x for x in GRID_LOG1P if x > 0) <= r["log1p_e_pos"]["min"]
    for g in (1.26, 1.314):
        xs = grid_pow(g)
        assert 0.0 < xs.min() <= r[f"pow_s_g{g}"]["min"] and r[f"pow_s_g{g}"]["max"] <= xs.max()
    assert CUR_VGK[0] <= r["vgk"]["min"] and r["vgk"]["max"] <= CUR_VGK[1]
    assert CUR_VAK[0] <= r["vak"]["min"] and r["vak"]["max"] <= CUR_VAK[1]


# ---- currents (#60: componentwise against the term magnitudes) --------------------------------------------
def currents_err(fast, ref, mu=DZ_12AX7_RSD1["mu"]):
    """max over the 6 outputs of |fast - ref| / scale, scale = the sum of the magnitudes of the terms the output is
    formed from (ref): Ig = Gg sg^xi + Ig0 -> |Ig|; Ia = Ik - Ig -> Ik + Ig; dIg/dVgk -> |dIg|;
    dIa/dVgk = dIk - dIg -> |dIk| + |dIg|; dIa/dVak = dIk / mu -> |dIa/dVak|; dIg/dVak = 0 (exact). The per-element
    |fast - ref| / |ref| used before #60 has no bound where Ia crosses zero (3e3 on the Ia = 0 curve)."""
    ig, ia, J = ref
    dig, dik = J[0][0], J[1][1] * mu
    pairs = ((fast[0], ig, abs(ig)), (fast[1], ia, abs(ia + ig) + abs(ig)), (fast[2][0][0], dig, abs(dig)),
             (fast[2][0][1], J[0][1], 0.0), (fast[2][1][0], J[1][0], abs(dik) + abs(dig)),
             (fast[2][1][1], J[1][1], abs(J[1][1])))
    w = 0.0
    for f, l, s in pairs:
        if s == 0.0:
            assert f == l, (f, l)
            continue
        w = max(w, abs(f - l) / s)
    return w


def _ia_zero(vgk, p=DZ_12AX7_RSD1):
    """Vak where libm Ia(Vgk, Vak) = 0 (Ia increases with Vak), or None if no sign change on [0, 1e4]."""
    lo, hi = 0.0, 1e4
    if T.currents(vgk, lo, p)[1] > 0.0 or T.currents(vgk, hi, p)[1] < 0.0:
        return None
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if m in (lo, hi):
            break
        lo, hi = (m, hi) if T.currents(vgk, m, p)[1] < 0.0 else (lo, m)
    return lo


IA_ZERO = [(g, a) for g in np.linspace(-6.0, -2.5, 36).tolist() for a in [_ia_zero(g)] if a is not None]
PTS = ([(g, a) for g in np.linspace(*CUR_VGK, CUR_N).tolist() for a in np.linspace(*CUR_VAK, CUR_N).tolist()]
       + [(g, b) for g, a in IA_ZERO for b in (a, math.nextafter(a, 0.0), math.nextafter(a, 1e9))])


def test_currents_points_cross_ia_zero():
    assert len(IA_ZERO) >= 30
    for g, a in IA_ZERO:
        ig, ia, _ = T.currents(g, a)
        assert abs(ia) <= 1e-12 * ig                   # Ik = Ig: Ia cancels completely


def test_bound_currents():
    w = max(currents_err(T.currents_fast(g, a), T.currents(g, a)) for g, a in PTS)
    assert w <= F.BOUND["currents"]


def test_currents_only_fast_matches_currents_fast():
    for g, a in PTS[::7]:
        assert T.currents_only_fast(g, a) == T.currents_fast(g, a)[:2]


# ---- C (dk/fastmath.h, dk/triode_eval.h) ----------------------------------------------------------------
NOFMA = ["-O2", "-ffp-contract=off"]
FMA = ["-O2", "-march=x86-64-v3"]            # AVX2 + FMA; gcc contracts a * b + c by default (GNU C)
_EXE = {}


def _build_check(flags):
    if shutil.which("gcc") is None:
        pytest.skip("gcc missing")
    key = tuple(flags)
    if key not in _EXE:
        dk = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fastmath_check.c")
        exe = os.path.join(tempfile.mkdtemp(), "fastmath_check")
        cmd = ["gcc", *flags, "-fno-builtin-exp", "-fno-builtin-log", "-fno-builtin-log1p", "-fno-builtin-pow",
               "-I", dk, "-o", exe, src, "-lm"]
        subprocess.run(cmd, check=True)
        _EXE[key] = exe
    return _EXE[key]


def _pipe(exe, args, xs):
    raw = struct.pack(f"{len(xs)}d", *map(float, xs))
    p = subprocess.run([exe, *args], input=raw, capture_output=True)
    if p.returncode == -signal.SIGILL:
        pytest.skip("this CPU cannot run the x86-64-v3 build")
    p.check_returncode()
    n = len(p.stdout) // 8
    return struct.unpack(f"{n}d", p.stdout)


def _c_currents(exe, pts):
    o = _pipe(exe, ["currents"], [v for p in pts for v in p])
    return [(o[6*i], o[6*i+1], ((o[6*i+2], o[6*i+3]), (o[6*i+4], o[6*i+5]))) for i in range(len(pts))]


KERNELS = (("exp", (), F.fast_exp, math.exp, "exp", GRID_EXP),
           ("log", (), F.fast_log, math.log, "log", GRID_LOG),
           ("log1p", (), F.fast_log1p, math.log1p, "log1p", GRID_LOG1P),
           ("pow", ("1.26",), lambda s: F.fast_pow(s, 1.26), lambda s: s ** 1.26, "pow_g1_26", grid_pow(1.26)),
           ("pow", ("1.314",), lambda s: F.fast_pow(s, 1.314), lambda s: s ** 1.314, "pow_g1_314", grid_pow(1.314)))


def _bits(v):
    return [struct.pack("d", float(x)) for x in v]


@pytest.mark.parametrize("name,args,fast,ref,key,xs", KERNELS, ids=["exp", "log", "log1p", "pow1.26", "pow1.314"])
def test_c_matches_python_kernels(name, args, fast, ref, key, xs):
    """With -ffp-contract=off, C and Python return the same bits on the full bound grids (stage domain, the EXP_LO
    edge where C adds to the exponent bits and Python calls ldexp, every log bin, x -> 1)."""
    exe = _build_check(NOFMA)
    assert _bits(_pipe(exe, [name, *args], xs)) == _bits(fast(float(x)) for x in xs)


def test_c_matches_python_currents():
    exe = _build_check(NOFMA)
    c = _c_currents(exe, PTS)
    assert c == [T.currents_fast(g, a) for g, a in PTS]


@pytest.mark.parametrize("name,args,fast,ref,key,xs", KERNELS, ids=["exp", "log", "log1p", "pow1.26", "pow1.314"])
def test_c_fma_kernels_within_bound(name, args, fast, ref, key, xs):
    """#60: the FMA build (the 1.39x configuration in bench_triode.json) is not bit-identical to Python; its kernels
    are held to the same BOUND against libm."""
    exe = _build_check(FMA)
    c = _pipe(exe, [name, *args], xs)
    w = 0.0
    for x, a in zip(xs, c):
        b = ref(float(x))
        if b == 0.0:
            assert a == 0.0, x
        elif math.isfinite(b):
            w = max(w, abs(a - b) / abs(b))
    assert w <= F.BOUND[key]


def test_c_currents_within_bound():
    for flags in (NOFMA, FMA):
        c = _c_currents(_build_check(flags), PTS)
        w = max(currents_err(f, T.currents(g, a)) for f, (g, a) in zip(c, PTS))
        assert w <= F.BOUND["currents"], flags


def test_c_fma_build_is_contracted():
    """The FMA tests above test contracted arithmetic: the FMA build's currents differ in bits from Python's."""
    c = _c_currents(_build_check(FMA), PTS)
    assert sum(f != T.currents_fast(g, a) for f, (g, a) in zip(c, PTS)) > 0
