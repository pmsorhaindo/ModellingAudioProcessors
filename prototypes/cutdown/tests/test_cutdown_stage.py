"""Unit and validation tests for the cut-down 12AX7 stage (#23)."""
import os
import sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cutdown import circuit, linear  # noqa: E402 -- after sys.path setup
from cutdown import validate as V  # noqa: E402 -- after sys.path setup
from cutdown.stage import CutdownStage, DEFAULT_PRESET, PRESETS  # noqa: E402 -- after sys.path setup
from dk import circuit as dk_circuit  # noqa: E402 -- after sys.path setup
from dk import solver as dk_solver  # noqa: E402 -- after sys.path setup
from dk import validate as dk_validate  # noqa: E402 -- after sys.path setup
from spice.run_refs import AMPS  # noqa: E402 -- after sys.path setup

FS = V.FS


@pytest.fixture(scope="module")
def res():
    return V.run(preset="baseline")


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
    st = CutdownStage(FS, preset="baseline")
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
    st = CutdownStage(FS, preset="baseline")
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


@pytest.mark.parametrize("preset", list(PRESETS))
def test_variants_silence_and_block_invariance(preset):
    st_sil = CutdownStage(FS, preset=preset)
    assert np.max(np.abs(st_sil.process(np.zeros(480))["out"])) < 1e-9

    # Burst followed by silence must decay cleanly back to DC rest (ADAA du->0 fallback).
    x_tail = np.r_[3.0 * np.sin(2 * np.pi * 1000 * np.arange(960) / FS), np.zeros(int(1.0 * FS))]
    y_tail = st_sil.process(x_tail)["out"]
    assert np.max(np.abs(y_tail[-480:])) < 1e-6

    t = np.arange(2400) / FS
    x = 3.0 * np.sin(2 * np.pi * 997 * t) + 0.5 * np.random.default_rng(5).standard_normal(len(t))
    ref = CutdownStage(FS, preset=preset).process(x, record=("out", "vgk"))
    st = CutdownStage(FS, preset=preset)
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
    st = CutdownStage(FS, preset="method_a")
    assert 13500.0 < st.miller_fc_hz < 15000.0
    b0, b1, p = st.miller_coefs
    assert abs((b0 + b1) / (1.0 - p) - 1.0) < 1e-14
    assert 0.0 < st.miller_group_delay_base(1000.0) < 1.0


def test_antiderivatives_match_lut_derivatives():
    st = CutdownStage(FS, preset="method_b2")
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


def test_adaa1_dev_matches_f1_slices_and_24bit_conditioning():
    st = CutdownStage(FS)
    tbl = st.table
    rng = np.random.default_rng(42)
    pairs = list(zip(rng.uniform(-4.0, tbl.n0 + 2.0, 200), rng.uniform(-4.0, tbl.n0 + 2.0, 200)))
    pairs += [(76.3, 76.300001), (76.9999, 77.0001), (76.0, 77.0), (-0.5, 0.5), (tbl.n0 - 1.5, tbl.n0 - 0.5)]
    for u, up in pairs:
        if abs(u - up) < 1e-6:
            continue
        j = int(rng.integers(0, tbl.n1 - 1))
        fw = float(rng.uniform(0.0, 1.0))
        g0, g1, a0, a1 = tbl.f1_slices(u, j)
        pg0, pg1, pa0, pa1 = tbl.f1_slices(up, j)
        ref_g = ((g0 + fw * (g1 - g0)) - (pg0 + fw * (pg1 - pg0))) / (u - up) - st.i0[0]
        ref_a = ((a0 + fw * (a1 - a0)) - (pa0 + fw * (pa1 - pa0))) / (u - up) - st.i0[1]
        got_g, got_a = tbl.adaa1_dev(u, up, j, fw)
        assert abs(got_g - ref_g) < 1e-14
        assert abs(got_a - ref_a) < 1e-13

    # Quantize deviation tables & rest-anchored ds1 to 24-bit fixed point (LSB = 2^-23 * 2^ceil(log2(max))).
    st_q = CutdownStage(FS)
    tb_q = st_q.table
    for arr_name in ("dtbl_ig", "dtbl_ia", "ds1_ig", "ds1_ia"):
        arr = getattr(tb_q, arr_name)
        q = 2.0 ** np.ceil(np.log2(np.max(np.abs(arr)))) * 2.0 ** -23
        setattr(tb_q, arr_name, np.round(arr / q) * q)
    tb_q._dtig_flat = tb_q.dtbl_ig.ravel().tolist()
    tb_q._dtia_flat = tb_q.dtbl_ia.ravel().tolist()
    tb_q._ds1g_flat = tb_q.ds1_ig.ravel().tolist()
    tb_q._ds1a_flat = tb_q.ds1_ia.ravel().tolist()

    t = np.arange(int(0.2 * FS)) / FS
    for amp, min_snr in ((0.001, 75.0), (0.01, 95.0), (0.5, 115.0)):
        st.reset()
        st_q.reset()
        x = amp * np.sin(2.0 * np.pi * 1000.0 * t)
        y0 = st.process(x)["out"][4800:]
        y1 = st_q.process(x)["out"][4800:]
        snr = 20.0 * np.log10(np.sqrt(np.mean(y0 ** 2)) / np.sqrt(np.mean((y1 - y0) ** 2)))
        assert snr > min_snr, (amp, snr)


