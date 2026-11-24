"""Generate the 6AS6 stage's ngspice netlists, run the same-model references, write
reference_<model>[_<mode>]_<bias>mA.json (#26, #102, #111).

  model 'dual' : the fit6as6 dual_control law (issue #55) with the DK stage's own parameters -> reference (ii), at
                 each bias in BIASES["dual"] (target cathode current, mA)
  model 'r51'  : the same law with the issue #51 parameters (refit_51: KR = 0, XA = 1), for the #51 vs #55 harmonic
                 comparison (dk6as6/compare_fits.py); checked against the DK stage like (ii), not a pass criterion
  model 'dualg': 'dual' plus the g1 grid current (#112; dk6as6/pentode.grid at pentode.HARDNESS, the DK stage's
                 default; UNVALIDATED against any 6AS6): a third B-source Bg1 g1 -> k, and the plate and screen sources
                 carry (Ik - Ig1) x share. Its own subckts (models/6as6_dualg{,_cg3k}.inc) and references
                 (reference_dualg[_<mode>]_<bias>mA.json, at GRID_BIASES); the 'dual' subckts and references (Ig1 = 0)
                 are unchanged and still validate the stage at grid_hardness=0.
  models 'dualg_rsd2', 'dualg_ehx1' (#131): 'dualg' with the BOUND grid shapes RSD-2 / EHX-1 (pentode.GRID_BOUNDS,
                 Stage(grid_shape=...): DZ Table 1's other two 12AX7s, scaled as RSD-1; estimated, unvalidated), T only
                 (P1 draws no grid current, dk6as6/RESULTS.md), at BOUND_BIASES. Their own subckts
                 (models/6as6_dualg_{rsd2,ehx1}_cg3k.inc) and references (reference_dualg_{rsd2,ehx1}_T_<bias>mA.json);
                 'dualg' (the nominal RSD-1) and its files are unchanged.
Mode (#111): the Distortion switch's wiring, dk6as6/circuit.MODES (P1, the default, keeps its #118 file names; T and
P2 references are reference_dual_<mode>_<bias>mA.json, at MODE_BIASES[mode]).
Bias: the cathode resistor of each reference is dk6as6.stage.cathode_resistor(bias, params, mode=mode), the Rk the DK
stage solves for the same target current, written to the netlist with 17 significant digits and recorded in the JSON.
Same-model only: there is no independent published 6AS6 SPICE model on this repo's sources to act as a (i).

Mirrors spice/run_refs.py (and reuses its analysis, solver settings and helpers unchanged): .op; .ac 1 Hz-100 kHz plus
the band edges; 1 kHz sines at AMPS for 500 ms (analysed 400-500 ms); the 4 V 1 kHz tone burst 10-30 ms.
Single source of truth: every netlist is built from dk6as6/circuit.VALUES (the #102 passives, EC2, the GE
capacitances, Rstrap), circuit.MODES (per mode: the valve's pins -> nodes, the screen feed and the CG3 placement),
ELEMENTS (the topology of circuit.netlist()), the solved Rk and spice/run_refs.SOLVER. The valve
subckts in models/ are GENERATED from fit6as6/results/fit_results.json by subckt(): the fit6as6/emit_spice.py
expressions (those of results/6AS6_fit.inc), written with 17 significant digits instead of 6 so that the reference
runs the DK model's exact parameters (and 2/pi to 17 digits instead of emit_spice's 0.63661977, 3.7e-9 short), with
the internal V(ik0) / V(ik) nodes substituted inline (no internal nodes
whose ~mA "voltages" would sit near vntol), and the GE inter-electrode capacitances from circuit.VALUES placed as
circuit.valve_cap_pins(mode): one subckt per CG3 placement (6as6_<model>.inc g3 -> g2, P1; 6as6_dual_cg3k.inc
g3 -> k, T and P2).
spice6as6/tests/test_netlists_6as6.py parses all of it and fails on any divergence.
Usage: python spice6as6/run_refs.py [dual] [r51] [dual:0.25] [dual/T] [dual/P2:0.45] ...   (model[/mode][:bias]; every
       P1 BIASES case if none given; ~2-4 min each, except the P1 0.6 mA burst: ~2 h, dk6as6/RESULTS.md)
       python spice6as6/run_refs.py --write-netlist   (rewrites models/*.inc and stage_6as6*.cir; no ngspice needed)
       python spice6as6/run_refs.py --convergence [dual] [dual/T:0.25]  (TIGHT rerun vs the committed JSONs; ~2x)
"""
import fcntl
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from analysis import load_wrdata, resample_uniform, harmonics, thd, db_rel  # noqa: E402 -- after the sys.path setup
from spice.run_refs import (SOLVER, SINE, BURST, AC_GRID, AC_EDGES_HZ, AMPS, NGBEHAVIOR, NGSPICE, spice_num,  # noqa: E402 -- after the sys.path setup
                            burst_metrics)
from dk6as6.circuit import VALUES, MODES, MODE, valve_cap_pins  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import cathode_resistor, BIAS_MA  # noqa: E402 -- after the sys.path setup
sys.path.insert(0, pentode.FIT6AS6)
import emit_spice  # noqa: E402  (fit6as6's SPICE expressions)

PARAMS = {"dual": pentode.DUAL_CONTROL, "r51": pentode.REFIT_51, "dualg": pentode.DUAL_CONTROL,
          "dualg_rsd2": pentode.DUAL_CONTROL, "dualg_ehx1": pentode.DUAL_CONTROL}
HARDNESS = {"dual": 0.0, "r51": 0.0, "dualg": pentode.HARDNESS,    # grid hardness of each model (0: no Ig1 port)
            "dualg_rsd2": pentode.HARDNESS, "dualg_ehx1": pentode.HARDNESS}
