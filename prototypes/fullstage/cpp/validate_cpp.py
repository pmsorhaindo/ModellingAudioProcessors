"""dk.validate on the C++ core (#19): the Python stage's reference JSON (dk/results.json) reproduced by cpp/dk_core.h.

CppStage has the part of dk.stage.Stage's interface that dk.validate.run uses, backed by `test_core cli` (the core's
Stage::process on the whole signal, plus its coefficient pack and operating point). run(quality) runs
dk.validate.run with CppStage in place of Stage, so every time-domain number (harmonics, THD, burst, Newton
iterations) comes from the C++ process(), and the small-signal numbers from the C++ matrices and operating point
(the analysis itself, dk/linear.py, and the resampler's analytical response stay Python; the C++ resampler is
checked against Python's sample by sample in cpp/test_core.cpp). compare() lists every deviation from
dk/results.json; LIMITS are the tolerances cpp/tests/test_cpp_core.py asserts.
Run from prototypes/fullstage:  python -m cpp.validate_cpp   (~10 s; builds test_core, writes cpp/results.json)
"""
import json
import os
import subprocess
import sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from dk import circuit, validate  # noqa: E402 -- after the sys.path setup
from dk.resample import Resampler  # noqa: E402 -- after the sys.path setup
from dk.stage import QUALITY  # noqa: E402 -- after the sys.path setup
from dk.triode import DZ_12AX7_RSD1, EVAL  # noqa: E402 -- after the sys.path setup
from cpp.build import CXXFLAGS, compiler, build  # noqa: E402, F401 -- after the sys.path setup; re-exported for cpp/tests

EXE = None          # test_core used by CppStage (set by run(), or by the caller)


def cli(vin, os_, prewarp_hz, bias=1.0, drive=1.0, output=1.0, math="libm", vgk=False, exe=None):
    """Stage::process on vin through `test_core cli`: (out, internal Vgk or None, info dict)."""
    vin = np.ascontiguousarray(vin, dtype=np.float64)
    args = [exe or EXE, "cli", str(os_), repr(float(prewarp_hz or 0.0)), repr(float(bias)), repr(float(drive)),
            repr(float(output)), math] + (["vgk"] if vgk else [])
    p = subprocess.run(args, input=vin.tobytes(), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"test_core cli failed ({p.returncode}): {p.stderr.decode()[-500:]}")
    info = json.loads(p.stderr.decode().strip().splitlines()[-1])
    raw = np.frombuffer(p.stdout, dtype=np.float64)
    n = len(vin)
    return raw[:n].copy(), (raw[n:].copy() if vgk else None), info


class CppStage:
    """dk.stage.Stage(fs, quality=..., filt="fir", triode_math=...) on the C++ core, for dk.validate.run. Each
    instance runs one process() call from reset (the CLI starts a fresh core), which is how validate uses Stage."""
    SHAPES = dict(A=(6, 6), B=(6, 2), C=(6, 2), D=(5, 6), E=(5, 2), F=(5, 2), G=(2, 6), H=(2, 2), K=(2, 2))

    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, triode=DZ_12AX7_RSD1, quality="standard", filt="fir",
                 newton=None, triode_math="libm"):
        if fs != 48000.0 or filt != "fir" or newton is not None or triode is not DZ_12AX7_RSD1:
            raise NotImplementedError("CppStage: 48 kHz, fir, default Newton settings and triode only")
        self.fs_base, self.bias, self.drive, self.output, self.par = fs, bias, drive, output, triode
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        self.triode_math, self.ev = triode_math, EVAL[triode_math]
        _, _, info = cli(np.zeros(0), self.os, self.prewarp_hz, bias, drive, output, triode_math)
        self.info, self.fs = info, info["fs"]
        self.m = {k: np.array(info[k]).reshape(s) for k, s in self.SHAPES.items()}
        self.m["out_names"] = ["out", "g", "k", "a", "gl"]
        self.K = self.m["K"].tolist()
        op = info["op"]
        self.dc = dict(vgk=op["vgk"], vak=op["vak"], ig=op["ig"], ia=op["ia"],
                       nodes=dict(zip(circuit.NODES, op["nodes"])))
        # the resampler's analytical response (validate.small_signal) is Python's; the C++ geometry must match it
        self.rs = Resampler(fs, self.os, filt)
        assert (info["latency_samples"], info["up_delay_internal"]) == (self.rs.latency_samples,
                                                                        self.rs.up_delay_internal)
        self._stats, self._used = None, False

    @property
    def latency_samples(self):
        return self.info["latency_samples"]

    @property
    def resampler_macs(self):
        return self.info["macs"], self.info["macs"]

    def process(self, vin, record=("out",)):
        if self._used:
            raise RuntimeError("CppStage: one process() call per instance")
        self._used = True
        y, vgk, info = cli(vin, self.os, self.prewarp_hz, self.bias, self.drive, self.output, self.triode_math,
                           vgk="vgk" in record)
        self._stats = info["stats"]
        out = {"out": y, "_internal": {}}
        if vgk is not None:
            out["_internal"]["vgk"] = vgk
        return out

    def iteration_stats(self):
        s = self._stats
        return dict(samples=s["samples"], mean=s["iterations"] / s["samples"] if s["samples"] else 0.0, max=s["max"],
                    nonconverged=s["nonconverged"], converged=s["nonconverged"] == 0,
                    histogram={int(k): v for k, v in s["histogram"].items()})


