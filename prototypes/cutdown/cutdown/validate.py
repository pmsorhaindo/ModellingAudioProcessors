"""Validate the cut-down 12AX7 stage against ngspice references, full DK, and the aliasing sweep (#23).

Run from prototypes/cutdown:
    python -m cutdown.validate [--workers N]
Writes prototypes/cutdown/results.json.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
import time
import numpy as np

import cutdown  # noqa: F401 -- ensures FULLSTAGE_DIR is on sys.path
from analysis import db_rel, harmonics, thd  # noqa: E402 -- after cutdown path setup
from dk import aliasing as AL  # noqa: E402 -- after cutdown path setup
from dk import validate as DK_V  # noqa: E402 -- after cutdown path setup
from spice.run_refs import AMPS, burst_metrics  # noqa: E402 -- after cutdown path setup
from cutdown import linear  # noqa: E402 -- after cutdown path setup
from cutdown.stage import CutdownStage  # noqa: E402 -- after cutdown path setup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS_JSON = os.path.join(ROOT, "results.json")
FS = 48000.0


def aligned(stage, x, record=("out",)):
    """x must run stage.latency_samples past the analysis window; returns outputs aligned with x[:len(x) - L]."""
    L = stage.latency_samples
    r = stage.process(x, record)
    return {k: (v[L:] if k != "_internal" else v) for k, v in r.items()}


def small_signal(stage, f):
    """Linearised small-signal response (dB) of the 3-state stage alone and the whole 2x IIR chain."""
    f = np.asarray(f, float)
    Hs = np.abs(linear.stage_response(stage, f))
    Hc = Hs * np.abs(stage.rs.chain_response(f))
    return 20 * np.log10(Hs), 20 * np.log10(Hc)


def run(T=0.5, shape=(128, 32), interp="cubic0_lin1", solver_mode="lut"):
    """Run op, AC (20 Hz-20 kHz), sine harmonics (0.05/0.5/2/8 V), and 4 V burst against dz & koren refs."""
    kw = dict(shape=shape, interp=interp, solver_mode=solver_mode)
    st = CutdownStage(FS, **kw)
    out = dict(
        fs_base=FS, oversampling=st.os, filter=st.filt, shape=list(shape),
        table_words=st.table.word_count, interp=interp, solver_mode=solver_mode,
        resampler=dict(latency_samples=st.latency_samples, latency_ms=st.rs.latency_ms,
                       macs_up_down=st.resampler_macs),
        p_dc=st.p_dc.tolist(),
        p0_range=[st.table.p0_min, st.table.p0_max],
        p1_range=[st.table.p1_min, st.table.p1_max],
        table_dc_offset=[st.table.dig_off, st.table.dia_off],
    )
    dc = st.dc
    out["op"] = dict(va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ia=dc["ia"],
                     vgk=dc["vgk"], vg=dc["nodes"]["g"])

    refs = {m: DK_V.ref(m) for m in ("dz", "koren")}
    out["vs"] = {}
    for mname, r in refs.items():
        o = {}
        o["op_rel"] = {k: abs(out["op"][k] / r["op"][k] - 1) for k in ("va", "vk", "ia", "vgk")}
        f = np.array(r["ac"]["f"])
        sel = (f >= DK_V.CRIT["ac_band"][0]) & (f <= DK_V.CRIT["ac_band"][1])
        ds, dch = small_signal(st, f[sel])
        refdb = np.array(r["ac"]["db"])[sel]
        o["ac_maxdev_db_stage"] = float(np.max(np.abs(ds - refdb)))
        o["ac_maxdev_db_chain"] = float(np.max(np.abs(dch - refdb)))
        o["ac_worst_f_stage"] = float(f[sel][np.argmax(np.abs(ds - refdb))])
        o["ac_worst_f_chain"] = float(f[sel][np.argmax(np.abs(dch - refdb))])
        o["ac_dev_at"] = {str(int(fx)): float((ds - refdb)[np.argmin(abs(f[sel] - fx))])
                          for fx in (20, 100, 1000, 10000, 20000)}
        o["ac_chain_dev_at"] = {str(int(fx)): float((dch - refdb)[np.argmin(abs(f[sel] - fx))])
                                for fx in (20, 100, 1000, 10000, 20000)}
        out["vs"][mname] = o
    out["ac_gain_1k_db"] = float(small_signal(st, [1000.0])[0][0])

    out["sine"], times = {}, {}
    n = int(T * FS)
    for a in AMPS:
        s = CutdownStage(FS, **kw)
        t = np.arange(n + s.latency_samples) / FS
        t0 = time.perf_counter()
        y = aligned(s, float(a) * np.sin(2 * np.pi * 1000 * t))["out"]
        times[a] = (time.perf_counter() - t0) / (len(t) / FS)
        h = harmonics(y, FS, 1000.0, 100)
        out["sine"][a] = dict(H_peak_V=h[:5].tolist(), H_dBc=db_rel(h)[:5].tolist(), thd_pct=100 * thd(h))
    for mname, r in refs.items():
        o = out["vs"][mname]
        o["harm"] = {}
        for a in AMPS:
            rd = np.array(r["sine"][a]["H_dBc"])
            dd = np.array(out["sine"][a]["H_dBc"])
            rows = {}
            for hk in range(1, 5):
                rows[f"H{hk+1}"] = dict(
                    ref_dBc=float(rd[hk]), cutdown_dBc=float(dd[hk]),
                    dev_db=float(dd[hk] - rd[hk]),
                    tested_in_full_dk=bool(rd[hk] > DK_V.CRIT["harm_floor_dbc"]),
                )
            rows["H1_dev_db"] = float(20 * np.log10(out["sine"][a]["H_peak_V"][0] / r["sine"][a]["H_peak_V"][0]))
            rows["thd_ref_pct"] = r["sine"][a]["thd_pct"]
            rows["thd_cutdown_pct"] = out["sine"][a]["thd_pct"]
            o["harm"][a] = rows

    s = CutdownStage(FS, **kw)
    nb = int(0.12 * FS)
    tb = np.arange(nb + s.latency_samples) / FS
    xb = 4 * np.sin(2 * np.pi * 1000 * tb) * ((tb > 10e-3) & (tb < 30e-3))
    rb = aligned(s, xb, record=("out", "vgk"))
    vg = rb["_internal"]["vgk"]
    ti = (np.arange(len(vg)) - s.rs.up_delay_internal) / s.fs
    keep = (ti >= 0) & (ti < nb / FS)
    vg, ti = vg[keep], ti[keep]
    out["burst"] = burst_metrics(ti, vg)
    for mname, r in refs.items():
        o = out["vs"][mname]
        rb_ = r["burst"]
        shift_ref = rb_["min_vgk"] - rb_["rest_vgk"]
        tr = np.array(out["burst"]["traj_vgk"]) - out["burst"]["rest_vgk"]
        rr = np.array(rb_["traj_vgk"]) - rb_["rest_vgk"]
        o["burst"] = dict(
            min_rel=abs((out["burst"]["min_vgk"] - out["burst"]["rest_vgk"]) / shift_ref - 1),
            tau_rel=abs(out["burst"]["recovery_tau_s"] / rb_["recovery_tau_s"] - 1),
            traj_rel=float(np.sqrt(np.mean((tr - rr) ** 2)) / abs(shift_ref)),
            t_min_diff_ms=1e3 * (out["burst"]["t_min"] - rb_["t_min"]),
            ref=dict(rest=rb_["rest_vgk"], min=rb_["min_vgk"], tau=rb_["recovery_tau_s"]),
        )
    out["cpu"] = dict(seconds_per_second=times)
    return out


def measure_alias_point(args):
    """Measure one (drive, tone) aliasing point with dk.aliasing's exact offset-tone method."""
    drive, f, kw = args
    t0 = time.perf_counter()
    n, n0 = int(round(AL.WINDOW_S * FS)), int(round(AL.SETTLE_S * FS))
    m = AL.tone_bin(f, n, FS)
    st = CutdownStage(FS, **kw)
    L = st.latency_samples
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return dict(
        setting="cutdown", os=st.os, filt=st.filt, drive=float(drive), tone=int(f),
        f_hz=m * FS / n, window_s=AL.WINDOW_S, settle_s=AL.SETTLE_S,
        collision_order=AL.collision_order(m, n),
        cpu_s=time.perf_counter() - t0,
        **AL.alias_dbc(y, m, FS, st.os),
    )


