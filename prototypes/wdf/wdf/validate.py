"""Validate the WDF stage with the DK stage's harness, and compare the two methods (#12).

Run from prototypes/wdf:  python -m wdf.validate [--workers N]   -> wdf/results.json (~6 min on 4 cores)

1. Criteria. dk.validate.run, the DK stage's validation against the ngspice references (CRIT, tested against
   reference (ii): ngspice with the same Dempwolf-Zoelzer equations), with WdfStage in place of dk.stage.Stage, for
   every quality setting. The DK stage runs through the same function on the same machine, for the comparison.
2. WDF vs DK directly: operating point, small-signal response (both linearised at their operating points), and
   the output sample by sample on the 1 kHz sines and the 4 V burst of the criteria, through process().
3. Aliasing: the CI subset of dk/aliasing_guard.py (aliasing.CI_TONES, short buffers) on the WDF stage, against
   the DK stage's limits (aliasing.LIMIT_DBC) and its committed baseline (dk/aliasing_baseline.json).
4. CPU per sample (pure Python, interpreted prototypes: relative numbers only). (a) The s/s figure of
   dk.validate.run (time.perf_counter around process(), resampler included). (b) process_internal() alone on a
   2 V 1 kHz sine at the internal rate, WDF and DK interleaved, median of REPS runs. (c) Multiplies per internal
   sample outside the Newton solve, counted from the structures (implementation independent).
5. Parameter-change cost. Bias (Rk scale): DK swap_bias (circuit.build + DC operating point + rest constants) vs
   WDF swap_bias (leaf change + adapt: cathode adaptor and R-type root), timed and counted; and the output after
   a mid-stream bias step, WDF vs DK. Drive and Output are scalar gains in both stages (no rebuild).
"""
import argparse
import json
import os
import platform
import sys
import time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
from . import FULLSTAGE
from .stage import WdfStage, CAPS
from .adaptors import Series, Parallel
from dk import validate as V, linear, circuit, aliasing as AL, aliasing_guard as AG
from dk import stage as dk_stage
from dk.stage import Stage, QUALITY
from spice.run_refs import AMPS

HERE = os.path.dirname(os.path.abspath(__file__))
FS = V.FS
REPS = 7
BIASES = (0.5, 0.75, 1.25, 1.5, 2.0, 1.0)


def run_wdf(quality, filt="fir"):
    """dk.validate.run with WdfStage in place of dk.stage.Stage (as cpp/validate_cpp.py does for the C++ core)."""
    saved = V.Stage
    V.Stage = WdfStage
    try:
        return V.run(quality=quality, filt=filt)
    finally:
        V.Stage = saved


def direct(quality, T=0.05):
    """WDF vs DK: operating point, linearised response, and process() output on the criteria's signals."""
    w, d = WdfStage(FS, quality=quality), Stage(FS, quality=quality)
    op = {k: abs(w.dc[k] / d.dc[k] - 1) for k in ("vgk", "vak", "ig", "ia")}
    op.update({f"v({n})": abs(w.dc["nodes"][n] / d.dc["nodes"][n] - 1) for n in ("g", "k", "a")})
    f = np.array(V.ref("dz")["ac"]["f"])
    f = f[(f >= V.CRIT["ac_band"][0]) & (f <= V.CRIT["ac_band"][1])]
    Hw, Hd = linear.stage_response(w, f), linear.stage_response(d, f)
    out = dict(op_rel_max=max(op.values()), op_rel=op,
               ac_db_max=float(np.max(np.abs(20 * np.log10(np.abs(Hw / Hd))))),
               ac_deg_max=float(np.max(np.abs(np.degrees(np.angle(Hw / Hd))))), sine={})
    n = int(T * FS)
    t = np.arange(n) / FS
    for a in AMPS:
        w.reset()
        d.reset()
        x = float(a) * np.sin(2 * np.pi * 1000 * t)
        yw, yd = w.process(x)["out"], d.process(x)["out"]
        out["sine"][a] = dict(max_abs_v=float(np.max(np.abs(yw - yd))), peak_v=float(np.max(np.abs(yd))),
                              iterations_equal=w.iteration_stats()["histogram"] == d.iteration_stats()["histogram"])
    w.reset()
    d.reset()
    tb = np.arange(int(0.12 * FS)) / FS
    xb = 4 * np.sin(2 * np.pi * 1000 * tb) * ((tb > 10e-3) & (tb < 30e-3))
    rw, rd = w.process(xb, record=("out", "vgk")), d.process(xb, record=("out", "vgk"))
    out["burst"] = dict(max_abs_out_v=float(np.max(np.abs(rw["out"] - rd["out"]))),
                        max_abs_vgk_v=float(np.max(np.abs(rw["_internal"]["vgk"] - rd["_internal"]["vgk"]))),
                        iterations_equal=w.iteration_stats()["histogram"] == d.iteration_stats()["histogram"])
    return out


