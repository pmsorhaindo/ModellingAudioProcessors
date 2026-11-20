"""The #102 stage across the bias range (informational): Rk, operating point, small-signal gain, and the 1 kHz harmonics
at a fixed 3 V peak output (the research comment's sensitivity level), for the RESULTS.md sanity check against the
issue's static-method numbers (THD minimum ~0.24-0.25 mA in P1; 0.02-0.2 % THD at 3 V for 0.25-0.4 mA; small-signal
gain +11 to +17 dB at 0.15-0.3 mA).
Each bias: the DK stage at standard 8x, a 1 kHz sine for T seconds (harmonics over the last 100 cycles, as
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
from dk6as6.stage import Stage  # noqa: E402 -- after the sys.path setup
from dk6as6 import linear  # noqa: E402 -- after the sys.path setup

FS = 48000.0
BIASES = (0.1, 0.15, 0.2, 0.225, 0.25, 0.275, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6)   # mA
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


if __name__ == "__main__":
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(point, BIASES))
    json.dump(rows, open(os.path.join(HERE, "bias_sweep.json"), "w"), indent=1)
    print("| bias mA | Rk kOhm | Va V | Vk V | gain dB | drive V pk | H2 dBc | H3 dBc | THD % |")
    for r in rows:
        print(f"| {r['bias_ma']:g} | {r['rk'] / 1e3:.2f} | {r['va']:.2f} | {r['vk']:.3f} | {r['gain_1k_db']:.2f} | "
              f"{r['drive_V_peak']:.3f} | {r['H_dBc'][0]:.1f} | {r['H_dBc'][1]:.1f} | {r['thd_pct']:.4f} |")
