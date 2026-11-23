"""The inaudibility criterion (dk6as6/inaudible.py) over every mode, bias, drive, rate, tone and oversampling factor of
the 6AS6 stage with grid current and the k140 resampler (#113 rework): the evidence behind the qualities in
dk6as6.stage.QUALITY and the CI guard (dk6as6/aliasing_guard.py).

Each point: a drive-volt sine on a coherent bin near `tone` (dk.aliasing.tone_bin), the stage from its DC operating
point, SETTLE_S (dk6as6.aliasing.SETTLE_S, 1.25 s: the #102 coupling caps) then a 1 s window, assessed by
inaudible.assess.

Two engines, the same stage:
  python  dk6as6.stage.Stage (the reference; pure Python, about 75 us per internal sample on a loaded M1, so a 32x point
          takes ~4 min and the full grid is out of reach on one machine);
  cpp     the C++ 6AS6 core (#104, cpp/dk6as6_core.h, on main since PR #133) through its `test_core_6as6 cli`
          harness, given by --cli PATH. That core reads the same as this stage to 1.1e-5 dB on #104's aliasing grid
          (PR #133). Its MAX_OS is 16; for 32x and 64x the screening binary raises dk_core.h's MAX_OS to 64 and
          ResamplerT's later_[3] to later_[5] (two lines, nothing else, not committed). Every record names its
          engine; every key's deciding points are re-run on the python engine (`confirm`), and the committed
          conclusions rest on those.
Run from prototypes/fullstage:
  python -m dk6as6.inaudible_sweep run --engine cpp --cli PATH [--factors 16 32] [--workers N] [--jsonl PATH]
  python -m dk6as6.inaudible_sweep confirm [--workers N]      the python engine at CONFIRM points -> JSONL
  python -m dk6as6.inaudible_sweep collect                    JSONLs -> dk6as6/inaudible.json
  python -m dk6as6.inaudible_sweep tables                     the RESULTS.md tables
"""
import argparse
import json
import os
import subprocess
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import tone_bin, WINDOW_S, SWEEP_HZ, BAND_HZ  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk6as6 import inaudible as IN  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.pentode import HARDNESS  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, fir_design, supported, ExperimentalWarning  # noqa: E402
from dk6as6 import lpf as LPF  # noqa: E402 -- after the sys.path setup

FACTORS = (4, 8, 16, 32, 64)
RATES = IN.RATES
DRIVES = IN.DRIVES
# #113's assessment grid (the #6 tones and 19937 Hz), its band-edge scan, and #104's extra band-edge tones
GRID_HZ = tuple(SWEEP_HZ) + (19237, 19437, 19637, 19831, 19837, 19937, 19957, 19977, 19987, 19997)
# Inputs above 20 kHz, below each rate's Nyquist (PR #146 review round 2). They are outside the guaranteed scope (the
# k140 interpolator passes to 20 kHz and stops from fs - 20 kHz, so a tone f in 20 kHz..fs/2 keeps an image at fs - f in
# the transition band, which the stage mixes onto |2 f - fs|): measured and reported, not guarded (RESULTS.md).
ABOVE_20K_HZ = {48000: (20299, 20899, 21499, 22099, 22699, 23299, 23899), 44100: (20299, 20599, 20899, 21199, 21499,
                                                                                 21799)}
LPFS = LPF.POSITIONS                 # the post-filter positions (#103): "off", "4k", "7k"
METRIC = 3                           # inaudible.products: 1 = +-3-bin harmonic mask, 2 = Hann (review round 2),
#                                      3 = unwindowed, every non-harmonic bin (review round 3, now)
# Cases (mode, bias mA): every reference bias of each mode, T's supported top (0.5 mA) and its steps, and #132's
# unsupported 0.55 / 0.6 mA; P2 (experimental, #123) at its reference biases. All with grid current (the default).
CASES = (tuple(("P1", b) for b in (0.1, 0.25, 0.45, 0.6))
         + tuple(("T", b) for b in (0.1, 0.25, 0.3, 0.35, 0.375, 0.4, 0.45, 0.5, 0.55, 0.6))
         + tuple(("P2", b) for b in (0.1, 0.25, 0.45, 0.6)))
