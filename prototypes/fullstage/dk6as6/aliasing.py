"""Aliasing of the 6AS6 stage (#102): dk/aliasing.py's metric (offset tones, coherent periodic-Hann analysis, total
non-harmonic energy 20 Hz-20 kHz in dBc, split by source) applied to dk6as6.stage.Stage.

Only the settling time differs from the 12AX7 stage's: the #102 coupling caps (100 nF into 1 MOhm, tau ~0.1 s; Cout
into Rl + the plate's ~0.2 MOhm, ~0.12 s) settle 4-5x slower than the 12AX7 stage's (~20-30 ms), and the stage
starts at its DC operating point, so a rectified DC shift at the plate decays through Cout during the window.
Measured (8 V, 0.45 mA, standard): in a 0.1 s window, dk's CI_SETTLE_S = 0.2 s leaves that drift at -82 dBc ("other"),
1.25 s at -157 dBc; in a 1 s window, 0.25 s leaves -128 dBc and 1.25 s -199 dBc. SETTLE_S = 1.25 s (~10 tau).
Modes (#111): each Distortion-switch mode (circuit.MODES) has its own sweep, aliasing.json (P1, unchanged since #120)
and aliasing_<mode>.json, and its own limits.
Grid current (#112): those sweeps are the stage at grid_hardness=0 (the #111 records). The default stage (g1 grid
current at pentode.HARDNESS) has its own: aliasing_grid.json (P1) and aliasing_grid_T.json, with LIMIT_DBC_GRID.
Run: python -m dk6as6.aliasing [--workers N] [--modes P1 T P2] [--grid] [--biases ...] [--tones ...]   (from
     prototypes/fullstage; the full sweep of every mode if none given -> dk6as6/aliasing[_grid][_<mode>].json)
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import FS, SWEEP_HZ, DRIVES, WINDOW_S, CI_WINDOW_S, BAND_HZ, MASK_BINS, tone_bin, alias_dbc, collision_order  # noqa: E402,F401,E501
from dk.aliasing import LIMIT_DBC as LIMIT_DBC_12AX7  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, BIAS_MA  # noqa: E402 -- after the sys.path setup
from dk6as6.circuit import MODES, MODE  # noqa: E402 -- after the sys.path setup
from dk6as6.pentode import HARDNESS, GRID_SHAPE  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import MODE_BIASES, GRID_BIASES  # noqa: E402 -- after the sys.path setup

SETTLE_S = 1.25
BIASES = (0.1, 0.25, BIAS_MA, 0.6)   # mA: the reference bias points (spice6as6/run_refs.BIASES["dual"], MODE_BIASES)


def json_file(mode=MODE, grid=False):
    """aliasing[_<mode>].json (grid_hardness 0, #111), aliasing_grid[_<mode>].json (the default grid current, #112)."""
    g = "_grid" if grid else ""
    return os.path.join(HERE, f"aliasing{g}.json" if mode == "P1" else f"aliasing{g}_{mode}.json")


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
    (0.3, 2.0): -132.0,           # -135.02 (resampler floor); 0.3 mA: the top of T's supported envelope
    (0.3, 8.0): -116.6,           # -119.64
    (0.35, 2.0): -130.3,          # -133.35 (resampler floor)
    (0.35, 8.0): -114.9,          # -117.98
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
LIMIT_DBC = {"P1": LIMIT_DBC_P1, "T": LIMIT_DBC_T, "P2": LIMIT_DBC_P2}   # regression baselines (worst + 3 dB)
# With the default g1 grid current (#112; aliasing_grid[_T].json, the same rule). Full 19-tone sweeps at T 0.45, 0.5
# (the top of T's envelope with grid current) and 0.6 mA; the five top tones (15037-19037 Hz) elsewhere: every full
# sweep of this stage so far has had its worst tone there (P1's, the no-grid T's and these), and P1's equal its no-grid
# sweep to the 0.01 dB printed (Ig1 < 1e-22 A).
LIMIT_DBC_GRID = {
    "P1": {                       # (bias mA, drive V): limit      worst measured (top five tones)
        (0.1, 2.0): -137.3,       # -140.31 at 19037 Hz (resampler floor), as without grid current
        (0.1, 8.0): -109.5,       # -112.50
        (0.25, 2.0): -144.9,      # -147.99
        (0.25, 8.0): -104.1,      # -107.18
        (BIAS_MA, 2.0): -127.3,   # -130.37
        (BIAS_MA, 8.0): -97.9,    # -100.96
        (0.6, 2.0): -120.5,       # -123.59
        (0.6, 8.0): -95.4,        # -98.44
    },
    "T": {
        (0.1, 2.0): -137.7,       # -140.73 at 19037 Hz (top five tones), as without grid current
        (0.1, 8.0): -122.2,       # -125.25
        (0.25, 2.0): -133.5,      # -136.55
        (0.25, 8.0): -118.0,      # -121.08
        (0.35, 2.0): -130.3,      # -133.35
        (0.35, 8.0): -92.0,       # -95.03 (internal; -117.98 without grid current)
        (BIAS_MA, 2.0): -126.3,   # -129.32 (full sweep)
        (BIAS_MA, 8.0): -63.1,    # -66.14 (full sweep; internal)
        (0.5, 2.0): -123.8,       # -126.84 (full sweep): the top of T's envelope with grid current
        (0.5, 8.0): -60.9,        # -63.92 at 17037 Hz (full sweep; internal)
        (0.55, 2.0): -106.6,      # -109.66 (top five tones; internal): above the gate, unsupported
        (0.55, 8.0): -57.3,       # -60.33 at 18037 Hz
        (0.6, 2.0): -84.4,        # -87.49 (full sweep; internal): above the gate, unsupported
        (0.6, 8.0): -54.2,        # -57.26
    },
}
GRID_MODES = tuple(LIMIT_DBC_GRID)
# The quality gate (#111 review), independent of any 6AS6 result: no supported setting may alias worse than the shipped
# 12AX7 stage's committed budget for the same setting, filter and drive (dk/aliasing.LIMIT_DBC, standard / FIR at
# 48 kHz, #6 / #96). It is an engineering budget, not an audibility threshold: the repo has no calibrated listening
# criterion and does not invent one (dk/RESULTS.md, #96 "A"). Supported settings: stage.SUPPORTED_MA.
QUALITY_GATE_DBC = LIMIT_DBC_12AX7[("standard", "fir")]          # {2.0: -118.3, 8.0: -48.6}
# The CI check of T and P2 (P1's is in the test): (mode, bias mA, drive V, the sweep's worst tone there)
CI_CASES_MODES = [("T", 0.3, 8.0, 19037), ("P2", 0.25, 8.0, 19037)]   # grid_hardness 0 (#111 records); T: its top
# ... and of the default stage, with grid current (#112): (mode, bias mA, drive V, the grid sweep's worst tone there):
# P1 at the default and at its top, T at the top of its envelope at both drives
CI_CASES_GRID = [("P1", BIAS_MA, 8.0, 19037), ("P1", 0.6, 8.0, 19037), ("T", 0.5, 2.0, 19037), ("T", 0.5, 8.0, 17037)]


def measure(drive, f, bias=BIAS_MA, quality="standard", filt="fir", window=WINDOW_S, settle=SETTLE_S, fs=FS,
            mode=MODE, grid_hardness=HARDNESS, grid_shape=GRID_SHAPE, fir="shipped"):
    """dk.aliasing.measure for the 6AS6 stage at `bias` mA, wired as `mode`, at `grid_hardness` (the default: the g1
    grid current; 0 for the #111 records) and `grid_shape` (the nominal, or a #131 bound, which the record names).
    fir: the stage's FIR design, "shipped" by default here: the resampler of the #111 / #112 sweeps and limits in this
    module (aliasing*.json); the stage's own default is k140 (#134)."""
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(f, n, fs)
    st = Stage(fs, bias=bias, quality=quality, filt=filt, mode=mode, experimental=True,   # support: stage.supported()
               grid_hardness=grid_hardness, grid_shape=grid_shape, fir=fir)
    L = st.latency_samples
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return dict(setting=quality, os=st.os, filt=filt, **({} if fir == "shipped" else dict(fir=fir)),
                bias_ma=bias, drive=float(drive), tone=int(f), fs_base=fs,
                f_hz=m * fs / n, window_s=window, settle_s=settle, **({} if mode == "P1" else dict(mode=mode)),
                **({} if not grid_hardness else dict(grid_hardness=grid_hardness, nonconverged=st.nonconv)),
                **({} if not grid_hardness or grid_shape == GRID_SHAPE else dict(grid_shape=grid_shape)),
                **alias_dbc(y, m, fs, st.os))


def output_relative(drive, f, bias=BIAS_MA, mode=MODE, window=WINDOW_S, settle=SETTLE_S, fs=FS, grid_hardness=0.0):
    """Supplementary to the dBc metric (whose denominator is the fundamental, which P2's frequency doubling suppresses):
    the same alias total relative to the stage's whole in-band output (fundamental + harmonics, 20 Hz-20 kHz), in dB.
    Returns the dBc total plus harmonics_re_fund_db and total_re_output_db = total_dbc - harmonics_re_fund_db."""
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(f, n, fs)
    st = Stage(fs, bias=bias, mode=mode, experimental=True, grid_hardness=grid_hardness, fir="shipped")  # the records'
    L = st.latency_samples
    y = st.process(drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n))["out"][n0 + L:n0 + L + n]
    a = alias_dbc(y, m, fs, st.os)
    P = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(n + 1)[:-1])) ** 2
    harm = sum(P[k * m - MASK_BINS:k * m + MASK_BINS + 1].sum() for k in range(1, n // (2 * m) + 1)
               if BAND_HZ[0] <= k * m * fs / n <= BAND_HZ[1])
    fund = P[m - MASK_BINS:m + MASK_BINS + 1].sum()
    h = float(10 * np.log10(harm / fund))
    return dict(mode=mode, bias_ma=bias, drive=float(drive), tone=int(f),
                total_dbc=a["total_dbc"], harmonics_re_fund_db=h, total_re_output_db=a["total_dbc"] - h)


def _job(a):
    return measure(*a[:2], **a[2])


def sweep(biases=BIASES, drives=DRIVES, tones=SWEEP_HZ, workers=None, **kw):
    jobs = [(d, f, dict(kw, bias=b)) for b in biases for d in drives for f in tones]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_job, jobs))
    return [_job(j) for j in jobs]


