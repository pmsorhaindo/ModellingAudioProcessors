"""Aliasing of the C++ 6AS6 core (#104): dk/aliasing.py's metric on cpp/dk6as6_core.h, the antialiasing decision
(oversampling alone or with ADAA; shipped or k140 resampler), and the per-tone aliasing guard (#98) at 44.1 and 48 kHz.

Metric: dk.aliasing (offset tones on coherent bins, periodic-Hann analysis, total non-harmonic energy 20 Hz-20 kHz in
dBc, split by source: "internal" = harmonics above the stage's internal Nyquist folded inside the stage, "resampler" =
in-band harmonics reaching 0-20 kHz through the half-band filters, "other" = energy on no fold, e.g. settling). The
stage starts at its DC operating point; settle dk6as6.aliasing.SETTLE_S = 1.25 s (the #102 coupling caps), then a 1 s
window (dk.aliasing.WINDOW_S). One C++ run per point (`test_core_6as6 cli`, 256-sample blocks as the plugin).

Settings: the supported envelope with the default grid current (dk6as6.stage.SUPPORTED_MA): P1 at 0.1 / 0.25 / 0.45 /
0.6 mA and T at 0.1 / 0.25 / 0.5 mA (0.5 = the top of T's envelope); standard 8x and hq 16x; 2 V and 8 V; filter off
(the post-filter only lowers absolute alias energy, dk6as6/RESULTS-lpf.md); tones SWEEP_HZ (1037 ... 19037 Hz) plus the
band edge 19937 Hz (#113 review); host rates 48 and 44.1 kHz; resampler designs SHIPPED_FIR and K140_FIR.

ADAA bound. Antiderivative antialiasing acts on the nonlinearity's own aliasing at the internal rate: at best it removes
the "internal" term entirely; it does nothing about the resampler term (in-band harmonics leaking through the
decimator and the interpolator's residual images), and its half-sample delay and droop cost accuracy instead. So
10 log10(10^(total/10) - 10^(internal/10)) is the total an ideal ADAA could reach at each point, and total minus that is
the most it could buy. `table` reports both; it is a measured upper bound, not an ADAA implementation.

Guard (CI): per rate, every setting x quality x drive above at its full sweep's worst tone, plus 19037 Hz, on the
shipped design (DEFAULT_FIR, what the core runs), with full 1 s windows. Two checks per tone, as dk.aliasing_guard:
  1. absolute: at or below the quality gate, the shipped 12AX7 stage's committed budget for the same quality and drive
     (dk.aliasing.limits_for_rate(fs), FIR; the gate dk6as6/stage.py's supported envelope uses at standard);
  2. ratchet: within dk.aliasing_guard.TOLERANCE_DB (1.5 dB) of the tone's committed baseline
     (cpp/aliasing_6as6_baseline[_44100].json, format_version 2: values[key].tones[tone] = {total_dbc, f_hz}).
Parity: the full sweep's shipped 48 kHz standard points against the committed Python sweeps of the same setting
(dk6as6/aliasing_grid[_T].json): `table` prints the worst |C++ - Python|.

Run from prototypes/fullstage:
  python -m cpp.aliasing_6as6 sweep [--workers N]          -> cpp/aliasing_6as6.json (full sweep, ~1.2k points)
  python -m cpp.aliasing_6as6 table                         the summary tables (cpp/README-6as6.md)
  python -m cpp.aliasing_6as6 guard --fs 48000|44100 [--workers N] [--json OUT]
                                    [--update-baseline --reason "..."]
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk import aliasing as AL, aliasing_guard as AG  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY  # noqa: E402 -- after the sys.path setup
from cpp import validate_cpp_6as6 as VC  # noqa: E402 -- after the sys.path setup
from cpp.export_vectors_6as6 import K140  # noqa: E402 -- after the sys.path setup

RATES = (48000, 44100)
QUALITIES = ("standard", "hq")
DRIVES = (2.0, 8.0)
FIRS = ("shipped", "k140")
SETTINGS = (("P1", 0.1), ("P1", 0.25), ("P1", 0.45), ("P1", 0.6), ("T", 0.1), ("T", 0.25), ("T", 0.5))
BAND_EDGE_HZ = 19937
TONES = tuple(AL.SWEEP_HZ) + (BAND_EDGE_HZ,)
GUARD_EXTRA_TONE = 19037
SWEEP_JSON = os.path.join(HERE, "aliasing_6as6.json")
GUARD_FIR = "shipped"            # the design the core runs (dk6as6_core.h DEFAULT_FIR)


def baseline_path(fs):
    return os.path.join(HERE, "aliasing_6as6_baseline.json" if fs == 48000 else f"aliasing_6as6_baseline_{fs}.json")


def gate(fs, quality, drive):
    """The quality gate: the shipped 12AX7 stage's committed FIR budget for the same quality and drive at this rate."""
    return AL.limits_for_rate(fs)[(quality, "fir")][drive]


