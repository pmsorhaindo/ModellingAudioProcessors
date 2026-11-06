"""#60: the stage on the fast path, Stage(triode_math="fast") (dk/fastmath.py), against the same validate.CRIT as the
libm path (dk/tests/test_dk_stage.py), reference (ii) = ngspice with the same Dempwolf-Zoelzer equations.
Lean: the full validate.run (op, AC, H2..H5 at every drive, burst) on eco only (~20 s; the cheapest setting and the
one with the largest AC deviation); op point and AC (stage and chain) on every setting. The full run on every
setting is `python -m dk.validate --triode-math fast` -> dk/results_fastmath.json.
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage, QUALITY
from dk import validate as V

C = V.CRIT


@pytest.fixture(scope="module")
def eco():
    return V.run(quality="eco", triode_math="fast")


def test_eco_fast_passes_every_criterion(eco):
    assert eco["triode_math"] == "fast" and eco["criteria"] == C
    assert all(st["nonconverged"] == 0 and st["converged"] for st in eco["iterations"].values())
    assert eco["pass_vs_dz"] == dict(op=True, ac_stage=True, ac_chain=True, harmonics=True, burst=True)


@pytest.mark.parametrize("quality", QUALITY)
def test_fast_op_and_ac_within_criteria(quality):
    r = V.ref("dz")
    st = Stage(V.FS, quality=quality, triode_math="fast")
    dc = st.dc
    op = dict(va=dc["nodes"]["a"], vk=dc["nodes"]["k"], ia=dc["ia"], vgk=dc["vgk"])
    for k, v in op.items():
        assert abs(v / r["op"][k] - 1) < C["op_rel"], k
    f = np.array(r["ac"]["f"]); sel = (f >= C["ac_band"][0]) & (f <= C["ac_band"][1])
    ds, dch = V.small_signal(st, f[sel])
    refdb = np.array(r["ac"]["db"])[sel]
    assert np.max(np.abs(ds - refdb)) < C["ac_db"]
    assert np.max(np.abs(dch - refdb)) < C["ac_db"]
