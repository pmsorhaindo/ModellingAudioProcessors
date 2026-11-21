"""FIR resampler stopband study for the 6AS6 stage (#113): how much aliasing the half-band FIR resampler itself leaves,
and what deeper stopbands cost in length, latency and CPU.

Why: at 2 V every supported 6AS6 setting, and T at 8 V, is at the resampler's floor (dk6as6/RESULTS.md, Aliasing), and
P2 fails the quality gate at 2 V (-89 to -97 dBc against -118.3) with every worst-tone harmonic above 20 kHz; hq (16x)
does not fix it (PR #121). The shipped design (dk/resample.py: first stage 127 taps at 48 kHz, 215 at 44.1 kHz, later
stages 23 taps, Kaiser beta 9 throughout) has a one-pass stopband of 92.8-94.2 dB (48 kHz) and 89.3-90.1 dB (44.1 kHz)
over every band that folds onto 0-20 kHz. Its first stage is limited by beta, not by its length: at 127 taps a larger
beta reaches ~157 dB.

Candidates (CANDIDATES), derived, not chosen by hand: for a target stopband A (dB), beta = 0.1102 (A - 8.7) (Kaiser's
formula, Oppenheim & Schafer, Discrete-Time Signal Processing, 3rd ed., Sec. 7.5, eq. 7.75, p. 542), and each stage
(the first, and the later ones, which share one design) gets the shortest half-band length 4m + 3 whose one-pass
response is at least A dB down over its folding band (R/2 - 20 kHz .. R/2 at that stage's high rate R) with passband
ripple <= 0.005 dB to 20 kHz (the module's resampler spec is 0.01 dB for the whole chain). design_for() is the search.

Commands (from prototypes/fullstage):
  python -m dk6as6.resampler_study design        -> table of every candidate: lengths, beta, ripple, stopband, latency,
                                                     MACs per base sample, at 4x / 8x / 16x and 48 / 44.1 kHz
  python -m dk6as6.resampler_study run [--rate R] [--candidates ...] [--points ...] [--workers N] [--jsonl PATH]
                                                  -> aliasing points (lpf_aliasing.measure, 1 s window) per candidate
  python -m dk6as6.resampler_study cost           -> C++ microbenchmark of the resampler alone (resampler_cost.cpp)
  python -m dk6as6.resampler_study collect        -> dk6as6/resampler_study.json
"""
import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import WINDOW_S  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler, _FirHB, FIR_FIRST, FIR_LATER, first_fir_taps, PASS_HZ  # noqa: E402
from dk6as6 import lpf, lpf_aliasing  # noqa: E402 -- after the sys.path setup
from dk6as6.aliasing import SETTLE_S  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import ExperimentalWarning  # noqa: E402 -- after the sys.path setup

RATES = (48000, 44100)
FACTORS = (4, 8, 16)
TARGETS_DB = (120, 140, 150)
RIPPLE_STAGE_DB = 0.005
OUT_JSON = os.path.join(HERE, "resampler_study.json")
SCRATCH = os.path.expanduser(os.environ.get("ISSUE113_SCRATCH", os.path.join(HERE, "..", "..", "..", ".scratch",
                                                                                   "issue113")))
JSONL = os.path.join(SCRATCH, "resampler_study.jsonl")


def kaiser_beta(a_db):
    """Kaiser's beta for a stopband of a_db > 50 dB."""
    return round(0.1102 * (a_db - 8.7), 2)


def stage_response(ntaps, beta, R):
    """(passband ripple to PASS_HZ, dB; one-pass stopband over R/2 - PASS_HZ .. R/2, dB down) of one half-band at R."""
    hb = _FirHB(ntaps, beta)
    rip = float(np.max(np.abs(20 * np.log10(np.abs(hb.freq(np.linspace(0, PASS_HZ, 2001), R))))))
    stop = float(-np.max(20 * np.log10(np.abs(hb.freq(np.linspace(R / 2 - PASS_HZ, R / 2, 8001), R)))))
    return rip, stop


def design_for(a_db, fs):
    """((first taps, beta), (later taps, beta)): the shortest 4m + 3 lengths meeting a_db (module docstring)."""
    beta = kaiser_beta(a_db)
    out = []
    for R in (2 * fs, 4 * fs):          # stage 0 and stage 1; stages 2+ have wider transitions than stage 1
        for n in range(7, 1000, 4):
            rip, stop = stage_response(n, beta, R)
            if stop >= a_db and rip <= RIPPLE_STAGE_DB:
                out.append((n, beta))
                break
    return tuple(out)


