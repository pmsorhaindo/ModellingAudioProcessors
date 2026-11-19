"""Reference test vectors for the C++ core (#19), exported from the Python DK stage (dk/).

Writes cpp/vectors.txt, which cpp/test_core.cpp reads. One entry per line: `name count v0 v1 ...`; lines starting with
'#' are comments. Every value is written exactly (shortest round-trip repr). Inputs are rounded to 10 significant
digits before they go through Python, which keeps them short.
Contents:
  taps.fir127 / taps.fir23            resample.FIR_FIRST / FIR_LATER half-band taps (firwin, Kaiser 9)
  coef.<name>.*                       circuit.build matrices A..K, the DC operating point and x0 (= Stage.x0, the
                                      absolute capacitor history at the operating point) for standard / hq / eco
                                      at bias 1 and standard at bias 1.5
  triode.*                            triode.currents / currents_only / currents_fast on a (Vgk, Vak) grid
  solve.*                             solver.solve alone from hard starts (pnjlim, line search, #7 best iterate)
  rs<os>.up.* / rs<os>.down.*         resample.Resampler(48 kHz, os, "fir") up() and down() alone, os = 4, 8, 16
  internal.std8v.*                    Stage.process_internal (no resampler) at 384 kHz: out, Vgk, iterations
  case.<name>.*                       Stage.process at the base rate: cfg, input, output, iteration stats
Run from prototypes/fullstage:  python -m cpp.export_vectors   (~15 s; rewrites cpp/vectors.txt)
cpp/tests/test_cpp_core.py checks that the committed file still matches what the Python stage produces now.
"""
import os
import sys
import warnings
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk import circuit, solver  # noqa: E402 -- after the sys.path setup
from dk.stage import Stage, QUALITY, NonConvergenceWarning  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler, FIR_FIRST, FIR_LATER, _FirHB  # noqa: E402 -- after the sys.path setup
from dk.triode import currents, currents_only, currents_fast  # noqa: E402 -- after the sys.path setup

FS = 48000.0
PATH = os.path.join(HERE, "vectors.txt")
DEFAULT_NEWTON = (solver.MAX_IT, solver.TOL_V, solver.TOL_F)


def r10(x):
    """Round to 10 significant digits (exactly representable in the file)."""
    return np.array([float("%.10g" % v) for v in np.atleast_1d(np.asarray(x, float))])


def sine(a, n, f=1000.0, fs=FS):
    return r10(a * np.sin(2 * np.pi * f * np.arange(n) / fs))


# (dp on the rest problem, start (Vgk, Vak)) for solver.solve's rare paths, see entries()
EDGE_SOLVES = [((2.78, -78.91), (0.306, 275.6)), ((-2.35, -64.7), (-2.979, 214.3)), ((-2.01, -63.07), (-2.646, 216.3)),
               ((1.27, -61.57), (-2.565, 222.8)), ((0.48, -76.14), (-2.988, 207.0)), ((-3.0, -48.71), (-2.725, 228.7))]

# (name, quality, bias)
COEFS = [("standard", "standard", 1.0), ("hq", "hq", 1.0), ("eco", "eco", 1.0), ("standard_b15", "standard", 1.5)]


def _signals():
    n = 480
    rng = np.random.default_rng(19)
    t = np.arange(960) / FS
    burst = r10(4.0 * np.sin(2 * np.pi * 1000 * t) * ((t > 2e-3) & (t < 6e-3)))
    return dict(s005=sine(0.05, n), s2=sine(2.0, n), s4=sine(4.0, n), s8=sine(8.0, n), s2_3k=sine(2.0, n, 3000.0),
                burst=burst, noise=r10(rng.uniform(-3.0, 3.0, n)))


