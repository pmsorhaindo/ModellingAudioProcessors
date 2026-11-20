"""Small-signal responses of the 6AS6 DK stage at its operating point: discretised and exact analog (as dk/linear.py).

  stage_response  the discretised stage (internal rate, no resampler) linearised at its operating point
  analog_response the exact continuous-time response of the same netlist at the same operating point
                  (circuit.analog_response), i.e. what ngspice's .ac computes
"""
import numpy as np
from . import circuit
from .pentode import currents


def jacobian(stage):
    """d(Ip, Ig2)/d(Vg1k, Vg2k, Vg3k, Vak) at the stage's DC operating point (2x4)."""
    dc = stage.dc
    return np.array(currents(dc["vg1k"], dc["vg2k"], dc["vg3k"], dc["vak"], stage.par)[2])


def linearised(stage):
    """(Al, Bl, Cl, Dl): the discretised stage linearised at its operating point, input vin -> output out.
    v = G x + H u - K J v  ->  v = M (G x + H u),  M = (I + K J)^-1 (4x4)."""
    m, J = stage.m, jacobian(stage)
    M = np.linalg.inv(np.eye(4) + m["K"] @ J)
    G, H = m["G"], m["H"][:, :1]
    return (m["A"] + m["C"] @ J @ M @ G, m["B"][:, :1] + m["C"] @ J @ M @ H,
            m["D"][:1] + m["F"][:1] @ J @ M @ G, m["E"][:1, :1] + m["F"][:1] @ J @ M @ H)


def stage_response(stage, f):
    """Complex response of the discretised stage alone (internal rate, no resampler) at frequencies f."""
    Al, Bl, Cl, Dl = linearised(stage)
    I = np.eye(Al.shape[0])  # noqa: E741 -- the identity matrix I of the DK equations
    z = np.exp(2j * np.pi * np.atleast_1d(np.asarray(f, float)) / stage.fs)
    return np.array([(Cl @ np.linalg.solve(zz * I - Al, Bl) + Dl)[0, 0] for zz in z])


def analog_response(stage, f):
    """Exact analog small-signal response at the stage's operating point (circuit.analog_response)."""
    return circuit.analog_response(f, jacobian(stage), stage.rk, stage.mode)
