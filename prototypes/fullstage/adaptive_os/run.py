"""Reproduce #148. Run from prototypes/fullstage with that directory on PYTHONPATH.

See RESULTS.md for the measurement matrix, interpretation and commands.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import numpy as np
from scipy.io import wavfile

from cpp.build import build, compiler
from dk.aliasing import BAND_HZ, SWEEP_HZ
from dk6as6.pentode import GRID_BOUNDS, GRID_SHAPE, grid

RATES = (44100, 48000)
FILTERS = {"off": 0, "4k": 1, "7k": 2}
METHODS = ("fixed16", "fixed32", "fade", "handoff")
TONES = (*SWEEP_HZ, 19237, 19437, 19637, 19831, 19837, 19937, 19957, 19977, 19987, 19997)
DISPATCH = Path(__file__).resolve().parents[3] / ".dispatch" / "issue148"
PR146_HEAD = "1c0a7f4b9d666768ffdd159d0543810f4141276a"
PR146_FILE = "prototypes/fullstage/dk6as6/inaudible.json"
WARM_S = 0.015
LONG_WARM_S = 0.750


def limit_dbfs(f_hz):
    """Same hearing curve and 105 dB SPL calibration as #146 (pinned source in RESULTS.md)."""
    k = np.asarray(f_hz) / 1000.0
    threshold_spl = 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4
    return np.minimum(-100.0, threshold_spl - 10.0 - 105.0)


def db(value):
    return 20.0 * np.log10(np.maximum(value, 1e-300))


def grid_args(shape):
    if shape == GRID_SHAPE:
        return ("0", "0", "0")  # use the C++ production default
    params = grid(shape=shape)
    return tuple(str(params[p]) for p in ("GG", "XI", "CG"))


def run(exe, *args, input_data=None):
    p = subprocess.run([exe, *map(str, args)], input=input_data, capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode())
    return np.frombuffer(p.stdout, dtype=np.float64), p.stderr.decode().strip().splitlines()


def spectral_result(f, amplitude):
    level = db(amplitude)
    margin = limit_dbfs(f) - level
    worst = int(np.argmin(margin))
    loudest = int(np.argmax(level))
    return dict(margin_db=float(margin[worst]), worst_hz=float(f[worst]), worst_dbfs=float(level[worst]),
                limit_dbfs=float(limit_dbfs(f[worst])), loudest_dbfs=float(level[loudest]),
                loudest_hz=float(f[loudest]), failing_bins=int(np.sum((margin < 0) | (level >= -100))))


