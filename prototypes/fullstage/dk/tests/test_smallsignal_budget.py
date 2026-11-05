"""Small-signal error budget (#38). Matrix computations only (no time stepping).

The 20 Hz-20 kHz small-signal check (validate.CRIT["ac_db"], 0.1 dB) has the least headroom of every criterion.
These tests pin down how it is measured:
  * the reference covers the band edges, where the error peaks;
  * the resampler is counted once (validate.small_signal squared chain_response before #38).
Run from prototypes/fullstage:  python -m pytest -q dk/tests
"""
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk.stage import Stage
from dk import validate as V

FS = V.FS


def band():
    r = V.ref("dz")["ac"]
    f, db = np.array(r["f"]), np.array(r["db"])
    sel = (f >= V.CRIT["ac_band"][0]) & (f <= V.CRIT["ac_band"][1])
    return f[sel], db[sel]


def test_reference_has_the_band_edges():
    f, _ = band()
    assert f[0] == V.CRIT["ac_band"][0] and f[-1] == V.CRIT["ac_band"][1]


@pytest.mark.parametrize("filt", ["fir", "iir"])
def test_chain_counts_the_resampler_once(filt):
    f, _ = band()
    st = Stage(FS, filt=filt)
    stage_db, chain_db = V.small_signal(st, f)
    assert np.allclose(chain_db - stage_db, 20 * np.log10(np.abs(st.rs.chain_response(f))), rtol=0, atol=1e-12)
