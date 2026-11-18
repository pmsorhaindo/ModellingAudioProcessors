"""Short comparative costs and ADAA quadrature check, run serially after issue96 / verify_issue96.

python -m dk.experiments.cost_issue96 --scratch ../../.scratch/issue96
Uses the existing benchmark signal/timers/counters, 0.5 s x 3 passes, three rounds in alternating variant order.
No official benchmark files are rewritten. Writes dk/results_issue96_cost.json.
"""
import argparse
import json
from pathlib import Path
import statistics
import subprocess

import numpy as np

from cpp import bench, validate_cpp as VC
from dk import aliasing as AL, resample as RS
from dk.experiments import issue96 as E

DEST = E.ROOT / "dk/results_issue96_cost.json"


def transfer(name, exe):
    """1 kHz small-signal group delay; circuit + FIR, rather than pretending ADAA adds a pure output delay."""
    saved = RS.FIR_FIRST_44100
    RS.FIR_FIRST_44100 = (231 if name == "fir231" else 127, 9.0)
    E.ExperimentStage.variant, VC.EXE = name, exe
    n, n0 = 44100, 11025
    t = np.arange(n+n0+200)/44100
    x = 1e-5*(np.sin(2*np.pi*999*t)+np.sin(2*np.pi*1001*t))
    X = np.fft.rfft(x[n0:n0+n])
    out = {}
    try:
        for q in (("eco",) if name == "prewarp20k" else ("hq", "standard", "eco")):
            st = E.ExperimentStage(44100, quality=q)
            Y = np.fft.rfft(st.process(x)["out"][n0+st.latency_samples:][:n])
            Hlo, Hhi = Y[999]/X[999], Y[1001]/X[1001]
            gd = -np.angle(Hhi/Hlo)/(2*np.pi*2/44100)+st.latency_samples
            out[q] = dict(group_delay_1k_base_samples=float(gd), reported_latency_samples=st.latency_samples,
                          nonconverged=st.iteration_stats()["nonconverged"])
    finally:
        RS.FIR_FIRST_44100 = saved
    return out


def quadrature_check(exe):
    """Double the integration order at every 8-node sweep's worst tone, and every quality's 19,037 Hz tone."""
    old = json.loads(E.DEST.read_text())["variants"]["adaa8"]["records"]
    keys = {(r["setting"], r["drive"], r["tone"]) for r in
            json.loads(E.DEST.read_text())["variants"]["adaa8"]["worst"].values()}
    keys |= {(q, d, 19037) for q in ("hq", "standard", "eco") for d in AL.DRIVES}
    saved_stage, saved_fir = AL.Stage, RS.FIR_FIRST_44100
    AL.Stage, RS.FIR_FIRST_44100 = E.ExperimentStage, (127, 9.0)
    E.ExperimentStage.variant, VC.EXE = "adaa16", exe
    records = []
    try:
        for q, d, f in sorted(keys):
            r = AL.measure(q, "fir", d, f, fs=44100)
            previous = next(v for v in old if (v["setting"], v["drive"], v["tone"]) == (q, d, f))
            records.append(dict(setting=q, drive=d, tone=f, adaa8_dbc=previous["total_dbc"],
                                adaa16_dbc=r["total_dbc"], delta_db=r["total_dbc"]-previous["total_dbc"]))
        return dict(records=records, max_abs_delta_db=max(abs(r["delta_db"]) for r in records))
    finally:
        AL.Stage, RS.FIR_FIRST_44100 = saved_stage, saved_fir


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch", type=Path, required=True)
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    assert scratch.is_relative_to(E.ROOT.parent.parent)
    exes = {name: E.build_variant(scratch, name) for name in (*E.VARIANTS, "adaa16")}
    out = dict(method=dict(fs=44100, seconds_per_pass=0.5, reps=3, rounds=3, block=64, math="libm", instances=1,
                           flags=["-O3"], order="baseline/fir/prewarp/adaa; reverse; forward",
                           note="comparative M1 experiment, not an official #20/#21/#76 benchmark"),
               host=bench.host_info(), rounds=[], summary={}, transfer={})
    for idx in range(3):
        order = E.VARIANTS if idx % 2 == 0 else E.VARIANTS[::-1]
        results = {}
        for name in order:
            qs = ("eco",) if name == "prewarp20k" else ("hq", "standard", "eco")
            only = ",".join(f"{q}_libm_{d}V_b64_x1" for q in qs for d in (2, 8))
            before = bench.host_state()
            p = subprocess.run([exes[name]["bench"], "--seconds", "0.5", "--reps", "3", "--only", only],
                               capture_output=True, text=True, check=True)
            results[name] = dict(state_before=before, state_after=bench.host_state(), **json.loads(p.stdout))
        out["rounds"].append(results)
        DEST.write_text(json.dumps(out, indent=1)+"\n")
        print("cost round", idx+1, flush=True)
    for name in E.VARIANTS:
        bykey = {}
        for run in out["rounds"]:
            for r in run[name]["results"]:
                bykey.setdefault(r["name"], []).append(r)
        out["summary"][name] = {
            key: dict(ns_per_sample=statistics.median(r["ns_per_sample"] for r in rows),
                      ns_run_median_min=min(r["ns_per_sample"] for r in rows),
                      ns_run_median_max=max(r["ns_per_sample"] for r in rows),
                      p_core_fraction_min=min(r["p_core_fraction_min"] for r in rows),
                      deterministic=all(r["deterministic"] for r in rows),
                      nonconverged=sum(r["nonconverged"] for r in rows))
            for key, rows in bykey.items()}
        out["transfer"][name] = transfer(name, exes[name]["test_core"])
    for name, rows in out["summary"].items():
        for key, r in rows.items():
            r["ratio_to_baseline"] = r["ns_per_sample"]/out["summary"]["baseline"][key]["ns_per_sample"]
    for name, rows in out["transfer"].items():
        for q, r in rows.items():
            r["group_delay_delta_samples"] = (r["group_delay_1k_base_samples"]
                                              - out["transfer"]["baseline"][q]["group_delay_1k_base_samples"])
    out["adaa_quadrature_check"] = quadrature_check(exes["adaa16"]["test_core"])
    DEST.write_text(json.dumps(out, indent=1)+"\n")
    print("cost checks done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
