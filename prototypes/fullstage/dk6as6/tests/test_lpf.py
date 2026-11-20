"""Post-distortion low-pass (#103, dk6as6/lpf.py). Limits from the #103 brief: magnitude / phase against the analog
prototype <= 0.15 dB / 0.35 deg over 20 Hz-20 kHz at 8x of 44.1 and 48 kHz; float32 vs float64 <= -125 dBc at 8x;
stable under fast cutoff modulation; total alias energy with the filter on not worse than off, in absolute terms.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_lpf.py
"""
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk6as6 import lpf, lpf_aliasing, stage  # noqa: E402 -- after the sys.path setup
from dk6as6 import aliasing as aliasing6  # noqa: E402 -- after the sys.path setup

MAG_DB, PHASE_DEG, F32_DBC = 0.15, 0.35, -125.0
OTHER_BELOW_DB = 20.0     # 'other' at least this far under the off total: it moves the total by <= 0.05 dB
CASES = [(fs, p) for fs in lpf.OS_RATES for p in lpf.F0_HZ]


def impulse_response(fs, f0, n=1 << 13, **kw):
    x = np.zeros(n)
    x[0] = 1.0
    return lpf.SVF(fs, f0, **kw).process(x)


def dtft(h, f, fs):
    return np.exp(-2j * np.pi * np.outer(f, np.arange(len(h))) / fs) @ h


def test_constants_trace_to_the_research_comment():
    """Q and f0 as in the #103 comment (F-a fit to the S5 chart; 4k = 7k x 4/7); the shape has the S5 chart's -5 dB
    at 7 kHz (its last point) and -3 dB near 4.92 kHz (F-a; the comment's f0 / Q are rounded, 4.93 kHz here)."""
    assert lpf.Q == 0.4277 and lpf.F0_HZ == {"4k": 9535.0 * 4 / 7, "7k": 9535.0}
    assert lpf.POSITIONS == ("off", "4k", "7k")
    db = lambda f: 20 * np.log10(abs(lpf.analog_response(f, lpf.F0_HZ["7k"])))  # noqa: E731
    assert abs(db(7000.0) + 5.0) < 0.05
    f = np.linspace(4800, 5100, 3001)
    assert abs(f[np.argmin(np.abs(db(f) + 3.0103))] / 4920.0 - 1) < 0.005


def test_svf_is_its_transfer_function():
    """The implementation is the bilinear transform digital_response() analyses (both pre-warp choices)."""
    f = np.geomspace(*lpf.BAND_HZ, 50)
    for fs, p in CASES:
        f0 = lpf.F0_HZ[p]
        for fp in (None, lpf.PREWARP_HZ):
            h = impulse_response(fs, f0, prewarp_hz=fp)
            assert np.max(np.abs(dtft(h, f, fs) / lpf.digital_response(f, fs, f0, prewarp_hz=fp) - 1)) < 1e-9


@pytest.mark.parametrize("fs,p", CASES)
def test_magnitude_and_phase_vs_analog_prototype(fs, p):
    f = np.geomspace(*lpf.BAND_HZ, 400)
    f0 = lpf.F0_HZ[p]
    e = dtft(lpf.PostFilter(fs, p).svf[lpf.POSITIONS.index(p) - 1].process(np.r_[1.0, np.zeros(8191)]), f, fs) \
        / lpf.analog_response(f, f0)
    mag, ph = np.max(np.abs(20 * np.log10(np.abs(e)))), np.max(np.abs(np.degrees(np.angle(e))))
    print(f"{fs:g} {p}: {mag:.4f} dB, {ph:.4f} deg")
    assert mag <= MAG_DB and ph <= PHASE_DEG


def test_prewarp_is_the_minimax():
    fp, worst_db = lpf.prewarp_minimax()
    assert abs(fp - lpf.PREWARP_HZ) < 50.0 and worst_db < -42.0


