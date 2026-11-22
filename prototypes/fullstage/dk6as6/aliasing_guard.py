"""CI aliasing guard for the 6AS6 stage (#113 rework): the inaudibility criterion (dk6as6/inaudible.py) on a lean subset
of the full assessment (dk6as6/inaudible.json, dk6as6/inaudible_sweep.py), for every offered quality at 48 and 44.1 kHz.
It replaces #113's dBc guard (worst total + 3 dB against the 12AX7 stage's budget), which is not an audibility test.

Each CI point is one run of the production default stage (g1 grid current, pentode.HARDNESS; the k140 resampler,
stage.FIR; post-filter off) at a key's tone, CI_WINDOW_S (0.1 s) after dk6as6.aliasing.SETTLE_S (1.25 s; the #102
coupling caps), checked two ways:
1. Criterion: inaudible.assess passes, i.e. every alias product in 20 Hz-20 kHz is below -100 dBFS and at least 10 dB
   under the threshold of hearing at its frequency (Terhardt; 0 dBFS = the output peak = 105 dB SPL). There is no
   tolerance on this check: a product over its limit fails the job.
2. Ratchet: the point's margin (the smallest limit - level over its products, dB) may not fall more than TOLERANCE_DB
   (1.5 dB, dk.aliasing_guard's) below its committed baseline (aliasing_baseline_inaudible_<fs>.json, format
   "inaudible-v1": values[key][tone] = {margin_db, worst_hz, worst_dbfs, f_hz}). A rise of more than 1.5 dB passes,
   with a note.
In the 0.1 s window a tone moves to the 10 Hz grid (19637 -> 19640 Hz) and lines less than 10 Hz apart share a bin and
read as their sum, so a CI point reads at or above the 1 s grid's level for the same products.

Keys (derive(), from inaudible.json's C++ screen, re-measured on this stage where they decide: RESULTS.md): per rate,
quality and drive, the supported case (stage.SUPPORTED_MA, every measured bias inside it) with the smallest margin in
each mode, and each mode's top supported bias (where the grid current and the least cathode feedback are). Tones: the
key's lowest-margin tone on the 1 s grid, and the next ones within dk.aliasing.MARGIN_DB of it on distinct CI-window
bins, up to MAX_TONES[quality] (CI time: the Python stage costs ~13 CPU-s per point and unit of oversampling factor on
a CI runner). Between the measured tones the guard measures nothing.

Run (from prototypes/fullstage):
  python -m dk6as6.aliasing_guard --fs 48000 --quality standard [--workers N] [--json out.json]   exit 1 on a failure
  python -m dk6as6.aliasing_guard --fs 48000 --quality standard --update-baseline --reason "..."
  python -m dk6as6.aliasing_guard derive         print CI as derive() computes it from inaudible.json
Writes a markdown table to $GITHUB_STEP_SUMMARY when that is set. CI: the jobs `aliasing (6as6, <quality>, <rate> Hz)`.
"""
import argparse
import datetime
import json
import os
import platform
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import CI_WINDOW_S, BAND_HZ, MASK_BINS, MARGIN_DB, tone_bin  # noqa: E402 -- after the sys.path setup
from dk.aliasing_guard import TOLERANCE_DB, git_rev, shown_path  # noqa: E402 -- after the sys.path setup
from dk6as6 import inaudible as IN, inaudible_sweep as SW  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.pentode import HARDNESS  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY, FIR, supported  # noqa: E402 -- after the sys.path setup

# What the baselines mean, named so that a change of default fails until they are updated on purpose
GRID_HARDNESS = HARDNESS
DESIGN = FIR                     # "k140"
FORMAT = "inaudible-v1"
RATES = IN.RATES
MAX_TONES = {"standard": 2, "hq": 1}

# CI[fs][quality] = {(mode, bias mA, drive V): tones}: derive()'s output, checked by test_dk6as6_aliasing_guard.py
CI = {
    48000: {
        "standard": {
            ("P1", 0.6, 2.0): (19997, 19977),
            ("P1", 0.6, 8.0): (18037,),
            ("T", 0.35, 2.0): (19997, 19977),
            ("T", 0.35, 8.0): (19637, 19831),
        },
        "hq": {
            ("P1", 0.6, 2.0): (19997,),
            ("P1", 0.6, 8.0): (18037,),
            ("T", 0.3, 8.0): (19997,),
            ("T", 0.35, 2.0): (19997,),
            ("T", 0.35, 8.0): (19997,),
        },
    },
    44100: {
        "standard": {
            ("P1", 0.6, 2.0): (19997, 19987),
            ("P1", 0.6, 8.0): (19997, 19987),
            ("T", 0.35, 2.0): (19997, 19987),
            ("T", 0.35, 8.0): (19997, 19987),
        },
        "hq": {
            ("P1", 0.1, 8.0): (19997,),
            ("P1", 0.6, 2.0): (19997,),
            ("P1", 0.6, 8.0): (16037,),
            ("T", 0.3, 8.0): (19997,),
            ("T", 0.35, 2.0): (19997,),
            ("T", 0.35, 8.0): (19997,),
        },
    },
}


