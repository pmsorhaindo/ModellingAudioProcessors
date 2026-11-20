"""Generate the 6AS6 stage's ngspice netlists, run the same-model references, write
reference_<model>[_<mode>]_<bias>mA.json (#26, #102, #111).

  model 'dual' : the fit6as6 dual_control law (issue #55) with the DK stage's own parameters -> reference (ii), at
                 each bias in BIASES["dual"] (target cathode current, mA)
  model 'r51'  : the same law with the issue #51 parameters (refit_51: KR = 0, XA = 1), for the #51 vs #55 harmonic
                 comparison (dk6as6/compare_fits.py); checked against the DK stage like (ii), not a pass criterion
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
import json
import math
import os
import subprocess
import sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from analysis import load_wrdata, resample_uniform, harmonics, thd, db_rel  # noqa: E402 -- after the sys.path setup
from spice.run_refs import (SOLVER, SINE, BURST, AC_GRID, AC_EDGES_HZ, AMPS, NGBEHAVIOR, NGSPICE, spice_num,  # noqa: E402 -- after the sys.path setup
                            burst_metrics, ngspice_version)
from dk6as6.circuit import VALUES, MODES, MODE, valve_cap_pins  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import cathode_resistor, BIAS_MA  # noqa: E402 -- after the sys.path setup
sys.path.insert(0, pentode.FIT6AS6)
import emit_spice  # noqa: E402  (fit6as6's SPICE expressions)

PARAMS = {"dual": pentode.DUAL_CONTROL, "r51": pentode.REFIT_51}
BIASES = {"dual": (0.1, 0.25, 0.45, 0.6), "r51": (0.45,)}   # target cathode currents of the P1 references, mA (#110:
                                                           # the range ends, lowest distortion, default)
MODE_BIASES = {"P1": BIASES["dual"], "T": (0.1, 0.25, 0.45, 0.6), "P2": (0.1, 0.25, 0.45, 0.6)}   # dual model (#111)
SUBCKT = {"dual": "6AS6_DUAL", "r51": "6AS6_R51"}
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
          "r51": "fit6as6 refit_51 (issue #51) behavioural subckt (models/6as6_r51{v}.inc)"}


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


def subckt(model, values=VALUES, mode=MODE):
    """The text of models/6as6_<model><variant(mode)>.inc."""
    ik, sh = expressions(PARAMS[model])
    caps = "\n".join(f"{k} {p} {n} {spice_num(values[k])}" for k, p, n in valve_cap_pins(mode))
    name = models(model, mode)[1]
    where = ("(the g3 input capacitance g3 -> g2, see dk6as6/circuit.py)" if not variant(mode) else
             "(the g3 input capacitance g3 -> k: T and P2, g3 off the cathode; see dk6as6/circuit.py)")
    return f"""* GENERATED by spice6as6/run_refs.py from prototypes/fit6as6/results/fit_results.json["{
        'dual_control' if model == 'dual' else 'refit_51'}"] -- do not edit.
