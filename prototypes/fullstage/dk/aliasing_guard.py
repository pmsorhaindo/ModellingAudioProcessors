"""CI aliasing guard (#35): the lean aliasing subset for every setting x filter x drive, checked two ways.

1. Absolute limit: each setting / filter / drive's worst total must stay at or below aliasing.limits_for_rate(fs).
2. Regression ratchet: it must not read more than TOLERANCE_DB worse than the committed baseline
   (dk/aliasing_baseline.json), even under the limit. Reading more than TOLERANCE_DB better passes, with a note
   suggesting a baseline update so the ratchet tightens.

The subset is aliasing.CI_TONES with aliasing.CI_SETTLE_S / CI_WINDOW_S buffers (18 points), run in parallel
processes at 48 kHz; --fs 44100 uses CI_TONES_44100 (11 FIR points) and a separate baseline (#96).
Delta = measured - baseline (positive = more aliasing); margin = limit - measured (positive = under).
The simulation is deterministic, so TOLERANCE_DB is not noise headroom. It covers floating-point differences
between platforms and library versions and changes too small to matter. Losing one octave of oversampling costs
14-19 dB at 8 V and a weakened half-band shows directly in the 2 V resampler floor, so a real regression fails.

Run (from prototypes/fullstage):
  python -m dk.aliasing_guard [--workers N] [--json out.json]        check; exit status 1 on any failure
  python -m dk.aliasing_guard --fs 44100                           44.1 kHz FIR budget and baseline
  python -m dk.aliasing_guard --update-baseline --reason "..."       rewrite the baseline (refused over a limit)
Writes a markdown table to $GITHUB_STEP_SUMMARY when that is set. The CI job `aliasing` runs the check.
"""
import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk import aliasing as AL  # noqa: E402 -- after the sys.path setup

BASELINE_JSON = os.path.join(HERE, "aliasing_baseline.json")
BASELINE_44100_JSON = os.path.join(HERE, "aliasing_baseline_44100.json")
TOLERANCE_DB = 1.5


def key(q, flt, d):
    return f"{q}/{flt}/{d:g}V"


def run_subset(workers=None, triode_math="libm", fs=AL.FS):
    """measure() at every CI tone of every setting / filter / drive; a flat list of result dicts."""
    jobs = [(q, flt, d, f, dict(window=AL.CI_WINDOW_S, settle=AL.CI_SETTLE_S, triode_math=triode_math, fs=fs))
            for (q, flt), tones in AL.ci_tones_for_rate(fs).items() for d, freqs in tones.items() for f in freqs]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(AL._job, jobs))
    return [AL._job(j) for j in jobs]


def worst_by_key(records, fs=AL.FS):
    """{key: dict(total_dbc, tone, f_hz, tones)} for every setting / filter / drive in records."""
    out = {}
    for (q, flt), tones in AL.ci_tones_for_rate(fs).items():
        for d in tones:
            sel = AL.select(records, q, flt, d)
            if sel:
                w = max(sel, key=lambda r: r["total_dbc"])
                out[key(q, flt, d)] = dict(total_dbc=w["total_dbc"], tone=w["tone"], f_hz=w["f_hz"],
                                           tones=sorted(r["tone"] for r in sel))
    return out


def compare(measured, baseline, limits, tol=TOLERANCE_DB):
    """One row per key in `limits` ({key: limit dBc}). measured / baseline: {key: dict(total_dbc, tones, ...)}.
    status: "FAIL limit" (over the absolute limit), "FAIL regression" (worse than baseline by more than tol),
    "FAIL no baseline" (no baseline entry, or one measured on other tones), "FAIL not measured",
    "improved" (better than baseline by more than tol: passes, suggests a baseline update), or "ok"."""
    rows = []
    for k, lim in limits.items():
        m, b = measured.get(k), baseline.get(k)
        row = dict(key=k, limit_dbc=lim, measured_dbc=None, baseline_dbc=None, delta_db=None, margin_db=None)
        if m is None:
            rows.append(dict(row, status="FAIL not measured"))
            continue
        row.update(measured_dbc=m["total_dbc"], tone=m.get("tone"), margin_db=lim - m["total_dbc"])
        if b is not None and b.get("tones", m.get("tones")) == m.get("tones"):
            row.update(baseline_dbc=b["total_dbc"], delta_db=m["total_dbc"] - b["total_dbc"])
        if m["total_dbc"] > lim:
            status = "FAIL limit"
        elif row["delta_db"] is None:
            status = "FAIL no baseline"
        elif row["delta_db"] > tol:
            status = "FAIL regression"
        elif row["delta_db"] < -tol:
            status = "improved"
        else:
            status = "ok"
        rows.append(dict(row, status=status))
    return rows


def failed(rows):
    return [r for r in rows if r["status"].startswith("FAIL")]


def limits_by_key(fs=AL.FS):
    return {key(q, flt, d): L for (q, flt), lim in AL.limits_for_rate(fs).items() for d, L in lim.items()}


