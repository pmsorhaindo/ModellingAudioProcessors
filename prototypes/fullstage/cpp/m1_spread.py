"""Spread of repeated M1 runs of the #20 benchmark (#143): the v0.1 timing of the 12AX7 and 6AS6 stages.

`cpp/bench.py` writes one record per run. On a shared Mac, one run is not a clean timing (cpp/BENCHMARK.md, "Caveats"),
so #143 repeats the run and reports the spread. This reads the repeated records of each core and prints, per
configuration, the median % of one core in each run, the spread of those medians, the best single pass over all runs,
and whether every run of it was clean by the test below. Standard library only. Run from prototypes/fullstage:

  python -m cpp.m1_spread cpp/m1_timing/bench_12ax7_r*.json
  python -m cpp.m1_spread cpp/m1_timing/bench_6as6_r*.json

The figures (cpp/BENCHMARK.md, "Reported per configuration"):
  % core      ns_per_sample x 48 000 / 1e9 x 100: wall time per pass at 48 kHz real time, median over the run's passes.
              Preemption inflates it, so on a loaded machine it bounds the clean cost from above.
  best pass   the same from ns_min, the least-preempted pass of any run: the closest to a clean figure that the
              runs contain, still an upper bound.
  est. quiet  cycles_per_sample (median over runs) x 48 000 / QUIET_GHZ: the % of one core the counted cycles would
              take at the P-core clock the quiet record held (ghz_on_cpu median 3.16 GHz). An estimate, labelled so:
              cycles are counted only while the thread runs, so preemption does not inflate them, but a lower clock
              under load, E-core cycles (P-core min below 1) and cache effects can still move them. On the quiet
              record itself it reproduces the measured median (test_m1_spread.py checks it).
  clean       every run of the configuration had its thread on a CPU for >= CLEAN_CPU_ON of the wall time (median pass),
              >= CLEAN_P_CORE of its cycles on P-cores in its worst pass, a clock of >= CLEAN_GHZ (ghz_on_cpu), all
              three counted (a row without them is not clean), and the run medians spread by <= CLEAN_SPREAD
              (max / min). These are #143's method choices, not a standard: they say when a run was not preempted, not
              moved to an E-core and repeatable, which is when the median is the stage's cost on a quiet M1. They were
              set on the quiet 13 Nov 2026 record (cpp/bench_apple_m1_simd_math.json, load average 1.7, one run:
              on-CPU >= 0.9965, worst-pass P-core >= 0.933, 3.15-3.17 GHz), where 116 of its 126 configurations
              pass them. A configuration is its quality and oversampling factor together: rows that differ in
              either are never combined.
"""
import argparse
import json
import statistics
import sys

FS = 48000
CLEAN_CPU_ON = 0.98
CLEAN_P_CORE = 0.95
CLEAN_SPREAD = 1.05
CLEAN_GHZ = 3.1     # the quiet record held 3.15-3.17 GHz on every row
QUIET_GHZ = 3.16    # cpp/bench_apple_m1_simd_math.json: median ghz_on_cpu 3.165 (3.15-3.17), rounded down (higher %)


def mode(r):
    """x1 / x2 (mono instances), s2 / v2 (12AX7 StereoStage, #21 / #67)."""
    return {"stereo_simd": "s2", "stereo_simd_vecmath": "v2"}.get(r.get("path"), f"x{r['instances']}")


def key(r):
    """A configuration: quality and its oversampling factor, setting (6AS6 variant / 12AX7 maths), mode, peak, block."""
    return (r["quality"], r["os"], r.get("variant") or r.get("math", ""), mode(r), r["level_v"], r["block"])


def label(k):
    q, _, var, m, v, b = k
    return f"{q} {var} {m} {v:g} V b{b}"


def pct(ns):
    return ns * FS / 1e9 * 100.0


def pct_at(cycles, ghz=QUIET_GHZ):
    return cycles * FS / (ghz * 1e9) * 100.0


def row_clean(r):
    """One run of a configuration was clean: on a CPU, on P-cores and at the quiet clock. Missing residency or clock
    data (a record without counters) is not evidence of a clean run."""
    pc, ghz = r.get("p_core_fraction_min"), r.get("ghz_on_cpu")
    return (r["cpu_on_fraction"] >= CLEAN_CPU_ON and pc is not None and pc >= CLEAN_P_CORE
            and ghz is not None and ghz >= CLEAN_GHZ)


