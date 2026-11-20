"""Generated 6AS6 ngspice netlists and valve subckts vs dk6as6/circuit.VALUES, circuit.MODES, spice/run_refs.SOLVER
and the fit (#26, #102, #111). No ngspice needed. Uses the 12AX7 netlist test's small SPICE parser
(spice/tests/test_netlists.py), not the generator. Checks every component value (Rk: the one the DK stage solves for the
reference's bias) and the topology against circuit.netlist(), the screen source or strap and each mode's wiring (P1 g3
on the cathode, T g2 + g3 strapped to the plate, P2 g3 on ground), the .options and tran steps, the committed GENERATED
files and references, and that the subckts' B-source expressions, evaluated here, ARE the DK stage's valve law
(dk6as6/pentode.py = fit6as6 model6as6.dual_ip_ig2) with the same parameters.
Run from prototypes/fullstage:  python -m pytest -q spice6as6/tests
"""
import itertools
import json
import math
import os
import re
import sys
import time
import numpy as np
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from spice.tests.test_netlists import parse, num, close, edges, node, subckt, lines  # noqa: E402 -- after the sys.path setup
from spice import run_refs as R12  # noqa: E402 -- after the sys.path setup
from dk6as6 import circuit, pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import cathode_resistor, BIAS_MA  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

VALUES = circuit.VALUES
CASES = R.cases(list(R.PARAMS) + [f"dual/{m}" for m in R.MODE_BIASES if m != "P1"]   # every committed reference:
                + [f"dualg/{m}" for m in R.GRID_BIASES if m != "P1"])                  # (model, mode, bias mA)
RK = {(m, md, b): cathode_resistor(b, R.PARAMS[m], mode=md, grid_hardness=R.HARDNESS[m]) for m, md, b in CASES}


def with_rk(model, bias=BIAS_MA, values=VALUES, mode="P1"):
    return dict(values, Rk=RK[model, mode, bias])


def check(text, model, analysis, values, solver=R12.SOLVER, mode="P1"):
    """All divergences of netlist `text` from `values` (circuit.VALUES plus Rk) / `solver` / circuit.MODES[mode]
    (empty list = consistent)."""
    err = []
    p = parse(text)
    el = p["elements"]
    inc, sub = R.models(model, mode)
    if p["includes"] != [inc.split(None, 1)[1]]:
        err.append(f"includes {p['includes']} != {inc}")
    want_pins = [circuit.MODES[mode]["pins"][q] or "0" for q in R.SUBCKT_PINS]
    if el.get("XV1") != want_pins + [sub]:
        err.append(f"XV1 {el.get('XV1')} != {want_pins + [sub]}")
    stiff = circuit.MODES[mode]["screen"] == "EC2"
    if not stiff and "VG2" in el:
        err.append(f"a screen supply VG2 in {mode}, whose screen is strapped to the plate")
    for name, n, key in (("VHT", "ht", "HT"),) + ((("VG2", "g2", "EC2"),) if stiff else ()):
        t = el.get(name, [])
        if (t[:3] != [n, "0", "DC"] or len(t) != 4
                or not (num(t[3]) == values[key] == 0 or close(num(t[3]), values[key]))):
            err.append(f"{name} {t} != {n} 0 DC {values[key]}")
    passives = {k: v for k, v in el.items() if k[0] in "RC"}
    for name, tok in passives.items():
        key = next((k for k in values if k.upper() == name), None)
        if key is None:
            err.append(f"element {name} is not a circuit.VALUES key or Rk")
        elif len(tok) != 3 or not close(num(tok[2]), values[key]):
            err.append(f"{name} = {tok[2:]} != VALUES[{key!r}] = {values[key]:g}")
    want_passives = circuit.PASSIVES + ("Rk",) + (() if stiff else ("Rstrap",))
    missing = {k.upper() for k in want_passives} - set(passives) - {"HT"}
    if missing:
        err.append(f"VALUES keys missing from the netlist: {sorted(missing)}")
    extra = set(passives) - {k.upper() for k in want_passives}
    if extra:
        err.append(f"elements not in {mode}'s netlist: {sorted(extra)}")
    R_, Cs, U, _, _, _ = circuit.netlist(values["Rk"], v=values, mode=mode)
    n_valve = len(circuit.valve_caps(values, mode))             # netlist() lists the valve's caps last
    want = edges((kind, p_, q, val) for kind, es in (("R", R_), ("C", Cs[:len(Cs) - n_valve])) for p_, q, val in es)
    have = edges((name[0], node(t[0]), node(t[1]), num(t[2])) for name, t in passives.items() if len(t) == 3)
    for e in sorted(set(want) | set(have)):
        if e not in have or e not in want or not close(have[e], want[e]):
            err.append(f"{e[0]} between {e[1]}: netlist {have.get(e)} != circuit.netlist() {want.get(e)}")
    if U != ["src", "ht"] + (["g2"] if stiff else []):
        err.append(f"circuit.netlist() sources {U} changed; update this test")
    vin = "VIN" if analysis == "main" else "BIN"
    if el.get(vin, [])[:2] != ["src", "0"]:
        err.append(f"input source {vin} for {analysis}: {el.get(vin)}")
    extra = set(el) - set(passives) - {"VHT", "VG2", "XV1", vin}
    if extra:
        err.append(f"unexpected elements {sorted(extra)}")
    o = p["options"]
    for k in ("reltol", "abstol", "vntol", "chgtol"):
        if k not in o or not close(num(o[k]), solver[k]):
            err.append(f".options {k}={o.get(k)} != SOLVER {solver[k]:g}")
    if o.get("method") != solver["method"] or set(o) != {"reltol", "abstol", "vntol", "chgtol", "method"}:
        err.append(f".options {o} != SOLVER {solver}")
    trans = [c.split()[1:] for c in p["control"] if c.split()[0].lower() == "tran"]
    want_tran = ([[R12.SINE["t_stop"], R12.SINE["t_save"]]] if analysis == "main" else [[R12.BURST["t_stop"], 0.0]])
    if len(trans) != len(want_tran):
        err.append(f"{len(trans)} tran lines, expected {len(want_tran)}")
    for t, (stop, start) in zip(trans, want_tran):
        got = [num(x) for x in t]
        if len(got) != 4 or not all(close(a, b) or a == b == 0 for a, b in
                                    zip(got, [solver["tstep"], stop, start, solver["tmax"]])):
            err.append(f"tran {t} != step {solver['tstep']:g} stop {stop:g} start {start:g} max {solver['tmax']:g}")
    if analysis == "main":
        fe = [c.split()[2:] for c in p["control"] if c.lower().startswith("foreach")]
        if fe != [R12.AMPS]:
            err.append(f"sine amplitudes {fe} != {R12.AMPS}")
    return err