def markdown(rows, tol=TOLERANCE_DB, baseline_meta=None, wall_s=None, baseline_name="dk/aliasing_baseline.json",
             fs=AL.FS):
    def f(v, fmt):
        return "—" if v is None else format(v, fmt)
    bad, imp = failed(rows), [r for r in rows if r["status"] == "improved"]
    head = (f"## Aliasing guard: {'FAIL' if bad else 'pass'} ({fs:g} Hz)\n\n"
            f"Absolute limit `aliasing.limits_for_rate({fs:g})`; regression tolerance {tol} dB vs `{baseline_name}`"
            + (f" (updated {baseline_meta.get('updated')}: {baseline_meta.get('reason')})" if baseline_meta else "")
            + ". Delta = measured − baseline (positive = more aliasing); margin = limit − measured."
            + (f" {wall_s:.0f} s wall." if wall_s is not None else "") + "\n")
    lines = [head, "| setting / filter / drive | worst tone Hz | measured dBc | baseline dBc | delta dB | limit dBc"
             " | margin dB | status |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        st = f"**{r['status']}**" if r["status"].startswith("FAIL") else r["status"]
        lines.append(f"| {r['key']} | {r.get('tone', '—')} | {f(r['measured_dbc'], '.2f')} | "
                     f"{f(r['baseline_dbc'], '.2f')} | {f(r['delta_db'], '+.2f')} | {r['limit_dbc']:.1f} | "
                     f"{f(r['margin_db'], '.2f')} | {st} |")
    if bad:
        lines.append(f"\n{len(bad)} failing: " + ", ".join(f"{r['key']} ({r['status']})" for r in bad) + ".")
    if imp:
        lines.append(f"\n{len(imp)} improved by more than {tol} dB (" + ", ".join(r["key"] for r in imp)
                     + "). If intended, tighten the ratchet: `python -m dk.aliasing_guard"
                     + (f" --fs {fs:g}" if fs != AL.FS else "")
                     + " --update-baseline --reason \"...\"`, and put the reason in the commit message.")
    return "\n".join(lines) + "\n"


def shown_path(path):
    """path relative to prototypes/fullstage if it is inside it, else absolute."""
    rel = os.path.relpath(os.path.abspath(path), os.path.dirname(HERE))
    return os.path.abspath(path) if rel.startswith("..") else rel


def git_rev():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def method(fs=AL.FS):
    return dict(fs_base=fs, tones={f"{q}/{flt}": {f"{d:g}V": list(t) for d, t in v.items()}
                                 for (q, flt), v in AL.ci_tones_for_rate(fs).items()},
                settle_s=AL.CI_SETTLE_S, window_s=AL.CI_WINDOW_S, band_hz=AL.BAND_HZ, mask_bins=AL.MASK_BINS)


def load_baseline(path=BASELINE_JSON):
    return json.load(open(path)) if os.path.exists(path) else dict(values={})


def write_baseline(path, measured, reason, fs=AL.FS):
    with open(path, "w") as fh:
        json.dump(dict(reason=reason, updated=datetime.date.today().isoformat(), git_rev=git_rev(),
                       tolerance_db=TOLERANCE_DB, method=method(fs), values=measured), fh, indent=1)
        fh.write("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="CI aliasing guard: absolute limits plus a baseline ratchet (#35).")
    ap.add_argument("--workers", type=int, default=os.cpu_count(), help="processes for the subset")
    ap.add_argument("--fs", type=int, choices=(44100, AL.FS), default=AL.FS, help="host rate (default 48000)")
    ap.add_argument("--baseline", help="baseline JSON (default: separate committed baseline for the selected rate)")
    ap.add_argument("--json", help="write the full results (records, rows) here")
    ap.add_argument("--update-baseline", action="store_true", help="rewrite the baseline from this run")
    ap.add_argument("--reason", help="why the baseline changes (required with --update-baseline)")
    ap.add_argument("--triode-math", default="libm", choices=("libm", "fast"),
                    help="triode exp/log1p/pow: libm (default, what CI runs) or dk/fastmath.py (#18)")
    a = ap.parse_args(argv)
    if a.update_baseline and not (a.reason and a.reason.strip()):
        ap.error("--update-baseline needs --reason \"...\" (it is stored in the baseline; also put it in the commit)")
    if a.update_baseline and a.triode_math != "libm":
        ap.error("the baseline is measured with --triode-math libm (what CI runs)")
    a.baseline = a.baseline or (BASELINE_44100_JSON if a.fs == 44100 else BASELINE_JSON)
    base = load_baseline(a.baseline)
    if base["values"] and base.get("method", {}).get("fs_base", AL.FS) != a.fs:
        ap.error("baseline host rate differs from --fs; use a separate baseline file for each rate")

    t0 = time.perf_counter()
    recs = run_subset(a.workers, a.triode_math, a.fs)
    wall = time.perf_counter() - t0
    measured = worst_by_key(recs, a.fs)
    rows = compare(measured, base["values"], limits_by_key(a.fs))
    meta = {k: base[k] for k in ("updated", "reason") if k in base} or None
    md = markdown(rows, baseline_meta=meta, wall_s=wall,
                  baseline_name=shown_path(a.baseline), fs=a.fs)
    if a.update_baseline:
        print("(--update-baseline: the table compares this run with the previous baseline)\n")
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a").write(md)
    if a.json:
        json.dump(dict(passed=not failed(rows), tolerance_db=TOLERANCE_DB, wall_s=wall, workers=a.workers,
                       python=platform.python_version(), git_rev=git_rev(), method=method(a.fs), baseline=base,
                       rows=rows, measured=measured, records=recs), open(a.json, "w"), indent=1)

    if a.update_baseline:
        over = [r for r in rows if r["status"] == "FAIL limit"]
        if over:
            sys.exit("not updating the baseline: over the absolute limit at " + ", ".join(r["key"] for r in over))
        write_baseline(a.baseline, measured, a.reason.strip(), a.fs)
        print(f"baseline written to {os.path.relpath(a.baseline)}. Commit it with the reason in the message, e.g.\n"
              f"  git commit -m \"Aliasing baseline: {a.reason.strip()}\" {os.path.relpath(a.baseline)}")
        return 0
    if failed(rows):
        print("aliasing guard FAILED: " + ", ".join(f"{r['key']} ({r['status']})" for r in failed(rows)),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
