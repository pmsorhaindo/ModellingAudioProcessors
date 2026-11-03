#!/usr/bin/env python3
"""Run every experiment, write results/results.json, figures, and index.html.

Usage:  python scripts/run_all.py            (about 1-2 minutes)
"""
import json, os, sys, time
import numpy as np
from scipy.optimize import brentq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from valvelab import params as P
from valvelab.models import KorenTriode, KorenPentode, DempwolfTriode
from valvelab.stage import Stage
from valvelab.analysis import harmonic_profile
from valvelab import antialias as AA

OUT = os.path.join(ROOT, "results"); FIG = os.path.join(OUT, "figs")
os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({"figure.dpi": 110, "font.size": 9, "axes.grid": True, "grid.alpha": 0.3})

# Illustrative textbook stage values (NOT Culture Vulture values).
CONFIGS = {
    "12AX7 (Koren)": dict(make=lambda: Stage(KorenTriode("12AX7"), B=250, Rp=100e3, RL=1e6, Rs=33e3),
                          role="triode reference"),
    "12AX7 (Dempwolf-Zoelzer)": dict(make=lambda: Stage(DempwolfTriode(), B=250, Rp=100e3, RL=1e6, Rs=33e3),
                                     role="alternative triode model, same stage"),
    "12AU7 (Koren) - stand-in for 5963": dict(make=lambda: Stage(KorenTriode("12AU7"), B=250, Rp=22e3, RL=1e6, Rs=33e3),
                                              role="output-valve stand-in"),
    "EF86 (Koren-format) - stand-in for 6AS6": dict(make=lambda: Stage(KorenPentode("EF86"), B=250, Rp=100e3, RL=1e6, Rs=33e3, Vg2=100),
                                                    role="pentode / distortion-stage stand-in"),
}
SHORT = {"12AX7 (Koren)": "12AX7 Koren", "12AX7 (Dempwolf-Zoelzer)": "12AX7 Dempwolf-Zoelzer",
         "12AU7 (Koren) - stand-in for 5963": "12AU7 Koren (5963 stand-in)",
         "EF86 (Koren-format) - stand-in for 6AS6": "EF86 pentode (6AS6 stand-in)"}
COLORS = {k: c for k, c in zip(CONFIGS, ["C0", "C2", "C1", "C3"])}
Q_CENTRE = 0.55
Q_SWEEP = np.round(np.arange(0.40, 0.925, 0.025), 3)
R = {"generated": time.strftime("%Y-%m-%d %H:%M %Z"), "configs": {}}
FIGS = {}


def savefig(fig, name):
    path = os.path.join(FIG, name + ".png")
    fig.savefig(path, bbox_inches="tight"); plt.close(fig)
    FIGS[name] = path


# ---------------------------------------------------------------- F1 models
def fig_models():
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.6))
    va = np.linspace(0, 300, 301)
    k = KorenTriode("12AX7"); d = DempwolfTriode()
    for i, vg in enumerate([0, -0.5, -1, -1.5, -2, -2.5, -3]):
        ax[0].plot(va, 1e3 * k.ip(vg, va), color=f"C{i}", label=f"Vg={vg}")
        ax[0].plot(va, 1e3 * d.ip(vg, va), color=f"C{i}", ls="--")
    ax[0].plot(va, 1e3 * (250 - va) / 100e3, "k:", lw=1, label="load 250V/100k")
    ax[0].set(title="12AX7 plate curves: Koren (solid) vs Dempwolf-Zoelzer (dashed)",
              xlabel="Va (V)", ylabel="Ia (mA)", ylim=(0, 4)); ax[0].legend(fontsize=6)
    k2 = KorenTriode("12AU7")
    for i, vg in enumerate([0, -4, -8, -12, -16, -20]):
        ax[1].plot(va, 1e3 * k2.ip(vg, va), color=f"C{i}", label=f"Vg={vg}")
    ax[1].plot(va, 1e3 * (250 - va) / 22e3, "k:", lw=1, label="load 250V/22k")
    ax[1].set(title="12AU7 (Koren) - 5963 stand-in", xlabel="Va (V)", ylabel="Ia (mA)", ylim=(0, 15)); ax[1].legend(fontsize=6)
    p = KorenPentode("EF86")
    for i, vg in enumerate([0, -0.5, -1, -1.5, -2, -2.5, -3]):
        ax[2].plot(va, 1e3 * p.ip(vg, 100.0, va), color=f"C{i}", label=f"Vg1={vg}")
    ax[2].plot(va, 1e3 * (250 - va) / 100e3, "k:", lw=1, label="load 250V/100k")
    ax[2].set(title="EF86 (Koren-format), Vg2=100 V - 6AS6 stand-in", xlabel="Va (V)", ylabel="Ia (mA)", ylim=(0, 5)); ax[2].legend(fontsize=6)
    savefig(fig, "f1_plate_curves")
    # agreement between the two 12AX7 models in the region the stage uses
    vg = np.linspace(-3, 0, 31)[:, None]; vv = np.linspace(80, 250, 35)[None, :]
    ik, idz = k.ip(vg, vv), d.ip(vg, vv)
    m = ik > 0.1e-3
    R["model_agreement_12AX7"] = dict(
        region="Vg -3..0 V, Va 80..250 V, Ia(Koren) > 0.1 mA",
        median_abs_rel_diff_pct=float(100 * np.median(np.abs(idz[m] - ik[m]) / ik[m])),
        p90_abs_rel_diff_pct=float(100 * np.percentile(np.abs(idz[m] - ik[m]) / ik[m], 90)),
        max_abs_rel_diff_pct=float(100 * np.max(np.abs(idz[m] - ik[m]) / ik[m])))


