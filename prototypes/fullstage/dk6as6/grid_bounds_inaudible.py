"""#131 (bounds only): the borrowed g1 grid-current law's published bounds under #113's inaudibility criterion, T mode,
0.1-0.5 mA, 2 and 8 V, 44.1 and 48 kHz, at every oversampling factor PR #146 weighs. Estimated, unvalidated.

Why: draft PR #146 proposes capping T at 0.35 mA because, with the nominal RSD-1 constants, T above 0.35 mA fails the
criterion at 16x. Michael (23 Nov 2026) asked which part of 0.1-0.5 mA holds under every published bound before that
decision, so it does not rest on one unvalidated fit.

Shapes: pentode.GRID_SHAPES, the three columns of Dempwolf & Zoelzer, DAFx-11, Table 1 (12AX7 fits), scaled as #112 /
PR #128 scaled RSD-1 (pentode.grid): "RSD-1" the nominal (shipped), "RSD-2" and "EHX-1" the bounds (#131a, PR #139).
No other fit of this form was found that is published and readable: Dempwolf, Holters & Zoelzer, AES Convention 131
(2011), paper 8507, gives per-tube parameter sets, but it is paywalled and was not read, so none of its values is used.

Criterion: dk6as6/inaudible.py, byte-identical to PR #146's (head 1c0a7f4): every product in 20 Hz-20 kHz below
-100 dBFS and >= 10 dB under Terhardt's threshold of hearing (0 dBFS = the output peak, a 0 dBFS sine = 105 dB SPL,
an assumption). Signals, settle, window and the 29-tone grid are #146's (inaudible_sweep: GRID_HZ, SETTLE_S, 1 s).

Engines, the same stage:
  python  dk6as6.stage.Stage(grid_shape=...) (the reference; experimental=True, the bounds have no supported envelope).
  cpp     the C++ 6AS6 core (cpp/dk6as6_core.h, main's) through test_core_6as6's `cli`, built by screen_build() from
          patched COPIES of the headers: MAX_OS 16 -> 64 and ResamplerT's later_[3] -> later_[5] (as #146's screen,
          for 32x / 64x), and grid()'s Table 1 column replaced by the shape's (SCREEN_PATCH). The committed core is
          not changed; nothing here touches the plugin or the audio path. Every record names its engine and shape;
          the deciding points are re-run on the python engine (confirm), and the conclusions rest on those.
Run from prototypes/fullstage:
  python -m dk6as6.grid_bounds_inaudible run [--shapes ...] [--factors 16 32] [--lpfs off] [--workers N]
  python -m dk6as6.grid_bounds_inaudible confirm [--workers N]
  python -m dk6as6.grid_bounds_inaudible collect     JSONLs -> dk6as6/grid_bounds_inaudible.json
  python -m dk6as6.grid_bounds_inaudible tables      the RESULTS.md tables
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk.aliasing import tone_bin, WINDOW_S, SWEEP_HZ  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk import triode  # noqa: E402 -- after the sys.path setup
from dk6as6 import inaudible as IN, pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.pentode import HARDNESS  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, fir_design, ExperimentalWarning  # noqa: E402 -- after the sys.path setup
from dk6as6 import lpf as LPF  # noqa: E402 -- after the sys.path setup

LABEL = "estimated, unvalidated"
SHAPES = (pentode.GRID_SHAPE,) + pentode.GRID_BOUNDS            # RSD-1 (nominal), RSD-2, EHX-1
MODE = "T"
BIASES = (0.1, 0.25, 0.3, 0.35, 0.375, 0.4, 0.45, 0.5)          # #146's T cases in 0.1-0.5 mA (the supported range)
FACTORS = (8, 16, 32, 64)
RATES = IN.RATES
DRIVES = IN.DRIVES
GRID_HZ = tuple(SWEEP_HZ) + (19237, 19437, 19637, 19831, 19837, 19937, 19957, 19977, 19987, 19997)   # #146's grid
FIR = "k140"
SCRATCH = os.path.join(os.path.dirname(os.path.dirname(ROOT)), ".dispatch", "scratch")
JSONL = {"cpp": os.path.join(SCRATCH, "grid_bounds_inaudible_cpp.jsonl"),
         "python": os.path.join(SCRATCH, "grid_bounds_inaudible_py.jsonl")}
OUT_JSON = os.path.join(HERE, "grid_bounds_inaudible.json")
SCREEN_DEPS = ("alloc_hooks.h",)
TABLE1 = {"RSD-1": "DZ_12AX7_RSD1", "RSD-2": "DZ_12AX7_RSD2", "EHX-1": "DZ_12AX7_EHX1"}


# --- the C++ screening engine ------------------------------------------------------------------------------------

SCREEN_PATCH = (   # (file, exact old text, new text): each must occur exactly once in main's file
    ("dk_core.h", "inline constexpr int MAX_OS = 16;", "inline constexpr int MAX_OS = 64;"),
    ("dk_core.h", "FirHalfBand<LATER_N, T> later_[3];", "FirHalfBand<LATER_N, T> later_[5];"),
    ("dk6as6_core.h", "    const auto& dz = dkcore::DZ_12AX7_RSD1;\n", "    const auto& dz = SCREEN_DZ;\n"),
)


def _cxx_table1(shape):
    p = getattr(triode, TABLE1[shape])
    return "dkcore::TriodeParams{" + ", ".join(repr(float(p[k])) for k in
                                               ("G", "mu", "gamma", "C", "Gg", "xi", "Cg", "Ig0")) + "}"


def screen_sources(shape, cpp_dir=os.path.join(ROOT, "cpp")):
    """{file name: patched text} for the screening build of `shape` (module docstring)."""
    out = {}
    for name in ("dk_core.h", "dk6as6_core.h", "test_core_6as6.cpp"):
        out[name] = open(os.path.join(cpp_dir, name)).read()
    for name, old, new in SCREEN_PATCH:
        n = out[name].count(old)
        if n != 1:
            raise RuntimeError(f"screen patch: {old!r} occurs {n} times in {name}, expected once (main moved?)")
        out[name] = out[name].replace(old, new)
    # the shape's Table 1 column, defined just before grid() (after TriodeParams and DZ_12AX7_RSD1 are declared)
    anchor = "struct GridParams { double GG, XI, CG, hardness; };\n"
    if out["dk6as6_core.h"].count(anchor) != 1:
        raise RuntimeError("screen patch: GridParams anchor not found once in dk6as6_core.h")
    out["dk6as6_core.h"] = out["dk6as6_core.h"].replace(
        anchor, anchor + f"inline constexpr dkcore::TriodeParams SCREEN_DZ = {_cxx_table1(shape)};   "
                         f"// #131 screen: DZ Table 1 {shape}\n")
    return out


def screen_build(shape, out_dir=None, cxx=None):
    """Compile the screening `test_core_6as6` for `shape` (-O2, the project's flags); returns the executable."""
    from cpp.build import CXXFLAGS, compiler
    cxx = cxx or compiler()
    if cxx is None:
        raise FileNotFoundError("no C++ compiler (set CXX)")
    src = screen_sources(shape)
    tag = hashlib.sha256("".join(src[k] for k in sorted(src)).encode()).hexdigest()[:12]
    d = out_dir or os.path.join(tempfile.gettempdir(), f"gb131_screen_{re.sub('[^A-Za-z0-9]', '', shape)}_{tag}")
    exe = os.path.join(d, "test_core_6as6")
    if not os.path.exists(exe):
        # the tree the sources include from: cpp/ (patched copies + alloc_hooks.h) and dk/ (fastmath*.h), unpatched
        for sub, names in (("cpp", SCREEN_DEPS), ("dk", ("fastmath.h", "fastmath_tables.h"))):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
            for k in names:
                shutil.copyfile(os.path.join(ROOT, sub, k), os.path.join(d, sub, k))
        for k, v in src.items():
            open(os.path.join(d, "cpp", k), "w").write(v)
        tmp = exe + f".{os.getpid()}.tmp"
        subprocess.run([cxx, *CXXFLAGS, "-O2", "-o", tmp, os.path.join(d, "cpp", "test_core_6as6.cpp")],
                       check=True, capture_output=True, text=True)
        os.replace(tmp, exe)
    return exe


def cpp_engine_note():
    head = subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return (f"main's cpp/dk6as6_core.h, dk_core.h, test_core_6as6.cpp at {head[:7]} (cli), patched copies "
            f"(grid_bounds_inaudible.SCREEN_PATCH: MAX_OS 64, later_[5], grid()'s Table 1 column per shape), -O2")


# --- one point ---------------------------------------------------------------------------------------------------

def signal(fs, tone, drive, latency, window=WINDOW_S, settle=SETTLE_S):
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(tone, n, fs)
    return m, n, n0, drive * np.sin(2 * np.pi * m * np.arange(n0 + n + latency) / n)


def run_python(shape, fs, os_, bias, drive, tone, window=WINDOW_S, settle=SETTLE_S, lpf="off"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        kw = dict(bias=bias, quality=(os_, None), mode=MODE, experimental=True, grid_hardness=HARDNESS, fir=FIR,
                  grid_shape=shape)
        st = Stage(fs, **kw) if lpf == "off" else LPF.Stage(fs, lpf=lpf, **kw)
    L = st.latency_samples
    m, n, n0, x = signal(fs, tone, drive, L, window, settle)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return m, y, dict(nonconverged=int(st.nonconv), out_of_law=int(st.out_of_law), rk=float(st.rk))


def run_cpp(shape, fs, os_, bias, drive, tone, window=WINDOW_S, settle=SETTLE_S, lpf="off", exe=None):
    exe = exe or screen_build(shape)
    L = Resampler(fs, os_, "fir", fir=fir_design(FIR, fs)).latency_samples
    m, n, n0, x = signal(fs, tone, drive, L, window, settle)
    args = [exe, "cli", repr(float(fs)), str(int(os_)), MODE, repr(float(bias)), repr(float(HARDNESS)), lpf,
            "1.0", "1.0", FIR]
    p = subprocess.run(args, input=np.ascontiguousarray(x, np.float64).tobytes(), capture_output=True)
    if p.returncode:
        raise RuntimeError(f"{exe} cli failed ({p.returncode}): {p.stderr.decode()[-500:]}")
    info = json.loads(p.stderr.decode().strip().splitlines()[-1])
    if info["latency_samples"] != L:
        raise RuntimeError(f"C++ latency {info['latency_samples']} != the Python stage's {L}")
    y = np.frombuffer(p.stdout, np.float64)[:len(x)][n0 + L:n0 + L + n]
    st = info["stats"]
    return m, y, dict(nonconverged=int(st["nonconverged"]), out_of_law=int(st["out_of_law"]), rk=float(info["rk"]))


def measure(engine, shape, fs, os_, bias, drive, tone, lpf="off", window=WINDOW_S, settle=SETTLE_S):
    t = time.perf_counter()
    run = run_python if engine == "python" else run_cpp
    m, y, stats = run(shape, fs, os_, bias, drive, tone, window, settle, lpf)
    a = IN.assess(y, m, fs, os_)
    return dict(engine=engine, shape=shape, label=LABEL, fs=fs, os=os_, fir=FIR, lpf=lpf, mode=MODE, bias_ma=bias,
                drive=drive, tone=tone, f_hz=m * fs / len(y), window_s=window, settle_s=settle,
                grid_hardness=HARDNESS, **stats, **a, wall_s=time.perf_counter() - t)


def point(r):
    return (r["engine"], r["shape"], int(r["fs"]), int(r["os"]), r.get("lpf", "off"), float(r["bias_ma"]),
            float(r["drive"]), int(r["tone"]))


def load(path):
    out = {}
    if os.path.exists(path):
        for line in open(path):
            if line.strip():
                r = json.loads(line)
                out[point(r)] = r
    return list(out.values())


def _job(j):
    return measure(**j)


def plan(shapes=SHAPES, factors=FACTORS, rates=RATES, biases=BIASES, drives=DRIVES, tones=GRID_HZ, lpfs=("off",),
         engine="cpp"):
    order = sorted(factors, key=lambda f: {16: 0, 32: 1, 64: 2, 8: 3}.get(f, 4))     # the deciding factors first
    return [dict(engine=engine, shape=s, fs=fs, os_=f, bias=b, drive=d, tone=t, lpf=lp)
            for f in order for s in shapes for fs in rates for lp in lpfs for b in biases for d in drives
            for t in tones]


def run(jobs, path, workers):
    done = {point(r) for r in load(path)}
    key = lambda j: (j["engine"], j["shape"], int(j["fs"]), int(j["os_"]), j["lpf"], float(j["bias"]),  # noqa: E731
                     float(j["drive"]), int(j["tone"]))
    todo = [j for j in jobs if key(j) not in done]
    print(f"{len(jobs) - len(todo)} done, {len(todo)} to run -> {path}", flush=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for s in {j["shape"] for j in todo if j["engine"] == "cpp"}:
        screen_build(s)                                  # once, before the pool
    with ProcessPoolExecutor(workers) as ex, open(path, "a") as fh:
        futs = {ex.submit(_job, j): j for j in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if i % 50 == 0 or (not r["passed"] and r["os"] >= 16 and r["drive"] == 8.0):
                print(f"[{i}/{len(todo)}] {r['shape']} {r['fs']} {r['os']}x {r['bias_ma']:g} mA {r['drive']:g} V "
                      f"{r['lpf']} {r['tone']} Hz: margin {r['margin_db']:+.1f} dB at {r['worst_hz']:.0f} Hz",
                      flush=True)


# --- the staged, fail-fast screen ---------------------------------------------------------------------------------
# The machine this ran on was shared (load 100-200 on 8 cores), so the screen is staged rather than the full grid of
# PR #146. A key (shape, rate, factor, post-filter, bias, drive) PASSES only if all 29 GRID_HZ tones pass; it FAILS at
# its first failing tone, and its other tones are then not run (its reported margin is that tone's, an upper bound on
# the key's worst). Tones run worst-first, in the order of the nominal (RSD-1) margins at the same key where those
# exist (PR #146's records), so a failing key usually stops at its first tone. Stages (stage_plan):
#   16x and 8 V at every bias; 32x where 16x failed; 64x where 32x failed (filter off and 4k);
#   8x and 8 V where 16x passed (the eco question, #136); 2 V at 16x at T 0.35 / 0.45 / 0.5 mA;
#   PR #151 review: "fill", every 32x / 64x key of the decision table not yet measured (so no cell of it is inferred
#   from a lower factor); "fine", 16x at FINE_BIASES (between 0.3 and 0.35 mA, where the bounds start to move);
#   "2v4k", 2 V at 16x with the 4k filter at T 0.35 / 0.45 / 0.5 mA.
FINE_BIASES = (0.31, 0.32, 0.33, 0.34)        # mA, 16x only (PR #151 review: the move below 0.35 mA)

def tone_order(cpp, shape, fs, os_, lpf, bias, drive):
    """GRID_HZ worst-first by the nominal margins at this key (else at the nearest factor / bias measured)."""
    nom = [r for r in cpp if r["shape"] == pentode.GRID_SHAPE and int(r["fs"]) == fs and r["lpf"] == lpf
           and float(r["drive"]) == drive]
    if not nom:
        return sorted(GRID_HZ, reverse=True)
    near = min(nom, key=lambda r: (abs(int(r["os"]) - os_), abs(float(r["bias_ma"]) - bias)))
    sel = {int(r["tone"]): r["margin_db"] for r in nom if int(r["os"]) == near["os"]
           and float(r["bias_ma"]) == near["bias_ma"]}
    return sorted(GRID_HZ, key=lambda t: (sel.get(t, -1e9), -t))


def _key_job(k):
    """Run one key's tones in order until one fails, appending each record to the JSONL under a lock."""
    import fcntl
    path, done = k.pop("path"), k.pop("done")
    out = []
    for t in k.pop("tones"):
        if t in done:
            continue
        r = measure(tone=t, **k)
        with open(path, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
        out.append(r)
        if not r["passed"]:
            break
    return out


def key_status(recs, shape, fs, os_, lpf, bias, drive):
    """"pass" (all GRID_HZ tones measured and passing), "fail" (a measured tone fails) or None (not decided)."""
    rr = [r for r in recs if (r["shape"], int(r["fs"]), int(r["os"]), r.get("lpf", "off"), float(r["bias_ma"]),
                              float(r["drive"])) == (shape, fs, os_, lpf, bias, drive)]
    if any(not r["passed"] for r in rr):
        return "fail"
    return "pass" if {int(r["tone"]) for r in rr} >= set(GRID_HZ) else None


def staged(keys, path=JSONL["cpp"], workers=None):
    """Run each key fail-fast (above) on the C++ screen."""
    recs = load(path)
    jobs = []
    for (sh, fs, f, lp, b, d) in keys:
        if key_status(recs, sh, fs, f, lp, b, d) is not None:
            continue
        done = {int(r["tone"]) for r in recs if (r["shape"], int(r["fs"]), int(r["os"]), r.get("lpf", "off"),
                                                 float(r["bias_ma"]), float(r["drive"])) == (sh, fs, f, lp, b, d)}
        jobs.append(dict(engine="cpp", shape=sh, fs=fs, os_=f, bias=b, drive=d, lpf=lp, path=path, done=done,
                         tones=tone_order(recs, sh, fs, f, lp, b, d)))
    print(f"{len(keys)} keys, {len(jobs)} to run -> {path}", flush=True)
    for s in {j["shape"] for j in jobs}:
        screen_build(s)
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(_key_job, j): j for j in jobs}
        for fu in as_completed(futs):
            rs = fu.result()
            if rs:
                r = rs[-1]
                print(f"{r['shape']} {r['fs']} {r['os']}x {r['lpf']} {r['bias_ma']:g} mA {r['drive']:g} V: "
                      f"{'FAIL' if not r['passed'] else 'pass'} after {len(rs)} tones, last margin "
                      f"{r['margin_db']:+.1f} dB at {r['tone']} Hz", flush=True)


def stage_plan(stage, shapes=SHAPES, rates=RATES, path=JSONL["cpp"], records=None):
    recs = load(path) if records is None else records
    st = lambda *k: key_status(recs, *k)  # noqa: E731
    keys = []
    for sh in shapes:
        for fs in rates:
            for lp in ("off", "4k"):
                if stage == "fine":
                    keys += [(sh, fs, 16, lp, b, 8.0) for b in FINE_BIASES]
                    continue
                for b in BIASES:
                    if stage == "16":
                        keys.append((sh, fs, 16, lp, b, 8.0))
                    elif stage == "32" and st(sh, fs, 16, lp, b, 8.0) == "fail":
                        keys.append((sh, fs, 32, lp, b, 8.0))
                    elif stage == "64" and st(sh, fs, 32, lp, b, 8.0) == "fail":
                        keys.append((sh, fs, 64, lp, b, 8.0))
                    elif stage == "8" and lp == "off" and st(sh, fs, 16, lp, b, 8.0) == "pass":
                        keys.append((sh, fs, 8, lp, b, 8.0))
                    elif stage == "2v" and lp == "off" and b in (0.35, 0.45, 0.5):
                        keys.append((sh, fs, 16, lp, b, 2.0))
                    elif stage == "2v4k" and lp == "4k" and b in (0.35, 0.45, 0.5):
                        keys.append((sh, fs, 16, lp, b, 2.0))
                    elif stage == "fill":
                        keys += [(sh, fs, f, lp, b, 8.0) for f in (32, 64)]
    return keys


# --- off-grid counterexamples (PR #151 review) ---------------------------------------------------------------------
# The regions are statements on the sampled biases and GRID_HZ tones. The review measured, with this study's signals
# and criterion, a failure for RSD-2 at T 0.349 mA (below the 0.35 mA grid point) with an off-grid 19693 Hz tone, and
# at 0.3499 mA with the grid's 19637 Hz tone, where the nominal passes both: recorded here on both engines.
COUNTEREXAMPLES = tuple(dict(shape=s, fs=44100, os_=16, bias=b, drive=8.0, tone=t, lpf="4k")
                        for b, t in ((0.349, 19693), (0.3499, 19637)) for s in ("RSD-1", "RSD-2"))
JSONL["counter"] = os.path.join(SCRATCH, "grid_bounds_inaudible_counter.jsonl")


def run_counterexamples(path=JSONL["counter"], workers=None):
    run([dict(j, engine=e) for j in COUNTEREXAMPLES for e in ("cpp", "python")], path, workers)


# --- the nominal (RSD-1) records of PR #146 -----------------------------------------------------------------------

PR146_HEAD = "1c0a7f4b9d666768ffdd159d0543810f4141276a"     # draft PR #146 (issue #113 rework), its inaudible.json
PR146_JSON = "prototypes/fullstage/dk6as6/inaudible.json"


def import_pr146(path=JSONL["cpp"], src=None):
    """Append PR #146's C++ records of T in BIASES (window 1 s, every post-filter position) to the C++ JSONL as shape
    RSD-1, tagged source "PR #146 <head>": the same engine, criterion and signals as this study's RSD-1 screen, which
    reproduces them exactly (test_dk6as6_grid_bounds_inaudible.test_rsd1_screen_reproduces_pr146). Points this
    study runs itself are not overwritten (load keeps the last record of a point, so they are appended first)."""
    if src is None:
        txt = subprocess.run(["git", "-C", ROOT, "show", f"{PR146_HEAD}:{PR146_JSON}"], capture_output=True,
                             text=True, check=True).stdout
        src = json.loads(txt)
    have = {point(r) for r in load(path)}
    add = []
    for r in src["records_cpp"]:
        if r["mode"] != MODE or float(r["bias_ma"]) not in BIASES or r["window_s"] != WINDOW_S \
                or int(r["tone"]) not in GRID_HZ:
            continue
        q = dict(r, shape=pentode.GRID_SHAPE, source=f"PR #146 {PR146_HEAD[:7]}")
        if point(q) not in have:
            add.append(q)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old = open(path).read() if os.path.exists(path) else ""
    with open(path + ".tmp", "w") as fh:
        fh.write("".join(json.dumps(q) + "\n" for q in add) + old)
    os.replace(path + ".tmp", path)
    print(f"{len(add)} PR #146 RSD-1 records -> {path}")


# --- summary -----------------------------------------------------------------------------------------------------

KEEP = ("engine", "source", "shape", "fs", "os", "lpf", "bias_ma", "drive", "tone", "f_hz", "passed", "margin_db",
        "worst_hz", "worst_dbfs", "worst_limit_dbfs", "worst_source", "worst_order", "loudest_dbfs", "loudest_hz",
        "floor_margin_db", "spl_headroom_db", "n_failing", "fund_dbfs", "peak", "nonconverged", "out_of_law", "rk")


def compact(r):
    return {k: (round(r[k], 4) if isinstance(r[k], float) and k != "rk" else r[k]) for k in KEEP if k in r}


def keyof(r):
    return (r["shape"], int(r["fs"]), int(r["os"]), r.get("lpf", "off"), float(r["bias_ma"]), float(r["drive"]))


def summarise(recs):
    """Per (shape, rate, factor, post-filter, bias, drive): the worst-margin record over the tones, the tone count."""
    by = {}
    for r in recs:
        by.setdefault(keyof(r), []).append(r)
    out = []
    for k in sorted(by):
        w = min(by[k], key=lambda r: r["margin_db"])
        out.append(dict(shape=k[0], fs=k[1], os=k[2], lpf=k[3], bias_ma=k[4], drive=k[5], tones=len(by[k]),
                        passed=all(r["passed"] for r in by[k]), nonconverged=sum(r["nonconverged"] for r in by[k]),
                        worst=compact(w)))
    return out


def index(summary):
    return {(x["shape"], x["fs"], x["os"], x["lpf"], x["bias_ma"], x["drive"]): x for x in summary}


def complete(x):
    return x is not None and x["tones"] == len(GRID_HZ)


def min_factor(idx, shape, fs, bias, drive, lpf="off", factors=FACTORS):
    """(the lowest factor at which every grid tone passes, measured; whether the factor below it was measured and
    failed). (None, None): no factor passes."""
    fs_ = sorted(factors)
    for i, f in enumerate(fs_):
        if passes(idx, shape, fs, f, drive, lpf, bias) is not None:
            below = idx.get((shape, fs, fs_[i - 1], lpf, bias, drive)) if i else None
            return f, (below is not None and not below["passed"]) or i == 0
    return None, None


def passes(idx, shape, fs, os_, drive, lpf, b):
    """"measured" if the key was measured at this factor and passed all GRID_HZ tones, else None. Nothing is inferred
    from another factor (PR #151 review: the "fill" stage measures every key of the decision table)."""
    x = idx.get((shape, fs, os_, lpf, b, drive))
    return "measured" if complete(x) and x["passed"] else None


def holds_to(idx, shape, fs, os_, drive, lpf="off", biases=BIASES):
    """The highest bias b such that every bias <= b passes at this factor (None: not even the lowest)."""
    top = None
    for b in biases:
        if passes(idx, shape, fs, os_, drive, lpf, b) is None:
            break
        top = b
    return top


DECIDING = dict(factors=(16, 32), lpfs=("off", "4k"), drive=8.0)     # the #146 choices: standard / hq, filter off / 4k


def confirm_points(cpp, shapes=pentode.GRID_BOUNDS, rates=RATES, factors=DECIDING["factors"], lpfs=DECIDING["lpfs"],
                   drive=DECIDING["drive"]):
    """The points the conclusions rest on, re-run on the python engine: per bound, rate, deciding factor and
    post-filter position, the worst tone at the top bias that holds (holds_to) and at the next bias (the first that
    fails), each as the C++ screen measured them."""
    idx = index(summarise(cpp))
    out = []
    for s in shapes:
        for fs in rates:
            for f in factors:
                for lp in lpfs:
                    top = holds_to(idx, s, fs, f, drive, lp)
                    i = -1 if top is None else BIASES.index(top)
                    for b in BIASES[max(i, 0):i + 2]:
                        x = idx.get((s, fs, f, lp, b, drive))
                        if x is not None:                    # measured at this factor (not inferred)
                            w = x["worst"]
                            out.append(dict(engine="python", shape=s, fs=fs, os_=f, bias=b, drive=drive,
                                            tone=int(w["tone"]), lpf=lp))
    seen, uniq = set(), []
    for j in out:
        k = tuple(sorted(j.items()))
        if k not in seen:
            seen.add(k)
            uniq.append(j)
    return sorted(uniq, key=lambda j: -j["os_"])           # the slow (32x) points first


LPFS = ("off", "4k")      # off, and PR #146's worst post-filter position for every T key (7k is not studied here)


def collect(out=OUT_JSON):
    cpp = [compact(r) for r in load(JSONL["cpp"]) if r.get("lpf", "off") in LPFS]
    py = [compact(r) for r in load(JSONL["python"])]
    ci = {(*keyof(r), int(r["tone"])): r for r in cpp}
    pairs = []
    for r in py:
        c = ci.get((*keyof(r), int(r["tone"])))
        if c is not None:
            pairs.append(dict(key=list(keyof(r)), tone=r["tone"], python_margin_db=r["margin_db"],
                              cpp_margin_db=c["margin_db"], diff_db=round(r["margin_db"] - c["margin_db"], 4),
                              python_worst_hz=r["worst_hz"], cpp_worst_hz=c["worst_hz"],
                              rk_rel=abs(r["rk"] / c["rk"] - 1.0)))
    method = dict(label=LABEL, criterion="every product <= min(FLOOR_DBFS, T_q(f) - UNDER_THRESHOLD_DB - SPL_FS_DB)",
                  floor_dbfs=IN.FLOOR_DBFS, under_threshold_db=IN.UNDER_THRESHOLD_DB, spl_fs_db=IN.SPL_FS_DB,
                  threshold=IN.CITATION, calibration=IN.CALIBRATION, settle_s=SETTLE_S, window_s=WINDOW_S,
                  grid_hz=list(GRID_HZ), fir=FIR, mode=MODE, biases=list(BIASES), factors=list(FACTORS),
                  lpfs=list(LPFS), fine_biases=list(FINE_BIASES), pr146_head=PR146_HEAD,
                  shapes={s: dict(table1=pentode.GRID_SHAPES[s], grid=pentode.grid(shape=s)) for s in SHAPES},
                  source="Dempwolf & Zoelzer, DAFx-11, Table 1, scaled as pentode.grid (#112 / PR #128)",
                  cpp_engine=cpp_engine_note())
    counter = [compact(r) for r in load(JSONL["counter"])]
    json.dump(dict(method=method, summary_cpp=summarise(cpp), summary_python=summarise(py), parity=pairs,
                   counterexamples=counter, records_cpp=cpp, records_python=py), open(out, "w"),
              separators=(",", ":"))
    print(f"{len(cpp)} cpp, {len(py)} python records, {len(pairs)} parity pairs -> {out}")


# --- tables ------------------------------------------------------------------------------------------------------

def _cell(x):
    if x is None:
        return "—"
    m = x["worst"]["margin_db"]
    if complete(x):
        return f"{m:+.1f}"
    # a fail-fast key: its worst measured tone fails, so its worst over all tones is at most this
    return f"≤ {m:+.1f}" if not x["passed"] else f"{m:+.1f} ({x['tones']} tones, undecided)"


def passed_all(x):
    return complete(x) and x["passed"]


def region(idx, fs, os_, lpf, drive=8.0, shapes=SHAPES):
    """{shape: holds_to} and the region that holds under every shape (the lowest holds_to; None if any is None)."""
    per = {s: holds_to(idx, s, fs, os_, drive, lpf) for s in shapes}
    tops = list(per.values())
    return per, (None if any(t is None for t in tops) else min(tops))


def tables(path=OUT_JSON):
    d = json.load(open(path))
    idx = index(d["summary_cpp"])
    L = []
    bh = " | ".join(f"{b:g}" for b in BIASES)
    L.append(f"| rate | factor | post-filter | shape | {bh} |")
    L.append("|---" * (4 + len(BIASES)) + "|")
    for fs in RATES:
        for f in FACTORS:
            for lp in ("off", "4k"):
                for sh in SHAPES:
                    xs = [idx.get((sh, fs, f, lp, b, 8.0)) for b in BIASES]
                    if all(x is None for x in xs):
                        continue
                    L.append(f"| {fs / 1000:g} kHz | {f}× | {lp} | {sh} | " + " | ".join(_cell(x) for x in xs) + " |")
    L.append("")
    L.append("| rate | post-filter | shape | lowest passing factor at " + " / ".join(f"{b:g}" for b in BIASES)
             + " mA (≤: the factor below not measured) |")
    L.append("|---|---|---|---|")
    for fs in RATES:
        for lp in ("off", "4k"):
            for sh in SHAPES:
                mf = [min_factor(idx, sh, fs, b, 8.0, lp) for b in BIASES]
                L.append(f"| {fs / 1000:g} kHz | {lp} | {sh} | " + " / ".join(
                    "—" if m is None else f"{m}×" if exact else f"≤ {m}×" for m, exact in mf) + " |")
    L.append("")
    L.append("| rate | factor | post-filter | holds to (mA): " + " / ".join(SHAPES) + " | under every shape |")
    L.append("|---|---|---|---|---|")
    for fs in RATES:
        for f in FACTORS:
            for lp in ("off", "4k"):
                per, allb = region(idx, fs, f, lp)
                if not any(k[1:4] == (fs, f, lp) and k[5] == 8.0 for k in idx):
                    continue
                c = " / ".join("none" if v is None else f"{v:g}" for v in per.values())
                L.append(f"| {fs / 1000:g} kHz | {f}× | {lp} | {c} | {'none' if allb is None else f'{allb:g} mA'} |")
    L.append("")
    fine = (0.3,) + FINE_BIASES + (0.35,)
    L.append("| 16×, 8 V: rate | post-filter | shape | " + " | ".join(f"{b:g}" for b in fine) + " mA |")
    L.append("|---" * (3 + len(fine)) + "|")
    for fs in RATES:
        for lp in ("off", "4k"):
            for sh in SHAPES:
                L.append(f"| {fs / 1000:g} kHz | {lp} | {sh} | " + " | ".join(
                    _cell(idx.get((sh, fs, 16, lp, b, 8.0))) for b in fine) + " |")
    L.append("")
    L.append("| 16×, 8 V: rate | post-filter | holds to over the refined grid (mA): " + " / ".join(SHAPES)
             + " | under every shape |")
    L.append("|---|---|---|---|")
    grid = tuple(sorted(BIASES + FINE_BIASES))
    for fs in RATES:
        for lp in ("off", "4k"):
            per = {s: holds_to(idx, s, fs, 16, 8.0, lp, grid) for s in SHAPES}
            allb = None if any(v is None for v in per.values()) else min(per.values())
            L.append(f"| {fs / 1000:g} kHz | {lp} | " + " / ".join(f"{v:g}" for v in per.values())
                     + f" | {allb:g} mA |")
    L.append("")
    two = [x for x in d["summary_cpp"] if x["drive"] == 2.0]
    if two:
        L.append("| rate | factor | post-filter | shape | worst 2 V margin (dB), biases |")
        L.append("|---|---|---|---|---|")
        for fs in RATES:
            for f in FACTORS:
                for lp in ("off", "4k"):
                    for sh in SHAPES:
                        xs = [x for x in two if (x["shape"], x["fs"], x["os"], x["lpf"]) == (sh, fs, f, lp)]
                        if xs:
                            w = min(xs, key=lambda x: x["worst"]["margin_db"])
                            L.append(f"| {fs / 1000:g} kHz | {f}× | {lp} | {sh} | {w['worst']['margin_db']:+.1f} at "
                                     f"{w['bias_ma']:g} mA ({len(xs)} biases; all pass: "
                                     f"{all(passed_all(x) for x in xs)}) |")
        L.append("")
    if d.get("counterexamples"):
        L.append("| engine | shape | rate | factor | post-filter | bias | tone | margin (dB) | worst product |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(d["counterexamples"], key=lambda r: (r["bias_ma"], r["shape"], r["engine"])):
            L.append(f"| {r['engine']} | {r['shape']} | {r['fs'] / 1000:g} kHz | {r['os']}× | {r['lpf']} | "
                     f"{r['bias_ma']:g} | {r['tone']} | {r['margin_db']:+.2f} | {r['worst_hz']:.0f} Hz "
                     f"({r.get('worst_source', '')}) |")
        L.append("")
    if d["parity"]:
        L.append("| shape | rate | factor | post-filter | bias | tone | python | C++ | diff (dB) |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for q in sorted(d["parity"], key=lambda q: (q["key"], q["tone"])):
            sh, fs, f, lp, b, _ = q["key"]
            L.append(f"| {sh} | {fs / 1000:g} kHz | {f}× | {lp} | {b:g} | {q['tone']} | {q['python_margin_db']:+.2f} | "
                     f"{q['cpp_margin_db']:+.2f} | {q['diff_db']:+.4f} |")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "stage", "confirm", "collect", "tables", "build", "import146", "counter"))
    ap.add_argument("--stage", choices=("16", "32", "64", "8", "2v", "fill", "fine", "2v4k"))
    ap.add_argument("--shapes", nargs="*", default=list(SHAPES))
    ap.add_argument("--factors", nargs="*", type=int, default=list(FACTORS))
    ap.add_argument("--rates", nargs="*", type=int, default=list(RATES))
    ap.add_argument("--biases", nargs="*", type=float, default=list(BIASES))
    ap.add_argument("--drives", nargs="*", type=float, default=list(DRIVES))
    ap.add_argument("--lpfs", nargs="*", default=["off"])
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    if a.cmd == "build":
        for s in a.shapes:
            print(s, screen_build(s))
    elif a.cmd == "run":
        run(plan(a.shapes, a.factors, a.rates, a.biases, a.drives, lpfs=a.lpfs), JSONL["cpp"], a.workers)
    elif a.cmd == "stage":
        staged(stage_plan(a.stage, a.shapes), workers=a.workers)
    elif a.cmd == "confirm":
        js = confirm_points([compact(r) for r in load(JSONL["cpp"])])
        print(len(js), "python points")
        run(js, JSONL["python"], a.workers)
    elif a.cmd == "counter":
        run_counterexamples(workers=a.workers)
    elif a.cmd == "import146":
        import_pr146()
    elif a.cmd == "collect":
        collect()
    elif a.cmd == "tables":
        print(tables())