def _wdf_measure(a):
    AL.Stage = WdfStage            # in the worker: aliasing.measure() builds WdfStage instead of the DK Stage
    return AL._job(a)


def aliasing_subset(workers=None):
    """dk/aliasing_guard.py's CI subset on the WDF stage, compared with the DK limits and baseline (same rows)."""
    jobs = [(q, flt, d, f, dict(window=AL.CI_WINDOW_S, settle=AL.CI_SETTLE_S))
            for (q, flt), tones in AL.CI_TONES.items() for d, fs in tones.items() for f in fs]
    t0 = time.perf_counter()
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            recs = list(ex.map(_wdf_measure, jobs))
    else:
        recs = [_wdf_measure(j) for j in jobs]
    AL.Stage = Stage
    base = json.load(open(AG.BASELINE_JSON))["values"]
    rows = AG.compare(AG.worst_by_key(recs), base, AG.limits_by_key())
    return dict(wall_s=time.perf_counter() - t0, rows=rows, passed=not AG.failed(rows), records=recs)


def _time(fn, reps=REPS):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t0)
    return float(np.median(ts))


def cpu_internal(quality, T=0.02):
    """process_internal() on a 2 V 1 kHz sine at the internal rate (resamplers excluded), us per internal sample;
    WDF and DK alternate, median of REPS."""
    w, d = WdfStage(FS, quality=quality), Stage(FS, quality=quality)
    x = 2.0 * np.sin(2 * np.pi * 1000 * np.arange(int(T * w.fs)) / w.fs)
    tw, td = [], []
    for _ in range(REPS):
        w.reset()
        d.reset()
        tw.append(_time(lambda: w.process_internal(x), 1))
        td.append(_time(lambda: d.process_internal(x), 1))
    us_w, us_d = 1e6 * np.median(tw) / len(x), 1e6 * np.median(td) / len(x)
    return dict(samples=len(x), wdf_us=us_w, dk_us=us_d, wdf_over_dk=us_w / us_d,
                newton_mean=dict(wdf=w.iteration_stats()["mean"], dk=d.iteration_stats()["mean"]))


def multiplies():
    """Multiplies per internal sample outside the Newton solve (the solve is the same code in both).
    DK (dk/stage.process_internal, counting only what one output needs): p = G x + H0 vin (2x6 + 2), y_out =
    D[out] x + E0[out] vin + F[out] di (6 + 1 + 2), x' = A x + B0 vin + C di (36 + 6 + 12).
    WDF: p = E a_E (2x6), b_E = M a_E + N i (6x6 + 6x2), each Parallel 2 (up), each Series 2 (down), v(out) 1."""
    w = WdfStage(FS)
    nodes, stack = [], list(w.ports)
    while stack:
        p = stack.pop()
        nodes.append(p)
        if isinstance(p, (Series, Parallel)):
            stack += [p.c1, p.c2]
    tree = 2 * sum(isinstance(p, (Series, Parallel)) for p in nodes)
    nx, nE = len(CAPS), len(w.ports)
    dk = dict(p=2 * nx + 2, y_out=nx + 1 + 2, state=nx * nx + nx + 2 * nx)
    wd = dict(p=2 * nE, root_scatter=nE * nE + 2 * nE, adaptors=tree, y_out=1)
    return dict(dk=dict(dk, total=sum(dk.values())), wdf=dict(wd, total=sum(wd.values())),
                adaptors=dict(series=sum(isinstance(p, Series) for p in nodes),
                              parallel=sum(isinstance(p, Parallel) for p in nodes), ports_E=nE))


def _dc_newton_iterations(d, bias):
    """Newton iterations of the DK stage's DC solve (all 10 source steps) at `bias`."""
    calls, orig = [], dk_stage.solve

    def counting(*a, **k):
        r = orig(*a, **k)
        calls.append(r[4])
        return r
    d.bias, dk_stage.solve = bias, counting
    try:
        d.operating_point()
    finally:
        dk_stage.solve = orig
    return sum(calls)


