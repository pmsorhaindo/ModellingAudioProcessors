"""Small-signal error budget (#38): where the 20 Hz-20 kHz deviation from reference (ii) comes from.

The deviation validate.small_signal measures, chain - reference (dB), splits exactly into four sources, each a
signed dB contribution (the rows add up to the total, to rounding):
  reference      exact analog response at ngspice's operating point (circuit.analog_response) - the ngspice .ac
                 data (spice/reference_dz.json): the JSON's 7-decimal rounding
  op point       analog response at the DK operating point - at ngspice's
  warping        discretised stage alone (linear.stage_response) - analog response, both at the DK op point
                 (eco: including its pre-warp)
  resampler      up -> down passband magnitude (FIR ripple; the IIR's magnitude is flat)
shown at the band edge (where the warping peaks), at 10.96 kHz (eco's overshoot) and at 1 kHz.
Measurement-only terms, not part of the total: the resampler counted a second time (what validate.small_signal did
before #38), and what test_e2e_response's multitone adds (intermodulation at 1 mV, log-f interpolation of the
reference at the tones). Also the internal-rate sweep 2x-128x of the warping alone (1/fs^2).
Run from prototypes/fullstage:  python -m dk.budget [--e2e]   (--e2e adds the time-domain measurement, ~20 s)
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dk.stage import Stage, QUALITY
from dk import validate as V, circuit, linear
from dk.triode import currents

FS = V.FS
AT_HZ = (20000.0, 10964.782, 1000.0)
SOURCES = ("reference", "op point", "warping", "resampler")
db = lambda h: 20 * np.log10(np.abs(h))


def band():
    r = V.ref("dz")["ac"]
    f = np.array(r["f"]); sel = (f >= V.CRIT["ac_band"][0]) & (f <= V.CRIT["ac_band"][1])
    return f[sel], np.array(r["db"])[sel]


def ngspice_jacobian(stage):
    """The valve Jacobian at reference (ii)'s operating point (same valve law and params as the stage)."""
    op = V.ref("dz")["op"]
    return np.array(currents(op["vgk"], op["va"] - op["vk"], stage.par)[2])


def rows(quality, filt="fir"):
    """(f, {source: dB contribution, ..., "total": chain - reference}) over the band; sum(SOURCES) == total."""
    f, ref = band()
    st = Stage(FS, quality=quality, filt=filt)
    A_ng = db(circuit.analog_response(f, ngspice_jacobian(st)))
    Ha = db(linear.analog_response(st, f))
    out = {"reference": A_ng - ref, "op point": Ha - A_ng, "warping": db(linear.stage_response(st, f)) - Ha,
           "resampler": db(st.rs.chain_response(f))}
    out["total"] = V.small_signal(st, f)[1] - ref
    return f, out


def e2e_terms(quality):
    """Measured (process(), the multitone of test_e2e_response) - linearised chain, worst over the tones at 1 mV
    and 0.1 mV; and the reference's log-f interpolation at the tones - exact analog response."""
    from dk.tests import test_e2e_response as E
    r = V.ref("dz")
    res = {}
    for amp in (1e-3, 1e-4):
        n0, N = int(E.SETTLE_S * FS), int(E.WIN_S * FS)
        st = Stage(FS, quality=quality)
        t = np.arange(n0 + N + st.latency_samples) / FS
        ph = np.random.default_rng(0).uniform(0, 2 * np.pi, len(E.TONES))
        x = sum(amp * np.sin(2 * np.pi * fr * t + p) for fr, p in zip(E.TONES, ph))
        y = V.aligned(st, x)["out"]
        k = np.rint(E.TONES * E.WIN_S).astype(int)
        Hm = np.fft.rfft(y[n0:n0 + N])[k] / np.fft.rfft(x[n0:n0 + N])[k]
        res[amp] = float(np.max(np.abs(db(Hm / E.linear_chain(st, E.TONES)))))
    interp = (np.interp(np.log(E.TONES), np.log(r["ac"]["f"]), r["ac"]["db"])
              - db(circuit.analog_response(E.TONES, ngspice_jacobian(st))))
    return res, float(np.max(np.abs(interp)))


def table(filt="fir"):
    data = {q: rows(q, filt) for q in QUALITY}
    lines = ["| source | " + " | ".join(f"{q} @ {fx / 1e3:.4g} kHz" for q in QUALITY for fx in AT_HZ) + " | "
             + " | ".join(f"{q} worst" for q in QUALITY) + " |"]
    lines.append("|---" * (1 + (len(AT_HZ) + 1) * len(QUALITY)) + "|")
    for name in SOURCES + ("total",):
        cells = []
        for q in QUALITY:
            f, o = data[q]
            cells += [f"{o[name][np.argmin(np.abs(f - fx))]:+.2e}" for fx in AT_HZ]
        worst = [f"{np.max(np.abs(data[q][1][name])):.2e}" for q in QUALITY]
        lines.append(f"| {name} | " + " | ".join(cells + worst) + " |")
    resid = max(np.max(np.abs(sum(o[s] for s in SOURCES) - o["total"])) for _, o in data.values())
    lines.append(f"\nrows - total: {resid:.0e} dB (rounding). Resampler counted twice (before #38), extra at the "
                 + ", ".join(f"{q} worst tone {data[q][1]['resampler'][np.argmax(np.abs(data[q][1]['total']))]:+.1e} dB"
                             for q in QUALITY))
    return "\n".join(lines)


def rate_sweep(factors=(2, 4, 8, 16, 32, 64, 128)):
    """Warping of the stage alone (no pre-warp) vs internal rate, and its 1/fs^2 extrapolation to fs -> inf."""
    f, ref = band()
    lines = ["| internal rate | worst, stage alone | at 20 kHz | ratio to previous |", "|---|---|---|---|"]
    prev, e = None, {}
    for os_ in factors:
        e[os_] = V.small_signal(Stage(FS, quality=(os_, None)), f)[0] - ref
        k = np.argmax(np.abs(e[os_]))
        lines.append(f"| {os_}x ({os_ * FS / 1e3:g} kHz) | {abs(e[os_][k]):.6f} dB at {f[k]:.0f} Hz | "
                     f"{e[os_][-1]:+.6f} | {'' if prev is None else f'{prev / e[os_][-1]:.3f}'} |")
        prev = e[os_][-1]
    a, b = factors[-2], factors[-1]
    ex = (4 * e[b] - e[a]) / 3
    lines.append(f"\n1/fs^2 extrapolation from {a}x and {b}x (fs -> inf): worst {np.max(np.abs(ex)):.1e} dB")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true", help="also run the time-domain multitone (test_e2e_response)")
    a = ap.parse_args()
    print(table())
    print()
    print(rate_sweep())
    for q in QUALITY:
        f, o = rows(q, "iir")
        print(f"{q} iir: resampler worst {np.max(np.abs(o['resampler'])):.1e} dB; total {np.max(np.abs(o['total'])):.5f} dB")
    if a.e2e:
        for q in QUALITY:
            m, interp = e2e_terms(q)
            print(f"e2e {q}: measured - linearised chain {m[1e-3]:.2e} dB at 1 mV, {m[1e-4]:.2e} at 0.1 mV; "
                  f"reference interpolation at the tones {interp:.2e} dB")