def key(quality, mode, bias, drive, fir=GUARD_FIR):
    return f"{quality}/{fir}/{mode} {bias:g} mA/{drive:g}V"


def latency(fs, os_, fir):
    return Resampler(fs, os_, "fir", fir=K140[float(fs)] if fir == "k140" else None).latency_samples


def measure(fs, quality, fir, mode, bias, drive, tone, window=AL.WINDOW_S, settle=SETTLE_S):
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = AL.tone_bin(tone, n, fs)
    os_ = QUALITY[quality][0]
    L = latency(fs, os_, fir)
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    t0 = time.perf_counter()
    y, _, info = VC.cli(x, fs, os_, mode, bias, 1.0, fir=fir)
    wall = time.perf_counter() - t0
    assert info["latency_samples"] == L, (info["latency_samples"], L)
    a = AL.alias_dbc(y[n0 + L:n0 + L + n], m, fs, os_)
    st = info["stats"]
    return dict(fs=fs, quality=quality, os=os_, fir=fir, mode=mode, bias_ma=bias, drive=drive, tone=int(tone),
                f_hz=m * fs / n, window_s=window, settle_s=settle, nonconverged=st["nonconverged"],
                restarted=st["restarted"], out_of_law=st["out_of_law"], it_max=st["max"], wall_s=wall, **a)


def _init(path):
    VC.EXE = path


def _job(a):
    return measure(*a)


def run(jobs, workers):
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(VC.exe(),)) as ex:
        return list(ex.map(_job, jobs, chunksize=1))


def sweep_jobs(rates=RATES, firs=FIRS):
    return [(fs, q, fir, mode, b, d, f) for fs in rates for q in QUALITIES for fir in firs for mode, b in SETTINGS
            for d in DRIVES for f in TONES]


def adaa_floor(total_dbc, internal_dbc):
    """The total with the internal term removed (an ideal ADAA), dBc."""
    rest = 10 ** (total_dbc / 10) - 10 ** (internal_dbc / 10)
    return 10 * math.log10(rest) if rest > 0 else AL.DB_FLOOR


