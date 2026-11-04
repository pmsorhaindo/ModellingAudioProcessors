"""Aliasing of the DK stage (#6): offset-tone sweep, 64x ground truth, per-setting limits.

Metric: drive Stage.process with a sine of `drive` volts near f, skip `settle` seconds, and analyse the next
`window` seconds of base-rate output: total non-harmonic energy 20 Hz-20 kHz relative to the fundamental (dBc).
Periodic Hann window; fundamental = its bin +-MASK_BINS; every harmonic bin below fs/2 +-MASK_BINS is masked;
everything else in 20 Hz-20 kHz is summed. Also reported: the largest single component and where it is.

Coherent: the tone sits exactly on a DFT bin m of the n-sample window (whole number of cycles), so in steady state
every harmonic and every alias is a line on its own bin, spread over +-1 bin by the window. The window must be the
DFT-periodic Hann (np.hanning(n + 1)[:-1]); the symmetric np.hanning(n) is not periodic in n and leaks a coherent
tone's skirt into the neighbouring bins at about -113 dBc (1 s window, +6 dB per halving of the window), which is
where the earlier "-110 to -113 dBc measurement floor" came from. With the periodic window an exactly harmonic
signal reads ANALYSIS_FLOOR_DBC (test_aliasing.py checks it).

Offset tones: SWEEP_HZ = 1037, 2037, ... 19037 Hz. A harmonic of order k folds onto bin +-k*m (mod n). It lands in the
mask of harmonic j only if (k -+ j)*m is within MASK_BINS of a multiple of n; collision_order() is the smallest such
k -+ j. A tone that divides 48 kHz folds its harmonics onto its own harmonic bins (10 kHz: order 24, e.g. H22 lands
on H2), where they are masked and not counted. At 1 s every sweep tone has collision order >= 81 (13037 Hz; the
rest >= 919). Shorter windows (CI) cannot hold 1 Hz offsets: tone_bin() moves the tone to the nearest bin that
keeps the collision order >= MIN_ORDER (19037 Hz becomes 19040 Hz in a 0.1 s window).

Source of each alias line: bin b is the fold of the lowest harmonic order k <= kmax with +-k*m = b (mod n), where
kmax = max(collision order - 1, os*fs/f, 1000) covers at least every harmonic up to twice the stage's internal
Nyquist (above the collision order a line can be credited to a lower order sharing its bin). If
k*f > os*fs/2 that harmonic is above the stage's internal Nyquist and folded INSIDE the stage ("internal": what
oversampling buys). Otherwise it was in the internal band and reached 0-20 kHz through the resampler: decimator
stopband leakage, or intermodulation with the interpolator's residual images, which lands on the same bins
("resampler": set by the half-band filters, about the same at every oversampling factor). Bins on no fold of
order <= kmax are "other" (e.g. settling transients). Empty sums read DB_FLOOR.

Latency: Stage.process lags its input by latency_samples (#14); the input runs latency_samples past the window and
the first latency_samples of output are dropped, so the window never depends on input past the end of the signal
(the #2 FIR-tail artefact). The stage starts at its DC operating point; SETTLE_S lets the coupling/bypass caps and
the bias shift settle (time constants ~20-30 ms).

Use: measure() one point, sweep() a set of points (optionally in parallel processes); window, settle and tones are
parameters, so a lean subset with short buffers (CI_SETTLE_S, CI_WINDOW_S) uses the same code as the full sweep.
`python -m dk.aliasing` regenerates the 64x ground truth (GROUND_TRUTH_JSON; 11 min on 8 cores).
"""
import argparse, json, os, sys, time
from concurrent.futures import ProcessPoolExecutor
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk.stage import Stage

FS = 48000
SWEEP_HZ = tuple(range(1037, 20000, 1000))      # 1037, 2037, ... 19037 Hz
DRIVES = (2.0, 8.0)                              # V peak
FILTERS = ("fir", "iir")
SETTLE_S, WINDOW_S = 0.25, 1.0                   # full sweep
CI_SETTLE_S, CI_WINDOW_S = 0.2, 0.1             # lean subset: within 0.05 dB of the full sweep at the worst tones
BAND_HZ = (20.0, 20000.0)
MASK_BINS = 3
MIN_ORDER = 64
DB_FLOOR = -300.0
ANALYSIS_FLOOR_DBC = -200.0                      # an exactly harmonic signal reads below this (measured ~-250)
GROUND_TRUTH_OS = 64
GROUND_TRUTH_JSON = os.path.join(HERE, "aliasing_64x.json")

def collision_order(m, n, mask=MASK_BINS):
    """Smallest d >= 1 with d*m within `mask` bins of a multiple of n: harmonics k, j with k -+ j < d never fold
    into each other's masks, so every alias of a harmonic of order < d is on its own (counted) bin."""
    d = 1
    while True:
        r = (d * m) % n
        if min(r, n - r) <= mask:
            return d
        d += 1


