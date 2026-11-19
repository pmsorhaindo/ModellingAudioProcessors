"""End-to-end checks on the WDF stage's process(), as dk/tests does for the DK stage.

* Small signal (dk/tests/test_e2e_response.py's method): a 1 mV multitone on exact FFT bins through process(), after
  0.3 s of settling, against the ngspice AC reference (CRIT["ac_db"]) and, in gain and phase, against the exact
  linearisation of the chain (WdfStage.m through dk/linear.py, times the resampler). This is what ties the
  small-signal criterion, which dk.validate computes from the probed matrices, to the per-sample tree passes.
* Aliasing: the worst CI tone of the standard setting (FIR) at 2 V and 8 V, with dk/aliasing_guard.py's buffers,
  against the DK stage's limit (aliasing.LIMIT_DBC) and its committed baseline within aliasing_guard.TOLERANCE_DB.
  The full CI subset of every setting runs in `python -m wdf.validate` (wdf/RESULTS.md).
Run from prototypes/wdf:  python -m pytest -q tests
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdf.stage import WdfStage  # noqa: E402 -- after the sys.path setup
from dk import validate as V, linear, aliasing as AL, aliasing_guard as AG  # noqa: E402
from dk.stage import QUALITY  # noqa: E402 -- after the sys.path setup

FS = V.FS
WIN_S, SETTLE_S, AMP = 0.1, 0.3, 1e-3
TONES = np.array([20, 40, 80, 150, 300, 600, 1000, 2000, 4000, 8000, 12000, 16000, 18000, 20000.0])
PHASE_DEG, LIN_DB = 0.5, 0.002          # dk/tests/test_e2e_response.py's limits


@pytest.fixture(scope="module", params=list(QUALITY))
def measured(request):
    n0, N = int(SETTLE_S * FS), int(WIN_S * FS)
    st = WdfStage(FS, quality=request.param)
    t = np.arange(n0 + N + st.latency_samples) / FS
    ph = np.random.default_rng(0).uniform(0, 2 * np.pi, len(TONES))
    x = sum(AMP * np.sin(2 * np.pi * f * t + p) for f, p in zip(TONES, ph))
    y = V.aligned(st, x)["out"]
    k = np.rint(TONES * WIN_S).astype(int)
    Hm = np.fft.rfft(y[n0:n0 + N])[k] / np.fft.rfft(x[n0:n0 + N])[k]
    return st, Hm, linear.stage_response(st, TONES) * np.abs(st.rs.chain_response(TONES))


def test_e2e_gain_matches_ngspice_within_0p1dB(measured):
    _, Hm, _ = measured
    r = V.ref("dz")["ac"]
    dev = 20 * np.log10(np.abs(Hm)) - np.interp(np.log(TONES), np.log(r["f"]), r["db"])
    assert np.max(np.abs(dev)) < V.CRIT["ac_db"], dict(zip(TONES, np.round(dev, 4)))


def test_e2e_matches_linearised_chain(measured):
    _, Hm, Hl = measured
    assert np.max(np.abs(20 * np.log10(np.abs(Hm / Hl)))) < LIN_DB
    assert np.max(np.abs(np.degrees(np.angle(Hm / Hl)))) < PHASE_DEG


@pytest.mark.parametrize("drive", [2.0, 8.0])
def test_aliasing_at_the_worst_ci_tone_matches_the_dk_baseline(drive, monkeypatch):
    monkeypatch.setattr(AL, "Stage", WdfStage)
    tone = max(AL.CI_TONES[("standard", "fir")][drive])
    r = AL.measure("standard", "fir", drive, tone, window=AL.CI_WINDOW_S, settle=AL.CI_SETTLE_S)
    base = AG.load_baseline(AG.BASELINE_JSON)["values"][AG.key("standard", "fir", drive)]["tones"][str(tone)]
    assert base["f_hz"] == r["f_hz"]
    assert r["total_dbc"] <= AL.LIMIT_DBC[("standard", "fir")][drive]
    assert abs(r["total_dbc"] - base["total_dbc"]) < AG.TOLERANCE_DB, (r["total_dbc"], base["total_dbc"])


def test_validator_ratchets_a_non_worst_tone_against_its_own_baseline(monkeypatch):
    from wdf import validate as WV
    base = AG.load_baseline(AG.BASELINE_JSON)["values"]

    def synthetic_measure(job):
        q, filt, drive, tone, _ = job
        k = AG.key(q, filt, drive)
        p = dict(base[k]["tones"][str(tone)])
        if (k, tone) == ("hq/fir/8V", 10037):
            p["total_dbc"] += AG.TOLERANCE_DB + 0.01
            assert p["total_dbc"] < base[k]["tones"]["19037"]["total_dbc"]
        return dict(setting=q, filt=filt, drive=drive, tone=tone, **p)

    monkeypatch.setattr(WV, "_wdf_measure", synthetic_measure)
    result = WV.aliasing_subset(workers=1)
    assert len(result["rows"]) == 18 and not result["passed"]
    (bad,) = AG.failed(result["rows"])
    assert (bad["key"], bad["tone"], bad["status"]) == ("hq/fir/8V", 10037, "FAIL regression")