SCRATCH = os.path.expanduser(os.environ.get("ISSUE113_SCRATCH", os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(HERE))), ".dispatch", "scratch")))
JSONL = {"cpp": os.path.join(SCRATCH, f"inaudible_cpp_v{METRIC}.jsonl"),
         "python": os.path.join(SCRATCH, f"inaudible_py_v{METRIC}.jsonl")}
OUT_JSON = os.path.join(HERE, "inaudible.json")
CPP_ENGINE = ("PR #133 head fc5d690 (cpp/dk6as6_core.h, dk_core.h, test_core_6as6.cpp cli: identical on main at "
              "c8d882a; Apple clang 17 -O2), with dk_core.h's MAX_OS = 64 and ResamplerT's later_[5] (from 16 and "
              "later_[3]) so that it runs 32x and 64x")


def signal(fs, tone, drive, latency, window=WINDOW_S, settle=SETTLE_S):
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(tone, n, fs)
    return m, n, n0, drive * np.sin(2 * np.pi * m * np.arange(n0 + n + latency) / n)


def run_python(fs, os_, mode, bias, drive, tone, window=WINDOW_S, settle=SETTLE_S, lpf="off"):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        kw = dict(bias=bias, quality=(os_, None), mode=mode, experimental=True, grid_hardness=HARDNESS, fir="k140")
        st = Stage(fs, **kw) if lpf == "off" else LPF.Stage(fs, lpf=lpf, **kw)
    L = st.latency_samples
    m, n, n0, x = signal(fs, tone, drive, L, window, settle)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return m, y, dict(nonconverged=int(st.nonconv), out_of_law=int(st.out_of_law))


def run_cpp(fs, os_, mode, bias, drive, tone, cli, window=WINDOW_S, settle=SETTLE_S, lpf="off"):
    L = Resampler(fs, os_, "fir", fir=fir_design("k140", fs)).latency_samples
    m, n, n0, x = signal(fs, tone, drive, L, window, settle)
    args = [cli, "cli", repr(float(fs)), str(int(os_)), mode, repr(float(bias)), repr(float(HARDNESS)), lpf,
            "1.0", "1.0", "k140"]
    p = subprocess.run(args, input=np.ascontiguousarray(x, np.float64).tobytes(), capture_output=True)
    if p.returncode:
        raise RuntimeError(f"{cli} cli failed ({p.returncode}): {p.stderr.decode()[-500:]}")
    info = json.loads(p.stderr.decode().strip().splitlines()[-1])
    if info["latency_samples"] != L:
        raise RuntimeError(f"C++ latency {info['latency_samples']} != the Python stage's {L}")
    y = np.frombuffer(p.stdout, np.float64)[:len(x)][n0 + L:n0 + L + n]
    st = info["stats"]
    return m, y, dict(nonconverged=int(st["nonconverged"]), out_of_law=int(st["out_of_law"]))


def measure(engine, fs, os_, mode, bias, drive, tone, cli=None, window=WINDOW_S, settle=SETTLE_S, lpf="off"):
    t = time.perf_counter()
    if engine == "python":
        m, y, stats = run_python(fs, os_, mode, bias, drive, tone, window, settle, lpf)
    else:
        m, y, stats = run_cpp(fs, os_, mode, bias, drive, tone, cli, window, settle, lpf)
    n = len(y)
    a = IN.assess(y, m, fs, os_)
    return dict(engine=engine, metric=METRIC, fs=fs, os=os_, fir="k140", lpf=lpf, mode=mode, bias_ma=bias,
                drive=drive, tone=tone, f_hz=m * fs / n, window_s=window, settle_s=settle, grid_hardness=HARDNESS,
                in_scope=tone < BAND_HZ[1], **stats, **a, wall_s=time.perf_counter() - t)


def point(r):
    return (r["engine"], int(r["fs"]), int(r["os"]), r.get("lpf", "off"), r["mode"], float(r["bias_ma"]),
            float(r["drive"]), int(r["tone"]), float(r["window_s"]))


