"""The #131 bounds on the borrowed g1 grid-current law: DZ Table 1's RSD-2 and EHX-1 columns, selectable as
pentode.grid(shape=...) / Stage(grid_shape=...) without touching the nominal (RSD-1, #112) path, which stays bit for
bit what main computes. The committed bound records (grid_bounds.json, results_grid_{rsd2,ehx1}_T.json) are checked
for their labels and what RESULTS.md claims from them; they take hours to regenerate (dk6as6/grid_bounds.py).
Run from prototypes/fullstage:  python -m pytest -q dk6as6/tests/test_dk6as6_grid_bounds.py
"""
import itertools
import json
import os
import sys
import warnings
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from dk import triode  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode as P, stage as stg, grid as GR, validate as V, aliasing as A6  # noqa: E402
from dk6as6 import grid_bounds as GB  # noqa: E402 -- after the sys.path setup
from dk6as6 import aliasing_guard as A6G  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage, UnsupportedSettingError, ExperimentalWarning  # noqa: E402
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

FS = 48000.0
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# main's nominal grid parameters (#112, commit 6a2e4a7), written out: the nominal path must not move
MAIN_GRID = {"GG": 0.00079115817626667, "XI": 1.314, "CG": 9.901, "hardness": 1.0}


# --- the parameters ----------------------------------------------------------------------------------------------

def test_table1_columns_are_the_papers():
    """dk/triode.py's three DZ Table 1 columns, as printed in the paper (DAFx-11, p. 5, Table 1: RSD-1, RSD-2, EHX-1;
    rows G, mu, gamma, C, Gg, xi, Cg, Ig0)."""
    assert triode.DZ_12AX7_RSD1 == dict(G=2.242e-3, mu=103.2, gamma=1.26, C=3.40, Gg=6.177e-4, xi=1.314, Cg=9.901,
                                        Ig0=8.025e-8)
    assert triode.DZ_12AX7_RSD2 == dict(G=2.173e-3, mu=100.2, gamma=1.28, C=3.19, Gg=5.911e-4, xi=1.358, Cg=11.76,
                                        Ig0=4.527e-8)
    assert triode.DZ_12AX7_EHX1 == dict(G=1.371e-3, mu=86.9, gamma=1.349, C=4.56, Gg=3.263e-4, xi=1.156, Cg=11.99,
                                        Ig0=3.917e-8)
    assert P.GRID_SHAPES == {"RSD-1": triode.DZ_12AX7_RSD1, "RSD-2": triode.DZ_12AX7_RSD2,
                             "EHX-1": triode.DZ_12AX7_EHX1}
    assert P.GRID_SHAPE == "RSD-1" and P.GRID_BOUNDS == ("RSD-2", "EHX-1")


def test_nominal_grid_is_mains():
    """The default and the explicit nominal shape give #112's dict exactly (no new key), so every nominal record and
    reference generator stays what it was."""
    assert P.GRID == P.grid() == P.grid(shape="RSD-1") == MAIN_GRID
    assert P.grid(0.5, shape="RSD-1") == P.grid(0.5)
    for fn in ("results_grid_T.json", "grid.json"):
        rec = json.load(open(os.path.join(HERE, fn)))
        g = rec["0.5"]["standard"]["grid"] if fn.startswith("results") else rec["grid"]
        assert g == MAIN_GRID, fn


@pytest.mark.parametrize("shape", P.GRID_BOUNDS)
def test_bounds_are_scaled_as_the_nominal(shape):
    """A bound takes XI, CG from its column and Gg = hardness x (2 / KG1) / G x Gg of that column: the #112 rule."""
    dz = P.GRID_SHAPES[shape]
    for h in (0.5, 1.0):
        g = P.grid(h, shape=shape)
        assert g["GG"] == h * ((2.0 / P.DUAL_CONTROL["KG1"]) / dz["G"]) * dz["Gg"]
        assert (g["XI"], g["CG"], g["hardness"], g["shape"]) == (dz["xi"], dz["Cg"], h, shape)
    with pytest.raises(ValueError):
        P.grid(shape="RSD-3")


