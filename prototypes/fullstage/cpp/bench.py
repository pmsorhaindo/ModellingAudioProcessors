"""Per-instance CPU benchmark of the C++ core (#20; stereo SIMD rows #21; vector-maths rows #67): builds cpp/bench.cpp,
runs it, and writes one results file with the host, build and power / thermal / load state next to the measurements.
Method and results: cpp/BENCHMARK.md. Run from prototypes/fullstage:
  python -m cpp.bench --out cpp/bench_<machine>_simd_math.json  # the official run: 126 configurations (x1, x2,
                                                                 # s2, v2), -O3, 2 s x 11 passes
  python -m cpp.bench --only standard_libm --reps 5        # a subset (bench.cpp --only), not for the record
  python -m cpp.bench --seconds 0.011 --reps 1             # a functional smoke (512 samples); its numbers mean nothing
  python -m cpp.bench --table cpp/bench_<machine>.json     # the BENCHMARK.md tables from a results file
  python -m cpp.bench --core 6as6 --out cpp/bench_6as6_<machine>.json   # the 6AS6 core (#104, cpp/bench_6as6.cpp):
                                                                 # 120 configurations (#135: fast law, two-lane
                                                                 # kernel), the same method
Pin on Linux with taskset (taskset -c 2 python -m cpp.bench ...); macOS has no pinning API (bench.cpp sets QoS).
"""
import argparse
import datetime
import json
import os
import platform
import shlex
import subprocess
import sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from cpp import build as B  # noqa: E402 -- after the sys.path setup

DEFAULT_FLAGS = "-O3"


def _sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def host_state():
    """What can change between runs on the same machine: load, power, thermal."""
    s = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
         "loadavg_1_5_15": [round(x, 2) for x in os.getloadavg()]}
    if platform.system() == "Darwin":
        s["pmset_batt"] = _sh(["pmset", "-g", "batt"])
        s["pmset_therm"] = _sh(["pmset", "-g", "therm"])
        s["lowpowermode"] = next((ln.split()[-1] for ln in (_sh(["pmset", "-g"]) or "").splitlines()
                                  if "lowpowermode" in ln), None)
    elif platform.system() == "Linux":
        s["scaling_governor_cpu0"] = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
        s["thermal_zone0_mC"] = _read("/sys/class/thermal/thermal_zone0/temp")
    return s


def host_info():
    h = {"platform": platform.platform(), "machine": platform.machine()}
    if platform.system() == "Darwin":
        def q(k):
            return _sh(["sysctl", "-n", k])
        h.update(cpu=q("machdep.cpu.brand_string"), ncpu=q("hw.ncpu"), p_cores=q("hw.perflevel0.logicalcpu"),
                 e_cores=q("hw.perflevel1.logicalcpu"), memsize=q("hw.memsize"),
                 model=q("hw.model"), os=" ".join(filter(None, [_sh(["sw_vers", "-productVersion"]),
                                                                _sh(["sw_vers", "-buildVersion"])])))
    elif platform.system() == "Linux":
        keep = ("Model name", "CPU(s)", "Thread(s) per core", "Core(s) per socket", "Hypervisor vendor",
                "Virtualization type", "CPU max MHz", "CPU MHz", "BogoMIPS", "L1d cache", "L2 cache", "L3 cache")
        lscpu = _sh(["lscpu"]) or ""
        h["lscpu"] = {k.strip(): v.strip() for k, _, v in (ln.partition(":") for ln in lscpu.splitlines())
                      if k.strip() in keep}
        h["perf_event_paranoid"] = _read("/proc/sys/kernel/perf_event_paranoid")
        h["nproc_online"] = os.cpu_count()
    return h


CORES = {"12ax7": "bench", "6as6": "bench_6as6"}       # --core: the harness built for each core