def load(path):
    if not os.path.exists(path):
        return []
    out = {}
    for line in open(path):
        line = line.strip()
        if line:
            r = json.loads(line)
            out[point(r)] = r
    return list(out.values())


def _job(a):
    return measure(*a[:7], **a[7])


def _point_of_job(j):
    e, fs, f, m, b, d, t, kw = j
    return (e, int(fs), int(f), kw.get("lpf", "off"), m, float(b), float(d), int(t), float(kw.get("window", WINDOW_S)))


def run(jobs, path, workers):
    done = {point(r) for r in load(path)}
    todo = [j for j in jobs if _point_of_job(j) not in done]
    print(f"{len(jobs) - len(todo)} done, {len(todo)} to run -> {path}", flush=True)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with ProcessPoolExecutor(workers) as ex, open(path, "a") as fh:
        futs = {ex.submit(_job, j): j for j in todo}
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if i % 25 == 0 or not r["passed"] and r["os"] >= 16:
                print(f"[{i}/{len(todo)}] {r['fs']} {r['os']}x {r['mode']} {r['bias_ma']:g} mA {r['drive']:g} V "
                      f"{r['tone']} Hz: margin {r['margin_db']:+.1f} dB at {r['worst_hz']:.0f} Hz", flush=True)


def plan(factors=FACTORS, cases=CASES, rates=RATES, drives=DRIVES, tones=GRID_HZ, lpfs=("off",)):
    # the cheap factors last within a rate, so the deciding (16x / 32x) points come first. tones: a tuple, or
    # "above20k" for each rate's ABOVE_20K_HZ
    order = sorted(factors, key=lambda f: {16: 0, 32: 1, 8: 2, 64: 3}.get(f, 4))
    return [(fs, f, lp, m, b, d, t) for f in order for fs in rates for lp in lpfs for m, b in cases for d in drives
            for t in (ABOVE_20K_HZ[fs] if tones == "above20k" else tones)]


KEEP = ("engine", "metric", "fs", "os", "lpf", "mode", "bias_ma", "drive", "tone", "f_hz", "window_s", "in_scope",
        "supported", "passed", "margin_db", "worst_hz", "worst_dbfs", "worst_limit_dbfs", "worst_source",
        "worst_order", "loudest_dbfs", "loudest_hz", "floor_margin_db", "spl_headroom_db", "n_failing",
        "first_hidden_order", "margin_hann_db", "fund_dbfs", "peak", "nonconverged", "out_of_law")


def compact(r):
    """One record as committed. `supported` is recomputed here from the stage's envelope at collect time
    (stage.supported), never kept from when the point ran (PR #146 review round 2: points measured before the T cap
    carried T to 0.5 mA)."""
    r = dict(r, supported=supported(r["mode"], r["bias_ma"]), lpf=r.get("lpf", "off"),
             in_scope=int(r["tone"]) < BAND_HZ[1])
    return {k: (round(r[k], 4) if isinstance(r[k], float) else r[k]) for k in KEEP if k in r}


def keyof(r):
    return (int(r["fs"]), int(r["os"]), r.get("lpf", "off"), r["mode"], float(r["bias_ma"]), float(r["drive"]))


def summarise(recs):
    """Per (rate, factor, post-filter position, mode, bias, drive): the worst-margin record over the in-scope tones
    (inputs below 20 kHz), and the tone count; above-20 kHz inputs are summarised apart (summarise_above_20k)."""
    by = {}
    for r in recs:
        if r["in_scope"]:
            by.setdefault(keyof(r), []).append(r)
    out = []
    for k in sorted(by):
        w = min(by[k], key=lambda r: r["margin_db"])
        out.append(dict(fs=k[0], os=k[1], lpf=k[2], mode=k[3], bias_ma=k[4], drive=k[5], tones=len(by[k]),
                        supported=supported(k[3], k[4]), passed=all(r["passed"] for r in by[k]), worst=compact(w)))
    return out