def _orel(a):
    return output_relative(*a)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--modes", nargs="*", default=list(MODES))
    ap.add_argument("--biases", nargs="*", type=float, default=None,
                    help="default: the mode's reference biases (run_refs.MODE_BIASES); a subset is merged in")
    ap.add_argument("--grid", action="store_true", help="the default stage's g1 grid current (#112) -> aliasing_grid*")
    ap.add_argument("--tones", nargs="*", type=int, default=None, help="default: the full sweep (SWEEP_HZ)")
    ap.add_argument("--output-relative", action="store_true",
                    help="only the supplementary output-relative figure at each mode / bias / drive's worst sweep tone "
                         "-> aliasing_output_relative.json")
    a = ap.parse_args()
    if a.output_relative:
        jobs = []
        for md in a.modes:
            rec = json.load(open(json_file(md)))
            for b in sorted({r["bias_ma"] for r in rec}):
                for d in DRIVES:
                    w = max((r for r in rec if r["bias_ma"] == b and r["drive"] == d), key=lambda r: r["total_dbc"])
                    jobs.append((d, w["tone"], b, md))
        with ProcessPoolExecutor(a.workers) as ex:
            rel = list(ex.map(_orel, jobs))
        json.dump(rel, open(os.path.join(HERE, "aliasing_output_relative.json"), "w"), indent=1)
        for r in rel:
            print(r)
        sys.exit(0)
    gh = HARDNESS if a.grid else 0.0
    for md in a.modes:
        bs = tuple(a.biases or (GRID_BIASES if a.grid else MODE_BIASES)[md])
        tones = tuple(a.tones or SWEEP_HZ)
        rec = sweep(biases=bs, tones=tones, workers=a.workers, mode=md, grid_hardness=gh)
        fn = json_file(md, a.grid)
        if os.path.exists(fn):          # merge into the committed sweep: replace the (bias, tone) points just run
            done = {(r["bias_ma"], r["tone"]) for r in rec}
            rec = sorted([r for r in json.load(open(fn)) if (r["bias_ma"], r["tone"]) not in done] + rec,
                         key=lambda r: (r["bias_ma"], r["drive"], r["tone"]))
        json.dump(rec, open(fn, "w"), indent=1)
        for b in bs:
            for d in DRIVES:
                w = max((r for r in rec if r["bias_ma"] == b and r["drive"] == d), key=lambda r: r["total_dbc"])
                print(f"{md} {b} mA, {d:g} V: worst {w['total_dbc']:.2f} dBc at {w['tone']} Hz (internal "
                      f"{w['internal_dbc']:.1f}, resampler {w['resampler_dbc']:.1f}, other {w['other_dbc']:.1f})")