def shipped(fs):
    return ((first_fir_taps(fs), FIR_FIRST[1]), FIR_LATER)


CANDIDATES = {"shipped": {fs: shipped(fs) for fs in RATES}}
CANDIDATES.update({f"k{a}": {fs: design_for(a, fs) for fs in RATES} for a in TARGETS_DB})


def chain_metrics(fs, os_, fir):
    """The whole cascade: chain passband ripple to 20 kHz (up then down), the worst one-pass response over every
    internal-rate band that folds onto 0-20 kHz (k fs +- 20 kHz, k >= 1, below os fs / 2), latency, MACs."""
    r = Resampler(fs, os_, "fir", fir=fir)
    rip = float(np.max(np.abs(20 * np.log10(np.abs(r.chain_response(np.linspace(0, PASS_HZ, 2001)))))))
    F, bands, k = os_ * fs / 2, [], 1
    while k * fs - PASS_HZ <= F:
        bands.append(np.linspace(k * fs - PASS_HZ, min(k * fs + PASS_HZ, F), 4001))
        k += 1
    stop = float(-np.max(20 * np.log10(np.abs(r.internal_response(np.concatenate(bands))) + 1e-300)))
    return dict(ripple_db=rip, stopband_db=stop, latency_samples=r.latency_samples, latency_ms=r.latency_ms,
                macs_per_direction=r.macs()[0], cpp_macs_per_sample=cpp_macs(fir, os_))


def cpp_macs(fir, os_):
    """Multiply-adds per base sample, up + down, as cpp/dk_core.h implements the cascade: FirHalfBand's polyphase
    branches hold N taps on the way up and N + 1 on the way down (the zero taps included), and stage i runs 2^i times
    per base sample. Machine-independent; the timings (cost()) are the cross-check."""
    (n0, _), (n1, _) = fir
    return sum(2 ** i * (2 * (n0 if i == 0 else n1) + 1) for i in range(int(np.log2(os_))))


def design_table():
    rows = []
    for name, by_rate in CANDIDATES.items():
        for fs in RATES:
            (n0, b0), (n1, b1) = by_rate[fs]
            for os_ in FACTORS:
                rows.append(dict(candidate=name, fs=fs, os=os_, first_taps=n0, first_beta=b0, later_taps=n1,
                                 later_beta=b1, **chain_metrics(fs, os_, by_rate[fs])))
    return rows


# ---- the band edge: an image-rejection bound (#113 review) -------------------------------------------------------
# Where a point sits on the resampler's floor, its resampler share follows the interpolator's image of the tone: the
# image at fs - f, rejected by A(fs - f) (the one-pass response, Resampler.up_response), mixes with the tone in the
# stage's second-order term and lands on |fs - 2f| in the audio band. So share + A(fs - f) = K, a coefficient of the
# stage alone; measured at 15037-19987 Hz (P1 0.6 mA, 2 V, 44.1 kHz) K spans -23.9 to -20.0 dB. Then for any tone in
# [f_lo, 20 kHz] the share is at most max K - min A over the images of that band: a bound between the sampled tones,
# up to the band edge, valid where the floor is this mechanism (the 2 V points; checked by the spread of K).
BOUND_LO_HZ = 15000.0


def image_rejection_db(fs, os_, f, fir=None):
    """A(fs - f): the interpolator's one-pass rejection (dB) of the image of tone(s) f."""
    r = Resampler(fs, os_, "fir", fir=fir)
    return -20 * np.log10(np.abs(r.up_response(fs - np.atleast_1d(np.asarray(f, float)))))


def band_edge_bound(records, fs, os_, fir=None, f_lo=BOUND_LO_HZ, f_hi=PASS_HZ):
    """records: one key's points (dicts with tone and off.resampler_dbc) at tones in [f_lo, f_hi]. Returns
    dict(k_min, k_max, min_rejection_db, bound_dbc): the image-rejection bound on the resampler share of any tone in
    [f_lo, f_hi], from the measured coefficients K = share + A(fs - f) and the design's minimum A over those images."""
    pts = [r for r in records if f_lo <= r["tone"] <= f_hi]
    k = [r["off"]["resampler_dbc"] + float(image_rejection_db(fs, os_, r["tone"], fir)[0]) for r in pts]
    a_min = float(np.min(image_rejection_db(fs, os_, np.linspace(f_lo, f_hi, 20001), fir)))
    return dict(tones=sorted(r["tone"] for r in pts), k_min=min(k), k_max=max(k), min_rejection_db=a_min,
                bound_dbc=max(k) - a_min)


