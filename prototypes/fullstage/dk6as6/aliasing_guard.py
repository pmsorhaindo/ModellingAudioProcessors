"""CI aliasing guard for the 6AS6 stage (#113), the 12AX7 guard's method (dk/aliasing_guard.py, #35 / #98) on
dk6as6.stage.Stage: a lean subset of the full sweeps (dk6as6/aliasing_quality.json), checked two ways.

1. Absolute limit: each tone's total must stay at or below its key's limit, the full sweep's worst total for that mode /
   bias / setting / drive / rate (1 s windows) plus dk.aliasing.MARGIN_DB (3 dB), rounded up to 0.1 dB, as for every
   aliasing limit in the repo. test_dk6as6_aliasing_guard.py recomputes every limit from the committed sweep.
2. Regression ratchet: each tone must not read more than dk.aliasing_guard.TOLERANCE_DB (1.5 dB) worse than its own
   committed baseline (dk6as6/aliasing_baseline.json at 48 kHz, aliasing_baseline_44100.json; format_version 2,
   values[key].tones[tone] = {total_dbc, f_hz}, #98). Reading more than 1.5 dB better passes, with a note.

Keys: "<mode>/<bias>mA/<setting>/fir/<drive>V"; settings "standard" (8x), "hq" (16x) and "4x" (plain 4x, not a
shipped setting: guarded so the eco evidence cannot drift unseen). FIR only (the plugin offers no IIR); post-filter
off (the filter's own absolute check is in test_lpf.py). The CI buffers are dk.aliasing.CI_WINDOW_S (0.1 s) after
dk6as6.aliasing.SETTLE_S (1.25 s; the #102 coupling caps), so a tone moves to the 10 Hz grid (19037 -> 19040 Hz).

Run (from prototypes/fullstage):
  python -m dk6as6.aliasing_guard [--fs 44100] [--workers N] [--json out.json]     exit status 1 on any failure
  python -m dk6as6.aliasing_guard [--fs 44100] --update-baseline --reason "..."
Writes a markdown table to $GITHUB_STEP_SUMMARY when that is set. The CI job `aliasing (6as6)` runs both rates.
"""
import argparse
import json
import os
import platform
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk import aliasing_guard as G  # noqa: E402 -- after the sys.path setup
from dk.aliasing import CI_WINDOW_S, BAND_HZ, MASK_BINS  # noqa: E402 -- after the sys.path setup
from dk6as6 import aliasing as A6  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import ExperimentalWarning  # noqa: E402 -- after the sys.path setup

QUALITY = {"4x": (4, None), "standard": "standard", "hq": "hq"}       # Stage(quality=)
OS = {"4x": 4, "standard": 8, "hq": 16}
RATES = (48000, 44100)


def key(mode, bias, setting, drive):
    return f"{mode}/{bias:g}mA/{setting}/fir/{drive:g}V"


# The CI subset per rate: (mode, bias mA, setting, drive V): (limit dBc, CI tones). Limit = the full sweep's worst over
# every tone + 3 dB, rounded up to 0.1 dB (module docstring); the tones are that sweep's worst tone (and any other
# within 3 dB of it at the same key). Worst measured (aliasing_quality.json, 1 s windows) in the comments.
CI = {
    48000: {
        ("P1", 0.6, "4x", 2.0): (-120.5, (19037, 16037)),                 # -123.60 at 19037 (19 tones)
        ("P1", 0.6, "4x", 8.0): (-56.5, (19037, 18037)),                  # -59.52 at 18037 (19 tones)
        ("P1", 0.6, "standard", 2.0): (-120.5, (19037, 16037)),           # -123.59 at 19037 (19 tones)
        ("P1", 0.6, "standard", 8.0): (-95.4, (19037,)),                  # -98.44 at 19037 (19 tones)
        ("T", 0.3, "standard", 8.0): (-116.6, (19037, 16037)),            # -119.64 at 19037 (16037-19037 Hz here;
        #                                                                   aliasing_T.json has the full sweep)
        ("P1", 0.6, "hq", 2.0): (-120.5, (19037, 16037)),                 # -123.59 at 19037 (3 tones)
        ("P1", 0.6, "hq", 8.0): (-108.5, (19037, 18037)),                 # -111.50 at 19037 (5 tones)
    },
    44100: {
        ("P1", 0.6, "4x", 2.0): (-123.3, (19037,)),                       # -126.31 at 19037 (19 tones)
        ("P1", 0.6, "4x", 8.0): (-56.6, (19037, 18037, 17037, 16037)),    # -59.63 at 17037 (19 tones)
        ("P1", 0.6, "standard", 2.0): (-123.3, (19037,)),                 # -126.32 at 19037 (19 tones)
        ("P1", 0.6, "standard", 8.0): (-87.8, (19037,)),                  # -90.83 at 19037 (19 tones)
        ("T", 0.3, "standard", 8.0): (-118.4, (19037,)),                  # -121.46 at 19037 (19 tones)
        ("P1", 0.6, "hq", 2.0): (-123.3, (19037,)),                       # -126.31 at 19037 (6 tones)
        ("P1", 0.6, "hq", 8.0): (-106.6, (19037,)),                       # -109.62 at 19037 (5 tones)
    },
}


