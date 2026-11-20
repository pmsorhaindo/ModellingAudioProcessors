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

Run: python -m dk6as6.envelope [--workers N]   (from prototypes/fullstage) -> dk6as6/envelope.json (~10 min, 8 cores)
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
from dk6as6.stage import Stage, QUALITY, ExperimentalWarning, OutOfLawWarning  # noqa: E402 -- after the sys.path setup

FS = 48000.0
DRIVE_V = 8.0
FREQS = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 18000, 20000)   # Hz
PHASES = ("sin", "cos")
SCAN = {"P1": (0.1, 0.6), "T": (0.1, 0.25, 0.3, 0.35)}   # mA: P1's range ends; T's candidates around its top end
JSON = os.path.join(HERE, "envelope.json")


def stimulus(f, phase, latency, fs=FS):
    """5 ms of silence, the gated tone, then 20 ms of silence (plus the resampler latency)."""
    n = int(((0.4 if f < 200 else 0.1) * fs))
    n = int(round(max(1, round(2 * f * n / fs)) * fs / (2 * f)))       # a whole number of half cycles: stops at a peak
    x = DRIVE_V * (np.cos if phase == "cos" else np.sin)(2 * np.pi * f * np.arange(n) / fs)
    return np.r_[np.zeros(int(0.005 * fs)), x, np.zeros(int(0.02 * fs) + latency)]


def excursion(mode, bias, quality, f, phase):
    """(max Vg1k, min Vak) over the whole run at the internal rate, and the guard's count."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        warnings.simplefilter("ignore", OutOfLawWarning)
        st = Stage(FS, bias=bias, mode=mode, quality=quality, experimental=True)
        r = st.process(stimulus(f, phase, st.latency_samples), record=("vgk", "a", "k"))["_internal"]
    return dict(mode=mode, bias_ma=bias, quality=quality, f_hz=f, phase=phase, vg1k_max=float(r["vgk"].max()),
                vak_min=float((r["a"] - r["k"]).min()), out_of_law=st.out_of_law)


def _job(a):
    return excursion(*a)


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
            out[m][b] = dict(w, vak_min=min(r["vak_min"] for r in rr),
                             in_law=all(r["vg1k_max"] <= 0.0 and r["vak_min"] >= 0.0 for r in rr))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    jobs = [(m, b, q, f, ph) for m, bs in SCAN.items() for b in bs for q in QUALITY for f in FREQS for ph in PHASES]
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(_job, jobs))
    s = summary(rows)
    json.dump(dict(drive_v=DRIVE_V, freqs_hz=FREQS, phases=PHASES, rows=rows,
                   summary={m: {f"{b:g}": w for b, w in v.items()} for m, v in s.items()}), open(JSON, "w"), indent=1)
    for m, v in s.items():
        for b, w in sorted(v.items()):
            print(f"{m} {b:g} mA: in law {w['in_law']}; worst Vg1k {w['vg1k_max']:+.4f} V ({w['quality']}, "
                  f"{w['f_hz']} Hz, {w['phase']}); min Vak {w['vak_min']:.1f} V")
