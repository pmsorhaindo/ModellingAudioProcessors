"""Validate the 6AS6 DK stage against its same-model ngspice references (spice6as6/reference_dual_<bias>mA.json, #26).

  (ii) 'dual' : ngspice running the same fit6as6 dual_control equations and parameters (#55) on the same netlist ->
                isolates METHOD error. The pass criteria are dk/validate.CRIT, unchanged, tested against this only.
  There is no (i): no independent 6AS6 SPICE model in this repo's sources.
Same measurements as dk/validate.py (op point, small signal 20 Hz-20 kHz over the whole resampler chain, H2-H5 of 1 kHz
sines at spice/run_refs.AMPS, the 4 V tone-burst bias shift and recovery). The shift and trajectory criteria are
unchanged. Recovery (#124, resolved for this stage in #112): CRIT's burst_tau_rel (1 %) compares the tail tau, the
decay constant of the post-burst recovery fitted on full-window cycle-mean samples only (burst_compare, below; the
shared burst_metrics' crossing tau read the zero-padded end of the trace). Without grid current that tail is the
~0.07 % coupling-cap tail; with it, grid blocking. `python -m dk6as6.validate --rescore` re-evaluates every committed
results[_grid][_<mode>].json burst comparison from the stored trajectories (no simulation).
run(model="r51") checks the DK stage with the #51 parameters against reference_r51_0.45mA.json the same way (for
dk6as6/compare_fits.py; informational).
Grid current (#112): model "dual" is the stage at grid_hardness=0 (Ig1 = 0, the #111 law); model "dualg" is the stage's
default, with the UNVALIDATED g1 grid current (pentode.grid at pentode.HARDNESS), against reference_dualg_*.json, which
run the same law (run_refs.HARDNESS). Same criteria, same signals.
Grid-current bounds (#131): models "dualg_rsd2" / "dualg_ehx1" are the stage at grid_shape "RSD-2" / "EHX-1"
(pentode.GRID_BOUNDS, run_refs.GRID_SHAPE; estimated, unvalidated) against reference_dualg_{rsd2,ehx1}_T_*.json, T at
run_refs.BOUND_BIASES; --bounds runs them -> results_grid_{rsd2,ehx1}_T.json. Experimental settings (no bound has a
supported envelope), so they are recorded, never counted as passes of a supported setting.
bias: the target cathode current in mA (dk6as6/stage.py); references exist at spice6as6/run_refs.BIASES.
mode: the Distortion switch (#111, dk6as6/circuit.MODES); T and P2 references exist at run_refs.MODE_BIASES[mode].
Where the reference is not the same law: ngspice clamps a B-source exp() argument at SPICE_EXP_CAP, so the subckt's
Koren term ln(1 + exp(KP (1/MU + Vg1k / max(Vg2k, 1 mV)))) stops following the DK law (an exact softplus) once that
argument passes it, i.e. for Vg1k / Vg2k > ~1.5: a positive g1 over a screen near 0 V. On the validate.py signals this
happens only in T at high current and drive, where the stage is outside the valve law anyway (dk6as6/RESULTS.md,
Modes). A sine (or the burst) whose DK run reaches it is marked "spice_exp_capped"; its harmonics (burst metrics) are
reported but not compared, and that criterion reads None ("not compared"), so the case is "inconclusive" (status()),
never "passed". Settings outside stage.SUPPORTED_MA are run as experimental and marked "supported": False; the
command fails if any supported setting does not pass.
Aliasing: dk6as6/aliasing.py.
Run: python -m dk6as6.validate [P1] [T] [P2] [--grid] [--workers N]   (from prototypes/fullstage; every mode if none
given; --grid: model "dualg", the modes with grid references, run_refs.GRID_BIASES). Writes, per mode,
dk6as6/results.json (P1) / results_<mode>.json (dual), results_grid.json / results_grid_<mode>.json (dualg): per
reference bias, "standard" and
"hq" against (ii), and for P1 dual at the default bias "plain_4x": the same run at 4x without pre-warp, for information
only (no 4x setting).
"""
import json
import os
import sys
import time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from analysis import harmonics, thd, db_rel  # noqa: E402 -- after the sys.path setup
from spice.run_refs import burst_metrics, AMPS  # noqa: E402 -- after the sys.path setup
from dk.validate import CRIT, aligned  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, QUALITY, BIAS_MA  # noqa: E402 -- after the sys.path setup
from dk6as6 import linear, pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.circuit import MODE  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import (MODE_BIASES, GRID_BIASES, BOUND_BIASES, HARDNESS, GRID_SHAPE, ref_file,  # noqa: E402,F401
                                 biases_of, shape)

