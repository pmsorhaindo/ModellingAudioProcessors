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


def miller_response(stage, f):
    """Complex small-signal response of the Method A 1-pole Miller LPF at internal rate stage.fs (1 if disabled)."""
    f = np.atleast_1d(np.asarray(f, float))
    if not stage.miller_lpf:
        return np.ones_like(f, dtype=complex)
    return stage.miller_filter.response(f, stage.fs)


def treble_comp_response(stage, f):
    """Complex small-signal response of the base-rate treble compensator at stage.fs_base (1 if disabled)."""
    f = np.atleast_1d(np.asarray(f, float))
    if not stage.treble_comp:
        return np.ones_like(f, dtype=complex)
    return stage.treble_filter.response(f, stage.fs_base)


def adaa_response(stage, f):
    """Small-signal linearised frequency response of ADAA at internal rate stage.fs."""
    f = np.atleast_1d(np.asarray(f, float))
    if stage.adaa == 0:
        return np.ones_like(f, dtype=complex)
    zi = np.exp(-2j * np.pi * f / stage.fs)
    if stage.adaa == 1:
        return 0.5 * (1.0 + zi)
    return (1.0 + zi + zi * zi) / 3.0


def stage_response(stage, f):
    """Complex small-signal response of the 3-state stage (with Method A / ADAA / treble comp if enabled)."""
    f = np.atleast_1d(np.asarray(f, float))
    z = np.exp(2j * np.pi * f / stage.fs)
    hlp = miller_response(stage, f)
    htc = treble_comp_response(stage, f)
    if stage.adaa == 0:
        Al, Bl, Cl, Dl = linearised(stage)
        I = np.eye(Al.shape[0])  # noqa: E741 -- the identity matrix I of the DK equations
        return np.array([
            hlp[k] * htc[k] * (Cl @ np.linalg.solve(zz * I - Al, Bl) + Dl)[0, 0]
            for k, zz in enumerate(z)
        ])

    m = stage.m
    Mp0 = jacobian_p(stage)
    A, B, C = m["A"], m["B"][:, :1], m["C"]
    D, E, F = m["D"][:1], m["E"][:1, :1], m["F"][:1]
    G, H = m["G"], m["H"][:, :1]
    I = np.eye(A.shape[0])  # noqa: E741 -- the identity matrix I of the DK equations
    ha = adaa_response(stage, f)
    out = np.empty(len(f), dtype=complex)
    for k, zz in enumerate(z):
        Mp = Mp0 * ha[k]
        Al = A + C @ Mp @ G
        Bl = B + C @ Mp @ H
        Cl = D + F @ Mp @ G
        Dl = E + F @ Mp @ H
        out[k] = hlp[k] * htc[k] * (Cl @ np.linalg.solve(zz * I - Al, Bl) + Dl)[0, 0]
    return out


def analog_response(stage, f):
    """Exact continuous-time small-signal response of the 3-state netlist (without pF caps)."""
    dc = stage.dc
    J = np.array(currents(dc["vgk"], dc["vak"], stage.par)[2])
    return circuit.analog_response(f, J, stage.bias)