# ------------------------------------------------------ DC points & sanity
def dc_points():
    out = {}
    for name, c in CONFIGS.items():
        s = c["make"]()
        # textbook cathode resistors for the DC table only
        s.Rk = {"12AX7 (Koren)": 1500, "12AX7 (Dempwolf-Zoelzer)": 1500,
                "12AU7 (Koren) - stand-in for 5963": 1000,
                "EF86 (Koren-format) - stand-in for 6AS6": 1500}[name]
        s.operating_point()
        out[name] = dict(B=s.B, Rp=s.Rp, Rk=s.Rk, RL=s.RL, Rs=s.Rs,
                         Vg2=(s.Vg2 if s.is_pentode else None),
                         Vk0=s.Vk0, Va0=s.Va0, Ia0_mA=1e3 * s.Ia0,
                         gain=float(s.small_signal_gain()))
    R["dc_points_textbook_Rk"] = out


# ----------------------------------------------------- F2 transfer vs bias
def drive_for_thd(s, target=0.10):
    return brentq(lambda a: harmonic_profile(s.transfer, a)["thd"] - target, 1e-3, 30.0, xtol=1e-4)


def fig_transfer():
    fig, ax = plt.subplots(1, 4, figsize=(13, 3.4))
    for j, (name, c) in enumerate(CONFIGS.items()):
        s = c["make"](); s.set_bias_va(Q_CENTRE * s.B)
        a10 = drive_for_thd(s)
        R["configs"].setdefault(name, {})["A10_centre_V"] = a10
        R["configs"][name]["centre"] = dict(q=Q_CENTRE, Vgk0=-s.Vk0, Rk=s.Rk, Va0=s.Va0, Ia0_mA=1e3 * s.Ia0,
                                            gain=float(s.small_signal_gain()))
        x = np.linspace(-3 * a10, 3 * a10, 801)
        for i, q in enumerate([0.45, 0.55, 0.70, 0.85]):
            s.set_bias_va(q * s.B)
            ax[j].plot(x, s.transfer(x), color=f"C{i}", label=f"Va0={q:.2f}B+ (Vgk0={-s.Vk0:.2f} V)")
        ax[j].set(title=SHORT[name], xlabel="input (V at grid, via Rs)", ylabel="output (V, AC plate swing)" if j == 0 else None)
        ax[j].legend(fontsize=6)
    savefig(fig, "f2_transfer_vs_bias")


# ------------------------------------------------ F3 THD vs drive at centre
def fig_thd_drive():
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.6))
    for name, c in CONFIGS.items():
        s = c["make"](); s.set_bias_va(Q_CENTRE * s.B)
        a10 = R["configs"][name]["A10_centre_V"]
        mult = np.logspace(-1.5, 1.0, 31)
        thd, h2, h3 = [], [], []
        for m in mult:
            hp = harmonic_profile(s.transfer, m * a10)
            thd.append(hp["thd_pct"]); h2.append(hp["dB"][1]); h3.append(hp["dB"][2])
        R["configs"][name]["thd_vs_drive"] = dict(drive_V=list(mult * a10), thd_pct=thd, H2_dB=h2, H3_dB=h3)
        ax[0].loglog(mult * a10, thd, color=COLORS[name], label=SHORT[name])
        ax[1].semilogx(mult, h2, color=COLORS[name], label=SHORT[name] + " H2")
        ax[1].semilogx(mult, h3, color=COLORS[name], ls="--")
    ax[0].set(title=f"THD vs drive, centre bias (Va0={Q_CENTRE}B+)", xlabel="input amplitude (V peak)", ylabel="THD (%)")
    ax[0].legend(fontsize=7)
    ax[1].set(title="H2 (solid) and H3 (dashed) re H1, vs drive / A10%", xlabel="drive / (drive giving 10% THD)", ylabel="dB re H1", ylim=(-90, 0))
    savefig(fig, "f3_thd_vs_drive")


