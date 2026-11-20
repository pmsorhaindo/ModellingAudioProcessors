"""The DK stage's 6AS6 law (dk6as6/pentode.py) is fit6as6's dual_control (model6as6.dual_ip_ig2), with a correct
Jacobian, for both the #55 parameters (the stage's default) and the #51 ones (the comparison); and with the optional
g1 grid current (#112), Dempwolf & Zoelzer's eq. (11) taken out of Ik before the split, against its numpy reference.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import itertools
import json
import math
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


def test_without_grid_current_the_cathode_current_is_the_sum():
    """Without grid current (hardness 0) the valve has only the plate and screen current ports; Ig3 = 0 always."""
    from dk6as6 import circuit
    assert circuit.NLI == circuit.nli(False) == [("a", "k"), ("g2", "k")]
    assert circuit.nli(True) == [("a", "k"), ("g2", "k"), ("g", "k")]
    for v in GRID[::7]:
        ip, ig2, _ = P.currents(*v)
        assert P.model6as6.dual_cathode(*v, P.DUAL_CONTROL) == pytest.approx(ip + ig2, rel=1e-12, abs=1e-30)


# --- g1 grid current (#112) ---------------------------------------------------------------------------------
# Vg1k from deep cut-off through the DZ onset (-1 ... 0 V) to a positive grid, the rest as GRID
GRID_G1 = list(itertools.product([-8.0, -3.3, -1.0, -0.5, -0.16, 0.0, 0.2, 0.7, 2.0, 8.0], [0.5, 60.0, 116.7, 140.0],
                                 [-12.0, -3.3, 0.0, 4.0], [0.3, 2.0, 20.0, 120.0, 300.0]))


def test_grid_parameters_are_dz_rsd1_scaled_by_the_stated_perveance_ratio():
    """XI and CG are Dempwolf & Zoelzer's 12AX7 RSD-1 values (DAFx-11 Table 1, as dk/triode.py), Gg is scaled by the
    6AS6 fit's cathode coefficient 2 / KG1 over RSD-1's G (= 1.2808), times the hardness; Ig0 is not used."""
    from dk.triode import DZ_12AX7_RSD1 as dz
    assert (dz["G"], dz["Gg"], dz["xi"], dz["Cg"]) == (2.242e-3, 6.177e-4, 1.314, 9.901)
    assert P.PERVEANCE_RATIO == (2.0 / P.DUAL_CONTROL["KG1"]) / dz["G"] and round(P.PERVEANCE_RATIO, 4) == 1.2808
    assert P.HARDNESS == 1.0 and P.GRID == P.grid(1.0)
    for h in (0.0, 0.5, 1.0, 3.0):
        g = P.grid(h)
        assert g["GG"] == pytest.approx(h * P.PERVEANCE_RATIO * dz["Gg"], rel=1e-15)
        assert (g["XI"], g["CG"], g["hardness"]) == (dz["xi"], dz["Cg"], h)
    assert P.grid(1.0, P.REFIT_51)["GG"] == pytest.approx(2.0 / P.REFIT_51["KG1"] / dz["G"] * dz["Gg"], rel=1e-15)
    with pytest.raises(ValueError):
        P.grid(-0.1)


def test_grid_current_values():
    """Ig1 at the onset and above (the DZ RSD-1 shape x 1.2808): 24.0 uA at Vg1k = 0, 0.79 mA at +1 V, 87 pA at -1 V."""
    assert P.grid_current(0.0)[0] == pytest.approx(P.GRID["GG"] * (math.log(2.0) / 9.901) ** 1.314, rel=1e-14)
    assert P.grid_current(0.0)[0] == pytest.approx(24.03e-6, rel=1e-3)
    assert P.grid_current(1.0)[0] == pytest.approx(0.7912e-3, rel=1e-3)
    assert P.grid_current(-1.0)[0] == pytest.approx(87.05e-12, rel=1e-3)
    assert P.grid_current(-100.0) == (0.0, 0.0) and P.grid_current(0.3, P.grid(0.0)) == (0.0, 0.0)


@pytest.mark.parametrize("hardness", [0.5, 1.0, 3.0])
def test_currents_grid_match_the_reference(hardness):
    """currents_grid against reference_grid (model6as6.dual_ip_ig2 and DZ eq. (11) in numpy) to 1e-12. Ip = Ip0 - f Ig1
    and Ig2 = Ig20 - (1 - f) Ig1 are differences, so (as dk/tests/test_triode.py's Ia = Ik - Ig) their error is relative
    to the sum of the magnitudes, |Ip0| + f Ig1 and |Ig20| + (1 - f) Ig1; Ig1 is relative to itself."""
    g = P.grid(hardness)
    for v in GRID_G1:
        ip, ig2, ig1, _ = P.currents_grid(*v, P.DUAL_CONTROL, g)
        rp, rg2, rg1 = P.reference_grid(*v, P.DUAL_CONTROL, g)
        ip0, ig20, _ = P.currents(*v)
        f = ip0 / (ip0 + ig20) if ip0 + ig20 > 0.0 else 1.0
        for a, b, scale in ((ip, rp, abs(ip0) + f * ig1), (ig2, rg2, abs(ig20) + (1.0 - f) * ig1), (ig1, rg1, ig1)):
            assert abs(a - b) <= 1e-12 * scale + 1e-30, (v, a, b)


def test_currents_grid_conserve_the_cathode_current():
    """Ip + Ig2 + Ig1 is the fit's cathode current: the grid takes its current from Ik, it adds none (to 1e-12 of
    Ik + Ig1, the scale of the differences)."""
    for v in GRID_G1[::3]:
        ip, ig2, ig1, _ = P.currents_grid(*v)
        ik = P.model6as6.dual_cathode(*v, P.DUAL_CONTROL)
        assert abs(ip + ig2 + ig1 - ik) <= 1e-12 * (ik + ig1) + 1e-30, v


def test_currents_grid_off_is_the_111_law():
    """Hardness 0: exactly currents(), with Ig1 = 0 and a zero Jacobian row."""
    g0 = P.grid(0.0)
    for v in GRID_G1[::5]:
        ip, ig2, ig1, J = P.currents_grid(*v, P.DUAL_CONTROL, g0)
        a, b, J0 = P.currents(*v)
        assert (ip, ig2, ig1, J) == (a, b, 0.0, (J0[0], J0[1], (0.0, 0.0, 0.0, 0.0)))


@pytest.mark.parametrize("hardness", [1.0, 3.0])
def test_currents_grid_jacobian_matches_central_differences(hardness):
    g = P.grid(hardness)
    for v in GRID_G1:
        _, _, _, J = P.currents_grid(*v, P.DUAL_CONTROL, g)
        scale = max(abs(x) for row in J for x in row)
        for j in range(4):
            h = 1e-5 * max(1.0, abs(v[j]))
            vp, vm = list(v), list(v)
            vp[j] += h
            vm[j] -= h
            fp, fm = P.reference_grid(*vp, P.DUAL_CONTROL, g), P.reference_grid(*vm, P.DUAL_CONTROL, g)
            for k in range(3):
                d = (fp[k] - fm[k]) / (2 * h)
                assert abs(J[k][j] - d) <= 1e-5 * (scale + abs(d)), (v, k, j, J[k][j], d)
        assert J[2][1:] == (0.0, 0.0, 0.0)          # DZ: Ig1 depends on Vg1k only