# --- the generated netlists -------------------------------------------------------------------------------

@pytest.mark.parametrize("analysis", R.ANALYSES)
@pytest.mark.parametrize("model, mode, bias", CASES)
def test_generated_netlist_matches_values_and_solver(model, mode, bias, analysis):
    assert check(R.netlist(model, analysis, bias, mode=mode), model, analysis, with_rk(model, bias, mode=mode),
                 mode=mode) == []


def test_p1_wiring_suppressor_on_cathode():
    """P1: g3 on the cathode in both descriptions; no g3 source; the Vg3k row of the DK incidence is zero."""
    pins = circuit.MODES["P1"]["pins"]
    assert R.PINS == R.pins("P1") == ("a", "g2", "k", "g", "k") and pins["G3"] == pins["K"] == "k"
    assert "g3" not in circuit.NODES and "EC3" not in VALUES
    assert parse(R.netlist("dual", "main", BIAS_MA))["elements"]["XV1"][:5] == ["a", "g2", "k", "g", "k"]
    m = circuit.build(48000.0 * 8, RK["dual", "P1", BIAS_MA])
    assert not m["G"][2].any() and not m["H"][2].any() and not m["K"][2].any()


def test_t_wiring_screen_and_suppressor_strapped_to_the_plate():
    """T: g2 and g3 on one node, tied to the plate by Rstrap; no screen supply (u = vin, HT); in the DK incidence
    Vg3k is Vg2k."""
    el = parse(R.netlist("dual", "main", BIAS_MA, mode="T"))["elements"]
    assert el["XV1"][:5] == ["a", "g2", "g2", "g", "k"] and "VG2" not in el
    assert el["RSTRAP"][:2] == ["g2", "a"] and close(num(el["RSTRAP"][2]), VALUES["Rstrap"])
    assert VALUES["Rstrap"] == 100.0                    # the #102 table's stopper
    m = circuit.build(48000.0 * 8, RK["dual", "T", BIAS_MA], mode="T")
    assert m["H"].shape == (4, 2)
    for k in "GHK":
        assert np.array_equal(m[k][2], m[k][1]), k


