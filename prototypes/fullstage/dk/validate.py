"""Validate the DK stage against both ngspice references (spice/reference_{dz,koren}.json).

  (ii) 'dz'    : same Dempwolf-Zoelzer equations and params as the DK model -> isolates METHOD error.
                 The pass criteria (tightened in #5, see dk/RESULTS.md) are tested against this.
  (i)  'koren' : Koren 12AX7 -> adds MODEL mismatch; reported for information only.
Run: python -m dk.validate   (from prototypes/fullstage). Writes dk/results.json.
"""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from analysis import harmonics, thd, db_rel
from spice.run_refs import burst_metrics, AMPS
from dk.stage import Stage, OS
from dk.triode import currents

FS = 48000.0
# #5 tightened op 1% -> 0.1%, harmonics 1 dB above -100 dBc -> 0.1 dB above -90 dBc, burst 5/10/5% -> 0.5/1/0.5%,
# each with >= 16x margin on the measured error against the tight-tolerance reference (dk/RESULTS.md).
CRIT = dict(op_rel=0.001, ac_db=0.1, ac_band=(20.0, 20000.0), harm_db=0.1, harm_floor_dbc=-90.0,
            burst_min_rel=0.005, burst_tau_rel=0.01, burst_traj_rel=0.005)
# Stage.process streams through stateful resamplers (#14): its output lags the input by stage.latency_samples
# (base rate). Each run below generates its input latency_samples longer than the window it analyses and drops
# the first latency_samples of output, so out[n] lines up with x[n] and the analysed samples never depend on
# input past the end of the signal (that edge was the #2 artefact; this replaces its GUARD_S trim).


def aligned(stage, x, record=("out",)):
    """x must run stage.latency_samples past the analysis window; returns outputs aligned with x[:len(x) - L]."""
    L = stage.latency_samples
    r = stage.process(x, record)
    return {k: (v[L:] if k != "_internal" else v) for k, v in r.items()}


def ref(model):
    return json.load(open(os.path.join(ROOT, "spice", f"reference_{model}.json")))


def small_signal(stage, f):
    """Exact small-signal response of the discretised DK stage at its op point (stage only),
    and with the interpolation + decimation filters (whole chain)."""
    m, dc = stage.m, stage.dc
    _, _, J = currents(dc["vgk"], dc["vak"], stage.par)
    Ji = np.array(J)
    M = np.linalg.inv(np.eye(2) + m["K"] @ Ji)
    G, H = m["G"], m["H"][:, :1]
    Al = m["A"] + m["C"] @ Ji @ M @ G
    Bl = m["B"][:, :1] + m["C"] @ Ji @ M @ H
    Cl = m["D"][:1] + m["F"][:1] @ Ji @ M @ G
    Dl = m["E"][:1, :1] + m["F"][:1] @ Ji @ M @ H
    z = np.exp(2j * np.pi * np.asarray(f) / stage.fs)
    n = Al.shape[0]
    Hs = np.array([(Cl @ np.linalg.solve(zz * np.eye(n) - Al, Bl) + Dl)[0, 0] for zz in z])
    Hh = np.abs(stage.rs.chain_response(np.asarray(f, float))) if stage.rs is not None else 1.0
    return 20 * np.log10(np.abs(Hs)), 20 * np.log10(np.abs(Hs) * Hh ** 2)


