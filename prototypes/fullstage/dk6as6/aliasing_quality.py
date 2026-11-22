"""Aliasing of the 6AS6 stage at every oversampling factor (#113): plain 4x, standard 8x and hq 16x (none pre-warped),
with the #103 post-filter off and on, at 48 and 44.1 kHz, for every supported mode / reference bias, against a 64x
ground truth. The metric and sweep are dk/aliasing.py's (offset tones SWEEP_HZ, 1 s coherent periodic-Hann window, total
non-harmonic energy 20 Hz-20 kHz in dBc, split internal / resampler / other); the settle is dk6as6.aliasing.SETTLE_S.

Each point is one stage run, decimated directly ("off") and through each PostFilter position (dk6as6/lpf_aliasing.py's
measure(), which this calls; "off" is bit for bit dk6as6.aliasing.measure). Per position: total / internal / resampler /
other dBc relative to that output's own fundamental, and abs_db_re_off_fund (the total relative to the UNFILTERED
fundamental, the absolute comparison: the filter cuts the fundamental too).

Cases: CASES (below): P1 0.1 / 0.25 / 0.45 / 0.6 mA and T 0.1 / 0.25 / 0.3 mA on the no-grid stage, which the
production default reads to <= 4.4e-4 dB there, and T 0.35 / 0.45 / 0.5 mA (supported only with grid current, #112) on
the default stage; every record carries its grid_hardness. P2 is experimental (#123); its numbers are in the resampler
study (dk6as6/resampler_study.py).

Reuse: P1 at standard 8x, both rates, every position, is dk6as6/lpf_aliasing.json (#119; the same function and
arguments). It is read from there, not rerun; the verify-reuse command remeasures a few of its points and requires them
bit for bit.

Run from prototypes/fullstage. Long (pure-Python Newton: ~35 / 70 / 140 / 560 CPU-s per point at 4x / 8x / 16x / 64x
on an M1); every point is appended to a JSONL as it finishes, so a run can be stopped and resumed:
  python -m dk6as6.aliasing_quality reproduce [--workers N] [--scratch DIR]   every run behind aliasing_quality.json
                                                       (REPRODUCE below, printed as it goes), then collect
  python -m dk6as6.aliasing_quality run --rate 48000 --setting 4x [--cases T:0.5] [--tones ...] [--jsonl PATH]
                                                       one run (default: every case, both drives, GRID_HZ)
  python -m dk6as6.aliasing_quality collect --jsonl GRID --scan SCAN --settle-check SETTLE --model-check MODEL
  python -m dk6as6.aliasing_quality tables | verify-reuse
"""
import argparse
import json
import os
import subprocess
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import SWEEP_HZ, DRIVES, WINDOW_S  # noqa: E402 -- after the sys.path setup
from dk6as6 import lpf, lpf_aliasing  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import supported, supported_records, ExperimentalWarning  # noqa: E402 -- after the sys.path setup
from dk6as6.pentode import HARDNESS  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import MODE_BIASES, GRID_BIASES  # noqa: E402 -- after the sys.path setup

RATES = (48000, 44100)
SETTINGS = {"4x": (4, None), "standard": (8, None), "hq": (16, None), "64x": (64, None)}   # (os, prewarp_hz): none
GROUND_TRUTH = "64x"
# The band edge (#113 review): dk/aliasing.SWEEP_HZ (the #6 grid) stops at 19037 Hz, but the interpolator's image of a
# tone f is at fs - f, which reaches the FIR's stopband edge (fs - 20 kHz) as f approaches 20 kHz, so the resampler's
# leakage grows toward the top of the band. BAND_EDGE_HZ is the highest tone of the grid's 37 Hz offset pattern (from a
# 100 Hz multiple) below 20 kHz: its 1 s bin is exact at both rates (collision order 11619 / 2181). GRID_HZ is the
# assessment's grid; band_edge_scan (19237-19987 Hz at the worst keys) shows how the total moves above 19037 Hz.
BAND_EDGE_HZ = 19937
GRID_HZ = tuple(SWEEP_HZ) + (BAND_EDGE_HZ,)
SCAN_HZ = (19237, 19437, 19637, 19831, 19837, 19987)   # 19831 Hz: the PR #129 re-review's probe (an image notch)
# Cases (mode, bias mA, grid hardness), with the model pinned and recorded on every record (#113 re-review):
#   CASES_NO_GRID  every reference bias supported WITHOUT grid current (stage.SUPPORTED_MA_NO_GRID: P1 0.1-0.6 mA, T
#                  0.1-0.3 mA), at grid_hardness 0 (the #111 stage these sweeps were first run on). The production
#                  default (#112: g1 grid current at pentode.HARDNESS) reads the same to <= 4.4e-4 dB at every compared
#                  point (model_check in OUT_JSON; test_default_stage_equals_the_no_grid_sweeps), so these sweeps
#                  stand for the default stage there.
#   CASES_GRID     the biases supported only WITH grid current (T 0.35 / 0.45 / 0.5 mA, stage.SUPPORTED_MA), at
#                  pentode.HARDNESS: the production default.
CASES_NO_GRID = tuple((m, b, 0.0) for m in ("P1", "T")
                      for b in sorted(set(MODE_BIASES[m]) | ({0.3} if m == "T" else set()))
                      if supported(m, b, grid_hardness=0.0))
