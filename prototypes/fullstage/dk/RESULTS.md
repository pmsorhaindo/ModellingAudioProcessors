# DK stage — validation of the quality settings (4 Nov 2026)

`python -m dk.validate` → `results.json` (one entry per setting); `python -m pytest -q dk/tests` → **91 passed,
all criteria pass for every setting** (criteria tightened in #5, below).

Setup: 48 kHz base rate, Drive = Bias = Output = 1, default setting **standard = 8× oversampling (stage at
384 kHz)**. History: the first pass ran at 4× and failed only the small-signal criterion (0.21 dB at 20 kHz, see the
diagnosis below). Michael chose to run at 8× rather than add a warping correction or relax the target;
the small-signal tolerance is unchanged. #17 adds the warping correction back as the eco setting, and a 16× setting.

## Quality settings (#17)
`Stage(fs, quality="hq" | "standard" | "eco", filt="fir" | "iir")`. The resampler (`filt`) is independent of the
setting; the settings are defined and validated with the FIR. Latency at the base rate, as `Stage.latency_samples`
reports it; MACs = resampler multiplies per base-rate sample (`Stage.resampler_macs`), each way (up / down).
Measured against (ii) (the #5 tight-tolerance reference) with the #5 criteria below; aliasing is 10 kHz at 8 V, the `ALIASING.md` metric
(`dk/tests/test_aliasing.py`), against each setting's limit from #9 / #17.

| | hq | standard (default) | eco |
|---|---|---|---|
| oversampling | 16× (768 kHz) | 8× (384 kHz) | 4× (192 kHz) + small-cap pre-warp at 17.3 kHz |
| FIR resampler: latency | 73 samples, 1.521 ms (exact) | 72 samples, 1.500 ms | 69 samples, 1.438 ms |
| FIR resampler: MACs up / down | 247 / 247 | 143 / 143 | 91 / 91 |
| IIR option: latency, MACs | 5 samples (0.104 ms, nominal), 36 / 36 | 5 samples, 20 / 20 | 4 samples (0.083 ms), 12 / 12 |
| small signal, worst 20 Hz–20 kHz (target 0.1 dB) | 0.0136 dB at 20 kHz | 0.0517 dB at 20 kHz | **0.0536 dB** at 11 kHz; **−0.0527 dB at 20 kHz** (plain 4×: 0.212 dB, fail) |
| H2–H5 worst tested dev (target 0.1 dB, ref > −90 dBc) | 0.00023 dB (H5, 2 V) | 0.00076 dB (H5, 2 V) | 0.0165 dB (H5, 2 V): 6× margin |
| burst shift / τ / trajectory (0.5 % / 1 % / 0.5 %) | 0.031 % / 0.041 % / 0.0035 % | 0.031 % / 0.041 % / 0.0035 % | 0.009 % / 0.041 % / 0.0078 % |
| aliasing 10 kHz / 8 V (limit) | −74.87 dBc (−74.8) | −59.72 dBc (−59.7) | −44.12 dBc (−44.1) |
| Newton iterations, 8 V sine: mean / max | 2.18 / 3 | 2.45 / 4 | 2.55 / 5 |
| CPU, pure Python (informational) | 12.2–12.9 s/s | 6.4–6.6 s/s | 3.1–3.6 s/s |

Operating point identical in all three (2×10⁻⁹ relative). Standard is bit-identical to the pre-#17 stage. Eco
has the least margin on the #5 harmonic limit (0.0165 dB at H5, 2 V, vs 0.1 dB); its harmonics carry the 4× and
pre-warp error that standard and hq do not. With `filt="iir"` every setting also passes every criterion above
(small signal 0.0128 / 0.0515 / 0.0534 dB, H2–H5 worst 0.00013 / 0.00053 / 0.0167 dB, burst shift ≤ 0.031 %,
τ ≤ 0.082 %, trajectory ≤ 0.011 %); its aliasing is −74.75 / −59.73 / −44.10 dBc, so only at standard does
it meet the FIR-derived limit (tested there, as in #14); at hq and eco it is 0.05 / 0.02 dB above.
In the pure-Python prototype CPU scales with the internal rate (the per-sample loop dominates: hq 1.9×, eco 0.53× of
standard), so it does not reproduce the ~0.99× / 0.55× / 0.31× of today's 8× quoted in #17; re-measure in C++.

### Eco: the pre-warp, and how 17.3 kHz was chosen
The trapezoidal companion conductance 2C/T is the bilinear map, which compresses frequency; pre-warping uses
Gc = C·ω₀ / tan(ω₀T/2) instead, so that capacitor's admittance is exact at ω₀ (`circuit.companion_g`; the companion
model and history update are unchanged, so this only changes the matrices: no runtime cost). It is applied to the
capacitors below 1 nF only — the pF valve capacitances, which with the Miller effect set the HF roll-off. The coupling and
bypass caps (poles below ~10 Hz) stay plain trapezoidal: pre-warped to ~17 kHz they would act ~2.7 % small at low
frequency, and pre-warping every cap at 17.5 kHz measures 0.086 dB worst (at 21 Hz) instead of 0.056 dB.

The pre-warp frequency is the one that minimises the worst |deviation| from (ii) over 20 Hz–20 kHz at 4×
(`validate.prewarp_minimax`, bounded scalar minimisation; `test_prewarp_frequency_is_the_minimax_optimum` re-derives it):

| f₀ | none | 15 kHz | 17.0 kHz | **17.267 kHz (optimum)** | 17.3 kHz (used) | 17.5 kHz | 18 kHz | 20 kHz |
|---|---|---|---|---|---|---|---|---|
| worst dev | 0.212 dB | 0.093 | 0.058 | **0.0533** | 0.0536 | 0.0557 | 0.061 | 0.085 |
| at | 20 kHz | 20 kHz | 20 kHz | both | 11 kHz | 11 kHz | 11 kHz | 12 kHz |

Below the optimum the worst case is still the warped roll-off at 20 kHz. Above it the error is set by the overshoot
around 11 kHz, where pre-warping makes the response slightly too high. The 17.5 kHz in #17 is the same optimum on a
0.5 kHz grid. The price is below 1 kHz: the pre-warped small caps act ~2.7 % small there, and CGP's Miller feedback
still matters at 100 Hz, so eco is +0.0012 dB at 20 Hz and +0.0028 dB at 1 kHz (8×: < 10⁻⁴ dB). Its H2–H5
deviations are within 0.02 dB of standard's. The optimum depends on the circuit values and the base rate, so
re-derive it (one call) when either changes.

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
The criteria started as first-pass targets and were tightened 10–100× in #5, after reference (ii) was regenerated
at tight ngspice tolerances (reltol 1e-6, 0.5 µs step; `spice/RESULTS.md`, v3).

| criterion | first pass | since #5 | measured worst (standard; other settings above) | margin |
|---|---|---|---|---|
| operating point, each of Va/Vk/Ia/Vgk | 1 % | 0.1 % | 2.1×10⁻⁹ (Vk) | 5×10⁵ |
| H2–H5 at 0.05/0.5/2/8 V, where ref > floor | 1 dB, floor −100 dBc | 0.1 dB, floor −90 dBc | 0.00076 dB (H5, 2 V) | 130× |
| burst Vgk shift | 5 % | 0.5 % | 0.031 % | 16× |
| burst recovery τ | 10 % | 1 % | 0.041 % | 24× |
| burst trajectory RMS / shift | 5 % | 0.5 % | 0.0034 % | 145× |
| small signal 20 Hz–20 kHz (unchanged) | 0.1 dB | 0.1 dB | 0.052 dB | 1.9× |

With `filt="iir"` the worst values are 2.1×10⁻⁹ / 0.00053 dB / 0.030 % / 0.082 % / 0.0047 %, also passing.

## Against (ii), same valve equations — the test

| criterion | target | result | |
|---|---|---|---|
| operating point (Va, Vk, Ia, Vgk) | within 0.1 % | all within 2×10⁻⁹ relative (Va 204.536 V, Vk 1.4321 V, Ia 0.9546 mA, Vgk −1.5178 V) | pass |
| small signal, 20 Hz–20 kHz | within 0.1 dB | **0.052 dB** worst, at 19.95 kHz (with resampling filters, 0.050 dB with the old Kaiser; stage alone 0.052 dB). −0.0071 dB at 10 kHz, < 0.001 dB at 1 kHz and below. At 4× it was 0.211 dB (fail) | pass |
| H2–H5 per drive level (where the reference is above −90 dBc) | within 0.1 dB | worst 0.00076 dB (H5 at 2 V: −43.0353 vs ref −43.0346 dBc); every tested harmonic ≤ 0.00076 dB; H1 within 2.1×10⁻⁵ dB; THD 0.169/1.776/16.700/60.691 % vs ref 0.169/1.776/16.700/60.693 % | pass |
| 4 V burst: cycle-averaged Vgk shift, recovery τ, trajectory | 0.5 % / 1 % / 0.5 % RMS of shift | shift −2.841 V vs −2.842 V (0.03 %); τ 12.755 ms vs 12.750 ms (0.04 %, i.e. 2 samples at 384 kHz); trajectory RMS 0.003 % of shift; minimum at 30.0 ms in both | pass |

**Measurement fix (issue #2).** Earlier figures (H5 at 0.5 V −0.645 dB, H3 at 0.05 V 0.22 dB) were an artefact: `Stage.process` resamples offline, so its last ~1.3 ms is FIR ringing against zero padding, and `harmonics()` analyses the last cycles of the buffer. `validate.py` now simulates 20 ms extra and ends the analysis window before that tail. The remaining -0.138 dB on H5 at 0.5 V was reference precision: an ngspice run with reltol 1e-6 and a 0.5 µs step gave −97.866 dBc (audit, 4 Nov 2026), within 0.004 dB of DK. Since #5 the committed reference is that tight run (−97.875 dBc, DK within 0.005 dB); H5 at 0.5 V is now below the −90 dBc floor anyway. Since #14 the resampler streams: every run feeds `latency_samples` extra input and drops that many output samples, so the analysed window never depends on input past the end of the signal, and the 20 ms guard is gone. Table rows below are generated from `results.json`.

The table below and the next sections are the standard setting (8×) unless stated.

Excluded harmonics (reference below −90 dBc; ngspice is converged to < 1e-3 dB above that and only to ~0.01 dB
near −97 dBc, see `spice/RESULTS.md` v3): H3/H4/H5 at 0.05 V (−96.3/−137.2/−178.9 dBc; DK within 0.0003/0.006/0.52 dB)
and H5 at 0.5 V (−97.9 dBc, within 0.005 dB). They are listed in `results.json` with `tested: false`.

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
The warping correction is now the eco setting (#17, above): 4× with the small caps pre-warped, 0.054 dB.

## Resampler change (#14): before / after, against (ii)
`python -m dk.validate` before (main at 7c182b4, 511-tap Kaiser, whole-buffer, delay trimmed) and after, at 8×.
IIR is `run(filt="iir")` in the same harness. Aliasing: 10 kHz at 8 V, the `ALIASING.md` metric
(`dk/tests/test_aliasing.py`); 16× and 4× measured the same way for reference.

| metric | Kaiser 511 (before) | half-band FIR (default) | half-band IIR |
|---|---|---|---|
| small signal, worst 20 Hz–20 kHz (chain) | 0.0503 dB | 0.0517 dB | 0.0515 dB |
| H2–H5 worst tested dev (old reference and floor) | 0.1375 dB (H5, 0.5 V) | 0.1370 dB | 0.1368 dB |
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
  THD 0.169/1.776/16.70/60.69 % vs Koren 0.269/2.725/13.73/57.75 %.
- Burst: shift 6.9 % larger, τ 4.2 % shorter (12.76 vs 13.32 ms), trajectory RMS 3.2 % of shift.
So most of any difference from the Koren reference is the valve model, which is why (ii) exists.

## Newton–Raphson iterations per sample (384 kHz, 8×)
Count includes the final converged check (tol **1e-6 V** on step and residual, was 1e-9 V). Each sample starts
from the linear extrapolation 2·v[n−1] − v[n−2] of the last two solutions (#15); pnjlim, the line search and
the 50-iteration cap are unchanged. No sample failed to converge, in any setting. Eco and hq mean / max in their own columns. Since #14 the sine runs
are 0.5 s + latency (no 20 ms guard) and start from a causal interpolator, hence the slightly different counts.

| run | mean | max | histogram | eco (4×) | hq (16×) | before #15 (tol 1e-9, v[n−1] start) |
|---|---|---|---|---|---|---|
| 0.05 V sine | 2.00 | 2 | 1: 580, 2: 191 996 | 2.00 / 2 | 2.00 / 2 | 2.97 / 3 (2: 6 761, 3: 192 919) |
| 0.5 V | 2.00 | 2 | 1: 47, 2: 192 529 | 2.00 / 3 | 2.00 / 2 | 3.00 / 3 (2: 520, 3: 199 160) |
| 2 V | 2.29 | 3 | 1: 35, 2: 137 074, 3: 55 467 | 2.95 / 3 | 2.02 / 3 | 3.81 / 4 (2: 17, 3: 38 369, 4: 161 294) |
| 8 V | 2.45 | 4 | 1: 32, 2: 109 466, 3: 79 102, 4: 3 976 | 2.55 / 5 | 2.18 / 3 | 3.06 / 6 (2: 81 925, 3: 37 053, 4: 68 246, 5: 12 453, 6: 3) |
| 4 V burst | 1.32 | 4 | 1: 35 349, 2: 7 779, 3: 3 527, 4: 1 | 1.58 / 4 | 1.20 / 3 | 2.41 / 5 (1: 3 593, 2: 24 430, 3: 13 636, 4: 4 242, 5: 179) |

Every accuracy metric above is unchanged by #15 to better than 1e-12 relative (operating point, response,
H2–H5, burst). The line search now evaluates the Jacobian once per accepted step rather than on every halving
(audit finding 8); that change alone is bit-identical.

## CPU (informational)
Pure Python 3.13.5 (NumPy/SciPy), one core of the lab box (Intel Xeon, shared VM), `time.perf_counter` around `Stage.process`
on 0.5 s sines at 48 kHz in/out, half-band FIR resampling included; all three settings measured in the same `python -m dk.validate` run.

| drive | standard (8×) | eco (4×) | hq (16×) | 8× before #14 (Kaiser 511) | 8× before #15 |
|---|---|---|---|---|---|
| 0.05 V | 6.41 s/s | 3.13 s/s | 12.87 s/s | 6.42 | 7.69 |
| 0.5 V | 6.39 | 3.20 | 12.66 | 6.45 | 7.65 |
| 2 V | 6.39 | 3.61 | 12.16 | 6.74 | 8.34 |
| 8 V | 6.58 | 3.36 | 12.45 | 6.83 | 7.39 |

#14 changes nothing measurable here: the per-sample Python loop dominates and the run-to-run spread is a few %.
The resampler saving is in multiplies (511 → 143 per sample each way for FIR, 20 for IIR), which matters in C++.

#15 cut the Python time by 8–19 %. Iterations fell 20–45 %, but here the per-sample NumPy matrix work dominates;
the solver share is far larger in C/C++ (~85–90 % in the C port timing), where the saving should be re-measured.
So in Python each doubling of the rate costs ~2× (eco ≈3.4, standard ≈6.5, hq ≈12.5 s of CPU per second of
audio). This is interpreter overhead and says nothing about the C++ core, where the cost of each setting should be
re-measured.
