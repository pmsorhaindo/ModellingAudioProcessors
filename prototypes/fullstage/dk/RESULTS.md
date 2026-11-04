# DK stage — first-pass validation (4 Nov 2026)

`python -m dk.validate` → `results.json`; `python -m pytest -q dk/tests` → **11 passed, 1 failed**.

Setup: 48 kHz base rate, 4× oversampling (stage at 192 kHz), Drive = Bias = Output = 1.
References: **(ii)** ngspice with the same Dempwolf–Zölzer equations and parameters (`spice/models_dz/`) —
the pass criteria are tested against this, so it measures *method* error. **(i)** ngspice with Koren's
12AX7 — adds *valve-model* mismatch; informational only.
The criteria are our proposed first-pass targets (approved as defaults, to be tightened).

## Against (ii), same valve equations — the test

| criterion | target | result | |
|---|---|---|---|
| operating point (Va, Vk, Ia, Vgk) | within 1 % | all within 2×10⁻⁹ relative (Va 204.536 V, Vk 1.4321 V, Ia 0.9546 mA, Vgk −1.5178 V) | pass |
| small signal, 20 Hz–20 kHz | within 0.1 dB | **0.21 dB at 20 kHz** (−0.026 dB at 10 kHz, < 0.001 dB at 1 kHz and below); stage alone 0.212 dB, with the resampling filters 0.211 dB | **FAIL** |
| H2–H5 per drive level (where the reference is above −100 dBc) | within 1 dB | worst 0.58 dB (H5 at 0.5 V); every other tested harmonic ≤ 0.18 dB; THD 0.169/1.776/16.70/60.69 % vs 0.169/1.776/16.70/60.69 % | pass |
| 4 V burst: cycle-averaged Vgk shift, recovery τ, trajectory | 5 % / 10 % / 5 % RMS of shift | shift −2.841 V vs −2.842 V (0.03 %); τ 12.76 ms vs 12.75 ms (0.04 %); trajectory RMS 0.005 % of shift; minimum at 30.0 ms in both | pass |

Excluded harmonics (reference below −100 dBc, the simulator's numerical floor; see `spice/RESULTS.md`):
H4/H5 at 0.05 V. They are listed in `results.json` with `tested: false`.

### Diagnosis of the small-signal failure
It is the trapezoidal rule's frequency warping, not a derivation error. The stage's high-frequency
roll-off comes from the Miller capacitance (CGP × stage gain) against the 68k grid stopper, and the
bilinear map moves that pole. Evidence: the error at 20 kHz scales as 1/fs² with the internal rate —

| internal rate | 96 kHz (2×) | 192 kHz (4×) | 384 kHz (8×) | 768 kHz (16×) |
|---|---|---|---|---|
| max deviation 20 Hz–20 kHz | 0.956 dB | 0.212 dB | 0.052 dB | 0.013 dB |

and below 1 kHz the DK response matches ngspice's AC analysis to < 10⁻⁵ dB. Options for the next pass
(not done; tolerance left at 0.1 dB): run the stage at 8×; pre-warp the Miller pole / use a
higher-order discretisation for the small capacitances; or relax the criterion above ~10 kHz if Michael
prefers the CPU budget.

## Against (i), Koren 12AX7 — informational (valve-model differences)
- Operating point: Va 0.8 %, Vk 1.7 %, Ia 1.7 %, Vgk 4.3 % different (DZ RSD-1 is a different, measured valve).
- Small signal: DZ stage is ~0.8 dB hotter mid-band (36.19 vs 35.39 dB at 1 kHz).
- Harmonics: H2 4.0 dB lower at 0.05 V and 3.7 dB lower at 0.5 V; H3 up to 6.2 dB higher at 2 V.
  THD 0.169/1.776/16.70/60.69 % vs Koren 0.269/2.725/13.73/57.74 %.
- Burst: shift 6.9 % larger, τ 4.2 % shorter (12.76 vs 13.32 ms), trajectory RMS 3.2 % of shift.
So most of any difference from the Koren reference is the valve model, which is why (ii) exists.

## Newton–Raphson iterations per sample (192 kHz)
Count includes the final converged check (tol 1e-9 V on step and residual). No sample failed to converge.

| run | mean | max | histogram |
|---|---|---|---|
| 0.05 V sine | 2.99 | 3 | 2: 1001, 3: 94 999 |
| 0.5 V | 3.39 | 4 | 2: 499, 3: 57 509, 4: 37 992 |
| 2 V | 3.94 | 4 | 3: 5 997, 4: 90 003 |
| 8 V | 3.24 | 8 | 2: 37 879, 3: 15 547, 4: 29 943, 5: 8 134, 6: 3 275, 7: 1 218, 8: 4 |
| 4 V burst | 2.53 | 6 | 1: 1 797, 2: 10 277, 3: 8 548, 4: 1 939, 5: 401, 6: 78 |

## CPU (informational)
Pure Python, one core, `time.perf_counter` around `Stage.process` (48 kHz in/out, 4× oversampling, FIR
resampling included): 3.9–4.3 s of CPU per second of audio, i.e. ~4× slower than real time. This is
interpreter overhead and says nothing about the C++ core.
