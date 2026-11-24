"""Accuracy at the #113 rework's factors (dk6as6/RESULTS.md, "Inaudible aliasing ... (#113 rework)", Accuracy):
dk6as6.validate.run (every dk/validate.CRIT criterion against the same-model ngspice references) at every reference
setting of both models, at 8x / 16x / 32x / 64x with k140, on the C++ 6AS6 core through its CppStage
(cpp/validate_cpp_6as6.py). NOT run by CI. The committed output, dk6as6/inaudible_validation.json, was run from a PR
#133 checkout (head fc5d690, whose core is identical to main's at c8d882a) with the screening build of
dk6as6/inaudible_sweep.py (MAX_OS 64, later_[5]):
  cd <checkout>/prototypes/fullstage && python <this file> --cli PATH [8 16 32 64]   -> JSON lines on stdout
"""
import argparse
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor

CLI = None


def one(job):
    warnings.simplefilter("ignore")
    from cpp import validate_cpp_6as6 as VC
    from dk6as6 import validate as V
    VC.EXE, VC.FIR = CLI, "k140"
    model, mode, b, os_ = job
    r = VC.run((os_, None), model, b, mode)
    o = r["vs"][r["model"]]
    harm = [abs(v["dev_db"]) for a in o["harm"].values() if isinstance(a, dict)
            for v in a.values() if isinstance(v, dict) and v.get("tested")]
    return dict(model=model, mode=mode, bias_ma=b, os=os_, status=V.status(r), supported=r.get("supported", True),
                passed=r["passed"], op_rel=max(o["op_rel"].values()), ac_stage_db=o["ac_maxdev_db_stage"],
                ac_chain_db=o["ac_maxdev_db_chain"], harm_db=max(harm) if harm else None, burst=o["burst"],
                nonconv=sum(st["nonconverged"] for st in r["iterations"].values()))


def _init(cli):
    global CLI
    CLI = cli


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", required=True)
    ap.add_argument("factors", nargs="*", type=int, default=[8, 16, 32, 64])
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    sys.path.insert(0, os.getcwd())
    from cpp import validate_cpp_6as6 as VC
    jobs = [(model, mode, b, f) for f in a.factors for model, modes in VC.MODELS.items() for mode, bs in modes.items()
            for b in bs]
    with ProcessPoolExecutor(a.workers, initializer=_init, initargs=(a.cli,)) as ex:
        for r in ex.map(one, jobs):
            print(json.dumps(r, default=float), flush=True)