def summary(recs):
    """Per rate / quality / drive / design: the worst total over every supported setting and tone, its point, the worst
    internal term, the worst total with internal folding removed, and the gate."""
    rows = []
    for fs in RATES:
        for q in QUALITIES:
            for d in DRIVES:
                for fir in FIRS:
                    r = [x for x in recs if (x["fs"], x["quality"], x["drive"], x["fir"]) == (fs, q, d, fir)]
                    if not r:
                        continue
                    w = max(r, key=lambda x: x["total_dbc"])
                    wi = max(r, key=lambda x: x["internal_dbc"])
                    gain = max(x["total_dbc"] - adaa_floor(x["total_dbc"], x["internal_dbc"]) for x in r)
                    rows.append(dict(fs=fs, quality=q, drive=d, fir=fir, points=len(r), worst_dbc=w["total_dbc"],
                                     worst_at=f"{w['mode']} {w['bias_ma']:g} mA {w['tone']} Hz",
                                     worst_internal_dbc=wi["internal_dbc"],
                                     worst_internal_at=f"{wi['mode']} {wi['bias_ma']:g} mA {wi['tone']} Hz",
                                     worst_without_internal_dbc=max(adaa_floor(x["total_dbc"], x["internal_dbc"])
                                                                    for x in r),
                                     max_adaa_gain_db=gain, max_other_dbc=max(x["other_dbc"] for x in r),
                                     gate_dbc=gate(fs, q, d), margin_db=gate(fs, q, d) - w["total_dbc"],
                                     nonconverged=sum(x["nonconverged"] for x in r),
                                     out_of_law=sum(x["out_of_law"] for x in r)))
    return rows


def parity(recs):
    """|C++ - Python| total_dbc at the points the committed Python sweeps share (48 kHz, standard, shipped FIR, the
    default grid current: dk6as6/aliasing_grid.json (P1), aliasing_grid_T.json (T))."""
    py = {}
    for mode, fn in (("P1", "aliasing_grid.json"), ("T", "aliasing_grid_T.json")):
        for r in json.load(open(os.path.join(ROOT, "dk6as6", fn))):
            if r.get("setting", "standard") == "standard" and r.get("window_s") == AL.WINDOW_S:
                py[(mode, r["bias_ma"], r["drive"], r["tone"])] = r
    out = []
    for r in recs:
        k = (r["mode"], r["bias_ma"], r["drive"], r["tone"])
        if (r["fs"], r["quality"], r["fir"]) == (48000, "standard", "shipped") and k in py:
            out.append(dict(point=k, cpp=r["total_dbc"], python=py[k]["total_dbc"],
                            diff_db=r["total_dbc"] - py[k]["total_dbc"]))
    return out


def guard_tones(fs, recs=None):
    """{key: tones}: the full sweep's worst tone per setting / quality / drive (shipped design), plus 19037 Hz."""
    recs = recs if recs is not None else json.load(open(SWEEP_JSON))["records"]
    out = {}
    for q in QUALITIES:
        for mode, b in SETTINGS:
            for d in DRIVES:
                r = [x for x in recs if (x["fs"], x["quality"], x["fir"], x["mode"], x["bias_ma"], x["drive"]) ==
                     (fs, q, GUARD_FIR, mode, b, d)]
                w = max(r, key=lambda x: x["total_dbc"])["tone"]
                out[key(q, mode, b, d)] = tuple(sorted({w, GUARD_EXTRA_TONE}))
    return out


def guard_limits(fs):
    return {key(q, mode, b, d): gate(fs, q, d) for q in QUALITIES for mode, b in SETTINGS for d in DRIVES}