def test_p2_wiring_suppressor_on_ground():
    """P2 (our hypothesis): g3 on ground, the screen at EC2; in the DK incidence Vg3k = -Vk."""
    el = parse(R.netlist("dual", "main", BIAS_MA, mode="P2"))["elements"]
    assert el["XV1"][:5] == ["a", "g2", "0", "g", "k"] and el["VG2"][:2] == ["g2", "0"] and "RSTRAP" not in el
    nv = circuit._inc(circuit.nlv("P2"))
    assert nv[2].tolist() == [-1.0 if n == "k" else 0.0 for n in circuit.NODES]


def _rk_apart(text):
    """(text with the Rk value blanked, Rk): the solved Rk is a Brent root whose last digits differ between platforms'
    libm (the CI Linux runner and macOS differ at ~1e-14 relative), so it is compared numerically."""
    m = re.search(r"^Rk  k 0 (\S+)$", text, re.M)
    return text[:m.start(1)] + "<Rk>" + text[m.end(1):], float(m.group(1))


def close_rk(a, b):
    return math.isclose(a, b, rel_tol=1e-12)


@pytest.mark.parametrize("mode", R.NETLIST_MODES)
def test_committed_files_are_generated_and_current(mode):
    fn = R.netlist_file(mode)
    txt = open(fn).read()
    assert txt.startswith("* GENERATED by spice6as6/run_refs.py")
    have, want = _rk_apart(txt), _rk_apart(R.netlist("dual", "main", BIAS_MA, mode=mode))
    assert have[0] == want[0], f"{fn} is stale: python spice6as6/run_refs.py --write-netlist"
    assert close_rk(have[1], want[1]), (have[1], want[1])
    assert check(txt, "dual", "main", with_rk("dual", mode=mode), mode=mode) == []
    for m, md in R.SUBCKTS:
        assert open(R.model_file(m, md)).read() == R.subckt(m, mode=md), f"{R.model_file(m, md)} is stale"


def test_spiceinit():
    assert R.spiceinit() == f"set ngbehavior={R12.NGBEHAVIOR}"


@pytest.mark.parametrize("model, mode, bias", CASES)
def test_committed_references_were_made_with_these_settings(model, mode, bias):
    ref = json.load(open(R.ref_file(model, bias, mode)))
    gen = ref["generator"]
    assert gen["solver"] == R12.SOLVER and gen["sine"] == R12.SINE and gen["burst"] == R12.BURST
    assert gen["ac"] == dict(R12.AC_GRID, edges_hz=list(R12.AC_EDGES_HZ))
    assert gen["spiceinit"] == R.spiceinit()
    assert gen["params"] == R.PARAMS[model]
    assert gen["bias_ma"] == bias and gen["rk"] == pytest.approx(RK[model, mode, bias], rel=1e-9)
    assert gen.get("mode", "P1") == ref.get("mode", "P1") == mode
    if R.HARDNESS[model]:   # #112: the grid law the reference ran, and the split runs' pieces
        assert gen["grid"] == pentode.grid(R.HARDNESS[model], R.PARAMS[model])
        assert gen["split"] == R.split_analyses() + ["burst"] and "ig1" in ref["op"]
    else:
        assert "grid" not in gen and "ig1" not in ref["op"]


VALUE_KEYS = [k for k in VALUES if k not in {"CG3"} | {c[0] for c in circuit.VALVE_CAPS}] + ["Rk"]


@pytest.mark.parametrize("key", VALUE_KEYS)
def test_check_catches_changed_value(key):
    mode = "T" if key == "Rstrap" else "P1"             # Rstrap is only in T's netlist
    v = with_rk("dual", mode=mode)
    bad = dict(v, **{key: v[key] * 1.01})
    txt = R.netlist("dual", "main", BIAS_MA, values=bad, rk=bad["Rk"], mode=mode)
    assert any(key.upper() in e.upper() or "VHT" in e or "VG2" in e for e in check(txt, "dual", "main", v, mode=mode))