def aliasing_sweep(workers=None, **kw):
    """Full 38-point offset-tone sweep (dk.aliasing.SWEEP_HZ x DRIVES)."""
    t0 = time.perf_counter()
    jobs = [(d, f, kw) for d in AL.DRIVES for f in AL.SWEEP_HZ]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            recs = list(ex.map(measure_alias_point, jobs))
    else:
        recs = [measure_alias_point(j) for j in jobs]
    worst = {}
    for d in AL.DRIVES:
        w = max((r for r in recs if r["drive"] == d), key=lambda r: r["total_dbc"])
        worst[f"{d:g}V"] = dict(
            worst_dbc=w["total_dbc"], tone=w["tone"],
            internal_dbc=w["internal_dbc"], resampler_dbc=w["resampler_dbc"],
            worst_component_dbc=w["worst_dbc"], worst_component_hz=w["worst_hz"],
        )
    return dict(
        method=dict(tones=AL.SWEEP_HZ, drives=AL.DRIVES, settle_s=AL.SETTLE_S, window_s=AL.WINDOW_S,
                    band_hz=AL.BAND_HZ, mask_bins=AL.MASK_BINS),
        wall_s=time.perf_counter() - t0,
        worst=worst,
        records=recs,
    )


def paper_opcount_estimate():
    """Paper arithmetic + memory op-count ESTIMATE per base-rate sample (not a measured 56300 cycle count)."""
    st = CutdownStage(FS)
    m = st.m
    nz_A = int(np.count_nonzero(np.abs(m["A"]) > 1e-15))
    nz_B0 = int(np.count_nonzero(np.abs(m["B"][:, 0]) > 1e-15))
    nz_C = int(np.count_nonzero(np.abs(m["C"]) > 1e-15))
    nz_D0 = int(np.count_nonzero(np.abs(m["D"][0]) > 1e-15))
    nz_E0 = int(np.count_nonzero(np.abs(m["E"][0, 0]) > 1e-15))
    nz_F0 = int(np.count_nonzero(np.abs(m["F"][0]) > 1e-15))
    nz_G = int(np.count_nonzero(np.abs(m["G"]) > 1e-15))
    nz_H0 = int(np.count_nonzero(np.abs(m["H"][:, 0]) > 1e-15))
    return dict(
        note="ESTIMATE of arithmetic & memory operations (issue #24 measures real DSP56300 cycles).",
        target_cycles_per_base_sample=300,
        slot_budget_cycles_per_base_sample=411,
        table_words=st.table.word_count,
        table_shape=list(st.shape),
        resampler_allpasses_up=st.resampler_macs[0],
        resampler_allpasses_down=st.resampler_macs[1],
        nonzero_counts=dict(A=nz_A, B0=nz_B0, C=nz_C, D0=nz_D0, E0=nz_E0, F0=nz_F0, G=nz_G, H0=nz_H0),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    args = ap.parse_args()

    t0 = time.perf_counter()
    cutdown_res = run(shape=(128, 32), interp="cubic0_lin1", solver_mode="lut")
    bilinear_res = run(shape=(64, 64), interp="bilinear", solver_mode="lut")
    newton3_res = run(shape=(128, 32), interp="cubic0_lin1", solver_mode="newton")
    eco_iir_res = DK_V.run(quality="eco", filt="iir")
    alias_res = aliasing_sweep(workers=args.workers)
    op_est = paper_opcount_estimate()

    full_dk = json.load(open(os.path.join(cutdown.FULLSTAGE_DIR, "dk", "results.json")))
    gt_64x = AL.load_ground_truth()["records"]

    # Build side-by-side aliasing table data
    combined_recs = alias_res["records"] + full_dk["aliasing"]["records"] + gt_64x
    summary = dict(
        wall_s=time.perf_counter() - t0,
        cutdown=cutdown_res,
        ablations=dict(bilinear_64x64=bilinear_res, newton_3state=newton3_res),
        full_dk_refs=dict(
            standard_fir=full_dk["standard"],
            eco_fir=full_dk["eco"],
            eco_iir=eco_iir_res,
        ),
        aliasing=alias_res,
        aliasing_table_md=AL.table(combined_recs),
        opcount_estimate=op_est,
    )
    with open(RESULTS_JSON, "w") as fh:
        json.dump(summary, fh, indent=1, default=float)
    print(f"Wrote {RESULTS_JSON} in {summary['wall_s']:.1f} s")
    print("Cutdown aliasing worst:", alias_res["worst"])
    print("Nonzero counts:", op_est["nonzero_counts"])


if __name__ == "__main__":
    main()