FS = 48000.0
PARAMS = {"dual": pentode.DUAL_CONTROL, "r51": pentode.REFIT_51, "dualg": pentode.DUAL_CONTROL,
          "dualg_rsd2": pentode.DUAL_CONTROL, "dualg_ehx1": pentode.DUAL_CONTROL}
OP_KEYS = ("va", "vk", "ia", "ig2", "ik", "vgk")
SPICE_EXP_CAP = 227.9559242   # ngspice 41 B-source exp() argument limit: ln(1 + exp(300)) evaluates to 227.9559


def exp_capped(stage, rec):
    """True if the DK run's Koren softplus argument passed ngspice's exp() cap anywhere (see the module docstring)."""
    p = stage.par
    x = p["KP"] * (1.0 / p["MU"] + rec["vgk"] / np.maximum(rec["vg2k"], 1e-3))
    return bool(np.max(x) > SPICE_EXP_CAP)


def ref(model, bias=BIAS_MA, mode=MODE):
    return json.load(open(ref_file(model, bias, mode)))


def results_file(mode=MODE, model="dual"):
    g = "_grid" if model == "dualg" else "_grid_" + model.split("_", 1)[1] if model in GRID_SHAPE else ""
    return os.path.join(HERE, f"results{g}.json" if mode == "P1" else f"results{g}_{mode}.json")


FIR = None     # the stage's FIR design: None = dk6as6.stage's default (k140 since #134); "shipped" for the #113 records


def stage(model="dual", **kw):
    """The DK stage of `model`: its valve parameters, grid hardness (run_refs.HARDNESS) and grid shape (run_refs.shape),
    experimental allowed. FIR (module level), if set, picks the stage's FIR design."""
    if FIR is not None:
        kw.setdefault("fir", FIR)
    return Stage(FS, pentode=PARAMS[model], grid_hardness=HARDNESS[model], grid_shape=shape(model), experimental=True,
                 **kw)


def op_record(st):
    """The DK operating point in the reference's op keys (OP_KEYS), plus vg, vg2k, vg3k, vak; ig1 with grid current."""
    dc = st.dc
    return dict(va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ia=dc["ip"], ig2=dc["ig2"], ik=dc["ik"], vgk=dc["vgk"],
                vg=dc["nodes"]["g"], vg2k=dc["vg2k"], vg3k=dc["vg3k"], vak=dc["vak"],
                **({} if st.grid is None else dict(ig1=dc["ig1"])))


def small_signal(stage, f):
    """(stage alone, whole chain) small-signal response in dB, as dk/validate.small_signal."""
    f = np.asarray(f, float)
    Hs = np.abs(linear.stage_response(stage, f))
    Hc = Hs * np.abs(stage.rs.chain_response(f))
    return 20 * np.log10(Hs), 20 * np.log10(Hc)


def ac_dev(st, r):
    f = np.array(r["ac"]["f"])
    sel = (f >= CRIT["ac_band"][0]) & (f <= CRIT["ac_band"][1])
    ds, dch = small_signal(st, f[sel])
    refdb = np.array(r["ac"]["db"])[sel]
    o = dict(ac_maxdev_db_stage=float(np.max(np.abs(ds - refdb))),
             ac_maxdev_db_chain=float(np.max(np.abs(dch - refdb))),
             ac_worst_f_stage=float(f[sel][np.argmax(np.abs(ds - refdb))]),
             ac_worst_f_chain=float(f[sel][np.argmax(np.abs(dch - refdb))]))
    o["ac_dev_at"] = {str(int(fx)): float((ds - refdb)[np.argmin(abs(f[sel] - fx))])
                      for fx in (20, 100, 1000, 10000, 20000)}
    o["ac_chain_dev_at"] = {str(int(fx)): float((dch - refdb)[np.argmin(abs(f[sel] - fx))])
                            for fx in (20, 100, 1000, 10000, 20000)}
    return o