# ---- aliasing points -------------------------------------------------------------------------------------------
# (mode, bias mA, drive V, tone Hz): P2's worst 2 V tones (19037, 16037: dk6as6/aliasing_P2.json) at every reference
# bias and its two worst 8 V points; the supported settings nearest the gate at the resampler floor (P1 0.6 / 0.45 mA
# and T 0.3 mA at 2 V, T 0.3 mA at 8 V, all worst at 19037 Hz in aliasing[_T].json).
POINTS = ([("P2", b, 2.0, f) for b in (0.1, 0.25, 0.45, 0.6) for f in (19037, 16037)]
          + [("P2", 0.25, 8.0, 19037), ("P2", 0.45, 8.0, 19037)]
          + [("P1", 0.6, 2.0, 19037), ("P1", 0.45, 2.0, 19037), ("T", 0.3, 2.0, 19037), ("T", 0.3, 8.0, 19037)])


def measure(fs, candidate, mode, bias, drive, tone, setting="standard", up=None, down=None, window=WINDOW_S):
    """lpf_aliasing.measure with the candidate on both sides (or `up` / `down` candidates for a split)."""
    up, down = up or candidate, down or candidate
    q = {"4x": (4, None), "standard": (8, None), "hq": (16, None)}[setting]
    t = time.process_time()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ExperimentalWarning)
        r = lpf_aliasing.measure(fs, drive, tone, bias=bias, window=window, settle=SETTLE_S, quality=q, mode=mode,
                                 fir_up=CANDIDATES[up][fs], fir_down=CANDIDATES[down][fs])
    return dict(r, mode=mode, setting=setting, os=q[0], candidate=candidate, up=up, down=down,
                cpu_s=time.process_time() - t)


def _job(a):
    return measure(*a[:6], **a[6])


def point_key(r):
    return (r["candidate"], r["up"], r["down"], r["setting"], int(r["fs"]), r["mode"], float(r["bias_ma"]),
            float(r["drive"]), int(r["tone"]))


def load_jsonl(path=JSONL):
    out = {}
    if os.path.exists(path):
        for line in open(path):
            if line.strip():
                r = json.loads(line)
                out[point_key(r)] = r
    return out


def run(jobs, workers=None, jsonl=JSONL):
    os.makedirs(os.path.dirname(os.path.abspath(jsonl)), exist_ok=True)
    done = set(load_jsonl(jsonl))
    todo = []
    for j in jobs:
        kw = j[6]
        up, down = kw.get("up") or j[1], kw.get("down") or j[1]
        k = (j[1], up, down, kw.get("setting", "standard"), j[0], j[2], float(j[3]), float(j[4]), int(j[5]))
        if k not in done:
            todo.append(j)
    print(f"{len(todo)} points to run ({len(jobs) - len(todo)} done)", flush=True)
    with ProcessPoolExecutor(workers) as ex, open(jsonl, "a") as fh:
        futs = [ex.submit(_job, j) for j in todo]
        for i, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(f"[{i}/{len(todo)}] {r['candidate']} (up {r['up']}, down {r['down']}) {r['setting']} {r['fs']} "
                  f"{r['mode']} {r['bias_ma']} mA {r['drive']:g} V {r['tone']}: off {r['off']['total_dbc']:.2f} dBc "
                  f"(internal {r['off']['internal_dbc']:.1f}, resampler {r['off']['resampler_dbc']:.1f})", flush=True)


# ---- CPU cost (C++) ----------------------------------------------------------------------------------------------
COST_CPP = os.path.join(HERE, "resampler_cost.cpp")
CXXFLAGS = ["-std=c++17", "-O3", "-Wall", "-Wextra"]


