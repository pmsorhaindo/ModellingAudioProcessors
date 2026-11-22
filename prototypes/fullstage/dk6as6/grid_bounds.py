"""Bounds on the borrowed g1 grid-current law (#131, part a): how far the #112 results move between DZ's three
measured 12AX7s.

The 6AS6 stage's grid law (dk6as6/pentode.py, grid()) borrows its shape from Dempwolf & Zoelzer, "A physically-
motivated triode model for circuit simulations", DAFx-11 (https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf),
Table 1 ("Individually fitted parameters for 12AX7 triodes"), column RSD-1. No 6AS6 positive-g1 data exists (#131: the
measurement is parked, no tube available), so the table's other two columns, RSD-2 and EHX-1, scaled the same way
(Gg = (2 / KG1) / G x Gg of that column, XI and Cg as given), serve as BOUNDS: pentode.GRID_BOUNDS,
Stage(grid_shape=...). RSD-1 stays the nominal default, unchanged. All three are estimated, unvalidated.

Parts (each per bound shape; the nominal RSD-1 numbers are read from the committed #112 / #113 records, which the same
functions reproduce bit for bit, dk6as6/tests/test_dk6as6_grid_bounds.py):
  "law"      the parameters, Ig1 at LAW_VG1K, and the T operating points at T_BIASES (Rk, rest Vg1k, Ig1 at rest).
  "envelope" #112's envelope checks at T_BIASES: the 1 kHz tones (grid._tone, grid.ENV_DRIVES), the 8 V tone onsets
             (grid._onset, grid.ONSET_TONES, standard and hq), the steady-state band scan (grid._hf, grid.HF_*) and
             #121's stimulus class (envelope.excursion, envelope.FREQS x PHASES, standard and hq).
  "aliasing" dk6as6/aliasing.measure (1 s windows after 1.25 s, 8x, the shipped FIR, 48 kHz) at T_ALIAS_BIASES x
             2 / 8 V: the top five tones (where every full sweep of this stage has had its worst) and, at T 0.5 mA
             (T's top), the full 19-tone sweep; and #113's CI guard T keys (GUARD_T_KEYS: aliasing_guard.CI before
             #134 moved it to k140, both rates, its CI window) for the guard's limits.
The burst / blocking / recovery and THD checks are dk6as6/validate.py --bounds (DK against the bound shapes' own
same-model ngspice references, spice6as6/run_refs.py dualg_rsd2/T dualg_ehx1/T) -> results_grid_{rsd2,ehx1}_T.json;
"tables" prints every RESULTS.md table from those files and grid_bounds.json.
Run: python -m dk6as6.grid_bounds [law] [envelope] [aliasing] [tables] [--shapes RSD-2 EHX-1] [--workers N]
     (from prototypes/fullstage) -> dk6as6/grid_bounds.json (each part replaces its own key per shape).
"""
import argparse
import fcntl
import json
import os
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.aliasing import SWEEP_HZ, DRIVES, CI_WINDOW_S  # noqa: E402 -- after the sys.path setup
from dk6as6 import pentode, grid as GR, envelope as EN, aliasing as A6, aliasing_guard as AG  # noqa: E402
from dk6as6 import validate as V  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import cathode_resistor, operating_point, QUALITY  # noqa: E402 -- after the sys.path setup
from spice6as6 import run_refs as R  # noqa: E402 -- after the sys.path setup

JSON = os.path.join(HERE, "grid_bounds.json")
SHAPES = pentode.GRID_BOUNDS
LABEL = "estimated, unvalidated"          # every grid-current number here (#131)
SOURCE = ("Dempwolf & Zoelzer, DAFx-11, Table 1 (individually fitted parameters for 12AX7 triodes), column {}: G, Gg, "
          "xi, Cg; Gg scaled by the 6AS6 fit's cathode coefficient (2 / KG1) over that column's G (dk6as6/pentode.py)")