# --------------------------------------------- F4 harmonic bars per bias
def fig_bars():
    biases = [("hot", 0.45), ("centre", 0.55), ("cold", 0.80)]
    names = list(CONFIGS)
    fig, ax = plt.subplots(len(names), 3, figsize=(12, 2.3 * len(names)), sharey=True)
    bars = {}
    for i, name in enumerate(names):
        s = CONFIGS[name]["make"](); a = 2.0 * R["configs"][name]["A10_centre_V"]
        bars[name] = {}
        for j, (lab, q) in enumerate(biases):
            s.set_bias_va(q * s.B)
            hp = harmonic_profile(s.transfer, a)
            db = hp["dB"][1:7]
            bars[name][lab] = dict(q=q, Vgk0=-s.Vk0, H2_H7_dB=[float(v) for v in db], thd_pct=hp["thd_pct"],
                                   even_minus_odd_dB=hp["even_minus_odd_dB"])
            colors = ["C0" if k % 2 == 0 else "C3" for k in range(2, 8)]
            ax[i, j].bar(range(2, 8), np.maximum(db, -100) + 100, bottom=-100, color=colors)
            ax[i, j].set_ylim(-100, 0)
            ax[i, j].set_title(f"{SHORT[name]} | {lab} (Va0={q}B+) THD {hp['thd_pct']:.1f}%", fontsize=7)
            if j == 0: ax[i, j].set_ylabel("dB re H1")
            if i == len(names) - 1: ax[i, j].set_xlabel("harmonic (blue even, red odd)")
    R["bars_drive_2xA10"] = bars
    savefig(fig, "f4_harmonic_bars")


# -------------------------------------- F5 even vs odd as a function of bias
def fig_bias_even_odd():
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.6))
    sweep = {}
    for name, c in CONFIGS.items():
        s = c["make"](); a10 = R["configs"][name]["A10_centre_V"]
        rows = {m: dict(eo=[], h2=[], h3=[], thd=[]) for m in (0.3, 1.0, 2.0)}
        vgk = []
        for q in Q_SWEEP:
            s.set_bias_va(q * s.B); vgk.append(-s.Vk0)
            for m in rows:
                hp = harmonic_profile(s.transfer, m * a10)
                rows[m]["eo"].append(hp["even_minus_odd_dB"]); rows[m]["h2"].append(hp["dB"][1])
                rows[m]["h3"].append(hp["dB"][2]); rows[m]["thd"].append(hp["thd_pct"])
        sweep[name] = dict(q=list(Q_SWEEP), Vgk0=vgk, by_drive={str(m): v for m, v in rows.items()})
        for k, m in enumerate((0.3, 1.0, 2.0)):
            ax[k].plot(Q_SWEEP, rows[m]["eo"], color=COLORS[name], label=SHORT[name])
    for k, m in enumerate((0.3, 1.0, 2.0)):
        ax[k].axhline(0, color="k", lw=0.8)
        ax[k].set_title(f"even - odd balance vs bias, drive = {m} x A10%", fontsize=8)
        ax[k].set( xlabel="quiescent plate Va0 / B+  (hot -> cold)",
                  ylabel="even - odd harmonic level (dB)" if k == 0 else None, ylim=(-30, 60))
    ax[0].legend(fontsize=6)
    R["bias_sweep"] = sweep
    savefig(fig, "f5_bias_even_odd")


