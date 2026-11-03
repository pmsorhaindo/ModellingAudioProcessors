import numpy as np
import pytest
from valvelab.models import KorenTriode, KorenPentode, DempwolfTriode
from valvelab.stage import Stage
from valvelab.analysis import harmonic_profile


def stages():
    return [Stage(KorenTriode("12AX7"), Rk=1500), Stage(DempwolfTriode(), Rk=1500),
            Stage(KorenTriode("12AU7"), Rp=22e3, Rk=1000), Stage(KorenPentode("EF86"), Rk=1500)]


@pytest.mark.parametrize("s", stages(), ids=lambda s: s.valve.name)
def test_dc_point_sanity(s):
    s.operating_point()
    assert 0 < s.Vk0 < 10
    assert s.Vk0 < s.Va0 < s.B
    ik = s._ik(-s.Vk0, s.Va0, s.Vk0)
    assert abs(s.Rk * ik - s.Vk0) < 1e-6                     # cathode KCL
    assert abs((s.B - s.Va0) / s.Rp - s.Ia0) < 1e-9           # plate KCL


@pytest.mark.parametrize("s", stages(), ids=lambda s: s.valve.name)
def test_gain_matches_small_signal_model(s):
    """Solver gain equals -gm * (Rp || RL || ra) from the model's own derivatives."""
    s.operating_point()
    vk, va, d = s.Vk0, s.Va0, 1e-5
    gm = (s._ip(-vk + d, va, vk) - s._ip(-vk - d, va, vk)) / (2 * d)
    ga = (s._ip(-vk, va + d, vk) - s._ip(-vk, va - d, vk)) / (2 * d)
    expected = -gm / (1 / s.Rp + 1 / s.RL + ga)
    assert s.small_signal_gain() == pytest.approx(expected, rel=2e-3)


def test_transfer_zero_and_monotone():
    s = Stage(KorenTriode("12AX7")).operating_point()
    assert abs(s.transfer(np.array([0.0]))[0]) < 1e-6
    y = s.transfer(np.linspace(-5, 5, 2001))
    assert np.all(np.diff(y) < 0)            # inverting and single-valued


def test_set_bias_va_hits_target():
    s = Stage(KorenPentode("EF86"))
    s.set_bias_va(0.6 * s.B)
    assert s.Va0 == pytest.approx(0.6 * s.B, abs=1e-6)
    assert s.Rk > 0


def test_harmonic_analysis_known_answer():
    A = 0.3
    hp = harmonic_profile(lambda x: x + x ** 2, A)
    assert hp["rel"][1] == pytest.approx(A / 2, rel=1e-9)     # x^2 -> H2/H1 = A/2
    assert hp["rel"][2] < 1e-12
    assert harmonic_profile(lambda x: 3 * x, 1.0)["thd"] < 1e-12