def sines(quality="standard", filt="fir", model="dual", T=0.5, amps=AMPS, bias=BIAS_MA, mode=MODE):
    """1 kHz sines through Stage.process: harmonics, iteration stats and CPU per amplitude."""
    out, times, its = {}, {}, {}
    n = int(T * FS)
    for a in amps:
        s = stage(model, bias=bias, quality=quality, filt=filt, mode=mode)
        t = np.arange(n + s.latency_samples) / FS
        t0 = time.perf_counter()
        r = aligned(s, float(a) * np.sin(2 * np.pi * 1000 * t), record=("out", "vgk", "vg2k"))
        times[a] = (time.perf_counter() - t0) / (len(t) / FS)
        h = harmonics(r["out"], FS, 1000.0, 100)
        out[a] = dict(H_peak_V=h[:5].tolist(), H_dBc=db_rel(h)[:5].tolist(), thd_pct=100 * thd(h))
        if exp_capped(s, r["_internal"]):
            out[a]["spice_exp_capped"] = True
        its[a] = s.iteration_stats()
    return out, times, its


def burst(quality="standard", filt="fir", model="dual", bias=BIAS_MA, mode=MODE, t_stop=0.12, raw=False):
    """The 4 V 1 kHz tone burst (10-30 ms, t_stop = 120 ms as the references) through Stage.process: (burst_metrics of
    Vgk, iteration stats); raw=True adds the internal-rate (t, Vgk) as m["raw"]."""
    s = stage(model, bias=bias, quality=quality, filt=filt, mode=mode)
    nb = int(t_stop * FS)
    tb = np.arange(nb + s.latency_samples) / FS
    xb = 4 * np.sin(2 * np.pi * 1000 * tb) * ((tb > 10e-3) & (tb < 30e-3))
    rb = aligned(s, xb, record=("out", "vgk", "vg2k"))
    vg = rb["_internal"]["vgk"]
    ti = (np.arange(len(vg)) - s.rs.up_delay_internal) / s.fs   # Vgk is seen after the interpolator only
    keep = (ti >= 0) & (ti < nb / FS)
    m = burst_metrics(ti[keep], vg[keep])
    if raw:
        m["raw"] = (ti[keep], vg[keep], s.fs)
    if exp_capped(s, rb["_internal"]):
        m["spice_exp_capped"] = True
    return m, s.iteration_stats()


# The burst recovery measure (#124, resolved here for the 6AS6 stage; PR #128 review). spice/run_refs.burst_metrics'
# recovery_tau (kept in every record as "padded" tau, informational) starts on the 30.5 ms sample, whose one-cycle mean
# still straddles the burst's end, and searches the cycle mean right up to the end of the 120 ms trace, where
# np.convolve(..., "same") pads with zeros: where the true 1/e crossing lies beyond the trace it reports the padded edge
# (T 0.5 mA with grid current: 89.0 ms on the 120 ms trace, 106.0 ms on a 250 ms one). Both measures below use only the
# stored 0.5 ms trajectory (traj_t / traj_vgk: the cycle mean at 0-118.5 ms, every sample a full one-cycle window inside
# the trace) from TAIL_T0 = 31 ms, the first sample whose window has left the burst (10-30 ms):
#   tail tau   the decay constant of a log-linear fit of |Vgk - rest| over 31-118 ms (the tail single-signed: above
#              rest for the coupling-cap tail without grid current, below it for grid blocking). THE CRITERION: CRIT's
#              burst_tau_rel (1 %), unchanged, compares it with the reference's.
#   cross tau  the time from 31 ms for |Vgk - rest| to fall to 1/e of its 31 ms value, on the same samples; NaN
#              ("unobserved") if it does not within the trace. Reported, compared where both are observed.
TAIL_T0, TAIL_T1 = 31e-3, 118e-3