def key(mode, bias, quality, drive):
    return f"{mode}/{bias:g}mA/{quality}/{DESIGN}/{drive:g}V"


def os_of(quality):
    return QUALITY[quality][0]


def derive(summary_records, fs, quality):
    """{(mode, bias, drive): tones} for one rate and quality from the 1 s grid records (inaudible.json records_cpp at
    the quality's factor): module docstring."""
    f = os_of(quality)
    recs = [r for r in summary_records if r["fs"] == fs and r["os"] == f and supported(r["mode"], r["bias_ma"])]
    by = {}
    for r in recs:
        by.setdefault((r["mode"], float(r["bias_ma"]), float(r["drive"])), []).append(r)
    keys = set()
    for d in IN.DRIVES:
        for mode in sorted({k[0] for k in by}):
            cand = [k for k in by if k[0] == mode and k[2] == d]
            keys.add(min(cand, key=lambda k: min(r["margin_db"] for r in by[k])))       # the mode's worst case
            keys.add(max(cand, key=lambda k: k[1]))                                      # and its top bias
    n = round(CI_WINDOW_S * fs)
    out = {}
    for k in sorted(keys):
        rs = sorted(by[k], key=lambda r: (r["margin_db"], -r["tone"]))
        w, tones, bins = rs[0]["margin_db"], [], set()
        for r in rs:                    # distinct CI-window bins only (19997 and 19987 Hz share one in 0.1 s)
            b = tone_bin(int(r["tone"]), n, fs)
            if r["margin_db"] <= w + MARGIN_DB and b not in bins and len(tones) < MAX_TONES[quality]:
                tones.append(int(r["tone"]))
                bins.add(b)
        out[k] = tuple(tones)
    return out


def baseline_path(fs):
    if fs not in RATES:
        raise ValueError(f"no 6AS6 aliasing baseline at {fs} Hz")
    return os.path.join(HERE, f"aliasing_baseline_inaudible_{fs}.json")


def method(fs, quality):
    return dict(fs_base=fs, quality=quality, os=os_of(quality), stage="dk6as6.stage.Stage", grid_hardness=GRID_HARDNESS,
                fir=DESIGN, lpf="off", settle_s=SETTLE_S, window_s=CI_WINDOW_S, band_hz=list(BAND_HZ),
                mask_bins=MASK_BINS, floor_dbfs=IN.FLOOR_DBFS, under_threshold_db=IN.UNDER_THRESHOLD_DB,
                spl_fs_db=IN.SPL_FS_DB, tones={key(m, b, quality, d): list(t) for (m, b, d), t in
                                               CI[fs][quality].items()})


def _job(a):
    fs, quality, mode, bias, drive, tone = a
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = SW.measure("python", fs, os_of(quality), mode, bias, drive, tone, window=CI_WINDOW_S, settle=SETTLE_S)
    return dict(r, quality=quality, key=key(mode, bias, quality, drive))


def run_subset(fs, quality, workers=None):
    jobs = [(fs, quality, m, b, d, t) for (m, b, d), tones in CI[fs][quality].items() for t in tones]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_job, jobs))
    return [_job(j) for j in jobs]


def load_baseline(path):
    if not os.path.exists(path):
        return dict(format=FORMAT, values={})
    b = json.load(open(path))
    if b.get("format") != FORMAT:
        raise ValueError(f"{path}: format {b.get('format')!r}, expected {FORMAT!r}")
    return b


def compare(records, base, tol=TOLERANCE_DB):
    """One row per CI point: criterion (passed / margin) and ratchet (delta = margin - baseline margin)."""
    rows = []
    for r in records:
        b = base.get("values", {}).get(r["key"], {}).get(str(r["tone"]))
        d = None if b is None else r["margin_db"] - b["margin_db"]
        if not r["passed"]:
            st = "FAIL criterion"
        elif r["nonconverged"]:
            st = "FAIL Newton"
        elif b is None:
            st = "no baseline"
        elif d < -tol:
            st = "FAIL ratchet"
        else:
            st = "ok" if d <= tol else "ok (better: update the baseline?)"
        rows.append(dict(key=r["key"], tone=r["tone"], f_hz=r["f_hz"], margin_db=r["margin_db"],
                         worst_hz=r["worst_hz"], worst_dbfs=r["worst_dbfs"], worst_limit_dbfs=r["worst_limit_dbfs"],
                         worst_source=r.get("worst_source"), baseline_margin_db=None if b is None else b["margin_db"],
                         delta_db=d, status=st))
    measured = {(r["key"], str(r["tone"])) for r in records}
    for k, tones in base.get("values", {}).items():
        for t in tones:
            if (k, t) not in measured:
                rows.append(dict(key=k, tone=int(t), status="FAIL not measured"))
    return rows


