"""Post-distortion low-pass of the 6AS6 stage (#103): a linear 2-pole low-pass, decoupled from the plate, run at the
internal (oversampled) rate after the stage nonlinearity and before decimation.

Sources and labels: the #103 research comment (https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/103
#issuecomment-7021776633); S1-S9 are the source table of the #102 comment.
  - 12 dB/oct (second order), after the 6AS6 stage: S1, S2, S4, S6, S7 (sourced).
  - Positions Off / 4k / 7k: the 2003 revision, S1 and S7 (sourced). Other revisions: 5 / 9 kHz (S2), 6 / 9 kHz (S6).
  - Shape: 2-pole low-pass fitted to the digitised S5 (Super 15) LPF curve, f0 9535 Hz, Q 0.428 (0.4277 to four digits,
    the implementation brief), -3 dB at 4.92 kHz (F-a: RMS error 0.023 dB, max 0.12 dB over 300 Hz-7.5 kHz; the
    rounded f0 / Q put -3 dB at 4.93 kHz). The fit determines Q; the chart stops at -5 dB, so the second order comes
    from the slope statements above.
  - 7k position = the S5 curve: INFERRED (the comment's revisit 2; the alternative, label = -3 dB point, puts f0
    near 13.6 kHz). 4k position = the same shape scaled by 4/7: INFERRED.
  - Decoupled (buffered), not loading the plate: INFERRED from the fixed published corners (a plate-loaded RC would
    move its corner up to ~2.2x with mode and bias, #102 E5). No source gives a topology or component values, and none
    suggests an active or saturating filter, so the filter is linear.
  - The slight HF roll-off with the filter Off (S6, "except in P2") is left out (revisit 5; #111).
To follow a different revision, change F0_HZ (one line per position; keep the S5 shape or refit Q).

Method: TPT state-variable filter (Zavalishin, "The Art of VA Filter Design"; Simper, "Linear trapezoidal integrated
SVF", Cytomic 2013): g = tan(pi f0 / fs), k = 1/Q, a1 = 1/(1 + g(g + k)), a2 = g a1, a3 = g a2. It is the bilinear
transform of the analog prototype, so it runs at the 8x internal rate: at host rate the warping error reaches
13.6-25 dB at 20 kHz (F-b). Of the structures compared in F-c, it has the lowest float32 noise at 8x (direct-form
biquads -107 to -122 dBc), and it stays stable under fast cutoff modulation (F-d).
Increment form: Simper's state update ic <- 2 v - ic is computed as ic <- ic + 2 (v - ic), with v - ic formed
directly from the coefficients a2, a3 and b1 = 1 - a1 = g (g + k) a1 (no cancellation). It is the same filter in exact
arithmetic (same transfer function, same state trajectory under per-sample coefficient changes). In float32 its fixed
point for a constant input is the input whatever the coefficient rounding (d1 = d2 = 0 needs v3 = ic1 = 0), and the
states take small increments instead of being re-formed as 2 v - ic from two near-equal terms; only the states'
rounding deadband is left (a float32 step settles 4-8 ulp low, the direct form 11-18 ulp). Float32 vs float64, sines
over 20 Hz-20 kHz and white noise, 8x of 44.1 / 48 kHz: worst -131.5 dBc (4k position, 20 kHz, where the output is
22 dB below the input), against -117.2 dBc for the direct form (19.6 kHz at 8 x 44.1 kHz, found in the #119 review).
Much of the direct form's excess is coefficient rounding: float32 coefficients with float64 states alone read up to
-133.2 dBc in its 4k position, against -146.1 dBc worst for the increment form. RESULTS-lpf.md has the table.
Pre-warp: g = tan(pi fp / fs) * f0 / fp makes the response exact at fp. fp = f0 (the formula above) leaves 0.153 dB /
0.351 deg worst error against the prototype over 20 Hz-20 kHz at 8 x 44.1 kHz (4k / 7k position, at 20 kHz). The
switch pre-warps both positions at PREWARP_HZ instead, the frequency that minimises the worst complex error
|H_digital / H_analog - 1| over 20 Hz-20 kHz, both positions, 8 x 44.1 and 8 x 48 kHz (prewarp_minimax(); 15.69 kHz):
worst 0.064 dB / 0.293 deg, at no runtime cost.

Switching positions: every position's filter runs all the time, and the output crossfades linearly between them over
XFADE_S (F-d: an instant 7k -> 4k switch gives a -31 dBFS transient at 384 kHz in every structure, because the jump
itself is the discontinuity). The outputs are strongly correlated, so a linear (constant-amplitude) fade. Running both
filters costs two SVF ticks (~10 multiply-adds) per internal sample, small next to the Newton solve, and means a newly
selected filter is never started from a cold state. Smoothing of the other controls is #115.
"""
import numpy as np
from . import stage as _stage