def run(flags=DEFAULT_FLAGS, reps=11, seconds=2.0, only=None, core="12ax7"):
    flags = shlex.split(flags)
    exe = B.build(tuple(flags), name=CORES[core])
    cxx = B.compiler()
    commit = _sh(["git", "-C", HERE, "rev-parse", "--short", "HEAD"])
    dirty = bool(_sh(["git", "-C", HERE, "status", "--porcelain", "--", "."]))
    before = host_state()
    args = [exe, "--reps", str(reps), "--seconds", str(seconds)] + (["--only", only] if only else [])
    p = subprocess.run(args, capture_output=True, text=True)
    after = host_state()
    sys.stderr.write(p.stderr)
    if p.returncode != 0:
        raise SystemExit(f"bench failed ({p.returncode}): output not deterministic or bad arguments")
    out = json.loads(p.stdout)
    build = {"cxx": cxx, "cxx_version": (_sh([cxx, "--version"]) or "").splitlines()[0],
             "flags": B.CXXFLAGS + flags, "source": f"cpp/{CORES[core]}.cpp", "git": commit, "dirty": dirty}
    return {"host": host_info(), "build": build, "state_before": before, "state_after": after,
            "meta": out["meta"], "results": out["results"]}


def dump(rec, f):
    """JSON with one result row per line (small, diffable)."""
    head = {k: v for k, v in rec.items() if k != "results"}
    s = json.dumps(head, indent=1)[:-2]
    rows = ",\n".join(json.dumps(r) for r in rec["results"])
    f.write(f'{s},\n "results": [\n{rows}\n]}}\n')


def _cell(r, key, fmt):
    v = r.get(key) if r else None
    return "—" if v is None else format(v, fmt)


def _cycles(r):
    """cycles per sample; on macOS marked * if any pass had < 99 % of its cycles on P-cores (E-core cycles differ)."""
    c = _cell(r, "cycles_per_sample", ".0f")
    pf = r.get("p_core_fraction_min") if r else None
    return c + ("*" if pf is not None and pf < 0.99 else "")


def _mode(r):
    """x1 / x2 / s2 (#21) / v2 (#67). The #20 records have no "path": mono only."""
    return {"stereo_simd": "s2", "stereo_simd_vecmath": "v2"}.get(r.get("path"), f"x{r['instances']}")


def _newton(r):
    """Newton iterations per internal sample: left; "left / right" for x2; "left / right / kernel max" for s2 and v2
    (#67 records; earlier ones have the left channel only)."""
    if not r:
        return "—"
    v = [r.get(k) for k in ("newton_mean", "newton_mean_r", "newton_mean_max")]
    return " / ".join(f"{x:.2f}" for x in v if x is not None)


def _ratio(a, b, key):
    va, vb = (a or {}).get(key), (b or {}).get(key)
    return "—" if va is None or vb is None else f"{va / vb:.2f}"


def _path6(r):
    """#135 rows: x1 / x2 (mono, by math), s2 (stereo libm), s2pl (stereo fast per-lane), v2 (stereo fast vector).
    Records before #135 have no "math" / "path": libm mono."""
    p, m = r.get("path", "mono"), r.get("math", "libm")
    if p == "mono":
        return f"x{r['instances']}" + ("" if m == "libm" else "_" + m)
    return {"stereo_simd": "s2", "stereo_simd_per_lane": "s2pl", "stereo_simd_vecmath": "v2"}[p]


