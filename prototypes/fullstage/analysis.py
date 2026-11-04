"""Shared measurement helpers for the full-stage references and models.

harmonics(): amplitude of H1..Hn of a periodic signal by a single-bin DFT over an exact integer
number of cycles (no window needed, no leakage when the signal is settled and periodic).
"""
import numpy as np


def load_wrdata(path, ncols):
    """ngspice `wrdata` (without wr_singlescale) writes (t, v) pairs per vector.
    Returns t and an (N, ncols) array of the vectors on ngspice's own time points."""
    d = np.loadtxt(path, skiprows=1 if open(path).readline().strip()[0].isalpha() else 0)
    return d[:, 0], d[:, 1:2 * ncols:2]


def resample_uniform(t, x, fs, t0=None, t1=None):
    t0 = t[0] if t0 is None else t0
    t1 = t[-1] if t1 is None else t1
    tu = np.arange(t0, t1, 1.0 / fs)
    return tu, np.interp(tu, t, x)


def harmonics(x, fs, f0, ncycles, nharm=10):
    """Amplitudes (peak) of H1..H{nharm} over the LAST `ncycles` cycles of x (uniformly sampled)."""
    n = int(round(ncycles * fs / f0))
    seg = x[-n:]
    k = np.arange(n)
    out = []
    for h in range(1, nharm + 1):
        out.append(2.0 * abs(np.dot(seg, np.exp(-2j * np.pi * h * f0 * k / fs))) / n)
    return np.array(out)


def thd(h):
    return float(np.sqrt(np.sum(h[1:] ** 2)) / h[0])


def db_rel(h):
    """Harmonic levels in dB relative to the fundamental (dBc)."""
    return 20 * np.log10(np.maximum(h, 1e-30) / h[0])


def cycle_mean(x, fs, f0):
    """One-cycle moving average (removes the 1 kHz content, leaves bias shift)."""
    n = int(round(fs / f0))
    return np.convolve(x, np.ones(n) / n, "same")


def recovery_tau(t, b, t_end, rest):
    """Time after t_end for (b - rest) to fall to 1/e of its value at t_end (cycle-averaged bias)."""
    i0 = np.searchsorted(t, t_end)
    d0 = b[i0] - rest
    rel = (b[i0:] - rest) / d0
    j = np.argmax(rel < np.exp(-1))
    return float(t[i0 + j] - t[i0]) if rel[j] < np.exp(-1) else float("nan")
