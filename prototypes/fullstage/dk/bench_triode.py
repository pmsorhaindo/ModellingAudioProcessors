"""#16: per-call cost of dk.triode.currents() before (gold copy of the pre-#16 formulas, kept in
dk/tests/test_triode.py) and after (shared exp, divided powers). Writes dk/bench_triode.json.
- python: the Python functions, scalar calls with Python floats (interpreter overhead dominates);
- c: dk/bench_triode.c, a C port of both forms on the same grid, double and float (the libm cost; skipped
  if gcc is missing). Built in a temporary directory, nothing is left behind.
Run from prototypes/fullstage:  python -m dk.bench_triode
Timings depend on the machine and load; RESULTS.md quotes one run.
"""
import json, os, platform, shutil, statistics, subprocess, sys, tempfile, time
import numpy as np
from dk.triode import currents, currents_only
from dk.tests.test_triode import currents_ref, currents_only_ref

HERE = os.path.dirname(os.path.abspath(__file__))
# Operating points the stage visits: grid -4..+1 V, plate 50..350 V (30 x 31 = 930 points, Python floats).
PTS = [(g, a) for g in np.linspace(-4.0, 1.0, 30).tolist() for a in np.linspace(50.0, 350.0, 31).tolist()]
WARMUP, REPEAT, PASSES = 3, 41, 20     # REPEAT timed blocks per case, each = PASSES passes over PTS


def _block(fn):
    t0 = time.perf_counter_ns()
    for _ in range(PASSES):
        for g, a in PTS:
            fn(g, a)
    return (time.perf_counter_ns() - t0) / (PASSES * len(PTS))


def _empty(g, a):
    return None


CFLAGS = ["-O2", "-fno-builtin-exp", "-fno-builtin-log1p", "-fno-builtin-pow"]


def bench_c():
    if shutil.which("gcc") is None:
        return None
    out = dict(gcc=subprocess.run(["gcc", "--version"], capture_output=True, text=True).stdout.splitlines()[0])
    with tempfile.TemporaryDirectory() as d:
        for name, extra in (("double", []), ("float", ["-DR=float", "-DR_IS_FLOAT=1"])):
            exe = os.path.join(d, name)
            cmd = ["gcc", *CFLAGS, *extra, "-o", exe, os.path.join(HERE, "bench_triode.c"), "-lm"]
            subprocess.run(cmd, check=True)
            r = json.loads(subprocess.run([exe], check=True, capture_output=True, text=True).stdout)
            r["build"] = " ".join(["gcc", *CFLAGS, *extra, "dk/bench_triode.c", "-lm"])
            r["speedup"] = r["before_ns_per_call_median"] / r["after_ns_per_call_median"]
            out[name] = r
    return out


CASES = dict(currents_before=currents_ref, currents_after=currents,
             currents_only_before=currents_only_ref, currents_only_after=currents_only, call_overhead=_empty)

if __name__ == "__main__":
    for fn in CASES.values():
        for _ in range(WARMUP):
            _block(fn)
    t = {k: [] for k in CASES}
    for _ in range(REPEAT):                   # interleaved, so drift and load hit every case alike
        for k, fn in CASES.items():
            t[k].append(_block(fn))
    res = {k: dict(ns_per_call_median=statistics.median(v), ns_per_call_min=min(v)) for k, v in t.items()}
    ov = res["call_overhead"]["ns_per_call_median"]
    ratio = lambda a, b, sub=0.0: (res[a]["ns_per_call_median"] - sub) / (res[b]["ns_per_call_median"] - sub)
    out = dict(
        method_python=f"Scalar Python calls with Python floats on {len(PTS)} operating points (Vgk -4..+1 V x 30, Vak "
               f"50-350 V x 31). Each timed block = {PASSES} passes over the points, divided by the call count; "
               f"{WARMUP} warm-up blocks per case, then {REPEAT} blocks per case interleaved across cases; median "
               "(and min) block reported. time.perf_counter_ns, single thread. call_overhead = the same loop "
               "calling an empty function; *_net ratios subtract it.",
        method_c=f"dk/bench_triode.c: the same {len(PTS)} points in C arrays, each form a noinline function writing "
                 "(Ig, Ia, J); 3 warm-up blocks, then 41 blocks of 2000 passes per form interleaved; median (and min) "
                 "block, CLOCK_MONOTONIC. max_rel_diff = largest |after - before| / |before| over the outputs.",
        libm_calls_per_call=dict(
            currents_before="4 exp + 2 log1p + 4 pow = 10 (issue's count: 2 softplus + 2 sigmoid + 4 pow = 8)",
            currents_after="2 exp + 2 log1p + 2 pow = 6 (issue's count: 2 softplus/sigmoid pairs + 2 pow = 4)",
            currents_only="2 exp + 2 log1p + 2 pow, unchanged by #16"),
        machine=dict(python=sys.version.split()[0], numpy=np.__version__, platform=platform.platform(),
                     processor=platform.processor() or platform.machine(), note="this box (lab VM), one run"),
        python=res,
        speedup_currents=dict(gross=ratio("currents_before", "currents_after"),
                              net=ratio("currents_before", "currents_after", ov)),
        speedup_currents_only=dict(gross=ratio("currents_only_before", "currents_only_after"),
                                   net=ratio("currents_only_before", "currents_only_after", ov)),
        c=bench_c())
    with open(os.path.join(HERE, "bench_triode.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))
