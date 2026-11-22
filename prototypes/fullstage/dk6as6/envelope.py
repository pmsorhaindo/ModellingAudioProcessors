"""Where each Distortion-switch mode stays inside its valve law (#111, PR #121 review): the derivation of
stage.SUPPORTED_MA.

The valve law has no grid current and no positive-g1 data (fit6as6), and no electrode below the cathode collects
electrons, so a setting is "in the law" only if g1 never goes positive and the plate never goes below the cathode. Both
are read at the INTERNAL rate, on the voltages the solver actually sees, i.e. after the interpolator, whose onsets
overshoot the host signal (an 8 V peak 18 kHz sine from silence reaches 8.63 V at the grid side of the FIR).

Stimulus class (the supported use): host signals bounded by 8 V peak (validate.py's largest drive), as 1 kHz-grid tones
at FREQS over 20 Hz-20 kHz, each from silence at sine phase (a slope onset) and at cosine phase (a full 8 V step
onset, the worst found), gated off at a peak after a whole number of half cycles (a step offset), at standard 8x and
hq 16x. Tones below 200 Hz run 0.4 s (the 0.1 s coupling caps settle), the rest 0.1 s.
Signals outside the class (larger, or adversarial inter-sample peaks) are not covered by the derivation; the Stage's
runtime guard (Stage.out_of_law, OutOfLawWarning) counts every internal sample outside the law, whatever the input.

With grid current (#112; --grid, the default stage's pentode.HARDNESS -> envelope_grid.json, SCAN_GRID): g1 positive is
modelled (by the unvalidated grid law), so "in the law" is the grid law's: the plate never below the cathode, Ip + Ig2
never negative (Ik >= Ig1) and Newton converged on every sample. envelope.json stays the no-grid (#111) record.
Run: python -m dk6as6.envelope [--grid] [--workers N]   (from prototypes/fullstage) -> dk6as6/envelope[_grid].json
     (~10 min, 8 cores)
"""
import argparse
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk6as6.stage import Stage, QUALITY, ExperimentalWarning, OutOfLawWarning, NonConvergenceWarning  # noqa: E402
from dk6as6.pentode import HARDNESS, GRID_SHAPE  # noqa: E402 -- after the sys.path setup

FS = 48000.0
DRIVE_V = 8.0
FREQS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 18000, 20000)   # Hz
PHASES = ("sin", "cos")
SCAN = {"P1": (0.1, 0.6), "T": (0.1, 0.25, 0.3, 0.35)}   # mA: P1's range ends; T's candidates around its top end
SCAN_GRID = {"P1": (0.1, 0.6), "T": (0.1, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6)}   # with grid current (#112)
JSON = os.path.join(HERE, "envelope.json")
JSON_GRID = os.path.join(HERE, "envelope_grid.json")


def stimulus(f, phase, latency, fs=FS):
    """5 ms of silence, the gated tone, then 20 ms of silence (plus the resampler latency)."""
    n = int(((0.4 if f < 200 else 0.1) * fs))
    n = int(round(max(1, round(2 * f * n / fs)) * fs / (2 * f)))       # a whole number of half cycles: stops at a peak
    x = DRIVE_V * (np.cos if phase == "cos" else np.sin)(2 * np.pi * f * np.arange(n) / fs)
    return np.r_[np.zeros(int(0.005 * fs)), x, np.zeros(int(0.02 * fs) + latency)]


def excursion(mode, bias, quality, f, phase, grid_hardness=0.0, grid_shape=GRID_SHAPE):
    """(max Vg1k, min Vak, min Ip + Ig2) over the whole run at the internal rate, the guard's count and Newton's
    unconverged samples, at grid_hardness (0: the #111 law, the default here so envelope.json stays reproducible) and
    grid_shape (the nominal, or a #131 bound, which the row then names)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        warnings.simplefilter("ignore", OutOfLawWarning)
        warnings.simplefilter("ignore", NonConvergenceWarning)
        st = Stage(FS, bias=bias, mode=mode, quality=quality, experimental=True, grid_hardness=grid_hardness,
                   grid_shape=grid_shape)
        r = st.process(stimulus(f, phase, st.latency_samples), record=("vgk", "a", "k", "ipk"))["_internal"]
    return dict(mode=mode, bias_ma=bias, quality=quality, f_hz=f, phase=phase, vg1k_max=float(r["vgk"].max()),
                vak_min=float((r["a"] - r["k"]).min()), out_of_law=st.out_of_law,
                **({} if not grid_hardness else dict(grid_hardness=grid_hardness, ipk_min=float(r["ipk"].min()),
                                                     nonconverged=st.nonconv)),
                **({} if not grid_hardness or grid_shape == GRID_SHAPE else dict(grid_shape=grid_shape)))


def _job(a):
    return excursion(*a)


def in_law(r):
    """The law in force for the row: without grid current g1 <= 0 and Vak >= 0; with it Vak >= 0, Ip + Ig2 >= 0 and
    Newton converged (g1 > 0 is modelled)."""
    if r.get("grid_hardness"):
        return r["vak_min"] >= 0.0 and r["ipk_min"] >= 0.0 and r["nonconverged"] == 0
    return r["vg1k_max"] <= 0.0 and r["vak_min"] >= 0.0


def summary(rows):
    """{mode: {bias: worst row by vg1k_max, with in_law}}"""
    out = {}
    for r in rows:
        w = out.setdefault(r["mode"], {}).get(r["bias_ma"])
        if w is None or r["vg1k_max"] > w["vg1k_max"]:
            out[r["mode"]][r["bias_ma"]] = r
    for m in out:
        for b, w in out[m].items():
            rr = [r for r in rows if r["mode"] == m and r["bias_ma"] == b]
            out[m][b] = dict(w, vak_min=min(r["vak_min"] for r in rr), in_law=all(in_law(r) for r in rr),
                             **({} if "ipk_min" not in w else dict(ipk_min=min(r["ipk_min"] for r in rr),
                                                                   nonconverged=sum(r["nonconverged"] for r in rr))))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--grid", action="store_true", help="with the default grid current (#112) -> envelope_grid.json")
    a = ap.parse_args()
    gh, scan, fn = (HARDNESS, SCAN_GRID, JSON_GRID) if a.grid else (0.0, SCAN, JSON)
    jobs = [(m, b, q, f, ph, gh) for m, bs in scan.items() for b in bs for q in QUALITY for f in FREQS for ph in PHASES]
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(_job, jobs))
    s = summary(rows)
    json.dump(dict(drive_v=DRIVE_V, freqs_hz=FREQS, phases=PHASES, rows=rows,
                   summary={m: {f"{b:g}": w for b, w in v.items()} for m, v in s.items()}), open(fn, "w"), indent=1)
    for m, v in s.items():
        for b, w in sorted(v.items()):
            print(f"{m} {b:g} mA: in law {w['in_law']}; worst Vg1k {w['vg1k_max']:+.4f} V ({w['quality']}, "
                  f"{w['f_hz']} Hz, {w['phase']}); min Vak {w['vak_min']:.1f} V"
                  + (f"; min Ip+Ig2 {w['ipk_min']:.3g} A; unconverged {w['nonconverged']}" if "ipk_min" in w else ""))
