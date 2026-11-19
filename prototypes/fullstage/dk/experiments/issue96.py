"""Reproducible, serial #96 experiments; generated C++ variants live under --scratch (inside the worktree).

Run from prototypes/fullstage: python -m dk.experiments.issue96 --scratch ../../.scratch/issue96
Only this runner's results JSON is written; #95 results and aliasing baselines are read, never regenerated.
The generated headers are research variants, not production options. See RESULTS.md for their limitations.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess

import numpy as np

from cpp import build as B, bench, validate_cpp as VC
from dk import aliasing as AL, resample as RS, validate as V

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "dk/results_issue96.json"
VARIANTS = ("baseline", "fir231", "prewarp20k", "adaa8")


def headroom_proposal():
    """One-octave secants fixed using #95 48/96 kHz measurements, before consulting 44.1 kHz."""
    old = json.loads((ROOT / "dk/results_sample_rates.json").read_text())
    out = {}
    for q in ("hq", "standard", "eco"):
        for d in AL.DRIVES:
            w = {fs: AL.worst(old["rates"][str(fs)]["aliasing_records"], q, "fir", d)["total_dbc"]
                 for fs in (44100, 48000, 88200, 96000)}
            slope = w[48000] - w[96000]
            delta = slope * math.log2(48000 / 44100)
            limit = math.ceil((AL.LIMIT_DBC[(q, "fir")][d] + delta) * 10) / 10
            out[f"{q}/{d:g}V"] = dict(slope_db_octave=slope, delta_db=delta, proposed_limit_dbc=limit,
                                     baseline_dbc=w[44100], margin_db=limit - w[44100],
                                     check_88200_residual_db=w[88200] - (w[48000] - slope * math.log2(88200 / 48000)))
    return out


def adaa_code(order=8):
    """First-order ADAA = integral_0^1 i(vprev + t*(v-vprev)) dt; affine scalar port arguments.

    Gauss-Legendre integration avoids subtracting close antiderivatives and handles non-integer softplus powers.
    J = integral t*J_i(...) dt is the derivative w.r.t. the current endpoint. History is fixed during Newton.
    This changes the feedback discretisation; it is an experiment, not an equivalent circuit implementation.
    """
    nodes, weights = np.polynomial.legendre.leggauss(order)
    ts = ", ".join(repr(float(v)) for v in (nodes + 1) / 2)
    ws = ", ".join(repr(float(v)) for v in weights / 2)
    return f"""
inline thread_local double adaa_prev0 = 0.0, adaa_prev1 = 0.0;
template <class M> struct AdaaTriode {{
    static void currents(double v0, double v1, const TriodeParams& p, Currents& o) {{
        const double t[] = {{{ts}}}, w[] = {{{ws}}};
        o = Currents{{}};
        for (int k = 0; k < {order}; ++k) {{
            Currents c;
            Triode<M>::currents(adaa_prev0 + t[k] * (v0 - adaa_prev0),
                                adaa_prev1 + t[k] * (v1 - adaa_prev1), p, c);
            o.ig += w[k] * c.ig; o.ia += w[k] * c.ia;
            o.j00 += w[k] * t[k] * c.j00; o.j01 += w[k] * t[k] * c.j01;
            o.j10 += w[k] * t[k] * c.j10; o.j11 += w[k] * t[k] * c.j11;
        }}
    }}
    static void currents_only(double v0, double v1, const TriodeParams& p, double& ig, double& ia) {{
        Currents c; currents(v0, v1, p, c); ig = c.ig; ia = c.ia;
    }}
}};
"""


def replace_once(src, old, new):
    assert src.count(old) == 1, old
    return src.replace(old, new)


def build_variant(scratch, name):
    """Generate an isolated header from the pinned #95 source, without altering the shipped implementation."""
    folder = scratch / name
    folder.mkdir(parents=True, exist_ok=True)
    header = subprocess.check_output(["git", "show", "bb588c4:prototypes/fullstage/cpp/dk_core.h"], text=True)
    header = header.replace('#include "../dk/fastmath.h"', f'#include "{ROOT / "dk/fastmath.h"}"')
    if name == "fir231":
        header = replace_once(header, "FIR_FIRST_TAPS = 127", "FIR_FIRST_TAPS = 231")
    if name.startswith("adaa"):
        header = replace_once(header, "struct Solution {", adaa_code(int(name[4:])) + "\nstruct Solution {")
        header = replace_once(header, "template <class M>\ninline Solution solve",
                              "template <class M, class T = Triode<M>>\ninline Solution solve")
        header = replace_once(header, "    using T = Triode<M>;\n", "")
        old = "const Solution r = solve<M>(p0, p1, c.K, e0, e1, c.par, c.newton);"
        new = ("adaa_prev0 = q_[0]; adaa_prev1 = q_[1];\n        "
               "const Solution r = solve<M, AdaaTriode<M>>(p0, p1, c.K, e0, e1, c.par, c.newton);")
        header = replace_once(header, old, new)
    (folder / "dk_core.h").write_text(header)
    exes = {}
    for harness, flags in (("test_core", ("-O2",)), ("bench", ("-O3",))):
        src = subprocess.check_output(["git", "show", f"bb588c4:prototypes/fullstage/cpp/{harness}.cpp"],
                                      text=True)
        if harness == "bench":
            src = replace_once(src, "static constexpr double FS = 48000.0;", "static constexpr double FS = 44100.0;")
            if name == "prewarp20k":
                src = src.replace("{\"eco\", ECO}", '{"eco", Quality{4, 20000.0}}')
        (folder / f"{harness}.cpp").write_text(src)
        exe = folder / harness
        subprocess.run([B.compiler(), *B.CXXFLAGS, *flags, "-o", str(exe), str(folder / f"{harness}.cpp")],
                       check=True, capture_output=True)
        exes[harness] = str(exe)
    return exes