def _tail(burst, t0=TAIL_T0, t1=TAIL_T1):
    t, v = np.array(burst["traj_t"]), np.array(burst["traj_vgk"]) - burst["rest_vgk"]
    m = (t >= t0 - 1e-9) & (t <= t1 + 1e-9)
    return t[m], v[m]


def tail_tau(burst, t0=TAIL_T0, t1=TAIL_T1):
    """The tail tau (s) above; NaN if the tail crosses rest (no single decay constant)."""
    t, v = _tail(burst, t0, t1)
    sgn = np.sign(np.mean(v))
    if not np.all(sgn * v > 0):
        return float("nan")
    return float(-1.0 / np.polyfit(t, np.log(sgn * v), 1)[0])


def cross_tau(burst, t0=TAIL_T0):
    """The cross tau (s) above; NaN where the 1/e crossing is not observed in the full-window trajectory."""
    t, v = _tail(burst, t0, burst["traj_t"][-1])
    j = np.nonzero(v / v[0] < np.exp(-1))[0] if v[0] != 0 else []
    return float(t[j[0]] - t[0]) if len(j) else float("nan")


def _rel(a, b):
    return abs(a / b - 1) if np.isfinite(a) and np.isfinite(b) else None


def burst_compare(dk, ref):
    """The burst criteria of a DK burst record against its reference's (both burst_metrics dicts): shift, trajectory
    and the tail tau (CRIT), the cross tau and the padded tau for information."""
    shift_ref = ref["min_vgk"] - ref["rest_vgk"]
    tr = np.array(dk["traj_vgk"]) - dk["rest_vgk"]
    rr = np.array(ref["traj_vgk"]) - ref["rest_vgk"]
    td, trf = tail_tau(dk), tail_tau(ref)
    cd, crf = cross_tau(dk), cross_tau(ref)
    return dict(min_rel=abs((dk["min_vgk"] - dk["rest_vgk"]) / shift_ref - 1),
                traj_rel=float(np.sqrt(np.mean((tr - rr) ** 2)) / abs(shift_ref)),
                tau_tested=bool(np.isfinite(trf)),
                tau_rel=abs(td / trf - 1) if np.isfinite(trf) else None,       # tail tau; inf-safe: NaN DK fails
                tail_tau_s=dict(dk=td, ref=trf),
                cross_tau_s=dict(dk=cd, ref=crf), cross_tau_rel=_rel(cd, crf),
                padded_tau_s=dict(dk=dk["recovery_tau_s"], ref=ref["recovery_tau_s"]),
                **({"spice_exp_capped": True} if dk.get("spice_exp_capped") else {}),
                t_min_diff_ms=1e3 * (dk["t_min"] - ref["t_min"]),
                ref=dict(rest=ref["rest_vgk"], min=ref["min_vgk"]))


def burst_ok(b):
    tau_ok = not b["tau_tested"] or (b["tau_rel"] is not None and np.isfinite(b["tau_rel"])
                                     and b["tau_rel"] < CRIT["burst_tau_rel"])
    return b["min_rel"] < CRIT["burst_min_rel"] and b["traj_rel"] < CRIT["burst_traj_rel"] and tau_ok


def harm_dev(sine, r):
    res = {}
    for a in sine:
        rd = np.array(r["sine"][a]["H_dBc"])
        dd = np.array(sine[a]["H_dBc"])
        rows = {}
        capped = sine[a].get("spice_exp_capped", False)
        for hk in range(1, 5):          # H2..H5
            rows[f"H{hk+1}"] = dict(ref_dBc=float(rd[hk]), dk_dBc=float(dd[hk]), dev_db=float(dd[hk] - rd[hk]),
                                    tested=bool(rd[hk] > CRIT["harm_floor_dbc"]) and not capped)
        if capped:
            rows["spice_exp_capped"] = True
        rows["H1_dev_db"] = float(20 * np.log10(sine[a]["H_peak_V"][0] / r["sine"][a]["H_peak_V"][0]))
        rows["thd_ref_pct"] = r["sine"][a]["thd_pct"]
        rows["thd_dk_pct"] = sine[a]["thd_pct"]
        res[a] = rows
    return res


