"""Write results/6AS6_fit.inc (ngspice / LTspice behavioural subcircuit) from fit_results.json.
Expressions use only ln, exp, atan, pwr, max (common to ngspice and LTspice B-sources).
`expressions(p)` writes the issue #53 form (KR, XA in p; model6as6.py; dual_control since #55) or, for parameter
sets without KR/XA (#51 and earlier), the original two-node form."""
import json
import os
HERE = os.path.dirname(os.path.abspath(__file__))
p = json.load(open(os.path.join(HERE, "results", "fit_results.json")))["dual_control"]["params"]
VA0 = 120.0   # model6as6.VA0

E1 = "(max(V(G2,K),1m)/{KP})*ln(1+exp({KP}*(1/{MU}+V(G1,K)/max(V(G2,K),1m))))"
IK = "2*pwr(max(" + E1 + ",0),{EX})/{KG1}"
Z = "(V(G3,K)+max(V(P,K),0)/{MU3}-({V3C})-{RI}*V(ik))/{S3}"
F = "{FMAX}*0.63661977*atan(max(V(P,K),0)/{KVB})*exp(-{P3}*ln(1+exp(-" + Z + ")))"
# issue #53: plate term (VA0/MU3)*(1+((Va/VA0)^XA-1)/XA) and Ik = Ik0*(1-KR/(1+exp(z))); Ik0 on node ik0
UP53 = "({VA0}/{MU3})*(1+(pwr(max(V(P,K),1m)/{VA0},{XA})-1)/{XA})"
Z53 = "(V(G3,K)+" + UP53 + "-({V3C})-{RI}*V(ik0))/{S3}"
F53 = "{FMAX}*0.63661977*atan(max(V(P,K),0)/{KVB})*exp(-{P3}*ln(1+exp(-" + Z53 + ")))"
IK53 = "V(ik0)*(1-{KR}/(1+exp(" + Z53 + ")))"


def fmt(s, q):
    return s.format(**{k: f"{v:.6g}" for k, v in q.items()}, VA0=f"{VA0:g}")


def expressions(q):
    if "KR" in q or "XA" in q:
        q = dict(dict(KR=0.0, XA=1.0), **q)
        return dict(ik0=fmt(IK, q), ik=fmt(IK53, q), share=fmt(F53, q))
    return dict(ik=fmt(IK, q), share=fmt(F, q))


EXPR = expressions(p)


def sources(e):
    ik0 = f"Bik0 ik0 0 V={e['ik0']}\nRik0 ik0 0 1G\n" if "ik0" in e else ""
    return f"{ik0}Bik ik 0 V={e['ik']}\nRik ik 0 1G\nBshare sh 0 V={e['share']}\nRsh sh 0 1G\n"


def text(e=EXPR):
    return f"""* 6AS6 dual-control pentode, behavioural model fitted to the Raytheon 5725/6AS6W datasheet
* curves (15 Dec 1960, https://frank.pocnet.net/sheets/138/5/5725.pdf, pages 8-11, incl. the p.8 bottom
* plate family at Ec3 = -1..-14 V since issue #51 and the p.11 top Ic2-vs-Ec3 curves since #55), log-scale error.
* Model: Koren E1 cathode current Ik0 (node ik0), lowered past g3 cut-off by up to KR (node ik; issue #53/#55:
* Ik(Ec3 = -10 V)/Ik(0) = 0.79-0.81, Raytheon p.11 0.75-0.87), split plate/screen by a g3- and
* plate-dependent share with a power-law plate term. Empirical extension, NOT a published model.
* Fitted for Ec2 60-140 V, Ec1 -5..0 V, Ec3 -14..+12 V (Ec3 < -9 V only at Ec2 = 120 V, Ec1 = -2 V),
* Eb 20-435 V, currents down to ~0.1 mA. gm(g3) is too low for Ec3 > +5 V (about -65 %) and too high
* below -6 V (README). No g1/g3 grid current, no heater; capacitances are GE datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
{sources(e)}Bp P K I=V(ik)*V(sh)
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
    open(os.path.join(HERE, "results", "6AS6_fit.inc"), "w").write(text())
    print(text())
