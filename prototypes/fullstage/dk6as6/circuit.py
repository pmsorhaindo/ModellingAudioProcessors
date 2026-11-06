"""The 6AS6 common-cathode stage netlist and its nodal-DK state-space matrices (issue #26).

Same coupling / load topology and the same passive values as the 12AX7 stage: VALUES is built from
dk/circuit.VALUES (HT, Rs, Cin, Rg, Rgs, Ra, Rk, Ck, Cout, Rl). THESE ARE CHOSEN EXAMPLE VALUES, NOT CULTURE VULTURE
VALUES; sources in dk/circuit.py (Merlin Blencowe, "Designing Valve Preamps for Guitar and Bass", ch.1,
https://www.valvewizard.co.uk/Common_Gain_Stage.pdf: HT 300 V, Ra 100k, Rk 1.5k, Rg 1M; Cin, Ck, Cout, Rl, Rs and Rgs
our conventional round values). The 12AX7's inter-electrode capacitances are dropped; in their place:
  Screen g2 and suppressor g3: ideal voltage sources to ground at the datasheet typical conditions used throughout
    prototypes/fit6as6, EC2 = 120 V and EC3 = 0 V (GE ET-T706, https://frank.pocnet.net/sheets/084/6/6AS6.pdf p.1;
    Raytheon 5725/6AS6W, https://frank.pocnet.net/sheets/138/5/5725.pdf). No screen resistor and no T/P1/P2 switch
    wiring: the Culture Vulture schematic is unknown (references/schematics.md).
  Inter-electrode capacitances: GE sheet p.1 values, as in prototypes/fit6as6/results/6AS6_fit.inc:
    CG1K 4p, CG3K 3.4p, CPK 3p, CG1P 0.02p, CG1G3 0.15p. The same set is inside the same-model ngspice subckt
    (spice6as6/run_refs.py), so the two describe the same circuit.
The valve: pentode.py (fit6as6 dual_control). Ig1 = Ig3 = 0, so g1 and g3 carry no valve current.

Derivation: as dk/circuit.py (nodal DK, trapezoidal companion capacitors, Yeh 2009, Holters & Zoelzer), with one
change: the nonlinear CURRENT ports and the CONTROLLING-VOLTAGE ports differ. The valve injects two currents,
i_nl = (Ip a->k, Ig2 g2->k) (NLI, incidence Ni), and depends on four voltages, v_nl = (Vg1k, Vg2k, Vg3k, Vak) (NLV,
incidence Nv), so with  w = S^-1 (Nx^T x - Ni^T i_nl + Nu^T u):
     v_nl   = G x + H u - K i_nl,   K = Nv' Si Ni'^T  (4x2),   G = Nv' Si Nx'^T,  H = Nv' Si Nu'^T
     x[n+1] = A x + B u + C i_nl,   C = -2 Gx Nx' Si Ni'^T
     y      = D x + E u + F i_nl,   F = -No' Si Ni'^T
A, B, D, E as in dk/circuit.py. u = (vin, HT, EC2, EC3). g2 and g3 are source nodes, so Vg2k = EC2 - Vk and
Vg3k = EC3 - Vk come out of the same equations (rows of G, H, K).
"""
import numpy as np
from dk import circuit as _c12
from dk.circuit import companion_g, SMALL_C  # noqa: F401  (same companion model and pre-warp rule)

NODES = ["src", "in1", "gl", "g", "k", "a", "out", "ht", "g2", "g3"]
PASSIVES = ("HT", "Rs", "Cin", "Rg", "Rgs", "Ra", "Rk", "Ck", "Cout", "Rl")    # shared with the 12AX7 stage
VALUES = dict({k: _c12.VALUES[k] for k in PASSIVES},
              EC2=120.0, EC3=0.0,                                         # GE / Raytheon typical conditions
              CG1K=4e-12, CG3K=3.4e-12, CPK=3e-12, CG1P=0.02e-12, CG1G3=0.15e-12)   # GE sheet p.1
VALVE_CAPS = ("CG1K", "CG3K", "CPK", "CG1P", "CG1G3")
U = ["src", "ht", "g2", "g3"]                     # ideal voltage sources to ground: u = [vin, HT, EC2, EC3]
NLV = [("g", "k"), ("g2", "k"), ("g3", "k"), ("a", "k")]   # controlling voltages Vg1k, Vg2k, Vg3k, Vak
NLI = [("a", "k"), ("g2", "k")]                   # valve currents Ip (a->k), Ig2 (g2->k)
OUT_NAMES = ["out", "g", "k", "a", "gl"]


