"""dk6as6.validate on the C++ 6AS6 core (#104): the Python stage's ngspice validation (dk6as6/results*.json, against the
same-model ngspice references spice6as6/reference_*.json) reproduced by cpp/dk6as6_core.h, at standard and hq.

CppStage has the part of dk6as6.stage.Stage's interface that dk6as6.validate.run uses, backed by `test_core_6as6 cli`
(the core's Stage::process on the whole signal, its pack and operating point). run() runs dk6as6.validate.run with
CppStage in place of Stage, so every time-domain number (harmonics, THD, the burst's Vg1k trajectory, Newton
iterations) comes from the C++ process(), and the small-signal numbers from the C++ matrices and operating point (the
analysis itself, dk6as6/linear.py, and the resampler's analytical response stay Python; the C++ resampler is checked
against Python's sample by sample in cpp/test_core_6as6.cpp). compare() lists every deviation from the committed Python
result of the same setting; LIMITS are the tolerances cpp/tests/test_cpp_core_6as6.py asserts. Every criterion
(dk/validate.CRIT, unchanged) must read the same as the Python result's, and every supported setting must pass.
Run from prototypes/fullstage:  python -m cpp.validate_cpp_6as6 [--workers N] [--fir shipped]   (~1 min; writes
cpp/results_6as6.json: k140, the parity run against the committed Python results, which use k140 since #134; or with
--fir shipped cpp/results_6as6_shipped.json: the #102-#113 resampler, which the
Python results do not use, judged on dk/validate.CRIT alone)
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk6as6 import circuit, pentode, validate  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY, BIAS_MA, GRID_SHAPE, supported  # noqa: E402 -- after the sys.path setup
from spice6as6.run_refs import MODE_BIASES, GRID_BIASES  # noqa: E402 -- after the sys.path setup
from cpp.build import build  # noqa: E402 -- after the sys.path setup
from cpp.export_vectors_6as6 import K140  # noqa: E402 -- after the sys.path setup

EXE = None          # test_core_6as6 used by CppStage (set by run(), or by the caller)
PARITY_FIR = "k140"  # the design of the committed Python results (dk6as6.stage.FIR, the default since #134)
FIR = PARITY_FIR     # CppStage's resampler: PARITY_FIR (the parity run) or "shipped" (--fir shipped, CRIT alone)
MODELS = {"dual": MODE_BIASES, "dualg": GRID_BIASES}     # (ii) references: no grid current / the default grid law


def exe():
    global EXE
    EXE = EXE or build(name="test_core_6as6")
    return EXE


def cli(vin, fs, os_, mode, bias, hardness, lpf="off", drive=1.0, output=1.0, fir="default", rec=False):
    """Stage::process on vin through `test_core_6as6 cli`: (out, internal (Vg1k, Vg2k) or None, info dict)."""
    vin = np.ascontiguousarray(vin, dtype=np.float64)
    args = [exe(), "cli", repr(float(fs)), str(int(os_)), mode, repr(float(bias)), repr(float(hardness)), lpf,
            repr(float(drive)), repr(float(output)), fir] + (["rec"] if rec else [])
    p = subprocess.run(args, input=vin.tobytes(), capture_output=True)
    info = json.loads(p.stderr.decode().strip().splitlines()[-1])
    if p.returncode != 0:
        raise RuntimeError(f"test_core_6as6 cli failed ({p.returncode}): {info}")
    raw = np.frombuffer(p.stdout, dtype=np.float64)
    n, m = len(vin), len(vin) * int(os_)
    internal = (raw[n:n + m].copy(), raw[n + m:n + 2 * m].copy()) if rec else None
    return raw[:n].copy(), internal, info


class CppStage:
    """dk6as6.stage.Stage(fs, bias, quality, filt="fir", mode, grid_hardness, grid_shape) on the C++ core, for
    dk6as6.validate.run. The C++ core has the nominal grid law only (pentode.GRID_SHAPE); #131's bounds are
    Python-only sensitivity studies. Each instance runs one process() call from reset (the CLI starts a fresh core),
    which is how validate uses Stage."""

    def __init__(self, fs, bias=BIAS_MA, drive=1.0, output=1.0, pentode=pentode.DUAL_CONTROL, quality="standard",
                 filt="fir", newton=None, mode=circuit.MODE, experimental=False, grid_hardness=pentode.HARDNESS,
                 grid_shape=GRID_SHAPE):
        if filt != "fir" or newton is not None or pentode is not validate.PARAMS["dual"]:
            raise NotImplementedError("CppStage: fir, the default Newton settings and the dual_control fit only")
        if grid_shape != GRID_SHAPE:         # `pentode` is the fit here, not the module
            raise NotImplementedError(f"CppStage: the nominal grid shape {GRID_SHAPE} only, not {grid_shape}")
        self.fs_base, self.bias, self.drive, self.output, self.par, self.mode = fs, bias, drive, output, pentode, mode
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        assert self.prewarp_hz is None
        self.grid_hardness = grid_hardness
        self.grid = validate.pentode.grid(grid_hardness, pentode) if grid_hardness else None
        self.supported = supported(mode, bias, grid_hardness)
        _, _, info = cli(np.zeros(0), fs, self.os, mode, bias, grid_hardness, fir=FIR)
        self.info, self.fs, self.rk = info, info["fs"], info["rk"]
        nx, nv, no, nu, ni = 7, 4, 5, info["nu"], info["ni"]
        shapes = dict(A=(nx, nx), B=(nx, nu), C=(nx, ni), D=(no, nx), E=(no, nu), F=(no, ni), G=(nv, nx), H=(nv, nu),
                      K=(nv, ni))
        self.m = {k: np.array(info[k]).reshape(s) for k, s in shapes.items()}
        self.m["out_names"] = list(circuit.OUT_NAMES)
        self.K = self.m["K"].tolist()
        v, i = info["op_v"], info["op_i"]
        self.dc = dict(vg1k=v[0], vg2k=v[1], vg3k=v[2], vak=v[3], vgk=v[0], ip=i[0], ig2=i[1], ig1=i[2], ik=info["ik"],
                       ia=i[0], nodes=dict(zip(circuit.NODES, info["nodes"])), rk=self.rk)
        # the resampler's analytical response (validate.small_signal) is Python's; the C++ geometry must match it
        self.rs = Resampler(fs, self.os, filt, fir=K140[float(fs)] if FIR == "k140" else None)
        assert (info["latency_samples"], info["up_delay_internal"]) == (self.rs.latency_samples,
                                                                        self.rs.up_delay_internal)
        self._stats, self._used = None, False

    @property
    def latency_samples(self):
        return self.info["latency_samples"]

    @property
    def resampler_macs(self):
        return self.rs.macs()

    def process(self, vin, record=("out",)):
        if self._used:
            raise RuntimeError("CppStage: one process() call per instance")
        self._used = True
        rec = bool({"vgk", "vg2k"} & set(record))
        y, internal, info = cli(vin, self.fs_base, self.os, self.mode, self.bias, self.grid_hardness,
                                drive=self.drive, output=self.output, fir=FIR, rec=rec)
        self._stats = info["stats"]
        out = {"out": y, "_internal": {}}
        if internal is not None:
            out["_internal"]["vgk"], out["_internal"]["vg2k"] = internal
        return out

    def iteration_stats(self):
        s = self._stats
        return dict(samples=s["samples"], mean=s["iterations"] / s["samples"] if s["samples"] else 0.0, max=s["max"],
                    nonconverged=s["nonconverged"], converged=s["nonconverged"] == 0,
                    histogram={int(k): v for k, v in s["histogram"].items()})


def run(quality="standard", model="dual", bias=BIAS_MA, mode=circuit.MODE):
    """dk6as6.validate.run with the C++ core in place of the Python Stage."""
    exe()
    saved = validate.Stage
    validate.Stage = CppStage
    try:
        return validate.run(quality=quality, model=model, bias=bias, mode=mode)
    finally:
        validate.Stage = saved


# Tolerances on |C++ - Python| for every reproduced number, >= 10x the worst deviation measured over all 44 settings
# (both models, every mode and reference current, standard and hq, k140 on both sides since #134;
# cpp/results_6as6.json, Apple M1, Apple clang 17 -O2, numpy 2.5.4 on Accelerate; measured worst in the comment, its
# setting in brackets), each >= 300x below the criterion it belongs to (dk/validate.CRIT: op 1e-3 relative, AC 0.1 dB,
# harmonics 0.1 dB, burst 0.5 / 1 / 0.5 %). The largest are at hq in the experimental P2, where the slow coupling-cap
# modes integrate the ~1e-12 matrix rounding of either build (cpp/matrix_rounding_6as6.py: Python's own floor against
# exact matrices is 3.0e-7 V there, the C++ core's 3.8e-7 V).
LIMITS = dict(op_rel=1e-9,          # operating point, relative             8.6e-12
              ac_db=1e-6,           # every AC number (dB)                  3.8e-8
              harm_db=3e-4,         # H2..H5 above harm_floor_dbc (dB)      9.6e-6  [P2 0.45 mA hq; supported 1.0e-6]
              h1_rel=5e-6,          # H1 amplitude, relative                1.3e-7  [P2 0.6 mA hq; supported 2.5e-8]
              thd_rel=5e-6,         # THD, relative                         1.2e-7  [P2 0.6 mA hq; supported 9.6e-8]
              burst_v=1e-7,         # rest / min Vg1k and trajectory (V)    3.4e-9
              burst_t_s=1e-9,       # time of the Vg1k minimum (s)          0
              burst_tau_rel=2e-5,   # tail tau, relative                    5.2e-7  [P2 0.6 mA hq; supported 2.7e-7]
              burst_crit=2e-5,      # burst min_rel / tau_rel / traj_rel    5.2e-7  [P2 0.6 mA hq; supported 2.7e-7]
              iter_hist_frac=1e-3)  # Newton histograms, L1 / samples       0 (identical)


def _fin(x):
    return x is not None and np.isfinite(x)


def compare(c, p):
    """Deviations of a C++ dk6as6.validate.run result c from the Python one p (same setting)."""
    d = {}
    d["op_rel"] = max(abs(c["op"][k] / p["op"][k] - 1) for k in p["op"] if p["op"][k] != 0)
    cv, pv = c["vs"][c["model"]], p["vs"][p["model"]]
    ac = [abs(cv[k] - pv[k]) for k in ("ac_maxdev_db_stage", "ac_maxdev_db_chain")]
    ac += [abs(cv[a][f] - pv[a][f]) for a in ("ac_dev_at", "ac_chain_dev_at") for f in pv[a]]
    ac.append(abs(c["ac_gain_1k_db"] - p["ac_gain_1k_db"]))
    d["ac_db"] = max(ac)
    hd, h1, th = [], [], []
    for a, ps in p["sine"].items():
        cs = c["sine"][a]
        hd += [abs(x - y) for x, y in zip(cs["H_dBc"][1:], ps["H_dBc"][1:]) if y > validate.CRIT["harm_floor_dbc"]]
        h1.append(abs(cs["H_peak_V"][0] / ps["H_peak_V"][0] - 1))
        th.append(abs(cs["thd_pct"] / ps["thd_pct"] - 1))
        assert cs.get("spice_exp_capped", False) == ps.get("spice_exp_capped", False), a
    d["harm_db"], d["h1_rel"], d["thd_rel"] = max(hd, default=0.0), max(h1), max(th)
    d["harm_db_all"] = max(abs(x - y) for a in p["sine"]
                           for x, y in zip(c["sine"][a]["H_dBc"][1:], p["sine"][a]["H_dBc"][1:]))
    cb, pb = c["burst"], p["burst"]
    d["burst_v"] = max([abs(cb[k] - pb[k]) for k in ("rest_vgk", "min_vgk")] +
                       list(np.abs(np.subtract(cb["traj_vgk"], pb["traj_vgk"]))))
    d["burst_t_s"] = abs(cb["t_min"] - pb["t_min"])
    td, tp = validate.tail_tau(cb), validate.tail_tau(pb)
    d["burst_tau_rel"] = abs(td / tp - 1) if _fin(td) and _fin(tp) else (0.0 if not (_fin(td) or _fin(tp)) else 1.0)
    d["burst_crit"] = max(abs(cv["burst"][k] - pv["burst"][k]) for k in ("min_rel", "traj_rel")
                          if _fin(cv["burst"][k]) and _fin(pv["burst"][k]))
    if _fin(cv["burst"]["tau_rel"]) or _fin(pv["burst"]["tau_rel"]):
        d["burst_crit"] = max(d["burst_crit"], abs(cv["burst"]["tau_rel"] - pv["burst"]["tau_rel"]))
    hist = []
    for k, pi in p["iterations"].items():
        ci = c["iterations"][k]
        if FIR == PARITY_FIR:               # parity run; with the other design the latency (so the length) differs
            assert ci["samples"] == pi["samples"] and ci["nonconverged"] == pi["nonconverged"], k
        ch = {int(k): v for k, v in ci["histogram"].items()}
        ph = {int(k): v for k, v in pi["histogram"].items()}
        hist.append(sum(abs(ch.get(b, 0) - ph.get(b, 0)) for b in set(ch) | set(ph)) / pi["samples"])
    d["iter_hist_frac"] = max(hist)
    d["resampler_equal"] = (c["resampler"]["latency_samples"] == p["resampler"]["latency_samples"]
                            and list(c["resampler"]["macs_up_down"]) == list(p["resampler"]["macs_up_down"]))
    d["passed"], d["passed_python"] = c["passed"], p["passed"]
    d["status"], d["status_python"] = validate.status(c), validate.status(p)
    d["supported"] = c.get("supported", True)
    return d


def check(d):
    """[(name, value, limit)] of every deviation over its LIMIT, any criterion that reads differently from Python's,
    and any supported setting that does not pass."""
    bad = [(k, d[k], lim) for k, lim in LIMITS.items() if not d[k] <= lim]
    if not d["resampler_equal"]:
        bad.append(("resampler_equal", False, True))
    bad += [(f"passed.{k}", v, d["passed_python"][k]) for k, v in d["passed"].items() if v != d["passed_python"][k]]
    if d["supported"] and d["status"] != "passed":
        bad.append(("supported setting passes", d["status"], "passed"))
    return bad


def jobs():
    """(model, mode, bias, quality) of every committed Python result at standard and hq."""
    return [(model, mode, b, q) for model, modes in MODELS.items() for mode, bs in modes.items() for b in bs
            for q in QUALITY]


def check_crit_alone(d):
    """The shipped-FIR run has no Python counterpart (the committed Python results use k140 since #134): every
    supported setting must pass every CRIT criterion on its own; the deviations from the Python results are reported."""
    if not d["supported"] or d["status"] == "passed":
        return []
    return [("supported setting passes", d["status"], "passed")]


def one(job):
    model, mode, b, q = job
    ref = json.load(open(validate.results_file(mode, model)))[f"{b:g}"][q]
    r = run(q, model, b, mode)
    d = compare(r, ref)
    return dict(model=model, mode=mode, bias_ma=b, quality=q, fir=FIR, deviation=d,
                failed=check(d) if FIR == PARITY_FIR else check_crit_alone(d))


def _init(path, fir):
    global EXE, FIR
    EXE, FIR = path, fir


def main(workers=1, write=True, fir=PARITY_FIR):
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(exe(), fir)) as ex:
        rows = list(ex.map(one, jobs()))
    worst = {k: max(r["deviation"][k] for r in rows) for k in list(LIMITS) + ["harm_db_all"]}
    comp = subprocess.run([os.environ.get("CXX", "c++"), "--version"], capture_output=True, text=True).stdout
    out = dict(method="python -m cpp.validate_cpp_6as6: dk6as6.validate.run with the C++ core (cpp/dk6as6_core.h, "
                      "test_core_6as6 cli, -O2) in place of dk6as6.stage.Stage; deviations = |C++ - Python| against "
                      "the committed dk6as6/results*.json of the same setting",
               compiler=comp.splitlines()[0] if comp else None, limits=LIMITS, worst=worst, rows=rows)
    if fir != PARITY_FIR:
        out["method"] += (f"; resampler {fir} (not the core's DEFAULT_FIR): judged on dk/validate.CRIT alone, the "
                          "deviations from the k140 Python results are for information")
    if write:
        name = "results_6as6.json" if fir == PARITY_FIR else f"results_6as6_{fir}.json"
        json.dump(out, open(os.path.join(HERE, name), "w"), indent=1, default=float)
    for r in rows:
        print(r["model"], r["mode"], r["bias_ma"], r["quality"], r["deviation"]["status"],
              "python:", r["deviation"]["status_python"], "supported" if r["deviation"]["supported"] else "exp",
              r["failed"] or "")
    print("worst:", json.dumps(worst, default=float))
    bad = [r for r in rows if r["failed"]]
    return out, bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--fir", choices=("k140", "shipped"), default=PARITY_FIR,
                    help="k140: parity with the committed Python results; shipped: the #102-#113 resampler, CRIT alone")
    a = ap.parse_args()
    _, bad = main(a.workers, fir=a.fir)
    failed = [(r["model"], r["mode"], r["bias_ma"], r["quality"], r["failed"]) for r in bad]
    sys.exit(f"C++ core deviates from the Python results: {failed}" if bad else 0)