LAW_VG1K = (-1.5, -1.0, -0.5, -0.16, -0.05, 0.0, 0.5, 1.0, 2.0)
ONSET_A = (1e-6, 1e-5, 2e-5)        # A: onset thresholds, Vg1k at which Ig1 reaches each (PR #139 review)
T_BIASES = (0.35, 0.4, 0.45, 0.5, 0.55, 0.6)        # mA: where the nominal grid current moves T's results (#112)
T_ALIAS_BIASES = (0.45, 0.5, 0.55, 0.6)             # around T's top (0.5 mA), which the 2 V aliasing gate sets
TOP_TONES = SWEEP_HZ[-5:]                           # 15037 ... 19037 Hz
FULL_SWEEP_BIAS = 0.5
# The study was measured with the shipped FIR (#113's guard at 92d1b3d). It keeps that guard's T keys, CI tones,
# limits and nominal (RSD-1) baseline values, so grid_bounds.json stays reproducible and compares like with like; the
# live guard (aliasing_guard.CI, k140 since #134) is not this study's configuration.
FIR = GR.FIR                                        # "shipped": the resampler of grid.json and grid_bounds.json
GUARD_T_KEYS = {                                    # #113's aliasing_guard.CI T keys at 92d1b3d: (limit dBc, tones)
    48000: {("T", 0.5, "4x", 2.0): (-101.8, (19637, 19437, 19237)),
            ("T", 0.5, "4x", 8.0): (-40.8, (19987, 19937, 19837)),
            ("T", 0.5, "standard", 8.0): (-60.3, (19987, 19937, 19837)),
            ("T", 0.5, "hq", 8.0): (-80.7, (19637, 19437, 19237))},
    44100: {("T", 0.5, "4x", 2.0): (-97.3, (19987, 19937, 19637)),
            ("T", 0.5, "4x", 8.0): (-39.4, (19637, 19437, 19237)),
            ("T", 0.5, "standard", 8.0): (-56.4, (19437, 19237, 19037)),
            ("T", 0.5, "hq", 8.0): (-77.3, (19987, 19937, 19837))},
}
NOMINAL_GUARD_JSON = os.path.join(HERE, "grid_bounds_nominal_guard.json")   # those keys' RSD-1 baseline values


def guard_key(mode, bias, setting, drive):
    """The study's guard key: #113's format, naming the shipped design "fir" (aliasing_guard.key names k140)."""
    return f"{mode}/{bias:g}mA/{setting}/fir/{drive:g}V"


# --- law ----------------------------------------------------------------------------------------------------------

def operating(bias, shape):
    """T's DC operating point at `bias` mA with grid shape `shape`: Rk, Vg1k, Vak and Ig1 at rest."""
    rk = cathode_resistor(bias, mode="T", grid_shape=shape)
    dc = operating_point(rk, mode="T", grid_shape=shape)
    return dict(bias_ma=bias, rk=float(rk), vg1k=float(dc["vg1k"]), vak=float(dc["vak"]), ig1=float(dc["ig1"]))


def onset_v(g, ig):
    """The Vg1k (V) at which Ig1 = GG (softplus(CG Vg1k) / CG)^XI reaches `ig` A (monotonic; bisection to 1e-12 V)."""
    from scipy.optimize import brentq
    return float(brentq(lambda v: pentode.grid_current(v, g)[0] - ig, -5.0, 5.0, xtol=1e-12))


def crossings(g, g0, lo=-1.5, hi=2.0, step=1e-3):
    """The Vg1k (V) in [lo, hi] where Ig1 of g equals that of g0 (sign changes on a 1 mV grid, refined by bisection)."""
    from scipy.optimize import brentq
    f = lambda v: pentode.grid_current(v, g)[0] - pentode.grid_current(v, g0)[0]          # noqa: E731
    xs = [lo + k * step for k in range(int(round((hi - lo) / step)) + 1)]
    return [float(brentq(f, a, b, xtol=1e-12)) for a, b in zip(xs, xs[1:]) if f(a) * f(b) < 0]


def law(shape):
    g = pentode.grid(shape=shape)
    return dict(shape=shape, label=LABEL, source=SOURCE.format(shape), table1=pentode.GRID_SHAPES[shape], grid=g,
                ig1_a={f"{v:g}": pentode.grid_current(v, g)[0] for v in LAW_VG1K},
                onset_v={f"{i:g}": onset_v(g, i) for i in ONSET_A},
                crossings_with_nominal_v=crossings(g, pentode.GRID),
                t_operating_points=[operating(b, shape) for b in T_BIASES])


# --- envelope -----------------------------------------------------------------------------------------------------

def _class(a):
    return EN.excursion(*a)


def envelope(shape, workers=None):
    tones = [("T", b, d, shape) for b in T_BIASES for d in GR.ENV_DRIVES]
    onsets = [("T", b, f, q, shape) for b in T_BIASES for f in GR.ONSET_TONES for q in QUALITY]
    hf = [("T", b, d, f, shape) for b in GR.HF_BIASES for d in GR.HF_DRIVES for f in GR.HF_TONES]
    cls = [("T", b, q, f, ph, pentode.HARDNESS, shape) for b in T_BIASES for q in QUALITY for f in EN.FREQS
           for ph in EN.PHASES]
    with ProcessPoolExecutor(workers) as ex:
        t = list(ex.map(GR._tone, tones))
        o = list(ex.map(GR._onset, onsets))
        h = list(ex.map(GR._hf, hf))
        c = list(ex.map(_class, cls))
    for r in t + o + h:
        r["in_law"] = GR.in_law(r) and r["nonconverged"] == 0
        r["grid_shape"] = shape
    return dict(shape=shape, label=LABEL, tones=t, onsets=o, hf=h, stimulus_class=c,
                stimulus_class_summary={f"{b:g}": w for b, w in EN.summary(c)["T"].items()})


