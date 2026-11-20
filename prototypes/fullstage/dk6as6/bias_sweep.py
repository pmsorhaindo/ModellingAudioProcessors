"""The 6AS6 stage across the bias range (#102, #110): Rk, operating point, small-signal gain and the 1 kHz harmonics at
a fixed 3 V peak output (the #102 research comment's sensitivity level), at
  "biases": target currents over 0.1-0.6 mA, 5 uA apart over 0.2-0.3 mA to locate the THD minimum (the manuals'
            lowest-distortion setting is 0.25-0.3 mA);
  "knobs" : every Bias knob position 1-11 (stage.knob_bias), adding the bias meter's readings (stage.bias_point) and
            the Newton record at standard and hq on dk6as6/validate.py's own signals (the 1 kHz sines at
            spice/run_refs.AMPS and the 4 V burst): #110 asks for zero non-converged samples across the range.
Each 3 V point: the DK stage at standard 8x, a 1 kHz sine for T seconds (harmonics over the last 100 cycles, as
dk6as6/validate.py; T = 1 s leaves the 0.1 s coupling caps settled), the drive scaled from the small-signal gain and
then corrected once by the measured fundamental, so H1 is 3 V peak at the output to ~0.1 %.
Run: python -m dk6as6.bias_sweep   (from prototypes/fullstage) -> dk6as6/bias_sweep.json
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from analysis import harmonics, thd, db_rel  # noqa: E402 -- after the sys.path setup
from dk.validate import aligned  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, QUALITY, knob_bias, bias_point  # noqa: E402 -- after the sys.path setup
from dk6as6 import linear, validate  # noqa: E402 -- after the sys.path setup

FS = 48000.0
BIASES = tuple(sorted({0.1, 0.15, 0.35, 0.4, 0.45, 0.5, 0.6} | {round(0.2 + 0.005 * k, 3) for k in range(21)}))   # mA
KNOBS = tuple(range(1, 12))
OUT_PEAK_V = 3.0
T = 1.0


def tone(st, a):
    t = np.arange(int(T * FS) + st.latency_samples) / FS
    st.reset()
    return harmonics(aligned(st, a * np.sin(2 * np.pi * 1000 * t))["out"], FS, 1000.0, 100)


def point(bias):
    st = Stage(FS, bias=bias)
    g = abs(linear.analog_response(st, [1000.0])[0])
    a = OUT_PEAK_V / g
    h = tone(st, a)
    a *= OUT_PEAK_V / h[0]
    h = tone(st, a)
    dc = st.dc
    return dict(bias_ma=bias, rk=st.rk, va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ip=dc["ip"], ig2=dc["ig2"],
                gain_1k_db=float(20 * np.log10(g)), drive_V_peak=a, H1_V_peak=float(h[0]),
                H_dBc=db_rel(h)[1:5].tolist(), thd_pct=100 * thd(h))


def knob(k):
    b = knob_bias(k)
    newton = {}
    for q in QUALITY:
        its = list(validate.sines(q, bias=b)[2].values()) + [validate.burst(q, bias=b)[1]]
        newton[q] = dict(samples=sum(s["samples"] for s in its), nonconverged=sum(s["nonconverged"] for s in its),
                         max_iterations=max(s["max"] for s in its))
    p = point(b)
    return dict(knob=k, meter=bias_point(b), gain_1k_db=p["gain_1k_db"], drive_V_peak=p["drive_V_peak"],
                H_dBc=p["H_dBc"], thd_pct=p["thd_pct"], newton=newton)


if __name__ == "__main__":
    with ProcessPoolExecutor() as ex:
        knobs = ex.map(knob, KNOBS)
        rows = list(ex.map(point, BIASES))
        knobs = list(knobs)
    best = min(rows, key=lambda r: r["thd_pct"])
    json.dump(dict(biases=rows, knobs=knobs, thd_min=dict(bias_ma=best["bias_ma"], thd_pct=best["thd_pct"])),
              open(os.path.join(HERE, "bias_sweep.json"), "w"), indent=1)
    print("| bias mA | Rk kOhm | Va V | Vk V | gain dB | drive V pk | H2 dBc | H3 dBc | THD % |")
    for r in rows:
        print(f"| {r['bias_ma']:g} | {r['rk'] / 1e3:.2f} | {r['va']:.2f} | {r['vk']:.3f} | {r['gain_1k_db']:.2f} | "
              f"{r['drive_V_peak']:.3f} | {r['H_dBc'][0]:.1f} | {r['H_dBc'][1]:.1f} | {r['thd_pct']:.4f} |")
    print(f"THD minimum on the grid: {best['thd_pct']:.4f} % at {best['bias_ma']:g} mA")
    print("| knob | Ik mA | Rk kOhm | Vk V | Va V | Ip mA | Ig2 mA | gain dB | THD % at 3 V | Newton nonconv std / hq "
          "| max it std / hq |")
    for r in knobs:
        m, n = r["meter"], r["newton"]
        print(f"| {r['knob']} | {1e3 * m['ik']:.4f} | {m['rk'] / 1e3:.2f} | {m['vk']:.4f} | {m['va']:.2f} | "
              f"{1e3 * m['ip']:.4f} | {1e3 * m['ig2']:.4f} | {r['gain_1k_db']:.2f} | {r['thd_pct']:.4f} | "
              f"{n['standard']['nonconverged']} / {n['hq']['nonconverged']} | "
              f"{n['standard']['max_iterations']} / {n['hq']['max_iterations']} |")
