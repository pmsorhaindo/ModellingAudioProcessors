"""#131 (bounds only): the #128 burst, blocking, recovery and THD checks at 44.1 kHz, T 0.1-0.5 mA, for the nominal
grid shape and each published bound. Estimated, unvalidated.

At 48 kHz these are dk6as6/validate.py's own records: `python -m dk6as6.validate T --grid` (RSD-1,
results_grid_T.json) and `python -m dk6as6.validate --bounds` (RSD-2 / EHX-1, results_grid_{rsd2,ehx1}_T.json), at
run_refs.GRID_BIASES["T"] = run_refs.BOUND_BIASES["T"]. validate.py runs at its module-level FS (48 kHz), so this
module runs the same validate.run, unchanged, with FS set to 44.1 kHz in each worker: the DK stage (standard and hq,
the k140 resampler) at 44.1 kHz against the same continuous-time ngspice references (spice6as6/reference_dualg[_<bound>]
_T_<bias>mA.json), with the same signals (1 kHz sines at run_refs.AMPS, the 4 V 1 kHz burst 10-30 ms) and the same
criteria (dk/validate.CRIT). Settings outside stage.SUPPORTED_MA (every bound) run as experimental, as in validate.py.
Run (from prototypes/fullstage): python -m dk6as6.grid_bounds_burst [--workers N]
  -> dk6as6/results_grid_bounds_T_44100.json
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk6as6 import validate as V  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import QUALITY  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

FS = 44100.0
MODE = "T"
MODELS = {"RSD-1": "dualg", **{s: m for m, s in R.GRID_SHAPE.items()}}       # shape -> validate / run_refs model
BIASES = tuple(b for b in R.BOUND_BIASES[MODE] if b <= 0.5)                     # 0.1-0.5 mA (the supported T range)
JSON = os.path.join(HERE, f"results_grid_bounds_{MODE}_{int(FS)}.json")
LABEL = "estimated, unvalidated"


def _run(a):
    shape, bias, quality, fs = a
    V.FS = fs                                        # validate.run reads its module-level FS at call time
    r = V.run(quality=quality, bias=bias, mode=MODE, model=MODELS[shape])
    r["shape"] = shape
    return r


def jobs(fs=FS, shapes=tuple(MODELS), biases=BIASES):
    return [(s, b, q, fs) for b in biases for s in shapes for q in QUALITY]


def main(workers=None, fs=FS, out=JSON, shapes=tuple(MODELS)):
    """Run `shapes` and merge them into `out` (other shapes' results kept)."""
    js = jobs(fs, shapes)
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_run, js))
    rec = json.load(open(out)) if os.path.exists(out) else dict(label=LABEL, fs_base=fs, mode=MODE, models=MODELS,
                                                                 results={})
    for s in shapes:
        rec["results"].pop(s, None)
    for (s, b, q, _), r in zip(js, res):
        rec["results"].setdefault(s, {}).setdefault(f"{b:g}", {})[q] = r
    json.dump(rec, open(out, "w"), indent=1, default=float)
    return {s: {b: {q: V.status(r) for q, r in rb.items()} for b, rb in sb.items()} for s, sb in rec["results"].items()}


# --- the 8 V burst (PR #151 review: the issue asks for the #128 checks "at 8 V") ---------------------------------
BURST8_JSON = os.path.join(HERE, f"results_grid_bounds_{MODE}_burst8V.json")
BURST8_RATES = (48000.0, 44100.0)
BURST8_AMP = R.BURST_AMPS[0]


def _burst8(a):
    """DK `BURST8_AMP` V burst (validate.burst, unchanged but for the amplitude) at one rate and quality, against the
    case's own 8 V ngspice reference (run_refs.burst_ref_file): validate.burst_compare and burst_ok (dk/validate.CRIT),
    and the op point against the reference's (CRIT op_rel)."""
    shape, bias, quality, fs = a
    V.FS = fs
    model = MODELS[shape]
    m, its = V.burst(quality, model=model, bias=bias, mode=MODE, amp=BURST8_AMP)
    st = V.stage(model, bias=bias, quality=quality, mode=MODE)
    op = V.op_record(st)
    out = dict(shape=shape, model=model, bias_ma=bias, quality=quality, fs_base=fs, burst_amp_v=BURST8_AMP,
               oversampling=st.os, rk=st.rk, grid=st.grid, op=op, burst=m, iterations=its,
               supported=bool(st.supported))
    fn = R.burst_ref_file(model, bias, MODE, BURST8_AMP)
    if not os.path.exists(fn):          # DK only until its reference exists: nothing compared, never a pass
        return dict(out, reference_pending=True, vs=None, op_rel=None,
                    passed=dict(op=None, burst=None, newton=its["converged"]))
    ref = json.load(open(fn))
    cmp = V.burst_compare(m, ref["burst"])
    op_rel = {k: abs(op[k] / ref["op"][k] - 1) for k in ("ia", "ig2", "ik", "vgk")}
    passed = dict(op=all(v < V.CRIT["op_rel"] for v in op_rel.values()),
                  burst=None if cmp.get("spice_exp_capped") else V.burst_ok(cmp), newton=its["converged"])
    return dict(out, op_rel=op_rel, vs=cmp, passed=passed)


