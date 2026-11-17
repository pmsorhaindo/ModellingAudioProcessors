"""Generated ngspice netlists vs dk/circuit.VALUES and run_refs.SOLVER (#4). No ngspice needed.

Parses the exact text run_refs.netlist() hands to ngspice (every model x analysis), the committed GENERATED
spice/stage_12ax7.cir and the valve subckts, with a small SPICE parser written here (not the generator's code),
and checks every component value, the topology (against circuit.netlist()), the .options, every tran step and
.spiceinit. Values are compared to 1e-12 relative (the generator writes 12 significant digits).
Run from prototypes/fullstage:  python -m pytest -q spice/tests
"""
import json
import math
import os
import re
import sys
import pytest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from dk import circuit  # noqa: E402 -- after the sys.path setup
from dk.triode import DZ_12AX7_RSD1  # noqa: E402 -- after the sys.path setup
from spice import run_refs as R  # noqa: E402 -- after the sys.path setup

SPICE = os.path.join(ROOT, "spice")
VALUES = circuit.VALUES
VALVE_CAPS = ("CCG", "CGP", "CCP")
VALVE_PINS = ("a", "g", "k")          # XV1 a g k: plate grid cathode, the pin order of both subckts
SUFFIX = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
NUM = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(meg|[tgkmunpf])?[a-z]*$", re.I)


def num(tok):
    """A SPICE number: '22n', '1Meg', '.9P', '1e-06', '300'."""
    m = NUM.match(tok)
    if not m:
        raise ValueError(f"not a SPICE number: {tok!r}")
    return float(m.group(1)) * SUFFIX.get((m.group(2) or "").lower(), 1.0)


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=0.0)


def lines(text, title=True):
    """Logical lines: title dropped, '*' comments and ';' tails removed, '+' continuations joined."""
    out = []
    for ln in text.splitlines()[1 if title else 0:]:
        ln = ln.split(";")[0].rstrip()
        if not ln.strip() or ln.lstrip().startswith("*"):
            continue
        if ln.startswith("+") and out:
            out[-1] += " " + ln[1:].strip()
        else:
            out.append(ln.strip())
    return out


def parse(text, title=True):
    """-> dict(elements={NAME: tokens}, options={}, includes=[], control=[lines])."""
    p = dict(elements={}, options={}, includes=[], control=[])
    in_control = False
    for ln in lines(text, title):
        low = ln.lower()
        if low == ".control":
            in_control = True
        elif low == ".endc":
            in_control = False
        elif in_control:
            p["control"].append(ln)
        elif low.startswith(".options"):
            for kv in ln.split()[1:]:
                k, v = kv.split("=")
                p["options"][k.lower()] = v
        elif low.startswith(".include"):
            p["includes"].append(ln.split(None, 1)[1].strip())
        elif low == ".end":
            break
        elif not ln.startswith("."):
            tok = ln.split()
            assert tok[0].upper() not in p["elements"], f"duplicate element {tok[0]}"
            p["elements"][tok[0].upper()] = tok[1:]
    return p


def node(n):
    return None if n == "0" else n


def edges(items):
    """{(kind, (node, node)): value} from (kind, p, q, value); ground is '0', node order ignored."""
    d = {}
    for kind, p, q, val in items:
        k = (kind, tuple(sorted(n or "0" for n in (p, q))))
        assert k not in d, f"two {kind}s between {k[1]}"
        d[k] = val
    return d


