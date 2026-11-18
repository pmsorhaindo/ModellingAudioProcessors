"""Generated 6AS6 ngspice netlists and valve subckts vs dk6as6/circuit.VALUES, spice/run_refs.SOLVER and the fit (#26).
No ngspice needed. Uses the 12AX7 netlist test's small SPICE parser (spice/tests/test_netlists.py), not the generator.
Checks every component value and the topology against circuit.netlist(), the screen / suppressor sources, the .options
and tran steps, the committed GENERATED files, and that the subckts' B-source expressions, evaluated here, ARE the DK
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
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

SPICE = os.path.join(ROOT, "spice6as6")
VALUES = circuit.VALUES
PIN_NODES = dict(zip(("P", "G2", "G3", "G1", "K"), R.PINS))   # subckt pin -> stage node


def check(text, model, analysis, values=VALUES, solver=R12.SOLVER):
    """All divergences of netlist `text` from `values` / `solver` (empty list = consistent)."""
    err = []
    p = parse(text)
    el = p["elements"]
    inc, sub = R.MODELS[model]
    if p["includes"] != [inc.split(None, 1)[1]]:
        err.append(f"includes {p['includes']} != {inc}")
    if el.get("XV1") != list(R.PINS) + [sub]:
        err.append(f"XV1 {el.get('XV1')} != {list(R.PINS) + [sub]}")
    for name, n, key in (("VHT", "ht", "HT"), ("VG2", "g2", "EC2"), ("VG3", "g3", "EC3")):
        t = el.get(name, [])
        if (t[:3] != [n, "0", "DC"] or len(t) != 4
                or not (num(t[3]) == values[key] == 0 or close(num(t[3]), values[key]))):
            err.append(f"{name} {t} != {n} 0 DC {values[key]}")
    passives = {k: v for k, v in el.items() if k[0] in "RC"}
    for name, tok in passives.items():
        key = next((k for k in VALUES if k.upper() == name), None)
        if key is None:
            err.append(f"element {name} is not a circuit.VALUES key")
        elif len(tok) != 3 or not close(num(tok[2]), values[key]):
            err.append(f"{name} = {tok[2:]} != VALUES[{key!r}] = {values[key]:g}")
    missing = {k.upper() for k in circuit.PASSIVES} - set(passives) - {"HT"}
    if missing:
        err.append(f"VALUES keys missing from the netlist: {sorted(missing)}")
    R_, Cs, U, _, _, _ = circuit.netlist(v=values)
    valve_caps = {(values[k], tuple(sorted((PIN_NODES[a], PIN_NODES[b])))) for k, a, b in R.CAP_PINS}
    want = edges((kind, p_, q, val) for kind, es in (("R", R_), ("C", Cs)) for p_, q, val in es
                 if not (kind == "C" and (val, tuple(sorted((p_, q or "0")))) in valve_caps))
    have = edges((name[0], node(t[0]), node(t[1]), num(t[2])) for name, t in passives.items() if len(t) == 3)
    for e in sorted(set(want) | set(have)):
        if e not in have or e not in want or not close(have[e], want[e]):
            err.append(f"{e[0]} between {e[1]}: netlist {have.get(e)} != circuit.netlist() {want.get(e)}")
    if U != ["src", "ht", "g2", "g3"]:
        err.append(f"circuit.netlist() sources {U} changed; update this test")
    vin = "VIN" if analysis == "main" else "BIN"
    if el.get(vin, [])[:2] != ["src", "0"]:
        err.append(f"input source {vin} for {analysis}: {el.get(vin)}")
    extra = set(el) - set(passives) - {"VHT", "VG2", "VG3", "XV1", vin}
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
@pytest.mark.parametrize("model", list(R.MODELS))
def test_generated_netlist_matches_values_and_solver(model, analysis):
    assert check(R.netlist(model, analysis), model, analysis) == []


def test_committed_files_are_generated_and_current():
    txt = open(R.NETLIST_FILE).read()
    assert txt.startswith("* GENERATED by spice6as6/run_refs.py")
    assert txt == R.netlist("dual", "main"), (
        "spice6as6/stage_6as6.cir is stale: python spice6as6/run_refs.py --write-netlist")
    assert check(txt, "dual", "main") == []
    for m in R.MODELS:
        assert open(R.model_file(m)).read() == R.subckt(m), f"{R.model_file(m)} is stale"


def test_spiceinit():
    assert R.spiceinit() == f"set ngbehavior={R12.NGBEHAVIOR}"


@pytest.mark.parametrize("model", list(R.MODELS))
def test_committed_references_were_made_with_these_settings(model):
    gen = json.load(open(os.path.join(SPICE, f"reference_{model}.json")))["generator"]
    assert gen["solver"] == R12.SOLVER and gen["sine"] == R12.SINE and gen["burst"] == R12.BURST
    assert gen["ac"] == dict(R12.AC_GRID, edges_hz=list(R12.AC_EDGES_HZ))
    assert gen["spiceinit"] == R.spiceinit()
    assert gen["params"] == R.PARAMS[model]


@pytest.mark.parametrize("key", [k for k in VALUES if k not in circuit.VALVE_CAPS and k != "EC3"])
def test_check_catches_changed_value(key):
    bad = R.netlist("dual", "main", values=dict(VALUES, **{key: VALUES[key] * 1.01}))
    assert any(key in e or "VHT" in e or "VG2" in e for e in check(bad, "dual", "main"))


@pytest.mark.parametrize("old, new", [("Rk  k 0 1.5k", "Rk  k 0 1.6k"), ("VG2 g2 0 DC 120", "VG2 g2 0 DC 150"),
                                      ("VG3 g3 0 DC 0", "VG3 g3 0 DC -1"), ("XV1 a g2 g3 g k", "XV1 a g3 g2 g k"),
                                      ("reltol=1e-06", "reltol=1e-04"), ("Rl  out 0 1Meg\n", "")])
def test_check_catches_hand_edit(old, new):
    txt = R.netlist("dual", "main")
    assert old in txt
    assert check(txt.replace(old, new), "dual", "main")


# --- the valve subckts: the GE capacitances, and the DK stage's valve law ---------------------------------

def _subckt_text(model):
    return open(R.model_file(model)).read()


@pytest.mark.parametrize("model", list(R.MODELS))
def test_subckt_caps_are_circuit_values(model):
    pins, _, caps = subckt(_subckt_text(model), R.SUBCKT[model])
    assert pins == ["P", "G2", "G3", "G1", "K"]
    have = edges(("C", PIN_NODES[a], PIN_NODES[b], c) for a, b, c in caps)
    want = edges(("C", p, q, c) for p, q, c in circuit.netlist()[1]
                 if any(c == VALUES[k] and {p, q} == {PIN_NODES[a], PIN_NODES[b]} for k, a, b in R.CAP_PINS))
    assert len(want) == len(circuit.VALVE_CAPS) and set(have) == set(want)
    for e in want:
        assert close(have[e], want[e]), e


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