# --- aliasing -----------------------------------------------------------------------------------------------------

def _alias(a):
    kw = dict(a)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return dict(A6.measure(kw.pop("drive"), kw.pop("tone"), **kw), **({"setting_key": a["setting_key"]}
                                                                          if "setting_key" in a else {}))


def alias_jobs(shape):
    jobs = []
    for b in T_ALIAS_BIASES:
        tones = SWEEP_HZ if b == FULL_SWEEP_BIAS else TOP_TONES
        jobs += [dict(drive=d, tone=f, bias=b, mode="T", grid_shape=shape, fir=FIR) for d in DRIVES for f in tones]
    guard = []
    for fs, keys in GUARD_T_KEYS.items():
        for (m, b, s, d), (_, tones) in keys.items():
            guard += [dict(drive=d, tone=f, bias=b, mode=m, grid_shape=shape, quality=AG.QUALITY[s], fs=fs, fir=FIR,
                           window=CI_WINDOW_S, settle=A6.SETTLE_S, setting_key=guard_key(m, b, s, d)) for f in tones]
    return jobs, guard


def aliasing(shape, workers=None):
    jobs, guard = alias_jobs(shape)
    allj = sorted(guard, key=lambda j: -AG.OS[j["setting_key"].split("/")[2]]) + jobs     # the slow (hq) points first
    with ProcessPoolExecutor(workers) as ex:
        out = list(ex.map(_alias, [{k: v for k, v in j.items() if k != "setting_key"} for j in allj]))
    for j, r in zip(allj, out):
        if "setting_key" in j:
            r["setting_key"] = j["setting_key"]
    sweep = [r for r in out if "setting_key" not in r]
    g = [r for r in out if "setting_key" in r]
    return dict(shape=shape, label=LABEL, sweep=sweep, guard=g, gate_dbc=A6.QUALITY_GATE_DBC)


# --- tables -------------------------------------------------------------------------------------------------------

def _worst(rows, **kw):
    sel = [r for r in rows if all(r.get(k) == v for k, v in kw.items())]
    return max(sel, key=lambda r: r["total_dbc"]) if sel else None


def nominal():
    """The nominal (RSD-1) records the bounds are compared with: #112's grid.json, envelope_grid.json,
    aliasing_grid_T.json, and #113's shipped-FIR guard baselines (CI window; grid_bounds_nominal_guard.json)."""
    g = json.load(open(GR.JSON))
    e = json.load(open(EN.JSON_GRID))
    a = json.load(open(A6.json_file("T", grid=True)))
    base = {int(fs): v for fs, v in json.load(open(NOMINAL_GUARD_JSON))["values"].items()}
    return dict(tones=[r for r in g["envelope"]["tones"] if r["mode"] == "T"],
                onsets=[r for r in g["envelope"]["onsets"] if r["mode"] == "T"], hf=g["hf"]["rows"],
                stimulus_class=[r for r in e["rows"] if r["mode"] == "T"], sweep=a, guard=base)