CASES_GRID = tuple(("T", b, HARDNESS) for b in sorted(GRID_BIASES["T"])
                   if supported_records("T", b) and not supported("T", b, grid_hardness=0.0))
CASES = CASES_NO_GRID + CASES_GRID
REUSED = ("P1", "standard")            # (mode, setting) read from lpf_aliasing.json
OUT_JSON = os.path.join(HERE, "aliasing_quality.json")
SCRATCH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), ".scratch", "issue113")
JSONL = os.path.join(SCRATCH, "aliasing_quality.jsonl")


def hardness(r):
    """A record's grid hardness (records from before #112 carry none: they are the no-grid stage)."""
    return float(r.get("grid_hardness", 0.0))


def point_key(r):
    """Everything that defines a measurement, so a resumed run never mistakes one method for another."""
    return (r["mode"], r["setting"], int(r["fs"]), float(r["bias_ma"]), float(r["drive"]), int(r["tone"]),
            hardness(r), float(r["settle_s"]), float(r["window_s"]))


def case_key(r):
    return (r["mode"], float(r["bias_ma"]), hardness(r))


def measure(fs, setting, mode, bias, drive, tone, window=WINDOW_S, settle=SETTLE_S, grid_hardness=0.0):
    t = time.process_time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        r = lpf_aliasing.measure(fs, drive, tone, bias=bias, window=window, settle=settle,
                                 quality=SETTINGS[setting], mode=mode, grid_hardness=grid_hardness)
    return dict(r, mode=mode, setting=setting, os=SETTINGS[setting][0], grid_hardness=float(grid_hardness),
                cpu_s=time.process_time() - t)


def _job(a):
    return measure(*a)


def load_jsonl(paths):
    recs = {}
    for p in paths:
        if os.path.exists(p):
            for line in open(p):
                if line.strip():
                    r = json.loads(line)
                    recs[point_key(r)] = r
    return recs


def reused_records():
    """P1 / standard / both rates from lpf_aliasing.json, labelled as this module's records (not copied into OUT_JSON;
    records() merges them)."""
    return [dict(r, mode="P1", setting="standard", os=8, grid_hardness=0.0, source="lpf_aliasing.json")
            for r in json.load(open(os.path.join(HERE, "lpf_aliasing.json")))["records"]]


def planned(rate, setting, cases=CASES, drives=DRIVES, tones=GRID_HZ, settle=SETTLE_S, window=WINDOW_S):
    """The measure() argument tuples of one run (before the resume and reuse filter)."""
    return [(rate, setting, m, b, d, f, window, settle, g) for m, b, g in cases for d in drives for f in tones]


def job_key(j):
    """point_key of the record a planned job produces."""
    rate, setting, m, b, d, f, window, settle, g = j
    return (m, setting, int(rate), float(b), float(d), int(f), float(g), float(settle), float(window))


def plan(args):
    """The point_keys one REPRODUCE entry's `run` arguments produce, reused points excluded (no stage runs)."""
    a = run_parser().parse_args(["run", *args])
    reused = {point_key(r) for r in reused_records()}
    cases = parse_cases(a.cases) if a.cases else CASES
    return {job_key(j) for j in planned(a.rate, a.setting, cases, a.drives, a.tones, a.settle)} - reused


