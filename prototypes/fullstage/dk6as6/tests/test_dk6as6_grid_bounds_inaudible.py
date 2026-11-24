"""#131 (bounds only): the published grid-current bounds under #113's inaudibility criterion, T 0.1-0.5 mA, 44.1 and
48 kHz (dk6as6/grid_bounds_inaudible.py), and the #128 burst / blocking / recovery / THD checks at 44.1 kHz
(dk6as6/grid_bounds_burst.py). The screen engine is checked live against the Python stage; the committed records
(hours to regenerate on a shared machine) are checked for their labels, provenance, completeness and what RESULTS.md
states from them. Estimated, unvalidated.
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_grid_bounds_inaudible.py
"""
import hashlib
import json
import os
import sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import triode  # noqa: E402 -- after the sys.path setup
from cpp import build as B  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode as P, inaudible as IN, validate as V  # noqa: E402 -- after the sys.path setup
from dk6as6 import grid_bounds_inaudible as G, grid_bounds_burst as GB  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# dk6as6/inaudible.py as on draft PR #146 (head 1c0a7f4): the criterion is the same file, byte for byte
PR146_INAUDIBLE_SHA256 = "ec60840272522a4016b6899f9d80cef0859ad25dba0499bd31533ea16a6e24ed"


@pytest.fixture(scope="module")
def rec():
    return json.load(open(G.OUT_JSON))


@pytest.fixture(scope="module")
def idx(rec):
    return G.index(rec["summary_cpp"])


# --- the criterion and the shapes ---------------------------------------------------------------------------------

def test_criterion_is_pr146s_byte_for_byte():
    assert hashlib.sha256(open(IN.__file__, "rb").read()).hexdigest() == PR146_INAUDIBLE_SHA256
    assert (IN.FLOOR_DBFS, IN.UNDER_THRESHOLD_DB, IN.SPL_FS_DB) == (-100.0, 10.0, 105.0)


def test_criterion_reads_a_known_line():
    """A full-scale 19037 Hz tone plus one exact line on its own bin is read at its level re the peak (to 0.01 dB;
    the line adds < 2e-5 to the peak), and its margin is the limit there minus that level: -125 dBFS at 3301 Hz
    (limit about -120 dBFS, the threshold term) passes; -95 dBFS at 15003 Hz (the -100 dBFS floor binds) fails."""
    from dk.aliasing import tone_bin
    fs, n = 48000, 48000
    m = tone_bin(19037, n, fs)
    t = np.arange(n) / fs
    for f, db, ok in ((3301.0, -125.0, True), (15003.0, -95.0, False)):
        y = np.sin(2 * np.pi * m * fs / n * t) + 10 ** (db / 20) * np.sin(2 * np.pi * f * t + 0.3)
        a = IN.assess(y, m, fs)
        assert a["worst_hz"] == f and a["passed"] is ok
        assert a["worst_dbfs"] == pytest.approx(db, abs=0.01)
        assert a["margin_db"] == pytest.approx(float(IN.limit_dbfs(f)) - a["worst_dbfs"], abs=1e-9)
    assert float(IN.limit_dbfs(15003.0)) == IN.FLOOR_DBFS and -121.0 < float(IN.limit_dbfs(3301.0)) < -119.0


def test_shapes_are_the_published_columns_and_rsd1_stays_nominal():
    assert G.SHAPES == ("RSD-1", "RSD-2", "EHX-1") and P.GRID_SHAPE == "RSD-1"
    assert P.GRID == P.grid(shape="RSD-1")              # the shipped default is unchanged
    for s, name in G.TABLE1.items():
        assert P.GRID_SHAPES[s] is getattr(triode, name)
    assert G.LABEL == "estimated, unvalidated"


