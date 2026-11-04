"""The stage netlist and its nodal-DK state-space matrices.

Same circuit and values as ../spice/stage_12ax7.cir. Values: HT, Ra, Rk, Rg from Blencowe ch.1
(sourced); the rest are our example values (see the netlist header). The inter-electrode capacitances
CCG/CGP/CCP are Koren's 12AX7 values (Tube.lib), included in BOTH the DK model and the
Dempwolf-Zoelzer ngspice reference so the two describe the same circuit.

Derivation (nodal DK; Yeh 2009, Holters & Zoelzer DAFx-11 / EUSIPCO-15), trapezoidal capacitors:
  companion model of each capacitor: i_c[n] = Gc v_c[n] - x[n],  Gc = 2C/T,
  history update                    x[n+1] = 2 Gc v_c[n] - x[n].
  MNA:  S [v; j] = Nx^T x - Nn^T i_nl + Nu^T u,    S = [[Nr^T Gr Nr + Nx^T Gx Nx, Nu^T], [Nu, 0]]
  so with  w = S^-1 (...):
     v_nl   = G x + H u - K i_nl      (nonlinear port voltages Vgk, Vak)
     x[n+1] = A x + B u + C i_nl
     y      = D x + E u + F i_nl
  where A = 2 Gx Nx' Si Nx'^T - I, B = 2 Gx Nx' Si Nu'^T, C = -2 Gx Nx' Si Nn'^T,
        G = Nn' Si Nx'^T, H = Nn' Si Nu'^T, K = Nn' Si Nn'^T, (D, E, F likewise with No'), ' = padded.
  (Sign convention: i_nl are device currents flowing g->k and a->k; K enters with a minus sign.)
"""
import numpy as np

NODES = ["src", "in1", "gl", "g", "k", "a", "out", "ht"]
VALUES = dict(HT=300.0, Rs=1e3, Cin=22e-9, Rg=1e6, Rgs=68e3, Ra=100e3, Rk=1.5e3, Ck=22e-6,
              Cout=22e-9, Rl=1e6, CCG=2.3e-12, CGP=2.4e-12, CCP=0.9e-12)


def netlist(rk_scale=1.0, v=VALUES):
    R = [("src", "in1", v["Rs"]), ("gl", None, v["Rg"]), ("gl", "g", v["Rgs"]), ("ht", "a", v["Ra"]),
         ("k", None, v["Rk"] * rk_scale), ("out", None, v["Rl"])]
    Cs = [("in1", "gl", v["Cin"]), ("k", None, v["Ck"]), ("a", "out", v["Cout"]),
          ("g", "k", v["CCG"]), ("g", "a", v["CGP"]), ("a", "k", v["CCP"])]
    U = ["src", "ht"]                      # ideal voltage sources to ground: u = [vin, HT]
    NL = [("g", "k"), ("a", "k")]          # ports: Vgk (grid current), Vak (plate current)
    OUT = [("out", None), ("g", None), ("k", None), ("a", None), ("gl", None)]
    return R, Cs, U, NL, OUT


def _inc(pairs):
    M = np.zeros((len(pairs), len(NODES)))
    for r, (p, q) in enumerate(pairs):
        M[r, NODES.index(p)] = 1.0
        if q is not None:
            M[r, NODES.index(q)] = -1.0
    return M


def build(fs, rk_scale=1.0, dc=False):
    """State-space matrices at sample rate fs. dc=True: capacitors open (for the operating point)."""
    R, Cs, U, NL, OUT = netlist(rk_scale)
    Nr, Nx, Nn, No = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NL), _inc(OUT)
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    Gr = np.diag([1.0 / r for _, _, r in R])
    Gx = np.diag([0.0 if dc else 2.0 * fs * c for _, _, c in Cs])
    nn, nu = len(NODES), len(U)
    S = np.block([[Nr.T @ Gr @ Nr + Nx.T @ Gx @ Nx, Nu.T], [Nu, np.zeros((nu, nu))]])
    Si = np.linalg.inv(S)
    pad = lambda M: np.hstack([M, np.zeros((M.shape[0], nu))])
    Nxp, Nnp, Nop = pad(Nx), pad(Nn), pad(No)
    Nup = np.hstack([np.zeros((nu, nn)), np.eye(nu)])
    I = np.eye(len(Cs))
    return dict(
        A=2 * Gx @ Nxp @ Si @ Nxp.T - I, B=2 * Gx @ Nxp @ Si @ Nup.T, C=-2 * Gx @ Nxp @ Si @ Nnp.T,
        D=Nop @ Si @ Nxp.T, E=Nop @ Si @ Nup.T, F=-(Nop @ Si @ Nnp.T),
        G=Nnp @ Si @ Nxp.T, H=Nnp @ Si @ Nup.T, K=Nnp @ Si @ Nnp.T,
        Gx=Gx, Nx=Nx, Si=Si, Nxp=Nxp, Nnp=Nnp, Nup=Nup, fs=fs, out_names=["out", "g", "k", "a", "gl"])
