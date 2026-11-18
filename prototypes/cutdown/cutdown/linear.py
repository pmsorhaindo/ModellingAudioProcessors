"""Small-signal linearisation of the cut-down 3-state stage (#23)."""
import numpy as np
import cutdown  # noqa: F401 -- ensures FULLSTAGE_DIR is on sys.path
from dk.triode import currents  # noqa: E402 -- after cutdown path setup
from . import circuit  # noqa: E402 -- after cutdown path setup


def jacobian_p(stage):
    """2x2 port map sensitivity d(Ig, Ia)/d(p0, p1) at the DC operating point."""
    if stage.solver_mode == "lut":
        return stage.table.jacobian_p(float(stage.p_dc[0]), float(stage.p_dc[1]))
    dc = stage.dc
    J = np.array(currents(dc["vgk"], dc["vak"], stage.par)[2])
    return J @ np.linalg.inv(np.eye(2) + stage.m["K"] @ J)


def linearised(stage):
    """(Al, Bl, Cl, Dl): the discretised 3-state stage linearised at p_dc, input vin -> output out."""
    m = stage.m
    Mp = jacobian_p(stage)
    G, H = m["G"], m["H"][:, :1]
    return (
        m["A"] + m["C"] @ Mp @ G,
        m["B"][:, :1] + m["C"] @ Mp @ H,
        m["D"][:1] + m["F"][:1] @ Mp @ G,
        m["E"][:1, :1] + m["F"][:1] @ Mp @ H,
    )


def stage_response(stage, f):
    """Complex small-signal response of the 3-state stage alone at internal rate stage.fs."""
    Al, Bl, Cl, Dl = linearised(stage)
    I = np.eye(Al.shape[0])  # noqa: E741 -- the identity matrix I of the DK equations
    z = np.exp(2j * np.pi * np.atleast_1d(np.asarray(f, float)) / stage.fs)
    return np.array([(Cl @ np.linalg.solve(zz * I - Al, Bl) + Dl)[0, 0] for zz in z])


def analog_response(stage, f):
    """Exact continuous-time small-signal response of the 3-state netlist (without pF caps)."""
    dc = stage.dc
    J = np.array(currents(dc["vgk"], dc["vak"], stage.par)[2])
    return circuit.analog_response(f, J, stage.bias)