Q = 0.4277                                  # S5 fit (F-a: 0.428 in the comment, 0.4277 to 4 digits)
F0_HZ = {"4k": 9535.0 * 4.0 / 7.0,          # S5 shape scaled by 4/7 (inferred)
         "7k": 9535.0}                      # S5 curve, -3 dB at 4.92 kHz (inferred mapping)
POSITIONS = ("off",) + tuple(F0_HZ)
XFADE_S = 0.015                             # position-change crossfade (F-d: ~10-20 ms)
PREWARP_HZ = 15700.0                        # prewarp_minimax(), rounded (module docstring)
OS_RATES = (8 * 44100.0, 8 * 48000.0)       # the internal rates PREWARP_HZ is chosen for (standard 8x)
BAND_HZ = (20.0, 20000.0)


def analog_response(f, f0, q=Q):
    """The analog prototype H(s) = 1 / ((s/w0)^2 + (s/w0)/Q + 1) at frequencies f (Hz)."""
    s = 1j * np.asarray(f, float) / f0
    return 1.0 / (s * s + s / q + 1.0)


def digital_response(f, fs, f0, q=Q, prewarp_hz=None):
    """The SVF's transfer function (the bilinear transform of analog_response with g = tan(pi fp/fs) f0/fp) at f."""
    fp = f0 if prewarp_hz is None else prewarp_hz
    z = np.exp(2j * np.pi * np.asarray(f, float) / fs)
    s = (z - 1) / (z + 1) / (np.tan(np.pi * fp / fs) * f0 / fp)
    return 1.0 / (s * s + s / q + 1.0)


def prewarp_minimax(rates=OS_RATES, band=BAND_HZ, n=2000):
    """The pre-warp frequency minimising the worst |H_digital / H_analog - 1| over `band`, every position and rate:
    how PREWARP_HZ was chosen. Returns (fp, worst error in dB)."""
    from scipy.optimize import minimize_scalar
    f = np.geomspace(*band, n)
    def worst(fp):
        return max(np.max(np.abs(digital_response(f, fs, f0, prewarp_hz=fp) / analog_response(f, f0) - 1))
                   for fs in rates for f0 in F0_HZ.values())
    m = minimize_scalar(worst, bounds=(min(F0_HZ.values()), band[1]), method="bounded", options=dict(xatol=1.0))
    return float(m.x), float(20 * np.log10(m.fun))