# ------------------------------------------------------ F6 antialiasing
def antialias_study():
    name = "EF86 (Koren-format) - stand-in for 6AS6"
    s = CONFIGS[name]["make"](); s.set_bias_va(Q_CENTRE * s.B)
    a = 2.0 * R["configs"][name]["A10_centre_V"]
    tab = AA.Table(s.transfer, xmax=1.25 * a, m=4097)
    xt = np.linspace(-1.2 * a, 1.2 * a, 20001)
    tab_err = float(np.max(np.abs(tab.f(xt) - s.transfer(xt))))
    fs = 48000.0; n = 1 << 16; warm = 4096
    methods = [("naive", 1, "naive", "default"), ("2x OS", 2, "naive", "default"), ("4x OS", 4, "naive", "default"),
               ("8x OS", 8, "naive", "default"), ("ADAA1", 1, "adaa", "default"), ("ADAA1 + 2x OS", 2, "adaa", "default"),
               ("2x OS, long FIR", 2, "naive", "long"), ("8x OS, long FIR", 8, "naive", "long"),
               ("ADAA1 + 2x OS, long FIR", 2, "adaa", "long")]
    f0s = [AA.coherent_freq(f, fs, n) for f in (1000.0, 2500.0, 5000.0, 10000.0)]
    f_spec = f0s[2]
    # window/measurement floor: the pure input sine through the same metric
    t = np.arange(n + 2 * warm) / fs   # discard both ends: FIR start-up and tail
    table = {}
    spectra = {}
    floor = {}
    for f0 in f0s:
        x = a * np.sin(2 * np.pi * f0 * t)
        floor[f0] = AA.alias_metric(x[warm:warm + n], fs, f0)["alias_to_harm_dB"]
        # fundamental reference from the 8x run, for ADAA droop
        res = {}
        for lab, L, meth, fir in methods:
            y = AA.oversampled(tab, x, L, meth, fir)[warm:warm + n]
            m = AA.alias_metric(y, fs, f0)
            res[lab] = m
        ref = res["8x OS, long FIR"]["fund_power"]
        table[f0] = {lab: dict(alias_to_harm_dB=float(m["alias_to_harm_dB"]),
                               fund_rel_ref_dB=float(10 * np.log10(m["fund_power"] / ref)))
                     for lab, m in res.items()}
        if f0 == f_spec:
            spectra = res
    # per-sample cost in Python (median of 7 runs, 2^16 samples)
    x = a * np.sin(2 * np.pi * f_spec * np.arange(n) / fs)
    cost = {}
    for lab, L, meth, fir in methods:
        ts = []
        for _ in range(7):
            t0 = time.perf_counter(); AA.oversampled(tab, x, L, meth, fir); ts.append(time.perf_counter() - t0)
        cost[lab] = 1e9 * float(np.median(ts)) / n
    # cost of solving the stage directly with Newton (no table)
    ts = []
    for _ in range(3):
        t0 = time.perf_counter(); _, it = s.plate(x[:8192], return_iters=True); ts.append(time.perf_counter() - t0)
    cost["direct Newton solve (no table)"] = 1e9 * float(np.median(ts)) / 8192
    _, iters = s.plate(x[:8192], return_iters=True)
    R["antialias"] = dict(stage=name, bias_q=Q_CENTRE, amplitude_V=a, fs=fs, n_fft=n, window="rectangular, coherent sampling (f0 on an odd FFT bin)", f0s_Hz=f0s,
                          table_points=tab.m, table_max_abs_err_V=tab_err,
                          oversampler="scipy.signal.resample_poly, Kaiser(beta=5) FIR, 20L+1 taps, up and down",
                          measurement_floor_dB={str(k): float(v) for k, v in floor.items()},
                          results={str(k): v for k, v in table.items()},
                          python_ns_per_sample=cost, newton_iterations_vectorised=int(iters))
    # spectra figure
    fig, ax = plt.subplots(3, 3, figsize=(13, 8), sharex=True, sharey=True)
    for k, (lab, _, _, _) in enumerate(methods):
        m = spectra[lab]; a_ = ax.flat[k]
        a_.plot(m["freqs"] / 1e3, m["spectrum_dB"], lw=0.5)
        a_.set_title(f"{lab}: alias/harmonic = {m['alias_to_harm_dB']:.1f} dB", fontsize=8)
        a_.set_ylim(-200, 5); a_.set_xlim(0, 24)
        for h in range(1, 5):
            a_.axvline(h * f_spec / 1e3, color="g", lw=0.4, alpha=0.5)
    for a_ in ax[-1]: a_.set_xlabel("kHz")
    for a_ in ax[:, 0]: a_.set_ylabel("dB re peak")
    fig.suptitle(f"EF86 stand-in stage, {f_spec:.1f} Hz sine, fs=48 kHz, drive {a:.2f} V pk (green = true harmonics)", fontsize=9)
    savefig(fig, "f6_alias_spectra")
    # summary figure
    fig, ax = plt.subplots(1, 1, figsize=(7, 3.8))
    for lab, _, _, fir in methods:
        ax.plot([f / 1e3 for f in f0s], [table[f][lab]["alias_to_harm_dB"] for f in f0s], "o-" if fir == "default" else "s--", label=lab)
    ax.set(xlabel="input frequency (kHz)", ylabel="alias energy re harmonics (dB)", title="Aliasing vs method")
    ax.legend(fontsize=7)
    savefig(fig, "f7_alias_summary")
    # export table + one test signal for the C benchmark
    bench = os.path.join(ROOT, "bench")
    np.asarray(tab.fs, np.float32).tofile(os.path.join(bench, "table_f32.bin"))
    from scipy.signal import firwin
    for L in (2, 4, 8):
        h = firwin(2 * 10 * L + 1, 1.0 / L, window=("kaiser", 5.0)).astype(np.float32)
        h.tofile(os.path.join(bench, f"fir_L{L}_f32.bin"))
    with open(os.path.join(bench, "table_meta.txt"), "w") as fh:
        fh.write(f"{tab.m} {tab.xmax!r} {a!r} {f_spec!r}\n")