def test_method_a_restores_miller_ac_and_burst():
    res_a = V.run(preset="method_a")
    o = res_a["vs"]["dz"]
    assert o["ac_maxdev_db_chain"] < dk_validate.CRIT["ac_db"]
    assert o["burst"]["min_rel"] < dk_validate.CRIT["burst_min_rel"]
    assert o["burst"]["tau_rel"] < dk_validate.CRIT["burst_tau_rel"]
    assert o["burst"]["traj_rel"] < dk_validate.CRIT["burst_traj_rel"]


def test_default_is_ab1c():
    st = CutdownStage(FS)
    assert DEFAULT_PRESET == "method_ab1c"
    assert st.miller_lpf is True
    assert st.adaa == 1
    assert st.treble_comp is True
    assert st.table.total_word_count(st.adaa) == 16384


def test_invalid_comp_and_adaa_combinations_rejected():
    with pytest.raises(ValueError):
        CutdownStage(FS, adaa=0, treble_comp=True)
    with pytest.raises(ValueError):
        CutdownStage(FS, adaa=2, treble_comp=True)
    with pytest.raises(ValueError):
        CutdownStage(FS, preset="method_ab1c", adaa=0)
    with pytest.raises(ValueError):
        CutdownStage(FS, preset="unknown_preset")
    st_nwt = CutdownStage(FS, solver_mode="newton", adaa=0)
    assert st_nwt.treble_comp is False


def test_filter_design_identities_and_realisability_checks():
    for fs in (44100.0, 48000.0, 96000.0):
        f1, f2 = circuit.treble_comp_match_points(fs, 2.0 * fs)
        b0, b1, p = circuit.treble_comp_coefs_at(fs, 2.0 * fs, f1, f2)
        assert abs((b0 + b1) / (1.0 - p) - 1.0) < 1e-14
        assert abs(p) < 1.0
        assert abs(b1) <= abs(b0)
        sec = circuit.FirstOrderSection(b0, b1, p)
        for fk in (f1, f2):
            mag2 = float(np.abs(sec.response([fk], fs)[0]) ** 2)
            target2 = 1.0 / (np.cos(np.pi * fk / (2.0 * fs)) ** 2)
            assert abs(mag2 - target2) < 1e-12
        # Analytical group delay matches tight finite difference
        f_chk = 5000.0
        df = 0.5
        h1, h2 = sec.response([f_chk - df, f_chk + df], fs)
        gd_fd = -np.angle(h2 / h1) / (2.0 * np.pi * (2.0 * df) / fs)
        assert abs(sec.group_delay(f_chk, fs) - gd_fd) < 1e-6

    # Clamped Miller f_eff consistency + negative-discriminant rejection
    b0m, b1m, pm = circuit.miller_filter_coefs(14000.0, 40000.0, f_ref=20000.0)
    sec_m = circuit.FirstOrderSection(b0m, b1m, pm)
    f_eff = 0.45 * 40000.0
    assert abs(float(np.abs(sec_m.response([f_eff], 40000.0)[0]) ** 2) - 1.0 / (1.0 + (f_eff / 14000.0) ** 2)) < 1e-12
    with pytest.raises(ValueError):
        circuit.treble_comp_coefs_at(48000.0, 44000.0, 14000.0, 20000.0)


