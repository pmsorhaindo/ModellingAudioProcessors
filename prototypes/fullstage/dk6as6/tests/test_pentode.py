"""The DK stage's 6AS6 law (dk6as6/pentode.py) is fit6as6's dual_control (model6as6.dual_ip_ig2), with a correct
Jacobian, for both the #55 parameters (the stage's default) and the #51 ones (the comparison).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import itertools
import json
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6 import pentode as P

SETS = {"dual_control": P.DUAL_CONTROL, "refit_51": P.REFIT_51}
# Vg1k, Vg2k, Vg3k, Vak: cut-off to positive grid, below / at / above the 120 V screen, g3 cut-off to positive,
# plate from the knee to above HT (the #102 stage stays inside at 0.1-0.6 mA: Vg1k ~ -4.3 to -5.3 V, Vg2k ~ 115 V,
# Vg3k = 0 with g3 on the cathode, Vak ~ 100-180 V at rest and swinging over the knee and up to HT when driven)
GRID = list(itertools.product([-8.0, -3.3, -1.0, 0.0, 2.0, 8.0], [0.5, 60.0, 116.7, 140.0],
                              [-12.0, -6.0, -3.3, 0.0, 4.0], [0.3, 2.0, 20.0, 120.0, 181.7, 300.0]))


def test_parameters_are_the_fit_results():
    fr = json.load(open(P.FIT_RESULTS))
    assert P.DUAL_CONTROL == fr["dual_control"]["params"] and P.REFIT_51 == fr["refit_51"]["params"]
    assert round(P.DUAL_CONTROL["KR"], 4) == 0.2737 and round(P.DUAL_CONTROL["XA"], 4) == 0.5743   # issue #55
    assert "KR" not in P.REFIT_51 and "XA" not in P.REFIT_51                                       # issue #51


@pytest.mark.parametrize("name", SETS)
def test_currents_match_model6as6(name):
    p = SETS[name]
    for v in GRID:
        ip, ig2, _ = P.currents(*v, p)
        rp, rg2 = P.reference(*v, p)
        for a, b in ((ip, rp), (ig2, rg2)):
            assert abs(a - b) <= 1e-12 * abs(b) + 1e-30, (v, a, b)


@pytest.mark.parametrize("name", SETS)
def test_jacobian_matches_central_differences(name):
    p = SETS[name]
    for v in GRID:
        _, _, J = P.currents(*v, p)
        scale = max(abs(x) for row in J for x in row)
        for j in range(4):
            h = 1e-5 * max(1.0, abs(v[j]))
            vp, vm = list(v), list(v)
            vp[j] += h
            vm[j] -= h
            fp, fm = P.reference(*vp, p), P.reference(*vm, p)
            for k in range(2):
                d = (fp[k] - fm[k]) / (2 * h)
                # 1e-5: central-difference truncation where the law is steep (Vg2k = 0.5 V, deep cut-off)
                assert abs(J[k][j] - d) <= 1e-5 * (scale + abs(d)), (name, v, k, j, J[k][j], d)


def test_no_grid_current_and_cathode_current_is_the_sum():
    """Ig1 = Ig3 = 0 (no sheet gives one): the valve has only the plate and screen current ports."""
    from dk6as6 import circuit
    assert circuit.NLI == [("a", "k"), ("g2", "k")]
    for v in GRID[::7]:
        ip, ig2, _ = P.currents(*v)
        assert P.model6as6.dual_cathode(*v, P.DUAL_CONTROL) == pytest.approx(ip + ig2, rel=1e-12, abs=1e-30)
