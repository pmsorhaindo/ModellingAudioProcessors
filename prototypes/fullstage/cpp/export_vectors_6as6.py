"""Reference test vectors for the C++ 6AS6 core (#104, cpp/dk6as6_core.h), exported from the Python 6AS6 DK stage
(dk6as6/: stage.py, solver.py, circuit.py, pentode.py, lpf.py).

Writes cpp/vectors_6as6.txt, which cpp/test_core_6as6.cpp reads. Format as cpp/vectors.txt: one entry per line,
`name count v0 v1 ...`, every value written exactly (shortest round-trip repr); inputs rounded to 10 significant digits.
Contents:
  par.dual_control / par.grid           pentode.DUAL_CONTROL (13 values, in PentodeParams order) and pentode.GRID
  law.in / law.nogrid / law.grid        pentode.currents / currents_grid (values and Jacobian) on a voltage grid that
                                        covers the floors (Vg2 <= 1 mV, Va <= 1 mV), cut-off and positive g1
  solve2.* / solve3.* / restarts.*      solver.solve / solve_grid / solve_grid_restarts alone, from hard starts, with
                                        the default settings and capped at 1..3 iterations with zero tolerance
  knob.*                                stage.knob_bias / bias_knob
  pack.<mode>.<g|n>.<bias>.<os>.<fs>.*  one stage's pack: cfg, Rk, circuit.build matrices A..K, the DC operating point,
                                        its node voltages, and the deviation form's rest constants (x_dc, i_dc, p_dc,
                                        y_dc); g = the default grid current (#112), n = none (hardness 0)
  lpf.*                                 lpf.PostFilter at 8 x 48 kHz on noise, with two position changes (one mid-fade)
  taps.k140_* / rsk140_*                the #113 k140 FIR design (dk/resample.py fir=): taps, and up / down alone
  internal.*                            lpf.Stage.process_internal (no resampler): out and the recorded voltages
  case.<name>.*                         lpf.Stage.process at the base rate: cfg, input, output, iteration stats and
                                        histogram (every mode at 0.1 / 0.25 / 0.45 / 0.6 mA, with and without the grid
                                        current, 8 V drive; plus 2 V, hq, 44.1 kHz, the post-filter, gains, noise, a
                                        burst, forced non-convergence, the k140 resampler)
Run from prototypes/fullstage:  python -m cpp.export_vectors_6as6   (~1 min; rewrites cpp/vectors_6as6.txt)
cpp/tests/test_cpp_core_6as6.py checks that the committed file still matches what the Python stage produces now.
"""
import json
import os
import sys
import warnings
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk.resample import Resampler, _FirHB  # noqa: E402 -- after the sys.path setup
from dk6as6 import circuit, lpf, pentode, solver  # noqa: E402 -- after the sys.path setup
from dk6as6 import stage as S  # noqa: E402 -- after the sys.path setup

FS = 48000.0
PATH = os.path.join(HERE, "vectors_6as6.txt")
MODES = ("P1", "T", "P2")
PAR_KEYS = ("MU", "EX", "KG1", "KP", "FMAX", "MU3", "V3C", "S3", "P3", "RI", "KVB", "KR", "XA")
DEFAULT_NEWTON = (solver.MAX_IT, solver.TOL_V, solver.TOL_F)
K140 = S.K140                        # #113's k140 design, dk6as6.stage.K140 (the stage's default since #134)
FIRS = ("shipped", "k140")
LPFS = tuple(lpf.POSITIONS)          # off, 4k, 7k
N = 480                              # base samples per case (10 ms at 48 kHz)


def r10(x):
    """Round to 10 significant digits (exactly representable in the file)."""
    return np.array([float("%.10g" % v) for v in np.atleast_1d(np.asarray(x, float))])


def sine(a, n, f=1000.0, fs=FS):
    return r10(a * np.sin(2 * np.pi * f * np.arange(n) / fs))


def pack_name(mode, hardness, bias, os_, fs):
    return f"pack.{mode}.{'g' if hardness else 'n'}.{bias:g}.{os_}.{int(fs)}"


