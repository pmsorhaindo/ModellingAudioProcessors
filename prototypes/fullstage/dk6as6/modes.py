"""The 6AS6 stage's Distortion-switch modes (#111): what each wiring does, and where it leaves the valve fit.

For each mode of circuit.MODES (P1 the default; T; P2 our hypothesis) at target currents over the documented
0.1-0.6 mA range, the DK stage (standard 8x, FIR, 48 kHz):
  "dc"      Rk, the operating point, the small-signal 1 kHz gain and its polarity (phase of v(out)/v(src)), and the
            20 kHz response relative to 1 kHz (the UA manual's "slight HF roll-off ... except in P2");
  "tones"   1 kHz sines at DRIVES V peak (validate.py's amplitudes plus a finer grid for P2): H1 at the output, H2-H5
            in dBc, THD, the even / odd balance (H2, H4 against H3, H5 energy), Newton non-convergence, and the swing
            of every controlling voltage over the analysed cycles against the fit's validity range (FIT_RANGE);
  "p2"      P2's documented traits (M-ORIG 3, M-MP p.6, UA, SOS; dk6as6/RESULTS.md "Modes"): the lowest drive at which
            H2 >= H1 (frequency doubling) and the drive above which H1 falls as the drive rises (inverted dynamics),
            per current.
Harmonics as validate.py (a 0.5 s sine, the last 100 cycles), swings over the same 100 cycles.
Vg2k in T is read as Va - Vk: the Rstrap drop (100 Ohm x Ig2) is left out, under 0.1 V while Ig2 < 1 mA.
Run: python -m dk6as6.modes   (from prototypes/fullstage) -> dk6as6/modes.json
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from analysis import harmonics, thd, db_rel  # noqa: E402 -- after the sys.path setup
from spice.run_refs import AMPS  # noqa: E402 -- after the sys.path setup
from dk.validate import aligned  # noqa: E402 -- after the sys.path setup
from dk6as6.circuit import MODES, VALUES  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage  # noqa: E402 -- after the sys.path setup
from dk6as6 import linear  # noqa: E402 -- after the sys.path setup

FS = 48000.0
T = 0.5
# grid_hardness=0: the #111 record (no g1 grid current). The default stage's grid current (#112) changes T at
# >= 0.35 mA and high drive (and nothing else above rounding): dk6as6/grid.py, grid.json.
GRID_HARDNESS = 0.0
BIASES = (0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6)          # mA
DRIVES = tuple(sorted({float(a) for a in AMPS} | {1.0, 1.5, 3.0, 4.0, 6.0}))   # V peak at the input
# The range of the data the #55 fit was fitted to (fit6as6/README.md, "Limitations"): Ec2 60-140 V, Ec1 -5 to 0 V,
# Ec3 about -9 to +12 V (to -14 V only at Ec2 = 120 V, Ec1 = -2 V), Eb 20-435 V. No positive-g1 data, no grid current.
FIT_RANGE = dict(vg1k=(-5.0, 0.0), vg2k=(60.0, 140.0), vg3k=(-9.0, 12.0), vak=(20.0, 435.0))


def dc(mode, bias):
    st = Stage(FS, bias=bias, mode=mode, experimental=True, grid_hardness=GRID_HARDNESS)
    h, h20 = linear.analog_response(st, [1000.0, 20000.0])
    d = st.dc
    return dict(mode=mode, bias_ma=bias, rk=st.rk, va=d["nodes"]["a"], vk=d["nodes"]["k"],
                vg1k=d["vg1k"],
                vg2k=d["vg2k"], vg3k=d["vg3k"], vak=d["vak"], ip=d["ip"], ig2=d["ig2"],
                gain_1k_db=float(20 * np.log10(abs(h))), phase_1k_deg=float(np.degrees(np.angle(h))),
                hf_20k_re_1k_db=float(20 * np.log10(abs(h20 / h))))


def swings(mode, nodes):
    """(min, max) of each controlling voltage over the analysed window, from the internal-rate node voltages."""
    va, vk, vg1k = nodes["a"], nodes["k"], nodes["vgk"]
    pins = MODES[mode]["pins"]
    v = dict(vg1k=vg1k, vak=va - vk,
             vg2k=(va if MODES[mode]["screen"] == "strap" else VALUES["EC2"]) - vk,
             vg3k={"k": 0.0 * vk, None: -vk, "g2": va - vk}[pins["G3"]])
    return {k: (float(x.min()), float(x.max())) for k, x in v.items()}


def outside(sw):
    """{voltage: (min, max)} of the swings that leave FIT_RANGE."""
    return {k: s for k, s in sw.items() if s[0] < FIT_RANGE[k][0] or s[1] > FIT_RANGE[k][1]}


def tone(mode, bias, drive):
    st = Stage(FS, bias=bias, mode=mode, experimental=True, grid_hardness=GRID_HARDNESS)
    t = np.arange(int(T * FS) + st.latency_samples) / FS
    r = aligned(st, drive * np.sin(2 * np.pi * 1000 * t), record=("out", "a", "k", "vgk"))
    h = harmonics(r["out"], FS, 1000.0, 100)
    n = len(r["_internal"]["a"])
    keep = slice(n - int(100 * st.fs / 1000.0), n)                   # the last 100 cycles at the internal rate
    sw = swings(mode, {k: r["_internal"][k][keep] for k in ("a", "k", "vgk")})
    d = db_rel(h)
    even, odd = np.hypot(h[1], h[3]), np.hypot(h[2], h[4])
    return dict(mode=mode, bias_ma=bias, drive=drive, H1_V_peak=float(h[0]), H_dBc=d[1:5].tolist(),
                thd_pct=100 * thd(h), even_minus_odd_db=float(20 * np.log10(even / odd)),
                nonconverged=st.nonconv, swing=sw, outside_fit=outside(sw))


def p2_traits(tones, bias):
    rows = sorted((r for r in tones if r["mode"] == "P2" and r["bias_ma"] == bias), key=lambda r: r["drive"])
    doubling = next((r["drive"] for r in rows if r["H_dBc"][0] >= 0.0), None)
    peak = max(rows, key=lambda r: r["H1_V_peak"])
    return dict(bias_ma=bias, doubling_from_drive=doubling, h1_peak_drive=peak["drive"],
                h1_peak_V=peak["H1_V_peak"], inverted_above_drive=peak["drive"] if peak is not rows[-1] else None)


def _tone(a):
    return tone(*a)


def _dc(a):
    return dc(*a)


if __name__ == "__main__":
    modes = list(MODES)
    with ProcessPoolExecutor() as ex:
        dcs = list(ex.map(_dc, [(m, b) for m in modes for b in BIASES]))
        tones = list(ex.map(_tone, [(m, b, d) for m in modes for b in BIASES for d in DRIVES]))
    p2 = [p2_traits(tones, b) for b in BIASES]
    json.dump(dict(biases=BIASES, drives=DRIVES, fit_range=FIT_RANGE, dc=dcs, tones=tones, p2=p2),
              open(os.path.join(HERE, "modes.json"), "w"), indent=1)
    print("| mode | bias mA | Rk kOhm | Va V | Vk V | Vg2k V | Vg3k V | Ip mA | Ig2 mA | gain dB | phase deg "
          "| 20k dB |")
    for r in dcs:
        print(f"| {r['mode']} | {r['bias_ma']:g} | {r['rk'] / 1e3:.3f} | {r['va']:.2f} | {r['vk']:.3f} | "
              f"{r['vg2k']:.2f} | {r['vg3k']:.3f} | {1e3 * r['ip']:.4f} | {1e3 * r['ig2']:.4f} | "
              f"{r['gain_1k_db']:.2f} | {r['phase_1k_deg']:.1f} | {r['hf_20k_re_1k_db']:.3f} |")
    print("| mode | bias | drive | H1 V | H2 | H3 | H4 | H5 dBc | THD % | even-odd dB | nonconv | outside fit |")
    for r in tones:
        o = ", ".join(f"{k} {a:.1f}..{b:.1f}" for k, (a, b) in r["outside_fit"].items())
        print(f"| {r['mode']} | {r['bias_ma']:g} | {r['drive']:g} | {r['H1_V_peak']:.4g} | "
              + " | ".join(f"{x:.1f}" for x in r["H_dBc"])
              + f" | {r['thd_pct']:.4g} | {r['even_minus_odd_db']:.1f} | {r['nonconverged']} | {o} |")
    print(p2)
