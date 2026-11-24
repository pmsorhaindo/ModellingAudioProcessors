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
        assert r["shape"] in G.SHAPES and r["bias_ma"] in G.BIASES + G.FINE_BIASES
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
    """Every key the staged screen plans is decided (all 29 tones pass, or a measured tone fails), including PR #151
    review's "fill" (every 32x / 64x key of the decision table), "fine" (16x at 0.31-0.34 mA) and "2v4k"."""
    recs = rec["records_cpp"]
    for stage in ("16", "32", "64", "8", "2v", "fill", "fine", "2v4k"):
        for k in G.stage_plan(stage, records=recs):
            assert G.key_status(recs, *k) is not None, (stage, k)


def test_decision_table_is_measured_not_inferred(idx):
    """PR #151 review: every cell of the decision table (16x / 32x / 64x, filter off and 4k, both rates, every shape
    and bias, 8 V) is a measured key, and passes() never credits a factor it was not measured at."""
    for sh in G.SHAPES:
        for fs in G.RATES:
            for f in (16, 32, 64):
                for lp in ("off", "4k"):
                    for b in G.BIASES:
                        x = idx.get((sh, fs, f, lp, b, 8.0))
                        assert x is not None and (G.complete(x) or not x["passed"]), (sh, fs, f, lp, b)
    assert G.passes({}, "RSD-2", 44100, 64, 8.0, "off", 0.1) is None
    assert not hasattr(G, "INFER_MARGIN_DB")


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


def test_bounds_do_not_move_t_at_the_sampled_0p1_to_0p3(idx):
    """At the sampled 0.1 / 0.25 / 0.3 mA (only those; see the counterexamples below 0.35 mA) every bound reads the
    nominal's margin to 1e-3 dB wherever both are measured in full (8 V)."""
    n = 0
    for (sh, fs, f, lp, b, d), x in idx.items():
        if sh != "RSD-1" and b in (0.1, 0.25, 0.3) and G.complete(x):
            y = idx[("RSD-1", fs, f, lp, b, d)]
            assert abs(x["worst"]["margin_db"] - y["worst"]["margin_db"]) < 1e-3
            n += 1
    assert n >= 24


def test_counterexamples_below_0p35_are_recorded(rec):
    """PR #151 review: just below the 0.35 mA grid point (0.349 mA, off-grid 19693 Hz; 0.3499 mA, grid 19637 Hz;
    44.1 kHz, 16x, 4k, 8 V) RSD-2 fails where the nominal passes, on both engines, which agree to 0.01 dB."""
    ce = rec["counterexamples"]
    assert len(ce) == 2 * len(G.COUNTEREXAMPLES)
    by = {(r["engine"], r["shape"], r["bias_ma"], r["tone"]): r for r in ce}
    for b, t in ((0.349, 19693), (0.3499, 19637)):
        for e in ("cpp", "python"):
            assert by[(e, "RSD-1", b, t)]["passed"] and not by[(e, "RSD-2", b, t)]["passed"], (e, b, t)
        for s in ("RSD-1", "RSD-2"):
            assert abs(by[("cpp", s, b, t)]["margin_db"] - by[("python", s, b, t)]["margin_db"]) <= 0.01


def test_fine_biases_locate_where_the_bounds_start_to_move(idx):
    """16x at 0.31-0.34 mA is measured for every shape, rate and position (stage "fine"), and every one passes, so on
    the refined grid 16x holds T to 0.34 mA under every shape at 44.1 kHz with 4k (RSD-2 fails 0.35 mA), and to
    0.35 mA elsewhere; the bounds move these margins by at most 2.7 dB (48 kHz, 4k, 0.34 mA)."""
    grid = tuple(sorted(G.BIASES + G.FINE_BIASES))
    want = {(48000, "off"): 0.35, (48000, "4k"): 0.35, (44100, "off"): 0.35, (44100, "4k"): 0.34}
    for (fs, lp), w in want.items():
        assert min(G.holds_to(idx, s, fs, 16, 8.0, lp, grid) for s in G.SHAPES) == w, (fs, lp)
    m = lambda s, fs, lp, b: idx[(s, fs, 16, lp, b, 8.0)]["worst"]["margin_db"]  # noqa: E731
    moves = [abs(m(s, fs, lp, b) - m("RSD-1", fs, lp, b))
             for s in P.GRID_BOUNDS for fs in G.RATES for lp in ("off", "4k") for b in G.FINE_BIASES]
    assert max(moves) < 2.7
    for sh in G.SHAPES:
        for fs in G.RATES:
            for lp in ("off", "4k"):
                for b in G.FINE_BIASES:
                    x = idx.get((sh, fs, 16, lp, b, 8.0))
                    assert x is not None and (G.complete(x) or not x["passed"]), (sh, fs, lp, b)


