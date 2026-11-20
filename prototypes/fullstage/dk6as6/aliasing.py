"""Aliasing of the 6AS6 stage (#102): dk/aliasing.py's metric (offset tones, coherent periodic-Hann analysis, total
non-harmonic energy 20 Hz-20 kHz in dBc, split by source) applied to dk6as6.stage.Stage.

Only the settling time differs from the 12AX7 stage's: the #102 coupling caps (100 nF into 1 MOhm, tau ~0.1 s; Cout
into Rl + the plate's ~0.2 MOhm, ~0.12 s) settle 4-5x slower than the 12AX7 stage's (~20-30 ms), and the stage
starts at its DC operating point, so a rectified DC shift at the plate decays through Cout during the window.
Measured (8 V, 0.45 mA, standard): in a 0.1 s window, dk's CI_SETTLE_S = 0.2 s leaves that drift at -82 dBc ("other"),
1.25 s at -157 dBc; in a 1 s window, 0.25 s leaves -128 dBc and 1.25 s -199 dBc. SETTLE_S = 1.25 s (~10 tau).
Modes (#111): each Distortion-switch mode (circuit.MODES) has its own sweep, aliasing.json (P1, unchanged since #120)
and aliasing_<mode>.json, and its own limits.
Run: python -m dk6as6.aliasing [--workers N] [--modes P1 T P2]   (from prototypes/fullstage; the full sweep of every
     mode if none given -> dk6as6/aliasing[_<mode>].json)
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import FS, SWEEP_HZ, DRIVES, WINDOW_S, CI_WINDOW_S, tone_bin, alias_dbc, collision_order  # noqa: E402,F401
from dk6as6.stage import Stage, BIAS_MA  # noqa: E402 -- after the sys.path setup
from dk6as6.circuit import MODES, MODE  # noqa: E402 -- after the sys.path setup

SETTLE_S = 1.25
BIASES = (0.1, 0.25, BIAS_MA, 0.6)   # mA: the reference bias points (spice6as6/run_refs.BIASES["dual"], MODE_BIASES)


def json_file(mode=MODE):
    return os.path.join(HERE, "aliasing.json" if mode == "P1" else f"aliasing_{mode}.json")


JSON = json_file("P1")
# Pass limits, standard 8x / FIR / 48 kHz, dBc, per mode: the full sweep's worst total (aliasing[_<mode>].json, 1 s
# windows) plus dk.aliasing.MARGIN_DB (3 dB), rounded up to 0.1 dB, as dk.aliasing.LIMIT_DBC. P1: every worst tone is
# 19037 Hz; the CI window (CI_WINDOW_S, tone on 19040 Hz) reads within 0.11 dB of the full sweep there.
LIMIT_DBC_P1 = {                  # (bias mA, drive V): limit      worst measured
    (0.1, 2.0): -137.3,           # -140.31 (resampler floor)
    (0.1, 8.0): -109.5,           # -112.50
    (0.25, 2.0): -144.9,          # -147.99 (resampler floor)
    (0.25, 8.0): -104.1,          # -107.18
    (BIAS_MA, 2.0): -127.3,       # -130.37 (resampler floor)
    (BIAS_MA, 8.0): -97.9,        # -100.96
    (0.6, 2.0): -120.5,           # -123.59 (resampler floor)
    (0.6, 8.0): -95.4,            # -98.44, the worst of the range (least cathode feedback)
}
# T and P2 (#111), same rule. Worst tone 19037 Hz except where noted (the full sweep's worst, aliasing_<mode>.json).
LIMIT_DBC_T = {
    (0.1, 2.0): -137.7,           # -140.73 (resampler floor)
    (0.1, 8.0): -122.2,           # -125.25
    (0.25, 2.0): -133.5,          # -136.55
    (0.25, 8.0): -118.0,          # -121.08
    (BIAS_MA, 2.0): -126.3,       # -129.32
    (BIAS_MA, 8.0): -53.7,        # -56.71 at 18037 Hz: NOT a physical stage (g1 positive, plate driven below the
    (0.6, 2.0): -52.3,            # -55.38               cathode; dk6as6/RESULTS.md, Modes): a regression ratchet
    (0.6, 8.0): -51.7,            # -54.77 at 18037 Hz   only, not an audio-quality pass
}
LIMIT_DBC_P2 = {
    (0.1, 2.0): -94.2,            # -97.25 (resampler: the 8x FIR against P2's large harmonics)
    (0.1, 8.0): -78.2,            # -81.21 at 16037 Hz
    (0.25, 2.0): -88.2,           # -91.24
    (0.25, 8.0): -58.5,           # -61.55, the worst P2 point (internal -63.1, resampler -66.7)
    (BIAS_MA, 2.0): -86.0,        # -89.01
    (BIAS_MA, 8.0): -60.5,        # -63.50
    (0.6, 2.0): -86.5,            # -89.53
    (0.6, 8.0): -62.7,            # -65.77
}
LIMIT_DBC = {"P1": LIMIT_DBC_P1, "T": LIMIT_DBC_T, "P2": LIMIT_DBC_P2}
# The CI check of T and P2 (P1's is in the test): (mode, bias mA, drive V, the sweep's worst tone there)
CI_CASES_MODES = [("T", 0.25, 8.0, 19037), ("P2", 0.25, 8.0, 19037), ("P2", BIAS_MA, 2.0, 19037)]


def measure(drive, f, bias=BIAS_MA, quality="standard", filt="fir", window=WINDOW_S, settle=SETTLE_S, fs=FS,
            mode=MODE):
    """dk.aliasing.measure for the 6AS6 stage at `bias` mA, wired as `mode`."""
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(f, n, fs)
    st = Stage(fs, bias=bias, quality=quality, filt=filt, mode=mode)
    L = st.latency_samples
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return dict(setting=quality, os=st.os, filt=filt, bias_ma=bias, drive=float(drive), tone=int(f), fs_base=fs,
                f_hz=m * fs / n, window_s=window, settle_s=settle, **({} if mode == "P1" else dict(mode=mode)),
                **alias_dbc(y, m, fs, st.os))


def _job(a):
    return measure(*a[:2], **a[2])


def sweep(biases=BIASES, drives=DRIVES, tones=SWEEP_HZ, workers=None, **kw):
    jobs = [(d, f, dict(kw, bias=b)) for b in biases for d in drives for f in tones]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_job, jobs))
    return [_job(j) for j in jobs]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--modes", nargs="*", default=list(MODES))
    a = ap.parse_args()
    for md in a.modes:
        rec = sweep(workers=a.workers, mode=md)
        json.dump(rec, open(json_file(md), "w"), indent=1)
        for b in BIASES:
            for d in DRIVES:
                w = max((r for r in rec if r["bias_ma"] == b and r["drive"] == d), key=lambda r: r["total_dbc"])
                print(f"{md} {b} mA, {d:g} V: worst {w['total_dbc']:.2f} dBc at {w['tone']} Hz (internal "
                      f"{w['internal_dbc']:.1f}, resampler {w['resampler_dbc']:.1f}, other {w['other_dbc']:.1f})")