# Float32 sweep: 20 Hz-20 kHz in third-octave steps, 1 kHz (the original case), and the frequencies the #119 review
# found failing in the direct form (3.2 and 13.6 kHz at 384 kHz, 19.6 kHz at 352.8 kHz: -124.5 / -123.4 / -117.2 dBc).
F32_SINES_HZ = tuple(np.round(np.geomspace(20.0, 20000.0, 31))) + (1000.0, 3200.0, 13600.0, 19600.0)


@pytest.mark.parametrize("fs,p", CASES)
def test_float32_noise_below_m125dBc(fs, p):
    """Float32 filter vs float64, same float32 input, error energy over output energy (DC included), 50 ms: every
    sine of F32_SINES_HZ and white noise."""
    rng = np.random.default_rng(1)
    n = np.arange(int(0.05 * fs))
    xs = {f"{f:g} Hz": np.sin(2 * np.pi * f * n / fs) for f in F32_SINES_HZ}
    xs["noise"] = rng.standard_normal(len(n))
    errs = {}
    for name, x in xs.items():
        x = x.astype(np.float32)
        y32 = lpf.SVF(fs, lpf.F0_HZ[p], prewarp_hz=lpf.PREWARP_HZ, dtype=np.float32).process(x)
        y64 = lpf.SVF(fs, lpf.F0_HZ[p], prewarp_hz=lpf.PREWARP_HZ).process(x.astype(float))
        assert y32.dtype == np.float32
        errs[name] = 10 * np.log10(np.sum((y32 - y64) ** 2) / np.sum(y64 ** 2))
    worst = max(errs, key=errs.get)
    print(f"{fs:g} {p}: worst {errs[worst]:.1f} dBc ({worst}), noise {errs['noise']:.1f} dBc")
    assert all(-170.0 < e <= F32_DBC for e in errs.values()), errs    # lower bound: the float32 path really rounds


@pytest.mark.parametrize("q", [lpf.Q, 5.0])
@pytest.mark.parametrize("prewarp_hz", [None, lpf.PREWARP_HZ])
def test_stable_under_fast_cutoff_modulation(q, prewarp_hz):
    """f0 jumps every sample: random over 20 Hz-0.45 fs, then alternating 4k / 7k. Output stays bounded and decays."""
    fs = lpf.OS_RATES[1]
    rng = np.random.default_rng(2)
    n = 20000
    x = np.r_[rng.uniform(-1, 1, n), np.zeros(n)]
    f0s = (rng.uniform(20.0, 0.45 * fs, 2 * n), np.resize(list(lpf.F0_HZ.values()), 2 * n))
    for f0 in f0s:
        s = lpf.SVF(fs, f0[0], q, prewarp_hz)
        y = np.empty(2 * n)
        for k in range(2 * n):
            s.set_f0(f0[k])
            y[k] = s.tick(x[k])
        assert np.all(np.isfinite(y)) and np.max(np.abs(y)) < 4 * q + 4
        assert np.max(np.abs(y[-1000:])) < 1e-9


