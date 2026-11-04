import json, math, os, re, sys
import numpy as np
import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import model6as6 as M
from fit import load, arr, FLOOR  # noqa: E402  (re-runs the fit; deterministic)

R = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
P = R["dual_control"]["params"]
SETS = {"plate_p3": ([r for r in load("plate_characteristics_p3.csv") if r["quantity"] == "Ib"], M.dual_ip),
        "screen_p3": ([r for r in load("plate_characteristics_p3.csv") if r["quantity"] == "Ic2"], M.dual_ig2),
        "plate_vs_g3_p4": (load("suppressor_characteristics_p4.csv"), M.dual_ip)}


@pytest.mark.parametrize("key", list(SETS))
def test_reproduces_digitised_points_within_reported_error(key):
    rows, fn = SETS[key]
    for curve, rep in R["dual_control"][key].items():
        rr = [r for r in rows if r["curve"] == curve]
        g1, g2, g3, va, i = arr(rr)
        e = np.abs(fn(g1, g2, g3, va, P) - i) * 1e3
        assert len(rr) == rep["n"] and len(rr) >= 25
        assert np.sqrt(np.mean(e**2)) <= rep["rms_mA"] * 1.001 + 1e-9
        assert e.max() <= rep["max_abs_mA"] * 1.001 + 1e-9
        assert rep["rms_rel"] < 0.07          # every curve under 7 % RMS relative error


def test_datasheet_typical_operation_crosscheck():
    """GE sheet p.1, class A1 columns (Eb=Ec2=120 V, Ec1=-2 V). NOT used in the fit."""
    for g3, ib, ic2, gm in ((-3, 3.6e-3, 4.8e-3, 1850e-6), (0, 5.2e-3, 3.5e-3, 3200e-6)):
        assert M.dual_ip(-2, 120, g3, 120, P) == pytest.approx(ib, rel=0.12)
        assert M.dual_ig2(-2, 120, g3, 120, P) == pytest.approx(ic2, rel=0.12)
        d = (M.dual_ip(-1.99, 120, g3, 120, P) - M.dual_ip(-2.01, 120, g3, 120, P)) / 0.02
        assert d == pytest.approx(gm, rel=0.12)


def _spice_eval(expr, v):
    s = expr.replace("1m", "1e-3")
    s = re.sub(r"V\((\w+),K\)", lambda m: f"v['{m.group(1)}']", s).replace("V(ik)", "v['ik']")
    env = dict(ln=np.log, exp=np.exp, atan=np.arctan, max=np.maximum, pwr=lambda x, y: np.abs(x) ** y, v=v)
    return eval(s, env)


def test_spice_subcircuit_matches_python_model():
    from emit_spice import EXPR
    rng = np.random.default_rng(0)
    for _ in range(200):
        v = dict(G1=rng.uniform(-4, 0), G2=120.0, G3=rng.uniform(-8, 10), P=rng.uniform(20, 240))
        v["ik"] = _spice_eval(EXPR["ik"], v)
        ip = v["ik"] * _spice_eval(EXPR["share"], v)
        assert ip == pytest.approx(M.dual_ip(v["G1"], 120, v["G3"], v["P"], P), rel=1e-4)