def tables():
    out = json.load(open(JSON))
    nom = nominal()
    shapes = ("RSD-1",) + SHAPES
    lines = []
    # law
    lines.append("| Vg1k | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 1) + "|")
    for v in LAW_VG1K:
        lines.append(f"| {v:+g} V | " + " | ".join(
            f"{pentode.grid_current(v, pentode.grid(shape=s))[0]:.3g} A" for s in shapes) + " |")
    lines.append("")
    lines.append("| onset threshold | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 1) + "|")
    for i in ONSET_A:
        lines.append(f"| {1e6 * i:g} µA | " + " | ".join(
            f"{1e3 * onset_v(pentode.grid(shape=s), i):+.1f} mV" for s in shapes) + " |")
    lines.append("")
    for s in SHAPES:
        lines.append(f"{s} crosses RSD-1 at " + ", ".join(
            f"{1e3 * v:+.1f} mV" for v in crossings(pentode.grid(shape=s), pentode.GRID)))
    lines.append("")
    # 1 kHz tones
    env = {s: out["envelope"][s] for s in SHAPES if s in out.get("envelope", {})}
    def tone(rows, b, d):
        return next((r for r in rows if r["bias_ma"] == b and r["drive"] == d), None)
    lines.append("| T, bias, drive | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 1) + "|")
    for b in T_BIASES:
        for d in GR.ENV_DRIVES:
            cells = []
            for s in shapes:
                r = tone(nom["tones"] if s == "RSD-1" else env.get(s, {}).get("tones", []), b, d)
                cells.append("—" if r is None else f"{r['vg1k_max']:+.2f} V, {r['vak_min']:.1f} V, "
                                                   f"{r['thd_pct']:.2f} %, Ig1 {1e3 * r['ig1_max']:.3f} mA")
            lines.append(f"| {b:g} mA, {d:g} V | " + " | ".join(cells) + " |")
    lines.append("")
    # stimulus class / onsets / hf: worst per bias
    lines.append("| T, bias | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 1) + "|")
    for b in T_BIASES:
        cells = []
        for s in shapes:
            rows = nom["stimulus_class"] if s == "RSD-1" else env.get(s, {}).get("stimulus_class", [])
            rr = [r for r in rows if r["bias_ma"] == b]
            if not rr:
                cells.append("—")
                continue
            cells.append(f"{max(r['vg1k_max'] for r in rr):+.3f} V, {min(r['vak_min'] for r in rr):.1f} V, "
                         f"{1e6 * min(r['ipk_min'] for r in rr):.2g} µA, in law {all(EN.in_law(r) for r in rr)}")
        lines.append(f"| {b:g} mA | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append("| T, bias (onsets / hf) | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 1) + "|")
    for b in T_BIASES:
        cells = []
        for s in shapes:
            src = nom if s == "RSD-1" else env.get(s, {})
            on = [r for r in src.get("onsets", []) if r["bias_ma"] == b]
            hf = [r for r in src.get("hf", []) if r["bias_ma"] == b]
            c = []
            if on:
                c.append(f"onsets {max(r['vg1k_max'] for r in on):+.3f} V, {min(r['vak_min'] for r in on):.1f} V, "
                         f"it ≤ {max(r['it_max'] for r in on)}, nc {sum(r['nonconverged'] for r in on)}")
            if hf:
                c.append(f"hf {min(r['vak_min'] for r in hf):.1f} V, {1e6 * min(r['ipk_min'] for r in hf):.2g} µA, "
                         f"it ≤ {max(r['it_max'] for r in hf)}, nc {sum(r['nonconverged'] for r in hf)}, "
                         f"restarts {sum(r['restarted'] for r in hf)}")
            cells.append("; ".join(c) or "—")
        lines.append(f"| {b:g} mA | " + " | ".join(cells) + " |")
    lines.append("")
    # aliasing sweep
    al = {s: out["aliasing"][s] for s in SHAPES if s in out.get("aliasing", {})}
    lines.append("| T, bias, drive (tones) | gate | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 2) + "|")
    for b in T_ALIAS_BIASES:
        tones = SWEEP_HZ if b == FULL_SWEEP_BIAS else TOP_TONES
        for d in DRIVES:
            cells = []
            for s in shapes:
                rows = nom["sweep"] if s == "RSD-1" else al.get(s, {}).get("sweep", [])
                rows = [r for r in rows if r["tone"] in tones]
                w = _worst(rows, bias_ma=b, drive=d)
                cells.append("—" if w is None else f"{w['total_dbc']:.2f} at {w['tone']} Hz "
                             f"({'internal' if w['internal_dbc'] >= w['resampler_dbc'] else 'resampler'}; "
                             f"{'pass' if w['total_dbc'] <= A6.QUALITY_GATE_DBC[d] else 'FAIL'})")
            lines.append(f"| {b:g} mA, {d:g} V ({'full' if b == FULL_SWEEP_BIAS else 'top 5'}) | "
                         f"{A6.QUALITY_GATE_DBC[d]} | " + " | ".join(cells) + " |")
    lines.append("")
    # guard keys
    lines.append("| rate, guard key | limit | " + " | ".join(shapes) + " |")
    lines.append("|---" * (len(shapes) + 2) + "|")
    for fs, keys in GUARD_T_KEYS.items():
        for k, (lim, tones) in keys.items():
            kk = guard_key(*k)
            cells = []
            for s in shapes:
                if s == "RSD-1":
                    v = max(t["total_dbc"] for t in nom["guard"][fs][kk]["tones"].values())
                else:
                    rr = [r for r in al.get(s, {}).get("guard", []) if r["setting_key"] == kk and r["fs_base"] == fs]
                    v = max(r["total_dbc"] for r in rr) if rr else None
                cells.append("—" if v is None else f"{v:.2f} ({'pass' if v <= lim else 'OVER'})")
            lines.append(f"| {fs / 1000:g} kHz, {kk} | {lim} | " + " | ".join(cells) + " |")
    lines.append("")
    lines += burst_thd_tables()
    return "\n".join(lines)


def _results(model):
    fn = V.results_file("T", model)
    return json.load(open(fn)) if os.path.exists(fn) else {}


def burst_thd_tables():
    """Burst (grid DC, shift, tail tau) and THD per shape at run_refs.BOUND_BIASES, DK standard, with the status of
    each DK run against its own same-model ngspice reference."""
    models = (("RSD-1", "dualg"),) + tuple((R.GRID_SHAPE[m], m) for m in R.GRID_SHAPE)
    res = {s: _results(m) for s, m in models}
    lines = ["| T, bias | shape | rest | shift | at 30 / 35 / 50 / 118.5 ms | tail τ | vs ngspice (std / hq) |",
             "|---|---|---|---|---|---|---|"]
    for b in R.BOUND_BIASES["T"]:
        for s, m in models:
            rb = res[s].get(f"{b:g}")
            if rb is None:
                lines.append(f"| {b:g} mA | {s} | — | — | — | — | — |")
                continue
            row = GR._burst_row(rb["standard"]["burst"])
            traj = " / ".join(f"{row[f'vgk_{ms:g}ms']:.3f}" for ms in GR.TRAJ_MS)
            st = " / ".join(V.status(rb[q]) for q in QUALITY if q in rb)
            lines.append(f"| {b:g} mA | {s} | {row['rest_vgk']:.4f} | {row['shift_v']:.3f} | {traj} | "
                         f"{1e3 * row['tail_tau_s']:.1f} ms | {st} |")
    lines.append("")
    lines.append("| T, bias | residual blocking (Vgk - rest), mV, at 35 ms: " + " / ".join(s for s, _ in models)
                 + " | at 118.5 ms |")
    lines.append("|---|---|---|")
    for b in R.BOUND_BIASES["T"]:
        rows = [GR._burst_row(res[s][f"{b:g}"]["standard"]["burst"]) if f"{b:g}" in res[s] else None for s, _ in models]
        cell = lambda ms: " / ".join("—" if r is None else f"{1e3 * (r[f'vgk_{ms:g}ms'] - r['rest_vgk']):.2f}"  # noqa: E731
                                     for r in rows)
        lines.append(f"| {b:g} mA | {cell(35.0)} | {cell(118.5)} |")
    lines.append("")
    lines.append("| T, bias | shape | " + " | ".join(f"{a} V" for a in R.AMPS) + " |")
    lines.append("|---|---|" + "---|" * len(R.AMPS))
    for b in R.BOUND_BIASES["T"]:
        for s, m in models:
            rb = res[s].get(f"{b:g}")
            if rb is None:
                continue
            sn = rb["standard"]["sine"]
            lines.append(f"| {b:g} mA | {s} | " + " | ".join(
                f"{sn[a]['thd_pct']:.4g} % (H2 {sn[a]['H_dBc'][1]:.1f}, H3 {sn[a]['H_dBc'][2]:.1f}, "
                f"H4 {sn[a]['H_dBc'][3]:.1f})" for a in R.AMPS) + " |")
    return lines


def publish(part, shape, record, fn=JSON):
    """Merge one part's record for one shape into `fn` (PR #139 review): the read / merge / write holds an exclusive
    lock (<fn>.lock), so concurrent runs of different parts or shapes never drop each other's updates, and the new
    file is written to a temporary name and renamed over `fn` (atomic, one filesystem), so a reader sees the old or
    the new file whole, never a partial one."""
    with open(fn + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)                 # released when the file closes
        out = json.load(open(fn)) if os.path.exists(fn) else {}
        out.setdefault("label", LABEL)
        out.setdefault(part, {})[shape] = record
        tmp = f"{fn}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(out, f, indent=1, default=float)
        os.replace(tmp, fn)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("parts", nargs="*", default=["law", "envelope", "aliasing"])
    ap.add_argument("--shapes", nargs="*", default=list(SHAPES))
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    for p in a.parts:
        if p == "tables":
            print(tables())
            continue
        for s in a.shapes:
            if s not in SHAPES:
                sys.exit(f"{s} is not a #131 bound shape {SHAPES}")
            r = law(s) if p == "law" else {"envelope": envelope, "aliasing": aliasing}[p](s, a.workers)
            publish(p, s, r)
            print(p, s, "done")
