"""Aliasing of the 6AS6 stage with the post-distortion low-pass (#103) off / 4k / 7k: dk/aliasing.py's metric and full
sweep (SWEEP_HZ, WINDOW_S window), standard 8x, FIR resampler, 2 V and 8 V, at 48 and 44.1 kHz, at every reference
bias (dk6as6.aliasing.BIASES, 0.1-0.6 mA since #110), after dk6as6.aliasing.SETTLE_S (1.25 s, validated there for
the #102 stage's slow coupling caps: dk's 0.2-0.25 s leaves the startup drift at -82 dBc in a 0.1 s window, -128 dBc
in 1 s).

One stage run per point: the stage's internal output is decimated directly ("off") and through each PostFilter
position, which is what lpf.Stage does (test_lpf.py checks the equivalence). Reported per point and position:
  total_dbc, internal_dbc, resampler_dbc, other_dbc
                       alias_dbc's total and its split by source, relative to that output's own fundamental (the
                       repo metric). "other" is energy on no harmonic fold, e.g. a settling transient: it shows
                       whether a point measures aliasing or the stage's startup.
  abs_db_re_off_fund   the total relative to the UNFILTERED fundamental: the absolute comparison (abs_other_db the
                       same for "other"). The filter cuts the fundamental too (14.9 dB at 19 kHz in the 7k position,
                       22.8 dB in 4k), so total_dbc rises with the filter on while the absolute total falls.
Run from prototypes/fullstage:  python -m dk6as6.lpf_aliasing   (writes dk6as6/lpf_aliasing.json)
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import tone_bin, alias_dbc, MASK_BINS, SWEEP_HZ, WINDOW_S  # noqa: E402 -- sys.path
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk6as6 import lpf, stage  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S, BIASES, BIAS_MA, MODE  # noqa: E402 -- after the sys.path setup

RATES, DRIVES = (48000, 44100), (2.0, 8.0)
SOURCES = ("total", "internal", "resampler", "other")
OUT_JSON = os.path.join(HERE, "lpf_aliasing.json")


def fundamental_db(y, m):
    """The fundamental's energy (dB), windowed and masked exactly as alias_dbc measures it."""
    P = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(len(y) + 1)[:-1])) ** 2
    return float(10 * np.log10(P[m - MASK_BINS:m + MASK_BINS + 1].sum()))


def measure(fs, drive, f, bias=BIAS_MA, window=WINDOW_S, settle=SETTLE_S, quality="standard", mode=MODE,
            fir_up=None, fir_down=None, grid_hardness=0.0):
    """One stage run, decimated directly ("off") and through each PostFilter position. quality / mode as Stage
    (settings outside stage.SUPPORTED_MA run with experimental=True). fir_up / fir_down: experimental interpolator /
    decimator designs (dk.resample.design's fir=; None = the shipped FIR), for #113's stopband study. grid_hardness:
    0.0 by default, the #110 record (P1: Ig1 < 1e-15 A, #112); pentode.HARDNESS for the default stage's grid current."""
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(f, n, fs)
    st = stage.Stage(fs, bias=bias, quality=quality, mode=mode, experimental=True, grid_hardness=grid_hardness)
    if fir_up is not None:
        st.rs = Resampler(fs, st.os, "fir", fir=fir_up)
    L = max(st.latency_samples, Resampler(fs, st.os, "fir", fir=fir_down).latency_samples)
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    yi = st.process_internal(st.rs.up(x))["out"]
    out = dict(fs=fs, bias_ma=bias, drive=drive, tone=f, f_hz=m * fs / n, window_s=window, settle_s=settle)
    for p in lpf.POSITIONS:
        y = Resampler(fs, st.os, "fir", fir=fir_down).down(lpf.PostFilter(st.fs, p).process(yi))[n0 + L:n0 + L + n]
        a = alias_dbc(y, m, fs, st.os)
        out[p] = {f"{s}_dbc": a[f"{s}_dbc"] for s in SOURCES}
        out[p].update(worst_dbc=a["worst_dbc"], worst_hz=a["worst_hz"], fund_db=fundamental_db(y, m))
    for p in lpf.POSITIONS:
        shift = out[p]["fund_db"] - out["off"]["fund_db"]
        out[p].update(abs_db_re_off_fund=out[p]["total_dbc"] + shift, abs_other_db=out[p]["other_dbc"] + shift)
    return out


def _job(a):
    return measure(*a)


def summary(recs):
    rows = []
    for fs in RATES:
        for b in BIASES:
            for d in DRIVES:
                r = [x for x in recs if x["fs"] == fs and x["bias_ma"] == b and x["drive"] == d]
                row = dict(fs=fs, bias_ma=b, drive=d)
                for p in lpf.POSITIONS:
                    w = max(r, key=lambda x: x[p]["total_dbc"])
                    wa = max(r, key=lambda x: x[p]["abs_db_re_off_fund"])
                    row[p] = dict(worst_dbc=w[p]["total_dbc"], worst_tone=w["tone"],
                                  worst_abs_db=wa[p]["abs_db_re_off_fund"], worst_abs_tone=wa["tone"],
                                  max_abs_other_db=max(x[p]["abs_other_db"] for x in r))
                row["max_on_minus_off_db"] = max(x[p]["abs_db_re_off_fund"] - x["off"]["abs_db_re_off_fund"]
                                                 for x in r for p in lpf.F0_HZ)
                rows.append(row)
    return rows


if __name__ == "__main__":
    jobs = [(fs, d, f, b) for fs in RATES for b in BIASES for d in DRIVES for f in SWEEP_HZ]
    with ProcessPoolExecutor(os.cpu_count()) as ex:
        recs = list(ex.map(_job, jobs))
    rows = summary(recs)
    json.dump(dict(method=dict(settle_s=SETTLE_S, window_s=WINDOW_S, quality="standard", filt="fir", q=lpf.Q,
                               f0_hz=lpf.F0_HZ, prewarp_hz=lpf.PREWARP_HZ, biases_ma=BIASES),
                   summary=rows, records=recs), open(OUT_JSON, "w"), indent=1)
    for r in rows:
        print(f"{r['fs']} Hz {r['bias_ma']} mA {r['drive']:g} V: " + " | ".join(
            f"{p} worst {r[p]['worst_dbc']:.1f} dBc @ {r[p]['worst_tone']}, abs {r[p]['worst_abs_db']:.1f} dB"
            f" @ {r[p]['worst_abs_tone']}, other <= {r[p]['max_abs_other_db']:.1f}" for p in lpf.POSITIONS)
              + f" | max(on - off) {r['max_on_minus_off_db']:.2f} dB")