# The #131 bound models' grid shapes (pentode.GRID_BOUNDS); every other model runs the nominal pentode.GRID_SHAPE
GRID_SHAPE = {"dualg_rsd2": "RSD-2", "dualg_ehx1": "EHX-1"}
# target cathode currents of the P1 references, mA (#110: the range ends, lowest distortion, default)
# dualg P1 stops at 0.45 mA: at 0.6 mA P1 draws Ig1 <= 1e-23 A on every validate.py signal, so its dual reference is
# the same circuit to far below ngspice's abstol (1e-14 A); test_p1_grid_current_is_negligible checks the DK side. A
# dualg 0.6 mA run's burst was killed at a 3 h cap (#120's silent-start chatter at that operating point).
BIASES = {"dual": (0.1, 0.25, 0.45, 0.6), "r51": (0.45,), "dualg": (0.1, 0.25, 0.45)}
# dual model (#111); T 0.35 mA brackets the top (0.3 mA) of its no-grid supported envelope (stage.SUPPORTED_MA_NO_GRID)
MODE_BIASES = {"P1": BIASES["dual"], "T": (0.1, 0.25, 0.35, 0.45, 0.6), "P2": (0.1, 0.25, 0.45, 0.6)}
# dualg (#112): T at its references' currents and wherever its envelope is tested (stage.SUPPORTED_MA)
GRID_BIASES = {"P1": BIASES["dualg"], "T": (0.1, 0.25, 0.35, 0.45, 0.5, 0.6)}   # T 0.5: the top of its envelope
# the #131 bounds: T at the nominal dualg T references' currents (GRID_BIASES["T"]): 0.45 mA up where the grid current
# moves the nominal results (#131a, PR #139), 0.1-0.35 mA added for the bounds-only report over 0.1-0.5 mA (#131)
BOUND_BIASES = {"T": GRID_BIASES["T"]}
SUBCKT = {"dual": "6AS6_DUAL", "r51": "6AS6_R51", "dualg": "6AS6_DUALG", "dualg_rsd2": "6AS6_DUALG_RSD2",
          "dualg_ehx1": "6AS6_DUALG_EHX1"}


def shape(model):
    """The grid shape (pentode.GRID_SHAPES key) of `model`: its #131 bound, else the nominal."""
    return GRID_SHAPE.get(model, pentode.GRID_SHAPE)


def check_mode(model, mode):
    """Raise ValueError if `model` has no subckt / references wired as `mode`: a #131 bound runs only in
    BOUND_BIASES' modes (T). Called before any netlist is written or ngspice started (PR #139 review)."""
    if model in GRID_SHAPE and mode not in BOUND_BIASES:
        raise ValueError(f"{model} is a #131 grid-current bound, wired only as {sorted(BOUND_BIASES)}, not {mode}")


def grid_of(model):
    """pentode.grid() of `model` (its hardness, params and shape)."""
    return pentode.grid(HARDNESS[model], PARAMS[model], shape(model))


def rk_of(model, bias, mode=MODE):
    """The cathode resistor the DK stage solves for `model` at `bias` mA wired as `mode`."""
    return cathode_resistor(bias, PARAMS[model], mode=mode, grid_hardness=HARDNESS[model], grid_shape=shape(model))
ANALYSES = ["main", "burst"]
SUBCKT_PINS = ("P", "G2", "G3", "G1", "K")   # XV1: plate screen suppressor grid cathode (6AS6_fit.inc order)
# Passive elements in netlist order: (SPICE name = VALUES key or Rk, node+, node-); the valve sits after Rgs; Rstrap
# (T: g2 -> plate) after Rl.
ELEMENTS = [("Rs", "src", "in1"), ("Cin", "in1", "gl"), ("Rg", "gl", "0"), ("Rgs", "gl", "g"),
            ("Ra", "ht", "a"), ("Rk", "k", "0"), ("Cout", "a", "out"), ("Rl", "out", "0")]
STRAP = ("Rstrap", "g2", "a")
VALVE_AFTER = "Rgs"


def pins(mode=MODE):
    """XV1's nodes in SUBCKT_PINS order for `mode` (circuit.MODES; ground is node 0)."""
    return tuple(MODES[mode]["pins"][p] or "0" for p in SUBCKT_PINS)


def elements(mode=MODE):
    return ELEMENTS + ([STRAP] if MODES[mode]["screen"] == "strap" else [])


def variant(mode=MODE):
    """The subckt variant of `mode`: '' (CG3 g3 -> g2, P1) or '_cg3k' (g3 -> k, T and P2)."""
    return "" if MODES[mode]["cg3"] == "G2" else "_" + "cg3" + MODES[mode]["cg3"].lower()


def models(model, mode=MODE):
    """(.include line, subckt name) of `model` wired as `mode`."""
    v = variant(mode)
    return f".include models/6as6_{model}{v}.inc", SUBCKT[model] + v.upper()


def netlist_file(mode=MODE):
    """The committed copy of netlist("dual", "main", BIAS_MA, mode=mode)."""
    return os.path.join(HERE, "stage_6as6.cir" if mode == "P1" else f"stage_6as6_{mode}.cir")


NETLIST_FILE = netlist_file("P1")
MODELS = {m: models(m) for m in PARAMS}       # P1
PINS = pins("P1")
# What the header says about the wiring, per mode (P1's text is #118's, unchanged)
WIRING = {"P1": ("P1 wiring (g3 on the cathode)",
                 "Screen: an ideal source at the\n* GE / Raytheon typical Ec2 = 120 V."),
          "T": ("T wiring (g2 + g3 strapped to the plate through Rstrap)",
                "Screen: strapped to the plate\n* (no screen supply)."),
          "P2": ("P2 wiring (g3 to ground: our hypothesis, #111)",
                 "Screen: an ideal source at the\n* GE / Raytheon typical Ec2 = 120 V.")}
