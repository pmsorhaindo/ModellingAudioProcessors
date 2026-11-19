"""C1: full-grid first-FIR tap sweep, isolated from production selection and committed baselines.

From fullstage: python -m dk.experiments.tap_sweep_issue96 --scratch ../../.scratch/taps
The research CLI comes from PR #98's pre-review head. Only the first FIR length at 44.1 kHz varies.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

from cpp import build as B, validate_cpp as VC
from dk import aliasing as AL, resample as RS

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "dk/results_issue96_taps.json"
SOURCE = "29c4153d232708280b4b676755f981fd0938826b"
TAPS = tuple(range(127, 232, 4))


def build(scratch):
    scratch.mkdir(parents=True, exist_ok=True)
    def source(path):
        return subprocess.check_output(["git", "show", f"{SOURCE}:prototypes/fullstage/cpp/{path}"], text=True)
    header = source("dk_core.h")
    header = header.replace('#include "../dk/fastmath.h"', f'#include "{ROOT / "dk/fastmath.h"}"')
    old = "const int first = fs_base == 44100.0 ? FIR_FIRST_44100_TAPS : FIR_FIRST_TAPS;"
    assert header.count(old) == 1
    header = header.replace(old, "const int first = fs_base == 44100.0 ? experiment_first_taps : FIR_FIRST_TAPS;")
    header = header.replace("inline constexpr int FIR_MAX_TAPS", "inline int experiment_first_taps = 231;\n"
                            "inline constexpr int FIR_MAX_TAPS")
    cpp = source("test_core.cpp")
    old = 'else if (arg.rfind("fs=", 0) == 0) fs_base = std::atof(argv[i] + 3);'
    assert cpp.count(old) == 1
    cpp = cpp.replace(old, old + '''
        else if (arg.rfind("taps=", 0) == 0) {
            const int nt = std::atoi(argv[i] + 5);
            if (nt < 127 || nt > FIR_MAX_TAPS || nt % 4 != 3) return 2;
            experiment_first_taps = nt;
        }''')
    (scratch / "dk_core.h").write_text(header)
    (scratch / "test_core.cpp").write_text(cpp)
    exe = scratch / "test_core"
    subprocess.run([B.compiler(), *B.CXXFLAGS, "-O2", "-o", str(exe), str(scratch / "test_core.cpp")],
                   check=True, capture_output=True)
    return exe


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    assert scratch.is_relative_to(ROOT.parent.parent)
    exe = build(scratch)
    out = json.loads(DEST.read_text()) if args.resume else dict(
        method=dict(source_commit=SOURCE, fs=44100, filter="fir", beta=9, later_taps=23, math="libm",
                    taps=list(TAPS), tones=list(AL.SWEEP_HZ), drive=2, settle_s=AL.SETTLE_S, window_s=AL.WINDOW_S,
                    backend="C++ -O2, actual process output, existing dk.aliasing metric",
                    selection="shortest length with >= existing MARGIN_DB headroom in both full 2 V grids",
                    required_margin_db=AL.MARGIN_DB), lengths={})
    original_stage = AL.Stage
    original_first = RS.FIR_FIRST_44100
    original_rule = getattr(RS, "first_fir_taps", None)
    try:
        AL.Stage = VC.CppStage
        for nt in TAPS:
            if str(nt) in out["lengths"]:
                continue
            RS.FIR_FIRST_44100 = (nt, 9.0)
            if original_rule:
                RS.first_fir_taps = lambda fs, n=nt: n if fs == 44100 else original_rule(fs)
            wrapper = scratch / f"taps{nt}"
            wrapper.write_text(f'#!/bin/sh\nexec {shlex.quote(str(exe))} "$@" taps={nt}\n')
            wrapper.chmod(0o755)
            VC.EXE = str(wrapper)
            records = AL.sweep([("hq", "fir"), ("standard", "fir")], drives=(2.0,), fs=44100, workers=1)
            for r in records:
                r.pop("cpu_s", None)
            worst = {q: AL.worst(records, q, "fir", 2.0) for q in ("hq", "standard")}
            margin = min(AL.LIMIT_DBC[(q, "fir")][2.0] - r["total_dbc"] for q, r in worst.items())
            st = VC.CppStage(44100)
            out["lengths"][str(nt)] = dict(records=records, worst=worst, minimum_margin_db=margin,
                                           added_latency_samples=st.latency_samples - 72,
                                           eligible=margin >= AL.MARGIN_DB)
            eligible = [int(n) for n, r in out["lengths"].items() if r["eligible"]]
            out["selected_taps"] = min(eligible) if eligible else None
            DEST.write_text(json.dumps(out, indent=1) + "\n")
            print(nt, "margin", round(margin, 4), "tones", {q: r["tone"] for q, r in worst.items()}, flush=True)
    finally:
        AL.Stage, RS.FIR_FIRST_44100 = original_stage, original_first
        if original_rule:
            RS.first_fir_taps = original_rule
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
