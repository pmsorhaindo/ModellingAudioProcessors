"""Unit and validation tests for the cut-down 12AX7 stage (#23)."""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutdown import circuit, linear  # noqa: E402 -- after sys.path setup
from cutdown import validate as V  # noqa: E402 -- after sys.path setup
from cutdown.stage import CutdownStage  # noqa: E402 -- after sys.path setup
from dk import circuit as dk_circuit  # noqa: E402 -- after sys.path setup
from dk import solver as dk_solver  # noqa: E402 -- after sys.path setup
from dk import validate as dk_validate  # noqa: E402 -- after sys.path setup
from spice.run_refs import AMPS  # noqa: E402 -- after sys.path setup

FS = V.FS


@pytest.fixture(scope="module")
def res():
    return V.run()


def test_circuit_reuses_fullstage_values_and_has_3_states():
    R, Cs, U, NL, OUT = circuit.netlist()
    assert len(Cs) == 3
    assert [c for _, _, c in Cs] == [dk_circuit.VALUES[k] for k in ("Cin", "Ck", "Cout")]
    m = circuit.build(2 * FS)
    assert m["A"].shape == (3, 3)
    # Cin, Ck, Cout only couple via the nonlinear ports, so A is diagonal.
    off_diag = m["A"] - np.diag(np.diag(m["A"]))
    assert np.max(np.abs(off_diag)) < 1e-15


def test_table_memory_budget_and_dc_rest():
    st = CutdownStage(FS)
    assert st.table.word_count == 8192
    assert st.table.tbl_ig.shape == (128, 32)
    assert st.table.tbl_ia.shape == (128, 32)
    ig_dc, ia_dc = st.table.lookup(float(st.p_dc[0]), float(st.p_dc[1]))
    assert abs(ig_dc - st.i0[0]) < 1e-14
    assert abs(ia_dc - st.i0[1]) < 1e-14


def test_silence_has_no_startup_thump():
    st = CutdownStage(FS)
    y = st.process(np.zeros(4800))["out"]
    assert np.max(np.abs(y)) < 1e-6


def test_silence_stays_at_operating_point():
    st = CutdownStage(FS)
    y = st.process(np.zeros(480))["out"]
    assert np.max(np.abs(y)) < 1e-9
    assert np.max(np.abs(st.x)) < 1e-9 * np.max(np.abs(st.x0))


def test_lut_accuracy_against_newton_over_operating_box():
    st = CutdownStage(FS)
    K = st.K
    p0s = np.linspace(-13.5, 6.0, 25)
    p1s = np.linspace(285.0, 296.0, 7)
    max_dia, max_dig = 0.0, 0.0
    for pp0 in p0s:
        for pp1 in p1s:
            _, _, eig, eia, _, ok = dk_solver.solve(
                float(pp0), float(pp1), K, min(float(pp0), 0.0), float(pp1), max_it=100)
            assert ok
            lig, lia = st.table.lookup(float(pp0), float(pp1))
            max_dia = max(max_dia, abs(lia - eia))
            max_dig = max(max_dig, abs(lig - eig))
    assert max_dia < 2e-5
    assert max_dig < 2e-5


@pytest.mark.parametrize("q", ["va", "vk", "ia", "vgk"])
def test_operating_point_matches_dz_reference(res, q):
    assert res["vs"]["dz"]["op_rel"][q] < dk_validate.CRIT["op_rel"]


def test_small_signal_matches_3state_analog_and_documents_missing_miller_rolloff(res):
    st = CutdownStage(FS)
    r = dk_validate.ref("dz")
    f = np.array(r["ac"]["f"])
    sel = (f >= 20.0) & (f <= 20000.0)
    ds, dch = V.small_signal(st, f[sel])
    ana3 = 20 * np.log10(np.abs(linear.analog_response(st, f[sel])))
    # At 2x (96 kHz), plain trapezoidal warping of Cin/Ck/Cout + LUT slope vs 3-state analog is tiny.
    assert np.max(np.abs(ds - ana3)) < 0.05
    assert np.max(np.abs(dch - ana3)) < 0.05
    # Vs full 6-state ngspice DZ: low/midband is < 0.1 dB; 20 kHz is ~+4.8 dB without CCG/CGP/CCP.
    o = res["vs"]["dz"]
    assert abs(o["ac_chain_dev_at"]["20"]) < 0.1
    assert abs(o["ac_chain_dev_at"]["1000"]) < 0.1
    assert 4.0 < o["ac_chain_dev_at"]["20000"] < 5.5