def test_position_change_crossfades_two_running_filters():
    fs = lpf.OS_RATES[1]
    x = np.sin(2 * np.pi * 3000 * np.arange(int(0.08 * fs)) / fs)
    ref = {p: lpf.SVF(fs, f0, prewarp_hz=lpf.PREWARP_HZ).process(x) for p, f0 in lpf.F0_HZ.items()}
    ref["off"] = x
    pf = lpf.PostFilter(fs, "7k")
    n0, nf = int(0.02 * fs), pf.n_fade
    assert nf == round(lpf.XFADE_S * fs)
    y0 = pf.process(x[:n0])
    pf.set_position("4k")
    y1 = pf.process(x[n0:])
    y = np.r_[y0, y1]
    t = np.minimum(np.arange(1, len(x) - n0 + 1) / nf, 1.0)
    assert np.array_equal(y0, ref["7k"][:n0])
    assert np.allclose(y1, (1 - t) * ref["7k"][n0:] + t * ref["4k"][n0:], rtol=0, atol=1e-12)
    assert np.array_equal(y[n0 + nf:], ref["4k"][n0 + nf:]) and pf.position() == "4k"
    # retarget mid-fade: the mix moves on from where it is, never jumps
    pf.set_position("off")
    pf.process(x[:nf // 3])
    g = pf.gains()
    pf.set_position("7k")
    assert np.allclose(pf.gains(), g) and np.isclose(g.sum(), 1.0)
    pf.reset()
    assert np.array_equal(pf.gains(), [0.0, 0.0, 1.0])
    with pytest.raises(ValueError):
        pf.set_position("9k")


def test_stage_off_is_the_unfiltered_stage_bit_for_bit():
    x = 2.0 * np.sin(2 * np.pi * 1000 * np.arange(2400) / 48000.0)
    assert np.array_equal(lpf.Stage(48000.0, lpf="off").process(x)["out"], stage.Stage(48000.0).process(x)["out"])


def test_stage_filters_inside_the_oversampled_block():
    """At small signal the base-rate output of Stage(lpf=p) over the unfiltered stage's is the 8x filter's response:
    within the analog-prototype limits up to 19 kHz in the 4k position, where a host-rate filter is off by
    1.5-1.8 dB at 10 kHz already (F-b). The internal 'out' is PostFilter applied to the stage's internal output."""
    fs = 48000.0
    n, n0 = 4800, 4800                       # 10 Hz bins; 100 ms settle
    tones = np.array([1000.0, 5000.0, 10000.0, 19000.0])
    t = np.arange(n0 + n + 200) / fs
    x = 0.01 * np.sum(np.sin(2 * np.pi * tones[:, None] * t), axis=0)
    ref = stage.Stage(fs)
    y0 = ref.process(x, record=("out", "vgk"))
    st = lpf.Stage(fs, lpf="4k")
    y1 = st.process(x, record=("vgk",))
    assert "out" not in y1["_internal"] and np.array_equal(y1["vgk"], y0["vgk"])
    L = st.latency_samples
    st2 = lpf.Stage(fs, lpf="4k")
    y = st2.process(x)
    assert np.array_equal(y["_internal"]["out"], lpf.PostFilter(st.fs, "4k").process(y0["_internal"]["out"]))
    w = slice(n0 + L, n0 + L + n)
    b = (tones * n / fs).astype(int)
    H = np.fft.rfft(y["out"][w])[b] / np.fft.rfft(y0["out"][w])[b]
    e = H / lpf.analog_response(tones, lpf.F0_HZ["4k"])
    assert np.max(np.abs(20 * np.log10(np.abs(e)))) <= MAG_DB
    assert np.max(np.abs(np.degrees(np.angle(e)))) <= PHASE_DEG


@pytest.mark.parametrize("fs,f", [(48000, 10037), (48000, 19037), (44100, 10037), (44100, 19037)])
def test_aliasing_with_filter_not_worse_than_off(fs, f):
    """8 V, default bias, standard 8x, FIR, the CI window (0.1 s) after dk6as6.aliasing.SETTLE_S (1.25 s, validated
    for the #102 stage): total alias energy relative to the UNFILTERED fundamental (lpf_aliasing.measure), each
    position against off. This checks the total, not every alias line (lines from several internal frequencies can
    fold onto one output bin). The settle is checked too: the 'other' energy (on no harmonic fold, e.g. the startup
    drift) must stay OTHER_BELOW_DB under the off total, so the comparison measures aliasing; with dk's 0.2 s settle
    the drift dominated, at -81.6 dB for every position."""
    r = lpf_aliasing.measure(fs, 8.0, f, window=0.1)
    assert r["settle_s"] == aliasing6.SETTLE_S
    res = {p: r[p]["abs_db_re_off_fund"] for p in lpf.POSITIONS}
    print(fs, f, {p: (round(v, 2), round(r[p]["abs_other_db"], 1)) for p, v in res.items()})
    for p in lpf.POSITIONS:
        assert r[p]["abs_other_db"] <= res["off"] - OTHER_BELOW_DB, (fs, f, p, r[p])
    for p in lpf.F0_HZ:
        assert res[p] <= res["off"] + 0.01, (fs, f, p, res)