def min_factor(summary, fs, mode, bias, drive, factors=FACTORS, lpf="off"):
    """The lowest measured factor at which every grid tone passes at this drive (None if none measured passes)."""
    for f in sorted(factors):
        s = [x for x in summary
             if (x["fs"], x["os"], x["lpf"], x["mode"], x["bias_ma"], x["drive"]) == (fs, f, lpf, mode, bias, drive)]
        if s and all(x["passed"] and x["tones"] == len(GRID_HZ) for x in s):
            return f
    return None


def collect(cpp_jsonl=JSONL["cpp"], py_jsonl=JSONL["python"], out=OUT_JSON, cli_note=None):
    cpp = [compact(r) for r in load(cpp_jsonl)]
    py = [compact(r) for r in load(py_jsonl)]
    idx = {(*keyof(r), int(r["tone"])): r for r in cpp if r["window_s"] == WINDOW_S}
    pairs = []
    for r in py:
        c = idx.get((*keyof(r), int(r["tone"])))
        if c is not None and r["window_s"] == WINDOW_S:
            pairs.append(dict(key=list(keyof(r)), tone=r["tone"], python_margin_db=r["margin_db"],
                              cpp_margin_db=c["margin_db"], diff_db=round(r["margin_db"] - c["margin_db"], 4),
                              python_worst_hz=r["worst_hz"], cpp_worst_hz=c["worst_hz"]))
    crit = "every alias product <= min(FLOOR_DBFS, T_q(f) - UNDER_THRESHOLD_DB - SPL_FS_DB)"
    json.dump(dict(method=dict(criterion=crit, metric=METRIC,
                               floor_dbfs=IN.FLOOR_DBFS, under_threshold_db=IN.UNDER_THRESHOLD_DB,
                               spl_fs_db=IN.SPL_FS_DB, threshold=IN.CITATION, calibration=IN.CALIBRATION,
                               settle_s=SETTLE_S, window_s=WINDOW_S, grid_hz=list(GRID_HZ),
                               above_20k_hz={str(k): list(v) for k, v in ABOVE_20K_HZ.items()}, lpfs=list(LPFS),
                               fir="k140", grid_hardness=HARDNESS, cases=[list(c) for c in CASES],
                               factors=list(FACTORS), cpp_engine=cli_note),
                   summary_cpp=summarise(cpp), summary_python=summarise(py), parity=pairs, records_cpp=cpp,
                   records_python=py), open(out, "w"), separators=(",", ":"))
    print(f"{len(cpp)} cpp, {len(py)} python records, {len(pairs)} parity pairs -> {out}")


PARITY_DB = 0.01      # python vs cpp margin, dB (measured: test_dk6as6_aliasing_guard.test_python_confirms_the_screen)


def _cell(s):
    if s is None:
        return "—"
    w = s["worst"]
    flag = "" if s["tones"] == len(GRID_HZ) else f" ({s['tones']} tones)"
    return f"{w['margin_db']:+.1f}{flag}"


def tables(path=OUT_JSON, factors=(8, 16, 32, 64)):
    """The RESULTS.md tables from inaudible.json: the worst margin per case, factor, rate and drive (C++ screen, full
    grid, post-filter off), the lowest passing factor per case."""
    d = json.load(open(path))
    summ = {(x["fs"], x["os"], x["lpf"], x["mode"], x["bias_ma"], x["drive"]): x for x in d["summary_cpp"]}
    lines = []
    for drv in DRIVES:
        lines += [f"\n**{drv:g} V**: the smallest margin over the {len(GRID_HZ)} grid tones, dB (negative = fails)", "",
                  "| case | " + " | ".join(f"{fs / 1000:g} kHz {f}x" for fs in RATES for f in factors) + " |",
                  "|---" * (1 + len(RATES) * len(factors)) + "|"]
        for m, b in CASES:
            sup = "" if supported(m, b) else " (unsupported)"
            lines.append(f"| {m} {b:g} mA{sup} | " + " | ".join(_cell(summ.get((fs, f, "off", m, b, drv)))
                                                            for fs in RATES for f in factors) + " |")
    lines += ["", "The lowest measured factor at which every grid tone passes (— : none measured passes):", "",
              "| case | " + " | ".join(f"{fs / 1000:g} kHz, {drv:g} V" for fs in RATES for drv in DRIVES) + " |",
              "|---" * (1 + len(RATES) * len(DRIVES)) + "|"]
    for m, b in CASES:
        mins = [min_factor(d["summary_cpp"], fs, m, b, drv) for fs in RATES for drv in DRIVES]
        lines.append(f"| {m} {b:g} mA | " + " | ".join(f"{f}×" if f else "—" for f in mins) + " |")
    return "\n".join(lines)