HEADER = ("* GENERATED by spice6as6/run_refs.py ({model}{mode_tag}, {analysis}, bias {bias:g} mA) -- do not edit; "
          "change\n* dk6as6/circuit.VALUES or spice/run_refs.SOLVER and run "
          "`python spice6as6/run_refs.py --write-netlist`.\n") + """* spice6as6/tests/test_netlists_6as6.py checks.
* One 6AS6 stage, {wiring}, unbypassed cathode resistor. Passive values: dk6as6/circuit.VALUES,
* inferred in issue #102 (no public schematic; sources and labels in dk6as6/RESULTS.md). Rk = the resistor the DK
* stage solves for a {bias:g} mA cathode current (dk6as6.stage.cathode_resistor). {screen}
* Valve: {valve}.
* Solver: {solver}.
* .spiceinit sets ngbehavior={ngbehavior}.
"""
VALVES = {"dual": "fit6as6 dual_control (issue #55) behavioural subckt with the DK model's params "
                  "(models/6as6_dual{v}.inc)",
          "r51": "fit6as6 refit_51 (issue #51) behavioural subckt (models/6as6_r51{v}.inc)",
          "dualg": "fit6as6 dual_control (issue #55) with the UNVALIDATED g1 grid current (issue #112) behavioural\n"
                   "* subckt with the DK model's params (models/6as6_dualg{v}.inc)"}
VALVES.update({m: "fit6as6 dual_control (issue #55) with the UNVALIDATED g1 grid current, #131 BOUND shape "
                  f"{GRID_SHAPE[m]}\n* (issue #112 law), behavioural subckt with the DK model's params "
                  f"(models/6as6_{m}{{v}}.inc)" for m in GRID_SHAPE})


TWO_OVER_PI = "0.63661977"          # emit_spice's 2/pi, replaced by the full double


def _fmt(s, q):
    assert s.count(TWO_OVER_PI) <= 1
    return (s.replace(TWO_OVER_PI, f"{2.0 / math.pi:.17g}")
            .format(**{k: f"{v:.17g}" for k, v in q.items()}, VA0=f"{emit_spice.VA0:g}"))


def expressions(q):
    """(Ik, share) as B-source expressions of the subckt pins: emit_spice's, with the internal nodes inlined."""
    if "KR" in q or "XA" in q:
        q = dict(dict(KR=0.0, XA=1.0), **q)
        ik0 = "(" + _fmt(emit_spice.IK, q) + ")"
        return _fmt(emit_spice.IK53, q).replace("V(ik0)", ik0), _fmt(emit_spice.F53, q).replace("V(ik0)", ik0)
    ik = "(" + _fmt(emit_spice.IK, q) + ")"
    return ik, _fmt(emit_spice.F, q).replace("V(ik)", ik)


def grid_expression(g):
    """Ig1 = GG (softplus(CG Vg1k) / CG)^XI as a B-source expression, softplus in the overflow-free form
    max(x, 0) + ln(1 + exp(-|x|)) (ngspice clamps exp() arguments at ~228, validate.SPICE_EXP_CAP)."""
    x = f"{g['CG']:.17g}*V(G1,K)"
    return f"({g['GG']:.17g}*pwr((max({x},0)+ln(1+exp(-abs({x}))))/{g['CG']:.17g},{g['XI']:.17g}))"


def subckt(model, values=VALUES, mode=MODE):
    """The text of models/6as6_<model><variant(mode)>.inc."""
    ik, sh = expressions(PARAMS[model])
    if HARDNESS[model]:
        g = grid_of(model)
        ig1 = grid_expression(g)
        sources = (f"Bp P K I=(({ik})-{ig1})*({sh})\nBg2 G2 K I=(({ik})-{ig1})*(1-({sh}))\nBg1 G1 K I={ig1}")
        if model in GRID_SHAPE:     # a #131 bound
            sh_ = GRID_SHAPE[model]
            ratio = (2.0 / PARAMS[model]["KG1"]) / pentode.GRID_SHAPES[sh_]["G"]
            law = ("* g1 grid current (#112 law, #131 BOUND), NOT 6AS6 data: Ig1 = GG*(softplus(CG*Vg1k)/CG)^XI, "
                   "Dempwolf &\n* Zoelzer DAFx-11 eq. (11) without Ig0, XI and CG of their 12AX7 "
                   f"{sh_} (Table 1), GG = hardness {g['hardness']:g} x\n* the cathode-perveance ratio "
                   f"{ratio:.6g} x the {sh_} Gg (dk6as6/pentode.py); taken out of Ik\n"
                   "* before the split: Ip = (Ik-Ig1)*sh, Ig2 = (Ik-Ig1)*(1-sh). No g3 current.")
        else:
            law = ("* g1 grid current (#112), NOT 6AS6 data: Ig1 = GG*(softplus(CG*Vg1k)/CG)^XI, Dempwolf & Zoelzer "
                   "DAFx-11\n* eq. (11) without Ig0, XI and CG of their 12AX7 RSD-1 (Table 1), GG = hardness "
                   f"{g['hardness']:g} x the\n* cathode-perveance ratio {pentode.PERVEANCE_RATIO:.6g} x the RSD-1 Gg "
                   "(dk6as6/pentode.py); taken out of Ik\n"
                   "* before the split: Ip = (Ik-Ig1)*sh, Ig2 = (Ik-Ig1)*(1-sh). No g3 current.")
    else:
        sources = f"Bp P K I=({ik})*({sh})\nBg2 G2 K I=({ik})*(1-({sh}))"
        law = "* No g1 / g3 grid current."
    caps = "\n".join(f"{k} {p} {n} {spice_num(values[k])}" for k, p, n in valve_cap_pins(mode))
    name = models(model, mode)[1]
    where = ("(the g3 input capacitance g3 -> g2, see dk6as6/circuit.py)" if not variant(mode) else
             "(the g3 input capacitance g3 -> k: T and P2, g3 off the cathode; see dk6as6/circuit.py)")
    return f"""* GENERATED by spice6as6/run_refs.py from prototypes/fit6as6/results/fit_results.json["{
        'refit_51' if model == 'r51' else 'dual_control'}"] -- do not edit.
* 6AS6 dual-control pentode, the fit6as6 model6as6.dual_ip_ig2 law (OUR empirical extension of Koren's pentode, not a
* published model), fitted to the Raytheon 5725/6AS6W curves (https://frank.pocnet.net/sheets/138/5/5725.pdf).
* Expressions: fit6as6/emit_spice.py (as in fit6as6/results/6AS6_fit.inc), parameters and 2/pi to 17 digits, the
* internal ik0 / ik nodes inlined. Ik = cathode current, sh = plate share; Ip = Ik*sh, Ig2 = Ik*(1-sh).
{law} Capacitances: GE ET-T706 p.1 (https://frank.pocnet.net/sheets/084/6/6AS6.pdf),
* dk6as6/circuit.VALUES and VALVE_CAPS {where}.
* Pins: plate screen suppressor grid cathode
.SUBCKT {name} P G2 G3 G1 K
{sources}
{caps}
.ENDS {name}
"""


