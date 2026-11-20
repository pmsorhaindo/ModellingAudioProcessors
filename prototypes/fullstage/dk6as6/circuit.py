"""The 6AS6 stage netlist and its nodal-DK state-space matrices (issues #26, #102).

Values: issue #102's research pass (https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/102#issuecomment-7021775895;
table and labels copied in dk6as6/RESULTS.md, "Circuit values"). No public schematic of the inspiring unit exists,
so every passive below is INFERRED from the manuals and datasheets, not measured:
  HT 200 V, Ra 270k        inferred (#102 E2/E3/E7): puts the P1 THD minimum on the manuals' 0.25-0.3 mA; <= the
                           5725's 200 V plate rating at cut-off (S9). Low confidence.
  Rk                       unbypassed rheostat cathode -> ground, NO bypass capacitor (#102 E4/E6, topology from the
                           manuals S3/S6). Not a value: the stage solves it for a target cathode current (stage.py).
  Rg 1M                    S9 maximum grid-circuit resistance.
  Cin = Cout = 100n        inferred (HP corner of a few Hz).
  Rl 1M                    next-stage grid leak (the #102 table's Zout figures include a 1 MOhm load).
  EC2 120 V                screen at the datasheet typical condition, stiff (S9; GE ET-T706,
                           https://frank.pocnet.net/sheets/084/6/6AS6.pdf p.1).
  Modes (#111, MODES)      the valve's g2 / g3 connection per Distortion-switch position. No source states how T / P1 /
                           P2 are wired (dk6as6/RESULTS.md, "Modes (#111)", has the sourced / inferred labels):
                           P1 g3 -> cathode, g2 the stiff EC2: S8 speculation, the datasheet's standard pentode
                              connection. Low confidence. The default; unchanged since #118.
                           T  g2 + g3 strapped to the plate through Rstrap = 100 Ohm (no EC2): S8 speculation plus
                              standard triode-strapping practice (#102). Low confidence.
                           P2 g3 -> ground (so Vg3k = -Vk), g2 the stiff EC2: OUR hypothesis (#102 E9), the only one
                              of those tried that shows all three documented P2 traits. Low confidence.
  Inter-electrode caps     GE sheet p.1 values (with external shield): g1 input 4p, g3 input 3.4p, output 3p,
                           g1-p 0.02p, g1-g3 0.15p. Placed as in prototypes/fit6as6/results/6AS6_fit.inc (g1 input,
                           output -> cathode) except the g3 input capacitance (g3 to all other electrodes), which
                           6AS6_fit.inc and #26 put g3 -> k: with g3 on the cathode that is a short, leaving the cathode
                           with no capacitance to AC ground (and ngspice 41's transient then fails at its first step
                           at the #5 tolerances). So in P1 CG3 goes g3 -> g2, the screen, an AC ground like the heater
                           and shield it also faces; in T and P2, where g3 is not on the cathode, it goes g3 -> k as in
                           6AS6_fit.inc (MODES[mode]["cg3"]). CG1G3 sits g1-k in P1.
  Rs 1k, Rgs 68k           NOT in the #102 table: the test bench's source resistance and the grid stopper, kept
                           from the 12AX7 stage (dk/circuit.py, our conventional round values). Ig1 = 0, so neither
                           has any DC effect.
The valve: pentode.py (fit6as6 dual_control). Ig1 = Ig3 = 0, so g1 and g3 carry no valve current.

Derivation: as dk/circuit.py (nodal DK, trapezoidal companion capacitors, Yeh 2009, Holters & Zoelzer), with one
change: the nonlinear CURRENT ports and the CONTROLLING-VOLTAGE ports differ. The valve injects two currents,
i_nl = (Ip a->k, Ig2 g2->k) (NLI, incidence Ni), and depends on four voltages, v_nl = (Vg1k, Vg2k, Vg3k, Vak) (NLV,
incidence Nv), so with  w = S^-1 (Nx^T x - Ni^T i_nl + Nu^T u):
     v_nl   = G x + H u - K i_nl,   K = Nv' Si Ni'^T  (4x2),   G = Nv' Si Nx'^T,  H = Nv' Si Nu'^T
     x[n+1] = A x + B u + C i_nl,   C = -2 Gx Nx' Si Ni'^T
     y      = D x + E u + F i_nl,   F = -No' Si Ni'^T
A, B, D, E as in dk/circuit.py. u = (vin, HT, EC2) in P1 and P2, (vin, HT) in T. In P1 / P2 g2 is a source node, so
Vg2k = EC2 - Vk comes out of the same equations; in T it is a node strapped to the plate. In P1 g3 is the cathode node,
so the Vg3k row of Nv is zero and Vg3k = 0; in P2 g3 is ground, so that row is -1 on k; in T g3 is the g2 node.
"""
import numpy as np
from dk.circuit import companion_g, SMALL_C  # noqa: F401  (same companion model and pre-warp rule)

