"""Harmonic analysis of a memoryless map driven by a sine."""
import numpy as np


def harmonics(fn, amp, n=4096, nh=20, offset=0.0):
    """Exact harmonic amplitudes of y = fn(offset + amp*sin) using one period
    sampled at n points (no aliasing worry: we evaluate the continuous map at
    exact phases and n >> 2*nh). Returns |H_k| for k=1..nh (volts peak)."""
    t = np.arange(n) / n
    y = fn(offset + amp * np.sin(2 * np.pi * t))
    Y = np.fft.rfft(y - y.mean()) * 2.0 / n
    return np.abs(Y[1:nh + 1])


def harmonic_profile(fn, amp, **kw):
    h = harmonics(fn, amp, **kw)
    h1 = h[0]
    rel = h / h1
    thd = np.sqrt(np.sum(rel[1:] ** 2))
    even = np.sqrt(np.sum(rel[1::2] ** 2))      # H2, H4, ...
    odd = np.sqrt(np.sum(rel[2::2] ** 2))       # H3, H5, ...
    with np.errstate(divide="ignore"):
        db = 20 * np.log10(np.maximum(rel, 1e-15))
    return dict(H1=h1, rel=rel, dB=db, thd=thd, thd_pct=100 * thd,
                even=even, odd=odd,
                even_minus_odd_dB=20 * np.log10(max(even, 1e-15) / max(odd, 1e-15)))
