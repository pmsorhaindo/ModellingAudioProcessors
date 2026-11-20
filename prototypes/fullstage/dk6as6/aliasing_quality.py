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
    """P1 / standard / both rates from lpf_aliasing.json, labelled as this module's records."""
    return [dict(r, mode="P1", setting="standard", os=8, source="lpf_aliasing.json")
            for r in json.load(open(os.path.join(HERE, "lpf_aliasing.json")))["records"]]


def run(rate, setting, cases=CASES, drives=DRIVES, tones=SWEEP_HZ, workers=None, jsonl=JSONL):
    os.makedirs(os.path.dirname(os.path.abspath(jsonl)), exist_ok=True)
    done = set(load_jsonl([jsonl]))
    jobs = [(rate, setting, m, b, d, f) for m, b in cases for d in drives for f in tones
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
        row = dict(mode=m, bias_ma=b, setting=s, os=SETTINGS[s][0], fs=fs, drive=d, tones=len(rs))
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


def collect(jsonl_paths):
    recs = list(load_jsonl(jsonl_paths).values())
    keys = {point_key(r) for r in recs}
    recs += [r for r in reused_records() if point_key(r) not in keys]
    recs.sort(key=lambda r: (r["mode"], SETTINGS[r["setting"]][0], -r["fs"], r["bias_ma"], r["drive"], r["tone"]))
    for r in recs:
        r.pop("cpu_s", None)                   # machine-load dependent; summarised in method.cpu_s_per_point
    json.dump(dict(method=dict(settle_s=SETTLE_S, window_s=WINDOW_S, sweep_hz=list(SWEEP_HZ), drives=list(DRIVES),
                               filt="fir", settings={k: list(v) for k, v in SETTINGS.items()},
                               cases=[list(c) for c in CASES], positions=list(lpf.POSITIONS),
                               lpf_prewarp_hz=lpf.PREWARP_HZ, reused="P1 standard 8x: lpf_aliasing.json (#119)"),
                   summary=summary(recs), records=recs), open(OUT_JSON, "w"), indent=0)
    print(f"{len(recs)} records -> {OUT_JSON}")


def verify_reuse(n=3, workers=None):
    """Remeasure n of lpf_aliasing.json's points (the high tones at 8 V, 0.6 mA, both rates) through this module."""
    old = {point_key(r): r for r in reused_records()}
    pick = [k for k in old if k[3] == 0.6 and k[4] == 8.0 and k[5] in (19037, 18037, 10037)][:n]
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
    r.add_argument("--workers", type=int, default=os.cpu_count())
    r.add_argument("--jsonl", default=JSONL)
    c = sub.add_parser("collect")
    c.add_argument("--jsonl", nargs="*", default=[JSONL])
    v = sub.add_parser("verify-reuse")
    v.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "run":
        cases = parse_cases(a.cases) if a.cases else CASES
        if (a.setting == "standard") and any(m == REUSED[0] for m, _ in cases) and not a.cases:
            cases = tuple(cs for cs in cases if cs[0] != REUSED[0])     # P1 standard is lpf_aliasing.json
        run(a.rate, a.setting, cases, drives=a.drives, tones=a.tones, workers=a.workers, jsonl=a.jsonl)
    elif a.cmd == "collect":
        collect(a.jsonl)
    else:
        verify_reuse(workers=a.workers)
