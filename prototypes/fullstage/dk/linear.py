"""Small-signal responses of the DK stage at its operating point (#38): discretised and exact analog.

  stage_response  the discretised stage (internal rate, no resampler) linearised at its operating point: what
                  Stage.process() does to a small signal, before the resampler (test_e2e_response checks it)
  analog_response the exact continuous-time response of the same netlist at the same operating point
                  (circuit.analog_response); this is what ngspice's .ac computes (reference (ii), to its 7-decimal
                  rounding), so stage_response / analog_response is the discretisation error alone
Both are input vin -> output out. Matrix computations only, no time stepping.
"""
import numpy as np
from . import circuit


def jacobian(stage):
    """d(Ig, Ia)/d(Vgk, Vak) at the stage's DC operating point, with the stage's triode evaluation (#18)."""
    dc = stage.dc
    return np.array(stage.ev[0](dc["vgk"], dc["vak"], stage.par)[2])


def linearised(stage):
    """(Al, Bl, Cl, Dl): the discretised stage linearised at its operating point, input vin -> output out."""
    m, J = stage.m, jacobian(stage)
    M = np.linalg.inv(np.eye(2) + m["K"] @ J)
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
    return circuit.analog_response(f, jacobian(stage), stage.bias)
