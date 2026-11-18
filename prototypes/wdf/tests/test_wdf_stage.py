"""WDF stage against the DK stage's criteria (dk/validate.CRIT, tested against reference (ii): ngspice with the same
Dempwolf-Zoelzer equations) in every quality setting, through dk.validate.run itself (wdf/validate.run_wdf), and
against the DK stage directly.
Run from prototypes/wdf:  python -m pytest -q tests
"""
import os
import sys
import warnings
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wdf import validate as W  # noqa: E402 -- after the sys.path setup
from wdf.stage import WdfStage  # noqa: E402 -- after the sys.path setup
from dk import validate as V, solver  # noqa: E402 -- after the sys.path setup
from dk.stage import Stage, QUALITY, OperatingPointError, NonConvergenceWarning  # noqa: E402
from spice.run_refs import AMPS  # noqa: E402 -- after the sys.path setup

C = V.CRIT
FS = V.FS
# WDF vs DK: the same discrete-time system, so these are rounding and Newton-tolerance (1e-6 V) limits, not criteria.
# Measured worst over the three settings (wdf/RESULTS.md): op 4e-12 rel, AC 4e-10 dB / 1e-9 deg, sines 7e-8 V, burst
# Vgk 7e-12 V.
SAME_OP_REL, SAME_AC_DB, SAME_AC_DEG, SAME_OUT_V, SAME_VGK_V = 1e-9, 1e-7, 1e-6, 1e-5, 1e-8


@pytest.fixture(scope="module", params=list(QUALITY))
def res(request):
    return W.run_wdf(request.param)


@pytest.fixture(scope="module", params=list(QUALITY))
def direct(request):
    return W.direct(request.param)


@pytest.mark.parametrize("quality", QUALITY)
def test_silence_has_no_startup_thump(quality):
    s = WdfStage(FS, quality=quality)
    y = s.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6


@pytest.mark.parametrize("quality", QUALITY)
def test_silence_stays_at_the_operating_point(quality):
    """The WDF runs on absolute waves (HT and the bias currents flow through the tree), so this is the check the DK
    stage's absolute-state form failed before #70 (it drifted 1.8e-7 V at standard, 5e-7 V at hq within 10 ms on
    the M1). The WDF's rest is its operating point to 6e-11 V (eco), with no drift over 0.2 s (wdf/RESULTS.md); 1e-9 V
    is the DK stage's limit for the same test."""
    s = WdfStage(FS, quality=quality)
    y = s.process(np.zeros(480))["out"]
    assert np.max(np.abs(y)) < 1e-9
    assert np.max(np.abs(s.states() - s.dc["x"])) < 1e-9 * np.max(np.abs(s.dc["x"]))


def test_newton_always_converges(res):
    for k, st in res["iterations"].items():
        assert st["nonconverged"] == 0 and st["converged"], k


@pytest.mark.parametrize("q", ["va", "vk", "ia", "vgk"])
def test_operating_point_within_0p1pct(res, q):
    assert res["vs"]["dz"]["op_rel"][q] < C["op_rel"]


def test_small_signal_within_0p1dB_20Hz_20kHz(res):
    o = res["vs"]["dz"]
    print(f"{res['quality']}: worst {o['ac_maxdev_db_chain']:.4f} dB at {o['ac_worst_f_chain']:.0f} Hz")
    assert o["ac_maxdev_db_chain"] < C["ac_db"]


@pytest.mark.parametrize("amp", AMPS)
def test_harmonics_H2_H5_within_0p1dB_above_m90dBc(res, amp):
    rows = res["vs"]["dz"]["harm"][amp]
    tested = {k: v for k, v in rows.items() if isinstance(v, dict) and v["tested"]}
    assert tested, "no harmonic above the reference's numerical floor"
    for k, v in tested.items():
        assert abs(v["dev_db"]) < C["harm_db"], (amp, k, v)


def test_burst_bias_shift_and_recovery(res):
    b = res["vs"]["dz"]["burst"]
    assert b["min_rel"] < C["burst_min_rel"]
    assert b["tau_rel"] < C["burst_tau_rel"]
    assert b["traj_rel"] < C["burst_traj_rel"]


def test_every_criterion_passes(res):
    assert all(res["pass_vs_dz"].values()), res["pass_vs_dz"]


# ---- WDF vs DK: same circuit, same discretisation, same solver
def test_same_operating_point_as_dk(direct):
    assert direct["op_rel_max"] < SAME_OP_REL, direct["op_rel"]


def test_same_small_signal_response_as_dk(direct):
    assert direct["ac_db_max"] < SAME_AC_DB and direct["ac_deg_max"] < SAME_AC_DEG