# (name, quality, bias, drive, output, triode_math, newton, signal, swap)  swap = (at, bias, newton) or None
def _cases():
    d = DEFAULT_NEWTON
    return [
        ("std_s005", "standard", 1.0, 1.0, 1.0, "libm", d, "s005", None),
        ("std_s2", "standard", 1.0, 1.0, 1.0, "libm", d, "s2", None),
        ("std_s8", "standard", 1.0, 1.0, 1.0, "libm", d, "s8", None),
        ("std_burst4", "standard", 1.0, 1.0, 1.0, "libm", d, "burst", None),
        ("std_noise3", "standard", 1.0, 1.0, 1.0, "libm", d, "noise", None),
        ("std_gain", "standard", 1.0, 2.5, 0.4, "libm", d, "s2_3k", None),
        ("std_bias15", "standard", 1.5, 1.0, 1.0, "libm", d, "s2", None),
        ("std_swap_bias", "standard", 1.0, 1.0, 1.0, "libm", d, "s2", (240, 1.5, d)),
        ("std_fast_s8", "standard", 1.0, 1.0, 1.0, "fast", d, "s8", None),
        ("std_nonconv", "standard", 1.0, 1.0, 1.0, "libm", (1, 0.0, 0.0), "s4", (240, 1.0, d)),
        ("hq_s2", "hq", 1.0, 1.0, 1.0, "libm", d, "s2", None),
        ("hq_s8", "hq", 1.0, 1.0, 1.0, "libm", d, "s8", None),
        ("eco_s2", "eco", 1.0, 1.0, 1.0, "libm", d, "s2", None),
        ("eco_s8", "eco", 1.0, 1.0, 1.0, "libm", d, "s8", None),
    ]


def _run_case(quality, bias, drive, output, math, newton, x, swap):
    s = Stage(FS, bias=bias, drive=drive, output=output, quality=quality, triode_math=math)
    s.newton = tuple(newton)                  # runtime only: the DC solve above used the defaults
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NonConvergenceWarning)
        if swap is None:
            y = s.process(x)["out"]
        else:
            at, b2, nw2 = swap
            y1 = s.process(x[:at])["out"]
            # the C++ swap keeps the circuit state and replaces the pack: matrices and operating point for the new
            # bias (request_bias, only if the bias changes, as test_core.cpp run_blocks), then the new Newton settings
            if b2 != s.bias:
                s.swap_bias(b2)
            s.newton = tuple(nw2)
            y = np.r_[y1, s.process(x[at:])["out"]]
    it = np.concatenate(s.iters)
    stats = [len(it), int(it.sum()), s.nonconv, int(it.max())]
    return y, stats