def stage(fs, os_, mode, hardness, bias, lpf_pos="off", fir="shipped"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", S.ExperimentalWarning)
        st = lpf.Stage(fs, lpf=lpf_pos, bias=bias, mode=mode, quality=(os_, None), experimental=True,
                       grid_hardness=hardness, fir=fir)
    return st


def law_points():
    vg1 = (-12.0, -1.5, -0.25, 0.0, 0.4, 1.5)
    vg2 = (0.0, 5e-4, 1e-3, 35.0, 120.0)
    vg3 = (-20.0, -4.0, 0.0, 5.0)
    va = (-5.0, 0.0, 5e-4, 1e-3, 2.0, 150.0, 310.0)
    return [(a, b, c, d) for a in vg1 for b in vg2 for c in vg3 for d in va]


def flat_law(r):
    return [r[0], r[1], *r[2][0], *r[2][1]]


def flat_law_grid(r):
    return [r[0], r[1], r[2], *r[3][0], *r[3][1], *r[3][2]]


def _signals(fs=FS):
    rng = np.random.default_rng(104)
    t = np.arange(2 * N) / fs
    return dict(s2=sine(2.0, N, fs=fs), s8=sine(8.0, N, fs=fs), s4=sine(4.0, N, fs=fs), s2_3k=sine(2.0, N, 3000.0, fs),
                s8_15k=sine(8.0, N, 15037.0, fs), noise3=r10(rng.uniform(-3.0, 3.0, N)),
                burst4=r10(4.0 * np.sin(2 * np.pi * 1000 * t) * ((t > 2e-3) & (t < 6e-3))))


# (name, fs, os, mode, hardness, bias, drive, output, lpf, (switch_at, to) or None, newton, fir, signal)
def _cases():
    d, out = DEFAULT_NEWTON, []
    for mode in MODES:
        for h in (1.0, 0.0):
            for b in (0.1, 0.25, 0.45, 0.6):
                out.append((f"{mode}_{'g' if h else 'n'}_{b:g}_s8", FS, 8, mode, h, b, 1.0, 1.0, "off", None, d,
                            "shipped", "s8"))
            out.append((f"{mode}_{'g' if h else 'n'}_0.45_s2", FS, 8, mode, h, 0.45, 1.0, 1.0, "off", None, d,
                        "shipped", "s2"))
    out += [
        ("hq_P1_g_0.45_s8", FS, 16, "P1", 1.0, 0.45, 1.0, 1.0, "off", None, d, "shipped", "s8"),
        ("hq_T_g_0.5_s8", FS, 16, "T", 1.0, 0.5, 1.0, 1.0, "off", None, d, "shipped", "s8"),
        ("r44_P1_g_0.45_s8", 44100.0, 8, "P1", 1.0, 0.45, 1.0, 1.0, "off", None, d, "shipped", "s8"),
        ("r44_hq_T_g_0.25_s8", 44100.0, 16, "T", 1.0, 0.25, 1.0, 1.0, "off", None, d, "shipped", "s8"),
        ("lpf7k_P1_g_0.45_s8", FS, 8, "P1", 1.0, 0.45, 1.0, 1.0, "7k", None, d, "shipped", "s8"),
        ("lpf4k_T_g_0.25_s8", FS, 8, "T", 1.0, 0.25, 1.0, 1.0, "4k", None, d, "shipped", "s8"),
        ("lpfswitch_P1_g_0.45_s8", FS, 8, "P1", 1.0, 0.45, 1.0, 1.0, "off", (240, "4k"), d, "shipped", "s8"),
        ("gain_P1_g_0.25_s2_3k", FS, 8, "P1", 1.0, 0.25, 2.5, 0.4, "off", None, d, "shipped", "s2_3k"),
        ("noise_P1_g_0.45", FS, 8, "P1", 1.0, 0.45, 1.0, 1.0, "off", None, d, "shipped", "noise3"),
        ("burst_T_g_0.45", FS, 8, "T", 1.0, 0.45, 1.0, 1.0, "off", None, d, "shipped", "burst4"),
        ("hf_T_g_0.6_s8_15k", FS, 8, "T", 1.0, 0.6, 1.0, 1.0, "off", None, d, "shipped", "s8_15k"),
        ("nonconv_P1_n_0.45_s4", FS, 8, "P1", 0.0, 0.45, 1.0, 1.0, "off", None, (1, 0.0, 0.0), "shipped", "s4"),
        ("nonconv_T_g_0.6_s8", FS, 8, "T", 1.0, 0.6, 1.0, 1.0, "off", None, (2, 0.0, 0.0), "shipped", "s8"),
        ("k140_P1_g_0.45_s8", FS, 8, "P1", 1.0, 0.45, 1.0, 1.0, "off", None, d, "k140", "s8"),
        ("k140_r44_hq_T_g_0.25_s8", 44100.0, 16, "T", 1.0, 0.25, 1.0, 1.0, "7k", None, d, "k140", "s8"),
    ]
    return out


def _run_case(fs, os_, mode, h, b, drive, output, lpf_pos, switch, newton, fir, x):
    st = stage(fs, os_, mode, h, b, lpf_pos, fir)
    st.drive, st.output = drive, output
    st.newton = tuple(newton)                # runtime only: Rk and the DC point above used the defaults
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if switch is None:
            y = st.process(x)["out"]
        else:
            at, to = switch
            y1 = st.process(x[:at])["out"]
            st.lpf.set_position(to)
            y = np.r_[y1, st.process(x[at:])["out"]]
    it = np.concatenate(st.iters).astype(int)
    stats = [len(it), int(it.sum()), st.nonconv, int(it.max()), st.restarted, st.out_of_law]
    return y, stats, np.bincount(it), st


def entries():
    """[(name, values)], in file order."""
    out = []
    def add(name, v):
        out.append((name, np.asarray(v, float).ravel()))

    add("par.dual_control", [pentode.DUAL_CONTROL[k] for k in PAR_KEYS])
    add("par.grid", [pentode.GRID[k] for k in ("GG", "XI", "CG", "hardness")])

    pts = law_points()
    add("law.in", [v for p in pts for v in p])
    add("law.nogrid", [v for p in pts for v in flat_law(pentode.currents(*p))])
    add("law.grid", [v for p in pts for v in flat_law_grid(pentode.currents_grid(*p))])

    # the solvers alone, on the P1 0.45 mA standard packs' K, around their rest problem p_dc: from the operating
    # point, the zero / previous-sample-like starts the stage uses, and wild ones; default settings and capped
    rng = np.random.default_rng(10412)
    for tag, h in (("solve2", 0.0), ("solve3", 1.0)):
        st = stage(FS, 8, "P1", h, 0.45)
        K, n = st.K, st.n_nl
        dps = [np.zeros(4), np.array([2.5, -2.5, 0.0, -60.0]), np.array([-8.0, 8.0, 0.0, 90.0]),
               np.array([0.9, -0.9, 0.0, -170.0]), np.array([5.0, -5.0, 0.0, -150.0])]
        starts = [list(st.i_dc), [0.0] * n, [2e-3, 5e-4, 0.0][:n], [-1e-3, 3e-3, 1e-3][:n], [5e-2, -2e-2, 1e-2][:n]]
        settings = [DEFAULT_NEWTON, (1, 0.0, 0.0), (2, 0.0, 0.0), (3, 0.0, 0.0)]
        rows, res = [], []
        for dp in dps:
            for s0 in starts:
                for nw in settings:
                    p = [float(x) for x in r10(st.p_dc + dp)]
                    i0 = [float(x) for x in r10(s0)]
                    rows += [*p, *(i0 + [0.0] * (3 - n)), *nw]
                    if n == 2:
                        v, a, b, it, ok = solver.solve(p, K, i0[0], i0[1], st.par, *nw)
                        res += [*v, a, b, 0.0, it, 1.0 if ok else 0.0, np.nan]
                    else:
                        v, i, it, ok, fv = solver.solve_grid(p, K, i0, st.par, st.grid, *nw)
                        res += [*v, *i, it, 1.0 if ok else 0.0, fv]
        add(f"{tag}.K", K)
        add(f"{tag}.in", rows)            # p (4), start (3), max_it, tol_v, tol_f per row
        add(f"{tag}.out", res)            # v (4), i (3), iterations, converged, residual (solve3) per row
        if n == 3:                         # the restart contract: three starts in turn, best residual if all fail
            rows, res = [], []
            for _ in range(24):
                dp = r10(rng.uniform([-6.0, -6.0, 0.0, -200.0], [6.0, 6.0, 0.0, 120.0]))
                p = [float(x) for x in r10(st.p_dc + dp)]
                sts = [[float(x) for x in r10(rng.uniform(-3e-3, 3e-3, 3))] for _ in range(2)] + [list(st.i_dc)]
                for nw in (DEFAULT_NEWTON, (1, 0.0, 0.0), (4, 0.0, 0.0)):
                    v, i, it, ok, fv, att = solver.solve_grid_restarts(p, K, sts, st.par, st.grid, *nw)
                    rows += [*p, *sts[0], *sts[1], *sts[2], *nw]
                    res += [*v, *i, it, 1.0 if ok else 0.0, fv, att]
            # recoveries: a non-finite first start (fails before any iteration) and a far one that the cap stops
            # (-0.1 A each needs ~20 iterations), each followed by starts that converge
            for dp in ([0.0, 0.0, 0.0, 0.0], [3.0, -3.0, 0.0, -100.0]):
                p = [float(x) for x in r10(st.p_dc + np.array(dp))]
                for sts, nw in (([[np.inf, 0.0, 0.0], [np.nan, 0.0, 0.0], list(st.i_dc)], DEFAULT_NEWTON),
                                ([[-0.1, -0.1, -0.1], list(st.i_dc), list(st.i_dc)], (6, 1e-6, 1e-6)),
                                ([[-0.1, -0.1, -0.1], [-0.1, -0.1, -0.1], list(st.i_dc)], (6, 1e-6, 1e-6))):
                    v, i, it, ok, fv, att = solver.solve_grid_restarts(p, K, sts, st.par, st.grid, *nw)
                    rows += [*p, *sts[0], *sts[1], *sts[2], *nw]
                    res += [*v, *i, it, 1.0 if ok else 0.0, fv, att]
            add("restarts.in", rows)      # p (4), three starts (9), max_it, tol_v, tol_f
            add("restarts.out", res)      # v (4), i (3), iterations, converged, residual, attempts

    knobs = [1.0, 2.5, 6.0, 9.75, 11.0]
    add("knob.in", knobs)
    add("knob.bias", [S.knob_bias(k) for k in knobs])
    biases = [0.1, 0.245, 0.45, 0.6]
    add("knob.bias_in", biases)
    add("knob.knob", [S.bias_knob(b) for b in biases])

    cases = _cases()
    packs = {}
    for c in cases:
        packs[pack_name(c[3], c[4], c[5], c[2], c[1])] = (c[1], c[2], c[3], c[4], c[5])
    for mode in MODES:                     # every mode / grid / reference current at standard 48 kHz
        for h in (1.0, 0.0):
            for b in (0.1, 0.25, 0.45, 0.6):
                packs.setdefault(pack_name(mode, h, b, 8, FS), (FS, 8, mode, h, b))
    for name, (fs, os_, mode, h, b) in sorted(packs.items()):
        st = stage(fs, os_, mode, h, b)
        m, dc = st.m, st.dc
        nu, ni = m["B"].shape[1], m["C"].shape[1]
        add(f"{name}.cfg", [fs, os_, MODES.index(mode), h, b, nu, ni, 1.0 if st.supported else 0.0])
        add(f"{name}.rk", [st.rk])
        for k in "ABCDEFGHK":
            add(f"{name}.{k}", m[k])
        add(f"{name}.op", [dc["vg1k"], dc["vg2k"], dc["vg3k"], dc["vak"], dc["ip"], dc["ig2"], dc["ig1"], dc["ik"]])
        add(f"{name}.nodes", [dc["nodes"][n] for n in circuit.NODES])
        x_dc = m["Gx"] @ m["Nx"] @ dc["w"][:len(circuit.NODES)]
        add(f"{name}.rest", np.r_[x_dc, st.i_dc, st.p_dc, st.y_dc])

    # the post-filter alone at 8 x 48 kHz: noise, a change to 4k at sample 100 and to 7k mid-fade at 3000
    fs_i = 8 * FS
    x = r10(np.random.default_rng(1103).normal(0.0, 1.0, 8000))
    pf = lpf.PostFilter(fs_i, "off")
    y = np.r_[pf.process(x[:100])]
    pf.set_position("4k")
    y = np.r_[y, pf.process(x[100:3000])]
    pf.set_position("7k")
    y = np.r_[y, pf.process(x[3000:])]
    add("lpf.in", x)
    add("lpf.out", y)

    for fs in (48000.0, 44100.0):
        (n0, b0), (n1, b1) = K140[fs]
        add(f"taps.k140_{n0}", _FirHB(n0, b0).h)
        if fs == 48000.0:
            add(f"taps.k140_{n1}", _FirHB(n1, b1).h)
        r2 = np.random.default_rng(int(fs) + 140)
        for os_ in (8, 16):
            xi = r10(r2.normal(0.0, 1.0, 64))
            yi = r10(r2.normal(0.5, 1.0, 64 * os_))
            p = f"rsk140_{int(fs)}_{os_}"
            add(p + ".up.in", xi)
            add(p + ".up.out", Resampler(fs, os_, "fir", fir=K140[fs]).up(xi))
            add(p + ".down.in", yi)
            add(p + ".down.out", Resampler(fs, os_, "fir", fir=K140[fs]).down(yi))

    # the stage alone at the internal rate, T with grid current at 0.45 mA: out and the recorded voltages
    st = stage(FS, 8, "T", 1.0, 0.45, "7k")
    vin = sine(8.0, 1024, fs=st.fs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rec = st.process_internal(vin, record=("out", "vgk", "vg2k", "vak", "ig1", "ipk"))
    add("internal.in", vin)
    for k in ("out", "vgk", "vg2k", "vak", "ig1", "ipk"):
        add(f"internal.{k}", rec[k])
    add("internal.iters", st.iters[-1])

    sig = {FS: _signals(FS), 44100.0: _signals(44100.0)}
    for name, fs, os_, mode, h, b, dr, og, lp, sw, nw, fir, sg in cases:
        x = sig[fs][sg]
        y, stats, hist, st = _run_case(fs, os_, mode, h, b, dr, og, lp, sw, nw, fir, x)
        at, to = sw or (-1, lp)
        add(f"case.{name}.cfg", [fs, os_, MODES.index(mode), h, b, dr, og, LPFS.index(lp), at, LPFS.index(to), *nw,
                                 FIRS.index(fir), 1.0 if st.supported else 0.0])
        add(f"case.{name}.in", x)
        add(f"case.{name}.out", y)
        add(f"case.{name}.stats", stats)
        add(f"case.{name}.hist", hist)
    return out


def write(path=PATH):
    ent = entries()
    with open(path, "w") as f:
        f.write("# GENERATED by `python -m cpp.export_vectors_6as6` (prototypes/fullstage) from the Python 6AS6 DK "
                "stage (dk6as6/).\n")
        f.write("# Format: name count values...  Read by cpp/test_core_6as6.cpp; see cpp/README-6as6.md.\n")
        f.write("# pack.*.cfg = fs_base, os, mode (0 P1, 1 T, 2 P2), grid hardness, bias mA, nu, ni, supported\n")
        f.write("# pack.*.op = Vg1k, Vg2k, Vg3k, Vak, Ip, Ig2, Ig1, Ik;  pack.*.rest = x_dc (7), i_dc (ni), p_dc (4),"
                " y_dc (5)\n")
        f.write("# case.*.cfg = fs_base, os, mode, hardness, bias mA, drive, output, lpf (0 off, 1 4k, 2 7k), switch_at"
                " (-1 none), switch to, max_it, tol_v, tol_f, fir (0 shipped, 1 k140), supported\n")
        f.write("# case.*.stats = internal samples, Newton iterations (sum), nonconverged, max iterations, restarted,"
                " out of law;  case.*.hist = samples per iteration count 0, 1, ...\n")
        for name, v in ent:
            f.write(f"{name} {len(v)} " + " ".join(repr(float(x)) for x in v) + "\n")
    return path


def read(path=PATH):
    out = {}
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        name, n, *vals = line.split()
        assert len(vals) == int(n), name
        out[name] = np.array([float(x) for x in vals])
    return out


def manifest():
    """The case list as JSON (for the README)."""
    return json.dumps([c[0] for c in _cases()])


if __name__ == "__main__":
    p = write()
    print(f"wrote {p} ({os.path.getsize(p)} bytes)")