NODES = ["src", "in1", "gl", "g", "k", "a", "out", "ht", "g2"]
PASSIVES = ("HT", "Rs", "Cin", "Rg", "Rgs", "Ra", "Cout", "Rl")   # Rk is solved per bias (stage.cathode_resistor)
VALUES = dict(HT=200.0, Rs=1e3, Cin=100e-9, Rg=1e6, Rgs=68e3, Ra=270e3, Cout=100e-9, Rl=1e6,
              EC2=120.0,                                                                   # stiff screen supply
              CG1K=4e-12, CG3=3.4e-12, CPK=3e-12, CG1P=0.02e-12, CG1G3=0.15e-12,          # GE sheet p.1
              Rstrap=100.0)                                       # T: g2 + g3 -> plate stopper (#102, inferred)
# The Distortion switch (#111): per mode, the valve's pins -> stage nodes (None = ground), in the subckt's pin order;
# the screen feed ("EC2": the g2 node is the stiff supply; "strap": the g2 node is tied to the plate through Rstrap);
# and the pin CG3 goes to from g3. One table for the DK netlist and the ngspice netlists (spice6as6/run_refs.py).
MODES = {
    "P1": dict(pins=dict(P="a", G2="g2", G3="k", G1="g", K="k"), screen="EC2", cg3="G2"),
    "T": dict(pins=dict(P="a", G2="g2", G3="g2", G1="g", K="k"), screen="strap", cg3="K"),
    "P2": dict(pins=dict(P="a", G2="g2", G3=None, G1="g", K="k"), screen="EC2", cg3="K"),
}
MODE = "P1"                                      # the default (#118)
VALVE_CAPS = ("CG1K", "G1", "K"), ("CPK", "P", "K"), ("CG1P", "G1", "P"), ("CG1G3", "G1", "G3")   # + CG3, per mode
NLI = [("a", "k"), ("g2", "k")]                  # valve currents Ip (a->k), Ig2 (g2->k)
OUT_NAMES = ["out", "g", "k", "a", "gl"]


def valve_cap_pins(mode=MODE):
    """The inter-electrode capacitances as (VALUES key, pin+, pin-), as inside the subckt, in the #118 order."""
    return [VALVE_CAPS[0], ("CG3", "G3", MODES[mode]["cg3"])] + list(VALVE_CAPS[1:])


def valve_caps(v=VALUES, mode=MODE):
    """The inter-electrode capacitances as (node+, node-, C) of the stage (a grounded end last)."""
    pins = MODES[mode]["pins"]
    return [(pins[a], pins[b], v[k]) if pins[a] is not None else (pins[b], None, v[k])
            for k, a, b in valve_cap_pins(mode)]


def sources_of(mode=MODE):
    """The ideal voltage sources to ground: u = [vin, HT, EC2] (P1, P2) or [vin, HT] (T, no screen supply)."""
    return ["src", "ht", "g2"] if MODES[mode]["screen"] == "EC2" else ["src", "ht"]


def nlv(mode=MODE):
    """The controlling voltages Vg1k, Vg2k, Vg3k, Vak as (node+, node-) pairs (None = ground)."""
    pins = MODES[mode]["pins"]
    return [(pins[p], pins["K"]) for p in ("G1", "G2", "G3", "P")]


