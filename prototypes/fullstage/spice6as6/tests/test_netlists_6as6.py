"""Generated 6AS6 ngspice netlists and valve subckts vs dk6as6/circuit.VALUES, spice/run_refs.SOLVER and the fit
(#26, #102). No ngspice needed. Uses the 12AX7 netlist test's small SPICE parser (spice/tests/test_netlists.py), not
the generator. Checks every component value (Rk: the one the DK stage solves for the reference's bias) and the topology
against circuit.netlist(), the screen source and the P1 wiring (g3 on the cathode), the .options and tran steps, the
committed GENERATED files and references, and that the subckts' B-source expressions, evaluated here, ARE the DK
stage's valve law (dk6as6/pentode.py = fit6as6 model6as6.dual_ip_ig2) with the same parameters.
Run from prototypes/fullstage:  python -m pytest -q spice6as6/tests
"""
import itertools
import json
import math
import os
import re
import sys
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from spice.tests.test_netlists import parse, num, close, edges, node, subckt, lines  # noqa: E402 -- after the sys.path setup
from spice import run_refs as R12  # noqa: E402 -- after the sys.path setup
from dk6as6 import circuit, pentode  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import cathode_resistor, BIAS_MA  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

VALUES = circuit.VALUES
PIN_NODES = circuit.PINS                                      # subckt pin -> stage node
CASES = R.cases(R.PARAMS)                                     # every committed reference: (model, bias mA)
RK = {(m, b): cathode_resistor(b, R.PARAMS[m]) for m, b in CASES}


def with_rk(model, bias=BIAS_MA, values=VALUES):
    return dict(values, Rk=RK[model, bias])


def check(text, model, analysis, values, solver=R12.SOLVER):
    """All divergences of netlist `text` from `values` (circuit.VALUES plus Rk) / `solver` (empty list = consistent)."""
    err = []
    p = parse(text)
    el = p["elements"]
    inc, sub = R.MODELS[model]
    if p["includes"] != [inc.split(None, 1)[1]]:
        err.append(f"includes {p['includes']} != {inc}")
    if el.get("XV1") != list(R.PINS) + [sub]:
        err.append(f"XV1 {el.get('XV1')} != {list(R.PINS) + [sub]}")
    for name, n, key in (("VHT", "ht", "HT"), ("VG2", "g2", "EC2")):
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
    missing = {k.upper() for k in circuit.PASSIVES + ("Rk",)} - set(passives) - {"HT"}
    if missing:
        err.append(f"VALUES keys missing from the netlist: {sorted(missing)}")
    R_, Cs, U, _, _, _ = circuit.netlist(values["Rk"], v=values)
    n_valve = len(circuit.valve_caps(values))                   # netlist() lists the valve's caps last
    want = edges((kind, p_, q, val) for kind, es in (("R", R_), ("C", Cs[:len(Cs) - n_valve])) for p_, q, val in es)
    have = edges((name[0], node(t[0]), node(t[1]), num(t[2])) for name, t in passives.items() if len(t) == 3)
    for e in sorted(set(want) | set(have)):
        if e not in have or e not in want or not close(have[e], want[e]):
            err.append(f"{e[0]} between {e[1]}: netlist {have.get(e)} != circuit.netlist() {want.get(e)}")
    if U != ["src", "ht", "g2"]:
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
@pytest.mark.parametrize("model, bias", CASES)
def test_generated_netlist_matches_values_and_solver(model, bias, analysis):
    assert check(R.netlist(model, analysis, bias), model, analysis, with_rk(model, bias)) == []


def test_p1_wiring_suppressor_on_cathode():
    """P1: g3 on the cathode in both descriptions; no g3 source; the Vg3k row of the DK incidence is zero."""
    assert R.PINS == ("a", "g2", "k", "g", "k") and circuit.PINS["G3"] == circuit.PINS["K"] == "k"
    assert "g3" not in circuit.NODES and "EC3" not in VALUES
    assert parse(R.netlist("dual", "main", BIAS_MA))["elements"]["XV1"][:5] == ["a", "g2", "k", "g", "k"]
    m = circuit.build(48000.0 * 8, RK["dual", BIAS_MA])
    assert not m["G"][2].any() and not m["H"][2].any() and not m["K"][2].any()


def test_committed_files_are_generated_and_current():
    txt = open(R.NETLIST_FILE).read()
    assert txt.startswith("* GENERATED by spice6as6/run_refs.py")
    assert txt == R.netlist("dual", "main", BIAS_MA), (
        "spice6as6/stage_6as6.cir is stale: python spice6as6/run_refs.py --write-netlist")
    assert check(txt, "dual", "main", with_rk("dual")) == []
    for m in R.MODELS:
        assert open(R.model_file(m)).read() == R.subckt(m), f"{R.model_file(m)} is stale"


def test_spiceinit():
    assert R.spiceinit() == f"set ngbehavior={R12.NGBEHAVIOR}"