def tag(model, mode=MODE):
    """'dual' for P1 (the #118 names), 'dual_T' / 'dual_P2' for the other modes."""
    return model if mode == "P1" else f"{model}_{mode}"


def case(model, bias, mode=MODE):
    """The name of one reference, e.g. 'dual_0.45ma', 'dual_t_0.25ma': its wrdata file prefix (lower case, as
    ngspice's `set` lower-cases the sine file names)."""
    return f"{tag(model, mode)}_{bias:g}ma".lower()


def ref_file(model, bias, mode=MODE):
    return os.path.join(HERE, f"reference_{tag(model, mode)}_{bias:g}mA.json")


# #131 review: the issue asks for the #128 burst / blocking / recovery checks "at 8 V". The 4 V burst (BURST, #128)
# stays the references' main burst; BURST_AMPS adds an 8 V burst, as its own reference file per case
# (burst_ref_file), with the op point (for the check) and that burst only (run_burst).
BURST_AMPS = (8.0,)


def burst_of(amp):
    """spice/run_refs.BURST at amplitude `amp` V (4 V: BURST itself); same 1 kHz, 10-30 ms, 120 ms."""
    if amp != BURST["amp"] and amp not in BURST_AMPS:
        raise ValueError(f"burst amplitude {amp:g} V is neither BURST's {BURST['amp']:g} V nor in BURST_AMPS")
    return dict(BURST, amp=float(amp))


def burst_ref_file(model, bias, mode=MODE, amp=BURST_AMPS[0]):
    """The reference of the `amp` V burst (BURST_AMPS) of a case: reference_<tag>_<bias>mA_burst<amp>V.json."""
    return os.path.join(HERE, f"reference_{tag(model, mode)}_{bias:g}mA_burst{amp:g}V.json")


def split_analyses():
    """The 'main' analysis as separate ngspice runs (run(split=True), #112): 'opac' (.op and .ac) and one 'sine:<A>' per
    AMPS amplitude. Each tran of 'main' starts from its own operating point (its source differs only in the SIN
    amplitude), so each piece writes the same files as 'main' does; only the run time per ngspice call is shorter."""
    return ["opac"] + [f"sine:{a}" for a in AMPS]