def entries():
    """[(name, values)], in file order."""
    out = []
    def add(name, v):
        return out.append((name, np.asarray(v, float).ravel()))
    add("taps.fir127", _FirHB(*FIR_FIRST).h)
    add("taps.fir23", _FirHB(*FIR_LATER).h)

    for name, q, b in COEFS:
        s = Stage(FS, quality=q, bias=b)
        m = s.m
        add(f"coef.{name}.cfg", [QUALITY[q][0], QUALITY[q][1] or 0.0, b])
        for k in "ABCDEFGHK":
            add(f"coef.{name}.{k}", m[k])
        dc = s.dc
        add(f"coef.{name}.op", [dc["vgk"], dc["vak"], dc["ig"], dc["ia"]])
        add(f"coef.{name}.nodes", [dc["nodes"][n] for n in circuit.NODES])
        add(f"coef.{name}.x0", s.x0)

    g = r10(np.linspace(-16.0, 2.0, 10))
    a = np.array([0.0, 12.75, 100.0, 204.5, 296.8, 450.0])
    pts = [(vg, va) for vg in g for va in a]
    add("triode.in", [v for p in pts for v in p])
    def flat(r):
        return [r[0], r[1], r[2][0][0], r[2][0][1], r[2][1][0], r[2][1][1]]
    add("triode.libm", [v for vg, va in pts for v in flat(currents(vg, va))])
    add("triode.libm_only", [v for vg, va in pts for v in currents_only(vg, va)])
    add("triode.fast", [v for vg, va in pts for v in flat(currents_fast(vg, va))])

    # solver.solve alone on the standard pack's K: rest / forward-biased / cut-off problems from hard starts, with the
    # default settings (pnjlim, line search) and capped at 1..4 iterations with zero tolerance (#7 best iterate)
    s = Stage(FS)
    p_rest = s.m["G"] @ s.x0 + s.m["H"] @ np.array([0.0, circuit.VALUES["HT"]])
    probs = [p_rest, p_rest + np.array([2.0, -30.0]), p_rest + np.array([-10.0, 50.0]), p_rest + np.array([0.6, 5.0])]
    starts = [(2.0, 50.0), (1.0, 300.0), (-5.0, 10.0), (0.5, 100.0), (3.0, 20.0), (-1.0, 200.0),
              (s.dc["vgk"], s.dc["vak"])]
    settings = [DEFAULT_NEWTON] + [(k, 0.0, 0.0) for k in (1, 2, 3, 4)]
    rows, res = [], []
    cases = [(pp, v, nw) for pp in probs for v in starts for nw in settings]
    # rare paths, found by a random search over 2e4 problems (p_rest + dp, start): a pnjlim step in (2 VT, 3 VT]
    # with Vgk > 0, and five line-search halvings in one iteration
    for dp, v in EDGE_SOLVES:
        cases.append((p_rest + np.array(dp), v, DEFAULT_NEWTON))
    for pp, v, nw in cases:
        rows += [pp[0], pp[1], v[0], v[1], *nw]
        r = solver.solve(pp[0], pp[1], s.K, v[0], v[1], s.par, *nw)
        res += [r[0], r[1], r[2], r[3], r[4], 1.0 if r[5] else 0.0]
    add("solve.in", rows)                    # p0, p1, v0, v1, max_it, tol_v, tol_f per row (K: coef.standard.K)
    add("solve.out", res)                    # Vgk, Vak, Ig, Ia, iterations, converged per row

    rng = np.random.default_rng(1910)
    for os_ in (4, 8, 16):
        x = r10(rng.normal(0.0, 1.0, 32))
        add(f"rs{os_}.up.in", x)
        add(f"rs{os_}.up.out", Resampler(FS, os_).up(x))
        y = r10(rng.normal(0.5, 1.0, 32 * os_))
        add(f"rs{os_}.down.in", y)
        add(f"rs{os_}.down.out", Resampler(FS, os_).down(y))

    s = Stage(FS)
    vin = sine(8.0, 1024, fs=s.fs)
    rec = s.process_internal(vin, record=("out", "vgk"))
    add("internal.std8v.in", vin)
    add("internal.std8v.out", rec["out"])
    add("internal.std8v.vgk", rec["vgk"])
    add("internal.std8v.iters", s.iters[-1])

    sig = _signals()
    for name, q, b, dr, og, math, nw, sg, swap in _cases():
        x = sig[sg]
        y, stats = _run_case(q, b, dr, og, math, nw, x, swap)
        sw = swap or (-1, b, nw)
        add(f"case.{name}.cfg", [QUALITY[q][0], QUALITY[q][1] or 0.0, b, dr, og, 1.0 if math == "fast" else 0.0, *nw,
                                 sw[0], sw[1], *sw[2]])
        add(f"case.{name}.in", x)
        add(f"case.{name}.out", y)
        add(f"case.{name}.stats", stats)
    out.extend(resampler_44100_entries())
    return out


def resampler_44100_entries():
    """Separate seed and appended entries preserve every existing 48 kHz vector byte."""
    from dk.resample import _FirHB, FIR_FIRST_44100
    out = [(f"taps.fir{FIR_FIRST_44100[0]}", _FirHB(*FIR_FIRST_44100).h)]
    rng = np.random.default_rng(9644100)
    for os_ in (4, 8, 16):
        x = r10(rng.normal(0.0, 1.0, 256))
        y = r10(rng.normal(0.5, 1.0, 256 * os_))
        p = f"rs44100_{os_}"
        out.extend([(p + ".up.in", x), (p + ".up.out", Resampler(44100, os_).up(x)),
                    (p + ".down.in", y), (p + ".down.out", Resampler(44100, os_).down(y))])
    return out


def write(path=PATH):
    ent = entries()
    with open(path, "w") as f:
        f.write("# GENERATED by `python -m cpp.export_vectors` (prototypes/fullstage) from the "
                "Python DK stage (dk/).\n")
        f.write("# Format: name count values...  Read by cpp/test_core.cpp; see cpp/README.md.\n")
        f.write("# case.*.cfg = os, prewarp_hz, bias, drive, output, fast(0/1), max_it, tol_v, tol_f,"
                " swap_at (-1 none), swap bias, swap max_it, swap tol_v, swap tol_f\n")
        f.write("# case.*.stats = internal samples, Newton iterations (sum), nonconverged, max iterations\n")
        for name, v in ent:
            # repr: the shortest string that reads back as the same double (exact; inputs have <= 10 digits)
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


if __name__ == "__main__":
    p = write()
    print(f"wrote {p} ({os.path.getsize(p)} bytes)")
