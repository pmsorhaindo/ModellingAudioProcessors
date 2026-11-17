"""#16: currents() with the shared exp and divided powers against a gold copy of the pre-#16 formulas.
Run from prototypes/fullstage:  python -m pytest -q dk/tests/test_triode.py
"""
import math
import os
import sys
import numpy as np
HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)
from dk.triode import currents, currents_only, softplus, softplus_sigmoid, DZ_12AX7_RSD1  # noqa: E402 -- after the sys.path setup

# Dense grid over and beyond the stage's range (grid -20..+5 V, plate 0..450 V), plus the far cutoff where
# exp(-|z|) underflows (z < -745: softplus = sigmoid = 0) and the subnormal tail just before it.
VGK = np.concatenate([np.linspace(-20.0, 5.0, 501), [-1e-12, -0.0, 0.0, 1e-12, -60.0, -75.0, -219.0, -230.0, -300.0]])
VAK = np.concatenate([np.linspace(0.0, 450.0, 451), [1e-9, 1000.0]])
MAX_ULPS = 8        # sk**gam / sk vs sk**(gam - 1): worst measured on this grid 3 ulps (dIg/dVgk), 4 (dIa/dV*)


# ---- pre-#16 formulas, verbatim (gold) ----
def _softplus_old(x):
    return (x if x > 0.0 else 0.0) + math.log1p(math.exp(-abs(x)))


def _sigmoid_old(x):
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def currents_ref(vgk, vak, p=DZ_12AX7_RSD1):
    G, mu, gam, C = p["G"], p["mu"], p["gamma"], p["C"]
    Gg, xi, Cg, Ig0 = p["Gg"], p["xi"], p["Cg"], p["Ig0"]
    zk = C * (vak / mu + vgk)
    sk = _softplus_old(zk) / C
    ik = G * sk ** gam
    dik_dz = G * gam * sk ** (gam - 1.0) * _sigmoid_old(zk)
    zg = Cg * vgk
    sg = _softplus_old(zg) / Cg
    ig = Gg * sg ** xi + Ig0
    dig = Gg * xi * sg ** (xi - 1.0) * _sigmoid_old(zg)
    ia = ik - ig
    J = ((dig, 0.0),
         (dik_dz - dig, dik_dz / mu))
    return ig, ia, J


def currents_only_ref(vgk, vak, p=DZ_12AX7_RSD1):
    G, mu, gam, C = p["G"], p["mu"], p["gamma"], p["C"]
    Gg, xi, Cg, Ig0 = p["Gg"], p["xi"], p["Cg"], p["Ig0"]
    ik = G * (_softplus_old(C * (vak / mu + vgk)) / C) ** gam
    ig = Gg * (_softplus_old(Cg * vgk) / Cg) ** xi + Ig0
    return ig, ik - ig


def _flat(r):
    ig, ia, J = r
    return (ig, ia, J[0][0], J[0][1], J[1][0], J[1][1])


def _ulps(a, b):
    """Distance in ulps of the larger magnitude (0 if equal, including both 0)."""
    return 0.0 if a == b else abs(a - b) / math.ulp(max(abs(a), abs(b)))


def test_softplus_sigmoid_bit_identical():
    zs = [C * v for C in (DZ_12AX7_RSD1["C"], DZ_12AX7_RSD1["Cg"]) for v in VGK.tolist()] + [-0.0, 0.0, -800.0, 800.0]
    for z in zs:
        sp, sg = softplus_sigmoid(z)
        assert sp == _softplus_old(z) and sg == _sigmoid_old(z), z
        assert softplus(z) == _softplus_old(z), z


def test_currents_only_bit_identical():
    for g in VGK.tolist():
        for a in VAK.tolist():
            assert currents_only(g, a) == currents_only_ref(g, a), (g, a)


def test_currents_matches_gold():
    """Ig, Ia equal bit for bit (same pows); the Jacobian within MAX_ULPS, except where the gold's terms are
    at or near zero (entries below 1e-300 A/V, e.g. dIg/dVak = 0 and the deep cutoff, must agree to 1e-300 absolute)."""
    worst = [0.0] * 6
    for g in VGK.tolist():
        for a in VAK.tolist():
            new, ref = _flat(currents(g, a)), _flat(currents_ref(g, a))
            assert all(math.isfinite(x) for x in new), (g, a, new)
            assert new[:2] == ref[:2], (g, a)
            for k in range(2, 6):
                if max(abs(new[k]), abs(ref[k])) < 1e-300:
                    assert abs(new[k] - ref[k]) < 1e-300, (g, a, k)
                    continue
                u = _ulps(new[k], ref[k])
                worst[k] = max(worst[k], u)
                assert u <= MAX_ULPS, (g, a, k, new[k], ref[k], u)
    print("worst ulps (Ig, Ia, dIg/dVgk, dIg/dVak, dIa/dVgk, dIa/dVak):", worst)