def test_same_output_and_newton_iterations_as_dk(direct):
    for a, r in direct["sine"].items():
        assert r["max_abs_v"] < SAME_OUT_V and r["iterations_equal"], (a, r)
    b = direct["burst"]
    assert b["max_abs_out_v"] < SAME_OUT_V and b["max_abs_vgk_v"] < SAME_VGK_V and b["iterations_equal"], b


def test_probed_map_is_the_dk_map_up_to_a_change_of_state_basis():
    """The WDF's sample map in DK form (WdfStage.m) and the DK matrices: equal where they are basis free (K; the
    poles, i.e. the eigenvalues of A)."""
    w, d = WdfStage(FS), Stage(FS)
    assert np.max(np.abs(w.m["K"] - d.m["K"])) < 1e-10 * np.max(np.abs(d.m["K"]))
    ew, ed = np.sort_complex(np.linalg.eigvals(w.m["A"])), np.sort_complex(np.linalg.eigvals(d.m["A"]))
    assert np.max(np.abs(ew - ed)) < 1e-9


# ---- parameter changes
def test_bias_swap_needs_no_dc_solve(monkeypatch):
    """swap_bias re-adapts the tree and keeps the waves: no Newton call (the DK stage's swap_bias runs a DC solve)."""
    w = WdfStage(FS)
    calls = []
    monkeypatch.setattr("wdf.stage.solve", lambda *a, **k: calls.append(a) or solver.solve(*a, **k))
    w.swap_bias(1.5)
    assert not calls
    assert w.cathode.R == pytest.approx(1.0 / (1.0 / (1.5 * 1.5e3) + 1.0 / w.caps["Ck"].R))


@pytest.mark.parametrize("bias", [0.5, 1.5, 2.0])
def test_bias_swap_mid_stream_matches_dk(bias):
    """A live bias step on a 2 V sine: the WDF (waves kept, tree re-adapted) and the DK stage (history re-expressed
    around the new operating point) follow the same trajectory."""
    w, d = WdfStage(FS), Stage(FS)
    x = 2.0 * np.sin(2 * np.pi * 1000 * np.arange(1440) / FS)
    yw, yd = [w.process(x[:480])["out"]], [d.process(x[:480])["out"]]
    w.swap_bias(bias)
    d.swap_bias(bias)
    yw.append(w.process(x[480:])["out"])
    yd.append(d.process(x[480:])["out"])
    assert np.max(np.abs(np.concatenate(yw) - np.concatenate(yd))) < SAME_OUT_V


def test_operating_point_after_bias_swap_is_the_new_one():
    w, d = WdfStage(FS), Stage(FS, bias=1.7)
    w.swap_bias(1.7)
    assert abs(w.dc["vgk"] / d.dc["vgk"] - 1) < SAME_OP_REL and abs(w.dc["ia"] / d.dc["ia"] - 1) < SAME_OP_REL


def test_drive_and_output_are_gains():
    x = 0.5 * np.sin(2 * np.pi * 1000 * np.arange(480) / FS)
    a = WdfStage(FS, drive=2.0, output=0.5).process(x)["out"]
    b = WdfStage(FS).process(2.0 * x)["out"]
    assert np.max(np.abs(a - 0.5 * b)) < 1e-12


# ---- non-convergence (the DK stage's #7 contract)
def test_dc_nonconvergence_raises():
    with pytest.raises(OperatingPointError, match="DC operating point did not converge"):
        WdfStage(FS, newton=dict(max_it=1))


def test_forced_runtime_nonconvergence_is_reported_and_finite():
    s = WdfStage(FS)
    s.newton = (1, 0.0, 0.0)
    x = 4.0 * np.sin(2 * np.pi * 1000 * np.arange(480) / FS)
    with pytest.warns(NonConvergenceWarning, match="did not converge"):
        y = s.process(x)["out"]
    n = 480 * s.os
    st = s.iteration_stats()
    assert s.nonconv == s.nonconv_block == st["nonconverged"] == st["samples"] == n and st["converged"] is False
    assert np.isfinite(y).all() and np.isfinite(s.states()).all() and np.isfinite(s.v).all()
    s.newton = (solver.MAX_IT, solver.TOL_V, solver.TOL_F)
    with warnings.catch_warnings():
        warnings.simplefilter("error", NonConvergenceWarning)
        y = s.process(x)["out"]
    assert s.nonconv_block == 0 and np.isfinite(y).all()


def test_unknown_newton_option_is_rejected():
    with pytest.raises(TypeError, match="max_iter"):
        WdfStage(FS, newton=dict(max_iter=1))