* 6AS6 dual-control pentode, the fit6as6 model6as6.dual_ip_ig2 law (OUR empirical extension of Koren's pentode, not a
* published model), fitted to the Raytheon 5725/6AS6W curves (https://frank.pocnet.net/sheets/138/5/5725.pdf).
* Expressions: fit6as6/emit_spice.py (as in fit6as6/results/6AS6_fit.inc), parameters and 2/pi to 17 digits, the
* internal ik0 / ik nodes inlined. Ik = cathode current, sh = plate share; Ip = Ik*sh, Ig2 = Ik*(1-sh).
* No g1 / g3 grid current. Capacitances: GE ET-T706 p.1 (https://frank.pocnet.net/sheets/084/6/6AS6.pdf),
* dk6as6/circuit.VALUES and VALVE_CAPS {where}.
* Pins: plate screen suppressor grid cathode
.SUBCKT {name} P G2 G3 G1 K
Bp P K I=({ik})*({sh})
Bg2 G2 K I=({ik})*(1-({sh}))
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


def netlist(model, analysis, bias, out="out", values=VALUES, solver=SOLVER, rk=None, mode=MODE):
    """The complete ngspice netlist (text) of `analysis` ('main' or 'burst') for `model` wired as `mode` at `bias` mA
    (Rk solved for it unless rk is given); wrdata goes to `out`/<case>_*."""
    inc, sub = models(model, mode)
    values = dict(values, Rk=cathode_resistor(bias, PARAMS[model], mode=mode) if rk is None else rk)
    name = case(model, bias, mode)
    ts, tm = f"{solver['tstep']:g}", f"{solver['tmax']:g}"
    f0 = f"{SINE['f0']:g}"
    if analysis == "main":
        vin = f"Vin src 0 DC 0 AC 1 SIN(0 {AMPS[0]} {f0})"
        g = AC_GRID
        edges = "".join(f"ac lin 1 {spice_num(fe)} {spice_num(fe)}\n"
                        f"wrdata {out}/{name}_ac_{fe:g}.txt db(v(out)) ph(v(out))\n" for fe in AC_EDGES_HZ)
        if MODES[mode]["screen"] == "EC2":
            cur = "let ia = -i(VHT)\nlet ig2 = -i(VG2)"
        else:   # T: HT feeds Ip + Ig2 through Ra; Ig2 is the current in Rstrap
            cur = f"let ig2 = (v(a)-v(g2))/{spice_num(values['Rstrap'])}\nlet ia = -i(VHT)-ig2"
        control = f"""op
{cur}
wrdata {out}/{name}_op.txt v(a) v(k) v(g) v(gl) ia ig2
ac dec {g['points_per_decade']} {spice_num(g['f_start'])} {spice_num(g['f_stop'])}
wrdata {out}/{name}_ac.txt db(v(out)) ph(v(out))
{edges}foreach A {' '.join(AMPS)}
  alter @Vin[sin] = [ 0 $A {f0} ]
  tran {ts} {SINE['t_stop']:g} {SINE['t_save']:g} {tm}
  set fn = {out}/{name}_sine_{{$A}}.txt
  wrdata $fn v(out) v(g) v(k)
end"""
    elif analysis == "burst":
        b = BURST
        vin = f"Bin src 0 V = {b['amp']:g}*sin(2*3.14159265*{f0}*time)*((time>{b['t_on']:g})&(time<{b['t_off']:g}))"
        control = f"""tran {ts} {b['t_stop']:g} 0 {tm}
wrdata {out}/{name}_burst.txt v(g) v(k) v(out) v(gl)"""
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
SUBCKTS = [("dual", "P1"), ("r51", "P1"), ("dual", "T")]      # T and P2 share the g3 -> k variant
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


def run(model, bias, solver=SOLVER, out="out", write=True, mode=MODE):
    """Simulate `model` wired as `mode` at `bias` mA with `solver` settings into spice6as6/<out>/ and analyse it;
    write=True (re)writes the committed reference_<tag>_<bias>mA.json, write=False is for convergence studies. Fails if
    models/ is stale."""
    if open(model_file(model, mode)).read() != subckt(model, mode=mode):
        raise RuntimeError(f"{model_file(model, mode)} is stale; run python spice6as6/run_refs.py --write-netlist")
    o = os.path.join(HERE, out)
    os.makedirs(o, exist_ok=True)
    init = spiceinit()
    rk = cathode_resistor(bias, PARAMS[model], mode=mode)
    c = case(model, bias, mode)
    for name in ANALYSES:   # one at a time: a failure stops the run with nothing left running (PR #120 review)
        fn = os.path.join(HERE, f"_gen_{out.replace('/', '_')}_{c}_{name}.cir")
        open(fn, "w").write(netlist(model, name, bias, out=out, solver=solver, rk=rk, mode=mode))
        r = subprocess.run([NGSPICE, "-b", fn], cwd=HERE, capture_output=True, text=True)
        open(os.path.join(o, f"{c}_{name}_log.txt"), "w").write(r.stdout + r.stderr)
        os.remove(fn)
        if r.returncode != 0:
            raise RuntimeError(f"ngspice failed on {name} ({c}), see {o}/{c}_{name}_log.txt")
    gen = dict(script="spice6as6/run_refs.py", ngspice=ngspice_version(), solver=solver, sine=SINE, burst=BURST,
               ac=dict(AC_GRID, edges_hz=AC_EDGES_HZ), spiceinit=init, params=PARAMS[model], bias_ma=bias,
               rk=rk)
    if mode != "P1":
        gen["mode"] = mode
    return analyse(model, bias, out, gen, write, mode)


def analyse(model, bias, out="out", generator=None, write=True, mode=MODE):
    o = os.path.join(HERE, out)
    c = case(model, bias, mode)
    op = np.loadtxt(os.path.join(o, f"{c}_op.txt"))   # (scale, value) pairs, no header
    va, vk, vg, vgl, ia, ig2 = op[1], op[3], op[5], op[7], op[9], op[11]
    ac = np.vstack([np.loadtxt(os.path.join(o, fn), ndmin=2) for fn in
                    [f"{c}_ac.txt"] + [f"{c}_ac_{fe:g}.txt" for fe in AC_EDGES_HZ]])
    ac = ac[np.argsort(ac[:, 0], kind="stable")]
    res = dict(model=model, bias_ma=bias, generator=generator, **({} if mode == "P1" else dict(mode=mode)),
               op=dict(va=va, vk=vk, vg=vg, vgl=vgl, ia=ia, ig2=ig2, ik=ia + ig2, vgk=vg - vk),
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


def cases(args):
    """[(model, mode, bias)] from command-line args model[/mode][:bias], e.g. 'dual', 'dual:0.25', 'dual/T',
    'dual/P2:0.45'; a bare model or model/mode means every bias of BIASES (P1) or MODE_BIASES."""
    out = []
    for arg in args:
        mm, _, b = arg.partition(":")
        m, _, mode = mm.partition("/")
        mode = mode or "P1"
        biases = BIASES[m] if mode == "P1" else MODE_BIASES[mode]
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
    for mdl, md, bias in cases(sys.argv[1:] or list(PARAMS)):
        r = run(mdl, bias, mode=md)
        print(case(mdl, bias, md), r["op"], {a: round(s["thd_pct"], 3) for a, s in r["sine"].items()},
              {k: r["burst"][k] for k in ("rest_vgk", "min_vgk", "t_min", "recovery_tau_s")})