def burst8_main(workers=None, out=BURST8_JSON, shapes=tuple(MODELS), biases=BIASES):
    """Run every (rate, shape, bias, quality) not yet in `out`, and re-run a DK-only ("reference_pending") entry once
    its 8 V reference exists; merge into `out`. A case whose reference is still missing is recorded DK-only."""
    rec = json.load(open(out)) if os.path.exists(out) else dict(label=LABEL, mode=MODE, burst_amp_v=BURST8_AMP,
                                                                 models=MODELS, criteria=V.CRIT, results={})
    def done(s, b, q, fs):
        r = rec["results"].get(f"{int(fs)}", {}).get(s, {}).get(f"{b:g}", {}).get(q)
        return r is not None and not (r.get("reference_pending")
                                      and os.path.exists(R.burst_ref_file(MODELS[s], b, MODE, BURST8_AMP)))
    js = [(s, b, q, fs) for fs in BURST8_RATES for b in biases for s in shapes for q in QUALITY
          if not done(s, b, q, fs)]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_burst8, js))
    for (s, b, q, fs), r in zip(js, res):
        rec["results"].setdefault(f"{int(fs)}", {}).setdefault(s, {}).setdefault(f"{b:g}", {})[q] = r
    json.dump(rec, open(out, "w"), indent=1, default=float)
    return {fs: {s: {b: {q: V.status(r) for q, r in rb.items()} for b, rb in sb.items()} for s, sb in d.items()}
            for fs, d in rec["results"].items()}


def burst8_tables(path=BURST8_JSON):
    """The 8 V burst table per rate (DK standard; vs ngspice, standard / hq) and the residual blocking."""
    from dk6as6 import grid as GR
    rec = json.load(open(path))
    L = []
    for fs, d in rec["results"].items():
        L += [f"**{int(fs) / 1000:g} kHz, {rec['burst_amp_v']:g} V burst**", "",
              "| T, bias | shape | rest | shift | at 30 / 35 / 50 / 118.5 ms | tail τ | residual at 35 / 118.5 ms, mV "
              "| vs ngspice (std / hq) |", "|---|---|---|---|---|---|---|---|"]
        for b in BIASES:
            for s in MODELS:
                rb = d.get(s, {}).get(f"{b:g}")
                if rb is None:
                    continue
                row = GR._burst_row(rb["standard"]["burst"])
                traj = " / ".join(f"{row[f'vgk_{ms:g}ms']:.3f}" for ms in GR.TRAJ_MS)
                res = " / ".join(f"{1e3 * (row[f'vgk_{ms:g}ms'] - row['rest_vgk']):.2f}" for ms in (35.0, 118.5))
                st = " / ".join("DK only (reference pending)" if rb[q].get("reference_pending") else V.status(rb[q])
                                for q in QUALITY if q in rb)
                L.append(f"| {b:g} mA | {s} | {row['rest_vgk']:.4f} | {row['shift_v']:.3f} | {traj} | "
                         f"{1e3 * row['tail_tau_s']:.1f} ms | {res} | {st} |")
        L.append("")
    return "\n".join(L)


def tables(path=JSON):
    """The RESULTS.md burst / blocking / THD tables at 44.1 kHz (grid_bounds.burst_thd_tables' format)."""
    from dk6as6 import grid_bounds
    rec = json.load(open(path))
    return "\n".join(grid_bounds.burst_thd_tables(rec["results"], BIASES))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    ap.add_argument("--shapes", nargs="*", default=list(MODELS))
    ap.add_argument("--tables", action="store_true")
    ap.add_argument("--burst8", action="store_true", help="the 8 V burst at both rates -> " + BURST8_JSON)
    a = ap.parse_args()
    if a.burst8:
        print(burst8_tables() if a.tables else json.dumps(burst8_main(a.workers, shapes=tuple(a.shapes))))
    else:
        print(tables() if a.tables else json.dumps(main(a.workers, shapes=tuple(a.shapes))))
