"""Issue #55: per-evaluation cost of the dual-control model, #51 form (KR = 0, XA = 1) vs the #55 dual_control
(KR, XA fitted), timed on this Python prototype (model6as6.dual_ip_ig2). Writes results/bench_eval.json.

Also times the #53 implementation of the KR term (z recomputed, its own softplus and exp; `ip_ig2_unshared` below,
kept only here) to show what reusing the share's exp saves. Timings depend on the machine; the README quotes one run.
- vectorised: one dual_ip_ig2 call on the whole grid (numpy arrays), reported per operating point;
- scalar: one dual_ip_ig2 call per operating point with Python floats (numpy scalar overhead dominates).
"""
import json
import os
import platform
import sys
import time
import numpy as np
import model6as6 as M

HERE = os.path.dirname(os.path.abspath(__file__))
FIT = json.load(open(os.path.join(HERE, "results", "fit_results.json")))
G = np.meshgrid(np.linspace(-5, 0, 8), np.array([60.0, 80.0, 100.0, 120.0, 140.0]), np.linspace(-14, 12, 13),
                np.linspace(20, 420, 10), indexing="ij")
GRID = [g.ravel() for g in G]                     # Ec1, Ec2, Ec3, Eb: 8 x 5 x 13 x 10 = 5200 operating points
WARMUP_V, REPEAT_V = 20, 200                      # vectorised: calls on the full grid
WARMUP_S, REPEAT_S = 1, 5                         # scalar: passes over the grid (5200 calls each)


def ip_ig2_unshared(vg1, vg2, vg3, va, p):
    """The #53 code path for KR > 0: share via dual_share, then z and 1/(1 + exp(z)) computed again."""
    ik0 = M.dual_ik(vg1, vg2, p["MU"], p["EX"], p["KG1"], p["KP"])
    f = M.dual_share(vg3, va, p["FMAX"], p["MU3"], p["V3C"], p["S3"], p["P3"], p["RI"], ik0, p["KVB"], p["XA"])
    z = M.dual_z(vg3, va, p["MU3"], p["V3C"], p["S3"], p["RI"], ik0, p["XA"])
    ik = ik0 * (1.0 - p["KR"] * np.exp(-M._sp(z)))
    return ik * f, ik * (1.0 - f)


def _median_ns(fn, warmup, repeat):
    for _ in range(warmup):
        fn()
    t = []
    for _ in range(repeat):
        t0 = time.perf_counter_ns()
        fn()
        t.append(time.perf_counter_ns() - t0)
    return float(np.median(t)), float(np.min(t))


def bench(fn, p):
    n = len(GRID[0])
    pts = list(zip(*(g.tolist() for g in GRID)))
    vmed, vmin = _median_ns(lambda: fn(*GRID, p), WARMUP_V, REPEAT_V)
    smed, smin = _median_ns(lambda: [fn(a, b, c, d, p) for a, b, c, d in pts], WARMUP_S, REPEAT_S)
    return dict(vectorised_ns_per_point=vmed / n, vectorised_ns_per_point_min=vmin / n,
                scalar_us_per_call=smed / n / 1e3, scalar_us_per_call_min=smin / n / 1e3)


CASES = dict(form_51=(M.dual_ip_ig2, FIT["refit_51"]["params"]),
             dual_control_55=(M.dual_ip_ig2, FIT["dual_control"]["params"]),
             dual_control_55_unshared_exp=(ip_ig2_unshared, FIT["dual_control"]["params"]))

if __name__ == "__main__":
    res = {k: bench(fn, p) for k, (fn, p) in CASES.items()}
    def r(a, b, m):
        return res[a][m] / res[b][m]
    out = dict(
        method=f"model6as6.dual_ip_ig2 on a fixed grid of {len(GRID[0])} operating points "
               f"(Ec1 -5..0 V x 8, Ec2 60-140 V "
               f"x 5, Ec3 -14..+12 V x 13, Eb 20-420 V x 10). Vectorised: {WARMUP_V} warm-up calls, then the median "
               f"of {REPEAT_V} timed calls on the whole grid, divided by the point count. Scalar: {WARMUP_S} warm-up "
               f"pass, then the median of {REPEAT_S} timed passes of one call per point with Python floats. "
               "time.perf_counter_ns; single thread; *_min is the fastest repeat.",
        machine=dict(python=sys.version.split()[0], numpy=np.__version__, platform=platform.platform(),
                     processor=platform.processor() or platform.machine(), note="this box (lab VM), one run"),
        results=res,
        ratio_55_to_51=dict(vectorised=r("dual_control_55", "form_51", "vectorised_ns_per_point"),
                            scalar=r("dual_control_55", "form_51", "scalar_us_per_call")),
        ratio_unshared_to_shared=dict(vectorised=r("dual_control_55_unshared_exp", "dual_control_55",
                                                   "vectorised_ns_per_point"),
                                      scalar=r("dual_control_55_unshared_exp", "dual_control_55",
                                               "scalar_us_per_call")))
    with open(os.path.join(HERE, "results", "bench_eval.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(json.dumps(out, indent=1))