def cost(rounds=7, seconds=0.5):
    """Build resampler_cost.cpp once per candidate/rate (its capacities are template constants) and time each
    candidate's up + down at 4x / 8x / 16x, in alternating order over `rounds` rounds; returns the median ns per base
    sample per (candidate, rate, os) with the min-max of the round medians."""
    exe = {}
    tmp = tempfile.mkdtemp(prefix="issue113_cost_")
    for name, by_rate in CANDIDATES.items():
        for fs in RATES:
            (n0, b0), (n1, b1) = by_rate[fs]
            path = os.path.join(tmp, f"{name}_{fs}")
            subprocess.run(["c++", *CXXFLAGS, f"-DFIRST={n0}", f"-DLATER={n1}", f"-DBETA0={b0}", f"-DBETA1={b1}",
                            f"-I{os.path.join(os.path.dirname(HERE), 'cpp')}", COST_CPP, "-o", path], check=True)
            exe[name, fs] = path
    res = {}
    order = list(exe)
    for k in range(rounds):
        for key in (order if k % 2 == 0 else order[::-1]):
            for os_ in FACTORS:
                out = subprocess.run([exe[key], str(os_), str(key[1]), str(seconds)], capture_output=True, text=True,
                                     check=True).stdout.split()
                res.setdefault((*key, os_), []).append(float(out[0]))
    cc = subprocess.run(["c++", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    return dict(method=dict(compiler=cc, flags=CXXFLAGS, machine=platform.machine(), platform=platform.platform(),
                            rounds=rounds, seconds_per_timing=seconds,
                            what="up + down of the FIR cascade alone (dk_core.h FirHalfBand), mono double, one "
                                 "base-rate sample at a time as ResamplerT does; median over rounds of ns per base "
                                 "sample; candidates in alternating order; other jobs were running on the machine"),
                rows=[dict(candidate=c, fs=fs, os=o, ns_per_sample=statistics.median(v), ns_min=min(v), ns_max=max(v))
                      for (c, fs, o), v in sorted(res.items())])


def collect(jsonl=JSONL, cost_json=None):
    recs = sorted(load_jsonl(jsonl).values(), key=point_key)
    for r in recs:
        r.pop("cpu_s", None)
    out = dict(method=dict(settle_s=SETTLE_S, window_s=WINDOW_S, targets_db=list(TARGETS_DB),
                           ripple_stage_db=RIPPLE_STAGE_DB, positions=list(lpf.POSITIONS),
                           candidates={k: {str(fs): [list(s) for s in v[fs]] for fs in RATES}
                                       for k, v in CANDIDATES.items()}),
               design=design_table(), records=recs)
    if cost_json and os.path.exists(cost_json):
        out["cost"] = json.load(open(cost_json))
    elif os.path.exists(OUT_JSON) and "cost" in json.load(open(OUT_JSON)):
        out["cost"] = json.load(open(OUT_JSON))["cost"]
    json.dump(out, open(OUT_JSON, "w"), indent=0)
    print(f"{len(recs)} records -> {OUT_JSON}")


def tables(path=OUT_JSON):
    """Markdown for dk6as6/RESULTS.md: the design table, and the aliasing per POINTS row and candidate."""
    d = json.load(open(path))
    out = ["| candidate | rate | first stage taps / beta | later taps / beta | one-pass stopband 4x / 8x / 16x, dB | "
           "chain ripple, dB | latency 4x / 8x / 16x, base samples | C++ MACs per sample 4x / 8x / 16x |",
           "|---|---|---|---|---|---|---|---|"]
    for c in CANDIDATES:
        for fs in RATES:
            r = sorted((x for x in d["design"] if x["candidate"] == c and x["fs"] == fs), key=lambda x: x["os"])
            out.append(f"| {c} | {fs / 1000:g} kHz | {r[0]['first_taps']} / {r[0]['first_beta']:g} | "
                       f"{r[0]['later_taps']} / {r[0]['later_beta']:g} | "
                       + " / ".join(f"{x['stopband_db']:.1f}" for x in r)
                       + f" | <= {max(x['ripple_db'] for x in r):.1e} | "
                       + " / ".join(str(x["latency_samples"]) for x in r) + " | "
                       + " / ".join(str(x["cpp_macs_per_sample"]) for x in r) + " |")
    idx = {(r["candidate"], r["up"], r["down"], r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"],
            r["tone"]): r for r in d["records"]}
    out += ["", "| point | rate | " + " | ".join(CANDIDATES) + " |", "|---" * (len(CANDIDATES) + 2) + "|"]
    for fs in RATES:
        for m, b, dr, f in POINTS:
            cells = []
            for c in CANDIDATES:
                r = idx.get((c, c, c, "standard", fs, m, b, dr, f))
                cells.append("—" if r is None else f"{r['off']['total_dbc']:.1f} [{r['off']['resampler_dbc']:.1f}]")
            out.append(f"| {m} {b:g} mA {dr:g} V {f} | {fs / 1000:g} kHz | " + " | ".join(cells) + " |")
    return "\n".join(out)


CHECKPOINT_CASES = (("P1", 0.1), ("P1", 0.6), ("T", 0.3))
CHECKPOINT_HZ = (16037, 19037, 19937)


def checkpoint_table(path=OUT_JSON):
    """Markdown: the supported-mode k140 checkpoints (#113 review) at every factor and rate, against the shipped FIR's
    total at the same point (aliasing_quality.json / lpf_aliasing.json), filter off, dBc."""
    from dk6as6 import aliasing_quality as Q
    ship = {(r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]): r["off"]["total_dbc"]
            for r in Q.records()}
    k = {(r["setting"], r["fs"], r["mode"], r["bias_ma"], r["drive"], r["tone"]): r["off"]["total_dbc"]
         for r in json.load(open(path))["records"] if r["candidate"] == r["up"] == r["down"] == "k140"}
    out = ["| rate | setting | case, drive | " + " | ".join(f"{f} Hz: shipped → k140" for f in CHECKPOINT_HZ) + " |",
           "|---" * (3 + len(CHECKPOINT_HZ)) + "|"]
    for fs in RATES:
        for st in ("4x", "standard", "hq"):
            for m, b in CHECKPOINT_CASES:
                for d in (2.0, 8.0):
                    cells = [f"{ship[st, fs, m, b, d, f]:.1f} → {k[st, fs, m, b, d, f]:.1f}"
                             if (st, fs, m, b, d, f) in k and (st, fs, m, b, d, f) in ship else "—"
                             for f in CHECKPOINT_HZ]
                    out.append(f"| {fs / 1000:g} kHz | {st} | {m} {b:g} mA, {d:g} V | " + " | ".join(cells) + " |")
    return "\n".join(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("design")
    r = sub.add_parser("run")
    r.add_argument("--rate", type=int, nargs="*", default=list(RATES))
    r.add_argument("--candidates", nargs="*", default=list(CANDIDATES))
    r.add_argument("--setting", default="standard", choices=("4x", "standard", "hq"))
    r.add_argument("--points", nargs="*", help="mode:bias:drive:tone (default POINTS)")
    r.add_argument("--split", help="up/down split for this candidate against shipped (P2 0.45 mA 2 V 19037 Hz)")
    r.add_argument("--workers", type=int, default=os.cpu_count())
    r.add_argument("--jsonl", default=JSONL)
    c = sub.add_parser("cost")
    c.add_argument("--rounds", type=int, default=7)
    c.add_argument("--out", default=os.path.join(SCRATCH, "resampler_cost.json"))
    sub.add_parser("tables")
    k = sub.add_parser("collect")
    k.add_argument("--jsonl", default=JSONL)
    k.add_argument("--cost", default=None)
    a = ap.parse_args()
    if a.cmd == "design":
        for row in design_table():
            print(row)
    elif a.cmd == "run":
        pts = [tuple(t(x) for t, x in zip((str, float, float, int), p.split(":"))) for p in a.points] if a.points \
            else POINTS
        jobs = []
        for fs in a.rate:
            if a.split:
                for (m, b, d, f) in pts:
                    jobs += [(fs, a.split, m, b, d, f, dict(setting=a.setting, up=a.split, down="shipped")),
                             (fs, a.split, m, b, d, f, dict(setting=a.setting, up="shipped", down=a.split))]
            else:
                jobs += [(fs, cand, m, b, d, f, dict(setting=a.setting)) for cand in a.candidates
                         for (m, b, d, f) in pts]
        run(jobs, a.workers, a.jsonl)
    elif a.cmd == "cost":
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(cost(a.rounds), open(a.out, "w"), indent=1)
        print(open(a.out).read())
    elif a.cmd == "tables":
        print(tables())
        print()
        print(checkpoint_table())
    else:
        collect(a.jsonl, a.cost)
