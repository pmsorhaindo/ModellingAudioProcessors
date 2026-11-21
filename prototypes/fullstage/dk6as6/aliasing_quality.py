"""Aliasing of the 6AS6 stage at every oversampling factor (#113): plain 4x, standard 8x and hq 16x (none pre-warped),
with the #103 post-filter off and on, at 48 and 44.1 kHz, for every supported mode / reference bias, against a 64x
ground truth. The metric and sweep are dk/aliasing.py's (offset tones SWEEP_HZ, 1 s coherent periodic-Hann window, total
non-harmonic energy 20 Hz-20 kHz in dBc, split internal / resampler / other); the settle is dk6as6.aliasing.SETTLE_S.

Each point is one stage run, decimated directly ("off") and through each PostFilter position (dk6as6/lpf_aliasing.py's
measure(), which this calls; "off" is bit for bit dk6as6.aliasing.measure). Per position: total / internal / resampler /
other dBc relative to that output's own fundamental, and abs_db_re_off_fund (the total relative to the UNFILTERED
fundamental, the absolute comparison: the filter cuts the fundamental too).

Cases: CASES, every (mode, bias) of the reference biases (spice6as6/run_refs.MODE_BIASES, plus T's 0.3 mA envelope top)
inside stage.SUPPORTED_MA: P1 0.1 / 0.25 / 0.45 / 0.6 mA and T 0.1 / 0.25 / 0.3 mA. P2 is experimental (#123); its
numbers are in the resampler study (dk6as6/resampler_study.py).

Reuse: P1 at standard 8x, both rates, every position, is dk6as6/lpf_aliasing.json (#119; the same function and
arguments). It is read from there, not rerun; the verify-reuse command remeasures a few of its points and requires them
bit for bit.

Run from prototypes/fullstage (long: pure-Python Newton, ~35 / 70 / 140 / 560 CPU-s per point at 4x / 8x / 16x / 64x on
an M1; each point is appended to a JSONL file as it finishes, so a run can be stopped and resumed):
  python -m dk6as6.aliasing_quality run --rate 48000 --setting 4x [--workers N] [--jsonl PATH]
  python -m dk6as6.aliasing_quality run --rate 48000 --setting 64x --tones 10037 16037 17037 18037 19037 \
      --cases P1:0.6 T:0.3
  python -m dk6as6.aliasing_quality collect [--jsonl PATH ...]  -> dk6as6/aliasing_quality.json
"""
import argparse
import json
import os
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import SWEEP_HZ, DRIVES, WINDOW_S  # noqa: E402 -- after the sys.path setup
from dk6as6 import lpf, lpf_aliasing  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import supported, ExperimentalWarning  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import MODE_BIASES  # noqa: E402 -- after the sys.path setup

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
SCAN_HZ = (19237, 19437, 19637, 19837, 19987)
CASES = tuple((m, b) for m in ("P1", "T") for b in sorted(set(MODE_BIASES[m]) | ({0.3} if m == "T" else set()))
              if supported(m, b))
REUSED = ("P1", "standard")            # (mode, setting) read from lpf_aliasing.json
OUT_JSON = os.path.join(HERE, "aliasing_quality.json")
SCRATCH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), ".scratch", "issue113")
JSONL = os.path.join(SCRATCH, "aliasing_quality.jsonl")


def point_key(r):
    return (r["mode"], r["setting"], int(r["fs"]), float(r["bias_ma"]), float(r["drive"]), int(r["tone"]))


def measure(fs, setting, mode, bias, drive, tone, window=WINDOW_S, settle=SETTLE_S):
    t = time.process_time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        r = lpf_aliasing.measure(fs, drive, tone, bias=bias, window=window, settle=settle,
                                 quality=SETTINGS[setting], mode=mode)
    return dict(r, mode=mode, setting=setting, os=SETTINGS[setting][0], cpu_s=time.process_time() - t)


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
    return [dict(r, mode="P1", setting="standard", os=8, source="lpf_aliasing.json")
            for r in json.load(open(os.path.join(HERE, "lpf_aliasing.json")))["records"]]


def run(rate, setting, cases=CASES, drives=DRIVES, tones=SWEEP_HZ, workers=None, jsonl=JSONL, settle=SETTLE_S):
    os.makedirs(os.path.dirname(os.path.abspath(jsonl)), exist_ok=True)
    done = set(load_jsonl([jsonl]))
    jobs = [(rate, setting, m, b, d, f, WINDOW_S, settle) for m, b in cases for d in drives for f in tones
            if (m, setting, rate, float(b), float(d), int(f)) not in done]
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
            print(f"[{k}/{len(jobs)}] {r['mode']} {r['bias_ma']} mA {setting} {rate} {r['drive']:g} V {r['tone']}: "
                  f"off {r['off']['total_dbc']:.2f} dBc, 4k abs {r['4k']['abs_db_re_off_fund']:.2f}, "
                  f"7k abs {r['7k']['abs_db_re_off_fund']:.2f} ({r['cpu_s']:.0f} CPU-s)", flush=True)