def tone_bin(f, n, fs=FS, min_order=MIN_ORDER):
    """DFT bin of the coherent tone nearest f in an n-sample window whose collision order is >= min_order."""
    m0 = int(round(f * n / fs))
    for dm in range(n // 4):
        for m in (m0 - dm, m0 + dm):
            if 0 < m < n // 2 and collision_order(m, n) >= min_order:
                return m
    raise ValueError(f"no coherent bin near {f} Hz for n = {n}")


def fold_order(m, n, kmax):
    """Per rfft bin: the lowest harmonic order k <= kmax whose fold (+-k*m mod n) lands on it or next to it
    (the Hann main lobe is +-1 bin); 0 where none does."""
    org = np.zeros(n // 2 + 1, int)
    for k in range(kmax, 0, -1):          # descending, so the lowest order wins
        r = (k * m) % n
        b = min(r, n - r)
        org[max(b - 1, 0):b + 2] = k
    return org


def alias_dbc(y, m, fs=FS, os_=None, band=BAND_HZ, mask=MASK_BINS):
    """y: one analysis window (n samples) of output; m: the tone's bin. Returns the total and the largest single
    non-harmonic component in `band` (dBc) and where the largest is (Hz); with os_ (the stage's oversampling)
    also the total split by source: internal / resampler / other (see the module docstring)."""
    n = len(y)
    P = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(n + 1)[:-1])) ** 2
    fb = np.arange(len(P)) * fs / n
    harm = np.zeros(len(P), bool)
    for k in range(1, n // (2 * m) + 1):
        harm[max(k * m - mask, 0):k * m + mask + 1] = True
    sel = (fb >= band[0]) & (fb <= band[1]) & ~harm
    fund = P[m - mask:m + mask + 1].sum()
    db = lambda e: max(float(10 * np.log10(max(e, 1e-300) / fund)), DB_FLOOR)
    j = int(np.argmax(np.where(sel, P, 0.0)))
    out = dict(total_dbc=db(P[sel].sum()), worst_dbc=db(P[j]), worst_hz=float(fb[j]))
    if os_ is not None:
        k = fold_order(m, n, max(collision_order(m, n) - 1, int(np.ceil(os_ * n / m)), 1000))
        internal = k * m * fs / n > os_ * fs / 2
        out.update(internal_dbc=db(P[sel & (k > 0) & internal].sum()),
                   resampler_dbc=db(P[sel & (k > 0) & ~internal].sum()), other_dbc=db(P[sel & (k == 0)].sum()))
    return out


def setting_name(quality):
    return quality if isinstance(quality, str) else f"{quality[0]}x"


def measure(quality, filt, drive, f, window=WINDOW_S, settle=SETTLE_S, fs=FS):
    """Aliasing of Stage(fs, quality, filt) for a `drive`-volt sine near f Hz (on a coherent bin, see tone_bin).
    quality: a stage.QUALITY key, or an (os, prewarp_hz) pair such as (GROUND_TRUTH_OS, None)."""
    n, n0 = int(round(window * fs)), int(round(settle * fs))
    m = tone_bin(f, n, fs)
    st = Stage(fs, quality=quality, filt=filt)
    L = st.latency_samples
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + L) / n)
    y = st.process(x)["out"][n0 + L:n0 + L + n]
    return dict(setting=setting_name(quality), os=st.os, filt=filt, drive=float(drive), tone=int(f),
                f_hz=m * fs / n, window_s=window, settle_s=settle, collision_order=collision_order(m, n),
                **alias_dbc(y, m, fs, st.os))


def _job(a):
    t = time.perf_counter()
    r = measure(*a[:4], **a[4])
    r["cpu_s"] = time.perf_counter() - t
    return r


def sweep(settings, drives=DRIVES, tones=SWEEP_HZ, window=WINDOW_S, settle=SETTLE_S, workers=None):
    """measure() over every (quality, filt) in `settings` x drives x tones; a flat list of result dicts.
    workers > 1 runs the points in that many processes (each point is independent)."""
    jobs = [(q, flt, d, f, dict(window=window, settle=settle)) for q, flt in settings for d in drives for f in tones]
    if workers and workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            return list(ex.map(_job, jobs))
    return [_job(j) for j in jobs]


def select(records, setting, filt, drive):
    return [r for r in records if r["setting"] == setting and r["filt"] == filt and r["drive"] == drive]


def worst(records, setting, filt, drive):
    """The record with the largest total for one setting / filter / drive."""
    return max(select(records, setting, filt, drive), key=lambda r: r["total_dbc"])


def load_ground_truth():
    return json.load(open(GROUND_TRUTH_JSON))


def table(records, drives=DRIVES):
    """Markdown tables of the total (dBc), one per drive: a row per tone, a column per setting / filter."""
    cols = list(dict.fromkeys((r["setting"], r["filt"]) for r in records))
    val = {(r["setting"], r["filt"], r["drive"], r["tone"]): r["total_dbc"] for r in records}
    lines = []
    for d in drives:
        lines += [f"\n{d:g} V, total non-harmonic energy 20 Hz-20 kHz, dBc",
                  "| tone Hz | " + " | ".join(f"{q} {flt}" for q, flt in cols) + " |", "|---" * (len(cols) + 1) + "|"]
        for f in sorted({r["tone"] for r in records if r["drive"] == d}):
            lines.append(f"| {f} | " + " | ".join(f"{val.get((q, flt, d, f), float('nan')):.1f}" for q, flt in cols) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Regenerate the 64x aliasing ground truth (dk/aliasing_64x.json).")
    ap.add_argument("--workers", type=int, default=os.cpu_count())
    a = ap.parse_args()
    t = time.perf_counter()
    recs = sweep([((GROUND_TRUTH_OS, None), flt) for flt in FILTERS], workers=a.workers)
    json.dump(dict(method=dict(fs=FS, settle_s=SETTLE_S, window_s=WINDOW_S, band_hz=BAND_HZ, mask_bins=MASK_BINS,
                               oversampling=GROUND_TRUTH_OS, prewarp_hz=None),
                   wall_s=time.perf_counter() - t, records=recs), open(GROUND_TRUTH_JSON, "w"), indent=1)
    print(table(recs))