def guard(fs, workers, json_out=None, update=False, reason=None):
    tones = guard_tones(fs)
    jobs = []
    for q in QUALITIES:
        for mode, b in SETTINGS:
            for d in DRIVES:
                jobs += [(fs, q, GUARD_FIR, mode, b, d, f) for f in tones[key(q, mode, b, d)]]
    t0 = time.perf_counter()
    recs = run(jobs, workers)
    wall = time.perf_counter() - t0
    measured = {}
    for r in recs:
        measured.setdefault(key(r["quality"], r["mode"], r["bias_ma"], r["drive"]), dict(tones={}))["tones"][
            str(r["tone"])] = dict(total_dbc=r["total_dbc"], f_hz=r["f_hz"])
    AG.validate_values(measured)
    path = baseline_path(fs)
    base = AG.load_baseline(path)
    rows = AG.compare(measured, base["values"], guard_limits(fs), tones)
    bad_nc = [r for r in recs if r["nonconverged"] or r["out_of_law"]]
    md = AG.markdown(rows, baseline_meta={k: base[k] for k in ("updated", "reason") if k in base} or None, wall_s=wall,
                     baseline_name=AG.shown_path(path), fs=fs).replace("Aliasing guard:", "6AS6 C++ aliasing guard:")
    print(md)
    if bad_nc:
        print(f"{len(bad_nc)} points had non-converged or out-of-law samples: "
              + ", ".join(f"{r['quality']} {r['mode']} {r['bias_ma']} {r['drive']} {r['tone']}" for r in bad_nc))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md)
    if json_out:
        json.dump(dict(rows=rows, records=recs, measured=measured, wall_s=wall), open(json_out, "w"), indent=1)
    if update:
        over = [r for r in rows if r["status"] in ("FAIL limit", "FAIL not measured")]
        if over or bad_nc:
            sys.exit("not updating the baseline: " + ", ".join(f"{r['key']} at {r['tone']} Hz ({r['status']})"
                                                               for r in over))
        with open(path, "w") as fh:
            json.dump(dict(format_version=AG.FORMAT_VERSION, reason=reason, updated=time.strftime("%Y-%m-%d"),
                           git_rev=AG.git_rev(), tolerance_db=AG.TOLERANCE_DB,
                           method=dict(fs_base=fs, core="cpp/dk6as6_core.h", fir=GUARD_FIR, settle_s=SETTLE_S,
                                       window_s=AL.WINDOW_S, band_hz=AL.BAND_HZ, mask_bins=AL.MASK_BINS,
                                       grid_hardness=1.0, lpf="off", tones={k: list(v) for k, v in tones.items()}),
                           values=measured), fh, indent=1)
            fh.write("\n")
        print(f"baseline written to {os.path.relpath(path)}")
        return 0
    return 1 if AG.failed(rows) or bad_nc else 0


def table(recs):
    lines = ["| rate | quality | drive | design | worst total dBc (point) | worst internal dBc | worst without "
             "internal dBc | most an ideal ADAA buys, dB | gate dBc | margin dB |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in summary(recs):
        lines.append(f"| {r['fs'] / 1e3:g} kHz | {r['quality']} | {r['drive']:g} V | {r['fir']} | "
                     f"{r['worst_dbc']:.1f} ({r['worst_at']}) | {r['worst_internal_dbc']:.1f} | "
                     f"{r['worst_without_internal_dbc']:.1f} | {r['max_adaa_gain_db']:.2f} | {r['gate_dbc']:.1f} | "
                     f"{r['margin_db']:.1f} |")
    p = parity(recs)
    if p:
        w = max(p, key=lambda x: abs(x["diff_db"]))
        lines.append(f"\nParity with the Python sweeps (48 kHz standard shipped, {len(p)} shared points): worst "
                     f"|C++ - Python| {abs(w['diff_db']):.2g} dB at {w['point']}")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("sweep", "table", "guard"))
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--fs", type=int, choices=RATES, default=48000)
    ap.add_argument("--json")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--reason")
    a = ap.parse_args()
    if a.cmd == "sweep":
        t0 = time.perf_counter()
        recs = run(sweep_jobs(), a.workers)
        json.dump(dict(method=dict(settle_s=SETTLE_S, window_s=AL.WINDOW_S, band_hz=AL.BAND_HZ, mask_bins=AL.MASK_BINS,
                                   grid_hardness=1.0, lpf="off", settings=SETTINGS, tones=TONES, firs=FIRS,
                                   k140=K140, wall_s=time.perf_counter() - t0, core="cpp/dk6as6_core.h"),
                       summary=summary(recs), parity=parity(recs), records=recs), open(SWEEP_JSON, "w"), indent=1)
        print(table(recs))
    elif a.cmd == "table":
        print(table(json.load(open(SWEEP_JSON))["records"]))
    else:
        if a.update_baseline and not (a.reason and a.reason.strip()):
            ap.error("--update-baseline needs --reason")
        sys.exit(guard(a.fs, a.workers, a.json, a.update_baseline, a.reason))