def test_bound_grid_current_values():
    """Ig1 of the three shapes (A) at a few Vg1k: both bounds have a sharper onset (Cg ~12 /V against 9.9), so less
    current below 0 V; at +0.5 V all three are within 5 %; at +2 V EHX-1 is 23 % lower (RESULTS.md table)."""
    want = {"RSD-1": (87.05e-12, 24.03e-6, 0.3188e-3, 1.967e-3),
            "RSD-2": (3.187e-12, 16.71e-6, 0.3049e-3, 2.002e-3),
            "EHX-1": (36.99e-12, 25.33e-6, 0.3068e-3, 1.523e-3)}
    for s, w in want.items():
        g = P.grid(shape=s)
        for v, x in zip((-1.0, 0.0, 0.5, 2.0), w):
            assert P.grid_current(v, g)[0] == pytest.approx(x, rel=2e-3), (s, v)


GRID_G1 = list(itertools.product([-8.0, -1.0, -0.3, 0.0, 0.3, 2.0], [0.5, 60.0, 140.0], [-3.3, 0.0, 4.0],
                                 [0.3, 20.0, 181.7]))


@pytest.mark.parametrize("shape", P.GRID_BOUNDS)
def test_bound_currents_match_the_reference_and_its_jacobian(shape):
    """currents_grid with a bound shape against reference_grid (1e-12 of the magnitudes, as test_pentode) and its
    Jacobian against central differences: the bound runs the #112 law, only its constants differ."""
    g = P.grid(shape=shape)
    for v in GRID_G1:
        ip, ig2, ig1, J = P.currents_grid(*v, P.DUAL_CONTROL, g)
        rp, rg2, rg1 = P.reference_grid(*v, P.DUAL_CONTROL, g)
        ip0, ig20, _ = P.currents(*v)
        f = ip0 / (ip0 + ig20) if ip0 + ig20 > 0.0 else 1.0
        for a, b, scale in ((ip, rp, abs(ip0) + f * ig1), (ig2, rg2, abs(ig20) + (1.0 - f) * ig1), (ig1, rg1, ig1)):
            assert abs(a - b) <= 1e-12 * scale + 1e-30, (v, a, b)
        scale = max(abs(x) for row in J for x in row)
        for j in range(4):
            h = 1e-5 * max(1.0, abs(v[j]))
            vp, vm = list(v), list(v)
            vp[j] += h
            vm[j] -= h
            fp, fm = P.reference_grid(*vp, P.DUAL_CONTROL, g), P.reference_grid(*vm, P.DUAL_CONTROL, g)
            for k in range(3):
                d = (fp[k] - fm[k]) / (2 * h)
                assert abs(J[k][j] - d) <= 1e-5 * (scale + abs(d)), (v, k, j)


# --- the stage: bounds selectable, nominal untouched -------------------------------------------------------------

def test_bounds_have_no_supported_envelope():
    """A bound is experimental only, in every mode; the nominal envelope is unchanged."""
    assert stg.envelope() == stg.envelope(1.0, "RSD-1") == stg.SUPPORTED_MA == {"P1": (0.1, 0.6), "T": (0.1, 0.5)}
    assert stg.envelope(0.0, "EHX-1") == stg.SUPPORTED_MA_NO_GRID      # no grid current: the shape is moot
    for s in P.GRID_BOUNDS:
        assert stg.envelope(1.0, s) == {}
        assert not stg.supported("P1", 0.45, grid_shape=s) and not stg.supported("T", 0.25, grid_shape=s)
        with pytest.raises(UnsupportedSettingError, match=f"grid shape {s} is a #131 bound"):
            Stage(FS, bias=0.45, grid_shape=s)
        with pytest.warns(ExperimentalWarning, match="#131 bound"):
            st = Stage(FS, bias=0.45, mode="T", grid_shape=s, experimental=True)
        assert st.grid == P.grid(shape=s) and st.grid_shape == s and not st.supported
    with pytest.raises(ValueError, match="unknown grid shape"):
        Stage(FS, grid_shape="12AX7")
    with pytest.raises(ValueError, match="unknown grid shape"):
        Stage(FS, grid_shape="12AX7", grid_hardness=0.0)


