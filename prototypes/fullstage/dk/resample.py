"""Streaming 2^k-times oversampling as a cascade of 2x half-band stages (issue #14).

Replaces the one-shot whole-buffer Kaiser FIR (511 taps at 8x) that `Stage.process` used before: that
filter kept no state between calls, so block-wise processing clicked at every boundary, and it hid its
~1.3 ms delay by trimming the group delay off a whole buffer. These filters keep their state, so
`process()` can be called with blocks of any size and the output is the same as one call on the whole
buffer; the delay is reported instead of trimmed (`latency_samples`, at the base rate).

Two filter families, each a cascade of 2x stages (stage 0 next to the base rate is the sharp one; later
stages only have to protect the 0-20 kHz band, so they are short):

* "fir": linear-phase half-band FIR (scipy firwin, Kaiser window, cutoff fs/4 of the higher rate). In a
  half-band FIR every second tap is zero, so the polyphase branches hold only the non-zero taps. Linear
  phase: the whole chain is a pure delay in the passband. A short integer delay at the internal rate makes
  the total latency a whole number of base-rate samples so callers can compensate it exactly.
* "iir": polyphase IIR half-band, two parallel chains of first-order allpasses in z^-1 at the lower rate,
  H(z) = (A0(z^2) + z^-1 A1(z^2)) / 2 (Valenzuela & Constantinides 1983). Coefficients from the
  closed-form elliptic design used by Laurent de Soras' HIIR, `PolyphaseIir2Designer::compute_coefs_spec_order_tbw`
  (fetched 4 Nov 2026 from https://github.com/unevens/hiir/blob/master/PolyphaseIir2Designer.h, WTFPL;
  the formulas are re-implemented here, not copied). Even-indexed coefficients go to A0, odd to A1, as in
  HIIR's Upsampler2x/Downsampler2x. About 1/5 of the FIR's multiplies, but non-linear phase near 20 kHz, so
  `latency_samples` is the group delay at 1 kHz rounded to whole samples (nominal, not exact).

Filter specs (dk/tests/test_resample.py checks them): passband flat to 0.01 dB up to 20 kHz at a 48 kHz
base rate, and at least 85 dB rejection of every band that folds onto 0-20 kHz (images k*fs +- 20 kHz).
"""
import math
import numpy as np
from scipy.signal import firwin, lfilter, lfilter_zi

# ---- design ---------------------------------------------------------------------------------------------

def hiir_coefs(nbr_coefs, transition):
    """Allpass coefficients of a polyphase IIR half-band, after HIIR's compute_coefs_spec_order_tbw.
    transition: normalised transition bandwidth (fraction of the HIGH rate, 0..0.5). Order = 2*n + 1."""
    assert nbr_coefs > 0 and 0 < transition < 0.5
    k = math.tan((1 - transition * 2) * math.pi / 4) ** 2
    kksqrt = (1 - k * k) ** 0.25
    e = 0.5 * (1 - kksqrt) / (1 + kksqrt)
    e4 = e ** 4
    q = e * (1 + e4 * (2 + e4 * (15 + 150 * e4)))
    order = 2 * nbr_coefs + 1

    def acc_num(c):
        i, j, acc = 0, 1, 0.0
        while True:
            t = q ** (i * (i + 1)) * math.sin((2 * i + 1) * c * math.pi / order) * j
            acc += t
            j = -j
            i += 1
            if abs(t) <= 1e-100:
                return acc

    def acc_den(c):
        i, j, acc = 1, -1, 0.0
        while True:
            t = q ** (i * i) * math.cos(2 * i * c * math.pi / order) * j
            acc += t
            j = -j
            i += 1
            if abs(t) <= 1e-100:
                return acc

    out = []
    for idx in range(nbr_coefs):
        c = idx + 1
        ww = acc_num(c) * q ** 0.25 / (acc_den(c) + 0.5)
        w2 = ww * ww
        x = math.sqrt((1 - w2 * k) * (1 - w2 / k)) / (1 + w2)
        out.append((1 - x) / (1 + x))
    return np.array(out)


def _hiir_q(transition):
    k = math.tan((1 - transition * 2) * math.pi / 4) ** 2
    kksqrt = (1 - k * k) ** 0.25
    e = 0.5 * (1 - kksqrt) / (1 + kksqrt)
    e4 = e ** 4
    return e * (1 + e4 * (2 + e4 * (15 + 150 * e4)))


def hiir_atten_db(nbr_coefs, transition):
    """HIIR's compute_atten: design stopband attenuation (dB) for that order and transition."""
    a = 4 * math.exp((2 * nbr_coefs + 1) * 0.5 * math.log(_hiir_q(transition)))
    return -10 * math.log10(a / (1 + a))


def hiir_nbr_coefs(attenuation, transition):
    """HIIR's compute_order: fewest coefficients giving `attenuation` dB for that transition."""
    p2 = 10.0 ** (-attenuation / 10)
    a = p2 / (1 - p2)
    order = math.ceil(math.log(a * a / 16) / math.log(_hiir_q(transition)))
    order = max(order | 1, 3)
    return (order - 1) // 2