def failed(rows, need_baseline=True):
    bad = ("FAIL",) + (("no baseline",) if need_baseline else ())
    return [r for r in rows if r["status"].startswith(bad)]


def markdown(rows, fs, quality, wall_s, base_name):
    out = [f"### 6AS6 aliasing guard (inaudibility criterion), {quality}, {fs} Hz", "",
           f"Every alias product below {IN.FLOOR_DBFS:g} dBFS and {IN.UNDER_THRESHOLD_DB:g} dB under the threshold of "
           f"hearing ({IN.CALIBRATION}); ratchet {TOLERANCE_DB} dB on the margin against `{base_name}`. "
           f"Wall {wall_s:.0f} s.", "",
           "| key | tone Hz | margin dB | worst product Hz | level dBFS | limit dBFS | source | baseline dB | delta dB "
           "| status |", "|---|---|---|---|---|---|---|---|---|---|"]

    def f(v, fmt):
        return "" if v is None else format(v, fmt)
    for r in rows:
        out.append(f"| {r['key']} | {r['tone']} | {f(r.get('margin_db'), '+.2f')} | {f(r.get('worst_hz'), '.0f')} | "
                   f"{f(r.get('worst_dbfs'), '.1f')} | {f(r.get('worst_limit_dbfs'), '.1f')} | "
                   f"{r.get('worst_source') or ''} | {f(r.get('baseline_margin_db'), '+.2f')} | "
                   f"{f(r.get('delta_db'), '+.2f')} | {r['status']} |")
    return "\n".join(out) + "\n"


def write_baseline(path, records, reason, fs, quality):
    b = load_baseline(path)
    vals = b.get("values", {})
    for k in [k for k in vals if f"/{quality}/" in k]:
        del vals[k]
    for r in records:
        vals.setdefault(r["key"], {})[str(r["tone"])] = dict(margin_db=r["margin_db"], worst_hz=r["worst_hz"],
                                                            worst_dbfs=r["worst_dbfs"], f_hz=r["f_hz"])
    meth = b.get("method", {})
    meth[quality] = method(fs, quality)
    hist = b.get("history", []) + [dict(updated=datetime.date.today().isoformat(), quality=quality,
                                        reason=reason, git_rev=git_rev(), python=platform.python_version())]
    json.dump(dict(format=FORMAT, tolerance_db=TOLERANCE_DB, method=meth, history=hist,
                   values=dict(sorted(vals.items()))), open(path, "w"), indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description="6AS6 CI aliasing guard: the inaudibility criterion (#113 rework).")
    ap.add_argument("cmd", nargs="?", choices=("check", "derive"), default="check")
    ap.add_argument("--fs", type=int, choices=RATES, default=48000)
    ap.add_argument("--quality", choices=sorted(QUALITY), default="standard")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--baseline")
    ap.add_argument("--json")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--reason")
    a = ap.parse_args(argv)
    if a.cmd == "derive":
        recs = json.load(open(SW.OUT_JSON))["records_cpp"]
        print("CI = {")
        for fs in RATES:
            print(f"    {fs}: {{")
            for q in sorted(QUALITY, key=os_of):
                print(f"        {q!r}: {{")
                for k, t in derive(recs, fs, q).items():
                    print(f"            {k!r}: {t!r},")
                print("        },")
            print("    },")
        print("}")
        return 0
    if a.update_baseline and not (a.reason and a.reason.strip()):
        ap.error("--update-baseline needs --reason \"...\"")
    path = a.baseline or baseline_path(a.fs)
    base = load_baseline(path)
    t0 = time.perf_counter()
    recs = run_subset(a.fs, a.quality, a.workers)
    wall = time.perf_counter() - t0
    vals = {k: v for k, v in base.get("values", {}).items() if f"/{a.quality}/" in k}
    rows = compare(recs, dict(base, values=vals))
    md = markdown(rows, a.fs, a.quality, wall, shown_path(path))
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md)
    if a.json:
        json.dump(dict(passed=not failed(rows), wall_s=wall, workers=a.workers, python=platform.python_version(),
                       git_rev=git_rev(), method=method(a.fs, a.quality), rows=rows, records=recs),
                  open(a.json, "w"), indent=1)
    if a.update_baseline:
        bad = [r for r in rows if r["status"] in ("FAIL criterion", "FAIL Newton")]
        if bad:
            sys.exit("not updating the baseline: " + ", ".join(f"{r['key']} at {r['tone']} Hz ({r['status']})"
                                                                for r in bad))
        write_baseline(path, recs, a.reason.strip(), a.fs, a.quality)
        print(f"baseline written to {shown_path(path)}")
        return 0
    bad = failed(rows)
    if bad:
        print("6AS6 aliasing guard FAILED: " + ", ".join(f"{r['key']} at {r['tone']} Hz ({r['status']})"
                                                         for r in bad), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