def netlist(rk, v=VALUES, mode=MODE):
    """(resistors, capacitors, sources, NLV, NLI, outputs) with cathode resistor rk (ohms), wired as `mode`."""
    R = [("src", "in1", v["Rs"]), ("gl", None, v["Rg"]), ("gl", "g", v["Rgs"]), ("ht", "a", v["Ra"]),
         ("k", None, rk), ("out", None, v["Rl"])]
    if MODES[mode]["screen"] == "strap":
        R.append(("g2", "a", v["Rstrap"]))
    Cs = [("in1", "gl", v["Cin"]), ("a", "out", v["Cout"])] + valve_caps(v, mode)
    OUT = [(n, None) for n in OUT_NAMES]
    return R, Cs, sources_of(mode), nlv(mode), NLI, OUT


def sources(v=VALUES, mode=MODE):
    """u without the input: (HT, EC2), or (HT,) in T."""
    return np.array([v["HT"], v["EC2"]] if MODES[mode]["screen"] == "EC2" else [v["HT"]])


def _inc(pairs):
    M = np.zeros((len(pairs), len(NODES)))
    for r, (p, q) in enumerate(pairs):
        if p is not None:
            M[r, NODES.index(p)] += 1.0     # p is None: Vg3k with g3 on ground (P2)
        if q is not None:
            M[r, NODES.index(q)] -= 1.0     # p == q (Vg3k with g3 on the cathode, P1): a zero row
    return M


def _nu(U):
    Nu = np.zeros((len(U), len(NODES)))
    for r, n in enumerate(U):
        Nu[r, NODES.index(n)] = 1.0
    return Nu


def analog_response(f, J, rk, mode=MODE):
    """Exact (continuous-time) small-signal response v(out)/v(src) at frequencies f, with the valve linearised to
    the 2x4 Jacobian J = d(Ip, Ig2)/d(Vg1k, Vg2k, Vg3k, Vak) of its operating point (what ngspice's .ac computes)."""
    R, Cs, U, NV, NI, OUT = netlist(rk, mode=mode)
    Nr, Nx, Nv, Ni, Nu = _inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NV), _inc(NI), _nu(U)
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


def build(fs, rk, dc=False, prewarp_hz=None, mode=MODE):
    """State-space matrices at sample rate fs with cathode resistor rk (ohms), wired as `mode`. dc=True: capacitors
    open (for the operating point). prewarp_hz: pre-warp the small capacitances to this frequency (None = plain
    trapezoidal)."""
    R, Cs, U, NV, NI, OUT = netlist(rk, mode=mode)
    Nr, Nx, Nv, Ni, No, Nu = (_inc([(p, q) for p, q, _ in R]), _inc([(p, q) for p, q, _ in Cs]), _inc(NV), _inc(NI),
                              _inc(OUT), _nu(U))
    Gr = np.diag([1.0 / r for _, _, r in R])
    Gx = np.diag([0.0 if dc else companion_g(c, fs, prewarp_hz) for _, _, c in Cs])
    nn, nu = len(NODES), len(U)
    S = np.block([[Nr.T @ Gr @ Nr + Nx.T @ Gx @ Nx, Nu.T], [Nu, np.zeros((nu, nu))]])
    Si = np.linalg.inv(S)
    def pad(M):
        return np.hstack([M, np.zeros((M.shape[0], nu))])
    Nxp, Nvp, Nip, Nop = pad(Nx), pad(Nv), pad(Ni), pad(No)
    Nup = np.hstack([np.zeros((nu, nn)), np.eye(nu)])
    I = np.eye(len(Cs))  # noqa: E741 -- the identity matrix I of the DK equations
    return dict(
        A=2 * Gx @ Nxp @ Si @ Nxp.T - I, B=2 * Gx @ Nxp @ Si @ Nup.T, C=-2 * Gx @ Nxp @ Si @ Nip.T,
        D=Nop @ Si @ Nxp.T, E=Nop @ Si @ Nup.T, F=-(Nop @ Si @ Nip.T),
        G=Nvp @ Si @ Nxp.T, H=Nvp @ Si @ Nup.T, K=Nvp @ Si @ Nip.T,
        Gx=Gx, Nx=Nx, Si=Si, Nxp=Nxp, Nvp=Nvp, Nip=Nip, Nup=Nup, fs=fs, out_names=list(OUT_NAMES))