def run(T=0.5, quality="standard", filt="fir", model="dual", bias=BIAS_MA, mode=MODE):
    st = stage(model, bias=bias, quality=quality, filt=filt, mode=mode)
    out = dict(criteria=CRIT, fs_base=FS, quality=quality, oversampling=st.os, prewarp_hz=st.prewarp_hz, filter=filt,
               model=model, params=PARAMS[model], bias_ma=bias, rk=st.rk, fir=getattr(st, "fir", None))
    if st.grid is not None:
        out["grid"] = st.grid
    if mode != "P1":
        out["mode"] = mode
    out["resampler"] = dict(latency_samples=st.latency_samples, latency_ms=st.rs.latency_ms,
                            macs_up_down=st.resampler_macs)
    out["op"] = op_record(st)
    r = ref(model, bias, mode)
    o = dict(op_rel={k: abs(out["op"][k] / r["op"][k] - 1) for k in OP_KEYS})
    o.update(ac_dev(st, r))
    out["ac_gain_1k_db"] = float(small_signal(st, [1000.0])[0][0])

    out["sine"], times, its = sines(quality, filt, model, T, bias=bias, mode=mode)
    o["harm"] = harm_dev(out["sine"], r)

    out["burst"], its["burst"] = burst(quality, filt, model, bias, mode)
    o["burst"] = burst_compare(out["burst"], r["burst"])
    out["vs"] = {model: o}
    out["iterations"] = its
    out["cpu"] = dict(method=f"time.perf_counter around Stage.process (pure Python, one core, 48 kHz base, "
                             f"{st.os}x OS, "
                             f"{filt} half-band resampling included); value = seconds of CPU per second of audio",
                      seconds_per_second=times)
    harm_ok = all(abs(h["dev_db"]) < CRIT["harm_db"] for a in AMPS for k, h in o["harm"][a].items()
                  if isinstance(h, dict) and h["tested"])
    harm_uncompared = any(o["harm"][a].get("spice_exp_capped") and any(
        isinstance(h, dict) and h["ref_dBc"] > CRIT["harm_floor_dbc"] for h in o["harm"][a].values()) for a in AMPS)
    out["passed"] = dict(   # True / False per criterion; None where the reference is not the same law (not compared)
        op=all(v < CRIT["op_rel"] for v in o["op_rel"].values()),
        ac_stage=o["ac_maxdev_db_stage"] < CRIT["ac_db"],
        ac_chain=o["ac_maxdev_db_chain"] < CRIT["ac_db"],
        harmonics=False if not harm_ok else (None if harm_uncompared else True),
        burst=None if o["burst"].get("spice_exp_capped") else burst_ok(o["burst"]),
        newton=all(v["converged"] for v in its.values()))
    if not st.supported:
        out["supported"] = False
    return out


def rescore():
    """Re-evaluate the burst comparison of every committed results[_grid][_<mode>].json case with burst_compare (#124),
    from its stored DK burst record and its reference's, and rewrite the files. Returns {file: {bias: {quality:
    (status before, status after)}}}."""
    out = {}
    for model, modes in (("dual", MODE_BIASES), ("dualg", GRID_BIASES),
                         *((m, BOUND_BIASES) for m in GRID_SHAPE)):
        for mode in modes:
            fn = results_file(mode, model)
            res = json.load(open(fn))
            rep = out.setdefault(os.path.basename(fn), {})
            for b, rb in res.items():
                for q, r in rb.items():
                    before = status(r)
                    o = r["vs"][r["model"]]
                    o["burst"] = burst_compare(r["burst"], ref(r["model"], float(b), mode)["burst"])
                    r["passed"]["burst"] = None if o["burst"].get("spice_exp_capped") else burst_ok(o["burst"])
                    rep.setdefault(b, {})[q] = (before, status(r))
            json.dump(res, open(fn, "w"), indent=1, default=float)
    return out


