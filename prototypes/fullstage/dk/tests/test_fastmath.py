"""#18: error bounds for dk/fastmath.py (and, when gcc is present, bit-match vs dk/fastmath.h).

Bounds are relative against glibc libm on dense grids that cover the stage's recorded argument ranges
(dk/fastmath_domain.json) and a bit beyond. The test fails if any sample exceeds fastmath.BOUND.
"""
import json, math, os, shutil, struct, subprocess, sys, tempfile
import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
from dk import fastmath as F
from dk import triode as T
from dk.triode import DZ_12AX7_RSD1

DOMAIN = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "fastmath_domain.json")))


def _max_rel(f, ref, xs):
    w = 0.0
    for x in xs:
        a, b = f(x), ref(x)
        if not (math.isfinite(a) and math.isfinite(b)) or b == 0.0:
            continue
        w = max(w, abs(a - b) / abs(b))
    return w


def test_bound_exp():
    xs = np.concatenate([np.linspace(F.EXP_LO, F.EXP_HI, 20001),
                         [F.EXP_LO, F.EXP_HI, -100.0, -1.0, 0.0, 0.04, 1.0, 100.0]])
    assert _max_rel(F.fast_exp, math.exp, xs) <= F.BOUND["exp"]


def test_bound_log1p():
    xs = np.concatenate([np.linspace(0.0, 1.0, 20001), np.logspace(-300, 0, 4001), [0.0, 1.0]])
    assert _max_rel(F.fast_log1p, math.log1p, xs) <= F.BOUND["log1p"]


def test_bound_log():
    xs = np.concatenate([np.logspace(-300, 3, 20001), np.linspace(0.5, 1.5, 4001),
                         [sys.float_info.min, 0.6875, 1.0, 1.375]])
    assert _max_rel(F.fast_log, math.log, xs) <= F.BOUND["log"]


@pytest.mark.parametrize("g,key", [(1.26, "pow_g1_26"), (1.314, "pow_g1_314")])
def test_bound_pow(g, key):
    r = DOMAIN["ranges"][f"pow_s_g{g}"]
    lo, hi = r["min"], r["max"]
    xs = np.concatenate([
        np.logspace(math.log10(max(lo * 0.1, 1e-300)), math.log10(max(hi * 2, 1e-6)), 15001),
        np.linspace(max(lo, 1e-30), max(hi * 1.1, lo + 1e-12), 4001),
    ])
    assert _max_rel(lambda s: F.fast_pow(s, g), lambda s: s ** g, xs) <= F.BOUND[key]


def test_bound_covers_recorded_domain():
    """The dense grids above cover the ranges recorded by dk/fastmath_domain.py on hq/standard/eco."""
    r = DOMAIN["ranges"]
    assert r["exp_x"]["min"] >= F.EXP_LO and r["exp_x"]["max"] <= F.EXP_HI
    assert 0.0 <= r["log1p_e"]["min"] and r["log1p_e"]["max"] <= 1.0
    assert r["pow_s_g1.26"]["min"] > 0 and r["pow_s_g1.314"]["min"] > 0


def test_bound_currents():
    vgk = np.linspace(-4.0, 1.0, 61)
    vak = np.linspace(50.0, 350.0, 63)
    w = 0.0
    for g in vgk:
        for a in vak:
            fr, lr = T.currents_fast(g, a), T.currents(g, a)
            flat_f = (fr[0], fr[1], fr[2][0][0], fr[2][0][1], fr[2][1][0], fr[2][1][1])
            flat_l = (lr[0], lr[1], lr[2][0][0], lr[2][0][1], lr[2][1][0], lr[2][1][1])
            for f, l in zip(flat_f, flat_l):
                if abs(l) < 1e-300:
                    continue
                w = max(w, abs(f - l) / abs(l))
    assert w <= F.BOUND["currents"]


def _build_check():
    if shutil.which("gcc") is None:
        pytest.skip("gcc missing")
    dk = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fastmath_check.c")
    exe = os.path.join(tempfile.mkdtemp(), "fastmath_check")
    cmd = ["gcc", "-O2", "-ffp-contract=off", "-fno-builtin-exp", "-fno-builtin-log",
           "-fno-builtin-log1p", "-fno-builtin-pow", "-I", dk, "-o", exe, src, "-lm"]
    subprocess.run(cmd, check=True)
    return exe


def _pipe(exe, args, xs):
    raw = struct.pack(f"{len(xs)}d", *xs)
    p = subprocess.run([exe, *args], input=raw, capture_output=True, check=True)
    n = len(p.stdout) // 8
    return struct.unpack(f"{n}d", p.stdout)


def test_c_matches_python_kernels():
    """With -ffp-contract=off, C and Python return the same bits on a sample grid."""
    exe = _build_check()
    xs_exp = np.linspace(-40.0, 40.0, 401).tolist()
    py = [F.fast_exp(x) for x in xs_exp]
    c = _pipe(exe, ["exp"], xs_exp)
    assert list(c) == py
    xs_l1 = np.linspace(0.0, 1.0, 401).tolist()
    assert list(_pipe(exe, ["log1p"], xs_l1)) == [F.fast_log1p(x) for x in xs_l1]
    xs_log = np.logspace(-20, 2, 401).tolist()
    assert list(_pipe(exe, ["log"], xs_log)) == [F.fast_log(x) for x in xs_log]
    for g in (1.26, 1.314):
        xs = np.logspace(-10, 0, 201).tolist()
        assert list(_pipe(exe, ["pow", str(g)], xs)) == [F.fast_pow(x, g) for x in xs]


def test_c_currents_within_bound():
    exe = _build_check()
    pts = [(g, a) for g in np.linspace(-4.0, 1.0, 21) for a in np.linspace(50.0, 350.0, 21)]
    raw = struct.pack(f"{2*len(pts)}d", *[v for p in pts for v in p])
    p = subprocess.run([exe, "currents"], input=raw, capture_output=True, check=True)
    outs = struct.unpack(f"{6*len(pts)}d", p.stdout)
    w = 0.0
    for i, (g, a) in enumerate(pts):
        c = outs[6*i:6*i+6]
        lr = T.currents(g, a)
        flat_l = (lr[0], lr[1], lr[2][0][0], lr[2][0][1], lr[2][1][0], lr[2][1][1])
        for f, l in zip(c, flat_l):
            if abs(l) < 1e-300:
                continue
            w = max(w, abs(f - l) / abs(l))
    assert w <= F.BOUND["currents"]