def run(rate, setting, cases=CASES, drives=DRIVES, tones=GRID_HZ, workers=None, jsonl=JSONL, settle=SETTLE_S,
        window=WINDOW_S):
    """Measure every case x drive x tone not yet in `jsonl` (resume key: point_key, which includes the model, settle
    and window) and not already one of the reused lpf_aliasing.json records."""
    os.makedirs(os.path.dirname(os.path.abspath(jsonl)), exist_ok=True)
    done = set(load_jsonl([jsonl])) | {point_key(r) for r in reused_records()}
    jobs = [j for j in planned(rate, setting, cases, drives, tones, settle, window) if job_key(j) not in done]
    # most expensive first is irrelevant (all points cost the same at one setting); high tones first so the worst
    # cases arrive early
    jobs.sort(key=lambda j: (-j[5], j[2], j[3], j[4]))
    print(f"{len(jobs)} points to run ({len(done)} already in {jsonl})", flush=True)
    with ProcessPoolExecutor(workers) as ex, open(jsonl, "a") as fh:
        futs = {ex.submit(_job, j): j for j in jobs}
        for k, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(f"[{k}/{len(jobs)}] {r['mode']} {r['bias_ma']} mA (grid {r['grid_hardness']:g}) {setting} {rate} "
                  f"{r['drive']:g} V {r['tone']}: "
                  f"off {r['off']['total_dbc']:.2f} dBc, 4k abs {r['4k']['abs_db_re_off_fund']:.2f}, "
                  f"7k abs {r['7k']['abs_db_re_off_fund']:.2f} ({r['cpu_s']:.0f} CPU-s)", flush=True)


def worst(recs, key="total_dbc", position="off"):
    return max(recs, key=lambda r: r[position][key])


def summary(records):
    """Per mode / bias / setting / rate / drive and position: the worst dBc total and its tone, the worst absolute total
    (re the unfiltered fundamental) and its tone, and the worst internal / resampler / other shares."""
    groups = {}
    for r in records:
        groups.setdefault((r["mode"], float(r["bias_ma"]), hardness(r), r["setting"], int(r["fs"]), float(r["drive"])),
                          []).append(r)
    rows = []
    for (m, b, g, s, fs, d), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2],
                                                                          SETTINGS[kv[0][3]][0], -kv[0][4], kv[0][5])):
        got = {r["tone"] for r in rs}
        row = dict(mode=m, bias_ma=b, grid_hardness=g, setting=s, os=SETTINGS[s][0], fs=fs, drive=d, tones=len(rs),
                   legacy_grid=set(SWEEP_HZ) <= got, band_edge=BAND_EDGE_HZ in got, full_grid=set(GRID_HZ) <= got)
        leg = [r for r in rs if r["tone"] in SWEEP_HZ]
        if leg:
            w = worst(leg)
            row["legacy"] = dict(worst_dbc=w["off"]["total_dbc"], worst_tone=w["tone"], tones=len(leg))
        for p in lpf.POSITIONS:
            w, wa = worst(rs, position=p), worst(rs, "abs_db_re_off_fund", p)
            row[p] = dict(worst_dbc=w[p]["total_dbc"], worst_tone=w["tone"],
                          internal_dbc=w[p]["internal_dbc"], resampler_dbc=w[p]["resampler_dbc"],
                          worst_abs_db=wa[p]["abs_db_re_off_fund"], worst_abs_tone=wa["tone"],
                          max_internal_dbc=max(x[p]["internal_dbc"] for x in rs),
                          max_resampler_dbc=max(x[p]["resampler_dbc"] for x in rs),
                          max_other_dbc=max(x[p]["other_dbc"] for x in rs))
        rows.append(row)
    return rows


def records(path=OUT_JSON):
    """Every record of the assessment: OUT_JSON's plus the reused lpf_aliasing.json ones (stored once, there)."""
    return json.load(open(path))["records"] + reused_records()


def collect(jsonl_paths, scan_jsonl=None, settle_jsonl=None, model_jsonl=None):
    """Write OUT_JSON from the run JSONLs: records (the grid), band_edge_scan, settle_check, model_check (the default
    stage, grid current on, at points of the no-grid cases), each kept apart so no method stands in for another."""
    reused = {point_key(r) for r in reused_records()}
    def side(path):
        return sorted(load_jsonl([path]).values(), key=point_key) if path else []
    scan, settle, model = side(scan_jsonl), side(settle_jsonl), side(model_jsonl)
    for r in scan + settle + model:
        r.pop("cpu_s", None)
    recs = [r for r in load_jsonl(jsonl_paths).values() if point_key(r) not in reused]
    recs.sort(key=lambda r: (r["mode"], SETTINGS[r["setting"]][0], -r["fs"], r["bias_ma"], r["drive"], r["tone"]))
    for r in recs:
        r.pop("cpu_s", None)                   # machine-load dependent
    rows = summary(recs + reused_records())
    json.dump(dict(method=dict(settle_s=SETTLE_S, window_s=WINDOW_S, sweep_hz=list(SWEEP_HZ), grid_hz=list(GRID_HZ),
                               band_edge_hz=BAND_EDGE_HZ, scan_hz=list(SCAN_HZ), drives=list(DRIVES),
                               filt="fir", settings={k: list(v) for k, v in SETTINGS.items()},
                               cases=[list(c) for c in CASES], grid_hardness_default=HARDNESS,
                               positions=list(lpf.POSITIONS),
                               lpf_prewarp_hz=lpf.PREWARP_HZ, reused="P1 standard 8x: lpf_aliasing.json (#119)"),
                   summary=rows, records=recs, band_edge_scan=scan, settle_check=settle, model_check=model),
              open(OUT_JSON, "w"),
              indent=0)
    print(f"{len(recs)} records (+ {len(reused)} reused) -> {OUT_JSON}")