def run(quality="standard", triode_math="libm", exe=None):
    """dk.validate.run(quality) with the C++ core in place of the Python Stage."""
    global EXE
    EXE = exe or EXE or build()
    saved = validate.Stage
    validate.Stage = CppStage
    try:
        return validate.run(quality=quality, triode_math=triode_math)
    finally:
        validate.Stage = saved


# Tolerances on |C++ - Python| for every reproduced number (cpp/README.md). Set at ~100x or more the worst deviation
# measured over the three settings (cpp/results.json; measured worst in the comment); each is orders below the
# criterion it belongs to (validate.CRIT: op 1e-3 relative, AC 0.1 dB, harmonics 0.1 dB, burst 0.5 / 1 / 0.5 %).
LIMITS = dict(op_rel=1e-9,          # operating point, relative             measured 0
              ac_db=1e-6,           # every AC number (dB)                  8.8e-9
              harm_db=1e-5,         # H2..H5 above harm_floor_dbc (dB)      5.5e-8
              h1_rel=1e-7,          # H1 amplitude, relative                7.1e-10
              thd_rel=1e-6,         # THD, relative                         3.0e-9
              burst_v=1e-6,         # rest / min Vgk and trajectory (V)     5.1e-9
              burst_t_s=1e-9,       # time of the Vgk minimum (s)           0 (a sample is 2.6e-6 s at 8x)
              burst_tau_rel=1e-6,   # recovery tau, relative                0
              burst_crit=1e-7,      # burst min_rel / tau_rel / traj_rel    1.1e-10
              iter_hist_frac=1e-4)  # Newton histograms, L1 / samples       0 (identical)