def netlist(rk_scale=1.0, v=VALUES):
    R = [("src", "in1", v["Rs"]), ("gl", None, v["Rg"]), ("gl", "g", v["Rgs"]), ("ht", "a", v["Ra"]),
         ("k", None, v["Rk"] * rk_scale), ("out", None, v["Rl"])]
    Cs = [("in1", "gl", v["Cin"]), ("k", None, v["Ck"]), ("a", "out", v["Cout"]),
          ("g", "k", v["CG1K"]), ("g3", "k", v["CG3K"]), ("a", "k", v["CPK"]), ("g", "a", v["CG1P"]),
          ("g", "g3", v["CG1G3"])]
    OUT = [(n, None) for n in OUT_NAMES]
    return R, Cs, U, NLV, NLI, OUT


def sources(v=VALUES):
    """u without the input: (HT, EC2, EC3)."""
    return np.array([v["HT"], v["EC2"], v["EC3"]])


def _inc(pairs):
    M = np.zeros((len(pairs), len(NODES)))
    for r, (p, q) in enumerate(pairs):
        M[r, NODES.index(p)] = 1.0
        if q is not None:
            M[r, NODES.index(q)] = -1.0
    return M


def _nu():
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    return Nu


def analog_response(f, J, rk_scale=1.0):
    """Exact (continuous-time) small-signal response v(out)/v(src) at frequencies f, with the valve linearised to
    the 2x4 Jacobian J = d(Ip, Ig2)/d(Vg1k, Vg2k, Vg3k, Vak) of its operating point (what ngspice's .ac computes)."""
    R, Cs, _, NV, NI, OUT = netlist(rk_scale)
    Nr, Nx, Nv, Ni, Nu = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NV), _inc(NI), _nu()
    Y0 = Nr.T @ np.diag([1.0 / r for _, _, r in R]) @ Nr + Ni.T @ np.asarray(J) @ Nv
    Yc = Nx.T @ np.diag([c for _, _, c in Cs]) @ Nx
    nu = len(U)
    rhs = np.r_[np.zeros(len(NODES)), 1.0, np.zeros(nu - 1)]      # AC 1 on vin, the supplies at AC ground
    iout = NODES.index(OUT[0][0])
    out = []
    for s in 2j * np.pi * np.atleast_1d(np.asarray(f, float)):
        S = np.block([[Y0 + s * Yc, Nu.T], [Nu, np.zeros((nu, nu))]])
        out.append(np.linalg.solve(S, rhs)[iout])
    return np.array(out)


def build(fs, rk_scale=1.0, dc=False, prewarp_hz=None):
    """State-space matrices at sample rate fs. dc=True: capacitors open (for the operating point).
    prewarp_hz: pre-warp the small capacitances to this frequency (None = plain trapezoidal)."""
    R, Cs, _, NV, NI, OUT = netlist(rk_scale)
    Nr, Nx, Nv, Ni, No, Nu = (_inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NV), _inc(NI),
                              _inc(OUT), _nu())
    Gr = np.diag([1.0 / r for _, _, r in R])
    Gx = np.diag([0.0 if dc else companion_g(c, fs, prewarp_hz) for _, _, c in Cs])
    nn, nu = len(NODES), len(U)
    S = np.block([[Nr.T @ Gr @ Nr + Nx.T @ Gx @ Nx, Nu.T], [Nu, np.zeros((nu, nu))]])
    Si = np.linalg.inv(S)
    pad = lambda M: np.hstack([M, np.zeros((M.shape[0], nu))])
    Nxp, Nvp, Nip, Nop = pad(Nx), pad(Nv), pad(Ni), pad(No)
    Nup = np.hstack([np.zeros((nu, nn)), np.eye(nu)])
    I = np.eye(len(Cs))
    return dict(
        A=2 * Gx @ Nxp @ Si @ Nxp.T - I, B=2 * Gx @ Nxp @ Si @ Nup.T, C=-2 * Gx @ Nxp @ Si @ Nip.T,
        D=Nop @ Si @ Nxp.T, E=Nop @ Si @ Nup.T, F=-(Nop @ Si @ Nip.T),
        G=Nvp @ Si @ Nxp.T, H=Nvp @ Si @ Nup.T, K=Nvp @ Si @ Nip.T,
        Gx=Gx, Nx=Nx, Si=Si, Nxp=Nxp, Nvp=Nvp, Nip=Nip, Nup=Nup, fs=fs, out_names=list(OUT_NAMES))