# Design. Stage i has high rate R = 2^(i+1) fs. It must pass 0-20 kHz and stop everything that its decimation
# folds onto 0-20 kHz, i.e. from R/2 - 20 kHz (28 kHz for stage 0 at a 48 kHz base). HIIR's transition is
# centred on R/4, so tbw = 2 (R/4 - 20 kHz) / R. The FIR lengths were chosen to meet the same spec.
PASS_HZ = 20000.0
FIR_FIRST, FIR_LATER = (127, 9.0), (23, 9.0)   # (taps, Kaiser beta): >= 92.8 dB stop, < 0.0003 dB ripple
# At 44.1 kHz the 20 kHz -> fs-20 kHz transition is 4.1 kHz instead of 8 kHz. Scale the tap length inversely with
# normalised transition width, then round up to 4m+3: 127*(8000/96000)/(4100/88200) -> 231. See RESULTS.md (#96).
FIR_FIRST_44100 = (231, 9.0)
FIRST_FIR_TAPS_BY_RATE = {44100: FIR_FIRST_44100[0]}


def first_fir_taps(fs):
    """Validated rate exceptions; other rates retain historical taps, without an aliasing certification."""
    return FIRST_FIR_TAPS_BY_RATE.get(fs, FIR_FIRST[0])


IIR_ATTEN_DB = 96.0                             # per-stage design attenuation -> 6, 3, 2, 2 coefficients


class _Lf:
    """lfilter with persistent state."""
    def __init__(self, b, a=(1.0,)):
        self.b, self.a = np.asarray(b, float), np.asarray(a, float)
        self.zi0 = lfilter_zi(self.b, self.a) if max(len(self.b), len(self.a)) > 1 else np.zeros(0)
        self.z = np.zeros(max(len(self.b), len(self.a)) - 1)
        self.dc = self.b.sum() / self.a.sum()

    def prime(self, c):
        """Steady state for a constant input c; returns the constant output."""
        self.z = self.zi0 * c
        return self.dc * c

    def __call__(self, x):
        if len(x) == 0:
            return np.zeros(0)
        if len(self.z) == 0:
            return self.b[0] * x
        y, self.z = lfilter(self.b, self.a, x, zi=self.z)
        return y


class _Chain:
    def __init__(self, fs):
        self.fs = list(fs)

    def prime(self, c):
        for f in self.fs:
            c = f.prime(c)
        return c

    def __call__(self, x):
        for f in self.fs:
            x = f(x)
        return x


def _allpass_chain(coefs):
    return _Chain(_Lf([a, 1.0], [1.0, a]) for a in coefs)   # (a + z^-1) / (1 + a z^-1)


class _Delay(_Lf):
    def __init__(self, n):
        super().__init__(np.r_[np.zeros(n), 1.0])


# ---- 2x stages: up(x) at rate r -> 2r, down(y) at 2r -> r ----------------------------------------------

class _FirHB:
    def __init__(self, ntaps, beta):
        assert ntaps % 4 == 3, "half-band length 4m+3 keeps both end taps non-zero"
        self.h = firwin(ntaps, 0.5, window=("kaiser", beta))
        self.delay_hi = (ntaps - 1) // 2              # samples at the higher rate, each direction
        self.nz = int(np.count_nonzero(np.abs(self.h) > 1e-15))

    def make_up(self):
        e, o = _Lf(2 * self.h[0::2]), _Lf(2 * self.h[1::2])

        def up(x):
            y = np.empty(2 * len(x))
            y[0::2] = e(x)
            y[1::2] = o(x)
            return y
        return up

    def make_down(self):
        e, o = _Lf(self.h[0::2]), _Lf(np.r_[0.0, self.h[1::2]])   # odd branch is one low-rate sample late

        def down(y):
            return e(y[0::2]) + o(y[1::2])
        down.prime = lambda c: e.prime(c) + o.prime(c)
        return down

    def freq(self, f, fs_hi):
        w = np.exp(-2j * np.pi * np.asarray(f, float) / fs_hi)
        return np.polyval(self.h[::-1], w)                         # sum h[n] w^n


class _IirHB:
    def __init__(self, nbr_coefs, transition):
        self.a = hiir_coefs(nbr_coefs, transition)
        self.atten_db = hiir_atten_db(nbr_coefs, transition)
        self.nz = nbr_coefs

    def make_up(self):
        p0, p1 = _allpass_chain(self.a[0::2]), _allpass_chain(self.a[1::2])

        def up(x):
            y = np.empty(2 * len(x))
            y[0::2] = p0(x)
            y[1::2] = p1(x)
            return y
        return up

    def make_down(self):
        p0, p1 = _allpass_chain(self.a[0::2]), _Chain([_Delay(1)] + _allpass_chain(self.a[1::2]).fs)

        def down(y):
            return 0.5 * (p0(y[0::2]) + p1(y[1::2]))
        down.prime = lambda c: 0.5 * (p0.prime(c) + p1.prime(c))
        return down

    def freq(self, f, fs_hi):
        """H(z) = (A0(z^2) + z^-1 A1(z^2)) / 2 at the higher rate (= up and down response, x2 gain on up)."""
        zi = np.exp(-2j * np.pi * np.asarray(f, float) / fs_hi)   # z^-1
        z2 = zi * zi
        def A(cs):
            return np.prod([(a + z2) / (1 + a * z2) for a in cs], axis=0) if len(cs) else 1.0
        return 0.5 * (A(self.a[0::2]) + zi * A(self.a[1::2]))


