"""Host-rate validation (#74), using validate.run and aliasing.measure unchanged criteria.

From prototypes/fullstage: python -m dk.sample_rates [--backend python|cpp] [--workers 8].
Writes dk/results_sample_rates.json or cpp/results_sample_rates.json, checkpointed after each setting.
Suites must run serially. FIR only (the C++ core's filter); no CPU benchmark or 64x reference regeneration.
"""
import argparse
import json
import platform
from pathlib import Path
import subprocess

import numpy as np
import scipy

from dk import aliasing as AL, validate as V
from dk.stage import Stage, QUALITY

RATES = (44100, 48000, 88200, 96000, 176400, 192000)
REST_LIMIT_V = 1e-9  # existing #70 rest test / C++ TOL_PINNED_ABS; startup also has a 1e-6 V limit
# Experimental recommendation only: power-of-two factors nearest the original internal rates,
# bounded by the core's minimum 2x. The shipped QUALITY definitions remain fixed.
NEAR_INTERNAL = {44100: (16, 8, 4), 48000: (16, 8, 4), 88200: (8, 4, 2), 96000: (8, 4, 2),
                 176400: (4, 2, 2), 192000: (4, 2, 2)}
ROOT = Path(__file__).resolve().parent.parent


def rest(fs, quality, stage_class=Stage):
    """First 100 ms from prepare/reset; includes the startup and late rest, with the existing strict rest limit."""
    s = stage_class(fs, quality=quality)
    y = s.process(np.zeros(round(fs * 0.1)))["out"]
    return dict(duration_s=0.1, max_abs_v=float(np.max(np.abs(y))),
                last_10ms_max_abs_v=float(np.max(np.abs(y[-round(fs * 0.01):]))),
                limit_v=REST_LIMIT_V, passed=bool(np.max(np.abs(y)) < REST_LIMIT_V),
                nonconverged=s.iteration_stats()["nonconverged"])


def worst_rows(records):
    return {f"{q}/{flt}/{d:g}V": dict(worst_dbc=w, tone=f, limit_dbc=lim, margin_db=lim - w, passed=ok)
            for (q, flt, d), (w, f, lim, ok) in AL.check_limits(records).items()}