def parameter_change(quality="standard", reps=REPS):
    """Bias change cost, WDF vs DK, and what each recomputes. Times are medians over reps passes through BIASES."""
    w, d = WdfStage(FS, quality=quality), Stage(FS, quality=quality)

    def sweep(fn):
        return _time(lambda: [fn(b) for b in BIASES], reps) / len(BIASES)
    out = dict(quality=quality, biases=BIASES)
    out["dk_swap_bias_s"] = sweep(d.swap_bias)
    out["dk_build_s"] = sweep(lambda b: circuit.build(d.fs, rk_scale=b, prewarp_hz=d.prewarp_hz))

    def op(b):
        d.bias = b
        d.operating_point()
    out["dk_operating_point_s"] = sweep(op)
    d.bias = 1.0
    out["wdf_swap_bias_s"] = sweep(w.swap_bias)
    out["wdf_over_dk"] = out["wdf_swap_bias_s"] / out["dk_swap_bias_s"]
    nit = [_dc_newton_iterations(d, b) for b in BIASES]
    d.bias = 1.0
    nn, nu = len(circuit.NODES), 2
    out["recomputed"] = dict(
        dk=f"circuit.build: inverse of the {nn + nu}x{nn + nu} MNA matrix and 9 state-space products; DC operating "
           f"point: a second build (capacitors open, another {nn + nu}x{nn + nu} inverse) and 10 source-stepped Newton "
           f"solves ({min(nit)}-{max(nit)} iterations over these biases); rest constants of the deviation form (#70)",
        wdf=f"Rk leaf, the cathode Parallel adaptor (2 coefficients), the R-type scattering ({len(w.inc)}x{len(w.inc)} "
            f"nodal solve over {w.inc.shape[1]} ports) and E, F, M, N (one 2x2 inverse); no DC solve, no Newton")
    out["dk_dc_newton_iterations"] = nit

    # same physics after a live bias step: 2 V sine, bias 1 -> 1.5 after 20 ms, 30 ms more
    w, d = WdfStage(FS, quality=quality), Stage(FS, quality=quality)
    t = np.arange(int(0.05 * FS)) / FS
    x = 2.0 * np.sin(2 * np.pi * 1000 * t)
    k = int(0.02 * FS)
    yw, yd = [w.process(x[:k])["out"]], [d.process(x[:k])["out"]]
    w.swap_bias(1.5)
    d.swap_bias(1.5)
    yw.append(w.process(x[k:])["out"])
    yd.append(d.process(x[k:])["out"])
    yw, yd = np.concatenate(yw), np.concatenate(yd)
    out["bias_step"] = dict(bias=(1.0, 1.5), at_s=0.02, max_abs_v=float(np.max(np.abs(yw - yd))),
                            peak_v=float(np.max(np.abs(yd))))
    return out


def machine():
    return dict(python=platform.python_version(), numpy=np.__version__, platform=platform.platform(),
                processor=platform.processor() or platform.machine(), cpus=os.cpu_count())


def summary(res):
    lines = []
    for q in QUALITY:
        r = res["settings"][q]
        for m in ("wdf", "dk"):
            o = r[m]["vs"]["dz"]
            harm = max(abs(h["dev_db"]) for a in AMPS for h in o["harm"][a].values() if isinstance(h, dict)
                       and h["tested"])
            lines.append(f"{q} {m}: pass {r[m]['pass_vs_dz']} op {max(o['op_rel'].values()):.2e} ac "
                         f"{o['ac_maxdev_db_chain']:.4f} dB harm {harm:.5f} dB burst {o['burst']['min_rel']:.5f} / "
                         f"{o['burst']['tau_rel']:.5f} / {o['burst']['traj_rel']:.6f} cpu "
                         f"{r[m]['cpu']['seconds_per_second']}")
        lines.append(f"{q} direct: {json.dumps(r['direct'], default=float)}")
        lines.append(f"{q} cpu_internal: {r['cpu_internal']}")
    lines.append(f"multiplies: {res['multiplies']}")
    lines.append(f"parameter_change: {json.dumps(res['parameter_change'], default=float)}")
    if "aliasing" in res:
        for row in res["aliasing"]["rows"]:
            lines.append(f"aliasing {row['key']}: {row['measured_dbc']:.2f} dBc, baseline {row['baseline_dbc']:.2f}, "
                         f"delta {row['delta_db']:+.2f} dB, limit {row['limit_dbc']}, {row['status']}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count(), help="processes for the aliasing subset")
    ap.add_argument("--no-aliasing", action="store_true")
    args = ap.parse_args(argv)
    res = dict(machine=machine(), fullstage=os.path.relpath(FULLSTAGE, os.path.dirname(HERE)), settings={})
    for q in QUALITY:
        print(f"== {q}", flush=True)
        r = dict(wdf=run_wdf(q), dk=V.run(quality=q))
        r["direct"] = direct(q)
        r["cpu_internal"] = cpu_internal(q)
        res["settings"][q] = r
    res["multiplies"] = multiplies()
    res["parameter_change"] = parameter_change()
    if not args.no_aliasing:
        res["aliasing"] = aliasing_subset(args.workers)
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=float)
    print(summary(res))
    bad = [q for q in QUALITY if not all(res["settings"][q]["wdf"]["pass_vs_dz"].values())]
    if "aliasing" in res and not res["aliasing"]["passed"]:
        bad.append("aliasing")
    sys.exit(f"WDF failed: {bad}" if bad else 0)


if __name__ == "__main__":
    main()