def _onset(shape=None, bias=0.5, f0=18037.0, quality="standard"):
    kw = {} if shape is None else dict(grid_shape=shape)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        st = Stage(FS, bias=bias, mode="T", quality=quality, experimental=True, fir=GR.FIR, **kw)
        t = np.arange(int(GR.ONSET_S * FS) + st.latency_samples) / FS
        return st, st.process(8.0 * np.sin(2 * np.pi * f0 * t), record=("out", "vgk", "vak", "ig1", "ipk"))


def test_nominal_stage_is_bit_identical_to_main():
    """The default grid shape and an explicit grid_shape="RSD-1" give the same samples bit for bit, and both reproduce
    the committed records: T 0.5 mA's 8 V 18037 Hz onset (grid.json, #112, with the shipped FIR it was made with,
    grid.FIR) and T 0.5 mA's burst trajectory and Rk (results_grid_T.json, standard, #134's k140 default). The
    records were made on macOS arm64, where they reproduce exactly; other platforms' numpy (CI's Linux) differ in
    the last ulps, so the onset is compared to 1e-12 relative (Ip + Ig2, = Ik - Ig1, a difference, to 1e-14 A, ~1e-11
    of the cathode current); the 120 ms burst, where those ulps pass through ~46k Newton solves (TOL_V 1e-6 V), to
    1e-8 V (Linux: 1.2e-10 V); Rk, solved to 1e-13 in log10 (~2e-13 relative), to 1e-11."""
    a, ra = _onset()
    b, rb = _onset("RSD-1")
    assert a.rk == b.rk and all(np.array_equal(ra[k], rb[k]) for k in ra if k != "_internal")
    assert all(np.array_equal(ra["_internal"][k], rb["_internal"][k]) for k in ra["_internal"])
    i = ra["_internal"]
    row = next(r for r in json.load(open(GR.JSON))["envelope"]["onsets"]
               if (r["mode"], r["bias_ma"], r["f_hz"], r["quality"]) == ("T", 0.5, 18037.0, "standard"))
    assert (float(i["vgk"].max()), float(i["vak"].min()), float(i["ig1"].max())) == pytest.approx(
        (row["vg1k_max"], row["vak_min"], row["ig1_max"]), rel=1e-12)
    assert float(i["ipk"].min()) == pytest.approx(row["ipk_min"], rel=0, abs=1e-14)
    assert a.iteration_stats()["max"] == row["it_max"]
    rec = json.load(open(V.results_file("T", "dualg")))["0.5"]["standard"]
    m, _ = V.burst(model="dualg", bias=0.5, mode="T")
    assert m["traj_vgk"] == pytest.approx(rec["burst"]["traj_vgk"], rel=0, abs=1e-8)
    assert m["min_vgk"] == pytest.approx(rec["burst"]["min_vgk"], rel=0, abs=1e-8)
    assert V.stage("dualg", bias=0.5, mode="T").rk == pytest.approx(rec["rk"], rel=1e-11)


@pytest.mark.parametrize("shape", P.GRID_BOUNDS)
def test_a_bound_changes_only_the_grid_current(shape):
    """The same onset with a bound: the stage really runs the bound's law (Ig1 differs where g1 nears the cathode),
    while Rk at 0.5 mA (Ig1 < 1e-16 A at rest) is the nominal one to 1e-9."""
    a, ra = _onset()
    b, rb = _onset(shape)
    assert b.rk == pytest.approx(a.rk, rel=1e-9)
    assert not np.array_equal(ra["_internal"]["ig1"], rb["_internal"]["ig1"])
    assert rb["_internal"]["ipk"].min() > 0.0 and rb["_internal"]["vak"].min() > 0.0 and b.nonconv == 0


