"""Harmonic profile of the 6AS6 stage with the #51 vs the #55 valve fit (#26; informational, not a pass criterion).

Same circuit, same DK method and quality (standard, 8x), only the valve parameters change:
  #55 'dual' : fit_results["dual_control"]  (KR = 0.2737, XA = 0.5743; the stage's default)
  #51 'r51'  : fit_results["refit_51"]      (no KR / XA, i.e. KR = 0, XA = 1)
For each: the operating point, the 1 kHz small-signal gain, and H2-H5 (dBc) and THD of the 1 kHz sines at
spice/run_refs.AMPS. Each DK run is checked against its own same-model ngspice reference with dk/validate.CRIT
(validate.run(model=...)), so the difference between the columns is the fits', not the method's.
Run: python -m dk6as6.compare_fits   (from prototypes/fullstage). Writes dk6as6/fit_comparison.json.
"""
import json
import os
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from spice.run_refs import AMPS  # noqa: E402 -- after the sys.path setup
from dk6as6 import validate as V  # noqa: E402 -- after the sys.path setup

LABEL = {"r51": "#51 refit_51", "dual": "#55 dual_control"}


def compare(quality="standard"):
    runs = {m: V.run(quality=quality, model=m) for m in ("r51", "dual")}
    out = dict(quality=quality, oversampling=runs["dual"]["oversampling"], amps_V_peak=AMPS, models={})
    for m, r in runs.items():
        out["models"][m] = dict(
            label=LABEL[m], params=r["params"], op=r["op"], ac_gain_1k_db=r["ac_gain_1k_db"],
            harmonics={a: dict(zip(("H2", "H3", "H4", "H5"), r["sine"][a]["H_dBc"][1:5]),
                               thd_pct=r["sine"][a]["thd_pct"],
                               H1_peak_V=r["sine"][a]["H_peak_V"][0]) for a in AMPS},
            vs_own_reference=dict(passed=r["passed"], op_rel=r["vs"][m]["op_rel"],
                                  ac_maxdev_db_chain=r["vs"][m]["ac_maxdev_db_chain"],
                                  harm_maxdev_db_tested=max(abs(h["dev_db"]) for a in AMPS
                                                            for h in r["vs"][m]["harm"][a].values()
                                                            if isinstance(h, dict) and h["tested"]),
                                  burst=r["vs"][m]["burst"]))
    a, b = out["models"]["dual"]["harmonics"], out["models"]["r51"]["harmonics"]
    out["difference_55_minus_51"] = {x: {k: a[x][k] - b[x][k] for k in ("H2", "H3", "H4", "H5")} |
                                     dict(thd_ratio=a[x]["thd_pct"] / b[x]["thd_pct"]) for x in AMPS}
    return out


def table(c):
    hdr = "| drive (V peak) | fit | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD % |"
    rows = [hdr, "|" + "---|" * 7]
    for a in c["amps_V_peak"]:
        for m in ("r51", "dual"):
            h = c["models"][m]["harmonics"][a]
            rows.append(f"| {a} | {LABEL[m]} | " + " | ".join(f"{h[k]:.2f}" for k in ("H2", "H3", "H4", "H5"))
                        + f" | {h['thd_pct']:.4g} |")
    return "\n".join(rows)


if __name__ == "__main__":
    c = compare()
    json.dump(c, open(os.path.join(HERE, "fit_comparison.json"), "w"), indent=1, default=float)
    print(table(c))
    for m in ("r51", "dual"):
        mm = c["models"][m]
        print(m, {k: mm["op"][k] for k in ("va", "vk", "ia", "ig2")}, f"gain {mm['ac_gain_1k_db']:.3f} dB",
              mm["vs_own_reference"]["passed"])