def design(kind, os, fs):
    n = int(round(math.log2(os)))
    assert 2 ** n == os and n >= 1, "half-band cascades need os = 2, 4, 8, 16, ..."
    st = []
    for i in range(n):
        R = fs * 2 ** (i + 1)
        tbw = 2 * (R / 4 - PASS_HZ) / R          # HIIR: passband edge R(1/4 - tbw/2) = 20 kHz, stop R/2 - 20 kHz
        if kind == "fir":
            first = (first_fir_taps(fs), FIR_FIRST[1])
            st.append(_FirHB(*(first if i == 0 else FIR_LATER)))
        elif kind == "iir":
            st.append(_IirHB(hiir_nbr_coefs(IIR_ATTEN_DB, tbw), tbw))
        else:
            raise ValueError(kind)
    return st


class Resampler:
    """Streaming os-times up -> (stage at os*fs) -> down. Make one per signal path direction:
    `up(x)` (base rate in, internal rate out) and `down(y)` (internal in, base out), each stateful.
    `channels` names extra decimated signals (each gets its own decimator state)."""

    def __init__(self, fs, os, kind="fir"):
        self.fs, self.os, self.kind = float(fs), int(os), kind
        self.stages = design(kind, self.os, self.fs)
        d_int = 0                                      # total delay at the internal rate (fir: exact)
        if kind == "fir":
            for i, s in enumerate(self.stages):         # stage i high rate = 2^(i+1) fs
                d_int += 2 * s.delay_hi * self.os // 2 ** (i + 1)
            self.pad = (-d_int) % self.os               # extra internal-rate delay -> whole base samples
            self._lat = (d_int + self.pad) // self.os
        else:
            self.pad = 0
            f = 1000.0
            df = 1.0
            def ph(ff):
                return np.angle(self.chain_response(np.array([ff]))[0])
            gd = -(ph(f + df) - ph(f - df)) / (2 * np.pi * 2 * df) * self.fs
            self._lat = int(round(gd))
        self.reset()

    @property
    def latency_samples(self):
        """Delay of up -> down at the base rate, in samples (fir: exact; iir: group delay at 1 kHz, rounded)."""
        return self._lat

    @property
    def latency_ms(self):
        return 1e3 * self._lat / self.fs

    @property
    def up_delay_internal(self):
        """Delay of the interpolator alone, in internal-rate samples (fir: exact; iir: 1 kHz group delay)."""
        if self.kind == "fir":
            return sum(s.delay_hi * self.os // 2 ** (i + 1) for i, s in enumerate(self.stages))
        f, df = 1000.0, 1.0
        def ph(ff):
            return np.angle(self.up_response(np.array([ff]))[0])
        return -(ph(f + df) - ph(f - df)) / (2 * np.pi * 2 * df) * self.fs * self.os

    def macs(self):
        """Multiplies per base-rate sample (up, down), polyphase / half-band structure, zero taps skipped."""
        up = sum(s.nz * 2 ** i for i, s in enumerate(self.stages))
        return up, up

    def reset(self):
        self._up = [s.make_up() for s in self.stages]
        self._down = {}                                # input history starts at zero (lfilter state = 0)

    def up(self, x):
        x = np.asarray(x, float)
        for f in self._up:
            x = f(x)
        return x

    def down(self, y, channel="out"):
        """Decimate an internal-rate signal. The first call after reset() primes this channel's state with its
        first sample (the operating-point value), so a signal that starts at DC has no start-up transient."""
        y = np.asarray(y, float)
        assert len(y) % self.os == 0
        if len(y) == 0:
            return np.zeros(0)
        ch = self._down.get(channel)
        if ch is None:
            fs_ = ([_Delay(self.pad)] if self.pad else []) + [s.make_down() for s in reversed(self.stages)]
            c = y[0]
            for f in fs_:
                c = f.prime(c)
            ch = self._down[channel] = fs_
        for f in ch:
            y = f(y)
        return y

    # ---- analysis
    def up_response(self, f):
        """Complex response of the interpolator at base-rate frequencies f (gain normalised to 1)."""
        H = np.ones(len(f), complex)
        for i, s in enumerate(self.stages):
            H *= s.freq(f, self.fs * 2 ** (i + 1))
        return H

    def chain_response(self, f):
        """up -> down (including the pad), at base-rate frequencies f."""
        H = self.up_response(f) ** 2
        return H * np.exp(-2j * np.pi * np.asarray(f, float) * self.pad / (self.fs * self.os))

    def internal_response(self, f_int):
        """Interpolator magnitude at internal-rate frequencies (images); same filter shape as the decimator."""
        return self.up_response(np.asarray(f_int, float))
