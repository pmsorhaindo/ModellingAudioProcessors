"""Short serial CPU comparison after C1/C5: 127 baseline, pre-review 231, shipped 215.
Run after all numerical suites: python -m dk.experiments.review_cost_issue96 --scratch ../../.scratch/review_cost
Existing bench harness, same M1 / quality / drive, 0.5 s x 3 passes, three alternating rounds.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess

from cpp import bench, build as B

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "29c4153d232708280b4b676755f981fd0938826b"
DEST = ROOT / "dk/results_issue96_review_cost.json"
VARIANTS = ("baseline127", "previous231", "shipped215")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch", type=Path, required=True)
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    assert scratch.is_relative_to(ROOT.parent.parent)

    def old(name):
        return subprocess.check_output(["git", "show", f"{SOURCE}:prototypes/fullstage/cpp/{name}"], text=True)

    exes = {}
    hashes = {}
    for name in VARIANTS:
        folder = scratch / name
        folder.mkdir(parents=True, exist_ok=True)
        header = (ROOT / "cpp/dk_core.h").read_text() if name == "shipped215" else old("dk_core.h")
        if name == "baseline127":
            assert header.count("FIR_FIRST_44100_TAPS = 231") == 1
            header = header.replace("FIR_FIRST_44100_TAPS = 231", "FIR_FIRST_44100_TAPS = 127")
        header = header.replace('#include "../dk/fastmath.h"', f'#include "{ROOT / "dk/fastmath.h"}"')
        hashes[name] = hashlib.sha256(header.encode()).hexdigest()
        (folder / "dk_core.h").write_text(header)
        source = old("bench.cpp")
        assert source.count("static constexpr double FS = 48000.0;") == 1
        (folder / "bench.cpp").write_text(source.replace("static constexpr double FS = 48000.0;",
                                                       "static constexpr double FS = 44100.0;"))
        exe = folder / "bench"
        subprocess.run([B.compiler(), *B.CXXFLAGS, "-O3", "-o", str(exe), str(folder / "bench.cpp")],
                       check=True, capture_output=True)
        exes[name] = str(exe)

    out = dict(method=dict(fs=44100, seconds_per_pass=0.5, reps=3, rounds=3, block=64, math="libm", instances=1,
                           flags=B.CXXFLAGS + ["-O3"], compiler=bench._sh([B.compiler(), "--version"]),
                           source_commit=SOURCE, source_sha256=hashes,
                           order="baseline/previous/shipped; reverse; forward",
                           note="comparative M1 experiment, no concurrent suites, not an official benchmark",
                           variants="127 and 231 use pre-review layout; shipped215 includes C3-C5 refactor"),
               host=bench.host_info(), rounds=[], summary={})
    only = ",".join(f"{q}_libm_{d}V_b64_x1" for q in ("hq", "standard", "eco") for d in (2, 8))
    for idx in range(3):
        results = {}
        for name in VARIANTS if idx % 2 == 0 else VARIANTS[::-1]:
            before = bench.host_state()
            p = subprocess.run([exes[name], "--seconds", "0.5", "--reps", "3", "--only", only],
                               capture_output=True, text=True, check=True)
            results[name] = dict(state_before=before, state_after=bench.host_state(), **json.loads(p.stdout))
        out["rounds"].append(results)
        DEST.write_text(json.dumps(out, indent=1) + "\n")
        print("round", idx + 1, flush=True)
    for name in VARIANTS:
        bykey = {}
        for run in out["rounds"]:
            for r in run[name]["results"]:
                bykey.setdefault(r["name"], []).append(r)
        out["summary"][name] = {
            key: dict(ns_per_sample=statistics.median(r["ns_per_sample"] for r in rows),
                      median_min=min(r["ns_per_sample"] for r in rows),
                      median_max=max(r["ns_per_sample"] for r in rows),
                      deterministic=all(r["deterministic"] for r in rows),
                      nonconverged=sum(r["nonconverged"] for r in rows)) for key, rows in bykey.items()}
    for rows in out["summary"].values():
        for key, r in rows.items():
            r["ratio_to_baseline"] = r["ns_per_sample"] / out["summary"]["baseline127"][key]["ns_per_sample"]
    DEST.write_text(json.dumps(out, indent=1) + "\n")


if __name__ == "__main__":
    main()