@pytest.mark.parametrize("mode, old, new", [
    ("P1", "Rk  k 0 9872.89", "Rk  k 0 9972.89"), ("P1", "VG2 g2 0 DC 120", "VG2 g2 0 DC 150"),
    ("P1", "XV1 a g2 k g k", "XV1 a g2 0 g k"), ("P1", "XV1 a g2 k g k", "XV1 a k g2 g k"),
    ("P1", "reltol=1e-06", "reltol=1e-04"), ("P1", "Rl  out 0 1Meg\n", ""), ("P1", "Rk  k 0", "Ck  k 0 22u\nRk  k 0"),
    ("T", "Rstrap g2 a 100\n", ""), ("T", "Rstrap g2 a 100", "Rstrap g2 a 1k"),
    ("T", "XV1 a g2 g2 g k", "XV1 a g2 k g k"),
    ("T", "VHT ht 0 DC 200\n", "VHT ht 0 DC 200\nVG2 g2 0 DC 120\n"), ("T", "_CG3K", ""),
    ("P2", "XV1 a g2 0 g k", "XV1 a g2 k g k"), ("P2", "VG2 g2 0 DC 120\n", ""), ("P2", "dual_cg3k.inc", "dual.inc")])
def test_check_catches_hand_edit(mode, old, new):
    txt = R.netlist("dual", "main", BIAS_MA, mode=mode)
    assert old in txt
    assert check(txt.replace(old, new), "dual", "main", with_rk("dual", mode=mode), mode=mode)


# --- the valve subckts: the GE capacitances, and the DK stage's valve law ---------------------------------

@pytest.mark.parametrize("model, mode", R.SUBCKTS + [("dual", "P2")])
def test_subckt_caps_are_circuit_values(model, mode):
    """The subckt's capacitors are circuit.valve_cap_pins(mode) (pins and values; CG3 g3 -> g2 in P1, g3 -> k in T
    and P2), and circuit.netlist() has the same set mapped to the stage's nodes."""
    pins, _, caps = subckt(_subckt_text(model, mode), R.models(model, mode)[1])
    assert pins == ["P", "G2", "G3", "G1", "K"]
    have = {(a, b): c for a, b, c in caps}
    cap_pins = circuit.valve_cap_pins(mode)
    assert len(have) == len(caps) == len(cap_pins) == 5
    assert ("CG3", "G3", "G2" if mode == "P1" else "K") in cap_pins
    for k, a, b in cap_pins:
        assert close(have[a, b], VALUES[k]), k
    pin_nodes = circuit.MODES[mode]["pins"]
    dk = circuit.netlist(RK["dual", mode, BIAS_MA], mode=mode)[1][-len(cap_pins):]
    def unordered(caps):            # (sorted node pair, C); P1 has two caps g1-k (CG1K, CG1G3), so a list
        return sorted((tuple(sorted(n or "0" for n in (p, q))), c) for p, q, c in caps)
    assert unordered(dk) == unordered((pin_nodes[a], pin_nodes[b], VALUES[k]) for k, a, b in cap_pins)


def test_no_capacitor_shorted_by_a_mode():
    """No valve capacitance has both ends on one node in any mode (CG3 is placed per mode for this, circuit.py)."""
    for mode in circuit.MODES:
        assert all(p != q for p, q, _ in circuit.valve_caps(mode=mode)), mode


def _subckt_text(model, mode="P1"):
    return open(R.model_file(model, mode)).read()


def _bsources(model, mode="P1"):
    """{name: (pin+, pin-, python expression of the subckt pins' voltages)} of the subckt's B-sources."""
    out = {}
    for ln in lines(_subckt_text(model, mode), title=False):
        if ln[0].upper() == "B":
            name, a, b, rest = ln.split(None, 3)
            assert rest.startswith("I=")
            e = rest[2:]
            for pin in ("G1", "G2", "G3", "P"):
                e = e.replace(f"V({pin},K)", f"v{pin.lower()}")
            e = re.sub(r"(?<![\w.])(\d+(?:\.\d*)?(?:e[+-]?\d+)?)m(?![\w])", r"(\1e-3)", e)   # SPICE '1m'
            out[name.upper()] = (a, b, e.replace("ln(", "log(").replace("^", "**"))
    return out


def _eval(expr, vg1, vg2, vg3, va):
    env = dict(log=math.log, exp=math.exp, atan=math.atan, max=max, abs=abs,
               pwr=lambda x, y: math.copysign(abs(x) ** y, x), vg1=vg1, vg2=vg2, vg3=vg3, vp=va)
    return eval(expr, {"__builtins__": {}}, env)


