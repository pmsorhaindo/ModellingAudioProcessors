"""Compare the offline grid-ADAA renders against the stored same-model ngspice references."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from itertools import product
import json
from pathlib import Path

import numpy as np

from . import linear, pentode, validate as v
from .grid_adaa_measure import CONFIGS, RATES, config, render
from .stage import Stage

# Nominal-grid settings with complete committed ngspice reference records.
CASES = (("P1", 0.1), ("P1", 0.25), ("P1", 0.45),
         ("T", 0.1), ("T", 0.25), ("T", 0.35), ("T", 0.45), ("T", 0.5))


def response(stage, frequencies, order):
    """DC linearization, with the grid slope multiplied by the ADAA FIR."""
    if order == 0:
        return linear.stage_response(stage, frequencies)
    dc = stage.dc
    voltages = tuple(dc[k] for k in ("vg1k", "vg2k", "vg3k", "vak"))
    _, _, share, _ = pentode.cathode_share(*voltages, stage.par)
    _, slope = pentode.grid_current(voltages[0], stage.grid)
    ordinary_j = linear.jacobian(stage)
    grid_j = np.zeros_like(ordinary_j)
    grid_j[:, 0] = np.array([-share, share - 1, 1]) * slope
    m = stage.m
    result = []
    for z in np.exp(2j * np.pi * frequencies / stage.fs):
        # On an affine law the two-point and three-point divided
        # differences give equal weights to their input samples.
        fir = sum(z**-k for k in range(order + 1)) / (order + 1)
        j = ordinary_j + (fir - 1) * grid_j
        stamp = np.linalg.solve(np.eye(3) + j @ m["K"], j)
        a = m["A"] + m["C"] @ stamp @ m["G"]
        b = m["B"][:, 0] + m["C"] @ stamp @ m["H"][:, 0]
        c = m["D"][0] + m["F"][0] @ stamp @ m["G"]
        d = m["E"][0, 0] + m["F"][0] @ stamp @ m["H"][:, 0]
        result.append(c @ np.linalg.solve(z * np.eye(len(a)) - a, b) + d)
    return np.array(result)


def compare(job):
    lib, name, fs, (mode, bias) = job
    order, os = config(name)
    stage = Stage(fs, quality=(os, None), mode=mode, bias=bias)
    reference = Path(v.ref_file("dualg", bias, mode))
    ref = json.loads(reference.read_text())
    op = v.op_record(stage)
    op_rel = float(max(abs(op[k] / ref["op"][k] - 1) for k in v.OP_KEYS))
    f, db_ref = (np.asarray(ref["ac"][k]) for k in ("f", "db"))
    lo, hi = v.CRIT["ac_band"]
    keep = (lo <= f) & (f <= hi)
    f, db_ref = f[keep], db_ref[keep]
    h = response(stage, f, order)
    ac_stage = float(np.max(abs(20 * np.log10(abs(h)) - db_ref)))
    ac_chain = float(np.max(abs(20 * np.log10(abs(h * stage.rs.chain_response(f))) - db_ref)))

    failures = outside = 0

    def simulate(x):
        nonlocal failures, outside
        y, meta, vgk, vg2k = render(lib, fs, os, order, mode, bias, x, voltages=True)
        failures += meta["nonconverged"]
        outside += meta["out_of_law"]
        return y[meta["latency"]:], vgk, meta["up_delay"], v.exp_capped(stage, {"vgk": vgk, "vg2k": vg2k})

    tone = np.sin(2 * np.pi * 1000 * np.arange(round(0.5 * fs) + stage.latency_samples) / fs)
    sine = {}
    for amp in v.AMPS:
        y, _, _, capped = simulate(float(amp) * tone)
        hh = v.harmonics(y, fs, 1000.0, 100)
        sine[amp] = dict(H_peak_V=hh[:5].tolist(), H_dBc=v.db_rel(hh)[:5].tolist(),
                         thd_pct=float(100*v.thd(hh)), spice_exp_capped=capped)
    harmonics = v.harm_dev(sine, ref)
    harm_error = max((abs(h["dev_db"]) for row in harmonics.values() for h in row.values()
                      if isinstance(h, dict) and h["tested"]), default=0.0)
    harm_uncompared = any(
        row.get("spice_exp_capped") and any(isinstance(h, dict) and h["ref_dBc"] > v.CRIT["harm_floor_dbc"]
                                          for h in row.values()) for row in harmonics.values())

    n = round(0.12 * fs)
    t = np.arange(n + stage.latency_samples) / fs
    _, vgk, up, capped = simulate(4 * np.sin(2*np.pi*1000*t) * ((t > 0.01) & (t < 0.03)))
    ti = (np.arange(len(vgk)) - up) / (fs * os)
    keep = (0 <= ti) & (ti < n/fs)
    burst = v.burst_compare(v.burst_metrics(ti[keep], vgk[keep]), ref["burst"])
    passed = dict(op=op_rel < v.CRIT["op_rel"], ac_stage=ac_stage < v.CRIT["ac_db"],
                  ac_chain=ac_chain < v.CRIT["ac_db"],
                  harmonics=False if harm_error >= v.CRIT["harm_db"] else None if harm_uncompared else True,
                  burst=None if capped else bool(v.burst_ok(burst)), newton=failures == 0)
    return dict(config=name, fs=fs, mode=mode, bias=bias, reference=reference.name, passed=passed,
                op_rel=op_rel, ac_stage_db=ac_stage, ac_chain_db=ac_chain, harm_db=harm_error,
                thd_pct={a: row["thd_pct"] for a, row in sine.items()},
                thd_ref_pct={a: row["thd_ref_pct"] for a, row in harmonics.items()},
                burst={k: (float(burst[k]) if burst[k] is not None and np.isfinite(burst[k]) else None)
                       for k in ("min_rel", "traj_rel", "tau_rel")},
                nonconverged=failures, out_of_law=outside)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lib", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--configs", nargs="+", choices=CONFIGS, default=CONFIGS)
    p.add_argument("--workers", type=int, default=2)
    args = p.parse_args()
    jobs = product((args.lib.resolve(),), args.configs, RATES, CASES)
    with ProcessPoolExecutor(args.workers) as pool, args.out.open("w") as out:
        for row in pool.map(compare, jobs, chunksize=1):
            out.write(json.dumps(row, allow_nan=False) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
