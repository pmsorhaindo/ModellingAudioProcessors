"""cpp/m1_spread.py (#143): the spread of repeated M1 benchmark runs, and the committed cpp/m1_timing records."""
import glob
import json
import os

import pytest

from cpp import m1_spread as S

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMING = os.path.join(ROOT, "m1_timing")


def _row(ns, ns_min, on=1.0, pcore=1.0, quality="hq", inst=2, path=None, cycles=1000):
    r = {"quality": quality, "os": 16, "variant": "P1_0.45mA", "level_v": 8.0, "block": 64, "instances": inst,
         "ns_per_sample": ns, "ns_min": ns_min, "pct_core": ns * 48000 / 1e9 * 100, "cpu_on_fraction": on,
         "p_core_fraction_min": pcore, "ghz_on_cpu": 3.2, "cycles_per_sample": cycles, "instr_per_sample": 2 * cycles}
    if path:
        r["path"] = path
    return r


def _rec(*rows):
    state = {"utc": "2026-11-27T08:00:00+00:00", "loadavg_1_5_15": [1.0, 1.0, 1.0], "pmset_therm": "",
             "lowpowermode": "0"}
    return {"state_before": state, "state_after": state, "results": list(rows)}


def test_spread_known_values():
    # 20 000 ns per sample is 96 % of one core at 48 kHz; 25 000 is 120 %
    s = S.spread([_rec(_row(20000, 19000)), _rec(_row(25000, 18000)), _rec(_row(21000, 20000))])
    (k, v), = s.items()
    assert k == ("hq", "P1_0.45mA", "x2", 8.0, 64)
    assert v["pct_runs"] == pytest.approx([96.0, 120.0, 100.8])
    assert v["pct_median"] == pytest.approx(100.8)
    assert v["spread"] == pytest.approx(25000 / 20000)
    assert v["best_pct"] == pytest.approx(18000 * 48000 / 1e9 * 100)      # the fastest pass of any run
    assert not v["clean"]                                                 # spread 1.25 > CLEAN_SPREAD


def test_clean_needs_every_run_on_cpu_on_p_cores_and_repeatable():
    ok = [_rec(_row(10000, 9900)), _rec(_row(10200, 10000))]
    assert next(iter(S.spread(ok).values()))["clean"]
    preempted = [_rec(_row(10000, 9900)), _rec(_row(10200, 10000, on=S.CLEAN_CPU_ON - 0.01))]
    assert not next(iter(S.spread(preempted).values()))["clean"]
    e_core = [_rec(_row(10000, 9900, pcore=S.CLEAN_P_CORE - 0.01)), _rec(_row(10200, 10000))]
    assert not next(iter(S.spread(e_core).values()))["clean"]


def test_modes_are_separate_configurations():
    # the 12AX7 StereoStage (s2) and two mono stages (x2) at the same settings are different rows
    rec = _rec(_row(1000, 900, path="stereo_simd"), _row(1500, 1400, path="mono"))
    assert {k[2] for k in S.spread([rec])} == {"s2", "x2"}


def test_runs_must_hold_the_same_configurations():
    with pytest.raises(ValueError):
        S.spread([_rec(_row(1000, 900)), _rec(_row(1000, 900, quality="standard"))])


def test_table_renders():
    t = S.table([_rec(_row(20000, 19000)), _rec(_row(20500, 19500))], ["r1", "r2"])
    assert "| hq P1_0.45mA x2 8 V b64 | 16× | 96.0 / 98.4 |" in t and "1 of 1 configurations clean" in t


@pytest.mark.parametrize("core,source,n", [("12ax7", "cpp/bench.cpp", 54), ("6as6", "cpp/bench_6as6.cpp", 30)])
def test_committed_m1_runs(core, source, n):
    """The #143 records: at least three runs per core, from a clean tree, the same configurations in every run."""
    files = sorted(glob.glob(os.path.join(TIMING, f"bench_{core}_r*.json")))
    assert len(files) >= 3, files
    recs = [json.load(open(f)) for f in files]
    for f, d in zip(files, recs):
        assert d["meta"]["reps"] >= 5 and not d["build"]["dirty"] and d["build"]["source"] == source, f
        assert d["host"]["cpu"] == "Apple M1", f
        assert len(d["results"]) == n and all(r["deterministic"] and r["nonconverged"] == 0 for r in d["results"]), f
    assert len(S.spread(recs)) == n


def test_quiet_clock_estimate_reproduces_the_quiet_record():
    """est. quiet % (cycles at QUIET_GHZ) against the measured median on the quiet 13 Nov record it was taken from."""
    rec = json.load(open(os.path.join(ROOT, "bench_apple_m1_simd_math.json")))
    assert rec["state_before"]["loadavg_1_5_15"][0] < 2.0
    for r in rec["results"]:
        assert abs(S.pct_at(r["cycles_per_sample"]) / r["pct_core"] - 1) < 0.02, r["name"]
