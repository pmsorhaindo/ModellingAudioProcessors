"""Serial verification of the shipped #96 policy. Run from prototypes/fullstage:
python -m dk.experiments.verify_issue96 --workers 8 --scratch ../../.scratch/issue96

Writes dk/results_issue96_shipped.json only. Does not regenerate #95 results, reference fits or baselines.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np
import scipy

from cpp import validate_cpp as VC
from dk import aliasing as AL, validate as V
from dk.sample_rates import rest
from dk.stage import QUALITY

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "dk/results_issue96_shipped.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--ci-only", action="store_true", help="retain full/reference measurements, recheck short tones")
    args = ap.parse_args()
    out = dict(method=dict(fs=44100, quality="fixed 16/8/4; eco prewarp 17300 Hz", filter="fir", triode_math="libm",
                           python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                           platform=platform.platform(), workers=args.workers,
                           full_window_s=AL.WINDOW_S, full_settle_s=AL.SETTLE_S,
                           ci_window_s=AL.CI_WINDOW_S, ci_settle_s=AL.CI_SETTLE_S,
                           source_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in (ROOT / "dk/resample.py", ROOT / "cpp/dk_core.h",
                                                    ROOT / "dk/aliasing.py")}),
               reference={}, sweeps={}, failures=[])
    if args.ci_only:
        out = json.loads(DEST.read_text())
    out["method"]["ci_tones"] = {f"{q}/{f}": {str(d): list(t) for d, t in tones.items()}
                                  for (q, f), tones in AL.CI_TONES_44100.items()}
    out["method"]["ci_policy_sha256"] = hashlib.sha256((ROOT / "dk/aliasing.py").read_bytes()).hexdigest()

    def save():
        DEST.write_text(json.dumps(out, indent=1, default=float) + "\n")

    VC.EXE = VC.build()
    for q in (() if args.ci_only else QUALITY):
        py = V.run(quality=q, fs=44100)
        cpp = VC.run(q, fs=44100)
        diff = VC.compare(cpp, py)
        out["reference"][q] = dict(python=py, cpp=cpp, comparison=diff, failed=VC.check(diff),
                                    rest_python=rest(44100, q), rest_cpp=rest(44100, q, VC.CppStage))
        if VC.check(diff) or not all(py["pass_vs_dz"].values()):
            out["failures"].append(f"reference/{q}")
        for backend in ("python", "cpp"):
            r = out["reference"][q][f"rest_{backend}"]
            if not r["passed"] or r["nonconverged"]:
                out["failures"].append(f"rest/{backend}/{q}")
        save()
        print(q, "reference", py["pass_vs_dz"], cpp["pass_vs_dz"], "parity", VC.check(diff), flush=True)

    jobs = (("cpp", True),) if args.ci_only else (("python", False), ("cpp", False), ("cpp", True))
    for backend, short in jobs:
        saved = AL.Stage
        if backend == "cpp":
            AL.Stage = VC.CppStage
        t0 = time.perf_counter()
        try:
            recs = AL.sweep([(q, "fir") for q in QUALITY], fs=44100,
                            workers=args.workers if backend == "python" else 1,
                            window=AL.CI_WINDOW_S if short else AL.WINDOW_S,
                            settle=AL.CI_SETTLE_S if short else AL.SETTLE_S)
        finally:
            AL.Stage = saved
        for r in recs:
            r.pop("cpu_s", None)
        checks = AL.check_limits(recs)
        rows = {f"{q}/{d:g}V": dict(worst_dbc=w, tone=f, limit_dbc=lim, margin_db=lim-w, passed=ok,
                                    ci_covers_worst=f in AL.CI_TONES_44100[(q, flt)][d])
                for (q, flt, d), (w, f, lim, ok) in checks.items()}
        label = backend + ("_short" if short else "_full")
        out["sweeps"][label] = dict(records=recs, worst=rows, wall_s=time.perf_counter()-t0)
        for k, r in rows.items():
            if not r["passed"] or not r["ci_covers_worst"]:
                out["failures"].append(f"{label}/{k}")
        save()
        print(label, rows, flush=True)
    out["failures"] = [f for f in out["failures"] if not f.startswith(("python_full/", "cpp_full/", "cpp_short/"))]
    for label, sweep in out["sweeps"].items():
        for key, r in sweep["worst"].items():
            q, d = key.split("/")
            r["ci_covers_worst"] = r["tone"] in AL.CI_TONES_44100[(q, "fir")][float(d[:-1])]
            if not r["passed"] or not r["ci_covers_worst"]:
                out["failures"].append(f"{label}/{key}")
    pr, cr = (out["sweeps"][s]["records"] for s in ("python_full", "cpp_full"))
    assert [(r["setting"], r["drive"], r["tone"]) for r in pr] == [
        (r["setting"], r["drive"], r["tone"]) for r in cr]
    out["aliasing_max_python_cpp_diff_db"] = max(abs(p["total_dbc"]-c["total_dbc"]) for p, c in zip(pr, cr))
    # Same machine/build flags, pinned #95 binary vs current core, two math paths and all settings at 48 kHz.
    old = args.scratch.resolve() / "baseline/test_core"
    out["unchanged_48000"] = []
    for q, (os_, pw) in QUALITY.items():
        for math in ("libm", "fast"):
            t = np.arange(8192) / 48000
            x = 8 * np.sin(2*np.pi*1037*t) + .1*np.sin(2*np.pi*19037*t)
            a, _, _ = VC.cli(x, os_, pw, math=math, exe=str(old), fs=48000)
            b, _, _ = VC.cli(x, os_, pw, math=math, fs=48000)
            equal = bool(np.array_equal(a, b))
            out["unchanged_48000"].append(dict(quality=q, math=math, samples=len(x), bit_equal=equal,
                                               max_abs_diff_v=float(np.max(np.abs(a-b)))))
            if not equal:
                out["failures"].append(f"48000/{q}/{math}")
    save()
    print("failures", out["failures"], flush=True)
    return bool(out["failures"])


if __name__ == "__main__":
    raise SystemExit(main())
