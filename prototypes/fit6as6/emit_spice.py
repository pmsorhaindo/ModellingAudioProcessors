"""Write results/6AS6_fit.inc (ngspice / LTspice behavioural subcircuit) from fit_results.json.
Expressions use only ln, exp, atan, pwr, max (common to ngspice and LTspice B-sources)."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))
p = json.load(open(os.path.join(HERE, "results", "fit_results.json")))["dual_control"]["params"]

E1 = "(max(V(G2,K),1m)/{KP})*ln(1+exp({KP}*(1/{MU}+V(G1,K)/max(V(G2,K),1m))))"
IK = "2*pwr(max(" + E1 + ",0),{EX})/{KG1}"
Z = "(V(G3,K)+max(V(P,K),0)/{MU3}-({V3C})-{RI}*V(ik))/{S3}"
F = "{FMAX}*0.63661977*atan(max(V(P,K),0)/{KVB})*exp(-{P3}*ln(1+exp(-" + Z + ")))"

def fmt(s):
    return s.format(**{k: f"{v:.6g}" for k, v in p.items()})

EXPR = dict(ik=fmt(IK), share=fmt(F))

def text():
    return f"""* 6AS6 dual-control pentode, behavioural model fitted to the Raytheon 5725/6AS6W datasheet
* curves (15 Dec 1960, https://frank.pocnet.net/sheets/138/5/5725.pdf, pages 8-11), log-scale error.
* Model: Koren E1 total cathode current, split plate/screen by a g3- and plate-dependent share.
* Empirical extension, NOT a published model. Fitted for Ec2 60-140 V, Ec1 -5..0 V, Ec3 -9..+12 V,
* Eb 20-400 V, currents down to ~0.1 mA. Cathode current is independent of g3, so screen current
* is over-predicted by up to ~25 % at deep g3 cut-off (Ec3 < -6 V; README). No g1/g3 grid current,
* no heater; capacitances are GE datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
Bik ik 0 V={EXPR['ik']}
Rik ik 0 1G
Bshare sh 0 V={EXPR['share']}
Rsh sh 0 1G
Bp P K I=V(ik)*V(sh)
Bg2 G2 K I=V(ik)*(1-V(sh))
* inter-electrode capacitances from the GE sheet, page 1 (with external shield to pin 2)
Cg1 G1 K 4p
Cg3 G3 K 3.4p
Cpk P K 3p
Cg1p G1 P 0.02p
Cg1g3 G1 G3 0.15p
.ENDS 6AS6_FIT
"""

if __name__ == "__main__":
    open(os.path.join(HERE, "results", "6AS6_fit.inc"), "w").write(text()); print(text())