def table_6as6_135(rec):
    """#135: the default setting's paths side by side (block 64): instructions and cycles per base-rate sample, %
    core, and the ratios to two libm mono instances (x2, the stereo cost before #135) and to one (x1)."""
    R = {(r["quality"], r["level_v"], r["block"], _path6(r)): r for r in rec["results"] if r["variant"] == "P1_0.45mA"}
    paths = [p for p in ("x1", "x1_fast", "x2", "x2_fast", "s2", "s2pl", "v2") if any(k[3] == p for k in R)]
    if not any(p in paths for p in ("x1_fast", "s2", "v2")):
        return ""
    qs = [q for q in ("standard", "hq") if any(k[0] == q for k in R)]
    lv = sorted({k[1] for k in R})
    out = []
    for key, title in (("instr_per_sample", "instructions per base-rate sample"),
                       ("cycles_per_sample", "cycles per base-rate sample (* = not all on P-cores)"),
                       ("pct_core", "% of one core at 48 kHz (median)")):
        out += [f"**#135 paths, default setting, block 64: {title}**; in brackets / x2 (two libm mono instances)", "",
                "| setting | peak | " + " | ".join(paths) + " |", "|---|---|" + "---|" * len(paths)]
        for q in qs:
            for v in lv:
                base = R.get((q, v, 64, "x2"))
                cells = []
                for p in paths:
                    r = R.get((q, v, 64, p))
                    if r is None or r.get(key) is None:
                        cells.append("—")
                        continue
                    fmt = ".2f" if key == "pct_core" else ".0f"
                    val = _cycles(r) if key == "cycles_per_sample" else _cell(r, key, fmt)
                    rat = _ratio(r, base, key)
                    cells.append(f"{val} ({rat})" if p != "x2" else val)
                os_ = next((R[k]["os"] for k in R if k[0] == q), "?")
                out.append(f"| {q} {os_}× | {v:g} V | " + " | ".join(cells) + " |")
        out.append("")
    st = [r for r in rec["results"] if r.get("path", "mono") != "mono"]
    if st:
        m = {r.get("matches_x2") for r in st}
        out.append(f"stereo rows bit-identical to their x2 twins (matches_x2): "
                   f"{'all' if m == {True} else 'NOT all: ' + str(sorted(str(x) for x in m))}")
    return "\n".join(out)


def table_6as6(rec):
    """The cpp/README-6as6.md tables of a 6AS6 record: the default setting per quality / peak / block, mono and two
    instances; then each variant (x1, block 64) against the default at the same quality and peak; then the #135
    paths (table_6as6_135)."""
    R = {(r["quality"], r["variant"], r["level_v"], r["block"], r["instances"]): r for r in rec["results"]
         if r.get("math", "libm") == "libm" and r.get("path", "mono") == "mono"}
    qs = [q for q in ("standard", "hq") if any(k[0] == q for k in R)]
    lv = sorted({k[2] for k in R})
    fir = next((r["fir"] for r in rec["results"] if r["variant"] == "P1_0.45mA"), "?")
    lines = [f"**default setting (P1, 0.45 mA, grid current, filter off, {fir} FIR)** — % of one core at 48 kHz real "
             "time (median), ns per base-rate sample (median, min–max), cycles per base-rate sample (median; * = not "
             "all on P-cores), Newton iterations per internal sample (mean, max)", "",
             "| setting | instances | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | "
             "Newton it. |", "|---|---|---|---|---|---|---|---|---|---|"]
    def ns(r):
        return "—" if not r else f"{r['ns_per_sample']:.0f} ({r['ns_min']:.0f}–{r['ns_max']:.0f})"
    for q in qs:
        for inst in (1, 2):
            for v in lv:
                a, b = R.get((q, "P1_0.45mA", v, 64, inst)), R.get((q, "P1_0.45mA", v, 512, inst))
                if not (a or b):
                    continue
                r = a or b
                lines.append(f"| {q} {r['os']}× | x{inst} | {v:g} V | {_cell(a, 'pct_core', '.2f')} | "
                             f"{_cell(b, 'pct_core', '.2f')} | {ns(a)} | {ns(b)} | {_cycles(a)} | {_cycles(b)} | "
                             f"{r['newton_mean']:.2f}, {r['newton_max']} |")
    lines += ["", "**one change from the default** (x1, block 64): cost relative to the default at the same quality "
              "and peak, median ns and cycles", "",
              "| variant | setting | peak | % core | ns / default | cycles / default | Newton it. |",
              "|---|---|---|---|---|---|---|"]
    for var in dict.fromkeys(k[1] for k in R):
        if var == "P1_0.45mA":
            continue
        for q in qs:
            for v in lv:
                a, d = R.get((q, var, v, 64, 1)), R.get((q, "P1_0.45mA", v, 64, 1))
                if a:
                    lines.append(f"| {var} | {q} | {v:g} V | {_cell(a, 'pct_core', '.2f')} | "
                                 f"{_ratio(a, d, 'ns_per_sample')} | {_ratio(a, d, 'cycles_per_sample')} | "
                                 f"{a['newton_mean']:.2f}, {a['newton_max']} |")
    extra = table_6as6_135(rec)
    return "\n".join(lines) + ("\n\n" + extra if extra else "")