# --- the C++ screen -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("shape", G.SHAPES)
def test_screen_patch_changes_only_what_it_says(shape):
    """The screening sources differ from main's in exactly the SCREEN_PATCH lines and the one inserted SCREEN_DZ
    line, which carries the shape's Table 1 column in TriodeParams order."""
    cpp = os.path.join(os.path.dirname(HERE), "cpp")
    src = G.screen_sources(shape)
    changed = 0
    for name, text in src.items():
        a = open(os.path.join(cpp, name)).read().splitlines()
        b = text.splitlines()
        if name == "dk6as6_core.h":
            ins = [ln for ln in b if ln.startswith("inline constexpr dkcore::TriodeParams SCREEN_DZ")]
            assert len(ins) == 1
            vals = [float(v) for v in ins[0].split("{")[1].split("}")[0].split(",")]
            p = P.GRID_SHAPES[shape]
            assert vals == [p[k] for k in ("G", "mu", "gamma", "C", "Gg", "xi", "Cg", "Ig0")]
            b.remove(ins[0])
        assert len(a) == len(b), name
        diff = [(x, y) for x, y in zip(a, b) if x != y]
        changed += len(diff)
        for x, y in diff:
            assert any(old.strip() in x and x.replace(old.strip(), new.strip()) == y for n, old, new in G.SCREEN_PATCH
                       if n == name), (name, x, y)
    assert changed == len(G.SCREEN_PATCH)


def _screen_or_skip(shape):
    if B.compiler() is None:
        if os.environ.get("CI"):
            pytest.fail("no C++ compiler on CI")
        pytest.skip("no C++ compiler (set CXX)")
    return G.screen_build(shape)


@pytest.mark.parametrize("shape", P.GRID_BOUNDS)
def test_screen_runs_the_bound_as_the_python_stage(shape):
    """The bound's screen binary and Stage(grid_shape=bound) on a short T 0.5 mA, 8 V, 19037 Hz tone at 4x, both
    rates: Rk to 1e-12, every output sample to 1e-8 of the peak (Newton's 1e-6 V tolerance; measured <= 9.1e-10), and
    both > 1e-6 away from the nominal shape's output, so the patch really swaps the shape."""
    exe = _screen_or_skip(shape)
    nom = _screen_or_skip("RSD-1")
    for fs in G.RATES:
        kw = dict(window=0.02, settle=0.03)
        _, yc, sc = G.run_cpp(shape, fs, 4, 0.5, 8.0, 19037, exe=exe, **kw)
        _, yp, sp = G.run_python(shape, fs, 4, 0.5, 8.0, 19037, **kw)
        _, yn, _ = G.run_cpp("RSD-1", fs, 4, 0.5, 8.0, 19037, exe=nom, **kw)
        pk = np.max(np.abs(yp))
        assert sc["rk"] == pytest.approx(sp["rk"], rel=1e-12)
        assert np.max(np.abs(yc - yp)) < 1e-8 * pk
        assert np.max(np.abs(yn - yp)) > 1e-6 * pk
        assert sc["nonconverged"] == sp["nonconverged"] == 0


# --- the committed records ----------------------------------------------------------------------------------------

def test_record_method_and_labels(rec):
    m = rec["method"]
    assert m["label"] == G.LABEL and m["fir"] == "k140" and m["mode"] == "T"
    assert m["floor_dbfs"] == IN.FLOOR_DBFS and m["spl_fs_db"] == IN.SPL_FS_DB
    assert m["grid_hz"] == list(G.GRID_HZ) and len(G.GRID_HZ) == 29
    for s in G.SHAPES:
        assert m["shapes"][s]["grid"] == P.grid(shape=s)
    for r in rec["records_cpp"]:
        assert r["shape"] in G.SHAPES and r["bias_ma"] in G.BIASES
        if "source" in r:                                # only the nominal records imported from PR #146
            assert r["shape"] == "RSD-1" and r["source"] == f"PR #146 {G.PR146_HEAD[:7]}"
    assert all(r["shape"] in P.GRID_BOUNDS for r in rec["records_python"])


def test_every_record_converged(rec):
    assert all(r["nonconverged"] == 0 for r in rec["records_cpp"] + rec["records_python"])


def test_python_confirms_the_screen(rec):
    """Every deciding point (confirm_points) re-run on the Python stage gives the C++ margin to 0.01 dB and the same
    worst product, and the verdict does not change."""
    want = G.confirm_points(rec["records_cpp"])
    got = {(tuple(q["key"]), q["tone"]): q for q in rec["parity"]}
    assert want
    for j in want:
        q = got[((j["shape"], j["fs"], j["os_"], j["lpf"], j["bias"], j["drive"]), j["tone"])]
        assert abs(q["diff_db"]) <= 0.01, q
        assert q["python_worst_hz"] == q["cpp_worst_hz"]
        assert (q["python_margin_db"] >= 0) == (q["cpp_margin_db"] >= 0)