def check(text, model, analysis, values=VALUES, solver=R.SOLVER):
    """All divergences of netlist `text` from `values` / `solver` (empty list = consistent)."""
    err = []
    p = parse(text)
    el = p["elements"]
    inc, sub = R.MODELS[model]
    # include and valve
    if p["includes"] != [inc.split(None, 1)[1]]:
        err.append(f"includes {p['includes']} != {inc}")
    if el.get("XV1") != list(VALVE_PINS) + [sub]:
        err.append(f"XV1 {el.get('XV1')} != {list(VALVE_PINS) + [sub]}")
    # HT supply
    vht = el.get("VHT", [])
    if vht[:3] != ["ht", "0", "DC"] or len(vht) != 4 or not close(num(vht[3]), values["HT"]):
        err.append(f"VHT {vht} != ht 0 DC {values['HT']}")
    # passives: every VALUES key by name, and the topology of circuit.netlist() (valve caps are in the subckt)
    passives = {k: v for k, v in el.items() if k[0] in "RC"}
    for name, tok in passives.items():
        key = next((k for k in VALUES if k.upper() == name), None)
        if key is None:
            err.append(f"element {name} is not a circuit.VALUES key")
        elif len(tok) != 3 or not close(num(tok[2]), values[key]):
            err.append(f"{name} = {tok[2:]} != VALUES[{key!r}] = {values[key]:g}")
    missing = {k.upper() for k in VALUES} - set(passives) - {"HT"} - {c.upper() for c in VALVE_CAPS}
    if missing:
        err.append(f"VALUES keys missing from the netlist: {sorted(missing)}")
    R_, Cs, U, _, _ = circuit.netlist(v=values)
    want = edges((kind, p_, q, val) for kind, es in (("R", R_), ("C", Cs)) for p_, q, val in es
                 if not (kind == "C" and {p_, q} <= set(VALVE_PINS)))
    have = edges((name[0], node(t[0]), node(t[1]), num(t[2])) for name, t in passives.items() if len(t) == 3)
    for e in sorted(set(want) | set(have)):
        if e not in have or e not in want or not close(have[e], want[e]):
            err.append(f"{e[0]} between {e[1]}: netlist {have.get(e)} != circuit.netlist() {want.get(e)}")
    if {"src", "ht"} != set(U):
        err.append(f"circuit.netlist() sources {U} changed; update this test")
    # input source
    vin = "VIN" if analysis == "main" else "BIN"
    if el.get(vin, [])[:2] != ["src", "0"]:
        err.append(f"input source {vin} for {analysis}: {el.get(vin)}")
    extra = set(el) - set(passives) - {"VHT", "XV1", vin}
    if extra:
        err.append(f"unexpected elements {sorted(extra)}")
    # solver
    o = p["options"]
    for k in ("reltol", "abstol", "vntol", "chgtol"):
        if k not in o or not close(num(o[k]), solver[k]):
            err.append(f".options {k}={o.get(k)} != SOLVER {solver[k]:g}")
    if o.get("method") != solver["method"] or set(o) != {"reltol", "abstol", "vntol", "chgtol", "method"}:
        err.append(f".options {o} != SOLVER {solver}")
    trans = [c.split()[1:] for c in p["control"] if c.split()[0].lower() == "tran"]
    want_tran = ([[R.SINE["t_stop"], R.SINE["t_save"]]] if analysis == "main" else [[R.BURST["t_stop"], 0.0]])
    if len(trans) != len(want_tran):
        err.append(f"{len(trans)} tran lines, expected {len(want_tran)}")
    for t, (stop, start) in zip(trans, want_tran):
        got = [num(x) for x in t]
        if len(got) != 4 or not all(close(a, b) or a == b == 0 for a, b in
                                    zip(got, [solver["tstep"], stop, start, solver["tmax"]])):
            err.append(f"tran {t} != step {solver['tstep']:g} stop {stop:g} start {start:g} max {solver['tmax']:g}")
    if analysis == "main":
        fe = [c.split()[2:] for c in p["control"] if c.lower().startswith("foreach")]
        if fe != [R.AMPS]:
            err.append(f"sine amplitudes {fe} != {R.AMPS}")
    return err


def subckt(text, name):
    """(pins, params, caps) of `.SUBCKT name` in a model file; caps = [(node, node, farad)] with params resolved."""
    body, pins, params = [], None, {}
    for ln in lines(text, title=False):
        tok = ln.split()
        if tok[0].upper() == ".SUBCKT" and tok[1].upper() == name.upper():
            pins = tok[2:]
            if "PARAMS:" in [t.upper() for t in pins]:
                i = [t.upper() for t in pins].index("PARAMS:")
                pins, extra = pins[:i], pins[i + 1:]
                params.update(kv.split("=") for kv in extra)
            continue
        if pins is not None:
            if tok[0].upper().startswith(".ENDS"):
                break
            if tok[0].lower() == ".param":
                params.update(kv.split("=") for kv in tok[1:])
            body.append(tok)
    assert pins is not None, f"no .SUBCKT {name}"
    params = {k.upper(): v for k, v in params.items()}
    def val(s):
        return num(params[s.strip("{}").upper()]) if s.startswith("{") else num(s)
    caps = [(t[1], t[2], val(t[3])) for t in body if t[0][0].upper() == "C"]
    return pins, params, caps


def check_valve_caps(pins, caps):
    """The subckt's capacitances, mapped through XV1's pins, must be circuit.netlist()'s valve caps."""
    m = dict(zip(pins, VALVE_PINS))
    have = edges(("C", m[p], m[q], c) for p, q, c in caps)
    want = edges(("C", p, q, c) for p, q, c in circuit.netlist()[1] if {p, q} <= set(VALVE_PINS))
    assert set(have) == set(want), (have, want)
    for e in want:
        assert close(have[e], want[e]), f"valve cap {e[1]}: {have[e]:g} != circuit.VALUES {want[e]:g}"