def test_aliasing_record_names_a_bound_only():
    """dk6as6.aliasing.measure's record is #112's for the nominal shape (no new key) and names a bound."""
    kw = dict(bias=0.5, mode="T", window=0.01, settle=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nom, expl, bnd = (A6.measure(8.0, 19037, **kw), A6.measure(8.0, 19037, grid_shape="RSD-1", **kw),
                          A6.measure(8.0, 19037, grid_shape="EHX-1", **kw))
    assert "grid_shape" not in nom and nom == expl and bnd["grid_shape"] == "EHX-1"


# --- ngspice side -------------------------------------------------------------------------------------------------

def test_run_refs_bound_models():
    """The bound models run the bound law (generator grid, Rk) at T only; the nominal models are unchanged."""
    assert R.GRID_SHAPE == {"dualg_rsd2": "RSD-2", "dualg_ehx1": "EHX-1"}
    assert R.shape("dualg") == "RSD-1" and R.grid_of("dualg") == MAIN_GRID
    for m, s in R.GRID_SHAPE.items():
        assert R.grid_of(m) == P.grid(shape=s) and R.biases_of(m, "T") == R.BOUND_BIASES["T"]
        assert R.biases_of(m, "P1") == () and (m, "T") in R.SUBCKTS and (m, "P1") not in R.SUBCKTS
        assert f"{s} (Table 1)" in R.subckt(m, mode="T") and "#131 BOUND" in R.subckt(m, mode="T")
    assert "#131" not in R.subckt("dualg", mode="T")


# --- the committed bound records ----------------------------------------------------------------------------------

def _bounds():
    return json.load(open(GB.JSON))


def test_bound_records_are_labelled_and_complete():
    """Every bound record says "estimated, unvalidated", names its source column and is the bound law."""
    d = _bounds()
    assert d["label"] == GB.LABEL == "estimated, unvalidated"
    for part in ("law", "envelope", "aliasing"):
        assert set(d[part]) == set(P.GRID_BOUNDS), part
        for s, r in d[part].items():
            assert r["label"] == GB.LABEL and r["shape"] == s
    for s, r in d["law"].items():
        assert r["grid"] == P.grid(shape=s) and r["table1"] == P.GRID_SHAPES[s] and f"column {s}" in r["source"]
    for s, r in d["envelope"].items():
        assert all(x["grid_shape"] == s for k in ("tones", "onsets", "hf", "stimulus_class") for x in r[k])
    for s, r in d["aliasing"].items():
        assert all(x["grid_shape"] == s for k in ("sweep", "guard") for x in r[k])
        jobs, guard = GB.alias_jobs(s)
        sweep = lambda rows: sorted((x["bias_ma"], x["drive"], x["tone"]) for x in rows)       # noqa: E731
        keyed = lambda rows, k: sorted((x[k], x["setting_key"], x["tone"]) for x in rows)      # noqa: E731
        assert sweep(r["sweep"]) == sweep([dict(j, bias_ma=j["bias"]) for j in jobs])
        assert keyed(r["guard"], "fs_base") == keyed(guard, "fs")


def test_bound_study_keeps_its_shipped_fir_configuration():
    """#131a was measured with the shipped FIR against #113's guard (PR #133 review round 3): its jobs name that FIR
    and #113's keys, the nominal values it is compared with are those keys' and tones', and the report fills every
    guard cell; the live guard (k140, #134) is a different configuration."""
    assert GB.FIR == GR.FIR == "shipped"
    for s in P.GRID_BOUNDS:
        jobs, guard = GB.alias_jobs(s)
        assert all(j["fir"] == "shipped" for j in jobs + guard)
        assert all("/fir/" in j["setting_key"] for j in guard)
    nom = json.load(open(GB.NOMINAL_GUARD_JSON))
    assert nom["fir"] == "shipped"
    assert {int(fs): {k: sorted(map(int, v["tones"])) for k, v in vals.items()} for fs, vals in nom["values"].items()} \
        == {fs: {GB.guard_key(*k): sorted(t) for k, (_, t) in keys.items()} for fs, keys in GB.GUARD_T_KEYS.items()}
    assert {GB.guard_key(*k) for k in GB.GUARD_T_KEYS[48000]}.isdisjoint(A6G.limits_by_key(48000))
    rows = [ln for ln in GB.tables().splitlines() if "kHz, T/" in ln]
    assert len(rows) == sum(map(len, GB.GUARD_T_KEYS.values())) and not any("—" in ln for ln in rows), rows
    j = dict(next(j for j in GB.alias_jobs("RSD-2")[1] if j["fs"] == 44100), window=0.01, settle=0.0)
    kw = {k: v for k, v in j.items() if k not in ("drive", "tone", "setting_key", "fir")}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        got = GB._alias({k: v for k, v in j.items() if k != "setting_key"})       # as GB.aliasing calls it
        assert got["total_dbc"] == A6.measure(j["drive"], j["tone"], fir="shipped", **kw)["total_dbc"]
        assert got["total_dbc"] != A6.measure(j["drive"], j["tone"], fir="k140", **kw)["total_dbc"]


@pytest.mark.parametrize("model", list(R.GRID_SHAPE))
def test_bound_validation_against_its_own_references(model):
    """DK with each bound against that bound's same-model ngspice reference (validate.py --bounds), T at
    BOUND_BIASES, standard and hq: every criterion passes (a method check of the bound law; experimental settings)."""
    res = json.load(open(V.results_file("T", model)))
    assert set(res) == {f"{b:g}" for b in R.BOUND_BIASES["T"]}
    for b, rb in res.items():
        for q in ("standard", "hq"):
            r = rb[q]
            assert r["grid"] == R.grid_of(model) and r.get("supported") is False
            assert V.status(r) == "passed", (b, q, r["passed"])
            ref = V.ref(model, float(b), "T")
            assert ref["generator"]["grid"] == R.grid_of(model)


@pytest.mark.parametrize("model", list(R.GRID_SHAPE))
def test_live_validator_runs_each_bound_against_its_own_reference(model):
    """PR #139 review: the live validator, not the stored flags. validate.stage(model) builds the bound's shape, and
    its operating point (validate.op_record, what validate.run compares) at T 0.6 mA, where g1 rests at -0.95 V and the
    shapes' Ig1 differ by up to 23x, matches that bound's own ngspice reference (Ig1 to 1e-6, every OP_KEYS entry to
    1e-7) and no other shape's (Ig1 off by > 50 %); its small signal passes CRIT against it too."""
    st = V.stage(model, bias=0.6, mode="T")
    assert st.grid_shape == R.shape(model) and st.grid == R.grid_of(model)
    op = V.op_record(st)
    ref = V.ref(model, 0.6, "T")
    assert op["ig1"] == pytest.approx(ref["op"]["ig1"], rel=1e-6)
    assert max(abs(op[k] / ref["op"][k] - 1) for k in V.OP_KEYS) < 1e-7
    for other in ("dualg", *R.GRID_SHAPE):
        if other != model:
            assert abs(op["ig1"] / V.ref(other, 0.6, "T")["op"]["ig1"] - 1) > 0.5, other
    ac = V.ac_dev(st, ref)
    assert ac["ac_maxdev_db_stage"] < V.CRIT["ac_db"] and ac["ac_maxdev_db_chain"] < V.CRIT["ac_db"]


# --- the T-only entry points (PR #139 review) ------------------------------------------------------------------

@pytest.mark.parametrize("model", list(R.GRID_SHAPE))
def test_bound_models_refuse_every_mode_but_t(model, tmp_path, monkeypatch):
    """A bound has subckts and references only in T: run_refs.cases rejects an explicit other mode (with or without a
    bias), run() and netlist() raise before writing a netlist or starting ngspice, and validate.main raises before
    writing a results file. A bare bound model (the default "every model" run) is its empty P1 list."""
    for arg in (f"{model}/P1:0.5", f"{model}/P2:0.5", f"{model}/P2", f"{model}/P1"):
        with pytest.raises(ValueError, match="#131 grid-current bound"):
            R.cases([arg])
    assert R.cases([model]) == [] and R.cases([f"{model}/T"]) == [(model, "T", b) for b in R.BOUND_BIASES["T"]]
    assert R.cases([f"{model}/T:0.55"]) == [(model, "T", 0.55)]
    monkeypatch.setattr(R, "ngspice", lambda *a, **k: pytest.fail("ngspice started"))
    for mode in ("P1", "P2"):
        with pytest.raises(ValueError, match="#131 grid-current bound"):
            R.run(model, 0.5, mode=mode, out=str(tmp_path / "out"), write=False)
        with pytest.raises(ValueError, match="#131 grid-current bound"):
            R.netlist(model, "main", 0.5, mode=mode)
    assert not (tmp_path / "out").exists()
    fn = tmp_path / "results.json"
    monkeypatch.setattr(V, "results_file", lambda mode, model: str(fn))
    for mode in ("P1", "P2"):
        with pytest.raises(ValueError, match="#131 grid-current bound"):
            V.main(mode, model)
    assert not fn.exists()


def test_validate_cli_refuses_a_bound_mode_but_t():
    """`python -m dk6as6.validate --bounds P1` is a usage error (exit 2), before any stage is built."""
    import subprocess
    p = subprocess.run([sys.executable, "-m", "dk6as6.validate", "--bounds", "P1"], cwd=os.path.dirname(HERE),
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 2 and "--bounds runs only ['T']" in p.stderr


# --- the study's output file (PR #139 review) ------------------------------------------------------------------

def _publish(a):
    fn, part, shape = a
    GB.publish(part, shape, dict(shape=shape, part=part, pad=list(range(20000))), fn)


def test_concurrent_publishers_keep_every_update(tmp_path):
    """Sixteen processes publishing different parts / shapes into one file at once: every update survives (the read /
    merge / write is locked) and the file is whole JSON afterwards (it is replaced atomically, never truncated)."""
    from concurrent.futures import ProcessPoolExecutor
    fn = str(tmp_path / "bounds.json")
    jobs = [(fn, f"part{k}", s) for k in range(8) for s in GB.SHAPES]
    with ProcessPoolExecutor(8) as ex:
        list(ex.map(_publish, jobs))
    d = json.load(open(fn))
    assert d["label"] == GB.LABEL
    assert {(p, s) for p, v in d.items() if p != "label" for s in v} == {(p, s) for _, p, s in jobs}
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_shapes_cross_and_onsets_depend_on_the_threshold():
    """PR #139 review: the shapes are not ordered. EHX-1 crosses above RSD-1 at -66.5 mV and back below at +383 mV;
    RSD-2 stays below it to +1.34 V. Onset (Vg1k at a stated Ig1): EHX-1 is earlier than RSD-1 at 20 uA (-22.9 vs
    -19.2 mV) but later at 1 and 10 uA. The committed "law" records carry the same figures (to 1e-11 V: they are
    bisected to xtol 1e-12 V, and other platforms' libm moves the root in the last ulps)."""
    nom = P.GRID
    assert [round(1e3 * v, 1) for v in GB.crossings(P.grid(shape="EHX-1"), nom)] == [-66.5, 383.3]
    assert [round(1e3 * v, 1) for v in GB.crossings(P.grid(shape="RSD-2"), nom)] == [1336.4]
    on = {s: {i: round(1e3 * GB.onset_v(P.grid(shape=s), i), 1) for i in GB.ONSET_A} for s in P.GRID_SHAPES}
    assert on == {"RSD-1": {1e-6: -278.3, 1e-5: -85.9, 2e-5: -19.2}, "RSD-2": {1e-6: -203.8, 1e-5: -42.3, 2e-5: 15.9},
                  "EHX-1": {1e-6: -262.0, 1e-5: -84.3, 2e-5: -22.9}}
    law = _bounds()["law"]
    for s in P.GRID_BOUNDS:
        assert law[s]["crossings_with_nominal_v"] == pytest.approx(GB.crossings(P.grid(shape=s), nom), rel=0, abs=1e-11)
        assert law[s]["onset_v"] == pytest.approx({f"{i:g}": GB.onset_v(P.grid(shape=s), i) for i in GB.ONSET_A},
                                                  rel=0, abs=1e-11)