def baseline_path(fs):
    if fs not in RATES:
        raise ValueError(f"no 6AS6 aliasing baseline at {fs} Hz")
    return os.path.join(HERE, "aliasing_baseline.json" if fs == 48000 else f"aliasing_baseline_{fs}.json")


def limits_by_key(fs):
    return {key(*k): v[0] for k, v in CI[fs].items()}


def tones_by_key(fs):
    return {key(*k): v[1] for k, v in CI[fs].items()}


def measure(fs, mode, bias, setting, drive, tone):
    t = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        r = A6.measure(drive, tone, bias=bias, quality=QUALITY[setting], window=CI_WINDOW_S, settle=A6.SETTLE_S,
                       fs=fs, mode=mode)
    return dict(r, mode=mode, setting=setting, cpu_s=time.perf_counter() - t)


def _job(a):
    return measure(*a)


def run_subset(fs, workers=None):
    jobs = [(fs, m, b, s, d, f) for (m, b, s, d), (_, tones) in CI[fs].items() for f in tones]
    jobs.sort(key=lambda j: -OS[j[3]])                 # the slow (hq) points first
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_job, jobs))
    return [_job(j) for j in jobs]


def values_by_key(records):
    """dk.aliasing_guard's per-tone representation (format_version 2) under this guard's keys."""
    out = {}
    for r in records:
        k, tone = key(r["mode"], r["bias_ma"], r["setting"], r["drive"]), str(r["tone"])
        tones = out.setdefault(k, dict(tones={}))["tones"]
        if tone in tones:
            raise ValueError(f"duplicate measurement: {k} at {tone} Hz")
        tones[tone] = dict(total_dbc=r["total_dbc"], f_hz=r["f_hz"])
    G.validate_values(out)
    return out


def method(fs):
    return dict(fs_base=fs, stage="dk6as6.stage.Stage", filt="fir", lpf="off",
                tones={key(*k): list(v[1]) for k, v in CI[fs].items()},
                settle_s=A6.SETTLE_S, window_s=CI_WINDOW_S, band_hz=BAND_HZ, mask_bins=MASK_BINS)


def main(argv=None):
    ap = argparse.ArgumentParser(description="6AS6 CI aliasing guard: absolute limits plus a per-tone ratchet (#113).")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--fs", type=int, choices=RATES, default=48000)
    ap.add_argument("--baseline", help="baseline JSON (default: the committed one for --fs)")
    ap.add_argument("--json", help="write the full results here")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--reason")
    a = ap.parse_args(argv)
    if a.update_baseline and not (a.reason and a.reason.strip()):
        ap.error("--update-baseline needs --reason \"...\" (stored in the baseline; also put it in the commit)")
    a.baseline = a.baseline or baseline_path(a.fs)
    try:
        base = G.load_baseline(a.baseline)
    except ValueError as exc:
        ap.error(str(exc))
    if base["values"] and base.get("method", {}).get("fs_base") != a.fs:
        ap.error("baseline host rate differs from --fs; each rate has its own baseline file")
    t0 = time.perf_counter()
    recs = run_subset(a.fs, a.workers)
    wall = time.perf_counter() - t0
    measured = values_by_key(recs)
    rows = G.compare(measured, base["values"], limits_by_key(a.fs), tones_by_key(a.fs))
    meta = {k: base[k] for k in ("updated", "reason") if k in base} or None
    md = G.markdown(rows, baseline_meta=meta, wall_s=wall, baseline_name=G.shown_path(a.baseline), fs=a.fs,
                    title="6AS6 aliasing guard", limit_name="dk6as6.aliasing_guard.CI", module="dk6as6.aliasing_guard")
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md)
    if a.json:
        json.dump(dict(passed=not G.failed(rows), tolerance_db=G.TOLERANCE_DB, wall_s=wall, workers=a.workers,
                       python=platform.python_version(), git_rev=G.git_rev(), method=method(a.fs), baseline=base,
                       rows=rows, measured=measured, records=recs), open(a.json, "w"), indent=1)
    if a.update_baseline:
        over = [r for r in rows if r["status"] in ("FAIL limit", "FAIL not measured")]
        if over:
            sys.exit("not updating the baseline: " + ", ".join(f"{r['key']} at {r['tone']} Hz ({r['status']})"
                                                                for r in over))
        G.write_baseline(a.baseline, measured, a.reason.strip(), a.fs, method_=method(a.fs))
        print(f"baseline written to {os.path.relpath(a.baseline)}; commit it with the reason in the message")
        return 0
    if G.failed(rows):
        print("6AS6 aliasing guard FAILED: " + ", ".join(f"{r['key']} at {r['tone']} Hz ({r['status']})"
                                                         for r in G.failed(rows)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