def status(res):
    """"passed" only if every criterion passed; "failed" if any failed; else "inconclusive" (a criterion not compared,
    e.g. where ngspice's exp() cap makes the reference a different law). A non-pass is never counted as a pass."""
    p = res["passed"].values()
    return "passed" if all(v is True for v in p) else "failed" if any(v is False for v in p) else "inconclusive"


def summary(res):
    o = res["vs"][res["model"]]
    lines = [json.dumps(dict(op=res["op"], passed=res["passed"]), default=float),
             f"op_rel {o['op_rel']} ac stage {o['ac_maxdev_db_stage']:.4f} dB @ {o['ac_worst_f_stage']:.0f} Hz, "
             f"chain {o['ac_maxdev_db_chain']:.4f} dB @ {o['ac_worst_f_chain']:.0f}; chain at {o['ac_chain_dev_at']}",
             f"burst {o['burst']}"]
    for a in AMPS:
        lines.append(f"{a}: " + str({k: (round(v['dev_db'], 5), v['tested']) if isinstance(v, dict) else round(v, 5)
                                     for k, v in o["harm"][a].items()}))
    lines.append(str({k: (round(v["mean"], 2), v["max"], v["nonconverged"]) for k, v in res["iterations"].items()}))
    lines.append(str(res["cpu"]["seconds_per_second"]))
    return "\n".join(lines)


def _run(a):
    q, b, mode, model = a
    return run(quality=q, bias=b, mode=mode, model=model)


def main(mode=MODE, model="dual", workers=1):
    if model in GRID_SHAPE and mode not in BOUND_BIASES:      # PR #139 review: nothing to run, so nothing written
        raise ValueError(f"{model} is a #131 grid-current bound, validated only in {sorted(BOUND_BIASES)}, not {mode}")
    jobs = [(q, b, mode, model) for b in biases_of(model, mode) for q in QUALITY]
    if mode == "P1" and model == "dual":
        jobs.append(((4, None), BIAS_MA, mode, model))
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            out = list(ex.map(_run, jobs))
    else:
        out = [_run(j) for j in jobs]
    res = {}
    for (q, b, _, _), r in zip(jobs, out):
        res.setdefault(f"{b:g}", {})["plain_4x" if isinstance(q, tuple) else q] = r
    json.dump(res, open(results_file(mode, model), "w"), indent=1, default=float)
    for b, rb in res.items():
        for q, r in rb.items():
            print(f"== {mode}, {b} mA, {q}: {r['oversampling']}x, Rk {r['rk']:.6g} ohm, resampler {r['resampler']}")
            print(summary(r))
    return {b: {q: (status(rb[q]), rb[q].get("supported", True)) for q in QUALITY} for b, rb in res.items()}


if __name__ == "__main__":
    import argparse
    if sys.argv[1:] == ["--rescore"]:
        print(json.dumps(rescore(), indent=1))
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("modes", nargs="*")
    ap.add_argument("--grid", action="store_true", help='model "dualg": the default stage, with grid current (#112)')
    ap.add_argument("--bounds", action="store_true",
                    help="models dualg_rsd2 / dualg_ehx1: the #131 grid-current bounds (experimental), T")
    ap.add_argument("--workers", type=int, default=1, help="processes (CPU time per run is then not one-core time)")
    a = ap.parse_args()
    if a.bounds:
        if set(a.modes) - set(BOUND_BIASES):
            ap.error(f"--bounds runs only {sorted(BOUND_BIASES)}, not {sorted(set(a.modes) - set(BOUND_BIASES))}")
        st = {f"{m}/{md}": main(md, m, a.workers) for m in GRID_SHAPE for md in (a.modes or list(BOUND_BIASES))}
    else:
        st = {m: main(m, "dualg" if a.grid else "dual", a.workers)
              for m in (a.modes or list(GRID_BIASES if a.grid else MODE_BIASES))}
    print(json.dumps(st))
    # Every supported setting must pass; unsupported (experimental) ones are recorded, never counted as passes.
    bad = {m: {b: v for b, v in sm.items() if any(s != "passed" and ok for s, ok in v.values())}
           for m, sm in st.items()}
    if any(bad.values()):
        sys.exit("supported settings not passed: " + str(bad))