def test_harmonics_vs_dz_and_thd(res):
    o = res["vs"]["dz"]["harm"]
    for a in ("2", "8"):
        for k, h in o[a].items():
            if isinstance(h, dict) and h["tested_in_full_dk"]:
                assert abs(h["dev_db"]) < 0.5, (a, k, h)
    for k, h in o["0.5"].items():
        if isinstance(h, dict) and h["tested_in_full_dk"]:
            assert abs(h["dev_db"]) < 1.5, ("0.5", k, h)
    # 0.05 V has only H2 above -90 dBc; cubic0_lin1 keeps it within 1.5 dB without bilinear kinks.
    assert abs(o["0.05"]["H2"]["dev_db"]) < 1.5
    for a in AMPS:
        assert abs(o[a]["thd_cutdown_pct"] - o[a]["thd_ref_pct"]) < 1.0


def test_burst_blocking_and_recovery(res):
    b = res["vs"]["dz"]["burst"]
    assert b["min_rel"] < 0.02
    assert b["tau_rel"] < 0.02
    assert b["traj_rel"] < 0.02


def test_blocks_equal_whole():
    t = np.arange(2400) / FS
    x = 3.0 * np.sin(2 * np.pi * 997 * t) + 0.5 * np.random.default_rng(5).standard_normal(len(t))
    ref = CutdownStage(FS).process(x, record=("out", "vgk"))
    st = CutdownStage(FS)
    sizes = np.r_[1, 7, 1, 7, np.random.default_rng(6).integers(1, 257, 200)]
    out, vg, i = [], [], 0
    for n in sizes:
        if i >= len(x):
            break
        r = st.process(x[i:i + n], record=("out", "vgk"))
        i += min(n, len(x) - i)
        out.append(r["out"])
        vg.append(r["vgk"])
    assert i == len(x)
    scale = np.max(np.abs(ref["out"]))
    assert np.max(np.abs(np.concatenate(out) - ref["out"])) < 1e-9 * scale
    assert np.max(np.abs(np.concatenate(vg) - ref["vgk"])) < 1e-9


def test_reset_restores_initial_state():
    st = CutdownStage(FS)
    x = np.sin(2 * np.pi * 500 * np.arange(500) / FS)
    a = st.process(x)["out"]
    st.process(np.ones(100))
    st.reset()
    assert np.array_equal(st.process(x)["out"], a)


@pytest.mark.parametrize("new_bias", [0.5, 1.5, 2.0])
def test_swap_bias_keeps_state_and_rebuilds_table(new_bias):
    st = CutdownStage(FS)
    t = np.arange(1000) / FS
    st.process(2.0 * np.sin(2 * np.pi * 500 * t))
    x_abs_before = st.x0 + st.x
    st.swap_bias(new_bias)
    assert np.allclose(st.x0 + st.x, x_abs_before, rtol=0, atol=1e-12)
    ig_dc, ia_dc = st.table.lookup(float(st.p_dc[0]), float(st.p_dc[1]))
    assert abs(ig_dc - st.i0[0]) < 1e-14
    assert abs(ia_dc - st.i0[1]) < 1e-14
    # After reset at new bias, silence stays at 0 V
    st.reset()
    assert np.max(np.abs(st.process(np.zeros(480))["out"])) < 1e-9


def test_table_domain_covers_8V_20Hz_and_clamping_is_finite():
    st = CutdownStage(FS)
    t = np.arange(int(0.5 * FS)) / FS
    x_up = st.rs.up(8.0 * np.sin(2 * np.pi * 20 * t))
    m = st.m
    G, H0, p_dc = m["G"], m["H"][:, 0], st.p_dc
    x = np.zeros(3)
    p0_lo, p0_hi = np.inf, -np.inf
    p1_lo, p1_hi = np.inf, -np.inf
    for s in x_up:
        p = G @ x + H0 * s + p_dc
        p0_lo, p0_hi = min(p0_lo, p[0]), max(p0_hi, p[0])
        p1_lo, p1_hi = min(p1_lo, p[1]), max(p1_hi, p[1])
        ig, ia = st.table.lookup(float(p[0]), float(p[1]))
        inl = np.array([ig - st.i0[0], ia - st.i0[1]])
        x = m["A"] @ x + m["B"][:, 0] * s + m["C"] @ inl
    assert st.table.p0_min < p0_lo and p0_hi < st.table.p0_max
    assert st.table.p1_min < p1_lo and p1_hi < st.table.p1_max

    # Extreme overdrive beyond table bounds stays finite and bounded.
    st.reset()
    y_ext = st.process(20.0 * np.sin(2 * np.pi * 1000 * np.arange(4800) / FS))["out"]
    assert np.all(np.isfinite(y_ext))


