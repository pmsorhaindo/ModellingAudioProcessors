"""Compare the ADAA research renders with same-model ngspice records (#136)."""
import argparse
from itertools import product
import json
from pathlib import Path
import warnings

import numpy as np

from dk6as6 import adaa, validate as V
from dk6as6.adaa_measure import CONFIGS, config, render
from dk6as6.stage import ExperimentalWarning


def compare(lib, name, nodes, model, mode, bias, reference_dir=None):
    reference = Path(V.ref_file(model, bias, mode))
    if reference_dir is not None:
        extra = Path(reference_dir) / reference.name
        if extra.is_file():
            reference = extra
    order, os = config(name)
    hardness = V.HARDNESS[model]
    fs = V.FS
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        kw = dict(bias=bias, quality=(os, None), mode=mode)
        st = (adaa.Stage(fs, adaa=order, nodes=nodes, grid_hardness=hardness, experimental=True, **kw)
              if order else V.stage(model, **kw))

    def simulate(x):
        out, stats, vgk, vg2k = render(lib, fs, os, order, nodes, mode, bias, x,
                                       hardness=hardness, voltages=True)
        capped = V.exp_capped(st, dict(vgk=vgk, vg2k=vg2k))
        return out, stats, vgk, capped

    sine, failures = {}, 0
    n = round(0.5 * fs)  # dk6as6.validate.sines default
    time = np.arange(n + st.latency_samples) / fs
    for a in V.AMPS:
        out, stat, _, capped = simulate(float(a) * np.sin(2 * np.pi * 1000 * time))
        assert stat["latency"] == st.latency_samples
        h = V.harmonics(out[stat["latency"]:], fs, 1000.0, 100)
        sine[a] = dict(H_peak_V=h[:5].tolist(), H_dBc=V.db_rel(h)[:5].tolist(),
                       thd_pct=float(100 * V.thd(h)))
        if capped:
            sine[a]["spice_exp_capped"] = True
        failures += stat["nonconverged"]

    result = dict(config=name, model=model, mode=mode, bias=bias, supported=st.supported,
                  thd_pct={a: s["thd_pct"] for a, s in sine.items()})
    if not reference.is_file():
        return dict(result, status="no_reference")
    ref = json.loads(reference.read_text())
    op = V.op_record(st)
    op_rel = float(max(abs(op[k] / ref["op"][k] - 1) for k in V.OP_KEYS))
    f, db = np.asarray(ref["ac"]["f"]), np.asarray(ref["ac"]["db"])
    lo, hi = V.CRIT["ac_band"]
    keep = (f >= lo) & (f <= hi)
    f, db = f[keep], db[keep]
    stage_db, chain_db = (V.small_signal(st, f) if not order else
                          (20 * np.log10(abs(st.response(f))),
                           20 * np.log10(abs(st.response(f) * st.rs.chain_response(f)))))
    ac_stage = float(np.max(abs(stage_db - db)))
    ac_chain = float(np.max(abs(chain_db - db)))
    harmonics = V.harm_dev(sine, ref)
    harm_err = [abs(h["dev_db"]) for row in harmonics.values() for h in row.values()
                if isinstance(h, dict) and h["tested"]]
    harm_uncompared = any(row.get("spice_exp_capped") and any(
        isinstance(h, dict) and h["ref_dBc"] > V.CRIT["harm_floor_dbc"] for h in row.values())
        for row in harmonics.values())

    nb = round(0.12 * fs)
    t = np.arange(nb + st.latency_samples) / fs
    x = 4 * np.sin(2 * np.pi * 1000 * t) * ((t > 0.01) & (t < 0.03))
    _, stat, vgk, capped = simulate(x)
    ti = (np.arange(len(vgk)) - stat["up_delay"] - order / 2) / (fs * os)
    keep = (ti >= 0) & (ti < nb / fs)
    burst = V.burst_compare(V.burst_metrics(ti[keep], vgk[keep]), ref["burst"])
    failures += stat["nonconverged"]
    harm_max = max(harm_err, default=0.0)
    passed = dict(op=op_rel < V.CRIT["op_rel"], ac_stage=ac_stage < V.CRIT["ac_db"],
                  ac_chain=ac_chain < V.CRIT["ac_db"],
                  harmonics=False if harm_max >= V.CRIT["harm_db"] else None if harm_uncompared else True,
                  burst=None if capped else bool(V.burst_ok(burst)), newton=failures == 0)
    return dict(result, reference=reference.name, status=V.status(dict(passed=passed)), passed=passed,
                op_rel=op_rel, ac_stage_db=ac_stage, ac_chain_db=ac_chain, harm_db=harm_max,
                thd_ref_pct={a: r["thd_ref_pct"] for a, r in harmonics.items()},
                max_h1_db=max(abs(r["H1_dev_db"]) for r in harmonics.values() if not r.get("spice_exp_capped")),
                burst={k: (float(burst[k]) if burst[k] is not None and np.isfinite(burst[k]) else None)
                       for k in ("min_rel", "traj_rel", "tau_rel")},
                nonconverged=failures)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lib", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--reference-dir", type=Path)
    p.add_argument("--configs", nargs="+", choices=CONFIGS, default=CONFIGS)
    p.add_argument("--nodes", type=int, choices=(2, 4, 8), default=4)
    p.add_argument("--models", nargs="+", choices=("dual", "dualg"), default=("dual", "dualg"))
    args = p.parse_args()

    biases = (0.1, 0.25, 0.45, 0.6)
    cases = [*product(("P1", "T", "P2"), biases), ("T", 0.35), ("T", 0.5)]
    with args.out.open("w") as out:
        for name, model, (mode, bias) in product(args.configs, args.models, cases):
            row = compare(args.lib, name, args.nodes, model, mode, bias, args.reference_dir)
            out.write(json.dumps(row, allow_nan=False) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