class ExperimentStage(VC.CppStage):
    variant = "baseline"

    def __init__(self, fs, quality="standard", **kw):
        if self.variant == "prewarp20k" and quality == "eco":
            quality = (4, 20000.0)
        super().__init__(fs, quality=quality, **kw)


def run_measurements(name, exes):
    ExperimentStage.variant = name
    VC.EXE = exes["test_core"]
    saved_stage, saved_fir = AL.Stage, RS.FIR_FIRST
    saved_44100 = getattr(RS, "FIR_FIRST_44100", None)
    saved_rule = RS.first_fir_taps
    AL.Stage = ExperimentStage
    RS.FIR_FIRST = (231 if name == "fir231" else 127, 9.0)
    RS.FIR_FIRST_44100 = RS.FIR_FIRST
    RS.first_fir_taps = lambda fs: RS.FIR_FIRST[0] if fs == 44100 else saved_rule(fs)
    records = []
    try:
        for q in ("hq", "standard", "eco"):
            if name == "prewarp20k" and q != "eco":
                continue
            records.extend(AL.sweep([(q, "fir")], fs=44100, workers=1))
            print(name, q, {d: AL.worst(records, q, "fir", d)["total_dbc"] for d in AL.DRIVES}, flush=True)
        for r in records:
            r.pop("cpu_s", None)
        # Direct time-domain 20 kHz / 1 mV amplitude check, including ADAA's changed feedback discretisation.
        n, n0 = 44100, 11025
        x = .001 * np.sin(2 * np.pi * 20000 * np.arange(n + n0 + 200) / 44100)
        ac = {}
        latency = {}
        for q in ("hq", "standard", "eco"):
            if name == "prewarp20k" and q != "eco":
                continue
            st = ExperimentStage(44100, quality=q)
            y = st.process(x)["out"][n0 + st.latency_samples:][:n]
            gain = abs(np.fft.rfft(y)[20000]) * 2 / n / .001
            ref = V.ref("dz")["ac"]
            ac[q] = dict(gain_db=float(20 * np.log10(gain)),
                         dz_20k_db=float(np.interp(20000, ref["f"], ref["db"])),
                         nonconverged=st.iteration_stats()["nonconverged"])
            latency[q] = dict(samples=st.latency_samples, ms=st.latency_samples / 44.1,
                              macs_up_down=list(st.resampler_macs))
        return dict(records=records, worst={f"{q}/{d:g}V": AL.worst(records, q, "fir", d)
                                           for q in ("hq", "standard", "eco") for d in AL.DRIVES
                                           if AL.select(records, q, "fir", d)},
                    latency=latency, time_domain_ac_20k=ac)
    finally:
        AL.Stage, RS.FIR_FIRST = saved_stage, saved_fir
        RS.first_fir_taps = saved_rule
        if saved_44100 is not None:
            RS.FIR_FIRST_44100 = saved_44100


def run_bench(name, exes):
    # Small comparative run: 2 drives, 3 qualities (eco only for pre-warp), mono/libm, block 64, 5 x .5 s.
    qs = ("eco",) if name == "prewarp20k" else ("hq", "standard", "eco")
    only = ",".join(f"{q}_libm_{d}V_b64_x1" for q in qs for d in (2, 8))
    before = bench.host_state()
    p = subprocess.run([exes["bench"], "--seconds", "0.5", "--reps", "5", "--only", only],
                       capture_output=True, text=True, check=True)
    return dict(host=bench.host_info(), state_before=before, state_after=bench.host_state(),
                flags=[*B.CXXFLAGS, "-O3"], **json.loads(p.stdout))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--variant", choices=(*VARIANTS, "adaa16"), nargs="+", default=list(VARIANTS))
    ap.add_argument("--bench-only", action="store_true")
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    assert scratch.is_relative_to(ROOT.parent.parent), "scratch must stay inside this worktree"
    out = json.loads(DEST.read_text()) if DEST.exists() else dict(variants={})
    out["method"] = dict(fs=44100, filter="fir", triode_math="libm", settle_s=AL.SETTLE_S, window_s=AL.WINDOW_S,
                         tones=list(AL.SWEEP_HZ), drives=list(AL.DRIVES),
                         source_commit="bb588c435b5b91e55daa75f1c5361d3395c7ddac",
                         baseline_source_sha256=hashlib.sha256(subprocess.check_output(
                             ["git", "show", "bb588c4:prototypes/fullstage/cpp/dk_core.h"])).hexdigest(),
                         harness="existing cpp/test_core.cpp and bench.cpp; generated research headers",
                         note="serial suites; independent C++ processes per tone; ADAA is a changed discretisation")
    out["headroom_proposal"] = headroom_proposal()
    for name in args.variant:
        exes = build_variant(scratch, name)
        row = out["variants"].setdefault(name, {})
        if not args.bench_only:
            row.update(run_measurements(name, exes))
            DEST.write_text(json.dumps(out, indent=1) + "\n")
        row["bench"] = run_bench(name, exes)
        DEST.write_text(json.dumps(out, indent=1) + "\n")
        print(name, "done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