@pytest.mark.parametrize("fs", [44100.0, 48000.0, 96000.0])
def test_treble_comp_cancels_adaa1_droop_across_rates(fs):
    st = CutdownStage(fs)
    b0, b1, p = st.treble_comp_coefs
    assert abs((b0 + b1) / (1.0 - p) - 1.0) < 1e-14
    assert abs(p) < 1.0
    assert abs(st.treble_comp_group_delay_base(1000.0)) < 0.5
    f = np.geomspace(20.0, 20000.0, 400)
    combined = np.abs(linear.treble_comp_response(st, f) * linear.adaa_response(st, f))
    assert np.max(np.abs(20.0 * np.log10(combined))) < 0.07


@pytest.mark.parametrize("fs", [44100.0, 48000.0, 96000.0])
def test_default_ab1c_ac_flatness_across_rates(fs):
    st = CutdownStage(fs)
    r = dk_validate.ref("dz")
    f = np.array(r["ac"]["f"])
    sel = (f >= dk_validate.CRIT["ac_band"][0]) & (f <= dk_validate.CRIT["ac_band"][1])
    ds, dch = V.small_signal(st, f[sel])
    refdb = np.array(r["ac"]["db"])[sel]
    assert np.max(np.abs(ds - refdb)) < dk_validate.CRIT["ac_db"]
    assert np.max(np.abs(dch - refdb)) < dk_validate.CRIT["ac_db"]


def test_default_ab1c_passes_ac_burst_and_harmonics_regression_vs_dz():
    res_c = V.run(preset="method_ab1c")
    o = res_c["vs"]["dz"]
    assert o["ac_maxdev_db_stage"] < dk_validate.CRIT["ac_db"]
    assert o["ac_maxdev_db_chain"] < dk_validate.CRIT["ac_db"]
    assert o["burst"]["min_rel"] < dk_validate.CRIT["burst_min_rel"]
    assert o["burst"]["tau_rel"] < dk_validate.CRIT["burst_tau_rel"]
    assert o["burst"]["traj_rel"] < dk_validate.CRIT["burst_traj_rel"]
    for a, bound in (("0.05", 1.5), ("0.5", 1.5), ("2", 0.5), ("8", 0.5)):
        for h in o["harm"][a].values():
            if isinstance(h, dict) and h["tested_in_full_dk"]:
                assert abs(h["dev_db"]) < bound, (a, h)


def test_swap_bias_updates_miller_and_adaa_cleanly_without_reset():
    t = np.arange(2000) / FS
    x = 2.0 * np.sin(2.0 * np.pi * 500.0 * t)
    ref = CutdownStage(FS, preset="method_ab1c").process(x)["out"]

    # Identity swap mid-signal must match unswapped output bit-for-bit, and small swap must be glitch-free.
    st_id = CutdownStage(FS, preset="method_ab1c")
    y1_id = st_id.process(x[:1000])["out"]
    st_id.swap_bias(1.0)
    y2_id = st_id.process(x[1000:])["out"]
    assert np.allclose(np.r_[y1_id, y2_id], ref, rtol=0, atol=1e-12)

    st = CutdownStage(FS, preset="method_ab1c")
    fc1 = st.miller_fc_hz
    y_pre = st.process(x[:1000])["out"]
    x_abs_before = st.x0 + st.x
    st.swap_bias(1.05)
    assert np.allclose(st.x0 + st.x, x_abs_before, rtol=0, atol=1e-12)
    assert st.miller_fc_hz != fc1
    y_cont = st.process(x[1000:])["out"]
    assert abs((y_cont[0] - y_pre[-1]) - (ref[1000] - ref[999])) < 1e-2

    # Larger swap followed by silence without reset must stay finite and decay cleanly to rest.
    st.swap_bias(1.5)
    y_after = st.process(np.zeros(int(1.0 * FS)))["out"]
    assert np.all(np.isfinite(y_after))
    assert np.max(np.abs(y_after[-480:])) < 1e-6
    st.reset()
    assert np.max(np.abs(st.process(np.zeros(480))["out"])) < 1e-9
