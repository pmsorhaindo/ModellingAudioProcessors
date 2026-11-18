"""3-state DK netlist for the cut-down 12AX7 stage (#23).

Reuses the exact circuit values from prototypes/fullstage/dk/circuit.py (VALUES), dropping the
three pF inter-electrode valve capacitances (CCG, CGP, CCP) so only the three large coupling and
bypass capacitors remain:
  - Cin  = 22 nF  (in1 -> gl)
  - Ck   = 22 uF  (k   -> ground)
  - Cout = 22 nF  (a   -> out)

Because those three capacitors do not couple directly outside the valve ports, the resulting 3x3
state transition matrix A is diagonal and B0, C, D, E0, F, G, H0 are sparse. Plain trapezoidal
discretisation (Gc = 2 C fs) is used for all three states (these large caps set sub-10 Hz poles,
so high-frequency pre-warping does not apply).
"""
import math
import numpy as np
import cutdown  # noqa: F401 -- ensures FULLSTAGE_DIR is on sys.path
from dk.circuit import NODES, VALUES, _inc  # noqa: E402 -- after cutdown path setup

CAP_NAMES = ("Cin", "Ck", "Cout")


def miller_cutoff_hz(J, rk_scale=1.0, v=VALUES):
    """Open-circuit time-constant (OCTC) cutoff Hz of the dropped pF valve caps (CCG, CGP, CCP).

    Assumes Cin, Ck, Cout are AC shorts at HF so cathode is AC ground:
      Rg_th = Rgs + (Rs || Rg)
      R_plate = (1/gds) || Ra || Rl
      tau_octc = CCG * Rg_th + CGP * (Rg_th + R_plate + gm * Rg_th * R_plate) + CCP * R_plate
    """
    J_arr = np.asarray(J, float)
    gm = float(J_arr[1, 0])
    gds = float(J_arr[1, 1])
    r_plate = 1.0 / (gds + 1.0 / v["Ra"] + 1.0 / v["Rl"])
    rg_th = v["Rgs"] + 1.0 / (1.0 / v["Rs"] + 1.0 / v["Rg"])
    r_gp0 = rg_th + r_plate + gm * rg_th * r_plate
    tau = v["CCG"] * rg_th + v["CGP"] * r_gp0 + v["CCP"] * r_plate
    return 1.0 / (2.0 * math.pi * tau)


def miller_filter_coefs(fc, fs, f_ref=20000.0):
    """First-order (one-pole) IIR LPF H(z) = (b0 + b1 z^-1) / (1 - p z^-1) at rate fs.

    Uses the exact continuous-time pole mapping p = exp(-2*pi*fc/fs) and solves the zero weight
    alpha in b0 = (1-p)*(1-alpha), b1 = (1-p)*alpha so |H(f_ref)| equals 1/sqrt(1 + (f_ref/fc)^2).
    """
    p = math.exp(-2.0 * math.pi * float(fc) / float(fs))
    w_ref = 2.0 * math.pi * min(float(f_ref), 0.45 * float(fs)) / float(fs)
    c_w = math.cos(w_ref)
    target2 = 1.0 / (1.0 + (float(f_ref) / float(fc)) ** 2)
    r_val = target2 * (1.0 + p * p - 2.0 * p * c_w) / ((1.0 - p) ** 2)
    q_val = (1.0 - r_val) / (2.0 * (1.0 - c_w))
    disc = max(0.0, 1.0 - 4.0 * q_val)
    alpha = 0.5 * (1.0 - math.sqrt(disc))
    b0 = (1.0 - p) * (1.0 - alpha)
    b1 = (1.0 - p) * alpha
    return b0, b1, p