def compare(c, p):
    """Deviations of a C++ validate.run result c from the Python one p (same quality)."""
    d = {}
    d["op_rel"] = max(abs(c["op"][k] / p["op"][k] - 1) for k in p["op"])
    ac = []
    for m in ("dz", "koren"):
        cv, pv = c["vs"][m], p["vs"][m]
        d[f"{m}_op_rel_diff"] = max(abs(cv["op_rel"][k] - pv["op_rel"][k]) for k in pv["op_rel"])
        ac += [abs(cv[k] - pv[k]) for k in ("ac_maxdev_db_stage", "ac_maxdev_db_chain")]
        ac += [abs(cv[a][f] - pv[a][f]) for a in ("ac_dev_at", "ac_chain_dev_at") for f in pv[a]]
    ac.append(abs(c["ac_gain_1k_db"] - p["ac_gain_1k_db"]))
    d["ac_db"] = max(ac)
    hd, h1, th = [], [], []
    for a, ps in p["sine"].items():
        cs = c["sine"][a]
        hd += [abs(x - y) for x, y in zip(cs["H_dBc"][1:], ps["H_dBc"][1:])
               if y > validate.CRIT["harm_floor_dbc"]]               # the harmonics the criteria test
        h1.append(abs(cs["H_peak_V"][0] / ps["H_peak_V"][0] - 1))
        th.append(abs(cs["thd_pct"] / ps["thd_pct"] - 1))
    d["harm_db"], d["h1_rel"], d["thd_rel"] = max(hd), max(h1), max(th)
    d["harm_db_all"] = max(abs(x - y) for a in p["sine"]
                           for x, y in zip(c["sine"][a]["H_dBc"][1:], p["sine"][a]["H_dBc"][1:]))
    cb, pb = c["burst"], p["burst"]
    d["burst_v"] = max([abs(cb[k] - pb[k]) for k in ("rest_vgk", "min_vgk")] +
                       list(np.abs(np.subtract(cb["traj_vgk"], pb["traj_vgk"]))))
    d["burst_t_s"] = abs(cb["t_min"] - pb["t_min"])
    d["burst_tau_rel"] = abs(cb["recovery_tau_s"] / pb["recovery_tau_s"] - 1)
    d["burst_crit"] = max(abs(c["vs"][m]["burst"][k] - p["vs"][m]["burst"][k])
                          for m in ("dz", "koren") for k in ("min_rel", "tau_rel", "traj_rel"))
    hist = []
    for k, pi in p["iterations"].items():
        ci = c["iterations"][k]
        assert ci["samples"] == pi["samples"] and ci["nonconverged"] == pi["nonconverged"] == 0, k
        keys = set(ci["histogram"]) | set(map(int, pi["histogram"]))
        l1 = sum(abs(ci["histogram"].get(int(b), 0) - pi["histogram"].get(str(b), 0)) for b in keys)
        hist.append(l1 / pi["samples"])
    d["iter_hist_frac"] = max(hist)
    d["resampler_equal"] = (c["resampler"]["latency_samples"] == p["resampler"]["latency_samples"]
                            and list(c["resampler"]["macs_up_down"]) == list(p["resampler"]["macs_up_down"]))
    d["pass_vs_dz"] = c["pass_vs_dz"]
    return d


def check(d):
    """[(name, value, limit)] of every deviation over its LIMIT, plus any criterion the C++ run fails."""
    bad = [(k, d[k], lim) for k, lim in LIMITS.items() if not d[k] <= lim]
    if not d["resampler_equal"]:
        bad.append(("resampler_equal", False, True))
    bad += [(f"pass_vs_dz.{k}", v, True) for k, v in d["pass_vs_dz"].items() if not v]
    return bad


if __name__ == "__main__":
    ref = json.load(open(os.path.join(ROOT, "dk", "results.json")))
    out = dict(method="python -m cpp.validate_cpp: dk.validate.run with the C++ core (cpp/dk_core.h, test_core cli, "
                      "gcc -O2) in place of dk.stage.Stage; deviations = |C++ - Python| against dk/results.json",
               compiler=subprocess.run([compiler(), "--version"], capture_output=True,
                                       text=True).stdout.splitlines()[0],
               limits=LIMITS, settings={})
    for q in QUALITY:
        r = run(q)
        d = compare(r, ref[q])
        out["settings"][q] = dict(deviation=d, failed=check(d), pass_vs_dz=r["pass_vs_dz"],
                                  op=r["op"], ac_maxdev_db_chain_vs_dz=r["vs"]["dz"]["ac_maxdev_db_chain"],
                                  iterations={k: dict(mean=v["mean"], max=v["max"], nonconverged=v["nonconverged"])
                                              for k, v in r["iterations"].items()})
        print(q, json.dumps(d, default=float))
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=float)
    bad = {q: v["failed"] for q, v in out["settings"].items() if v["failed"]}
    sys.exit(f"C++ core deviates from dk/results.json: {bad}" if bad else 0)
