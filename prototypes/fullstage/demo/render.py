#!/usr/bin/env python3
"""Musical demo renders through the DK 12AX7 stage (issue #39).

    python demo/render.py render IN.wav -o OUT.wav --start 10 --dur 15 --level driven [--quality eco]
    python demo/render.py batch --src DIR --out DIR [--plot vocal] [--jobs 8]
    python demo/render.py plot  --out DIR --excerpt vocal      # PNGs from an existing batch
    python demo/render.py probe                                # 1 kHz sine sweep: where grid current / cutoff start

Signal path (both modes):
  1. load (soundfile for WAV/FLAC/AIFF/OGG, ffmpeg for anything else, e.g. MP3), keep every channel
     (--mono sums them), trim [start, start + dur), resample to the stage's validated 48 kHz base rate
     (scipy resample_poly, Kaiser beta 8.6), 5 ms / 50 ms raised-cosine fade in / out against clicks;
     this is the dry excerpt;
  2. scale to volts: ref "fs" maps digital full scale (1.0) to V volts peak, ref "peak" maps the
     excerpt's own sample peak to V (the batch uses "peak", so a level means the same on every excerpt);
  3. one Stage per channel (Stage(48000, quality, filt), Drive = Bias = Output = 1).
     `latency_samples` extra zeros are fed and that many output
     samples dropped, so the output lines up with the dry to the sample (exact for the FIR resampler,
     nominal for IIR). Polarity is restored (a common-cathode stage inverts; --keep-polarity keeps it);
  4. DC removal (subtract each channel's mean), the dry's 50 ms fade-out applied to the output tail;
  5. loudness match: one gain so the integrated loudness (ITU-R BS.1770-4 via pyloudnorm, gated) equals
     the dry excerpt's. Then a peak safety check: the true peak is estimated by 4x oversampling and, if it
     is above the ceiling (-1 dBTP), the gain is reduced until it is not. Gain only, never a limiter or
     clipper, so the waveform shape is the stage's; `limited` / `limit_db` in the metrics report it. The
     dry gets the same safety rule first, and the target is the dry *as written*;
  6. write 24-bit WAV (+ 320 kbps MP3 via ffmpeg in batch mode or with --mp3).
"""
import os
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")         # the per-sample loop uses tiny matrices; one process per core instead

import argparse  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import csv  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import json  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import math  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import subprocess  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import sys  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
import time  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
from concurrent.futures import ProcessPoolExecutor  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
from pathlib import Path  # noqa: E402 -- after the BLAS thread variables are set (before numpy)

import numpy as np  # noqa: E402 -- after the BLAS thread variables are set (before numpy)
from scipy.signal import resample_poly, welch  # noqa: E402 -- after the BLAS thread variables are set (before numpy)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from dk.stage import Stage                                        # noqa: E402

FS = 48000                         # base rate the stage and its resamplers are validated at (dk/RESULTS.md)
LEVELS = {"clean": 0.5, "edge": 2.0, "driven": 8.0}   # V peak at the reference point; README.md "Levels"
CEILING_DBTP = -1.0
FADE_IN_S, FADE_OUT_S = 0.005, 0.05
BLOCK = FS                         # Stage.process is streaming; 1 s blocks keep the internal-rate buffers small
SNDFILE_EXT = {".wav", ".flac", ".aif", ".aiff", ".ogg"}


# ---- I/O --------------------------------------------------------------------------------------------------

def load_audio(path):
    """(x, sr): x is float64 of shape (n, channels)."""
    path = Path(path)
    if path.suffix.lower() in SNDFILE_EXT:
        import soundfile as sf
        x, sr = sf.read(str(path), dtype="float64", always_2d=True)
        return x, int(sr)
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=sample_rate,channels",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)["streams"][0]
    sr, ch = int(info["sample_rate"]), int(info["channels"])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "f32le",
                          "-acodec", "pcm_f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, ch).astype(np.float64), sr


def write_wav(path, y):
    import soundfile as sf
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), y, FS, subtype="PCM_24")