def test_every_2v_case_passes_and_the_stated_minimum(idx):
    """Every recorded 2 V key passes (all tones). The smallest 2 V margin is with the 4k filter (RSD-2, 44.1 kHz, 16x,
    0.5 mA, stage "2v4k"); RESULTS.md and the PR body quote it, not the bounds' filter-off minimum (PR #151 review)."""
    two = {k: x for k, x in idx.items() if k[5] == 2.0}
    assert two and all(G.complete(x) and x["passed"] for x in two.values())
    k, x = min(two.items(), key=lambda kv: kv[1]["worst"]["margin_db"])
    assert x["worst"]["margin_db"] == pytest.approx(TWO_V_MIN_DB, abs=0.01), k
    assert k[3] == "4k"


TWO_V_MIN_DB = 29.72      # dB, from the committed records (below the review's +32.24 dB, the nominal 4k at 0.45 mA)


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


# --- the 8 V burst (PR #151 review: the issue's checks "at 8 V") --------------------------------------------------
# Cases whose 8 V ngspice reference was still running when this was committed (EHX-1 0.35 mA: its 4 V burst took
# 3 h 52 min; the 8 V one is past 4 h): recorded DK only, "reference_pending", never a pass. Remove when it lands.
PENDING8 = {("dualg_ehx1", 0.35)}

@pytest.mark.parametrize("model", ["dualg", *R.GRID_SHAPE])
def test_burst8_references(model):
    """Each shape's 8 V burst reference (run_refs.run_burst) at every T bias of GB.BIASES: the 8 V burst of the same
    netlist and solver, generated with the shape's grid law, and its op point is the main reference's to 1e-9."""
    for b in GB.BIASES:
        if (model, b) in PENDING8 and not os.path.exists(R.burst_ref_file(model, b, "T", 8.0)):
            continue
        ref = json.load(open(R.burst_ref_file(model, b, "T", 8.0)))
        g = ref["generator"]
        assert ref["burst_amp_v"] == 8.0 and g["burst"]["amp"] == 8.0 and g["ngspice"] == "ngspice-41"
        # Rk to 1e-12: the nominal T references were solved in an earlier run (last-ulp differences, 2e-15)
        assert g["grid"] == R.grid_of(model) and g["rk"] == pytest.approx(V.ref(model, b, "T")["generator"]["rk"],
                                                                          rel=1e-12)
        main = V.ref(model, b, "T")["op"]
        for k in ("ia", "ig2", "ik", "vgk"):
            assert ref["op"][k] == pytest.approx(main[k], rel=1e-9), (model, b, k)


def test_burst8_records_at_both_rates():
    """The 8 V burst record: both rates, every shape and bias, both qualities, each run at 8 V against its own 8 V
    reference, and every criterion (op, burst shift / trajectory / tail tau, Newton) passes or reads None only where
    ngspice's exp() cap makes the reference a different law (validate.py)."""
    rec = json.load(open(GB.BURST8_JSON))
    assert rec["label"] == G.LABEL and rec["burst_amp_v"] == 8.0 and rec["mode"] == "T"
    assert set(rec["results"]) == {"48000", "44100"}
    for fs, d in rec["results"].items():
        assert set(d) == set(G.SHAPES)
        for s, sb in d.items():
            assert set(sb) == {f"{b:g}" for b in GB.BIASES}
            for b, rb in sb.items():
                assert set(rb) == {"standard", "hq"}
                for q, r in rb.items():
                    assert r["burst_amp_v"] == 8.0 and r["fs_base"] == float(fs) and r["shape"] == s
                    assert r["grid"] == R.grid_of(GB.MODELS[s]) and r["quality"] == q
                    if r.get("reference_pending"):
                        assert (GB.MODELS[s], float(b)) in PENDING8, (fs, s, b, q)
                        assert r["vs"] is None and V.status(r) == "inconclusive" and r["passed"]["newton"] is True
                        continue
                    capped = r["vs"].get("spice_exp_capped", False)
                    assert r["passed"]["op"] is True and r["passed"]["newton"] is True
                    assert r["passed"]["burst"] is (None if capped else True), (fs, s, b, q, r["vs"])
