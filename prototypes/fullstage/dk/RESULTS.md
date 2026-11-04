# DK stage — validation at 8× oversampling (4 Nov 2026)

`python -m dk.validate` → `results.json`; `python -m pytest -q dk/tests` → **12 passed, all criteria pass**.

Setup: 48 kHz base rate, **8× oversampling (stage at 384 kHz)**, Drive = Bias = Output = 1.
History: the first pass ran at 4× and failed only the small-signal criterion (0.21 dB at 20 kHz, see the
diagnosis below). Michael chose to run at 8× rather than add a warping correction or relax the target;
tolerances are unchanged. 4× is still available with `Stage(fs, os=4)`.

Oversampling filters (#14, `dk/resample.py`): streaming cascade of 2× half-band stages that keep their state, so
`Stage.process` can run block by block. Default `filt="fir"`: linear-phase half-band FIRs (Kaiser), 127 taps β 9 next
to the base rate, then 23 taps β 9; 143 multiplies per base sample each way at 8×; **latency 72 samples = 1.50 ms**
at 48 kHz, reported by `Stage.latency_samples` and compensated by `validate.py` and the tests (no trim).
Measured through the streaming code: passband within 0.0011 dB to 20 kHz (whole chain 0.0021 dB), ≥ 92.8 dB
rejection of every band that folds onto 0–20 kHz. `filt="iir"`: polyphase allpass half-bands (HIIR's elliptic design,
96 dB per stage: 6 + 3 + 2 coefficients), 20 multiplies per sample each way, nominal latency 5 samples (0.10 ms),
non-linear phase near 20 kHz. Replaces the one-shot 511-tap Kaiser FIR (511 MACs, ~1.3 ms delay hidden by trimming).
References: **(ii)** ngspice with the same Dempwolf–Zölzer equations and parameters (`spice/models_dz/`) —
the pass criteria are tested against this, so it measures *method* error. **(i)** ngspice with Koren's
12AX7 — adds *valve-model* mismatch; informational only.
The criteria are our proposed first-pass targets (approved as defaults, to be tightened).

## Against (ii), same valve equations — the test

| criterion | target | result | |
|---|---|---|---|
| operating point (Va, Vk, Ia, Vgk) | within 1 % | all within 2×10⁻⁹ relative (Va 204.536 V, Vk 1.4321 V, Ia 0.9546 mA, Vgk −1.5178 V) | pass |
| small signal, 20 Hz–20 kHz | within 0.1 dB | **0.052 dB** worst, at 19.95 kHz (with resampling filters, 0.050 dB with the old Kaiser; stage alone 0.052 dB). −0.0071 dB at 10 kHz, < 0.001 dB at 1 kHz and below. At 4× it was 0.211 dB (fail) | pass |
| H2–H5 per drive level (where the reference is above −100 dBc) | within 1 dB | worst 0.137 dB (H5 at 0.5 V: -97.870 vs ref -97.733 dBc, i.e. reference precision at ~−98 dBc, see note below); H3 at 0.05 V -0.0032 dB; every other tested harmonic ≤ 0.0156 dB; THD 0.169/1.776/16.700/60.691 % vs ref 0.169/1.776/16.699/60.686 % | pass |
| 4 V burst: cycle-averaged Vgk shift, recovery τ, trajectory | 5 % / 10 % / 5 % RMS of shift | shift −2.841 V vs −2.842 V (0.03 %); τ 12.755 ms vs 12.75 ms (0.04 %); trajectory RMS 0.003 % of shift; minimum at 30.0 ms in both | pass |

**Measurement fix (issue #2).** Earlier figures (H5 at 0.5 V −0.645 dB, H3 at 0.05 V 0.22 dB) were an artefact: `Stage.process` resamples offline, so its last ~1.3 ms is FIR ringing against zero padding, and `harmonics()` analyses the last cycles of the buffer. `validate.py` now simulates 20 ms extra and ends the analysis window before that tail. The remaining -0.138 dB on H5 at 0.5 V is reference precision: an ngspice run with reltol 1e-6 and a 0.5 µs step gave −97.866 dBc (audit, 4 Nov 2026), within 0.004 dB of DK; the committed reference uses default tolerances. Since #14 the resampler streams: every run feeds `latency_samples` extra input and drops that many output samples, so the analysed window never depends on input past the end of the signal, and the 20 ms guard is gone. Table rows below are generated from `results.json`.

Excluded harmonics (reference below −100 dBc, the simulator's numerical floor; see `spice/RESULTS.md`):
H4/H5 at 0.05 V. They are listed in `results.json` with `tested: false`.

### Diagnosis of the small-signal failure
It is the trapezoidal rule's frequency warping, not a derivation error. The stage's high-frequency
roll-off comes from the Miller capacitance (CGP × stage gain) against the 68k grid stopper, and the
bilinear map moves that pole. Evidence: the error at 20 kHz scales as 1/fs² with the internal rate —

| internal rate | 96 kHz (2×) | 192 kHz (4×) | 384 kHz (8×) | 768 kHz (16×) |
|---|---|---|---|---|
| max deviation 20 Hz–20 kHz | 0.956 dB | 0.212 dB | 0.052 dB | 0.013 dB |

and below 1 kHz the DK response matches ngspice's AC analysis to < 10⁻⁵ dB. Options were: run at 8×; pre-warp the Miller pole /
use a higher-order discretisation for the small capacitances; or relax the criterion above ~10 kHz.
**Decision (Michael, 4 Nov 2026): 8×.** Re-measured at 8×: 0.050 dB (chain), 0.052 dB (stage) — pass.
A warping correction remains an option for the C++ core if the CPU cost of 8× matters there.

## Resampler change (#14): before / after, against (ii)
`python -m dk.validate` before (main at 7c182b4, 511-tap Kaiser, whole-buffer, delay trimmed) and after, at 8×.
IIR is `run(filt="iir")` in the same harness. Aliasing: 10 kHz at 8 V, the `ALIASING.md` metric
(`dk/tests/test_aliasing.py`); 16× and 4× measured the same way for reference.

| metric | Kaiser 511 (before) | half-band FIR (default) | half-band IIR |
|---|---|---|---|
| small signal, worst 20 Hz–20 kHz (chain) | 0.0503 dB | 0.0517 dB | 0.0515 dB |
| H2–H5 worst tested dev | 0.1375 dB (H5, 0.5 V) | 0.1370 dB | 0.1368 dB |
| largest change in any tested H2–H5 dev vs before | — | 0.0010 dB | 0.0010 dB |
| burst shift / τ / trajectory | 0.032 % / 0.041 % / 0.0036 % | 0.030 % / 0.041 % / 0.0034 % | 0.030 % / 0.082 % / 0.0047 % |
| operating point | 2×10⁻⁹ | unchanged | unchanged |
| aliasing 10 kHz / 8 V, 8× | −59.7 dBc | −59.72 dBc | −59.73 dBc |
| aliasing 10 kHz / 8 V, 16× / 4× | — | −74.87 / −44.20 dBc | −74.75 / −44.21 dBc |
| latency, 8× | ~1.33 ms (hidden by trim) | 72 samples, 1.500 ms (exact) | 5 samples, 0.104 ms (nominal) |
| multiplies per base sample, up / down, 8× | 511 / 511 | 143 / 143 | 20 / 20 |
| streams in blocks | no (clicks) | yes, = whole buffer to < 1e-9 | yes |

Latency and MACs at the other factors (FIR / IIR): 2× 63 / 3 samples, 65 / 6 MACs; 4× 69 / 4, 91 / 12;
16× 73 / 5, 247 / 36 (each way). The IIR burst τ error (0.08 %) is far below the ~5 % in `ALIASING.md`: that
experiment did not compensate the IIR delay; here the burst time axis is shifted by the interpolator's delay.

## Against (i), Koren 12AX7 — informational (valve-model differences)
- Operating point: Va 0.8 %, Vk 1.7 %, Ia 1.7 %, Vgk 4.3 % different (DZ RSD-1 is a different, measured valve).
- Small signal: DZ stage is ~0.8 dB hotter mid-band (36.19 vs 35.39 dB at 1 kHz).
- Harmonics: H2 4.0 dB lower at 0.05 V and 3.7 dB lower at 0.5 V; H3 up to 6.2 dB higher at 2 V.
  THD 0.169/1.776/16.70/60.69 % vs Koren 0.269/2.725/13.73/57.74 %.
- Burst: shift 6.9 % larger, τ 4.2 % shorter (12.76 vs 13.32 ms), trajectory RMS 3.2 % of shift.
So most of any difference from the Koren reference is the valve model, which is why (ii) exists.

## Newton–Raphson iterations per sample (384 kHz, 8×)
Count includes the final converged check (tol **1e-6 V** on step and residual, was 1e-9 V). Each sample starts
from the linear extrapolation 2·v[n−1] − v[n−2] of the last two solutions (#15); pnjlim, the line search and
the 50-iteration cap are unchanged. No sample failed to converge. 4× means/max in brackets. Since #14 the sine runs
are 0.5 s + latency (no 20 ms guard) and start from a causal interpolator, hence the slightly different counts.

| run | mean | max | histogram | 4× | before #15 (tol 1e-9, v[n−1] start) |
|---|---|---|---|---|---|
| 0.05 V sine | 2.00 | 2 | 1: 580, 2: 191 996 | 2.00 / 2 | 2.97 / 3 (2: 6 761, 3: 192 919) |
| 0.5 V | 2.00 | 2 | 1: 47, 2: 192 529 | 2.00 / 3 | 3.00 / 3 (2: 520, 3: 199 160) |
| 2 V | 2.29 | 3 | 1: 35, 2: 137 074, 3: 55 467 | 2.93 / 3 | 3.81 / 4 (2: 17, 3: 38 369, 4: 161 294) |
| 8 V | 2.45 | 4 | 1: 32, 2: 109 466, 3: 79 102, 4: 3 976 | 2.56 / 5 | 3.06 / 6 (2: 81 925, 3: 37 053, 4: 68 246, 5: 12 453, 6: 3) |
| 4 V burst | 1.32 | 4 | 1: 35 349, 2: 7 779, 3: 3 527, 4: 1 | 1.59 / 4 | 2.41 / 5 (1: 3 593, 2: 24 430, 3: 13 636, 4: 4 242, 5: 179) |

Every accuracy metric above is unchanged by #15 to better than 1e-12 relative (operating point, response,
H2–H5, burst). The line search now evaluates the Jacobian once per accepted step rather than on every halving
(audit finding 8); that change alone is bit-identical.

## CPU (informational)
Pure Python 3.13.5 (NumPy/SciPy), one core of the lab box (Intel Xeon, shared VM), `time.perf_counter` around `Stage.process`
on 0.5 s sines at 48 kHz in/out, half-band FIR resampling included; 4× measured in the same `python -m dk.validate` run.

| drive | 8× | 4× | 8× before #14 (Kaiser 511) | 8× before #15 |
|---|---|---|---|---|
| 0.05 V | 6.53 s/s | 3.24 s/s | 6.42 | 7.69 |
| 0.5 V | 6.44 | 3.20 | 6.45 | 7.65 |
| 2 V | 6.45 | 3.60 | 6.74 | 8.34 |
| 8 V | 6.75 | 3.43 | 6.83 | 7.39 |

#14 changes nothing measurable here: the per-sample Python loop dominates and the run-to-run spread is a few %.
The resampler saving is in multiplies (511 → 143 per sample each way for FIR, 20 for IIR), which matters in C++.

#15 cut the Python time by 8–19 %. Iterations fell 20–45 %, but here the per-sample NumPy matrix work dominates;
the solver share is far larger in C/C++ (~85–90 % in the C port timing), where the saving should be re-measured.
So 8× costs ~2.0× the CPU of 4× (≈6.5 s of CPU per second of audio, ~8× slower than real time). This is
interpreter overhead and says nothing about the C++ core, where the cost of 8× should be re-measured.