def worst(recs, key="total_dbc", position="off"):
    return max(recs, key=lambda r: r[position][key])


def summary(records):
    """Per mode / bias / setting / rate / drive and position: the worst dBc total and its tone, the worst absolute total
    (re the unfiltered fundamental) and its tone, and the worst internal / resampler / other shares."""
    groups = {}
    for r in records:
        groups.setdefault((r["mode"], float(r["bias_ma"]), r["setting"], int(r["fs"]), float(r["drive"])), []).append(r)
    rows = []
    for (m, b, s, fs, d), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], SETTINGS[kv[0][2]][0],
                                                                       -kv[0][3], kv[0][4])):
        got = {r["tone"] for r in rs}
        row = dict(mode=m, bias_ma=b, setting=s, os=SETTINGS[s][0], fs=fs, drive=d, tones=len(rs),
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


def standard_resampler_shares(records):
    """{(fs, mode, bias, drive, tone): the 8x point's resampler share (filter off)} over GRID_HZ, from `records` plus
    aliasing_T.json (T at 48 kHz: its 8x full sweep, filter off, #111) where `records` has no such point."""
    out = {}
    for r in json.load(open(os.path.join(HERE, "aliasing_T.json"))):
        if r.get("fs_base", 48000) == 48000:
            out[(48000, "T", float(r["bias_ma"]), float(r["drive"]), int(r["tone"]))] = r["resampler_dbc"]
    for r in records:
        if r["setting"] == "standard":
            out[(int(r["fs"]), r["mode"], float(r["bias_ma"]), float(r["drive"]), int(r["tone"]))] = \
                r["off"]["resampler_dbc"]
    return {k: v for k, v in out.items() if k[4] in GRID_HZ}


def hq_bound(records):
    """hq (16x) is measured on a tone subset; its full-grid maximum is bounded from the 8x full grid. At 16x the total
    is the resampler's (16x equals 64x; internal <= -154.6 dBc), and it equals the 8x point's resampler share wherever
    both were measured. So per key: bound = the 8x resampler share's grid maximum + the largest excess of the hq total
    over the 8x share at any common point (all keys). Returns (excess dB, {key: dict(bound_dbc, bound_tone,
    measured_dbc, measured_tone, tones)}), key = (fs, mode, bias, drive)."""
    sh = standard_resampler_shares(records)
    hq = {(int(r["fs"]), r["mode"], float(r["bias_ma"]), float(r["drive"]), int(r["tone"])): r["off"]["total_dbc"]
          for r in records if r["setting"] == "hq" and r["tone"] in GRID_HZ}
    excess = max(v - sh[k] for k, v in hq.items() if k in sh)
    out = {}
    for key in {k[:4] for k in hq}:
        grid = {k[4]: v for k, v in sh.items() if k[:4] == key}
        meas = {k[4]: v for k, v in hq.items() if k[:4] == key}
        if set(grid) != set(GRID_HZ):
            raise ValueError(f"8x grid incomplete at {key}: {len(grid)} of {len(GRID_HZ)} tones")
        bt, mt = max(grid, key=grid.get), max(meas, key=meas.get)
        out[key] = dict(bound_dbc=grid[bt] + excess, bound_tone=bt, measured_dbc=meas[mt], measured_tone=mt,
                        tones=len(meas))
    return excess, out


def records(path=OUT_JSON):
    """Every record of the assessment: OUT_JSON's plus the reused lpf_aliasing.json ones (stored once, there)."""
    return json.load(open(path))["records"] + reused_records()


def collect(jsonl_paths, scan_jsonl=None, settle_jsonl=None):
    reused = {point_key(r) for r in reused_records()}
    scan = sorted(load_jsonl([scan_jsonl]).values(), key=point_key) if scan_jsonl else []
    settle = sorted(load_jsonl([settle_jsonl]).values(), key=point_key) if settle_jsonl else []
    for r in scan + settle:
        r.pop("cpu_s", None)
    recs = [r for r in load_jsonl(jsonl_paths).values() if point_key(r) not in reused]
    recs.sort(key=lambda r: (r["mode"], SETTINGS[r["setting"]][0], -r["fs"], r["bias_ma"], r["drive"], r["tone"]))
    for r in recs:
        r.pop("cpu_s", None)                   # machine-load dependent
    allrecs = recs + reused_records()
    excess, hb = hq_bound(allrecs)
    rows = summary(allrecs)
    for r in rows:
        b = hb.get((r["fs"], r["mode"], r["bias_ma"], r["drive"])) if r["setting"] == "hq" else None
        if b and not r["full_grid"]:
            r["off"]["grid_bound_dbc"], r["off"]["grid_bound_tone"] = b["bound_dbc"], b["bound_tone"]
    json.dump(dict(hq_bound=dict(excess_db=excess, keys=[dict(fs=k[0], mode=k[1], bias_ma=k[2], drive=k[3], **v)
                                                         for k, v in sorted(hb.items())]),
                   method=dict(settle_s=SETTLE_S, window_s=WINDOW_S, sweep_hz=list(SWEEP_HZ), grid_hz=list(GRID_HZ),
                               band_edge_hz=BAND_EDGE_HZ, scan_hz=list(SCAN_HZ), drives=list(DRIVES),
                               filt="fir", settings={k: list(v) for k, v in SETTINGS.items()},
                               cases=[list(c) for c in CASES], positions=list(lpf.POSITIONS),
                               lpf_prewarp_hz=lpf.PREWARP_HZ, reused="P1 standard 8x: lpf_aliasing.json (#119)"),
                   summary=rows, records=recs, band_edge_scan=scan,
                   settle_check=settle), open(OUT_JSON, "w"),
              indent=0)
    print(f"{len(recs)} records (+ {len(reused)} reused) -> {OUT_JSON}")


def tables(path=OUT_JSON):
    """Markdown for dk6as6/RESULTS.md: per rate and drive, a row per case, the worst total (dBc, filter off; tone) for
    each setting, and the worst absolute total with the 4k / 7k filter on (dB re the unfiltered fundamental)."""
    rows = json.load(open(path))["summary"]
    idx = {(r["mode"], r["bias_ma"], r["setting"], r["fs"], r["drive"]): r for r in rows}
    sets = [s for s in SETTINGS if s != GROUND_TRUTH and any(r["setting"] == s for r in rows)]
    out = []
    for fs in RATES:
        for d in DRIVES:
            out += [f"\n**{fs / 1000:g} kHz, {d:g} V**: worst over the {len(GRID_HZ)}-tone grid (the #6 tones and "
                    f"{BAND_EDGE_HZ} Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered "
                    "fundamental]; a tone count is shown only where the grid is not complete",
                    "| case | " + " | ".join(sets) + " |", "|---" * (len(sets) + 1) + "|"]
            for m, b in CASES:
                cells = []
                for s in sets:
                    r = idx.get((m, b, s, fs, d))
                    cov = "" if r is None or r["full_grid"] else f"; {r['tones']} of {len(GRID_HZ)} tones" + (
                        f", grid bound {r['off']['grid_bound_dbc']:.1f}" if "grid_bound_dbc" in r["off"] else "")
                    cells.append("—" if r is None else
                                 f"{r['off']['worst_dbc']:.1f} ({r['off']['worst_tone']}{cov}) "
                                 f"[{r['4k']['worst_abs_db']:.1f} / {r['7k']['worst_abs_db']:.1f}]")
                out.append(f"| {m} {b:g} mA | " + " | ".join(cells) + " |")
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


def parse_cases(items):
    out = []
    for it in items:
        m, b = it.split(":")
        out.append((m, float(b)))
    return tuple(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--rate", type=int, choices=RATES, required=True)
    r.add_argument("--setting", choices=list(SETTINGS), required=True)
    r.add_argument("--cases", nargs="*", help="mode:bias pairs (default CASES)")
    r.add_argument("--tones", nargs="*", type=int, default=list(SWEEP_HZ))
    r.add_argument("--drives", nargs="*", type=float, default=list(DRIVES))
    r.add_argument("--settle", type=float, default=SETTLE_S, help="settle before the window, s (default SETTLE_S)")
    r.add_argument("--workers", type=int, default=os.cpu_count())
    r.add_argument("--jsonl", default=JSONL)
    c = sub.add_parser("collect")
    c.add_argument("--jsonl", nargs="*", default=[JSONL])
    c.add_argument("--scan", help="the band-edge scan's JSONL (-> band_edge_scan)")
    c.add_argument("--settle-check", help="the longer-settle repeats' JSONL (-> settle_check)")
    sub.add_parser("tables")
    v = sub.add_parser("verify-reuse")
    v.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "run":
        cases = parse_cases(a.cases) if a.cases else CASES
        if (a.setting == "standard") and any(m == REUSED[0] for m, _ in cases) and not a.cases:
            cases = tuple(cs for cs in cases if cs[0] != REUSED[0])     # P1 standard is lpf_aliasing.json
        run(a.rate, a.setting, cases, drives=a.drives, tones=a.tones, workers=a.workers, jsonl=a.jsonl, settle=a.settle)
    elif a.cmd == "collect":
        collect(a.jsonl, a.scan, a.settle_check)
    elif a.cmd == "tables":
        print(tables())
    else:
        verify_reuse(workers=a.workers)