def steady_point(exe, job):
    fs, shape, mode, bias, factor, position, tone = job
    raw, [stats] = run(exe, "steady", fs, FILTERS[position], *grid_args(shape), factor, mode, bias, tone)
    latency, fail, out_of_law, recover = map(int, stats.split())
    y = raw[round(1.25 * fs) + latency:]
    if len(y) != fs or not np.all(np.isfinite(y)):
        raise ValueError("invalid steady render")
    y = y - y.mean()
    peak = np.max(np.abs(y))
    if not peak > 0:
        raise ValueError("silent steady render")
    f = np.arange(fs // 2 + 1)
    use = (f >= BAND_HZ[0]) & (f <= BAND_HZ[1]) & (f % tone != 0)
    return dict(fs=fs, shape=shape, mode=mode, bias_ma=bias, factor=factor, filter=position, tone=int(tone),
                **spectral_result(f[use], 2 * np.abs(np.fft.rfft(y))[use] / (fs * peak)),
                nonconverged=fail, out_of_law=out_of_law, recoveries=recover)


def prior_points():
    source = subprocess.check_output(["git", "show", f"{PR146_HEAD}:{PR146_FILE}"], text=True)
    prior = json.loads(source)
    assert prior["method"]["metric"] == 3 and tuple(prior["method"]["grid_hz"]) == TONES
    return [r for r in prior["summary_cpp"] if r["drive"] == 8 and r["os"] in (16, 32, 64) and
            (r["mode"] == "P1" or r["mode"] == "T" and r["bias_ma"] <= 0.5)]


def steady(exe, out, workers, resume):
    prior = prior_points()
    jobs = set()
    for fs in RATES:
        edge_tone = 19831 if fs == 44100 else 19987
        low_rate_tone = 19997 if fs == 44100 else 19637
        # Repeat the whole #146 grid at 44.1 kHz, and a targeted bracket at 48 kHz.
        probes = (1037, 15037, 18037, 19037, 19437, 19637, 19831, 19937, 19987, 19997)
        for bias, factor in ((0.349, 16), (0.351, 32)):
            jobs.update((fs, GRID_SHAPE, "T", bias, factor, "4k", f) for f in (TONES if fs == 44100 else probes))
            jobs.update((fs, GRID_SHAPE, "T", bias, factor, p, f)
                        for p in ("off", "7k") for f in (edge_tone, low_rate_tone))
        for bias in (0.375, 0.4, 0.45, 0.5):
            positions = FILTERS if bias == 0.5 else ("4k",)
            jobs.update((fs, GRID_SHAPE, "T", bias, 32, pos, edge_tone) for pos in positions)
        jobs.update((fs, GRID_SHAPE, "T", 0.5, 64, "4k", f) for f in (edge_tone, low_rate_tone, 3037))
        jobs.add((fs, GRID_SHAPE, "T", 0.35, 16, "4k", low_rate_tone))
        # Sample the crossover and endpoint for each published shape.
        for shape in GRID_BOUNDS:
            for bias, factor in ((0.3, 16), (0.325, 16), (0.349, 16), (0.35, 16), (0.351, 32),
                                 (0.375, 16), (0.375, 32), (0.4, 32), (0.45, 32), (0.5, 32), (0.5, 64)):
                tones = (edge_tone, low_rate_tone)
                if bias in (0.349, 0.5):
                    tones = (*tones, 19037, 19937, 19997)
                jobs.update((fs, shape, "T", bias, factor, "4k", f) for f in tones)
    # Cache individual completed records for a resumable multi-minute sweep.
    checkpoint = out.with_name("steady-progress.jsonl")
    records = {}
    if resume and checkpoint.exists():
        for line in checkpoint.read_text().splitlines():
            r = json.loads(line)
            k = tuple(r[name] for name in ("fs", "shape", "mode", "bias_ma", "factor", "filter", "tone"))
            records[k] = r
    pending = sorted(jobs - records.keys(), key=lambda j: (j[3] not in (0.349, 0.351, 0.5), j[1] != GRID_SHAPE, j))
    with ThreadPoolExecutor(max_workers=workers) as pool, checkpoint.open("a" if resume else "w") as progress:
        for i, (job, row) in enumerate(zip(pending, pool.map(lambda j: steady_point(exe, j), pending), strict=True), 1):
            records[job] = row
            print(json.dumps(row), file=progress, flush=True)
            if i % 25 == 0:
                print(f"steady: {i}/{len(pending)} new points", flush=True)
    pinned = [{**{k: r[k] for k in ("fs", "mode", "bias_ma", "lpf", "os", "tones", "passed")},
               "worst": {k: r["worst"][k] for k in ("margin_db", "tone", "worst_hz", "worst_dbfs",
                                                     "worst_limit_dbfs", "worst_source", "nonconverged", "out_of_law")}}
              for r in prior]
    write_json(out, dict(pr146_head=PR146_HEAD, pr146_summary=pinned, fresh=[records[job] for job in sorted(jobs)]))


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def controls(fs, scenario, tone):
    settling = round(1.25 * fs)
    duration = 2.15 if scenario == "roundtrip" else 0.85
    t = (np.arange(settling + round(duration * fs)) - settling) / fs
    # Keep the control update grid independent of processing block segmentation.
    control_t = np.floor_divide(np.arange(len(t)) - settling, 16) * (16 / fs)
    mode_t = np.ones_like(t)
    hq = np.zeros_like(t)
    if scenario in ("sweep-up", "sweep-down"):
        direction = 1 if scenario.endswith("up") else -1
        bias = 0.35 + direction * 0.04 * np.clip((control_t - 0.30) / 0.22, -1, 1)
    elif scenario.startswith("full"):
        bias = 0.30 + 0.20 * np.clip((control_t - 0.10) / 0.50, 0, 1)
        if scenario.endswith("down"):
            bias = 0.80 - bias
    elif scenario == "automation":
        # Slow enough to finish both transfers, then chatter faster than the warm-up.
        bias = 0.35 + 0.028 * np.sin(2 * np.pi * 9 * np.clip(control_t - 0.12, 0, 4 / 9) - np.pi / 2)
        chatter = (control_t >= 0.63) & (control_t < 0.73)
        bias[chatter] = 0.35 + 0.005 * np.sin(2 * np.pi * 65 * (control_t[chatter] - 0.63))
    elif scenario.startswith("mode"):
        mode_t = (t >= 0.25).astype(float) if scenario.endswith("up") else (t < 0.25).astype(float)
        bias = np.full_like(t, 0.38)
    elif scenario.startswith("quality"):
        hq = (t >= 0.25).astype(float) if scenario.endswith("up") else (t < 0.25).astype(float)
        bias = np.full_like(t, 0.349)
    elif scenario == "roundtrip":
        # Hold the bias just beyond each crossing so the 750 ms causal delay is exposed
        # without pushing the still-audible 16× path into the already measured failure range.
        bias = np.interp(control_t, (0.1, 0.132, 1.12, 1.152), (0.349, 0.351, 0.351, 0.349))
    else:
        raise ValueError(scenario)
    signal = 8 * np.sin(2 * np.pi * tone * t) if tone else np.zeros_like(t)
    return np.column_stack((signal, bias, mode_t, hq)), settling


def render(exe, fs, shape, position, timeline, reps=1, *, high=32, long_warm=0.0):
    methods = ("fixed16", f"fixed{high}", "fade", "handoff")
    if long_warm:
        methods += ("long_fade",)
    raw, stats = run(exe, "transition", fs, FILTERS[position], *grid_args(shape), reps, high, long_warm,
                     input_data=timeline.tobytes())
    if not np.all(np.isfinite(raw)):
        raise ValueError("nonfinite render")
    latencies = set()
    times = {m: [] for m in methods}
    for method, lines in zip(methods, np.asarray(stats).reshape(len(methods), reps), strict=True):
        for line in lines:
            latency, single_ns, dual_ns, dual_n, allocs, nonconv, out_of_law, recoveries = map(float, line.split())
            if allocs or recoveries:
                raise ValueError(f"{method} used {allocs} allocations and {recoveries} recoveries")
            latencies.add(int(latency))
            times[method].append(dict(single_ns=single_ns, dual_ns=dual_ns, dual_samples=int(dual_n),
                                      nonconverged=int(nonconv), out_of_law=int(out_of_law),
                                      allocations=int(allocs), recoveries=int(recoveries)))
    latency, = latencies
    return dict(zip(methods, raw.reshape(len(methods), len(timeline), 3), strict=True)), latency, times


def fit_tone(y, start, end, tone, fs):
    n = np.arange(start, end)
    phase = 2 * np.pi * tone * n / fs
    cols = np.column_stack((np.cos(phase), np.sin(phase), np.ones_like(phase)))
    co, si, _ = np.linalg.lstsq(cols, y[n], rcond=None)[0]
    return complex(co, -si)


def carrier_steps(y, refs, tone, fs, edge, span):
    if not tone:
        return None
    # Two local carrier fits before / after the edge; same operator for every reference.
    delta = max(round(0.002 * fs), round(fs / tone * 3))
    if edge - delta < span.start or edge + delta > span.stop:
        return None
    a, b, c = edge - delta, edge, edge + delta
    obs_before = fit_tone(y, a, b, tone, fs)
    obs_after = fit_tone(y, b, c, tone, fs)
    if not abs(obs_before) or not abs(obs_after):
        return None
    out = {}
    for name, ref in refs.items():
        ref_before = fit_tone(ref, a, b, tone, fs)
        ref_after = fit_tone(ref, b, c, tone, fs)
        if abs(ref_before) and abs(ref_after):
            ratio = (obs_after / obs_before) / (ref_after / ref_before)
            out[name] = dict(level_db=float(db(abs(ratio))), phase_deg=float(np.angle(ratio, deg=True)))
    return out


def short_spectrum(y, scale, fs, tone, span):
    # Sliding 8 ms Hann bins. Exclude carrier/harmonic main lobes for this diagnostic only.
    # A broadband residual or modulation sideband may occupy the same bin as a fold.
    size = round(0.008 * fs)
    hop = round(0.001 * fs)
    w = np.hanning(size + 1)[:-1]
    freq = np.fft.rfftfreq(size, 1 / fs)
    use = (freq >= BAND_HZ[0]) & (freq <= BAND_HZ[1])
    if tone:
        for harmonic in range(tone, fs // 2 + 1, tone):
            use &= np.abs(freq - harmonic) > 2 * fs / size
    tracked = {(44100, 19831): (3199, 8316), (48000, 19987): (2999, 8494)}.get((fs, tone), ())
    projection = np.exp(-2j * np.pi * np.outer(tracked, np.arange(size) / fs))
    windows = list(range(span.start, span.stop - size + 1, hop))
    rows = []
    levels = []
    for i in windows:
        segment = y[i:i + size] * w * 2 / (w.sum() * scale)
        a = np.abs(np.fft.rfft(segment))[use]
        rows.append(dict(center_sample=i + size // 2, **spectral_result(freq[use], a)))
        levels.append(np.abs(projection @ segment))
    worst = min(rows, key=lambda r: r["margin_db"])
    worst["fold_probes"] = [{"hz": f, "peak_dbfs": float(db(a)), "limit_dbfs": float(limit_dbfs(f)),
                            "center_sample": windows[int(i)] + size // 2}
                           for f, a, i in zip(tracked, np.max(levels, axis=0), np.argmax(levels, axis=0), strict=True)]
    return worst


def level_stats(y, scale, span):
    data = y[span]
    return dict(peak_dbfs=float(db(np.max(np.abs(data)) / scale)),
                rms_dbfs=float(db(np.sqrt(np.mean(data ** 2)) / scale)),
                peak_first_difference_dbfs=float(db(np.max(np.abs(np.diff(data))) / scale)))


def envelope_spectrum(y, scale, fs, tone, span):
    # Zipper diagnostic: estimate the carrier in 2 ms Hann windows, 0.5 ms apart.
    # Measure the RMS of its modulation above 100 Hz (slow automation is 9 Hz).
    size, hop = round(0.002 * fs), round(0.0005 * fs)
    samples = np.arange(size)
    window = np.hanning(size + 1)[:-1]
    carrier = np.exp(-2j * np.pi * tone * samples / fs)
    z = np.array([2 * np.sum(y[i:i + size] * window * carrier) / window.sum() *
                  np.exp(-2j * np.pi * tone * i / fs) for i in range(span.start, span.stop - size + 1, hop)])
    w = np.hanning(len(z) + 1)[:-1]
    f = np.fft.fftfreq(len(z), hop / fs)
    p = np.abs(np.fft.fft(w * (z - z.mean())) / len(z)) ** 2 / np.mean(w ** 2)
    return float(db(np.sqrt(np.sum(p[np.abs(f) >= 100])) / scale))


def measure_transition(fs, tone, scenario, y, timeline, settling, latency, *, high=32):
    switch_at = np.flatnonzero(np.diff((timeline[:, 3] == 1) |
                                      ((timeline[:, 2] == 1) & (timeline[:, 1] >= 0.35)))) + 1
    targets = ((timeline[switch_at, 3] == 1) |
               ((timeline[switch_at, 2] == 1) & (timeline[switch_at, 1] >= 0.35)))
    # The controller initiates on the input clock. The rendered audio is still delayed by the common latency.
    extended = scenario == "roundtrip"
    span = slice(settling + round(0.05 * fs), settling + round((2.10 if extended else 0.80) * fs))
    fixed_high = f"fixed{high}"
    refs = {name: y[name][:, 0] for name in ("fixed16", fixed_high)}
    scale = max(np.max(np.abs(ref[span])) for ref in refs.values())
    if not tone:
        scale = 100.0  # 100 V = 0 dBFS only for diagnostic silence rows
    elif not scale > 0:
        raise ValueError("missing driven reference")
    results = {}
    for name in (key for key in y if key not in refs):
        output, weight_high, dual = y[name].T
        rate_difference = refs[fixed_high] - refs["fixed16"]
        oracle = refs["fixed16"] + weight_high * rate_difference
        warm_residual = output - oracle
        versus = {k: output - ref for k, ref in refs.items()}
        versus["always_warm_mix"] = warm_residual
        response = {ref: level_stats(res, scale, span) for ref, res in versus.items()}
        if tone and scenario.startswith(("automation", "sweep", "full", "roundtrip")):
            response["zipper_dbfs_above_100hz"] = {
                ref: envelope_spectrum(res, scale, fs, tone, span) for ref, res in versus.items()
            }
        events = []
        for i, (at, up) in enumerate(zip(switch_at, targets, strict=True)):
            end = switch_at[i + 1] if i + 1 < len(switch_at) else len(output)
            delta_w = np.diff(weight_high[at:end], prepend=weight_high[at - 1])
            changes = np.flatnonzero(delta_w)
            anchor = at + int(changes[0]) if extended and len(changes) else at
            window = slice(max(span.start, anchor - round(0.004 * fs)),
                           min(span.stop, end, anchor + round(0.050 * fs)))
            if window.stop - window.start < round(0.008 * fs):
                continue
            event = dict(start_s=(at - settling) / fs, direction="up" if up else "down",
                         target_reached=bool(weight_high[end - 1] == float(up)),
                         residuals={ref: level_stats(res, scale, window) for ref, res in versus.items()},
                         off_harmonic={ref: short_spectrum(res, scale, fs, tone, window)
                                       for ref, res in versus.items()})
            event[f"fixed16_minus_{fixed_high}_spectrum"] = short_spectrum(-rate_difference, scale, fs, tone, window)
            event["fixed_spectra"] = {ref: short_spectrum(data, scale, fs, tone, window) for ref, data in refs.items()}
            event["adaptive_spectrum"] = short_spectrum(output, scale, fs, tone, window)
            event["mix_edges"] = []
            for edge in sorted({at + int(i) for i in changes[[0, -1]]}) if len(changes) else ():
                # The one-sample change due only to changing the ideal mix coefficient.
                delta = delta_w[edge - at] * rate_difference[edge]
                event["mix_edges"].append(dict(time_s=(edge - settling) / fs,
                    mix_step_dbfs=float(db(abs(delta) / scale)),
                    cold_step_dbfs=float(db(abs(warm_residual[edge] - warm_residual[edge - 1]) / scale)),
                    carrier_step=carrier_steps(output, {**refs, "always_warm_mix": oracle}, tone, fs, edge, span)))
            if extended and len(changes):
                event["max_mix_step_dbfs"] = float(db(np.max(np.abs(delta_w * rate_difference[at:end])) / scale))
            events.append(event)
        results[name] = dict(overlap_samples=int(np.sum(dual)), residuals=response, events=events)
    return dict(sample_rate=fs, tone=tone, scenario=scenario, common_latency=latency,
                reference_full_scale_volts=float(scale), results=results)


def transient(exe, out, resume):
    samples = out / "samples"
    samples.mkdir(exist_ok=True)
    results = []
    path = out / "transient.json"
    previous = {}
    if resume and path.exists():
        previous = {(r["sample_rate"], r["scenario"], r["tone"]): r for r in json.loads(path.read_text())}
        for row in previous.values():
            for counters in row["stats"].values():
                for key in ("single_ns", "dual_ns", "dual_samples"):
                    counters.pop(key, None)
    for fs in RATES:
        high_tone = 19831 if fs == 44100 else 19987  # #146 fixed-32 hot T's worst tones
        cases = [(scenario, tone, "4k" if tone == high_tone else "off")
                 for scenario in ("sweep-up", "sweep-down", "automation")
                 for tone in (1037, high_tone)]
        cases += [(scenario, 1037, "off") for scenario in ("mode-up", "mode-down", "quality-up", "quality-down")]
        cases += [(scenario, high_tone, "4k") for scenario in ("full-up", "full-down")]
        for scenario, tone, position in cases:
            name = f"{fs}-{scenario}-{tone}Hz"
            key = (fs, scenario, tone)
            if key in previous and all((samples / f"{name}-{method}.wav").exists() for method in METHODS):
                results.append(previous[key])
                continue
            timeline, settling = controls(fs, scenario, tone)
            y, latency, stats = render(exe, fs, GRID_SHAPE, position, timeline)
            row = measure_transition(fs, tone, scenario, y, timeline, settling, latency)
            counters = ("allocations", "nonconverged", "out_of_law", "recoveries")
            row.update(filter=position, stats={m: {k: t[0][k] for k in counters} for m, t in stats.items()})
            results.append(row)
            take = slice(settling, settling + round(0.82 * fs))
            audio = np.column_stack([y[m][take, 0] for m in METHODS])
            # One gain per four-way comparison, 1 dB of headroom. Float WAV preserves quiet residuals.
            gain = 10 ** (-1 / 20) / max(np.max(np.abs(audio)), 1.0)
            for method, data in zip(METHODS, audio.T, strict=True):
                wavfile.write(samples / f"{name}-{method}.wav", fs, (gain * data).astype(np.float32))
            row["wav_gain_per_volt"] = float(gain)
            previous[key] = row
            write_json(path, list(previous.values()))
            print(f"transient: {name}", flush=True)
    write_json(path, results)


def bench(exe, out):
    rows = []
    cxx = subprocess.check_output([compiler(), "--version"], text=True).splitlines()[0]
    for fs in RATES:
        timeline, settling = controls(fs, "automation", 1037)
        # Retain 100 ms of preroll and time the same two-way automation on all paths.
        timeline = timeline[settling - round(0.10 * fs):]
        _, _, times = render(exe, fs, GRID_SHAPE, "off", timeline, reps=3)
        methods = {}
        for method, reps in times.items():
            whole = [(r["single_ns"] + r["dual_ns"]) / len(timeline) for r in reps]
            overlap = [r["dual_ns"] / r["dual_samples"] for r in reps if r["dual_samples"]]
            methods[method] = dict(ns_per_sample=float(np.median(whole)),
                                   overlap_ns_per_sample=float(np.median(overlap)) if overlap else None,
                                   overlap_samples=reps[0]["dual_samples"], reps=reps)
        rows.append(dict(fs=fs, scenario="automation", total_samples=len(timeline), methods=methods))
        write_json(out / "cpu.json", dict(compiler=cxx, flags="-O3", block_max=64, records=rows))
        print(f"bench: {fs} automation", flush=True)


def review_case(exe, samples, case):
    fs, high, tone, position, scenario = case
    timeline, settling = controls(fs, scenario, tone)
    y, latency, timings = render(exe, fs, GRID_SHAPE, position, timeline, high=high, long_warm=LONG_WARM_S)
    row = measure_transition(fs, tone, scenario, y, timeline, settling, latency, high=high)
    audio = np.column_stack([trace[settling:, 0] for trace in y.values()])
    gain = 10 ** (-1 / 20) / np.max(np.abs(audio))
    stem = f"{fs}-16-{high}-{tone}Hz"
    if scenario == "automation":
        stem += "-automation"
    for method, track in zip(y, audio.T, strict=True):
        wavfile.write(samples / f"{stem}-{method}.wav", fs, (gain * track).astype(np.float32))
    row.update(high=high, filter=position, long_warm_s=LONG_WARM_S, timed_samples=len(timeline),
               wav_gain_per_volt=float(gain), timings={method: times[0] for method, times in timings.items()})
    return row


def review_transients(exe, out, workers):
    samples = out / "samples"
    samples.mkdir(exist_ok=True)
    cases = ((44100, 32, 1037, "off", "roundtrip"), (44100, 64, 19831, "4k", "roundtrip"),
             (48000, 64, 19987, "4k", "roundtrip"), (48000, 64, 19987, "4k", "automation"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = []
        for row in pool.map(lambda case: review_case(exe, samples, case), cases):
            rows.append(row)
            write_json(out / "long-warm.json", rows)
            print(f"review transient: {row['sample_rate']} Hz at 16×/{row['high']}×", flush=True)


def review_stationary(exe, out, workers):
    # Bridge the threshold to the already measured 0.5 mA endpoint with the nominal 32× endpoint stress tone.
    cases = [(fs, shape, "T", bias, 64, "4k", tone)
             for fs, tone in ((44100, 19831), (48000, 19987))
             for shape in (GRID_SHAPE, *GRID_BOUNDS)
             for bias in (0.35, 0.351, 0.4, 0.45)]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        rows = []
        for row in pool.map(lambda job: steady_point(exe, job), cases):
            rows.append(row)
            write_json(out / "steady-64.json", rows)


def review_fixed_bench(out):
    flags = ("-O3",)
    # Reproduce the historical hot-T, 8 V, block-64 stock path and the corresponding extended cases.
    cases = {}
    configurations = (
        ("stock", (), ("hq",)),
        ("extended", ("DK6AS6_BENCH_EXTENDED_RATES",), ("hq", "research32", "research64")),
    )
    for label, defines, qualities in configurations:
        exe = build(flags=flags, defines=defines, name="bench_6as6")
        only = ",".join(f"{q}_T_0.5mA_8V_b64_x1" for q in qualities)
        p = subprocess.run([exe, "--reps", "3", "--seconds", "2", "--only", only],
                           capture_output=True, text=True, check=True)
        cases[label] = json.loads(p.stdout)
        write_json(out / "fixed-cpu.json", dict(flags=flags, **cases))


def verify(exe):
    for fs in RATES:
        timeline, settling = controls(fs, "automation", 1037)
        timeline = timeline[settling - round(0.05 * fs):]
        y, latency, _ = render(exe, fs, GRID_SHAPE, "off", timeline)
        assert latency > 0
        for method, factor in (("fixed16", 0), ("fixed32", 1)):
            assert np.all(y[method][:, 1] == factor) and np.all(y[method][:, 2] == 0)
        blend = y["fade"][:, 1]
        hard = y["handoff"][:, 1]
        assert np.all((0 <= blend) & (blend <= 1)) and np.all((hard == 0) | (hard == 1))
        for weights in (blend, hard):
            assert np.any(np.diff(weights) > 0) and np.any(np.diff(weights) < 0)
        assert np.max(np.abs(np.diff(blend))) <= 1 / round(WARM_S * fs) + 1e-15
        assert np.any(np.abs(np.diff(hard)) == 1)
        stable = timeline.copy()
        stable[:, 1:] = (0.349, 1, 0)
        y, _, _ = render(exe, fs, GRID_SHAPE, "off", stable)
        for method in ("fade", "handoff"):
            np.testing.assert_array_equal(y[method], y["fixed16"])
        stable[:, 1] = 0.351
        y, _, _ = render(exe, fs, GRID_SHAPE, "off", stable)
        for method in ("fade", "handoff"):
            np.testing.assert_array_equal(y[method], y["fixed32"])
    print("fixed-path parity, both handoff directions, reversal bounds and allocation checks passed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = ("steady", "transient", "bench", "verify", "review-transient", "review-steady", "review-fixed-bench")
    parser.add_argument("action", choices=actions)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--resume", action="store_true", help="reuse completed rows in the output directory")
    args = parser.parse_args()
    if args.output is None:
        args.output = DISPATCH / "round2" if args.action.startswith("review-") or args.action == "bench" else DISPATCH
    args.output.mkdir(exist_ok=True, parents=True)
    if args.action == "review-fixed-bench":
        review_fixed_bench(args.output)
        return
    flags = ("-O3",) if args.action in ("bench", "review-transient") else ("-O2",)
    exe = build(flags=flags, name="adaptive_os_6as6")
    if args.action == "steady":
        steady(exe, args.output / "steady.json", args.workers, args.resume)
    elif args.action == "transient":
        transient(exe, args.output, args.resume)
    elif args.action == "bench":
        bench(exe, args.output)
    elif args.action == "review-transient":
        review_transients(exe, args.output, args.workers)
    elif args.action == "review-steady":
        review_stationary(exe, args.output, args.workers)
    else:
        verify(exe)


if __name__ == "__main__":
    main()