def tables(path=OUT_JSON):
    """Markdown for dk6as6/RESULTS.md: per rate and drive, a row per case, the worst total (dBc, filter off; tone) for
    each setting, and the worst absolute total with the 4k / 7k filter on (dB re the unfiltered fundamental)."""
    rows = json.load(open(path))["summary"]
    idx = {(r["mode"], r["bias_ma"], r["grid_hardness"], r["setting"], r["fs"], r["drive"]): r for r in rows}
    sets = [s for s in SETTINGS if s != GROUND_TRUTH and any(r["setting"] == s for r in rows)]
    out = []
    for fs in RATES:
        for d in DRIVES:
            out += [f"\n**{fs / 1000:g} kHz, {d:g} V**: worst over the {len(GRID_HZ)}-tone grid (the #6 tones and "
                    f"{BAND_EDGE_HZ} Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered "
                    "fundamental]; a tone count is shown only where the grid is not complete",
                    "| case | " + " | ".join(sets) + " |", "|---" * (len(sets) + 1) + "|"]
            for m, b, g in CASES:
                cells = []
                for s in sets:
                    r = idx.get((m, b, g, s, fs, d))
                    cov = "" if r is None or r["full_grid"] else f"; {r['tones']} of {len(GRID_HZ)} tones"
                    cells.append("—" if r is None else
                                 f"{r['off']['worst_dbc']:.1f} ({r['off']['worst_tone']}{cov}) "
                                 f"[{r['4k']['worst_abs_db']:.1f} / {r['7k']['worst_abs_db']:.1f}]")
                out.append(f"| {m} {b:g} mA{' (grid current)' if g else ''} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def verify_reuse(workers=None):
    """Remeasure six of lpf_aliasing.json's points through this module: 0.6 mA, 8 V, 10037 / 18037 / 19037 Hz, at
    each rate (48 and 44.1 kHz)."""
    old = {point_key(r): r for r in reused_records()}
    pick = [k for k in old if k[3] == 0.6 and k[4] == 8.0 and k[5] in (19037, 18037, 10037)]
    assert {k[2] for k in pick} == set(RATES) and len(pick) == 6
    with ProcessPoolExecutor(workers) as ex:
        new = list(ex.map(_job, [(k[2], k[1], k[0], k[3], k[4], k[5]) for k in pick]))
    for r in new:
        o = old[point_key(r)]
        same = all(r[p] == o[p] for p in lpf.POSITIONS)
        print(point_key(r), "bit for bit" if same else "DIFFERS", r["off"]["total_dbc"], o["off"]["total_dbc"])
        if not same:
            sys.exit(1)


# Every run behind OUT_JSON, in order, as `run` arguments with the JSONL each writes to ("grid", "scan", "settle",
# "model"): `python -m dk6as6.aliasing_quality reproduce` runs them (resumable: the resume key is point_key, which
# includes the model, settle and window) and then collects. Each method has its own JSONL, so a longer-settle or
# default-stage point never stands in for a grid point or the other way round.
_NG = ["P1:0.1", "P1:0.25", "P1:0.45", "P1:0.6", "T:0.1", "T:0.25", "T:0.3"]
_SCAN = [str(t) for t in SCAN_HZ]
REPRODUCE = (
    [("grid", ["--rate", str(fs), "--setting", st]) for fs in RATES for st in ("4x", "standard", "hq")]
    + [("grid", ["--rate", "48000", "--setting", "64x", "--cases", "P1:0.6", "T:0.3", "--tones", "16037", "19037"]),
       ("grid", ["--rate", "48000", "--setting", "64x", "--cases", "P1:0.6", "T:0.3", "--drives", "8",
                 "--tones", "19937"]),
       ("grid", ["--rate", "44100", "--setting", "64x", "--cases", "P1:0.6", "T:0.3", "--drives", "8",
                 "--tones", "19037", "19937"])]
    + [("grid", ["--rate", str(fs), "--setting", "64x", "--cases", "T:0.5", "--drives", "8", "--tones", "19037",
                 "19937"]) for fs in RATES]
    + [(j, ["--rate", str(fs), "--setting", st] + extra) for fs in RATES for st in ("4x", "standard", "hq")
       for j, extra in (("scan", ["--cases", "P1:0.6", "T:0.5", "--tones"] + _SCAN),
                        ("scan", ["--cases", "T:0.3", "--drives", "8", "--tones"] + _SCAN),
                        ("model", ["--cases", "P1:0.6:1", "T:0.3:1", "--tones", "19037", "19937"]))]
    + [("settle", ["--rate", "48000", "--setting", "4x", "--cases", "P1:0.25", "--drives", "2", "--tones", "19037",
                   "--settle", "2.5"]),
       ("settle", ["--rate", "44100", "--setting", "standard", "--cases", "P1:0.25", "--drives", "2", "--tones",
                   "8037", "--settle", "2.5"]),
       ("settle", ["--rate", "44100", "--setting", "hq", "--cases", "T:0.1", "--drives", "2", "--tones", "19037",
                   "--settle", "2.5"])])


def parse_cases(items):
    """'mode:bias[:hardness]' -> (mode, bias, hardness); without a hardness, the case's own in CASES (else 0.0)."""
    own = {(m, b): g for m, b, g in CASES}
    out = []
    for it in items:
        f = it.split(":")
        m, b = f[0], float(f[1])
        out.append((m, b, float(f[2]) if len(f) > 2 else own.get((m, b), 0.0)))
    return tuple(out)


def run_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--rate", type=int, choices=RATES, required=True)
    r.add_argument("--setting", choices=list(SETTINGS), required=True)
    r.add_argument("--cases", nargs="*", help="mode:bias[:grid hardness] (default CASES; the hardness defaults to the "
                                              "case's in CASES)")
    r.add_argument("--tones", nargs="*", type=int, default=list(GRID_HZ))
    r.add_argument("--drives", nargs="*", type=float, default=list(DRIVES))
    r.add_argument("--settle", type=float, default=SETTLE_S, help="settle before the window, s (default SETTLE_S)")
    r.add_argument("--workers", type=int, default=os.cpu_count())
    r.add_argument("--jsonl", default=JSONL)
    c = sub.add_parser("collect")
    c.add_argument("--jsonl", nargs="*", default=[JSONL])
    c.add_argument("--scan", help="the band-edge scan's JSONL (-> band_edge_scan)")
    c.add_argument("--settle-check", help="the longer-settle repeats' JSONL (-> settle_check)")
    c.add_argument("--model-check", help="the default-stage comparison's JSONL (-> model_check)")
    sub.add_parser("tables")
    rp = sub.add_parser("reproduce", help="every run behind OUT_JSON (REPRODUCE), then collect; resumable")
    rp.add_argument("--scratch", default=SCRATCH, help="directory for the four JSONLs")
    rp.add_argument("--workers", type=int, default=os.cpu_count())
    v = sub.add_parser("verify-reuse")
    v.add_argument("--workers", type=int, default=3)
    return ap


if __name__ == "__main__":
    ap = run_parser()
    a = ap.parse_args()
    if a.cmd == "run":
        cases = parse_cases(a.cases) if a.cases else CASES
        run(a.rate, a.setting, cases, drives=a.drives, tones=a.tones, workers=a.workers, jsonl=a.jsonl, settle=a.settle)
    elif a.cmd == "collect":
        collect(a.jsonl, a.scan, a.settle_check, a.model_check)
    elif a.cmd == "tables":
        print(tables())
    elif a.cmd == "reproduce":
        files = {k: os.path.join(a.scratch, f"aliasing_quality_{k}.jsonl") for k in ("grid", "scan", "settle", "model")}
        for which, args in REPRODUCE:
            print("$ python -m dk6as6.aliasing_quality run " + " ".join(args) + f" --jsonl {files[which]}", flush=True)
            subprocess.run([sys.executable, "-m", "dk6as6.aliasing_quality", "run", *args, "--jsonl", files[which],
                            "--workers", str(a.workers)], check=True, cwd=os.path.dirname(HERE))
        collect([files["grid"]], files["scan"], files["settle"], files["model"])
    else:
        verify_reuse(workers=a.workers)
