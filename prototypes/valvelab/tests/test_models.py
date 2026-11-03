import numpy as np
import pytest
from valvelab import params as P
from valvelab.models import KorenTriode, KorenPentode, DempwolfTriode

VG = np.linspace(-6, 0, 61)
VA = np.linspace(5, 400, 80)


@pytest.mark.parametrize("name", list(P.KOREN_TRIODE))
def test_koren_triode_monotone_and_nonnegative(name):
    t = KorenTriode(name)
    I = t.ip(VG[:, None], VA[None, :])
    assert np.all(I >= 0)
    assert np.all(np.diff(I, axis=0) >= 0), "Ip must rise with grid voltage"
    assert np.all(np.diff(I, axis=1) >= 0), "Ip must rise with plate voltage"
    assert t.ip(-50.0, 250.0) < 1e-6           # cut off far below the grid base


@pytest.mark.parametrize("name", list(P.KOREN_PENTODE))
def test_koren_pentode_monotone(name):
    p = KorenPentode(name)
    I = p.ip(VG[:, None], 100.0, VA[None, :])
    assert np.all(I >= 0)
    assert np.all(np.diff(I, axis=0) >= 0)
    assert np.all(np.diff(I, axis=1) >= 0)
    # pentode: plate current nearly independent of Va above the knee
    i1, i2 = p.ip(-1.5, 100.0, 150.0), p.ip(-1.5, 100.0, 300.0)
    assert abs(i2 - i1) / i1 < 0.1


def test_dempwolf_monotone_and_grid_current():
    d = DempwolfTriode()
    I = d.ik(VG[:, None], VA[None, :])
    assert np.all(np.diff(I, axis=0) >= 0) and np.all(np.diff(I, axis=1) >= 0)
    assert d.ig(-2.0) < 1e-6 and d.ig(1.0) > 1e-4   # grid conducts only when positive


def test_koren_grid_current_series_network():
    ig = KorenTriode.ig(np.array([-1.0, 0.0, 2.0, 5.0]))
    assert ig[0] == 0 and ig[1] == 0
    # with RGI=2k in series, current is below v/RGI and above (v-0.7)/RGI
    for v, i in zip([2.0, 5.0], ig[2:]):
        assert (v - 0.7) / 2001 < i < v / 2001


def test_every_parameter_set_has_a_source():
    for table in (P.KOREN_TRIODE, P.KOREN_PENTODE, P.DEMPWOLF_TRIODE):
        for name, d in table.items():
            assert d["source"]["url"].startswith("https://"), name
    for v in P.STAND_INS.values():
        assert v["datasheet"].startswith("https://")