# --- the generated netlists -------------------------------------------------------------------------------

@pytest.mark.parametrize("analysis", R.ANALYSES)
@pytest.mark.parametrize("model", list(R.MODELS))
def test_generated_netlist_matches_values_and_solver(model, analysis):
    assert check(R.netlist(model, analysis), model, analysis) == []


def test_committed_netlist_is_generated_and_current():
    txt = open(R.NETLIST_FILE).read()
    assert txt.startswith("* GENERATED by spice/run_refs.py")
    assert txt == R.netlist("koren", "main"), "spice/stage_12ax7.cir is stale: python spice/run_refs.py --write-netlist"
    assert check(txt, "koren", "main") == []


def test_no_hand_written_netlists():
    """The only .cir in spice/ is the GENERATED one (the hand-written duplicates were removed in #4)."""
    cirs = sorted(f for f in os.listdir(SPICE) if f.endswith(".cir") and not f.startswith("_gen_"))
    assert cirs == [os.path.basename(R.NETLIST_FILE)]


def test_spiceinit():
    assert R.spiceinit() == "set ngbehavior=psa" == f"set ngbehavior={R.NGBEHAVIOR}"


@pytest.mark.parametrize("model", list(R.MODELS))
def test_committed_references_were_made_with_these_settings(model):
    gen = json.load(open(os.path.join(SPICE, f"reference_{model}.json")))["generator"]
    assert gen["solver"] == R.SOLVER and gen["sine"] == R.SINE and gen["burst"] == R.BURST
    assert gen["ac"] == dict(R.AC_GRID, edges_hz=list(R.AC_EDGES_HZ))
    assert gen["spiceinit"] == R.spiceinit()


# --- the check really fails on divergence ---------------------------------------------------------------

@pytest.mark.parametrize("key", [k for k in VALUES if k not in VALVE_CAPS])
def test_check_catches_changed_value(key):
    bad = R.netlist("dz", "main", values=dict(VALUES, **{key: VALUES[key] * 1.01}))
    assert any(key in e or "VHT" in e for e in check(bad, "dz", "main"))


@pytest.mark.parametrize("key, factor", [("reltol", 10), ("abstol", 10), ("vntol", 10), ("chgtol", 10),
                                         ("tstep", 2), ("tmax", 2)])
def test_check_catches_changed_solver(key, factor):
    bad = R.netlist("koren", "burst", solver=dict(R.SOLVER, **{key: R.SOLVER[key] * factor}))
    assert check(bad, "koren", "burst")


@pytest.mark.parametrize("old, new", [("Rk  k 0 1.5k", "Rk  k 0 1.6k"), ("Cin in1 gl 22n", "Cin in1 gl 47n"),
                                      ("Rgs gl g 68k", "Rgs gl a 68k"), ("reltol=1e-06", "reltol=1e-04"),
                                      ("tran 5e-07 0.5 0.3 5e-07", "tran 2u 60m 0 2u"),
                                      ("VHT ht 0 DC 300", "VHT ht 0 DC 250"), ("Rl  out 0 1Meg\n", "")])
def test_check_catches_hand_edit(old, new):
    txt = R.netlist("koren", "main")
    assert old in txt
    assert check(txt.replace(old, new), "koren", "main")


# --- the valve subckts carry circuit.VALUES' inter-electrode capacitances -------------------------------

def test_dz_subckt_caps_and_params():
    pins, params, caps = subckt(open(os.path.join(SPICE, "models_dz", "dz_12ax7.inc")).read(), "DZ12AX7")
    check_valve_caps(pins, caps)
    names = dict(G="G", MU="mu", GAM="gamma", C="C", GG="Gg", XI="xi", CG="Cg", IG0="Ig0")
    assert {k: num(params[k]) for k in names} == {k: DZ_12AX7_RSD1[v] for k, v in names.items()}


def test_koren_subckt_caps():
    fn = os.path.join(SPICE, "models", "12ax7_koren.lib")
    if not os.path.exists(fn):
        pytest.skip("Koren model not fetched (spice/models/fetch_models.sh; not redistributed)")
    pins, _, caps = subckt(open(fn).read(), "12AX7")
    check_valve_caps(pins, caps)


def test_valve_cap_check_catches_changed_value(monkeypatch):
    pins, _, caps = subckt(open(os.path.join(SPICE, "models_dz", "dz_12ax7.inc")).read(), "DZ12AX7")
    monkeypatch.setitem(VALUES, "CGP", 2.5e-12)
    with pytest.raises(AssertionError, match="valve cap"):
        check_valve_caps(pins, caps)