@pytest.mark.parametrize("kw", [
    dict(miller_lpf=True, adaa=0),
    dict(miller_lpf=False, adaa=1),
    dict(miller_lpf=False, adaa=2),
    dict(miller_lpf=True, adaa=1),
])
def test_variants_silence_and_block_invariance(kw):
    st_sil = CutdownStage(FS, **kw)
    assert np.max(np.abs(st_sil.process(np.zeros(480))["out"])) < 1e-9

    # Burst followed by silence must decay cleanly back to DC rest (ADAA du->0 fallback).
    x_tail = np.r_[3.0 * np.sin(2 * np.pi * 1000 * np.arange(960) / FS), np.zeros(int(1.0 * FS))]
    y_tail = st_sil.process(x_tail)["out"]
    assert np.max(np.abs(y_tail[-480:])) < 1e-6

    t = np.arange(2400) / FS
    x = 3.0 * np.sin(2 * np.pi * 997 * t) + 0.5 * np.random.default_rng(5).standard_normal(len(t))
    ref = CutdownStage(FS, **kw).process(x, record=("out", "vgk"))
    st = CutdownStage(FS, **kw)
    sizes = np.r_[1, 7, 1, 7, np.random.default_rng(6).integers(1, 257, 200)]
    out, vg, i = [], [], 0
    for n in sizes:
        if i >= len(x):
            break
        r = st.process(x[i:i + n], record=("out", "vgk"))
        i += min(n, len(x) - i)
        out.append(r["out"])
        vg.append(r["vgk"])
    assert i == len(x)
    scale = np.max(np.abs(ref["out"]))
    assert np.max(np.abs(np.concatenate(out) - ref["out"])) < 1e-9 * scale
    assert np.max(np.abs(np.concatenate(vg) - ref["vgk"])) < 1e-9


def test_miller_cutoff_and_unit_dc_gain():
    st = CutdownStage(FS, miller_lpf=True)
    assert 13500.0 < st.miller_fc_hz < 15000.0
    b0, b1, p = st.miller_coefs
    assert abs((b0 + b1) / (1.0 - p) - 1.0) < 1e-14
    assert 0.0 < st.miller_group_delay_base(1000.0) < 1.0


def test_antiderivatives_match_lut_derivatives():
    st = CutdownStage(FS, adaa=2)
    tbl = st.table
    eps = 1e-6
    for p0 in (-17.0, -5.0, float(st.p_dc[0]), 0.5, 9.0):
        u = (p0 - tbl.p0_min) * tbl.inv_dp0
        j, fw = 15, 0.35
        b0, b1 = 1.0 - fw, fw
        p1 = tbl.p1_min + (j + fw) / tbl.inv_dp1
        fp = tbl.f1_f2(u + eps, j, b0, b1)
        fm = tbl.f1_f2(u - eps, j, b0, b1)
        fc = tbl.f1_f2(u, j, b0, b1)
        lig, lia = tbl.lookup(p0, p1)
        assert abs((fp[0] - fm[0]) / (2 * eps) - lig) < 1e-10
        assert abs((fp[1] - fm[1]) / (2 * eps) - lia) < 1e-9
        assert abs((fp[2] - fm[2]) / (2 * eps) - fc[0]) < 1e-10
        assert abs((fp[3] - fm[3]) / (2 * eps) - fc[1]) < 1e-8


def test_method_a_restores_miller_ac_and_burst():
    res_a = V.run(miller_lpf=True, adaa=0)
    o = res_a["vs"]["dz"]
    assert o["ac_maxdev_db_chain"] < dk_validate.CRIT["ac_db"]
    assert o["burst"]["min_rel"] < dk_validate.CRIT["burst_min_rel"]
    assert o["burst"]["tau_rel"] < dk_validate.CRIT["burst_tau_rel"]
    assert o["burst"]["traj_rel"] < dk_validate.CRIT["burst_traj_rel"]


def test_swap_bias_updates_miller_and_adaa_cleanly():
    st = CutdownStage(FS, miller_lpf=True, adaa=1)
    fc1 = st.miller_fc_hz
    t = np.arange(1000) / FS
    st.process(2.0 * np.sin(2 * np.pi * 500 * t))
    x_abs_before = st.x0 + st.x
    st.swap_bias(1.5)
    assert np.allclose(st.x0 + st.x, x_abs_before, rtol=0, atol=1e-12)
    assert st.miller_fc_hz != fc1
    st.reset()
    assert np.max(np.abs(st.process(np.zeros(480))["out"])) < 1e-9