def run(T=0.5, os=OS, filt="fir"):
    out = dict(criteria=CRIT, fs_base=FS, oversampling=os, filter=filt)
    st = Stage(FS, os=os, filt=filt)
    out["resampler"] = dict(latency_samples=st.latency_samples, latency_ms=st.rs.latency_ms,
                            macs_up_down=st.rs.macs())
    dc = st.dc
    out["op"] = dict(va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ia=dc["ia"], vgk=dc["vgk"], vg=dc["nodes"]["g"])
    R = {m: ref(m) for m in ("dz", "koren")}
    out["vs"] = {}
    for mname, r in R.items():
        o = {}
        o["op_rel"] = {k: abs(out["op"][k] / r["op"][k] - 1) for k in ("va", "vk", "ia", "vgk")}
        f = np.array(r["ac"]["f"]); sel = (f >= CRIT["ac_band"][0]) & (f <= CRIT["ac_band"][1])
        ds, dch = small_signal(st, f[sel])
        refdb = np.array(r["ac"]["db"])[sel]
        o["ac_maxdev_db_stage"] = float(np.max(np.abs(ds - refdb)))
        o["ac_maxdev_db_chain"] = float(np.max(np.abs(dch - refdb)))
        o["ac_worst_f_stage"] = float(f[sel][np.argmax(np.abs(ds - refdb))])
        o["ac_worst_f_chain"] = float(f[sel][np.argmax(np.abs(dch - refdb))])
        o["ac_dev_at"] = {str(int(fx)): float((ds - refdb)[np.argmin(abs(f[sel] - fx))]) for fx in (20, 100, 1000, 10000, 20000)}
        o["ac_chain_dev_at"] = {str(int(fx)): float((dch - refdb)[np.argmin(abs(f[sel] - fx))]) for fx in (20, 100, 1000, 10000, 20000)}
        out["vs"][mname] = o
    out["ac_gain_1k_db"] = float(small_signal(st, [1000.0])[0][0])

    out["sine"], times, its = {}, {}, {}
    n = int(T * FS)
    for a in AMPS:
        s = Stage(FS, os=os, filt=filt)
        t = np.arange(n + s.latency_samples) / FS
        t0 = time.perf_counter()
        y = aligned(s, float(a) * np.sin(2 * np.pi * 1000 * t))["out"]
        times[a] = (time.perf_counter() - t0) / (len(t) / FS)
        h = harmonics(y, FS, 1000.0, 100)
        out["sine"][a] = dict(H_peak_V=h[:5].tolist(), H_dBc=db_rel(h)[:5].tolist(), thd_pct=100 * thd(h))
        its[a] = s.iteration_stats()
    for mname, r in R.items():
        o = out["vs"][mname]; o["harm"] = {}
        for a in AMPS:
            rd = np.array(r["sine"][a]["H_dBc"]); dd = np.array(out["sine"][a]["H_dBc"])
            rows = {}
            for hk in range(1, 5):          # H2..H5
                rows[f"H{hk+1}"] = dict(ref_dBc=float(rd[hk]), dk_dBc=float(dd[hk]), dev_db=float(dd[hk] - rd[hk]),
                                        tested=bool(rd[hk] > CRIT["harm_floor_dbc"]))
            rows["H1_dev_db"] = float(20 * np.log10(out["sine"][a]["H_peak_V"][0] / r["sine"][a]["H_peak_V"][0]))
            rows["thd_ref_pct"] = r["sine"][a]["thd_pct"]; rows["thd_dk_pct"] = out["sine"][a]["thd_pct"]
            o["harm"][a] = rows

    s = Stage(FS, os=os, filt=filt)
    nb = int(0.12 * FS); tb = np.arange(nb + s.latency_samples) / FS
    xb = 4 * np.sin(2 * np.pi * 1000 * tb) * ((tb > 10e-3) & (tb < 30e-3))
    rb = aligned(s, xb, record=("out", "vgk"))
    vg = rb["_internal"]["vgk"]
    ti = (np.arange(len(vg)) - s.rs.up_delay_internal) / s.fs   # Vgk is seen after the interpolator only
    keep = (ti >= 0) & (ti < nb / FS); vg, ti = vg[keep], ti[keep]
    out["burst"] = burst_metrics(ti, vg)
    its["burst"] = s.iteration_stats()
    for mname, r in R.items():
        o = out["vs"][mname]; rb_ = r["burst"]
        shift_ref = rb_["min_vgk"] - rb_["rest_vgk"]
        tr = np.array(out["burst"]["traj_vgk"]) - out["burst"]["rest_vgk"]
        rr = np.array(rb_["traj_vgk"]) - rb_["rest_vgk"]
        o["burst"] = dict(min_rel=abs((out["burst"]["min_vgk"] - out["burst"]["rest_vgk"]) / shift_ref - 1),
                          tau_rel=abs(out["burst"]["recovery_tau_s"] / rb_["recovery_tau_s"] - 1),
                          traj_rel=float(np.sqrt(np.mean((tr - rr) ** 2)) / abs(shift_ref)),
                          t_min_diff_ms=1e3 * (out["burst"]["t_min"] - rb_["t_min"]),
                          ref=dict(rest=rb_["rest_vgk"], min=rb_["min_vgk"], tau=rb_["recovery_tau_s"]))
    out["iterations"] = its
    out["cpu"] = dict(method=f"time.perf_counter around Stage.process (pure Python, one core, 48 kHz base, {os}x OS, "
                             f"{filt} half-band resampling included); value = seconds of CPU per second of audio",
                      seconds_per_second=times)
    o = out["vs"]["dz"]
    out["pass_vs_dz"] = dict(
        op=all(v < CRIT["op_rel"] for v in o["op_rel"].values()),
        ac_stage=o["ac_maxdev_db_stage"] < CRIT["ac_db"],
        ac_chain=o["ac_maxdev_db_chain"] < CRIT["ac_db"],
        harmonics=all(abs(h["dev_db"]) < CRIT["harm_db"] for a in AMPS for k, h in o["harm"][a].items()
                      if isinstance(h, dict) and h["tested"]),
        burst=(o["burst"]["min_rel"] < CRIT["burst_min_rel"] and o["burst"]["tau_rel"] < CRIT["burst_tau_rel"]
               and o["burst"]["traj_rel"] < CRIT["burst_traj_rel"]))
    return out


def summary(res):
    lines = [json.dumps(dict(op=res["op"], pass_vs_dz=res["pass_vs_dz"]), default=float)]
    for m in ("dz", "koren"):
        o = res["vs"][m]
        lines.append(f"{m}: op_rel {o['op_rel']} ac stage {o['ac_maxdev_db_stage']:.3f} dB @ {o['ac_worst_f_stage']:.0f} Hz, "
                     f"chain {o['ac_maxdev_db_chain']:.3f} dB @ {o['ac_worst_f_chain']:.0f}; at {o['ac_dev_at']} chain {o['ac_chain_dev_at']}")
        lines.append(f"{m}: burst {o['burst']}")
        for a in AMPS:
            lines.append(f"{m} {a}: " + str({k: (round(v['dev_db'], 3), v['tested']) if isinstance(v, dict) else round(v, 3)
                                            for k, v in o["harm"][a].items()}))
    lines.append(str({k: (round(v["mean"], 2), v["max"], v["nonconverged"]) for k, v in res["iterations"].items()}))
    lines.append(str(res["cpu"]["seconds_per_second"]))
    lines.append(str({k: res["burst"][k] for k in ("rest_vgk", "min_vgk", "t_min", "recovery_tau_s")}))
    return "\n".join(lines)


if __name__ == "__main__":
    res = run()
    # informational: CPU at 4x on the same machine/run for comparison (criteria not evaluated)
    r4 = run(os=4)
    res["cpu"]["seconds_per_second_at_4x"] = r4["cpu"]["seconds_per_second"]
    res["at_4x_for_comparison"] = dict(ac_maxdev_db_chain=r4["vs"]["dz"]["ac_maxdev_db_chain"],
                                       iterations={k: (v["mean"], v["max"]) for k, v in r4["iterations"].items()})
    json.dump(res, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=float)
    print(summary(res))
