"""Validate the 6AS6 DK stage against its same-model ngspice references (spice6as6/reference_dual_<bias>mA.json, #26).

  (ii) 'dual' : ngspice running the same fit6as6 dual_control equations and parameters (#55) on the same netlist ->
                isolates METHOD error. The pass criteria are dk/validate.CRIT, unchanged, tested against this only.
  There is no (i): no independent 6AS6 SPICE model in this repo's sources.
Same measurements as dk/validate.py (op point, small signal 20 Hz-20 kHz over the whole resampler chain, H2-H5 of 1 kHz
sines at spice/run_refs.AMPS, the 4 V tone-burst bias shift and recovery). Recovery tau is tested only where the
reference has one: with no cathode bypass cap and no grid current (#102) the cycle-mean Vgk returns with the burst's
end, and what is left is a ~0.07 % coupling-cap tail that ngspice's own trace does not take to 1/e in the 120 ms
window (recovery_tau_s = nan; dk6as6/RESULTS.md). The shift and trajectory criteria are unchanged.
run(model="r51") checks the DK stage with the #51 parameters against reference_r51_0.45mA.json the same way (for
dk6as6/compare_fits.py; informational).
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
Run: python -m dk6as6.validate [P1] [T] [P2]   (from prototypes/fullstage; every mode if none given). Writes, per mode,
dk6as6/results.json (P1) / results_<mode>.json: per reference bias, "standard" and "hq" against (ii), and for P1 at
the default bias "plain_4x": the same run at 4x without pre-warp, for information only (no 4x setting).
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
from spice6as6.run_refs import BIASES, MODE_BIASES, ref_file  # noqa: E402 -- after the sys.path setup

FS = 48000.0
PARAMS = {"dual": pentode.DUAL_CONTROL, "r51": pentode.REFIT_51}
OP_KEYS = ("va", "vk", "ia", "ig2", "ik", "vgk")
SPICE_EXP_CAP = 227.9559242   # ngspice 41 B-source exp() argument limit: ln(1 + exp(300)) evaluates to 227.9559


def exp_capped(stage, rec):
    """True if the DK run's Koren softplus argument passed ngspice's exp() cap anywhere (see the module docstring)."""
    p = stage.par
    x = p["KP"] * (1.0 / p["MU"] + rec["vgk"] / np.maximum(rec["vg2k"], 1e-3))
    return bool(np.max(x) > SPICE_EXP_CAP)


def ref(model, bias=BIAS_MA, mode=MODE):
    return json.load(open(ref_file(model, bias, mode)))


def results_file(mode=MODE):
    return os.path.join(HERE, "results.json" if mode == "P1" else f"results_{mode}.json")


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
        s = Stage(FS, bias=bias, quality=quality, filt=filt, pentode=PARAMS[model], mode=mode, experimental=True)
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


def burst(quality="standard", filt="fir", model="dual", bias=BIAS_MA, mode=MODE):
    """The 4 V 1 kHz tone burst (10-30 ms, 120 ms) through Stage.process: (burst_metrics of Vgk, iteration stats)."""
    s = Stage(FS, bias=bias, quality=quality, filt=filt, pentode=PARAMS[model], mode=mode, experimental=True)
    nb = int(0.12 * FS)
    tb = np.arange(nb + s.latency_samples) / FS
    xb = 4 * np.sin(2 * np.pi * 1000 * tb) * ((tb > 10e-3) & (tb < 30e-3))
    rb = aligned(s, xb, record=("out", "vgk", "vg2k"))
    vg = rb["_internal"]["vgk"]
    ti = (np.arange(len(vg)) - s.rs.up_delay_internal) / s.fs   # Vgk is seen after the interpolator only
    keep = (ti >= 0) & (ti < nb / FS)
    m = burst_metrics(ti[keep], vg[keep])
    if exp_capped(s, rb["_internal"]):
        m["spice_exp_capped"] = True
    return m, s.iteration_stats()


def tail_tau(burst, t0=31e-3, t1=118e-3):
    """Decay constant (s) of the burst's recovery tail: a log-linear fit of its 0.5 ms trajectory over t0..t1, after
    the cycle-mean window has left the burst (informational, beside CRIT's recovery tau, which starts at the 30.5 ms
    sample: that sample straddles the burst's end)."""
    t, v = np.array(burst["traj_t"]), np.array(burst["traj_vgk"]) - burst["rest_vgk"]
    m = (t >= t0) & (t <= t1)
    return float(-1.0 / np.polyfit(t[m], np.log(v[m]), 1)[0])


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
    st = Stage(FS, bias=bias, quality=quality, filt=filt, pentode=PARAMS[model], mode=mode, experimental=True)
    out = dict(criteria=CRIT, fs_base=FS, quality=quality, oversampling=st.os, prewarp_hz=st.prewarp_hz, filter=filt,
               model=model, params=PARAMS[model], bias_ma=bias, rk=st.rk)
    if mode != "P1":
        out["mode"] = mode
    out["resampler"] = dict(latency_samples=st.latency_samples, latency_ms=st.rs.latency_ms,
                            macs_up_down=st.resampler_macs)
    dc = st.dc
    out["op"] = dict(va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ia=dc["ip"], ig2=dc["ig2"], ik=dc["ik"], vgk=dc["vgk"],
                     vg=dc["nodes"]["g"], vg2k=dc["vg2k"], vg3k=dc["vg3k"], vak=dc["vak"])
    r = ref(model, bias, mode)
    o = dict(op_rel={k: abs(out["op"][k] / r["op"][k] - 1) for k in OP_KEYS})
    o.update(ac_dev(st, r))
    out["ac_gain_1k_db"] = float(small_signal(st, [1000.0])[0][0])

    out["sine"], times, its = sines(quality, filt, model, T, bias=bias, mode=mode)
    o["harm"] = harm_dev(out["sine"], r)

    out["burst"], its["burst"] = burst(quality, filt, model, bias, mode)
    rb_ = r["burst"]
    shift_ref = rb_["min_vgk"] - rb_["rest_vgk"]
    tr = np.array(out["burst"]["traj_vgk"]) - out["burst"]["rest_vgk"]
    rr = np.array(rb_["traj_vgk"]) - rb_["rest_vgk"]
    o["burst"] = dict(min_rel=abs((out["burst"]["min_vgk"] - out["burst"]["rest_vgk"]) / shift_ref - 1),
                      tau_rel=abs(out["burst"]["recovery_tau_s"] / rb_["recovery_tau_s"] - 1),
                      traj_rel=float(np.sqrt(np.mean((tr - rr) ** 2)) / abs(shift_ref)),
                      tau_tested=bool(np.isfinite(rb_["recovery_tau_s"])),
                      **({"spice_exp_capped": True} if out["burst"].get("spice_exp_capped") else {}),
                      **({"tail_tau_fit_rel": abs(tail_tau(out["burst"]) / tail_tau(rb_) - 1)}
                         if np.isfinite(rb_["recovery_tau_s"]) else {}),
                      t_min_diff_ms=1e3 * (out["burst"]["t_min"] - rb_["t_min"]),
                      ref=dict(rest=rb_["rest_vgk"], min=rb_["min_vgk"], tau=rb_["recovery_tau_s"]))
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
    burst_ok = (o["burst"]["min_rel"] < CRIT["burst_min_rel"] and o["burst"]["traj_rel"] < CRIT["burst_traj_rel"]
                and (not o["burst"]["tau_tested"] or o["burst"]["tau_rel"] < CRIT["burst_tau_rel"]))
    out["passed"] = dict(   # True / False per criterion; None where the reference is not the same law (not compared)
        op=all(v < CRIT["op_rel"] for v in o["op_rel"].values()),
        ac_stage=o["ac_maxdev_db_stage"] < CRIT["ac_db"],
        ac_chain=o["ac_maxdev_db_chain"] < CRIT["ac_db"],
        harmonics=False if not harm_ok else (None if harm_uncompared else True),
        burst=None if o["burst"].get("spice_exp_capped") else burst_ok,
        newton=all(v["converged"] for v in its.values()))
    if not st.supported:
        out["supported"] = False
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


def main(mode=MODE):
    biases = BIASES["dual"] if mode == "P1" else MODE_BIASES[mode]
    res = {f"{b:g}": {q: run(quality=q, bias=b, mode=mode) for q in QUALITY} for b in biases}
    if mode == "P1":
        res[f"{BIAS_MA:g}"]["plain_4x"] = run(quality=(4, None))
    json.dump(res, open(results_file(mode), "w"), indent=1, default=float)
    for b, rb in res.items():
        for q, r in rb.items():
            print(f"== {mode}, {b} mA, {q}: {r['oversampling']}x, Rk {r['rk']:.6g} ohm, resampler {r['resampler']}")
            print(summary(r))
    return {b: {q: (status(rb[q]), rb[q].get("supported", True)) for q in QUALITY} for b, rb in res.items()}


if __name__ == "__main__":
    st = {m: main(m) for m in (sys.argv[1:] or MODE_BIASES)}
    print(json.dumps(st))
    # Every supported setting must pass; unsupported (experimental) ones are recorded, never counted as passes.
    bad = {m: {b: v for b, v in sm.items() if any(s != "passed" and ok for s, ok in v.values())}
           for m, sm in st.items()}
    if any(bad.values()):
        sys.exit("supported settings not passed: " + str(bad))
