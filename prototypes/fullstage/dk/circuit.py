"""The stage netlist and its nodal-DK state-space matrices.

VALUES is the single source of the component values: spice/run_refs.py generates every ngspice netlist from
it (#4; spice/stage_12ax7.cir is the GENERATED copy) and spice/tests/test_netlists.py checks them against it.
COMPONENT VALUES ARE CHOSEN EXAMPLE VALUES, NOT CULTURE VULTURE VALUES. From Merlin Blencowe, "Designing Valve
Preamps for Guitar and Bass", ch.1 (https://www.valvewizard.co.uk/Common_Gain_Stage.pdf): HT = 300 V, Ra = 100k,
Rk = 1.5k (sec 1.10 worked example), Rg (grid leak) = 1M (sec 1.9 "typically 1MΩ"). Chosen by us as conventional
round values (NOT from that source): Cin = 22n, Ck = 22u (fully bypassed, f_lo = 1/(2 pi Rk Ck) ≈ 4.8 Hz),
Cout = 22n, Rl (next-stage grid leak) = 1M, source resistance Rs = 1k, grid stopper Rgs = 68k.
The inter-electrode capacitances CCG/CGP/CCP are Koren's 12AX7 values (Tube.lib), included in BOTH the DK model
and the Dempwolf-Zoelzer ngspice reference (spice/models_dz/dz_12ax7.inc) so the two describe the same circuit.

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
Pre-warp (optional, #17): the trapezoidal rule is the bilinear map s -> (2/T)(1 - z^-1)/(1 + z^-1), which
compresses frequency (the 0.21 dB at 20 kHz at 4x, see RESULTS.md). Using Gc = C w0 / tan(w0 T/2) instead of
2C/T makes a capacitor's admittance exact at w0 (same companion model and history update, only Gc changes).
It is applied to the small capacitances only (< SMALL_C: the pF valve capacitances that, with the Miller
effect, set the HF roll-off): the coupling and bypass caps set poles below ~10 Hz, where pre-warping them to
a high w0 would detune them by up to 3 % at 4x. No runtime cost: it only changes the matrices.
"""
import numpy as np

NODES = ["src", "in1", "gl", "g", "k", "a", "out", "ht"]
VALUES = dict(HT=300.0, Rs=1e3, Cin=22e-9, Rg=1e6, Rgs=68e3, Ra=100e3, Rk=1.5e3, Ck=22e-6,
              Cout=22e-9, Rl=1e6, CCG=2.3e-12, CGP=2.4e-12, CCP=0.9e-12)
SMALL_C = 1e-9         # F: capacitors below this are pre-warped when build(prewarp_hz=...) is given


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


def companion_g(c, fs, prewarp_hz=None):
    """Companion conductance of capacitor c at rate fs: 2C/T, or pre-warped to prewarp_hz if c < SMALL_C."""
    if prewarp_hz is None or c >= SMALL_C:
        return 2.0 * fs * c
    w0 = 2.0 * np.pi * prewarp_hz
    return c * w0 / np.tan(w0 / (2.0 * fs))


def analog_response(f, J, rk_scale=1.0):
    """Exact (continuous-time) small-signal response v(out)/v(src) of the netlist at frequencies f, with the
    valve linearised to the 2x2 Jacobian J = d(Ig, Ia)/d(Vgk, Vak) of its operating point. This is what
    ngspice's .ac computes (matches spice/reference_dz.json to its 7-decimal rounding, 7e-8 dB); no
    discretisation, so dk/linear.py uses it to isolate the discretisation error (#38)."""
    R, Cs, U, NL, OUT = netlist(rk_scale)
    Nr, Nx, Nn = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NL)
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    Y0 = Nr.T @ np.diag([1.0 / r for _, _, r in R]) @ Nr + Nn.T @ np.asarray(J) @ Nn
    Yc = Nx.T @ np.diag([c for _, _, c in Cs]) @ Nx
    nu = len(U)
    rhs = np.r_[np.zeros(len(NODES)), 1.0, 0.0]               # AC 1 on vin, HT at AC ground
    iout = NODES.index(OUT[0][0])
    out = []
    for s in 2j * np.pi * np.atleast_1d(np.asarray(f, float)):
        S = np.block([[Y0 + s * Yc, Nu.T], [Nu, np.zeros((nu, nu))]])
        out.append(np.linalg.solve(S, rhs)[iout])
    return np.array(out)


def build(fs, rk_scale=1.0, dc=False, prewarp_hz=None):
    """State-space matrices at sample rate fs. dc=True: capacitors open (for the operating point).
    prewarp_hz: pre-warp the small capacitances to this frequency (None = plain trapezoidal)."""
    R, Cs, U, NL, OUT = netlist(rk_scale)
    Nr, Nx, Nn, No = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NL), _inc(OUT)
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    Gr = np.diag([1.0 / r for _, _, r in R])
    Gx = np.diag([0.0 if dc else companion_g(c, fs, prewarp_hz) for _, _, c in Cs])
    nn, nu = len(NODES), len(U)
    S = np.block([[Nr.T @ Gr @ Nr + Nx.T @ Gx @ Nx, Nu.T], [Nu, np.zeros((nu, nu))]])
    Si = np.linalg.inv(S)
    def pad(M):
        return np.hstack([M, np.zeros((M.shape[0], nu))])
    Nxp, Nnp, Nop = pad(Nx), pad(Nn), pad(No)
    Nup = np.hstack([np.zeros((nu, nn)), np.eye(nu)])
    I = np.eye(len(Cs))  # noqa: E741 -- the identity matrix I of the DK equations
    return dict(
        A=2 * Gx @ Nxp @ Si @ Nxp.T - I, B=2 * Gx @ Nxp @ Si @ Nup.T, C=-2 * Gx @ Nxp @ Si @ Nnp.T,
        D=Nop @ Si @ Nxp.T, E=Nop @ Si @ Nup.T, F=-(Nop @ Si @ Nnp.T),
        G=Nnp @ Si @ Nxp.T, H=Nnp @ Si @ Nup.T, K=Nnp @ Si @ Nnp.T,
        Gx=Gx, Nx=Nx, Si=Si, Nxp=Nxp, Nnp=Nnp, Nup=Nup, fs=fs, out_names=["out", "g", "k", "a", "gl"])