def netlist(model, analysis, bias, out="out", values=VALUES, solver=SOLVER, rk=None, mode=MODE):
    """The complete ngspice netlist (text) of `analysis` ('main', 'burst', or a piece of 'main': 'opac', 'sine:<A>',
    split_analyses()) for `model` wired as `mode` at `bias` mA (Rk solved for it unless rk is given); wrdata goes to
    `out`/<case>_*."""
    check_mode(model, mode)
    inc, sub = models(model, mode)
    values = dict(values, Rk=rk_of(model, bias, mode) if rk is None else rk)
    name = case(model, bias, mode)
    ts, tm = f"{solver['tstep']:g}", f"{solver['tmax']:g}"
    f0 = f"{SINE['f0']:g}"
    if analysis.startswith("sine:"):
        a = analysis[5:]
        if a not in AMPS:
            raise ValueError(f"unknown sine amplitude {a!r}, expected one of {AMPS}")
        vin = f"Vin src 0 DC 0 AC 1 SIN(0 {a} {f0})"
        control = f"""tran {ts} {SINE['t_stop']:g} {SINE['t_save']:g} {tm}
wrdata {out}/{name}_sine_{a}.txt v(out) v(g) v(k)"""
    elif analysis in ("main", "opac"):
        vin = f"Vin src 0 DC 0 AC 1 SIN(0 {AMPS[0]} {f0})"
        g = AC_GRID
        edges = "".join(f"ac lin 1 {spice_num(fe)} {spice_num(fe)}\n"
                        f"wrdata {out}/{name}_ac_{fe:g}.txt db(v(out)) ph(v(out))\n" for fe in AC_EDGES_HZ)
        if MODES[mode]["screen"] == "EC2":
            cur = "let ia = -i(VHT)\nlet ig2 = -i(VG2)"
        else:   # T: HT feeds Ip + Ig2 through Ra; Ig2 is the current in Rstrap
            cur = f"let ig2 = (v(a)-v(g2))/{spice_num(values['Rstrap'])}\nlet ia = -i(VHT)-ig2"
        g1 = ""
        if HARDNESS[model]:     # the g1 current, through the grid stopper (the only path into g at DC)
            cur += f"\nlet ig1 = (v(gl)-v(g))/{spice_num(values['Rgs'])}"
            g1 = " ig1"
        control = f"""op
{cur}
wrdata {out}/{name}_op.txt v(a) v(k) v(g) v(gl) ia ig2{g1}
ac dec {g['points_per_decade']} {spice_num(g['f_start'])} {spice_num(g['f_stop'])}
wrdata {out}/{name}_ac.txt db(v(out)) ph(v(out))
{edges}""" + ("" if analysis == "opac" else f"""foreach A {' '.join(AMPS)}
  alter @Vin[sin] = [ 0 $A {f0} ]
  tran {ts} {SINE['t_stop']:g} {SINE['t_save']:g} {tm}
  set fn = {out}/{name}_sine_{{$A}}.txt
  wrdata $fn v(out) v(g) v(k)
end""")
        control = control.rstrip("\n")
    elif analysis == "burst" or analysis.startswith("burst:"):
        b = BURST if analysis == "burst" else burst_of(float(analysis[6:]))
        sfx = "" if analysis == "burst" else f"_{b['amp']:g}v"
        vin = f"Bin src 0 V = {b['amp']:g}*sin(2*3.14159265*{f0}*time)*((time>{b['t_on']:g})&(time<{b['t_off']:g}))"
        control = f"""tran {ts} {b['t_stop']:g} 0 {tm}
wrdata {out}/{name}_burst{sfx}.txt v(g) v(k) v(out) v(gl)"""
    else:
        raise ValueError(f"unknown analysis {analysis!r}")
    lines = [inc, f"VHT ht 0 DC {spice_num(values['HT'])}"]
    if MODES[mode]["screen"] == "EC2":
        lines.append(f"VG2 g2 0 DC {spice_num(values['EC2'])}")
    lines.append(vin)
    for name, p, q in elements(mode):
        val = f"{values[name]:.17g}" if name == "Rk" else spice_num(values[name])   # Rk: the solved double, exact
        lines.append(f"{name:<3} {p} {q} {val}")
        if name == VALVE_AFTER:
            lines.append(f"XV1 {' '.join(pins(mode))} {sub}")
    lines.append(" ".join([".options"] + [f"{k}={solver[k]:g}" for k in ("reltol", "abstol", "vntol", "chgtol")]
                          + [f"method={solver['method']}"]))
    wiring, screen = WIRING[mode]
    hdr = HEADER.format(model=model, mode_tag="" if mode == "P1" else f", {mode}", wiring=wiring, screen=screen,
                        analysis=analysis, bias=bias, valve=VALVES[model].format(v=variant(mode)),
                        ngbehavior=NGBEHAVIOR,
                        solver=", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
                                         for k, v in solver.items()))
    return hdr + "\n".join(lines) + "\n\n.control\n" + control + "\n.endc\n.end\n"


def spiceinit():
    """The .spiceinit ngspice reads from spice6as6/; must match spice/run_refs.NGBEHAVIOR (checked, not rewritten)."""
    txt = open(os.path.join(HERE, ".spiceinit")).read().strip()
    if txt != f"set ngbehavior={NGBEHAVIOR}":
        raise RuntimeError(f"spice6as6/.spiceinit is {txt!r}, expected 'set ngbehavior={NGBEHAVIOR}'")
    return txt


def model_file(model, mode=MODE):
    return os.path.join(HERE, "models", f"6as6_{model}{variant(mode)}.inc")


# The subckts and committed netlists: (model, mode) of each subckt variant in use; the modes with a committed netlist
SUBCKTS = [("dual", "P1"), ("r51", "P1"), ("dual", "T"),      # T and P2 share the g3 -> k variant
           ("dualg", "P1"), ("dualg", "T"), ("dualg_rsd2", "T"), ("dualg_ehx1", "T")]   # the #131 bounds: T only
NETLIST_MODES = ("P1", "T", "P2")


def write_netlist():
    os.makedirs(os.path.join(HERE, "models"), exist_ok=True)
    fns = []
    for m, mode in SUBCKTS:
        open(model_file(m, mode), "w").write(subckt(m, mode=mode))
        fns.append(model_file(m, mode))
    for mode in NETLIST_MODES:
        open(netlist_file(mode), "w").write(netlist("dual", "main", BIAS_MA, mode=mode))
        fns.append(netlist_file(mode))
    return fns


# Every ngspice call has a wall-clock limit (#111: two Rosetta ngspice children wrote all their output, then hung
# for ~2 h until killed). The longest analysis so far took 2.4 h with eight runs sharing the CPU (P2 0.1 mA main).
NGSPICE_TIMEOUT_S = float(os.environ.get("NGSPICE_TIMEOUT_S", 4 * 3600))
KILL_WAIT_S = 30.0