@pytest.mark.parametrize("model, bias", CASES)
def test_committed_references_were_made_with_these_settings(model, bias):
    gen = json.load(open(R.ref_file(model, bias)))["generator"]
    assert gen["solver"] == R12.SOLVER and gen["sine"] == R12.SINE and gen["burst"] == R12.BURST
    assert gen["ac"] == dict(R12.AC_GRID, edges_hz=list(R12.AC_EDGES_HZ))
    assert gen["spiceinit"] == R.spiceinit()
    assert gen["params"] == R.PARAMS[model]
    assert gen["bias_ma"] == bias and gen["rk"] == pytest.approx(RK[model, bias], rel=1e-9)


@pytest.mark.parametrize("key", [k for k in VALUES if k not in {c[0] for c in circuit.VALVE_CAPS}] + ["Rk"])
def test_check_catches_changed_value(key):
    v = with_rk("dual")
    bad = dict(v, **{key: v[key] * 1.01})
    txt = R.netlist("dual", "main", BIAS_MA, values=bad, rk=bad["Rk"])
    assert any(key in e or "VHT" in e or "VG2" in e for e in check(txt, "dual", "main", v))


@pytest.mark.parametrize("old, new", [("Rk  k 0 9872.89", "Rk  k 0 9972.89"), ("VG2 g2 0 DC 120", "VG2 g2 0 DC 150"),
                                      ("XV1 a g2 k g k", "XV1 a g2 0 g k"), ("XV1 a g2 k g k", "XV1 a k g2 g k"),
                                      ("reltol=1e-06", "reltol=1e-04"), ("Rl  out 0 1Meg\n", ""),
                                      ("Rk  k 0", "Ck  k 0 22u\nRk  k 0")])
def test_check_catches_hand_edit(old, new):
    txt = R.netlist("dual", "main", BIAS_MA)
    assert old in txt
    assert check(txt.replace(old, new), "dual", "main", with_rk("dual"))


# --- the valve subckts: the GE capacitances, and the DK stage's valve law ---------------------------------

def _subckt_text(model):
    return open(R.model_file(model)).read()


@pytest.mark.parametrize("model", list(R.MODELS))
def test_subckt_caps_are_circuit_values(model):
    """The subckt's capacitors are circuit.VALVE_CAPS (pins and values), and circuit.netlist() has the same set mapped
    to the stage's nodes."""
    pins, _, caps = subckt(_subckt_text(model), R.SUBCKT[model])
    assert pins == ["P", "G2", "G3", "G1", "K"]
    have = {(a, b): c for a, b, c in caps}
    assert len(have) == len(caps) == len(circuit.VALVE_CAPS)
    for k, a, b in circuit.VALVE_CAPS:
        assert close(have[a, b], VALUES[k]), k
    dk = circuit.netlist(RK["dual", BIAS_MA])[1][-len(circuit.VALVE_CAPS):]
    assert dk == [(PIN_NODES[a], PIN_NODES[b], VALUES[k]) for k, a, b in circuit.VALVE_CAPS]


def _bsources(model):
    """{name: (pin+, pin-, python expression of the subckt pins' voltages)} of the subckt's B-sources."""
    out = {}
    for ln in lines(_subckt_text(model), title=False):
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
    env = dict(log=math.log, exp=math.exp, atan=math.atan, max=max,
               pwr=lambda x, y: math.copysign(abs(x) ** y, x), vg1=vg1, vg2=vg2, vg3=vg3, vp=va)
    return eval(expr, {"__builtins__": {}}, env)


@pytest.mark.parametrize("model", list(R.MODELS))
def test_subckt_is_the_dk_valve_law(model):
    """The B-sources, evaluated in Python, give pentode.currents (= model6as6.dual_ip_ig2) with the same parameters,
    over the stage's range and beyond (cut-off to positive grid; Va from the knee to HT). Agreement is 1e-12 relative
    except in deep cut-off: the SPICE form ln(1+exp(x)) (emit_spice.py; no log1p in ngspice) loses ~1e-16/exp(x)
    relative there, e.g. 4e-10 at Vg1k = -8 V, Vg2k = 60 V (Ik ~ 1e-13 A), which this test allows for."""
    b = _bsources(model)
    assert set(b) == {"BP", "BG2"} and b["BP"][:2] == ("P", "K") and b["BG2"][:2] == ("G2", "K")
    p = R.PARAMS[model]
    for v in itertools.product([-8.0, -3.3, 0.0, 3.0], [60.0, 116.7, 140.0], [-8.0, -3.3, 0.0, 3.0],
                               [0.3, 20.0, 181.7, 300.0]):
        ip, ig2, _ = pentode.currents(*v, p)
        x = p["KP"] * (1.0 / p["MU"] + v[0] / v[1])          # the softplus argument of Koren's E1
        rel = 1e-12 + (2e-16 / math.exp(x) if x < 0 else 0.0)
        assert _eval(b["BP"][2], *v) == pytest.approx(ip, rel=rel, abs=1e-25), v
        assert _eval(b["BG2"][2], *v) == pytest.approx(ig2, rel=10 * rel, abs=1e-25), v
