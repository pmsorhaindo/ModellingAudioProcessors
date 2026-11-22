"""CI aliasing guard for the 6AS6 stage (#113), the 12AX7 guard's method (dk/aliasing_guard.py, #35 / #98) on
dk6as6.stage.Stage: a lean subset of the full sweeps (dk6as6/aliasing_quality.json), checked two ways.

1. Absolute limit: each tone's total must stay at or below its key's limit, the worst total for that mode / bias /
   setting / drive / rate over the k140 grid at the key (GRID_JSON: aliasing_quality.GRID_HZ, the #6 tones and the
   band-edge tone 19937 Hz, plus SCAN_HZ, 19237-19987 Hz, plus 19957 / 19977 / 19997 Hz; 1 s windows) plus
   dk.aliasing.MARGIN_DB (3 dB), rounded up to 0.1 dB, as for every aliasing limit in the repo.
   test_dk6as6_aliasing_guard.py recomputes every limit and tone set from GRID_JSON. Since #134 the guard measures
   k140 (FIR, below). Between the measured tones the guard measures nothing, and nothing here bounds what lies there
   (dk6as6/RESULTS.md, "The band edge": resampler_study.band_edge_estimate is an estimate only).
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
from dk6as6.pentode import HARDNESS  # noqa: E402 -- after the sys.path setup

# The guard measures the production default stage (#112: g1 grid current at pentode.HARDNESS), named explicitly so a
# change of default cannot silently change what the baselines mean. The no-grid cases' limits come from no-grid sweeps,
# which the default stage reads to <= 4.4e-4 dB (aliasing_quality.json model_check).
GRID_HARDNESS = HARDNESS
# ... and its FIR design, named too (#134): k140, dk6as6.stage's default and the C++ core's DEFAULT_FIR. The design is
# part of every key, so a change of default fails the guard until the baselines are updated on purpose.
FIR = "k140"
# The k140 full grid at this guard's keys (cpp/aliasing_6as6.py guardgrid): #113's grid and band-edge scan, plus 19957 /
# 19977 / 19997 Hz, 1 s windows, measured on the C++ core (cpp/dk6as6_core.h), which reads the same as this stage to
# 1.1e-5 dB (cpp/aliasing_6as6.json parity) and to <= PARITY_DB at this guard's own CI tones (ci_window there, against
# the committed baselines: test_dk6as6_aliasing_guard.py). A Python full grid at 1 s windows costs hours per rate.
GRID_JSON = os.path.join(HERE, "aliasing_guard_k140.json")
PARITY_DB = 0.01

QUALITY = {"4x": (4, None), "standard": "standard", "hq": "hq"}       # Stage(quality=)
MAX_TONES = 3
OS = {"4x": 4, "standard": 8, "hq": 16}
RATES = (48000, 44100)


def key(mode, bias, setting, drive):
    return f"{mode}/{bias:g}mA/{setting}/{FIR}/{drive:g}V"


# The CI subset per rate: (mode, bias mA, setting, drive V): (limit dBc, CI tones). Limit = the worst over every tone
# measured at the key in GRID_JSON (#113's grid and band-edge scan, plus 19957 / 19977 / 19997 Hz; k140, the stage
# default since #134), and over the key's CI tones read in the CI window (GRID_JSON ci_window: the window's floor at
# k140 2 V), + 3 dB, rounded up to 0.1 dB (derive(), module docstring); the tones are the highest (at most)
# MAX_TONES of those within 3 dB of the worst, which bounds CI time where a key is flat across the top of the band.
# Keys: #113's (each setting x drive's worst supported case on the shipped-FIR assessment, aliasing_quality.json, and at
# standard / 8 V also the other mode's worst, so each mode keeps a key). With k140 the 44.1 kHz P1 0.6 mA 2 V keys
# guard the band edge where the shipped FIR failed the quality gate (19957 Hz and up). The C++ core's guard
# (cpp/aliasing_6as6.py, CI jobs `aliasing (6as6 C++ core, <rate> Hz)`) covers every supported setting with k140; key
# choice on a k140 assessment of this stage belongs to #113's rework. The worst measured is in the comments.
CI = {
    48000: {
        ("T", 0.5, "4x", 2.0): (-101.8, (19237, 19437, 19637)),
        #   worst -104.87 dBc at 19237 Hz over 29 tones (k140, grid current)
        ("T", 0.5, "4x", 8.0): (-40.8, (19997, 19987, 19977)),
        #   worst -43.87 dBc at 19997 Hz over 29 tones (k140, grid current)
        ("P1", 0.6, "standard", 2.0): (-146.5, (19997, 19987, 19977)),
        #   worst -162.80 dBc at 19997 Hz over 29 tones; CI window -149.56 at 19997 Hz (k140, grid current)
        ("T", 0.5, "standard", 8.0): (-60.3, (19997, 19987, 19977)),
        #   worst -63.38 dBc at 19997 Hz over 29 tones (k140, grid current)
        ("P1", 0.6, "standard", 8.0): (-93.5, (19997, 19987, 19237)),
        #   worst -96.56 dBc at 19997 Hz over 29 tones (k140, grid current)
        ("P1", 0.6, "hq", 2.0): (-146.5, (19997, 19987, 19977)),
        #   worst -162.80 dBc at 19997 Hz over 29 tones; CI window -149.56 at 19997 Hz (k140, grid current)
        ("T", 0.5, "hq", 8.0): (-80.7, (19237, 19437, 19637)),
        #   worst -83.79 dBc at 19237 Hz over 29 tones (k140, grid current)
    },
    44100: {
        ("T", 0.5, "4x", 2.0): (-97.4, (19637, 19831, 19837)),
        #   worst -100.42 dBc at 19637 Hz over 29 tones (k140, grid current)
        ("T", 0.5, "4x", 8.0): (-39.4, (19637, 19437, 19237)),
        #   worst -42.49 dBc at 19637 Hz over 29 tones (k140, grid current)
        ("P1", 0.6, "standard", 2.0): (-146.5, (19997, 19987, 19831)),
        #   worst -162.94 dBc at 19997 Hz over 29 tones; CI window -149.54 at 19997 Hz (k140, grid current)
        ("T", 0.5, "standard", 8.0): (-56.4, (19437, 19237, 19037)),
        #   worst -59.41 dBc at 19437 Hz over 29 tones; CI window -59.41 at 19437 Hz (k140, grid current)
        ("P1", 0.6, "standard", 8.0): (-87.8, (19037, 19997, 19987)),
        #   worst -90.89 dBc at 19037 Hz over 29 tones (k140, grid current)
        ("P1", 0.6, "hq", 2.0): (-146.5, (19997, 19987, 19831)),
        #   worst -162.94 dBc at 19997 Hz over 29 tones; CI window -149.54 at 19997 Hz (k140, grid current)
        ("T", 0.5, "hq", 8.0): (-77.3, (19997, 19987, 19977)),
        #   worst -80.36 dBc at 19997 Hz over 29 tones; CI window -80.36 at 19997 Hz (k140, grid current)
    },
}


def derive(points, ci_points=()):
    """#113's rule for one key, from its full-grid points [(tone, total dBc)] (1 s windows): (limit, CI tones). Tones =
    the highest (at most) MAX_TONES within 3 dB of the worst. Limit = the worst + 3 dB (dk.aliasing.MARGIN_DB), rounded
    up to 0.1 dB, where the worst also counts ci_points [(tone, total dBc)], the same key's readings at those tones in
    the guard's own CI window: with k140 at 2 V the 0.1 s window's analysis floor (about -149.6 dBc) lies above the
    1 s grid's worst (about -162.9), so a limit from the grid alone would fail on the window, not on the stage."""
    import math
    from dk.aliasing import MARGIN_DB
    w = max(t for _, t in points)
    near = sorted((p for p in points if p[1] >= w - MARGIN_DB), key=lambda p: -p[1])
    tones = tuple(f for f, _ in near[:MAX_TONES])
    w_all = max([w] + [t for f, t in ci_points if f in tones])
    return math.ceil((w_all + MARGIN_DB) * 10) / 10, tones


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
                       fs=fs, mode=mode, grid_hardness=GRID_HARDNESS, fir=FIR)
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
    return dict(fs_base=fs, stage="dk6as6.stage.Stage", grid_hardness=GRID_HARDNESS, filt="fir", fir=FIR, lpf="off",
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