def ngspice(args, timeout=None):
    """(returncode, stdout + stderr) of `ngspice args` in spice6as6/. Past `timeout` seconds (NGSPICE_TIMEOUT_S) its
    process group is killed (SIGKILL), reaped with a bounded wait, and TimeoutError is raised: nothing waits forever,
    even on a child that does not die (then it is left to the OS and the error says so)."""
    timeout = NGSPICE_TIMEOUT_S if timeout is None else timeout
    p = subprocess.Popen([NGSPICE, *args], cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            p.communicate(timeout=KILL_WAIT_S)
            reaped = "killed"
        except subprocess.TimeoutExpired:
            reaped = f"killed but not reaped after {KILL_WAIT_S:g} s (pid {p.pid})"
        raise TimeoutError(f"ngspice {' '.join(args)} ran past {timeout:g} s: {reaped}") from None
    return p.returncode, out + err


def ngspice_version():
    _, txt = ngspice(["--version"], timeout=60)
    return next((ln.strip("* ").split(":")[0].strip() for ln in txt.splitlines() if "ngspice-" in ln), txt.strip())


def run(model, bias, solver=SOLVER, out="out", write=True, mode=MODE, split=False, jobs=1):
    """Simulate `model` wired as `mode` at `bias` mA with `solver` settings into spice6as6/<out>/ and analyse it;
    write=True (re)writes the committed reference_<tag>_<bias>mA.json, write=False is for convergence studies. Fails if
    models/ is stale. split=True (#112) runs 'main' as split_analyses(), the same analyses as shorter ngspice calls,
    `jobs` at a time (each still under NGSPICE_TIMEOUT_S); every call is waited for before a failure is raised, so
    nothing is left running. A model / mode with no subckt (check_mode) raises before anything is written.
    Raw output, one immutable directory per run (PR #128 review): ngspice writes into a fresh staging directory, the
    run's generator record is saved beside the traces, and the staged run is ANALYSED IN PLACE, so a missing or
    unreadable trace fails the run (whatever ngspice's exit code) before anything is published. Only then is the
    directory renamed to <out>/<case>_run_<id>/ and the pointer <out>/<case>.current (run_dir) replaced atomically to
    name it. A published run directory is never written again; the previous one is kept until the next publish, so a
    reader that resolved the pointer (--analyse-only) reads one run's record and traces, never a mixture. A failed run
    deletes its staging directory and leaves the pointer and every published run untouched."""
    check_mode(model, mode)
    if open(model_file(model, mode)).read() != subckt(model, mode=mode):
        raise RuntimeError(f"{model_file(model, mode)} is stale; run python spice6as6/run_refs.py --write-netlist")
    o = os.path.join(HERE, out)
    os.makedirs(o, exist_ok=True)
    spiceinit()
    rk = rk_of(model, bias, mode)
    c = case(model, bias, mode)
    rid = f"{time.strftime('%Y%m%dT%H%M%S')}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    stage = os.path.join(out, f"_staging_{c}_{rid}")      # relative to HERE, as `out` (or absolute)
    sdir = os.path.join(HERE, stage)
    os.makedirs(sdir)
    def one(name):
        tagn = name.replace(":", "_")
        fn = os.path.join(HERE, f"_gen_{out.replace('/', '_')}_{c}_{rid}_{tagn}.cir")   # unique per run
        open(fn, "w").write(netlist(model, name, bias, out=stage, solver=solver, rk=rk, mode=mode))
        try:
            code, log = ngspice(["-b", fn])
        finally:
            os.remove(fn)
        open(os.path.join(o, f"{c}_{tagn}_log.txt"), "w").write(log)
        if code != 0:
            raise RuntimeError(f"ngspice failed on {name} ({c}), see {o}/{c}_{tagn}_log.txt")
    try:
        if not split:
            for name in ANALYSES:   # one at a time: a failure stops the run with nothing left running (PR #120 review)
                one(name)
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(jobs) as ex:     # leaving the block waits for every call (each has its timeout)
                futs = [ex.submit(one, name) for name in split_analyses() + ["burst"]]
            errs = [f.exception() for f in futs if f.exception() is not None]
            if errs:
                raise errs[0]
        gen = dict(generator(model, bias, solver, mode, split, rk), run_id=rid)
        json.dump(gen, open(os.path.join(sdir, GENERATOR), "w"), indent=1)
        res = analyse(model, bias, stage, gen, False, mode)   # every trace present and readable, or raise
        publish(sdir, o, c, rid)
    finally:
        shutil.rmtree(sdir, ignore_errors=True)            # gone if published (renamed); removed if not
    if write:
        json.dump(res, open(ref_file(model, bias, mode), "w"), indent=1)
    return res


def run_burst(model, bias, amp=BURST_AMPS[0], solver=SOLVER, out="out", mode=MODE):
    """The `amp` V burst reference of a case (BURST_AMPS; #131 review): 'opac' (the op point, checked against the main
    reference's) and 'burst:<amp>' as two ngspice calls, staged, analysed in place and published as run() does
    (case <case>_burst<amp>v), then written to burst_ref_file. Same netlist, Rk, solver and spiceinit as run()."""
    check_mode(model, mode)
    if open(model_file(model, mode)).read() != subckt(model, mode=mode):
        raise RuntimeError(f"{model_file(model, mode)} is stale; run python spice6as6/run_refs.py --write-netlist")
    b = burst_of(amp)
    o = os.path.join(HERE, out)
    os.makedirs(o, exist_ok=True)
    spiceinit()
    rk = rk_of(model, bias, mode)
    c = case(model, bias, mode)
    cb = f"{c}_burst{amp:g}v"
    rid = f"{time.strftime('%Y%m%dT%H%M%S')}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    stage = os.path.join(out, f"_staging_{cb}_{rid}")
    sdir = os.path.join(HERE, stage)
    os.makedirs(sdir)
    def one(name):
        tagn = name.replace(":", "_")
        fn = os.path.join(HERE, f"_gen_{out.replace('/', '_')}_{cb}_{rid}_{tagn}.cir")
        open(fn, "w").write(netlist(model, name, bias, out=stage, solver=solver, rk=rk, mode=mode))
        try:
            code, log = ngspice(["-b", fn])
        finally:
            os.remove(fn)
        open(os.path.join(o, f"{cb}_{tagn}_log.txt"), "w").write(log)
        if code != 0:
            raise RuntimeError(f"ngspice failed on {name} ({cb}), see {o}/{cb}_{tagn}_log.txt")
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as ex:
            futs = [ex.submit(one, n) for n in ("opac", f"burst:{amp:g}")]
        errs = [f.exception() for f in futs if f.exception() is not None]
        if errs:
            raise errs[0]
        gen = dict(generator(model, bias, solver, mode, False, rk), burst=b, analyses=["opac", f"burst:{amp:g}"],
                   run_id=rid)
        del gen["sine"]
        json.dump(gen, open(os.path.join(sdir, GENERATOR), "w"), indent=1)
        res = analyse_burst(model, bias, amp, stage, gen, mode)
        publish(sdir, o, cb, rid)
    finally:
        shutil.rmtree(sdir, ignore_errors=True)
    json.dump(res, open(burst_ref_file(model, bias, mode, amp), "w"), indent=1)
    return res


def analyse_burst(model, bias, amp, out, generator, mode=MODE):
    """The op point and the `amp` V burst metrics (burst_metrics, as analyse()) of a run_burst run directory."""
    o = os.path.join(HERE, out)
    c = case(model, bias, mode)
    op = np.loadtxt(os.path.join(o, f"{c}_op.txt"))
    va, vk, vg, ia, ig2 = op[1], op[3], op[5], op[9], op[11]
    ig1 = op[13] if HARDNESS[model] else None
    t, v = load_wrdata(os.path.join(o, f"{c}_burst_{amp:g}v.txt"), 4)
    tu, g = resample_uniform(t, v[:, 0], 1e6, 0.0, BURST["t_stop"])
    _, k = resample_uniform(t, v[:, 1], 1e6, 0.0, BURST["t_stop"])
    return dict(model=model, bias_ma=bias, burst_amp_v=float(amp), generator=generator,
                **({} if mode == "P1" else dict(mode=mode)),
                op=dict(va=va, vk=vk, ia=ia, ig2=ig2, ik=ia + ig2 + (ig1 or 0.0), vgk=vg - vk,
                        **({} if ig1 is None else dict(ig1=ig1))),
                burst=burst_metrics(tu, g - k))


GENERATOR = "generator.json"          # the run's generator record, inside its run directory


def publish(sdir, o, c, rid):
    """Publish a complete, analysed staged run of case c: rename it to <o>/<c>_run_<rid> (atomic, one filesystem), then
    replace the pointer <o>/<c>.current atomically; delete the run directories of c older than the one just
    superseded (kept so a reader that resolved it can finish). The three steps hold an exclusive per-case lock
    (<o>/<c>.lock), so two publishers of one case cannot interleave: pruning never removes a directory another
    publisher is about to point at (PR #128 review)."""
    with open(os.path.join(o, f"{c}.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                   # released when the file closes
        new = os.path.join(o, f"{c}_run_{rid}")
        os.rename(sdir, new)
        ptr = os.path.join(o, f"{c}.current")
        prev = open(ptr).read().strip() if os.path.exists(ptr) else None
        tmp = f"{ptr}.{rid}.tmp"
        open(tmp, "w").write(os.path.basename(new) + "\n")
        os.replace(tmp, ptr)
        keep = {os.path.basename(new), prev}
        for d in os.listdir(o):
            if d.startswith(f"{c}_run_") and d not in keep:
                shutil.rmtree(os.path.join(o, d), ignore_errors=True)


def run_dir(model, bias, out="out", mode=MODE):
    """The current published run directory of a case, relative to HERE as `out` (resolved once: read the pointer,
    then read only inside the directory it names, which is never modified); None if no run was published."""
    ptr = os.path.join(HERE, out, f"{case(model, bias, mode)}.current")
    if not os.path.exists(ptr):
        return None
    return os.path.join(out, open(ptr).read().strip())


def analyse_published(model, bias, out="out", write=True, mode=MODE):
    """--analyse-only: re-analyse the current published run with its own generator record (both from one run
    directory, PR #128 review)."""
    d = run_dir(model, bias, out, mode)
    if d is None:
        raise FileNotFoundError(f"no published run of {case(model, bias, mode)} in {out}/ (no .current pointer)")
    gen = json.load(open(os.path.join(HERE, d, GENERATOR)))
    return analyse(model, bias, d, gen, write, mode)


def generator(model, bias, solver=SOLVER, mode=MODE, split=False, rk=None):
    """The reference JSON's "generator" record of a run()."""
    rk = rk_of(model, bias, mode) if rk is None else rk
    gen = dict(script="spice6as6/run_refs.py", ngspice=ngspice_version(), solver=solver, sine=SINE, burst=BURST,
               ac=dict(AC_GRID, edges_hz=AC_EDGES_HZ), spiceinit=spiceinit(), params=PARAMS[model], bias_ma=bias,
               rk=rk)
    if split:
        gen["split"] = split_analyses() + ["burst"]
    if HARDNESS[model]:
        gen["grid"] = grid_of(model)
    if mode != "P1":
        gen["mode"] = mode
    return gen


def analyse(model, bias, out="out", generator=None, write=True, mode=MODE):
    o = os.path.join(HERE, out)
    c = case(model, bias, mode)
    op = np.loadtxt(os.path.join(o, f"{c}_op.txt"))   # (scale, value) pairs, no header
    va, vk, vg, vgl, ia, ig2 = op[1], op[3], op[5], op[7], op[9], op[11]
    ig1 = op[13] if HARDNESS[model] else None
    ac = np.vstack([np.loadtxt(os.path.join(o, fn), ndmin=2) for fn in
                    [f"{c}_ac.txt"] + [f"{c}_ac_{fe:g}.txt" for fe in AC_EDGES_HZ]])
    ac = ac[np.argsort(ac[:, 0], kind="stable")]
    res = dict(model=model, bias_ma=bias, generator=generator, **({} if mode == "P1" else dict(mode=mode)),
               op=dict(va=va, vk=vk, vg=vg, vgl=vgl, ia=ia, ig2=ig2, ik=ia + ig2 + (ig1 or 0.0), vgk=vg - vk,
                       **({} if ig1 is None else dict(ig1=ig1))),
               ac=dict(f=ac[:, 0].tolist(), db=ac[:, 1].tolist()), sine={})
    for a in AMPS:
        t, v = load_wrdata(os.path.join(o, f"{c}_sine_{a}.txt"), 3)
        _, y = resample_uniform(t, v[:, 0], SINE["fs_resample"], SINE["t_save"], SINE["t_stop"])
        h = harmonics(y, SINE["fs_resample"], SINE["f0"], SINE["ncycles"])
        res["sine"][a] = dict(H_peak_V=h[:5].tolist(), H_dBc=db_rel(h)[:5].tolist(), thd_pct=100 * thd(h))
    t, v = load_wrdata(os.path.join(o, f"{c}_burst.txt"), 4)
    tu, g = resample_uniform(t, v[:, 0], 1e6, 0.0, BURST["t_stop"])
    _, k = resample_uniform(t, v[:, 1], 1e6, 0.0, BURST["t_stop"])
    res["burst"] = burst_metrics(tu, g - k)
    if write:   # committed reference data
        json.dump(res, open(ref_file(model, bias, mode), "w"), indent=1)
    return res


# Convergence check of a committed reference (as spice/RESULTS.md v3): 10x tighter tolerances and half the time step
TIGHT = dict(SOLVER, reltol=SOLVER["reltol"] / 10, abstol=SOLVER["abstol"] / 10, vntol=SOLVER["vntol"] / 10,
             chgtol=SOLVER["chgtol"] / 10, tstep=SOLVER["tstep"] / 2, tmax=SOLVER["tmax"] / 2)


def convergence(model="dual", bias=BIAS_MA, mode=MODE):
    """Rerun `model` wired as `mode` at `bias` with TIGHT (not written) and compare it with the committed reference;
    writes convergence_<tag>_<bias>mA.json. H2-H5 differences are in dB, the rest relative."""
    b = run(model, bias, solver=TIGHT, out="out_tight", write=False, mode=mode)
    a = json.load(open(ref_file(model, bias, mode)))
    d = dict(model=model, bias_ma=bias, solver=TIGHT, **({} if mode == "P1" else dict(mode=mode)),
             op_rel={k: abs(b["op"][k] / a["op"][k] - 1) for k in ("va", "vk", "ia", "ig2")},
             ac_max_db=float(np.max(np.abs(np.array(b["ac"]["db"]) - np.array(a["ac"]["db"])))),
             harm_db={x: {f"H{h + 1}": dict(ref_dBc=a["sine"][x]["H_dBc"][h],
                                             diff_db=b["sine"][x]["H_dBc"][h] - a["sine"][x]["H_dBc"][h])
                          for h in range(1, 5)} for x in AMPS})
    sa, sb = a["burst"], b["burst"]
    shift = sa["min_vgk"] - sa["rest_vgk"]
    d["burst"] = dict(shift_rel=abs((sb["min_vgk"] - sb["rest_vgk"]) / shift - 1),
                      tau_rel=abs(sb["recovery_tau_s"] / sa["recovery_tau_s"] - 1),
                      traj_rel=float(np.sqrt(np.mean((np.array(sb["traj_vgk"]) - sb["rest_vgk"]
                                                      - np.array(sa["traj_vgk"]) + sa["rest_vgk"]) ** 2)) / abs(shift)))
    json.dump(d, open(os.path.join(HERE, f"convergence_{tag(model, mode)}_{bias:g}mA.json"), "w"), indent=1)
    return d


def biases_of(model, mode=MODE):
    """The reference currents (mA) of `model` wired as `mode`."""
    if model == "dualg":
        return GRID_BIASES[mode]
    if model in GRID_SHAPE:
        return BOUND_BIASES.get(mode, ())
    return BIASES[model] if mode == "P1" else MODE_BIASES[mode]


def cases(args):
    """[(model, mode, bias)] from command-line args model[/mode][:bias], e.g. 'dual', 'dual:0.25', 'dual/T',
    'dual/P2:0.45'; a bare model or model/mode means every bias of BIASES (P1) or MODE_BIASES. An explicit mode or
    bias a #131 bound has no subckt for raises (check_mode, PR #139 review); a bare bound model (no mode) is its P1
    case list, empty, so the default "every model" run still skips it."""
    out = []
    for arg in args:
        mm, _, b = arg.partition(":")
        m, sep, mode = mm.partition("/")
        mode = mode or "P1"
        if sep or b:
            check_mode(m, mode)
        biases = biases_of(m, mode)
        out += [(m, mode, float(b))] if b else [(m, mode, x) for x in biases]
    return out


if __name__ == "__main__":
    if sys.argv[1:2] == ["--convergence"]:
        for mdl, md, bias in cases(sys.argv[2:] or ["dual"]):
            print(json.dumps(convergence(mdl, bias, md), indent=1))
        sys.exit(0)
    if sys.argv[1:] == ["--write-netlist"]:
        print("wrote", *write_netlist())
        sys.exit(0)
    args, split = sys.argv[1:], 0
    if "--split" in args:          # --split N: run 'main' as split_analyses(), N ngspice calls at a time (#112)
        i = args.index("--split")
        split, args = int(args[i + 1]), args[:i] + args[i + 2:]
    if args[:1] == ["--burst8"]:   # the 8 V burst references (#131 review): --burst8 model/mode:bias ...
        for mdl, md, bias in cases(args[1:]):
            r = run_burst(mdl, bias, 8.0, mode=md)
            print(case(mdl, bias, md), "8 V burst", {k: r["burst"][k] for k in ("rest_vgk", "min_vgk", "t_min")})
        sys.exit(0)
    analyse_only = "--analyse-only" in args      # re-analyse a finished run's out/ files (no ngspice)
    args = [a for a in args if a != "--analyse-only"]
    for mdl, md, bias in cases(args or list(PARAMS)):
        if analyse_only:     # the current published run, record and traces from its own directory
            r = analyse_published(mdl, bias, "out", True, md)
        else:
            r = run(mdl, bias, mode=md, split=bool(split), jobs=max(split, 1))
        print(case(mdl, bias, md), r["op"], {a: round(s["thd_pct"], 3) for a, s in r["sine"].items()},
              {k: r["burst"][k] for k in ("rest_vgk", "min_vgk", "t_min", "recovery_tau_s")})