def write_mp3(wav, mp3, meta=None):
    Path(mp3).parent.mkdir(parents=True, exist_ok=True)
    tags = [a for k, v in (meta or {}).items() for a in ("-metadata", f"{k}={v}")]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "320k",
                    *tags, str(mp3)], check=True)


# ---- excerpt ----------------------------------------------------------------------------------------------

def to_fs(x, sr):
    if sr == FS:
        return x
    g = math.gcd(FS, sr)
    return resample_poly(x, FS // g, sr // g, axis=0, window=("kaiser", 8.6))


def fade(x, fade_in=FADE_IN_S, fade_out=FADE_OUT_S):
    x = x.copy()
    for n, sl, rising in ((int(fade_in * FS), slice(None, None), True), (int(fade_out * FS), slice(None, None), False)):
        if n:
            w = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
            if rising:
                x[:n] *= w[:, None]
            else:
                x[-n:] *= w[::-1, None]
    return x


def excerpt(path, start, dur, mono=False):
    """Dry excerpt at FS, shape (n, ch), faded. start/dur in seconds of the source file."""
    if start < 0 or (dur is not None and dur <= 0):
        raise SystemExit("--start must be nonnegative and --dur must be positive")
    x, sr = load_audio(path)
    i0 = int(round(start * sr))
    i1 = len(x) if dur is None else i0 + int(round(dur * sr))
    if not 0 <= i0 < i1 <= len(x):
        raise SystemExit(f"{path}: excerpt {start}+{dur} s is outside the file ({len(x) / sr:.3f} s)")
    x = x[i0:i1]
    if mono:
        x = x.mean(axis=1, keepdims=True)
    return fade(to_fs(x, sr))


# ---- measurement ------------------------------------------------------------------------------------------

def db(v):
    return 20 * math.log10(v) if v > 0 else -math.inf


def true_peak(y):
    """True-ish peak (linear): sample peak after 4x oversampling (BS.1770-4 Annex 2 asks for >= 4x at 48 kHz)."""
    return float(np.max(np.abs(resample_poly(y, 4, 1, axis=0))))


def lufs(y):
    import pyloudnorm
    return float(pyloudnorm.Meter(FS).integrated_loudness(y))


def level_metrics(y):
    return dict(peak_dbfs=db(float(np.max(np.abs(y)))), true_peak_dbtp=db(true_peak(y)),
                rms_dbfs=db(float(np.sqrt(np.mean(y ** 2)))), lufs=lufs(y))


def safe_gain(y, gain_db, ceiling_dbtp):
    """Reduce gain_db (never raise it) until the true peak is at or below the ceiling. -> (gain_db, limit_db)"""
    tp = db(true_peak(y)) + gain_db
    cut = max(0.0, tp - ceiling_dbtp)
    if cut:                         # the estimate is linear in gain, so one step lands on the ceiling
        cut += 1e-3
    return gain_db - cut, cut


# ---- the stage --------------------------------------------------------------------------------------------

def run_channel(job):
    """Worker: one channel in volts through a fresh Stage. Latency compensated. -> dict."""
    v, quality, filt = job["volts"], job["quality"], job["filt"]
    t0 = time.perf_counter()
    st = Stage(FS, quality=quality, filt=filt)
    lat = st.latency_samples
    vin = np.concatenate([v, np.zeros(lat)])
    y = np.concatenate([st.process(vin[i:i + BLOCK])["out"] for i in range(0, len(vin), BLOCK)])[lat:]
    assert len(y) == len(v)
    it = st.iteration_stats()
    return dict(key=job["key"], y=y, runtime_s=time.perf_counter() - t0, latency_samples=lat,
                nonconverged=it["nonconverged"], newton_mean=it["mean"], newton_max=it["max"])


def volts_scale(dry, volts, ref):
    if not math.isfinite(volts) or volts <= 0:
        raise SystemExit("input peak voltage must be a positive finite number")
    peak = float(np.max(np.abs(dry))) if ref == "peak" else 1.0
    if peak == 0:
        raise SystemExit("cannot scale a silent excerpt by its peak")
    return volts / peak


def channel_jobs(dry, volts, ref, quality, filt, key):
    """One fresh Stage job per channel; returns (jobs, mapping of output channel -> job index)."""
    s = volts_scale(dry, volts, ref)
    jobs, idx = [], []
    for c in range(dry.shape[1]):
        idx.append(len(jobs))
        jobs.append(dict(key=(key, len(jobs)), volts=dry[:, c] * s, quality=quality, filt=filt))
    return jobs, idx


def finish(results, idx, dry, target_lufs, ceiling_dbtp, keep_polarity=False):
    """Assemble channels, polarity, DC, tail fade, loudness match + peak safety. -> (y, info)."""
    y = np.stack([results[i]["y"] for i in idx], axis=1)
    if not keep_polarity:
        y = -y
    y = y - y.mean(axis=0)
    y = fade(y, fade_in=0.0)
    raw_lufs = lufs(y)
    gain_db, limit_db = safe_gain(y, target_lufs - raw_lufs, ceiling_dbtp)
    y = y * 10 ** (gain_db / 20)
    rs = [results[i] for i in sorted(set(idx))]
    info = dict(gain_db=gain_db, limited=limit_db > 0, limit_db=limit_db,
                latency_samples=rs[0]["latency_samples"], runtime_s=sum(r["runtime_s"] for r in rs),
                channels_processed=len(rs), nonconverged=sum(r["nonconverged"] for r in rs),
                newton_mean=float(np.mean([r["newton_mean"] for r in rs])), newton_max=max(r["newton_max"] for r in rs))
    info["rt_factor"] = info["runtime_s"] / (len(y) / FS * len(rs))
    return y, info


def prepare_dry(dry, ceiling_dbtp):
    gain_db, limit_db = safe_gain(dry, 0.0, ceiling_dbtp)
    return dry * 10 ** (gain_db / 20), dict(gain_db=gain_db, limited=limit_db > 0, limit_db=limit_db)


def run_jobs(jobs, workers):
    jobs = sorted(jobs, key=lambda j: -Stage_cost(j))         # longest first, so the pool drains evenly
    if workers <= 1:
        return {r["key"]: r for r in map(run_channel, jobs)}
    with ProcessPoolExecutor(workers) as ex:
        return {r["key"]: r for r in ex.map(run_channel, jobs)}


def Stage_cost(job):
    from dk.stage import QUALITY
    return len(job["volts"]) * QUALITY[job["quality"]][0]


# ---- single render ----------------------------------------------------------------------------------------

def cmd_render(a):
    volts = LEVELS[a.level] if a.volts is None else a.volts
    dry, dry_info = prepare_dry(excerpt(a.input, a.start, a.dur, a.mono), a.ceiling)
    target = lufs(dry)
    jobs, idx = channel_jobs(dry, volts, a.ref, a.quality, a.filt, "x")
    res = run_jobs(jobs, a.jobs)
    y, info = finish([res[j["key"]] for j in jobs], idx, dry, target, a.ceiling, a.keep_polarity)
    write_wav(a.output, y)
    if a.mp3:
        write_mp3(a.output, Path(a.output).with_suffix(".mp3"))
    if a.dry:
        write_wav(a.dry, dry)
    m = dict(input=str(a.input), start=a.start, dur=len(dry) / FS, quality=a.quality, filt=a.filt, volts=volts,
             ref=a.ref,
             input_peak_v=(float(np.max(np.abs(dry))) * volts_scale(dry, volts, a.ref)
                           / 10 ** (dry_info["gain_db"] / 20)),
             target_lufs=target, dry=dry_info, **level_metrics(y), **info)
    print(json.dumps(m, indent=2))


# ---- batch ------------------------------------------------------------------------------------------------

CSV_FIELDS = ["excerpt", "render", "quality", "filt", "level", "volts", "dur_s", "channels", "channels_processed",
              "input_peak_v", "peak_dbfs", "true_peak_dbtp", "rms_dbfs", "lufs", "target_lufs", "gain_db",
              "limited", "limit_db", "latency_samples", "runtime_s", "rt_factor", "nonconverged", "newton_mean",
              "newton_max", "wav", "mp3"]


def cmd_batch(a):
    man = json.loads(Path(a.manifest).read_text())
    out, src = Path(a.out).resolve(), Path(a.src)
    ref, ceiling, levels = man.get("ref", "peak"), man.get("ceiling_dbtp", CEILING_DBTP), man.get("levels", LEVELS)
    exs = [e for e in man["excerpts"] if not a.only or e["name"] in a.only]
    t_all = time.perf_counter()
    drys, jobs, plan = {}, [], []
    for e in exs:
        dry, dry_info = prepare_dry(excerpt(src / e["file"], e["start"], e["dur"], e.get("mono", False)), ceiling)
        drys[e["name"]] = (dry, dry_info, lufs(dry))
        for r in man["renders"]:
            volts = levels[r["level"]]
            js, idx = channel_jobs(dry, volts, ref, r["quality"], r.get("filt", "fir"), (e["name"], r["name"]))
            jobs += js
            plan.append((e, r, volts, js, idx))
    print(f"{len(exs)} excerpts, {len(plan)} renders, {len(jobs)} channel jobs on {a.jobs} workers", flush=True)
    res = run_jobs(jobs, a.jobs)

    rows = []
    def emit(e, name, y, extra):
        wav = out / "wav" / f"{e['name']}-{name}.wav"
        mp3 = out / "mp3" / f"{e['name']}-{name}.mp3"
        write_wav(wav, y)
        write_mp3(wav, mp3, {"title": f"{e['title']} ({e['name']}, {name})", "artist": e["author"],
                             "comment": f"{e['licence']}; excerpt {e['start']}-{e['start'] + e['dur']} s; {e['url']}"})
        row = dict(excerpt=e["name"], render=name, dur_s=len(y) / FS, channels=y.shape[1],
                   **level_metrics(y), **extra, wav=str(wav), mp3=str(mp3))
        rows.append(row)
        print(f"  {e['name']:7s} {name:16s} {row['lufs']:7.2f} LUFS  gain {row['gain_db']:+7.2f} dB"
              f"{'  LIMITED %.2f dB' % row['limit_db'] if row.get('limited') else ''}", flush=True)

    for e in exs:
        dry, dry_info, target = drys[e["name"]]
        emit(e, "dry", dry, dict(quality="", filt="", level="", volts="", channels_processed="", input_peak_v="",
                                  target_lufs=target, latency_samples=0, runtime_s=0.0, **dry_info))
        for e2, r, volts, js, idx in plan:
            if e2 is e:
                y, info = finish([res[j["key"]] for j in js], idx, dry, target, ceiling)
                emit(e, r["name"], y, dict(quality=r["quality"], filt=r.get("filt", "fir"), level=r["level"],
                                           volts=volts,
                                           input_peak_v=float(np.max(np.abs(dry))) * volts_scale(dry, volts, ref),
                                           target_lufs=target, **info))
    out.mkdir(parents=True, exist_ok=True)
    rows = [{k: r.get(k, "") for k in CSV_FIELDS} for r in rows]
    with open(out / "metrics.csv", "w", newline="") as f:
        w = csv.DictWriter(f, CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    meta = dict(manifest=str(Path(a.manifest).resolve()), ref=ref, levels=levels, ceiling_dbtp=ceiling, fs=FS,
                git=_git_rev(), wall_s=time.perf_counter() - t_all, workers=a.jobs,
                excerpts=[{k: e[k] for k in ("name", "file", "title", "author", "licence", "url", "start", "dur")}
                          for e in exs])
    (out / "metrics.json").write_text(json.dumps(dict(meta=meta, renders=rows), indent=2, default=_json_default))
    print(f"wrote {len(rows)} renders to {out} in {meta['wall_s']:.0f} s")
    if a.plot:
        e = next(e for e in exs if e["name"] == a.plot)
        plot(out, e, man)


def _json_default(v):
    return float(v) if isinstance(v, np.floating) else bool(v) if isinstance(v, np.bool_) else str(v)


def _git_rev():
    try:
        return subprocess.run(["git", "-C", str(HERE), "describe", "--always", "--dirty"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


# ---- plots ------------------------------------------------------------------------------------------------

INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
SERIES = {"dry": MUTED, "standard-clean": "#86b6ef", "standard-edge": "#2a78d6", "standard-driven": "#104281"}
LABEL = {"dry": "dry", "standard-clean": "clean 0.5 V", "standard-edge": "edge 2 V", "standard-driven": "driven 8 V"}


def _axes_style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _octave_smooth(f, p, frac=12):
    """Power average over a 1/frac-octave window around each bin."""
    c = np.concatenate([[0.0], np.cumsum(p)])
    lo = np.searchsorted(f, f * 2 ** (-0.5 / frac))
    hi = np.searchsorted(f, f * 2 ** (0.5 / frac), side="right")
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def _f0_track(x, t0, t1, frame, hop, fmin=150.0, fmax=500.0):
    """Per-frame f0 (Hz) by normalised autocorrelation with parabolic interpolation; None when unvoiced."""
    out = []
    for i in range(int(t0 * FS), int(t1 * FS) - frame, hop):
        s = x[i:i + frame] - x[i:i + frame].mean()
        r = np.correlate(s, s, "full")[frame - 1:]
        lo, hi = int(FS / fmax), int(FS / fmin)
        lag = lo + int(np.argmax(r[lo:hi]))
        if r[lag] < 0.8 * r[0]:
            out.append((i, None))
            continue
        a, b, c = r[lag - 1], r[lag], r[lag + 1]
        out.append((i, FS / (lag + 0.5 * (a - c) / (a - 2 * b + c))))
    return out


def harmonic_levels(y, track, frame, nharm=15):
    """Mean (power) level of H1..Hn in dB re H1, peak-picked within +-f0/4 of k*f0 in each voiced frame."""
    win = np.hanning(frame)
    nfft = 8 * frame
    f = np.fft.rfftfreq(nfft, 1 / FS)
    acc = np.zeros(nharm)
    nfr = 0
    for i, f0 in track:
        if f0 is None or nharm * f0 > 20000:
            continue
        X = np.abs(np.fft.rfft(y[i:i + frame] * win, nfft)) ** 2
        h = np.array([X[(f > (k - 0.25) * f0) & (f < (k + 0.25) * f0)].max() for k in range(1, nharm + 1)])
        acc += h / h[0]
        nfr += 1
    return 10 * np.log10(acc / nfr), nfr


def plot(out, e, man):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import soundfile as sf
    out = Path(out)
    wav = {n: sf.read(str(out / "wav" / f"{e['name']}-{n}.wav"), always_2d=True)[0].mean(axis=1) for n in SERIES}
    (out / "plots").mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "text.color": INK, "axes.labelcolor": INK2})

    # spectrum: dry vs driven, both loudness-matched (long-term average, Welch, 1/12-octave smoothed)
    fig, ax = plt.subplots(figsize=(9, 4.8), facecolor=SURFACE)
    _axes_style(ax)
    for n in ("dry", "standard-driven"):
        f, p = welch(wav[n], FS, nperseg=8192)
        k = (f >= 20) & (f <= 22000)
        lv = 10 * np.log10(_octave_smooth(f[k], p[k]) + 1e-30)
        ax.semilogx(f[k], lv, color=SERIES[n], linewidth=2 if n != "dry" else 1.6, label=LABEL[n])
        ax.annotate(LABEL[n], (f[k][-1], lv[-1]), xytext=(4, 0), textcoords="offset points", va="center",
                    fontsize=9, color=INK2)
    ax.set_xlim(20, 22000)
    ax.set_xlabel("frequency (Hz)")
    ax.set_ylabel("power spectral density (dB re FS²/Hz)")
    ax.set_title(f"{e['name']}: long-term spectrum, dry vs driven 8 V (standard, FIR), loudness-matched",
                 loc="left", fontsize=11, color=INK)
    ax.legend(frameon=False, loc="lower left", labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(out / "plots" / f"{e['name']}-spectrum.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)

    # Harmonics only when the manifest names a sustained-note interval.
    if "note" not in e:
        print(f"spectrum in {out / 'plots'}; no sustained-note interval for {e['name']}")
        return
    t0, t1 = e["note"]
    frame, hop = 4096, 1024
    track = _f0_track(wav["dry"], t0, t1, frame, hop)
    f0s = [f for _, f in track if f]
    lv = {n: harmonic_levels(wav[n], track, frame) for n in SERIES}
    k = np.arange(1, len(lv["dry"][0]) + 1)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.6), facecolor=SURFACE)
    for ax in (a1, a2):
        _axes_style(ax)
        ax.set_xticks(k)
        ax.set_xlabel("harmonic number")
    for n in SERIES:
        a1.plot(k, lv[n][0], color=SERIES[n], linewidth=2, marker="o", markersize=4.5, label=LABEL[n])
    a1.set_ylabel("level (dB re H1)")
    a1.set_title("harmonic levels", loc="left", fontsize=10, color=INK)
    a1.legend(frameon=False, labelcolor=INK2, fontsize=9)
    a2.axhline(0, color=AXIS, linewidth=1)
    for n in list(SERIES)[1:]:
        d = lv[n][0] - lv["dry"][0]
        a2.plot(k, d, color=SERIES[n], linewidth=2, marker="o", markersize=4.5, label=LABEL[n])
        a2.annotate(LABEL[n], (k[-1], d[-1]), xytext=(5, 0), textcoords="offset points", va="center",
                    fontsize=9, color=INK2)
    a2.set_xlim(0.5, len(k) + 2.5)
    a2.set_ylabel("change vs dry (dB)")
    a2.set_title("added by the stage (render − dry)", loc="left", fontsize=10, color=INK)
    fig.suptitle(f"{e['name']}: sustained note {t0:g}–{t1:g} s of the excerpt, f0 {np.median(f0s):.0f} Hz "
                 f"(median of {lv['dry'][1]} voiced frames), standard / FIR", x=0.01, ha="left", fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(out / "plots" / f"{e['name']}-harmonics.png", dpi=150, facecolor=SURFACE)
    plt.close(fig)
    summary = {LABEL[n]: [round(v, 2) for v in lv[n][0]] for n in SERIES}
    (out / "plots" / f"{e['name']}-harmonics.json").write_text(json.dumps(
        dict(note_s=[t0, t1], f0_median_hz=float(np.median(f0s)), frames=lv["dry"][1], dB_re_H1=summary), indent=2))
    print(f"plots in {out / 'plots'}")


def cmd_plot(a):
    man = json.loads(Path(a.manifest).read_text())
    plot(a.out, next(e for e in man["excerpts"] if e["name"] == a.excerpt), man)


# ---- probe ------------------------------------------------------------------------------------------------

def probe_one(amp):
    from dk.triode import currents_only
    from analysis import harmonics, thd
    st = Stage(FS, quality="standard")
    n, lat = int(0.5 * FS), st.latency_samples
    o = st.process(amp * np.sin(2 * np.pi * 1000 * np.arange(n + lat) / FS), record=("out", "g", "k", "a"))
    rec, fsi = o["_internal"], st.fs
    seg = slice(int(0.4 * fsi), int(0.5 * fsi))                    # last 100 ms: settled (blocking included)
    vgk, vak = (rec["g"] - rec["k"])[seg], (rec["a"] - rec["k"])[seg]
    cur = np.array([currents_only(g, v) for g, v in zip(vgk, vak)])
    h = harmonics(o["out"][lat:lat + n], FS, 1000, 100)
    return dict(vpk=amp, h1=h[0], thd=thd(h), ig_max=cur[:, 0].max() - st.par["Ig0"], ia_min=cur[:, 1].min(),
                vgk_max=vgk.max(), vgk_mean=vgk.mean())


def cmd_probe(a):
    amps = [0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 6.0, 8.0]
    with ProcessPoolExecutor(a.jobs) as ex:
        rs = list(ex.map(probe_one, amps))
    g0 = rs[0]["h1"] / rs[0]["vpk"]
    print("| V peak in | gain (dB) | compression (dB) | THD | grid current max above Ig0 (µA) | plate current min (mA) "
          "| Vgk max (V) | mean Vgk (V) |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rs:
        print(f"| {r['vpk']:g} | {db(r['h1'] / r['vpk']):.2f} | {db(r['h1'] / r['vpk'] / g0):.2f} "
              f"| {100 * r['thd']:.2f} % "
              f"| {1e6 * r['ig_max']:.3f} | {1e3 * r['ia_min']:.4f} | {r['vgk_max']:.3f} | {r['vgk_mean']:.3f} |")


# ---- CLI --------------------------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    jobs = dict(type=int, default=min(8, os.cpu_count() or 1), help="worker processes (default: up to 8)")

    r = sub.add_parser("render", help="one file through the stage")
    r.add_argument("input")
    r.add_argument("-o", "--output", required=True, help="24-bit WAV to write")
    r.add_argument("--start", type=float, default=0.0, help="excerpt start (s)")
    r.add_argument("--dur", type=float, default=None, help="excerpt length (s), default to the end")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--level", choices=LEVELS, default="edge", help="clean 0.5 V / edge 2 V / driven 8 V (default edge)")
    g.add_argument("--volts", type=float, help="peak input voltage at the reference point instead of a level")
    r.add_argument("--ref", choices=("fs", "peak"), default="fs",
                   help="what gets the peak voltage: digital full scale (default) or the excerpt's sample peak")
    r.add_argument("--quality", choices=("hq", "standard", "eco"), default="standard")
    r.add_argument("--filt", choices=("fir", "iir"), default="fir")
    r.add_argument("--mono", action="store_true", help="sum the channels to mono first")
    r.add_argument("--keep-polarity", action="store_true", help="keep the stage's polarity inversion")
    r.add_argument("--ceiling", type=float, default=CEILING_DBTP, help="true-peak ceiling, dBTP (default -1)")
    r.add_argument("--dry", help="also write the dry excerpt (as used for the loudness target) here")
    r.add_argument("--mp3", action="store_true", help="also write a 320 kbps MP3 next to the output")
    r.add_argument("--jobs", **jobs)
    r.set_defaults(fn=cmd_render)

    b = sub.add_parser("batch", help="every excerpt x render in the manifest")
    b.add_argument("--manifest", default=str(HERE / "manifest.json"))
    b.add_argument("--src", required=True, help="directory holding the source files named in the manifest")
    b.add_argument("--out", required=True, help="output directory (wav/, mp3/, plots/, metrics.*); not in the repo")
    b.add_argument("--only", nargs="*", help="excerpt names to render (default all)")
    b.add_argument("--plot", metavar="EXCERPT", help="also write the spectrum and harmonic plots for this excerpt")
    b.add_argument("--jobs", **jobs)
    b.set_defaults(fn=cmd_batch)

    pl = sub.add_parser("plot", help="spectrum + harmonic PNGs from an existing batch output")
    pl.add_argument("--manifest", default=str(HERE / "manifest.json"))
    pl.add_argument("--out", required=True)
    pl.add_argument("--excerpt", default="vocal")
    pl.set_defaults(fn=cmd_plot)

    pr = sub.add_parser("probe", help="1 kHz sine sweep: compression, THD, grid current, cutoff (markdown table)")
    pr.add_argument("--jobs", **jobs)
    pr.set_defaults(fn=cmd_probe)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