class SVF:
    """TPT state-variable low-pass (module docstring). prewarp_hz: None pre-warps at f0 (exact cutoff, the usual choice
    under modulation), else at that fixed frequency. dtype: np.float64 (default) or np.float32 for every coefficient,
    state and intermediate (the brief allows a float32 filter; the float32 noise test). The C++ core runs in double.
    set_f0 may be called between any two samples (modulation). Python floats have no denormal slowdown; the C++ port
    relies on the core's FTZ/DAZ guard."""

    def __init__(self, fs, f0, q=Q, prewarp_hz=None, dtype=np.float64):
        self.fs, self.q, self.prewarp_hz = float(fs), float(q), prewarp_hz
        if prewarp_hz is not None and not 0.0 < prewarp_hz < self.fs / 2:
            raise ValueError(f"prewarp_hz must be in (0, fs/2), got {prewarp_hz} at fs = {self.fs}")
        self.cast = float if np.dtype(dtype) == np.float64 else np.dtype(dtype).type
        self.set_f0(f0)
        self.reset()

    def set_f0(self, f0):
        if not 0.0 < f0 < self.fs / 2:
            raise ValueError(f"f0 must be in (0, fs/2), got {f0} at fs = {self.fs}")
        fp = f0 if self.prewarp_hz is None else self.prewarp_hz
        g = np.tan(np.pi * fp / self.fs) * f0 / fp
        a1 = 1.0 / (1.0 + g * (g + 1.0 / self.q))
        c = self.cast
        self.f0, self.a2, self.a3, self.b1 = f0, c(g * a1), c(g * g * a1), c(g * (g + 1.0 / self.q) * a1)

    def reset(self):
        self.ic1 = self.ic2 = self.cast(0.0)

    def tick(self, x):
        """One sample, increment form (module docstring): d1 = v1 - ic1, d2 = v2 - ic2; the output is v2."""
        a2, a3, b1, ic1, ic2 = self.a2, self.a3, self.b1, self.ic1, self.ic2
        v3 = x - ic2
        d1 = a2 * v3 - b1 * ic1
        d2 = a2 * ic1 + a3 * v3
        self.ic1, self.ic2 = ic1 + 2 * d1, ic2 + 2 * d2
        return ic2 + d2

    def process(self, x):
        x = np.asarray(x, dtype=np.float64 if self.cast is float else self.cast)
        tick = self.tick
        return np.array([tick(s) for s in (x.tolist() if self.cast is float else x)], dtype=x.dtype)


class PostFilter:
    """The Off / 4k / 7k switch at internal rate fs. position(): the target position; set_position() starts a linear
    crossfade from the current mix to it over XFADE_S (a new request mid-fade starts from wherever the mix is)."""

    def __init__(self, fs, position="off", q=Q, xfade_s=XFADE_S, prewarp_hz=PREWARP_HZ):
        self.svf = [SVF(fs, f0, q, prewarp_hz) for f0 in F0_HZ.values()]
        self.n_fade = max(1, int(round(xfade_s * fs)))
        self.g_to = self._onehot(position)
        self.reset()

    @staticmethod
    def _onehot(position):
        if position not in POSITIONS:
            raise ValueError(f"position must be one of {POSITIONS}, got {position!r}")
        return np.eye(len(POSITIONS))[POSITIONS.index(position)]

    def position(self):
        return POSITIONS[int(np.argmax(self.g_to))]

    def gains(self):
        """Current mix weights over POSITIONS (they sum to 1)."""
        t = min(self.t / self.n_fade, 1.0)
        return self.g_from + (self.g_to - self.g_from) * t

    def set_position(self, position):
        g_to = self._onehot(position)
        self.g_from, self.g_to, self.t = self.gains(), g_to, 0

    def reset(self):
        """Filter states to zero and the mix settled on the target position."""
        for f in self.svf:
            f.reset()
        self.g_from, self.t = self.g_to.copy(), self.n_fade

    def process(self, x):
        x = np.asarray(x, dtype=float)
        ys = np.vstack([x] + [f.process(x) for f in self.svf])       # rows in POSITIONS order
        t = np.minimum((self.t + np.arange(1, len(x) + 1)) / self.n_fade, 1.0)
        g = self.g_from[:, None] + (self.g_to - self.g_from)[:, None] * t
        self.t = min(self.t + len(x), self.n_fade)
        return np.sum(g * ys, axis=0)


class Stage(_stage.Stage):
    """dk6as6.stage.Stage with the post-distortion low-pass on 'out' inside the oversampled block: process_internal
    filters the stage's internal-rate output, so Stage.process decimates the filtered signal. lpf: initial position
    (POSITIONS); change it with self.lpf.set_position(). With "off" the output is the unfiltered stage's, bit for bit.
    The filter does not load the plate (decoupled), so the stage's own solve is unchanged."""

    def __init__(self, fs, lpf="off", **kw):
        self.lpf = None                         # the base __init__ calls reset() before self.fs is known
        super().__init__(fs, **kw)
        self.lpf = PostFilter(self.fs, lpf)

    def reset(self):
        super().reset()
        if self.lpf is not None:
            self.lpf.reset()

    def process_internal(self, vin, record=("out",)):
        rec = super().process_internal(vin, tuple(dict.fromkeys(("out",) + tuple(record))))   # keep the filter fed
        out = self.lpf.process(rec["out"])
        if "out" in record:
            rec["out"] = out
        else:
            del rec["out"]
        return rec