def lpf_table(path=OUT_JSON, factors=(16, 32)):
    """Per offered factor, rate, drive and post-filter position: the worst supported case and its margin (in scope)."""
    d = json.load(open(path))
    lines = ["| rate | factor | drive | " + " | ".join(f"filter {p}" for p in LPFS) + " |",
             "|---" * (3 + len(LPFS)) + "|"]
    for fs in RATES:
        for f in factors:
            for drv in DRIVES:
                cells = []
                for p in LPFS:
                    s = [x for x in d["summary_cpp"] if (x["fs"], x["os"], x["lpf"], x["drive"]) == (fs, f, p, drv)
                         and x["supported"]]
                    if not s:
                        cells.append("—")
                        continue
                    w = min(s, key=lambda x: x["worst"]["margin_db"])
                    cells.append(f"{w['worst']['margin_db']:+.1f} ({w['mode']} {w['bias_ma']:g} mA, "
                                 f"{w['worst']['tone']} Hz)")
                lines.append(f"| {fs / 1000:g} kHz | {f}x | {drv:g} V | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def above_20k_table(path=OUT_JSON, factors=(16, 32)):
    """Inputs above 20 kHz (out of scope, reported): per rate, factor, drive and position, the worst supported case."""
    d = json.load(open(path))
    lines = ["| rate | factor | drive | " + " | ".join(f"filter {p}" for p in LPFS) + " |",
             "|---" * (3 + len(LPFS)) + "|"]
    for fs in RATES:
        for f in factors:
            for drv in DRIVES:
                cells = []
                for p in LPFS:
                    rs = [r for r in d["records_cpp"] if not r["in_scope"] and r["supported"]
                          and (r["fs"], r["os"], r["lpf"], r["drive"]) == (fs, f, p, drv)]
                    if not rs:
                        cells.append("—")
                        continue
                    w = min(rs, key=lambda r: r["margin_db"])
                    cells.append(f"{w['margin_db']:+.1f} ({w['mode']} {w['bias_ma']:g} mA, {w['tone']} Hz → "
                                 f"{w['worst_hz']:.0f} Hz, {w['worst_dbfs']:.1f} dBFS)")
                lines.append(f"| {fs / 1000:g} kHz | {f}x | {drv:g} V | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def worst_table(path=OUT_JSON, factors=(16, 32)):
    """Per rate, factor and drive: the worst supported in-scope point over every post-filter position."""
    d = json.load(open(path))
    lines = ["| rate | factor | drive | worst supported point | margin dB | product Hz | level dBFS | limit dBFS | "
             "source | SPL headroom dB |", "|---" * 10 + "|"]
    for fs in RATES:
        for f in factors:
            for drv in DRIVES:
                rs = [r for r in d["records_cpp"] if (r["fs"], r["os"], r["drive"]) == (fs, f, drv)
                      and r["supported"] and r["in_scope"]]
                if not rs:
                    continue
                w = min(rs, key=lambda r: r["margin_db"])
                lines.append(f"| {fs / 1000:g} kHz | {f}x | {drv:g} V | {w['mode']} {w['bias_ma']:g} mA, "
                             f"{w['tone']} Hz, filter {w['lpf']} | "
                             f"{w['margin_db']:+.1f} | {w['worst_hz']:.0f} | {w['worst_dbfs']:.1f} | "
                             f"{w['worst_limit_dbfs']:.1f} | {w['worst_source']} | {w['spl_headroom_db']:+.1f} |")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--engine", choices=("cpp", "python"), default="cpp")
    r.add_argument("--cli")
    r.add_argument("--factors", nargs="*", type=int, default=list(FACTORS))
    r.add_argument("--modes", nargs="*", default=None)
    r.add_argument("--biases", nargs="*", type=float, default=None)
    r.add_argument("--drives", nargs="*", type=float, default=list(DRIVES))
    r.add_argument("--rates", nargs="*", type=int, default=list(RATES))
    r.add_argument("--tones", nargs="*", default=None, help='Hz, or "above20k" (default: GRID_HZ)')
    r.add_argument("--lpfs", nargs="*", default=["off"], choices=LPFS)
    r.add_argument("--workers", type=int, default=os.cpu_count())
    r.add_argument("--jsonl")
    c = sub.add_parser("confirm", help="the python engine at each key's worst cpp tone (complete keys only)")
    c.add_argument("--factors", nargs="*", type=int, default=[16, 32])
    c.add_argument("--rates", nargs="*", type=int, default=list(RATES))
    c.add_argument("--modes", nargs="*", default=None)
    c.add_argument("--min-bias", type=float, default=0.0)
    c.add_argument("--max-bias", type=float, default=1.0)
    c.add_argument("--workers", type=int, default=2)
    c.add_argument("--guard-keys", action="store_true",
                   help="instead: each CI guard key's first tone (aliasing_guard.CI), every rate and offered quality")
    c.add_argument("--points", nargs="*", default=[],
                   help="instead: explicit points fs:os:lpf:mode:bias:drive:tone")
    sub.add_parser("collect")
    sub.add_parser("tables")
    a = ap.parse_args(argv)
    if a.cmd == "confirm" and (a.guard_keys or a.points):
        from dk6as6 import aliasing_guard as AG
        jobs = [("python", fs, AG.os_of(q), m, b, d, t[0], dict(lpf=lp)) for fs, qs in AG.CI.items()
                for q, keys in qs.items() for (m, b, lp, d), t in keys.items()] if a.guard_keys else []
        for p in a.points:
            fs, f, lp, m, b, d, t = p.split(":")
            jobs.append(("python", int(fs), int(f), m, float(b), float(d), int(t), dict(lpf=lp)))
        run(jobs, JSONL["python"], a.workers)
    elif a.cmd == "confirm":
        summ = summarise(load(JSONL["cpp"]))
        jobs = [("python", s["fs"], s["os"], s["mode"], s["bias_ma"], s["drive"], s["worst"]["tone"],
                 dict(lpf=s["lpf"]))
                for s in summ if s["os"] in a.factors and s["fs"] in a.rates and s["tones"] == len(GRID_HZ)
                and (a.modes is None or s["mode"] in a.modes) and a.min_bias <= s["bias_ma"] <= a.max_bias]
        jobs.sort(key=lambda j: j[2])
        run(jobs, JSONL["python"], a.workers)
    elif a.cmd == "collect":
        collect(cli_note=CPP_ENGINE)
    elif a.cmd == "tables":
        for t in (tables(), worst_table(), lpf_table(), above_20k_table()):
            print(t)
            print()
    elif a.cmd == "run":
        if a.engine == "cpp" and not a.cli:
            ap.error("--engine cpp needs --cli PATH (module docstring)")
        cases = [c for c in CASES if (a.modes is None or c[0] in a.modes) and (a.biases is None or c[1] in a.biases)]
        tones = "above20k" if a.tones == ["above20k"] else tuple(int(t) for t in a.tones) if a.tones else GRID_HZ
        jobs = [(a.engine, fs, f, m, b, d, t, dict(lpf=lp, **(dict(cli=a.cli) if a.engine == "cpp" else {})))
                for fs, f, lp, m, b, d, t in plan(a.factors, cases, a.rates, a.drives, tones, a.lpfs)]
        run(jobs, a.jsonl or JSONL[a.engine], a.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