def c_bench():
    """Compile and run the scalar C cost benchmark, if a C compiler exists."""
    import subprocess
    src = os.path.join(ROOT, "bench", "adaa_bench.c"); exe = os.path.join(ROOT, "bench", "adaa_bench")
    try:
        subprocess.run(["gcc", "-O2", "-std=c11", "-D_POSIX_C_SOURCE=199309L", "-o", exe, src, "-lm"], check=True)
        out = subprocess.run([exe, "--dump"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
        cpu = subprocess.run("lscpu | grep 'Model name' | head -1", shell=True, capture_output=True, text=True).stdout.strip()
        R["c_bench"] = dict(cmd="gcc -O2 (scalar, no SIMD, no -ffast-math)", cpu=cpu, output=out)
    except Exception as e:  # noqa: BLE001
        R["c_bench"] = dict(error=str(e))


def demo_data():
    """Precomputed transfer curves for the in-browser demo (no solver in JS)."""
    demo = {}
    for name in ("12AX7 (Koren)", "EF86 (Koren-format) - stand-in for 6AS6"):
        s = CONFIGS[name]["make"](); a10 = R["configs"][name]["A10_centre_V"]
        u = np.linspace(-4, 4, 321)            # input in units of A10
        curves = []
        for q in Q_SWEEP:
            s.set_bias_va(q * s.B)
            curves.append([round(float(v), 4) for v in s.transfer(u * a10)])
        demo[SHORT[name]] = dict(A10=a10, u=[float(v) for v in u], q=[float(q) for q in Q_SWEEP], curves=curves)
    R["demo"] = demo


def main():
    t0 = time.time()
    fig_models(); print("models", time.time() - t0)
    dc_points(); print("dc", time.time() - t0)
    fig_transfer(); print("transfer", time.time() - t0)
    fig_thd_drive(); print("thd", time.time() - t0)
    fig_bars(); print("bars", time.time() - t0)
    fig_bias_even_odd(); print("bias", time.time() - t0)
    antialias_study(); print("aa", time.time() - t0)
    demo_data(); print("demo", time.time() - t0)
    c_bench(); print("c bench", time.time() - t0)
    R["param_sources"] = dict(koren_triode={k: {kk: v for kk, v in d.items() if kk != "source"} | {"source": d["source"]["url"]} for k, d in P.KOREN_TRIODE.items()},
                              koren_pentode={k: {kk: v for kk, v in d.items() if kk != "source"} | {"source": d["source"]["url"]} for k, d in P.KOREN_PENTODE.items()},
                              dempwolf={k: {kk: v for kk, v in d.items() if kk != "source"} | {"source": d["source"]["url"]} for k, d in P.DEMPWOLF_TRIODE.items()},
                              stand_ins=P.STAND_INS)
    R["figures"] = FIGS
    with open(os.path.join(OUT, "results.json"), "w") as fh:
        json.dump(R, fh, indent=1, default=float)
    print("wrote results.json in %.1fs" % (time.time() - t0))
    from valvelab.report import build
    build(ROOT); print("wrote index.html")


if __name__ == "__main__":
    main()