@pytest.mark.parametrize("model, mode", [c for c in R.SUBCKTS if not R.HARDNESS[c[0]]])
def test_subckt_is_the_dk_valve_law(model, mode):
    """The B-sources, evaluated in Python, give pentode.currents (= model6as6.dual_ip_ig2) with the same parameters,
    over the stage's range and beyond (cut-off to positive grid; Va from the knee to HT). Agreement is 1e-12 relative
    except in deep cut-off: the SPICE form ln(1+exp(x)) (emit_spice.py; no log1p in ngspice) loses ~1e-16/exp(x)
    relative there, e.g. 4e-10 at Vg1k = -8 V, Vg2k = 60 V (Ik ~ 1e-13 A), which this test allows for."""
    b = _bsources(model, mode)
    assert set(b) == {"BP", "BG2"} and b["BP"][:2] == ("P", "K") and b["BG2"][:2] == ("G2", "K")
    p = R.PARAMS[model]
    for v in itertools.product([-8.0, -3.3, 0.0, 3.0], [60.0, 116.7, 140.0], [-8.0, -3.3, 0.0, 3.0],
                               [0.3, 20.0, 181.7, 300.0]):
        ip, ig2, _ = pentode.currents(*v, p)
        x = p["KP"] * (1.0 / p["MU"] + v[0] / v[1])          # the softplus argument of Koren's E1
        rel = 1e-12 + (2e-16 / math.exp(x) if x < 0 else 0.0)
        assert _eval(b["BP"][2], *v) == pytest.approx(ip, rel=rel, abs=1e-25), v
        assert _eval(b["BG2"][2], *v) == pytest.approx(ig2, rel=10 * rel, abs=1e-25), v


@pytest.mark.parametrize("mode", ["P1", "T"])
def test_grid_subckt_is_the_dk_valve_law_with_grid_current(mode):
    """#112: the dualg subckt's three B-sources, evaluated in Python, give pentode.currents_grid (Ip, Ig2, Ig1) at the
    DK stage's default hardness. Ip and Ig2 are (Ik - Ig1) x share, so as in test_pentode their error is relative to
    the sum of the magnitudes; Ig1 relative to itself, except where SPICE's ln(1 + exp(-|x|)) (no log1p) loses
    ~1e-16 / exp(x) relative (deep cut-off, where Ig1 < 1e-22 A; abs 1e-25 A, as test_subckt_is_the_dk_valve_law)."""
    b = _bsources("dualg", mode)
    assert set(b) == {"BP", "BG2", "BG1"} and b["BG1"][:2] == ("G1", "K")
    g = pentode.grid(R.HARDNESS["dualg"])
    assert g == pentode.GRID
    for v in itertools.product([-8.0, -3.3, -1.0, -0.3, 0.0, 0.3, 3.0], [60.0, 116.7, 140.0], [-8.0, -3.3, 0.0, 3.0],
                               [0.3, 20.0, 181.7, 300.0]):
        ip, ig2, ig1, _ = pentode.currents_grid(*v, pentode.DUAL_CONTROL, g)
        ip0, ig20, _ = pentode.currents(*v)
        f = ip0 / (ip0 + ig20)
        x = pentode.DUAL_CONTROL["KP"] * (1.0 / pentode.DUAL_CONTROL["MU"] + v[0] / v[1])
        rel = 1e-12 + (2e-16 / math.exp(x) if x < 0 else 0.0)
        xg = g["CG"] * v[0]
        relg = 1e-12 + (2e-16 / math.exp(xg) if xg < 0 else 0.0)
        assert abs(_eval(b["BG1"][2], *v) - ig1) <= relg * ig1 + 1e-25, v
        assert abs(_eval(b["BP"][2], *v) - ip) <= rel * (abs(ip0) + f * ig1) + 1e-25, v
        assert abs(_eval(b["BG2"][2], *v) - ig2) <= 10 * rel * (abs(ig20) + (1 - f) * ig1) + 1e-25, v


def test_grid_subckts_leave_the_111_ones_alone():
    """#112 changes the P1 / T subckts only as new files: the dual ones (Ig1 = 0) have no Bg1 and are generated exactly
    as before (test_committed_files_are_generated_and_current), so every dual reference stays reproducible."""
    for mode in ("P1", "T"):
        assert set(_bsources("dual", mode)) == {"BP", "BG2"}
        assert R.models("dualg", mode)[0] != R.models("dual", mode)[0]
    assert R.HARDNESS == {"dual": 0.0, "r51": 0.0, "dualg": pentode.HARDNESS}


