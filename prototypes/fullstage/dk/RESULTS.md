# DK stage — validation at 8× oversampling (4 Nov 2026)

`python -m dk.validate` → `results.json`; `python -m pytest -q dk/tests` → **12 passed, all criteria pass**.

Setup: 48 kHz base rate, **8× oversampling (stage at 384 kHz)**, Drive = Bias = Output = 1.
History: the first pass ran at 4× and failed only the small-signal criterion (0.21 dB at 20 kHz, see the
diagnosis below). Michael chose to run at 8× rather than add a warping correction or relax the target;
tolerances are unchanged. 4× is still available with `Stage(fs, os=4)`.

Oversampling filter at 8×: `firwin`, 511 taps (64·os − 1, so the transition band has the same width in Hz as
the 255-tap 4× filter), Kaiser β 8, cutoff 22 kHz. Measured: passband flat to < 0.001 dB up to 20 kHz,
−6.0 dB at 22 kHz, −84 dB at 24 kHz, ≤ −88 dB everywhere from 26.5 kHz to 192 kHz (4×: ≤ −88.7 dB).
Used for interpolation (gain 8) and decimation; latency trimmed by the group delay.
References: **(ii)** ngspice with the same Dempwolf–Zölzer equations and parameters (`spice/models_dz/`) —
the pass criteria are tested against this, so it measures *method* error. **(i)** ngspice with Koren's
12AX7 — adds *valve-model* mismatch; informational only.
The criteria are our proposed first-pass targets (approved as defaults, to be tightened).

## Against (ii), same valve equations — the test

| criterion | target | result | |
|---|---|---|---|
| operating point (Va, Vk, Ia, Vgk) | within 1 % | all within 2×10⁻⁹ relative (Va 204.536 V, Vk 1.4321 V, Ia 0.9546 mA, Vgk −1.5178 V) | pass |
| small signal, 20 Hz–20 kHz | within 0.1 dB | **0.050 dB** worst, at 19.95 kHz (with resampling filters; stage alone 0.052 dB). −0.0068 dB at 10 kHz, < 0.001 dB at 1 kHz and below. At 4× it was 0.211 dB (fail) | pass |
| H2–H5 per drive level (where the reference is above −100 dBc) | within 1 dB | worst 0.138 dB (H5 at 0.5 V: -97.870 vs ref -97.733 dBc, i.e. reference precision at ~−98 dBc, see note below); H3 at 0.05 V -0.0033 dB; every other tested harmonic ≤ 0.0152 dB; THD 0.169/1.776/16.700/60.691 % vs ref 0.169/1.776/16.699/60.686 % | pass |
| 4 V burst: cycle-averaged Vgk shift, recovery τ, trajectory | 5 % / 10 % / 5 % RMS of shift | shift −2.841 V vs −2.842 V (0.03 %); τ 12.755 ms vs 12.75 ms (0.04 %); trajectory RMS 0.004 % of shift; minimum at 30.0 ms in both | pass |

**Measurement fix (issue #2).** Earlier figures (H5 at 0.5 V −0.645 dB, H3 at 0.05 V 0.22 dB) were an artefact: `Stage.process` resamples offline, so its last ~1.3 ms is FIR ringing against zero padding, and `harmonics()` analyses the last cycles of the buffer. `validate.py` now simulates 20 ms extra and ends the analysis window before that tail. The remaining -0.138 dB on H5 at 0.5 V is reference precision: an ngspice run with reltol 1e-6 and a 0.5 µs step gave −97.866 dBc (audit, 4 Nov 2026), within 0.004 dB of DK; the committed reference uses default tolerances. Streaming resampling (no edge at all) is #14. Table rows below are generated from `results.json`; sine-run iteration counts now include the 20 ms guard.

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

## Against (i), Koren 12AX7 — informational (valve-model differences)
- Operating point: Va 0.8 %, Vk 1.7 %, Ia 1.7 %, Vgk 4.3 % different (DZ RSD-1 is a different, measured valve).
- Small signal: DZ stage is ~0.8 dB hotter mid-band (36.19 vs 35.39 dB at 1 kHz).
- Harmonics: H2 4.0 dB lower at 0.05 V and 3.7 dB lower at 0.5 V; H3 up to 6.2 dB higher at 2 V.
  THD 0.169/1.776/16.70/60.69 % vs Koren 0.269/2.725/13.73/57.74 %.
- Burst: shift 6.9 % larger, τ 4.2 % shorter (12.76 vs 13.32 ms), trajectory RMS 3.2 % of shift.
So most of any difference from the Koren reference is the valve model, which is why (ii) exists.

## Newton–Raphson iterations per sample (384 kHz, 8×)
Count includes the final converged check (tol 1e-9 V on step and residual). No sample failed to converge.
Smaller steps give a better previous-sample guess, so per-sample iterations drop slightly vs 4×
(4× means/max in brackets), but there are twice as many samples.

| run | mean | max | histogram | 4× |
|---|---|---|---|---|
| 0.05 V sine | 2.97 | 3 | 2: 6 761, 3: 192 919 | 2.99 / 3 |
| 0.5 V | 3.00 | 3 | 2: 520, 3: 199 160 | 3.39 / 4 |
| 2 V | 3.81 | 4 | 2: 17, 3: 38 369, 4: 161 294 | 3.94 / 4 |
| 8 V | 3.06 | 6 | 2: 81 925, 3: 37 053, 4: 68 246, 5: 12 453, 6: 3 | 3.24 / 8 |
| 4 V burst | 2.41 | 5 | 1: 3 593, 2: 24 430, 3: 13 636, 4: 4 242, 5: 179 | 2.53 / 6 |

## CPU (informational)
Pure Python 3.13.5 (NumPy/SciPy), one core of the lab box (Intel Xeon, shared VM), `time.perf_counter` around `Stage.process`
on 0.5 s sines at 48 kHz in/out, FIR resampling included; 4× measured in the same `python -m dk.validate` run.

| drive | 8× | 4× |
|---|---|---|
| 0.05 V | 7.60 s/s | 3.78 s/s |
| 0.5 V | 7.70 | 4.50 |
| 2 V | 8.33 | 4.28 |
| 8 V | 7.35 | 3.88 |

So 8× costs ~2.0× the CPU of 4× (≈8 s of CPU per second of audio, ~8× slower than real time). This is
interpreter overhead and says nothing about the C++ core, where the cost of 8× should be re-measured.
