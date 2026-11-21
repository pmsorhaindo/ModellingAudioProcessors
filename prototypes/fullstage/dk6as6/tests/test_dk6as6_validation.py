"""Live validation of the 6AS6 DK stage against its same-model ngspice references, second shard (PR #128 review,
#127): the default stage (dualg) at hq at the ends of P1's grid references and at standard at the top of T's supported
envelope, and the no-grid stage (grid_hardness=0) at T 0.25 mA (#121's case). The checks are validation_checks.py;
test_dk6as6_stage.py runs the other cases.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests
"""
import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dk6as6 import validate as V  # noqa: E402
from dk6as6.stage import SUPPORTED_MA  # noqa: E402
from spice6as6.run_refs import GRID_BIASES  # noqa: E402
from validation_checks import (test_mode_harmonic_character, test_newton_always_converges,  # noqa: E402,F401
                               test_operating_point_within_0p1pct, test_small_signal_within_0p1dB_20Hz_20kHz,
                               test_harmonics_H2_H5_within_0p1dB_above_m90dBc, test_burst_bias_shift_and_recovery,
                               test_all_passed)

pytestmark = pytest.mark.filterwarnings("ignore::dk6as6.stage.ExperimentalWarning")

CASES = ([("P1", "hq", b, "dualg") for b in (GRID_BIASES["P1"][0], GRID_BIASES["P1"][-1])]
         + [("T", "standard", SUPPORTED_MA["T"][1], "dualg"), ("T", "standard", 0.25, "dual")])


@pytest.fixture(scope="module", params=CASES, ids=str)
def res(request):
    m, q, b, model = request.param
    return V.run(quality=q, bias=b, mode=m, model=model)