@pytest.mark.parametrize("model, mode", [("dual", "P1"), ("dualg", "T")])
def test_split_main_is_the_same_analyses(model, mode):
    """#112: run(split=True) runs 'main' as split_analyses(). Every piece is the main netlist above .control (the
    sine pieces with their amplitude in Vin's SIN instead of the first one, which 'main' then alters per amplitude);
    'opac' is main's .op / .ac; each sine piece is one of main's tran + wrdata, written to the same file."""
    def unnamed(txt):       # the header names the analysis; everything else must match
        return re.sub(r"^(\* GENERATED by spice6as6/run_refs.py \([^,]+(?:, \w+)?), [\w:.]+,", r"\1, <analysis>,", txt)
    main = R.netlist(model, "main", BIAS_MA, mode=mode)
    head, ctl = unnamed(main).split(".control\n")
    pieces = {a: unnamed(R.netlist(model, a, BIAS_MA, mode=mode)) for a in R.split_analyses()}
    assert R.split_analyses() == ["opac"] + [f"sine:{a}" for a in R12.AMPS]
    assert pieces["opac"].split(".control\n")[0] == head
    assert pieces["opac"].split(".control\n")[1].split("\n.endc")[0] == ctl.split("foreach")[0].rstrip("\n")
    for a in R12.AMPS:
        h, c = pieces[f"sine:{a}"].split(".control\n")
        assert h == head.replace(f"SIN(0 {R12.AMPS[0]} 1000)", f"SIN(0 {a} 1000)")
        tran = next(ln for ln in ctl.splitlines() if ln.strip().startswith("tran"))
        assert c.splitlines()[:2] == [tran.strip(),
                                      f"wrdata out/{R.case(model, BIAS_MA, mode)}_sine_{a}.txt v(out) v(g) v(k)"]
    with pytest.raises(ValueError):
        R.netlist(model, "sine:3", BIAS_MA, mode=mode)


def test_failed_analysis_stops_the_run(tmp_path, monkeypatch):
    """run() runs the analyses one at a time: when one fails it raises with no ngspice left running, its generated
    netlist removed and no later analysis started (PR #120 review: the concurrent version raised on a failed main
    while the ~2 h burst kept writing to the paths a retry reuses). A stand-in executable plays ngspice: "main"
    fails, anything else would sleep."""
    calls = tmp_path / "calls.txt"
    fake = tmp_path / "ngspice"
    fake.write_text('#!/bin/sh\necho "$2" >> "$CALLS"\ncase "$2" in *_main.cir) exit 1;; esac\nsleep 30\n')
    fake.chmod(0o755)
    monkeypatch.setattr(R, "NGSPICE", str(fake))
    monkeypatch.setenv("CALLS", str(calls))
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError, match="ngspice failed on main"):
        R.run("dual", BIAS_MA, out=out, write=False)
    started = calls.read_text().split()
    assert len(started) == 1 and started[0].endswith("_main.cir"), started
    assert not [f for f in os.listdir(R.HERE) if f.startswith("_gen_") and "_out_" in f and tmp_path.name in f]
    assert os.path.exists(os.path.join(out, f"{R.case('dual', BIAS_MA)}_main_log.txt"))


def test_ngspice_has_a_wall_clock_limit(tmp_path, monkeypatch):
    """An ngspice that never returns is killed at NGSPICE_TIMEOUT_S and run() raises TimeoutError, with nothing left
    running and its generated netlist removed (#111: two ngspice children hung for ~2 h after writing their output)."""
    pidfile = tmp_path / "pid"
    fake = tmp_path / "ngspice"
    fake.write_text('#!/bin/sh\necho $$ > "$PIDFILE"\nexec sleep 600\n')
    fake.chmod(0o755)
    monkeypatch.setattr(R, "NGSPICE", str(fake))
    monkeypatch.setattr(R, "NGSPICE_TIMEOUT_S", 1.0)
    monkeypatch.setenv("PIDFILE", str(pidfile))
    t0 = time.monotonic()
    with pytest.raises(TimeoutError, match="ran past 1 s: killed"):
        R.run("dual", BIAS_MA, out=str(tmp_path / "out"), write=False)
    assert time.monotonic() - t0 < 30
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not [f for f in os.listdir(R.HERE) if f.startswith("_gen_") and tmp_path.name in f]