def treble_comp_coefs(fs_base, fs_internal, f_ref=20000.0):
    """First-order IIR treble shelf H_C(z) = (b0 + b1 z^-1) / (1 - p z^-1) at base rate fs_base.

    Cancels the small-signal droop |H_ADAA1(f)| = cos(pi*f / fs_internal) of first-order ADAA
    at internal rate fs_internal. Writing |H_C(w)|^2 = (1 + N*x) / (1 + D*x) in x = 1 - cos(w)
    gives unit DC gain ((b0 + b1) / (1 - p) = 1). Matching 1 / cos^2(pi*f / fs_internal)
    analytically at f2 = min(f_ref, 0.45 * fs_base) and f1 = f2 / sqrt(2) (the midpoint in f^2,
    matching the leading quadratic dB droop) yields closed-form (b0, b1, p).
    """
    fs_base = float(fs_base)
    fs_internal = float(fs_internal)
    f2 = min(float(f_ref), 0.45 * fs_base)
    f1 = f2 / math.sqrt(2.0)
    x1 = 1.0 - math.cos(2.0 * math.pi * f1 / fs_base)
    x2 = 1.0 - math.cos(2.0 * math.pi * f2 / fs_base)
    t1 = 1.0 / (math.cos(math.pi * f1 / fs_internal) ** 2)
    t2 = 1.0 / (math.cos(math.pi * f2 / fs_internal) ** 2)
    s1 = (t1 - 1.0) / x1
    s2 = (t2 - 1.0) / x2
    d_coef = (s1 - s2) / (t2 - t1)
    n_coef = s2 + t2 * d_coef
    if abs(d_coef) < 1e-15:
        p = 0.0
    else:
        p = ((d_coef + 1.0) - math.sqrt(max(0.0, 2.0 * d_coef + 1.0))) / d_coef
    b0 = 0.5 * (1.0 - p) * (1.0 + math.sqrt(max(0.0, 1.0 + 2.0 * n_coef)))
    b1 = (1.0 - p) - b0
    return b0, b1, p


def netlist(rk_scale=1.0, v=VALUES):
    """3-state netlist (same resistors, sources, ports, and outputs as dk.circuit.netlist; no pF caps)."""
    R = [("src", "in1", v["Rs"]), ("gl", None, v["Rg"]), ("gl", "g", v["Rgs"]), ("ht", "a", v["Ra"]),
         ("k", None, v["Rk"] * rk_scale), ("out", None, v["Rl"])]
    Cs = [("in1", "gl", v["Cin"]), ("k", None, v["Ck"]), ("a", "out", v["Cout"])]
    U = ["src", "ht"]
    NL = [("g", "k"), ("a", "k")]
    OUT = [("out", None), ("g", None), ("k", None), ("a", None), ("gl", None)]
    return R, Cs, U, NL, OUT


def analog_response(f, J, rk_scale=1.0):
    """Exact continuous-time small-signal response v(out)/v(src) of the 3-state netlist (without pF caps)."""
    R, Cs, U, NL, OUT = netlist(rk_scale)
    Nr, Nx, Nn = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NL)
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    Y0 = Nr.T @ np.diag([1.0 / r for _, _, r in R]) @ Nr + Nn.T @ np.asarray(J) @ Nn
    Yc = Nx.T @ np.diag([c for _, _, c in Cs]) @ Nx
    nu = len(U)
    rhs = np.r_[np.zeros(len(NODES)), 1.0, 0.0]
    iout = NODES.index(OUT[0][0])
    out = []
    for s in 2j * np.pi * np.atleast_1d(np.asarray(f, float)):
        S = np.block([[Y0 + s * Yc, Nu.T], [Nu, np.zeros((nu, nu))]])
        out.append(np.linalg.solve(S, rhs)[iout])
    return np.array(out)


def build(fs, rk_scale=1.0, dc=False):
    """3-state DK state-space matrices at sample rate fs (dc=True opens all capacitors)."""
    R, Cs, U, NL, OUT = netlist(rk_scale)
    Nr, Nx, Nn, No = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NL), _inc(OUT)
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    Gr = np.diag([1.0 / r for _, _, r in R])
    Gx = np.diag([0.0 if dc else 2.0 * float(fs) * c for _, _, c in Cs])
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
        Gx=Gx, Nx=Nx, Si=Si, Nxp=Nxp, Nnp=Nnp, Nup=Nup, fs=float(fs),
        out_names=["out", "g", "k", "a", "gl"],
    )
