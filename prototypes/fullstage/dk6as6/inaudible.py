"""The inaudibility criterion for the 6AS6 stage's aliasing (#113 rework, Michael's decision of 21 Nov 2026).

It replaces the dBc gate (dk6as6.aliasing.QUALITY_GATE_DBC, the 12AX7 stage's engineering budget) for this path: every
alias product must sit below FLOOR_DBFS (-100 dBFS) AND at least UNDER_THRESHOLD_DB (10 dB) under the absolute threshold
of hearing at its own frequency. In one number per product, its margin is

    margin(f) = limit_dbfs(f) - level_dbfs,   limit_dbfs(f) = min(FLOOR_DBFS, T_q(f) - UNDER_THRESHOLD_DB - SPL_FS_DB)

and a point passes when every product's margin is >= 0.

Threshold of hearing T_q(f) (dB SPL): Terhardt's approximation of the absolute threshold in quiet,
    T_q(f) = 3.64 (f/kHz)^-0.8 - 6.5 exp(-0.6 (f/kHz - 3.3)^2) + 1e-3 (f/kHz)^4,
E. Terhardt, "Calculating virtual pitch", Hearing Research 1 (1979) 155-182; in the form used for perceptual audio
coding by T. Painter and A. Spanias, "Perceptual coding of digital audio", Proc. IEEE 88(4) (2000) 451-515, eq. (1).
It is a smooth fit for a young listener with normal hearing, and its minimum is about -5 dB SPL near 3.3 kHz. Above
about 12 kHz its 1e-3 f^4 term climbs steeply (about 66 dB SPL at 16 kHz, 160 dB SPL at 20 kHz), so the approximation
is lenient there. The -100 dBFS floor is the binding term wherever T_q(f) - 10 - 105 >= -100, i.e. below about 170 Hz
and above about 10.97 kHz, so the steep ends of the curve decide no product; between them the limit falls to about
-120 dBFS near 3.3 kHz.

Calibration (an assumption, as the brief asks): SPL_FS_DB = 105 dB SPL for a full-scale sine (0 dBFS, the AES17
convention). That is the cinema monitoring reference of SMPTE RP 200 (pink noise at -20 dBFS RMS reproduced at 85 dB
SPL, C-weighted, per screen channel), taken for a full-scale sine. It is a loud listening level for music production.
A product L dBFS is then L + 105 dB SPL. A quieter calibration only loosens the threshold term. spl_headroom_db reports
how far each point's calibration could rise before the threshold term fails: it passes for any 0 dBFS level up to
SPL_FS_DB + spl_headroom_db.

Full scale (0 dBFS): the analysed output's own sample peak, i.e. the alias level when the Output control puts the
processed signal at full scale (the loudest level that does not clip). It does not depend on the provisional host
volts mapping (#22, plugin/src/map_params.h: 1/64 of full scale per volt at Output 1). At 8 V drive the 6AS6 output
peaks at 75-90 V, which that mapping would put 1.4-3 dB OVER full scale at Output 1. So normalising to the peak is the
loudest non-clipping case at 8 V, and at 2 V it is louder than that mapping.

Products: the analysis is dk/aliasing.py's (a coherent tone on a DFT bin, periodic Hann window, every harmonic bin
+-MASK_BINS masked, 20 Hz-20 kHz). In steady state every alias is a line on its own bin, and a line of amplitude a reads
|X| = a n / 4 at its centre bin with this window. So level_dbfs(bin) = 20 log10(4 |X| / n / peak) is the amplitude of a
line centred on that bin, and every non-harmonic in-band bin is read as a product. Energy that is not a line (a settling
transient) is read per bin as well, which is the 1 / window Hz resolution of the spectrum. In a 0.1 s CI window, lines
less than 10 Hz apart share a bin and read as their sum (conservative).
Source of the worst product: dk.aliasing.fold_order credits each bin to the lowest harmonic order that folds onto it;
"internal" = folded inside the stage (above os fs / 2), "resampler" = reached the band through the half-band FIRs, as in
dk.aliasing.alias_dbc.
"""
import numpy as np
from dk.aliasing import MASK_BINS, BAND_HZ, fold_order, collision_order

FLOOR_DBFS = -100.0                 # every product below this (Michael, 21 Nov 2026)
UNDER_THRESHOLD_DB = 10.0           # ... and at least this far under the threshold of hearing at its frequency
SPL_FS_DB = 105.0                   # ASSUMPTION: a 0 dBFS sine at 105 dB SPL (SMPTE RP 200 reference, module docstring)
DRIVES = (2.0, 8.0)                 # V peak: the brief's two drives
RATES = (48000, 44100)
CITATION = ("E. Terhardt, Calculating virtual pitch, Hearing Research 1 (1979) 155-182; T. Painter, A. Spanias, "
            "Perceptual coding of digital audio, Proc. IEEE 88(4) (2000) 451-515, eq. (1)")
