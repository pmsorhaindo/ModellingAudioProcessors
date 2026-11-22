"""Plain 4x (no pre-warp) against the same-model ngspice references (#113): dk6as6/validate.py's run(), dk/validate.CRIT
unchanged, at every reference bias of P1 and of T (0.1 / 0.25 / 0.35 mA bracket T's supported 0.1-0.3 mA, as for
standard and hq). validate.py runs 4x at P1 0.45 mA only ("plain_4x", for information); this is the eco question's
accuracy evidence. P2 is experimental (#123) and not run.
Run from prototypes/fullstage:  python -m dk6as6.validate_4x [--workers N]   -> dk6as6/results_4x.json
"""
import argparse
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk6as6 import validate as V  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import ExperimentalWarning  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import MODE_BIASES  # noqa: E402 -- after the sys.path setup

OUT_JSON = os.path.join(HERE, "results_4x.json")
CASES = [("P1", b) for b in MODE_BIASES["P1"]] + [("T", b) for b in (0.1, 0.25, 0.35)]


def _job(case):
    mode, bias = case
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        V.FIR = "shipped"            # results_4x.json is #113's record, measured with the shipped FIR
        return mode, bias, V.run(quality=(4, None), bias=bias, mode=mode)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(_job, CASES))
    out = {}
    for mode, bias, r in res:
        out.setdefault(mode, {})[f"{bias:g}"] = r
        print(f"== {mode} {bias:g} mA, 4x: {V.status(r)}")
        print(V.summary(r))
    json.dump(out, open(OUT_JSON, "w"), indent=1, default=float)
