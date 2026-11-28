"""#135: the evidence for the C++ 6AS6 core's fast valve law (LawMath::fast), the lazier Jacobian and the two-lane
kernel, next to their speed (cpp/RESULTS-cpu-6as6.md).

  domain     the arguments FastLaw's exp / log1p / pow / atan / sq receive (every Newton trial and line-search point,
             the DC solves excluded: they are libm) on dk6as6.validate's signals, all 44 settings, standard and hq
             (cpp/validate_cpp_6as6.py --math fast with the research probes), as #18's dk/fastmath_domain.py did for the
             triode; and, with fast maths (the lazier Jacobian's path: the libm path is #104's, eager), every
             converging 3-current trial whose Jacobian the lazier Jacobian skips and whether that Jacobian would have
             been finite (solver._resid3_safe's contract). Plus the same probes on the deviation signals below.
             -> cpp/fastlaw_domain_6as6.json
  deviation  the fast path's output against the libm path's on the same input, mono and on the two-lane kernel (vector
             lane maths): sines at 0.05 / 0.5 / 2 / 8 V peak and 100 Hz / 1 kHz / 15 kHz, every supported setting with
             the default grid current and P1 0.45 mA without it, standard and hq, 48 kHz, after the 1.25 s settle; per
             point max |y_fast - y_libm| and its RMS, relative to the libm output's fundamental (dBc, peak / RMS), and
             the largest line of the difference's spectrum (dBc). -> cpp/results_fastlaw_6as6.json
Run from prototypes/fullstage:  python -m cpp.fastlaw_6as6 domain|deviation [--workers N]
"""
import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY  # noqa: E402 -- after the sys.path setup
from cpp import validate_cpp_6as6 as VC  # noqa: E402 -- after the sys.path setup

DOMAIN_JSON = os.path.join(HERE, "fastlaw_domain_6as6.json")
DEVIATION_JSON = os.path.join(HERE, "results_fastlaw_6as6.json")
FS = 48000.0
DRIVES = (0.05, 0.5, 2.0, 8.0)
TONES = (100.0, 1000.0, 15000.0)
SETTINGS = (("P1", 0.1, 1.0), ("P1", 0.25, 1.0), ("P1", 0.45, 1.0), ("P1", 0.6, 1.0), ("T", 0.1, 1.0), ("T", 0.25, 1.0),
            ("T", 0.5, 1.0), ("P1", 0.45, 0.0))
WINDOW_S = 0.5


def _merge(acc, probes):
    for p in probes:
        acc["skipped_jacobians"] += p["skipped_jacobians"]
        acc["skipped_nonfinite"] += p["skipped_nonfinite"]
        for r in p["ranges"]:
            k = f"{r['fn']}" + (f"_y{r['y']!r}" if r["fn"] in ("pow", "pow_ylog") else "")
            lo, hi, n = acc["ranges"].get(k, (math.inf, -math.inf, 0))
            acc["ranges"][k] = (min(lo, r["min"]), max(hi, r["max"]), n + r["calls"])


def _point(job):
    """One deviation point: libm mono, fast mono, fast stereo (vector), each with the probes on."""
    mode, bias, h, q, drive, tone = job
    os_ = QUALITY[q][0]
    n0, n = int(round(SETTLE_S * FS)), int(round(WINDOW_S * FS))
    m = int(round(tone * n / FS))                      # a coherent bin
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n) / n)
    runs = {}
    probes = {}
    for tag, kw in (("libm", dict()), ("fast", dict(math="fast")), ("fast_stereo", dict(math="fast", stereo=True))):
        y, _, info = VC.cli(x, FS, os_, mode, bias, h, fir="default", probe=True, **kw)
        runs[tag] = (y[n0:], info["stats"])
        probes[tag] = info["probe"]
    yl = runs["libm"][0]
    spec = np.abs(np.fft.rfft(yl - yl.mean())) * 2 / n
    a1 = spec[m]
    row = dict(mode=mode, bias_ma=bias, grid_hardness=h, quality=q, drive=drive, tone_hz=m * FS / n, fund_v=float(a1))
    for tag in ("fast", "fast_stereo"):
        d = runs[tag][0] - yl
        ds = np.abs(np.fft.rfft(d)) * 2 / n
        peak = max(np.max(np.abs(d)), 1e-300)
        row[tag] = dict(max_abs_v=float(np.max(np.abs(d))), peak_dbc=float(20 * np.log10(peak / a1)),
                        rms_dbc=float(20 * np.log10(max(np.sqrt(np.mean(d * d)) * math.sqrt(2), 1e-300) / a1)),
                        line_dbc=float(20 * np.log10(max(np.max(ds), 1e-300) / a1)),
                        iterations=runs[tag][1]["iterations"], iterations_libm=runs["libm"][1]["iterations"],
                        nonconverged=runs[tag][1]["nonconverged"])
    return row, probes


def deviation(workers):
    jobs = [(mo, b, h, q, d, t) for mo, b, h in SETTINGS for q in QUALITY for d in DRIVES for t in TONES]
    VC.exe()
    with ProcessPoolExecutor(workers, initializer=VC._init, initargs=(VC.exe(), VC.PARITY_FIR)) as ex:
        res = list(ex.map(_point, jobs))
    rows = [r for r, _ in res]
    worst = {tag: {k: max(r[tag][k] for r in rows) for k in ("max_abs_v", "peak_dbc", "rms_dbc", "line_dbc")}
             for tag in ("fast", "fast_stereo")}
    out = dict(method=__doc__.strip().splitlines()[8:13], settle_s=SETTLE_S, window_s=WINDOW_S, fs=FS, worst=worst,
               rows=rows)
    json.dump(out, open(DEVIATION_JSON, "w"), indent=1)
    print(json.dumps(worst, indent=1))
    return out, [p for _, p in res]


def domain(workers):
    acc = {m: dict(skipped_jacobians=0, skipped_nonfinite=0, ranges={}) for m in ("libm", "fast")}
    per = {}
    for math_ in ("libm", "fast"):
        out, _ = VC.main(workers=workers, write=False, math=math_, probe=True)
        for r in out["rows"]:
            _merge(acc[math_], r["probes"])
        per[math_] = dict(rows=len(out["rows"]), failed=sum(bool(r["failed"]) for r in out["rows"]))
    dev, probes = deviation(workers)
    for p in probes:
        _merge(acc["libm"], [p["libm"]])
        _merge(acc["fast"], [p["fast"]])
    rec = dict(method=__doc__.strip().splitlines()[2:8], validation=per, deviation_points=len(dev["rows"]),
               lazy_jacobian={m: {k: a[k] for k in ("skipped_jacobians", "skipped_nonfinite")} for m, a in acc.items()},
               ranges={k: dict(min=v[0], max=v[1], calls=v[2]) for k, v in sorted(acc["fast"]["ranges"].items())})
    json.dump(rec, open(DOMAIN_JSON, "w"), indent=1)
    print(json.dumps(rec["lazy_jacobian"], indent=1))
    print(json.dumps(rec["ranges"], indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("domain", "deviation"))
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    domain(a.workers) if a.cmd == "domain" else deviation(a.workers)
