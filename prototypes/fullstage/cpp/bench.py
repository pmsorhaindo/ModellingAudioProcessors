"""Per-instance CPU benchmark of the C++ core (#20): builds cpp/bench.cpp, runs it, and writes one results file with
the host, build and power / thermal / load state next to the measurements. Method and results: cpp/BENCHMARK.md.
Run from prototypes/fullstage:
  python -m cpp.bench --out cpp/bench_<machine>.json      # the official run: 72 configurations, -O3, 2 s x 11 passes
  python -m cpp.bench --only standard_libm --reps 5        # a subset (bench.cpp --only), not for the record
  python -m cpp.bench --seconds 0.011 --reps 1             # a functional smoke (512 samples); its numbers mean nothing
  python -m cpp.bench --table cpp/bench_<machine>.json     # the BENCHMARK.md tables from a results file
Pin on Linux with taskset (taskset -c 2 python -m cpp.bench ...); macOS has no pinning API (bench.cpp sets QoS).
"""
import argparse, datetime, json, os, platform, shlex, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from cpp import build as B

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
        q = lambda k: _sh(["sysctl", "-n", k])
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


def run(flags=DEFAULT_FLAGS, reps=11, seconds=2.0, only=None):
    flags = shlex.split(flags)
    exe = B.build(tuple(flags), name="bench")
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
             "flags": B.CXXFLAGS + flags, "source": "cpp/bench.cpp", "git": commit, "dirty": dirty}
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


def table(rec):
    """The BENCHMARK.md tables: mono per instance; two mono instances; per-block p99."""
    R = {(r["quality"], r["math"], r["level_v"], r["block"], r["instances"]): r for r in rec["results"]}
    qs = [q for q in ("eco", "standard", "hq") if any(k[0] == q for k in R)]
    ms = [m for m in ("libm", "fast") if any(k[1] == m for k in R)]
    lv = sorted({k[2] for k in R})
    os_ = {k[0]: R[k]["os"] for k in R}
    lines = []
    for inst, title in ((1, "one mono instance"), (2, "two mono instances, one after the other (stereo-equivalent)")):
        if not any(k[4] == inst for k in R):
            continue
        lines += [f"**{title}** — % of one core at 48 kHz real time (median), ns per base-rate sample "
                  f"(median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)", "",
                  "| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for q in qs:
            for m in ms:
                for v in lv:
                    a, b = R.get((q, m, v, 64, inst)), R.get((q, m, v, 512, inst))
                    if not (a or b):
                        continue
                    ns = lambda r: "—" if not r else f"{r['ns_per_sample']:.0f} ({r['ns_min']:.0f}–{r['ns_max']:.0f})"
                    lines.append(f"| {q} {os_[q]}× | {m} | {v:g} V | {_cell(a, 'pct_core', '.2f')} | "
                                 f"{_cell(b, 'pct_core', '.2f')} | {ns(a)} | {ns(b)} | {_cycles(a)} | {_cycles(b)} | "
                                 f"{_cell(a or b, 'newton_mean', '.2f')} |")
        lines.append("")
    lines += ["**one mono instance, per block** — block time as % of the block's real-time period "
              "(b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes", "",
              "| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |", "|---|---|---|---|---|---|---|"]
    for q in qs:
        for m in ms:
            for v in lv:
                a, b = R.get((q, m, v, 64, 1)), R.get((q, m, v, 512, 1))
                if a or b:
                    lines.append(f"| {q} | {m} | {v:g} V | {_cell(a, 'block_pct_p50', '.1f')} | {_cell(a, 'block_pct_p99', '.1f')} | "
                                 f"{_cell(b, 'block_pct_p50', '.1f')} | {_cell(b, 'block_pct_p99', '.1f')} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flags", default=DEFAULT_FLAGS, help=f"optimisation / target flags (default {DEFAULT_FLAGS!r})")
    ap.add_argument("--reps", type=int, default=11)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--only", help="comma-separated substrings of configuration names")
    ap.add_argument("--out", help="write the results file here (else stdout)")
    ap.add_argument("--table", metavar="RESULTS_JSON", help="print the markdown tables of a results file and exit")
    a = ap.parse_args()
    if a.table:
        print(table(json.load(open(a.table))))
        return
    rec = run(a.flags, a.reps, a.seconds, a.only)
    if a.out:
        with open(a.out, "w") as f:
            dump(rec, f)
    else:
        dump(rec, sys.stdout)


if __name__ == "__main__":
    main()