def spread(records):
    """{key: summary} over the runs; every run must hold the same configurations."""
    runs = []
    for i, rec in enumerate(records, 1):
        run = {}
        for r in rec["results"]:
            if key(r) in run:
                raise ValueError(f"run {i} holds {label(key(r))} ({r['os']}x) twice")
            run[key(r)] = r
        runs.append(run)
    keys = list(runs[0])
    for i, run in enumerate(runs[1:], 2):
        if set(run) != set(keys):
            raise ValueError(f"run {i} has different configurations from run 1")
    out = {}
    for k in keys:
        rows = [run[k] for run in runs]
        med = [r["pct_core"] for r in rows]
        out[k] = {
            "os": k[1],
            "pct_runs": med,
            "pct_median": statistics.median(med),
            "spread": max(med) / min(med),
            "best_pct": min(pct(r["ns_min"]) for r in rows),
            "cpu_on_min": min(r["cpu_on_fraction"] for r in rows),
            "p_core_min": min((r["p_core_fraction_min"] for r in rows if r.get("p_core_fraction_min") is not None),
                              default=None),
            "ghz_min": min((r["ghz_on_cpu"] for r in rows if r.get("ghz_on_cpu") is not None), default=None),
            "cycles_median": statistics.median(r["cycles_per_sample"] for r in rows)
            if all(r.get("cycles_per_sample") is not None for r in rows) else None,
            "instr_median": statistics.median(r["instr_per_sample"] for r in rows)
            if all(r.get("instr_per_sample") is not None for r in rows) else None,
        }
        out[k]["est_quiet_pct"] = None if out[k]["cycles_median"] is None else pct_at(out[k]["cycles_median"])
        out[k]["clean"] = all(row_clean(r) for r in rows) and out[k]["spread"] <= CLEAN_SPREAD
    return out


def host_lines(records, names):
    lines = ["| run | UTC start | load 1 / 5 / 15 before | after | thermal / power note |", "|---|---|---|---|---|"]
    for n, rec in zip(names, records):
        b, a = rec["state_before"], rec["state_after"]
        therm = (b.get("pmset_therm") or "").replace("\n", " ")
        warn = "none recorded" if therm.count("No ") >= 2 else therm[:60]
        lb, la = (" / ".join(f"{x:g}" for x in st["loadavg_1_5_15"]) for st in (b, a))
        lines.append(f"| {n} | {b['utc']} | {lb} | {la} | {warn}; low power {b.get('lowpowermode')} |")
    return lines


def table(records, names):
    s = spread(records)
    lines = host_lines(records, names) + [
        "", "| configuration | os | % core per run (median) | median | spread | best pass % | on-CPU min | "
        "P-core min | GHz min | cycles | est. quiet % | clean |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in s.items():
        pc = "—" if v["p_core_min"] is None else f"{v['p_core_min']:.2f}"
        gh = "—" if v["ghz_min"] is None else f"{v['ghz_min']:.2f}"
        cy = "—" if v["cycles_median"] is None else f"{v['cycles_median']:.0f}"
        eq = "—" if v["est_quiet_pct"] is None else f"{v['est_quiet_pct']:.1f}"
        lines.append(f"| {label(k)} | {v['os']}× | {' / '.join(f'{x:.1f}' for x in v['pct_runs'])} | "
                     f"{v['pct_median']:.1f} | {v['spread']:.2f} | {v['best_pct']:.1f} | {v['cpu_on_min']:.2f} | "
                     f"{pc} | {gh} | {cy} | {eq} | {'yes' if v['clean'] else 'no'} |")
    n_clean = sum(v["clean"] for v in s.values())
    lines += ["", f"{n_clean} of {len(s)} configurations clean (on-CPU >= {CLEAN_CPU_ON}, P-core >= {CLEAN_P_CORE}, "
              f">= {CLEAN_GHZ} GHz, spread <= {CLEAN_SPREAD})."]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("records", nargs="+", help="results files of repeated runs of one core (cpp/bench.py --out)")
    a = ap.parse_args(argv)
    recs = [json.load(open(f)) for f in a.records]
    print(table(recs, [f.rsplit("/", 1)[-1] for f in a.records]))


if __name__ == "__main__":
    sys.exit(main())