CALIBRATION = "0 dBFS sine = 105 dB SPL (SMPTE RP 200: -20 dBFS RMS pink noise = 85 dB SPL C), 0 dBFS = output peak"


def threshold_db_spl(f):
    """Terhardt's absolute threshold of hearing in quiet, dB SPL, at f Hz (module docstring)."""
    k = np.asarray(f, float) / 1000.0
    return 3.64 * k ** -0.8 - 6.5 * np.exp(-0.6 * (k - 3.3) ** 2) + 1e-3 * k ** 4


def limit_dbfs(f, spl_fs_db=SPL_FS_DB):
    """The highest level (dBFS) an alias product at f Hz may have: min(-100, T_q(f) - 10 - SPL at full scale)."""
    return np.minimum(FLOOR_DBFS, threshold_db_spl(f) - UNDER_THRESHOLD_DB - spl_fs_db)


def products(y, m, fs, band=BAND_HZ, mask=MASK_BINS):
    """y: one analysis window of output, m: the tone's bin. Returns (f_hz, level_dbfs, fund_dbfs, peak) for every
    non-harmonic bin in `band`: level re the window's sample peak (0 dBFS) of the mean-removed output."""
    n = len(y)
    y = np.asarray(y, float)
    if not np.all(np.isfinite(y)):
        raise ValueError("NaN/Inf audio in the analysis window: nothing to assess")
    y = y - np.mean(y)
    peak = float(np.max(np.abs(y)))
    if not peak > 0.0:
        raise ValueError("silent analysis window: no output peak to reference")
    a = 4.0 * np.abs(np.fft.rfft(y * np.hanning(n + 1)[:-1])) / n
    fb = np.arange(len(a)) * fs / n
    harm = np.zeros(len(a), bool)
    for k in range(1, n // (2 * m) + 1):
        harm[max(k * m - mask, 0):k * m + mask + 1] = True
    sel = (fb >= band[0]) & (fb <= band[1]) & ~harm
    lv = 20.0 * np.log10(np.maximum(a[sel], 1e-300) / peak)
    return fb[sel], lv, float(20.0 * np.log10(a[m] / peak)), peak


def assess(y, m, fs, os_=None, spl_fs_db=SPL_FS_DB):
    """The criterion on one analysis window (module docstring). Returns:
      passed            every product's margin >= 0
      margin_db         the smallest margin (dB; negative = fails), at worst_hz, level worst_dbfs, limit there
      worst_source      "internal" / "resampler" / "other" for the worst product (with os_)
      loudest_dbfs      the loudest product (dBFS) and loudest_hz
      floor_margin_db   FLOOR_DBFS - loudest_dbfs (the -100 dBFS term alone)
      spl_headroom_db   min over products of (T_q - 10 - SPL_FS_DB - level): how far the calibration could rise
      n_failing         products with margin < 0
      fund_dbfs, peak   the fundamental re the peak, and the peak itself (output units)"""
    f, lv, fund, peak = products(y, m, fs)
    lim = limit_dbfs(f, spl_fs_db)
    marg = lim - lv
    j, jl = int(np.argmin(marg)), int(np.argmax(lv))
    thr = threshold_db_spl(f) - UNDER_THRESHOLD_DB - spl_fs_db - lv
    out = dict(passed=bool(marg[j] >= 0.0), margin_db=float(marg[j]), worst_hz=float(f[j]), worst_dbfs=float(lv[j]),
               worst_limit_dbfs=float(lim[j]), loudest_dbfs=float(lv[jl]), loudest_hz=float(f[jl]),
               floor_margin_db=float(FLOOR_DBFS - lv[jl]), spl_headroom_db=float(np.min(thr)),
               n_failing=int(np.sum(marg < 0.0)), fund_dbfs=fund, peak=peak)
    if os_ is not None:
        n = len(y)
        b = int(round(f[j] * n / fs))
        k = fold_order(m, n, max(collision_order(m, n) - 1, int(np.ceil(os_ * n / m)), 1000))[b]
        out["worst_source"] = "other" if k == 0 else ("internal" if k * m * fs / n > os_ * fs / 2 else "resampler")
        out["worst_order"] = int(k)
    return out