def failures(out):
    bad = []
    for fs, row in out["rates"].items():
        for q, r in row.get("settings", {}).items():
            bad += [f"{fs}/{q}/{k}" for k, ok in r["pass_vs_dz"].items() if not ok]
            if not row["rest"][q]["passed"] or row["rest"][q]["nonconverged"]:
                bad.append(f"{fs}/{q}/rest")
            if any(s["nonconverged"] for s in r["iterations"].values()):
                bad.append(f"{fs}/{q}/nonconvergence")
        bad += [f"{fs}/aliasing/{k}" for k, r in row.get("aliasing_worst", {}).items() if not r["passed"]]
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=("python", "cpp"), default="python")
    ap.add_argument("--rates", type=int, nargs="+", choices=RATES, default=list(RATES))
    ap.add_argument("--workers", type=int, default=1, help="independent Python aliasing points only")
    ap.add_argument("--near-internal", action="store_true", help="measure the unimplemented factor proposal")
    ap.add_argument("--resume", action="store_true", help="continue an interrupted run with the same code")
    args = ap.parse_args()
    stage_class = Stage
    vc = None
    meta = dict(python=platform.python_version(), numpy=np.__version__, scipy=scipy.__version__,
                platform=platform.platform(),
                backend=args.backend, filter="fir", triode_math="libm",
                quality_policy="near internal (experimental)" if args.near_internal else "fixed factor",
                criteria=V.CRIT, aliasing_limits="unchanged dk.aliasing.LIMIT_DBC (48 kHz limits at every rate)",
                method="dk.validate.run(fs=...), 100 ms rest, full dk.aliasing offset sweep; no timing benchmark; "
                       "existing spice/reference_dz.json criteria; Koren informational; no new 64x reference")
    if args.backend == "cpp":
        from cpp import validate_cpp as vc
        vc.EXE = vc.build()
        stage_class = vc.CppStage
        meta["flags"] = [*vc.CXXFLAGS, "-O2"]
        meta["compiler"] = subprocess.run([vc.compiler(), "--version"], capture_output=True,
                                           text=True, check=True).stdout.splitlines()[0]
    filename = "results_internal_rate_proposal.json" if args.near_internal else "results_sample_rates.json"
    dest = ROOT / args.backend.replace("python", "dk") / filename
    out = json.loads(dest.read_text()) if args.resume and dest.exists() else dict(method=meta, rates={})
    out["method"] = meta

    def save():
        out["failures"] = failures(out)
        dest.write_text(json.dumps(out, indent=1, default=float) + "\n")

    for fs in args.rates:
        row = out["rates"].setdefault(str(fs), dict(settings={}, rest={}, aliasing_records=[]))
        if "prewarp_minimax" not in row:
            row["prewarp_minimax"] = V.prewarp_minimax(fs=fs, factor=NEAR_INTERNAL[fs][2] if args.near_internal else 4)
        for k, q in enumerate(QUALITY):
            quality = (NEAR_INTERNAL[fs][k], QUALITY[q][1]) if args.near_internal else q
            if q not in row["settings"]:
                r = (V.run(fs=fs, quality=quality, measure_cpu=False) if vc is None
                     else vc.run(quality, fs=fs, measure_cpu=False))
                row["settings"][q] = r
                row["rest"][q] = rest(fs, quality, stage_class)
                save()
                print(args.backend, fs, q, r["pass_vs_dz"], "rest", row["rest"][q], flush=True)
            if not AL.select(row["aliasing_records"], q, "fir", AL.DRIVES[0]):
                if vc is None:
                    recs = AL.sweep([(quality, "fir")], fs=fs, workers=args.workers)
                else:
                    saved = AL.Stage
                    AL.Stage = stage_class
                    try:
                        recs = AL.sweep([(quality, "fir")], fs=fs, workers=1)
                    finally:
                        AL.Stage = saved
                # These are validation measurements, not a CPU benchmark.
                for rec in recs:
                    rec.pop("cpu_s", None)
                    rec["setting"] = q
                row["aliasing_records"].extend(recs)
                row["aliasing_worst"] = worst_rows(row["aliasing_records"])
                save()
                print(args.backend, fs, q, "aliasing", worst_rows(recs), flush=True)
    if vc is not None:
        reference = ROOT / "dk" / filename
        if reference.exists():
            py = json.loads(reference.read_text())
            out["python_comparison"] = {}
            for fs, row in out["rates"].items():
                if fs not in py["rates"]:
                    continue
                out["python_comparison"][fs] = {}
                for q, r in row["settings"].items():
                    if q in py["rates"][fs]["settings"]:
                        d = vc.compare(r, py["rates"][fs]["settings"][q])
                        out["python_comparison"][fs][q] = dict(deviation=d, failed=vc.check(d))
            out["aliasing_python_comparison"] = {}
            for fs, row in out["rates"].items():
                if fs not in py["rates"]:
                    continue
                def key(r):
                    return r["setting"], r["drive"], r["tone"]
                pr = {key(r): r for r in py["rates"][fs]["aliasing_records"]}
                cr = {key(r): r for r in row["aliasing_records"]}
                if set(pr) == set(cr):
                    diffs = [dict(setting=k[0], drive=k[1], tone=k[2],
                                  abs_total_diff_db=abs(cr[k]["total_dbc"] - pr[k]["total_dbc"])) for k in cr]
                    out["aliasing_python_comparison"][fs] = dict(points=len(diffs),
                                                               worst=max(diffs, key=lambda r: r["abs_total_diff_db"]))
    save()
    print("Failed unchanged criteria:", out["failures"], flush=True)
    return bool(out["failures"] or any(r["failed"] for row in out.get("python_comparison", {}).values()
                                       for r in row.values()))


if __name__ == "__main__":
    raise SystemExit(main())
