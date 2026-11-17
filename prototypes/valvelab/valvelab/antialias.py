"""Antialiasing on a tabulated static nonlinearity.

The stage is tabulated once (piecewise-linear f on a uniform grid). For ADAA
we need the antiderivative F; with piecewise-linear f, F is piecewise
quadratic and exact, so naive, oversampled and ADAA all use the *same* curve.

First-order ADAA (Parker, Zavalishin & Le Bivic, DAFx-16; Bilbao, Esqueda,
Parker & Valimaki, IEEE SPL 2017):
    y[n] = (F(x[n]) - F(x[n-1])) / (x[n] - x[n-1]),
    y[n] = f((x[n] + x[n-1]) / 2)          if |x[n] - x[n-1]| < eps
It behaves like a half-sample delay plus a gentle (sinc-shaped) low-pass.
"""
import numpy as np
from scipy.signal import resample_poly


class Table:
    def __init__(self, fn, xmax, m=4097):
        self.x0 = -xmax
        self.dx = 2 * xmax / (m - 1)
        self.m = m
        self.xs = np.linspace(-xmax, xmax, m)
        self.fs = fn(self.xs)
        # exact cumulative integral of the piecewise-linear f
        seg = 0.5 * (self.fs[1:] + self.fs[:-1]) * self.dx
        self.Fs = np.concatenate([[0.0], np.cumsum(seg)])
        self.xmax = xmax

    def _idx(self, x):
        u = (np.clip(x, -self.xmax, self.xmax) - self.x0) / self.dx
        i = np.minimum(np.floor(u).astype(int), self.m - 2)
        return i, u - i

    def f(self, x):
        i, t = self._idx(x)
        return self.fs[i] + t * (self.fs[i + 1] - self.fs[i])

    def F(self, x):
        x = np.asarray(x, float)
        xc = np.clip(x, -self.xmax, self.xmax)
        i, t = self._idx(xc)
        f0 = self.fs[i]
        f1 = self.fs[i + 1]
        h = t * self.dx
        Fin = self.Fs[i] + f0 * h + 0.5 * (f1 - f0) / self.dx * h * h
        # linear extension beyond the table (f held constant at the ends)
        Fin = Fin + np.where(x > self.xmax, (x - self.xmax) * self.fs[-1], 0.0)
        Fin = Fin + np.where(x < -self.xmax, (x + self.xmax) * self.fs[0], 0.0)
        return Fin


def naive(tab, x):
    return tab.f(x)


def adaa1(tab, x, eps=1e-6):
    xp = np.concatenate([[x[0]], x[:-1]])
    dx = x - xp
    Fx = tab.F(x)
    Fp = tab.F(xp)
    small = np.abs(dx) < eps
    with np.errstate(divide="ignore", invalid="ignore"):
        y = (Fx - Fp) / dx
    return np.where(small, tab.f(0.5 * (x + xp)), y)


def long_fir(L, taps_per_phase=64, beta=10.0):
    """A steeper, higher-stopband FIR than scipy's default (for comparison)."""
    from scipy.signal import firwin
    return firwin(taps_per_phase * L + 1, 1.0 / L, window=("kaiser", beta))


def oversampled(tab, x, L, method="naive", fir="default"):
    """fir='default': scipy resample_poly default, Kaiser(beta=5), 20L+1 taps.
    fir='long': long_fir(L) (64L+1 taps, Kaiser beta=10)."""
    if L == 1:
        return naive(tab, x) if method == "naive" else adaa1(tab, x)
    kw = {} if fir == "default" else {"window": long_fir(L)}
    xu = resample_poly(x, L, 1, **kw)
    yu = naive(tab, xu) if method == "naive" else adaa1(tab, xu)
    return resample_poly(yu, 1, L, **kw)


def bh4(n):
    a = (0.35875, 0.48829, 0.14128, 0.01168)
    k = np.arange(n) / (n - 1)
    return a[0] - a[1] * np.cos(2 * np.pi * k) + a[2] * np.cos(4 * np.pi * k) - a[3] * np.cos(6 * np.pi * k)


def coherent_freq(f, fs, n):
    """Nearest frequency that sits on an ODD FFT bin. With n a power of two,
    every harmonic and every aliased harmonic then lands exactly on a bin and
    no alias can coincide with a true harmonic, so a rectangular window gives
    a leakage-free measurement."""
    j = int(round(f * n / fs))
    if j % 2 == 0:
        j += 1
    return j * fs / n


def alias_metric(y, fs, f0, band=(20.0, 20000.0), guard=0, nharm=400, window=None):
    """Energy in non-harmonic bins within `band` relative to harmonic energy.
    Harmonics k*f0 below fs/2 (+/- guard bins) are 'wanted'; everything else
    in the band is aliasing. Use with coherent_freq() and window=None (exact),
    or window='bh4' with guard>=6 for non-coherent signals."""
    n = len(y)
    w = bh4(n) if window == "bh4" else np.ones(n)
    Y = np.abs(np.fft.rfft((y - y.mean()) * w)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / fs)
    binw = fs / n
    wanted = np.zeros_like(Y, bool)
    for k in range(1, nharm):
        fk = k * f0
        if fk >= fs / 2:
            break
        c = int(round(fk / binw))
        wanted[max(c - guard, 0):c + guard + 1] = True
    inband = (freqs >= band[0]) & (freqs <= band[1])
    alias = Y[inband & ~wanted].sum()
    harm = Y[wanted].sum()
    c1 = int(round(f0 / binw))
    fund = Y[max(c1 - guard, 0):c1 + guard + 1].sum()
    return dict(alias_to_harm_dB=10 * np.log10(alias / harm), fund_power=fund,
                spectrum_dB=10 * np.log10(Y / Y.max() + 1e-30), freqs=freqs)