def test_stages_are_decided(rec):
    """Every key the staged screen plans at 8 V (16x everywhere, 32x where 16x failed, 64x where 32x failed) is
    decided: all 29 tones pass, or a measured tone fails."""
    recs = rec["records_cpp"]
    for stage in ("16", "32", "64"):
        for k in G.stage_plan(stage, records=recs):
            assert G.key_status(recs, *k) is not None, (stage, k)


# What RESULTS.md states: the highest T bias (mA) that holds (every bias up to it passes, all tones, 8 V) per rate,
# factor and post-filter position, under every shape (RSD-1, RSD-2, EHX-1). Filled from the committed records.
HOLDS_UNDER_ALL = {
    (48000, 8, "off"): 0.3, (48000, 16, "off"): 0.35, (48000, 16, "4k"): 0.35, (48000, 32, "off"): 0.45,
    (48000, 32, "4k"): 0.4, (48000, 64, "off"): 0.5, (48000, 64, "4k"): 0.5,
    (44100, 8, "off"): 0.3, (44100, 16, "off"): 0.35, (44100, 16, "4k"): 0.3, (44100, 32, "off"): 0.45,
    (44100, 32, "4k"): 0.375, (44100, 64, "off"): 0.5, (44100, 64, "4k"): 0.5}
# ... and per shape where a bound holds less than the nominal (rate, factor, position): {shape: holds to}
NOMINAL_VS_BOUNDS = {(48000, 16, "off"): {"RSD-1": 0.375, "RSD-2": 0.35, "EHX-1": 0.375},
                     (48000, 32, "off"): {"RSD-1": 0.5, "RSD-2": 0.45, "EHX-1": 0.45},
                     (44100, 16, "4k"): {"RSD-1": 0.35, "RSD-2": 0.3, "EHX-1": 0.35},
                     (44100, 32, "4k"): {"RSD-1": 0.4, "RSD-2": 0.375, "EHX-1": 0.4}}


def test_region_under_every_bound_is_what_results_states(idx):
    for (fs, f, lp), want in HOLDS_UNDER_ALL.items():
        assert G.region(idx, fs, f, lp)[1] == want, (fs, f, lp)
    for (fs, f, lp), want in NOMINAL_VS_BOUNDS.items():
        assert G.region(idx, fs, f, lp)[0] == want, (fs, f, lp)
    # the whole supported T range holds under every bound only at 64x
    assert all(v == 0.5 for (fs, f, lp), v in HOLDS_UNDER_ALL.items() if f == 64)
    assert all(v < 0.5 for (fs, f, lp), v in HOLDS_UNDER_ALL.items() if f < 64)


def test_bounds_do_not_move_t_below_0p35(idx):
    """At 0.1-0.3 mA the grid does not conduct enough to matter: every bound reads the nominal's margin to 1e-3 dB
    wherever both are measured in full (8 V)."""
    n = 0
    for (sh, fs, f, lp, b, d), x in idx.items():
        if sh != "RSD-1" and b <= 0.3 and G.complete(x):
            y = idx[("RSD-1", fs, f, lp, b, d)]
            assert abs(x["worst"]["margin_db"] - y["worst"]["margin_db"]) < 1e-3
            n += 1
    assert n >= 24


# --- burst / blocking / recovery / THD at 44.1 kHz ---------------------------------------------------------------

def test_burst_records_at_44100(tmp_path):
    rec = json.load(open(GB.JSON))
    assert rec["label"] == G.LABEL and rec["fs_base"] == 44100.0 and rec["mode"] == "T"
    assert set(rec["results"]) == set(G.SHAPES)
    for s, sb in rec["results"].items():
        assert set(sb) == {f"{b:g}" for b in GB.BIASES}
        for b, rb in sb.items():
            for q, r in rb.items():
                assert r["fs_base"] == 44100.0 and r["grid"] == R.grid_of(GB.MODELS[s]) and r["shape"] == s
                assert V.status(r) == "passed", (s, b, q, r["passed"])


def test_bound_references_cover_0p1_to_0p5():
    """Each bound has its own same-model ngspice reference at every nominal T grid reference current."""
    assert R.BOUND_BIASES["T"] == R.GRID_BIASES["T"]
    assert GB.BIASES == tuple(b for b in R.GRID_BIASES["T"] if b <= 0.5)
    for m in R.GRID_SHAPE:
        for b in R.BOUND_BIASES["T"]:
            ref = V.ref(m, b, "T")
            assert ref["generator"]["grid"] == R.grid_of(m) and ref["generator"]["ngspice"] == "ngspice-41"
