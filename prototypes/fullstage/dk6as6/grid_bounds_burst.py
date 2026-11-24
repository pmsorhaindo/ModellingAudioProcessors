"""#131 (bounds only): the #128 burst, blocking, recovery and THD checks at 44.1 kHz, T 0.1-0.5 mA, for the nominal
grid shape and each published bound. Estimated, unvalidated.

At 48 kHz these are dk6as6/validate.py's own records: `python -m dk6as6.validate T --grid` (RSD-1,
results_grid_T.json) and `python -m dk6as6.validate --bounds` (RSD-2 / EHX-1, results_grid_{rsd2,ehx1}_T.json), at
run_refs.GRID_BIASES["T"] = run_refs.BOUND_BIASES["T"]. validate.py runs at its module-level FS (48 kHz), so this
module runs the same validate.run, unchanged, with FS set to 44.1 kHz in each worker: the DK stage (standard and hq,
the k140 resampler) at 44.1 kHz against the same continuous-time ngspice references (spice6as6/reference_dualg[_<bound>]
_T_<bias>mA.json), with the same signals (1 kHz sines at run_refs.AMPS, the 4 V 1 kHz burst 10-30 ms) and the same
criteria (dk/validate.CRIT). Settings outside stage.SUPPORTED_MA (every bound) run as experimental, as in validate.py.
Run (from prototypes/fullstage): python -m dk6as6.grid_bounds_burst [--workers N]
  -> dk6as6/results_grid_bounds_T_44100.json
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk6as6 import validate as V  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

FS = 44100.0
MODE = "T"
MODELS = {"RSD-1": "dualg", **{s: m for m, s in R.GRID_SHAPE.items()}}       # shape -> validate / run_refs model
BIASES = tuple(b for b in R.BOUND_BIASES[MODE] if b <= 0.5)                     # 0.1-0.5 mA (the supported T range)
JSON = os.path.join(HERE, f"results_grid_bounds_{MODE}_{int(FS)}.json")
LABEL = "estimated, unvalidated"


def _run(a):
    shape, bias, quality, fs = a
    V.FS = fs                                        # validate.run reads its module-level FS at call time
    r = V.run(quality=quality, bias=bias, mode=MODE, model=MODELS[shape])
    r["shape"] = shape
    return r


def jobs(fs=FS, shapes=tuple(MODELS), biases=BIASES):
    return [(s, b, q, fs) for b in biases for s in shapes for q in QUALITY]


def main(workers=None, fs=FS, out=JSON, shapes=tuple(MODELS)):
    """Run `shapes` and merge them into `out` (other shapes' results kept)."""
    js = jobs(fs, shapes)
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_run, js))
    rec = json.load(open(out)) if os.path.exists(out) else dict(label=LABEL, fs_base=fs, mode=MODE, models=MODELS,
                                                                 results={})
    for s in shapes:
        rec["results"].pop(s, None)
    for (s, b, q, _), r in zip(js, res):
        rec["results"].setdefault(s, {}).setdefault(f"{b:g}", {})[q] = r
    json.dump(rec, open(out, "w"), indent=1, default=float)
    return {s: {b: {q: V.status(r) for q, r in rb.items()} for b, rb in sb.items()} for s, sb in rec["results"].items()}


def tables(path=JSON):
    """The RESULTS.md burst / blocking / THD tables at 44.1 kHz (grid_bounds.burst_thd_tables' format)."""
    from dk6as6 import grid_bounds
    rec = json.load(open(path))
    return "\n".join(grid_bounds.burst_thd_tables(rec["results"], BIASES))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--shapes", nargs="*", default=list(MODELS))
    ap.add_argument("--tables", action="store_true")
    a = ap.parse_args()
    print(tables() if a.tables else json.dumps(main(a.workers, shapes=tuple(a.shapes))))