def table(rec):
    """The BENCHMARK.md tables: mono per instance; two mono instances; stereo SIMD and its cost ratios (if in the
    record); per-block p99."""
    R = {(r["quality"], r["math"], r["level_v"], r["block"], _mode(r)): r for r in rec["results"]}
    qs = [q for q in ("eco", "standard", "hq") if any(k[0] == q for k in R)]
    ms = [m for m in ("libm", "fast") if any(k[1] == m for k in R)]
    lv = sorted({k[2] for k in R})
    os_ = {k[0]: R[k]["os"] for k in R}
    lines = []
    for inst, title in (("x1", "one mono instance"),
                        ("x2", "two mono instances, one after the other (stereo-equivalent)"),
                        ("s2", "stereo SIMD: one StereoStage, the x2 signals as two lanes of one kernel (#21)"),
                        ("v2", "stereo SIMD with vector maths: the s2 StereoStage with LaneMath::vector (#67)")):
        if not any(k[4] == inst for k in R):
            continue
        lines += [f"**{title}** — % of one core at 48 kHz real time (median), ns per base-rate sample "
                  f"(median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)", "",
                  "| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | "
                  "cycles b512 | Newton it. |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for q in qs:
            for m in ms:
                for v in lv:
                    a, b = R.get((q, m, v, 64, inst)), R.get((q, m, v, 512, inst))
                    if not (a or b):
                        continue
                    def ns(r):
                        return "—" if not r else f"{r['ns_per_sample']:.0f} ({r['ns_min']:.0f}–{r['ns_max']:.0f})"
                    lines.append(f"| {q} {os_[q]}× | {m} | {v:g} V | {_cell(a, 'pct_core', '.2f')} | "
                                 f"{_cell(b, 'pct_core', '.2f')} | {ns(a)} | {ns(b)} | {_cycles(a)} | {_cycles(b)} | "
                                 f"{_newton(a or b)} |")
        lines.append("")
    if any(k[4] == "v2" for k in R):
        lines += ["**vector-maths stereo cost (#67)** — v2 / x1, v2 / x2 and v2 / s2 from the same run: median ns per "
                  "base-rate sample, and cycles where counted (1.00 = one mono instance for v2/x1); matches x2 = v2 "
                  "output bit-identical to x2's", "",
                  "| setting | math | peak | ns v2/x1 b64 | ns v2/x1 b512 | ns v2/x2 b64 | ns v2/x2 b512 | "
                  "ns v2/s2 b64 | ns v2/s2 b512 | cycles v2/x1 b64 | cycles v2/s2 b64 | matches x2 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for q in qs:
            for m in ms:
                for v in lv:
                    w = {b: R.get((q, m, v, b, "v2")) for b in (64, 512)}
                    if not any(w.values()):
                        continue
                    one, two, s2 = ({b: R.get((q, m, v, b, mode)) for b in (64, 512)} for mode in ("x1", "x2", "s2"))
                    match = {r.get("matches_x2") for r in w.values() if r}
                    lines.append(f"| {q} {os_[q]}× | {m} | {v:g} V | {_ratio(w[64], one[64], 'ns_per_sample')} | "
                                 f"{_ratio(w[512], one[512], 'ns_per_sample')} | "
                                 f"{_ratio(w[64], two[64], 'ns_per_sample')} | "
                                 f"{_ratio(w[512], two[512], 'ns_per_sample')} | "
                                 f"{_ratio(w[64], s2[64], 'ns_per_sample')} | "
                                 f"{_ratio(w[512], s2[512], 'ns_per_sample')} | "
                                 f"{_ratio(w[64], one[64], 'cycles_per_sample')} | "
                                 f"{_ratio(w[64], s2[64], 'cycles_per_sample')} | "
                                 f"{'yes' if match == {True} else 'NO' if False in match else '—'} |")
        lines.append("")
    if any(k[4] == "s2" for k in R):
        lines += ["**stereo SIMD cost** — s2 / x1 and s2 / x2 from the same run: median ns per base-rate sample, and "
                  "cycles where counted (2.00 = two mono instances, 1.00 = one); matches x2 = s2 output bit-identical "
                  "to x2's", "",
                  "| setting | math | peak | ns s2/x1 b64 | ns s2/x1 b512 | ns s2/x2 b64 | ns s2/x2 b512 | "
                  "cycles s2/x1 b64 | cycles s2/x2 b64 | matches x2 |", "|---|---|---|---|---|---|---|---|---|---|"]
        for q in qs:
            for m in ms:
                for v in lv:
                    s = {b: R.get((q, m, v, b, "s2")) for b in (64, 512)}
                    if not any(s.values()):
                        continue
                    one = {b: R.get((q, m, v, b, "x1")) for b in (64, 512)}
                    two = {b: R.get((q, m, v, b, "x2")) for b in (64, 512)}
                    match = {r.get("matches_x2") for r in s.values() if r}
                    lines.append(f"| {q} {os_[q]}× | {m} | {v:g} V | {_ratio(s[64], one[64], 'ns_per_sample')} | "
                                 f"{_ratio(s[512], one[512], 'ns_per_sample')} | "
                                 f"{_ratio(s[64], two[64], 'ns_per_sample')} | "
                                 f"{_ratio(s[512], two[512], 'ns_per_sample')} | "
                                 f"{_ratio(s[64], one[64], 'cycles_per_sample')} | "
                                 f"{_ratio(s[64], two[64], 'cycles_per_sample')} | "
                                 f"{'yes' if match == {True} else 'NO' if False in match else '—'} |")
        lines.append("")
    lines += ["**one mono instance, per block** — block time as % of the block's real-time period "
              "(b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes", "",
              "| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |", "|---|---|---|---|---|---|---|"]
    for q in qs:
        for m in ms:
            for v in lv:
                a, b = R.get((q, m, v, 64, "x1")), R.get((q, m, v, 512, "x1"))
                if a or b:
                    lines.append(f"| {q} | {m} | {v:g} V | {_cell(a, 'block_pct_p50', '.1f')} | "
                                 f"{_cell(a, 'block_pct_p99', '.1f')} | "
                                 f"{_cell(b, 'block_pct_p50', '.1f')} | {_cell(b, 'block_pct_p99', '.1f')} |")
    return "\n".join(lines)



def _argv_with_flags(argv):
    """Rewrite argv so `--flags -O3` is accepted as well as `--flags=-O3` and `--flags "-O3 -march=..."`.

    argparse otherwise treats a following token that starts with `-` as a new option, and `--flags -O3`
    fails with "expected one argument".
    """
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "--flags" and i + 1 < len(argv):
            out.append("--flags=" + argv[i + 1])
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flags", default=DEFAULT_FLAGS, help=f"optimisation / target flags (default {DEFAULT_FLAGS!r})")
    ap.add_argument("--reps", type=int, default=11)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--only", help="comma-separated substrings of configuration names")
    ap.add_argument("--out", help="write the results file here (else stdout)")
    ap.add_argument("--table", metavar="RESULTS_JSON", help="print the markdown tables of a results file and exit")
    ap.add_argument("--core", choices=sorted(CORES), default="12ax7", help="which core's harness (default 12ax7)")
    a = ap.parse_args(_argv_with_flags(sys.argv[1:]))
    if a.table:
        rec = json.load(open(a.table))
        print(table_6as6(rec) if rec["meta"].get("core") == "cpp/dk6as6_core.h" else table(rec))
        return
    rec = run(a.flags, a.reps, a.seconds, a.only, a.core)
    if a.out:
        with open(a.out, "w") as f:
            dump(rec, f)
    else:
        dump(rec, sys.stdout)


if __name__ == "__main__":
    main()
