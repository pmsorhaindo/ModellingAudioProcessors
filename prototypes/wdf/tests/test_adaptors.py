"""WDF building blocks (wdf/adaptors.py): the R-type scattering against Werner et al.'s MNA formula, the classical
series / parallel adaptors, losslessness, and the K-method root's independence of the arbitrary root port resistances.
Run from prototypes/wdf:  python -m pytest -q tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdf.adaptors import (Resistor, ResistiveSource, Capacitor, Series, Parallel, voltage, incidence,  # noqa: E402
                          rtype_scattering)
from wdf.stage import WdfStage, RTYPE_NODES, ROOT_PORTS, EXT_PORTS  # noqa: E402 -- after the sys.path setup

RNG = np.random.default_rng(12)


def mna_scattering(ports, nodes, R):
    """Werner et al. DAFx-15 eq. (19)-(20) built element by element: each port n is a voltage source a_n (+ at an
    internal node m_n, - at the port's - node) in series with R_n (m_n to the port's + node); S = I + 2 [0 R] X^-1
    [0 I]^T, with X the MNA matrix (unknowns: every node voltage, then every source current)."""
    nn, npt = len(nodes), len(ports)
    idx = {n: i for i, n in enumerate(nodes)}
    X = np.zeros((nn + 2 * npt, nn + 2 * npt))
    for n, ((p, q), r) in enumerate(zip(ports, R)):
        m = nn + n
        g = 1.0 / r
        X[m, m] += g
        if p is not None:
            X[idx[p], idx[p]] += g
            X[m, idx[p]] -= g
            X[idx[p], m] -= g
        j = nn + npt + n                             # source current: from + (m) through the source to -
        X[m, j] += 1.0
        X[j, m] += 1.0
        if q is not None:
            X[idx[q], j] -= 1.0
            X[j, idx[q]] -= 1.0
    Xi = np.linalg.inv(X)
    sel_j = np.zeros((npt, nn + 2 * npt))
    sel_j[:, nn + npt:] = np.eye(npt)
    return np.eye(npt) + 2.0 * np.diag(R) @ sel_j @ Xi @ sel_j.T


def stage_ports(R=None):
    ports = ROOT_PORTS + EXT_PORTS
    R = RNG.uniform(10.0, 1e6, len(ports)) if R is None else R
    return ports, R


def test_rtype_scattering_is_werner_mna_formula():
    ports, R = stage_ports()
    S = rtype_scattering(incidence(ports, RTYPE_NODES), R)
    Sm = mna_scattering(ports, RTYPE_NODES, R)
    assert np.max(np.abs(S - Sm)) < 1e-12


def test_rtype_scattering_is_lossless():
    """A wiring adaptor stores and dissipates nothing: sum (a^2 - b^2) / R = 0, i.e. S^T G S = G."""
    ports, R = stage_ports()
    S, G = rtype_scattering(incidence(ports, RTYPE_NODES), R), np.diag(1.0 / R)
    assert np.max(np.abs(S.T @ G @ S - G)) < 1e-12 * np.max(G)


def test_three_port_parallel_and_series_are_the_classical_adaptors():
    R = np.array([1e3, 4.7e3, 22e3])
    g = (1.0 / R) / np.sum(1.0 / R)
    par = rtype_scattering(incidence([("x", None)] * 3, ["x"]), R)       # every port across x and ground
    assert np.allclose(par, 2.0 * np.outer(np.ones(3), g) - np.eye(3), atol=1e-14)
    ser = rtype_scattering(incidence([("x", None), ("y", "x"), (None, "y")], ["x", "y"]), R)   # one loop
    assert np.allclose(ser, np.eye(3) - 2.0 * np.outer(R / R.sum(), np.ones(3)), atol=1e-14)


@pytest.mark.parametrize("r_int", [1.0, 1e3, 1e5, 1e7])
def test_kmethod_root_does_not_depend_on_internal_port_resistances(r_int):
    """E, F, M, N eliminate the unadapted root ports' waves, so the arbitrary R_I drops out (to rounding)."""
    ref = WdfStage(48000.0)
    st = WdfStage(48000.0, r_internal=(r_int, 2.0 * r_int))
    for name in "EFMN":
        a, b = getattr(st, name), getattr(ref, name)
        assert np.max(np.abs(a - b)) < 1e-9 * np.max(np.abs(b)), name


def test_kmethod_K_is_the_driving_point_impedance_of_the_rest_of_the_circuit():
    """v_C = p - K i_C: K is the 2x2 impedance the triode's ports see, from nodal analysis of the adapted ports."""
    st = WdfStage(48000.0)
    R_E = np.array([p.adapt() for p in st.ports])
    inc_E, inc_I = incidence(EXT_PORTS, RTYPE_NODES), incidence(ROOT_PORTS, RTYPE_NODES)
    Z = inc_I.T @ np.linalg.solve(inc_E @ np.diag(1.0 / R_E) @ inc_E.T, inc_I)
    assert np.max(np.abs(np.array(st.K) - Z)) < 1e-10 * np.max(np.abs(Z))


def test_leaves_and_two_port_adaptors_obey_kirchhoff():
    """A source (1 V, 1k) driving Series(1k, Parallel(2k, 2k)), joined at a two-port parallel junction."""
    src, r1, r2, r3 = ResistiveSource(1e3, 1.0), Resistor(1e3), Resistor(2e3), Resistor(2e3)
    load = Series(r1, Parallel(r2, r3))
    assert load.R == pytest.approx(2e3)
    Rs, Rl = src.adapt(), load.adapt()
    b_s, b_l = src.up(), load.up()
    v = (b_s / Rs + b_l / Rl) / (1 / Rs + 1 / Rl)
    src.down(2 * v - b_s)
    load.down(2 * v - b_l)
    assert voltage(load) == pytest.approx(2.0 / 3.0)        # 1 V into 1k + 2k = 3k, 2k of it in the load
    assert voltage(r1) == pytest.approx(1.0 / 3.0)
    assert voltage(r2) == pytest.approx(1.0 / 3.0) and voltage(r3) == pytest.approx(1.0 / 3.0)


def test_capacitor_is_the_trapezoidal_rule():
    """C charged through R by a 1 V step from rest: the WDF one-port equals the trapezoidal recursion of
    RC dv/dt = u - v, v[n] (1 + k) = v[n-1] (1 - k) + k (u[n] + u[n-1]), k = T / 2RC, with u[-1] = 0."""
    fs, R, C = 48000.0, 1e3, 1e-6
    src, cap = ResistiveSource(R, 1.0), Capacitor(2 * fs * C)
    Rs, Rc = src.adapt(), cap.adapt()
    v_trap, k, u_prev = 0.0, 1.0 / (2 * fs * R * C), 0.0
    for _ in range(200):
        b_s, b_c = src.up(), cap.up()
        i = (b_s - b_c) / (Rs + Rc)                         # the one loop current, into the capacitor
        src.down(b_s - 2 * Rs * i)
        cap.down(b_c + 2 * Rc * i)
        v_trap, u_prev = ((1 - k) * v_trap + k * (1.0 + u_prev)) / (1 + k), 1.0
        assert voltage(cap) == pytest.approx(v_trap, abs=1e-14)
