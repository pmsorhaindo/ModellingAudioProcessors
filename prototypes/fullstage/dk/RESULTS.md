# DK stage — validation of the quality settings (4 Nov 2026)

`python -m dk.validate` → `results.json` (one entry per setting, plus the aliasing sweep); `python -m pytest -q dk/tests`
→ **118 passed, all criteria pass for every setting** (criteria tightened in #5, aliasing limits from #6, CI guard #35, below).

Setup: 48 kHz base rate, Drive = Bias = Output = 1, default setting **standard = 8× oversampling (stage at
384 kHz)**. History: the first pass ran at 4× and failed only the small-signal criterion (0.21 dB at 20 kHz, see the
diagnosis below). Michael chose to run at 8× rather than add a warping correction or relax the target;
the small-signal tolerance is unchanged. #17 adds the warping correction back as the eco setting, and a 16× setting.

## Quality settings (#17)
`Stage(fs, quality="hq" | "standard" | "eco", filt="fir" | "iir")`. The resampler (`filt`) is independent of the
setting; the settings are defined and validated with the FIR. Latency at the base rate, as `Stage.latency_samples`
reports it; MACs = resampler multiplies per base-rate sample (`Stage.resampler_macs`), each way (up / down).
Measured against (ii) (the #5 tight-tolerance reference) with the #5 criteria below; aliasing is the worst tone of the
offset-tone sweep against each setting's limit (#6, "Aliasing" below).

| | hq | standard (default) | eco |
|---|---|---|---|
| oversampling | 16× (768 kHz) | 8× (384 kHz) | 4× (192 kHz) + small-cap pre-warp at 17.3 kHz |
| FIR resampler: latency | 73 samples, 1.521 ms (exact) | 72 samples, 1.500 ms | 69 samples, 1.438 ms |
| FIR resampler: MACs up / down | 247 / 247 | 143 / 143 | 91 / 91 |
| IIR option: latency, MACs | 5 samples (0.104 ms, nominal), 36 / 36 | 5 samples, 20 / 20 | 4 samples (0.083 ms), 12 / 12 |
| small signal, worst 20 Hz–20 kHz (target 0.1 dB) | 0.0136 dB at 20 kHz | 0.0517 dB at 20 kHz | **0.0536 dB** at 11 kHz; **−0.0527 dB at 20 kHz** (plain 4×: 0.212 dB, fail) |
| H2–H5 worst tested dev (target 0.1 dB, ref > −90 dBc) | 0.00023 dB (H5, 2 V) | 0.00076 dB (H5, 2 V) | 0.0165 dB (H5, 2 V): 6× margin |
| burst shift / τ / trajectory (0.5 % / 1 % / 0.5 %) | 0.031 % / 0.041 % / 0.0035 % | 0.031 % / 0.041 % / 0.0035 % | 0.009 % / 0.041 % / 0.0078 % |
| aliasing, sweep worst, 8 V / 2 V (limit) | −66.03 (−63.0) / −121.22 dBc (−118.2) | −51.70 (−48.6) / −121.36 dBc (−118.3) | −32.65 (−29.6) / −75.74 dBc (−72.7) |
| Newton iterations, 8 V sine: mean / max | 2.18 / 3 | 2.45 / 4 | 2.55 / 5 |
| CPU, pure Python (informational) | 12.2–12.9 s/s | 6.4–6.6 s/s | 3.1–3.6 s/s |

Operating point identical in all three (2×10⁻⁹ relative). Standard is bit-identical to the pre-#17 stage. Eco
has the least margin on the #5 harmonic limit (0.0165 dB at H5, 2 V, vs 0.1 dB); its harmonics carry the 4× and
pre-warp error that standard and hq do not. With `filt="iir"` every setting also passes every criterion above
(small signal 0.0128 / 0.0515 / 0.0534 dB, H2–H5 worst 0.00013 / 0.00053 / 0.0167 dB, burst shift ≤ 0.031 %,
τ ≤ 0.082 %, trajectory ≤ 0.011 %). Its aliasing at 8 V matches the FIR's within 0.02 dB from 5 kHz up, including
the worst tone. Its resampler floor is higher (it shows at 2 V and at low tones), and since #6 it has its own limits.
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

## Aliasing (#6): offset-tone sweep against a 64× ground truth
`dk/aliasing.py`. `python -m dk.validate` runs the full sweep for every setting and both resamplers, writes it to
`results.json` (`"aliasing"`) and exits with status 1 if any setting exceeds its limit; `dk/tests/test_aliasing.py`
enforces the same limits on a lean subset. `python -m dk.aliasing` regenerates the 64× ground truth
(`dk/aliasing_64x.json`, 11 min on 8 cores). This replaces the single-point guard (10 kHz at 8 V, limits equal to the
measured values: 0.02 dB headroom at standard).

**Method.**
- Tones 1037, 2037, … 19037 Hz (37 Hz above each kHz) at 2 V and 8 V peak, 48 kHz base rate, Drive = Bias = Output = 1.
  Every point is a fresh `Stage` started at its operating point: 0.25 s settling, then a 1 s analysis window (1 Hz
  bins, the tone exactly on a bin). The input runs `latency_samples` past the window and that many output samples are
  dropped (#14), so the FIR tail never enters the window.
- Metric: total non-harmonic energy 20 Hz–20 kHz relative to the fundamental (dBc). Periodic Hann window; the
  fundamental and every harmonic below 24 kHz ±3 bins are masked; everything else in the band is summed.
- Every alias on its own bin: harmonic k folds onto ±k·f mod 48 kHz, and lands in another harmonic's mask only if
  (k ∓ j)·f is within 3 Hz of a multiple of 48 kHz. The smallest such k ∓ j ("collision order") is ≥ 919 for every
  sweep tone except 13037 Hz (81). For 10 kHz it is 24 (H22 folds onto H2), which is why tones that divide 48 kHz hide
  aliasing under the harmonic masks. In practice that cost little: the exact 10 kHz point reads −74.87 / −59.72 /
  −44.12 dBc (hq / standard / eco, 8 V) against −74.88 / −59.84 / −44.02 at 10037 Hz. What the old guard missed is that
  the worst tone is 19037 Hz, 8.8 / 8.0 / 11.5 dB worse.
- Where each alias comes from: each line is credited to the lowest harmonic order that folds onto its bin. A harmonic
  above the stage's internal Nyquist (os·24 kHz) folded **inside the stage** ("internal": what oversampling buys).
  Otherwise it reached 0–20 kHz **through the resampler** ("resampler": decimator stopband leakage, or intermodulation
  with the interpolator's residual images, which lands on the same bins). That share is about the same at every
  oversampling factor.
- Ground truth: the same sweep at 64× (no pre-warp), with each resampler.
- Lean subset (pytest): each setting's worst tone per drive, plus 10037 Hz at 8 V, with 0.2 s settling and a 0.1 s
  window. Tones move to the 10 Hz grid, e.g. 19037 → 19040 Hz (collision order ≥ 64 kept). At those tones it reads
  within 0.05 dB of the full sweep. Shorter settling leaves a start-up transient at 2 V. Even with 0.2 s, the short
  buffers read up to 1.4 dB high at other 2 V tones, and far more where the full sweep is near the settling floor
  (47 dB at 1037 Hz, which reads −174 dBc). That is why the subset uses each setting's worst tones.

**Measurement floor.** Measured, in parts:

| part | how | level |
|---|---|---|
| analysis (window, FFT) | an exactly harmonic synthetic signal through the metric (`test_aliasing.py`) | −253 dBc (1 s window) |
| — with the symmetric `np.hanning(n)` used before #6 | same | −113 dBc at 1 s, +6 dB per halving of the window |
| settling and anything unattributed | "other" share, max over the sweep, all settings | −174 dBc at 2 V, −164 dBc at 8 V (1037 Hz) |
| resampler, FIR | "resampler" share at 64×, max over the sweep | −121.2 dBc at 2 V (19037 Hz: H2 at 38 kHz through the ≥ 92.8 dB stopband), −105.6 dBc at 8 V |
| resampler, IIR | same | −115.0 dBc at 2 V, −100.3 dBc at 8 V |
| folding left at 64× | "internal" share at 64× | −175 dBc at 2 V, −102.8 dBc at 8 V (19037 Hz) |

The "−110 to −113 dBc measurement floor" quoted before was the symmetric window, not the simulation. A coherent
tone's skirt leaked past the ±3 bin mask. With the periodic window the floor is set by the resampler, and the 64×
worst case (−101.0 dBc FIR, −99.5 dBc IIR, at 8 V) is resampler leakage and residual folding of similar size. So 64×
is a ground truth for the stage's own folding down to about −100 dBc at 8 V and −115 to −121 dBc at 2 V.

**Full sweep**, total non-harmonic dBc, every setting × filter vs 64× (`results.json`, `aliasing_64x.json`):

**2 V** (bold: each column's worst tone)

| tone (Hz) | hq FIR | hq IIR | standard FIR | standard IIR | eco FIR | eco IIR | 64× FIR | 64× IIR |
|---|---|---|---|---|---|---|---|---|
| 1037 | −173.9 | −173.7 | −173.9 | −173.8 | −173.9 | −173.8 | −173.9 | −173.7 |
| 2037 | −167.7 | −155.5 | −167.8 | −156.2 | −168.0 | −155.6 | −167.7 | −154.8 |
| 3037 | −166.7 | −140.3 | −166.9 | −140.5 | −166.8 | −141.5 | −166.6 | −140.3 |
| 4037 | −155.2 | −136.8 | −155.3 | −136.2 | −140.5 | −136.7 | −155.2 | −137.7 |
| 5037 | −145.2 | −132.1 | −145.3 | −133.2 | −123.0 | −122.6 | −145.1 | −131.9 |
| 6037 | −147.0 | −144.2 | −147.0 | −146.7 | −104.7 | −104.7 | −146.9 | −143.3 |
| 7037 | −147.0 | −127.7 | −144.0 | −126.5 | −98.3 | −98.3 | −146.9 | −129.0 |
| 8037 | −140.1 | −122.9 | −134.6 | −122.7 | −90.0 | −90.0 | −140.1 | −122.4 |
| 9037 | −142.1 | −121.5 | −133.7 | −120.8 | −90.7 | −90.7 | −142.1 | −122.0 |
| 10037 | −134.4 | −117.1 | −130.8 | −116.1 | −94.0 | −94.0 | −134.3 | −117.8 |
| 11037 | −128.4 | −129.7 | −126.9 | −126.3 | −83.5 | −83.5 | −128.4 | −131.2 |
| 12037 | −136.7 | −119.0 | −132.6 | −120.5 | −81.7 | −81.7 | −136.6 | −118.5 |
| 13037 | −130.8 | −115.8 | −129.2 | **−114.0** | −77.0 | −77.0 | −130.7 | −116.4 |
| 14037 | −125.6 | **−115.2** | −125.2 | −115.6 | −78.3 | −78.3 | −125.6 | −115.1 |
| 15037 | −139.3 | −128.1 | −136.3 | −130.0 | −76.1 | −76.1 | −139.2 | −128.0 |
| 16037 | −124.9 | −115.3 | −125.0 | −116.7 | **−75.7** | **−75.7** | −124.8 | **−115.0** |
| 17037 | −128.9 | −118.6 | −129.3 | −116.9 | −77.7 | −77.7 | −128.8 | −119.3 |
| 18037 | −123.0 | −119.8 | −123.2 | −121.2 | −79.2 | −79.2 | −123.0 | −119.6 |
| 19037 | **−121.2** | −126.4 | **−121.4** | −122.9 | −85.3 | −85.3 | **−121.2** | −126.8 |

**8 V** (bold: each column's worst tone)

| tone (Hz) | hq FIR | hq IIR | standard FIR | standard IIR | eco FIR | eco IIR | 64× FIR | 64× IIR |
|---|---|---|---|---|---|---|---|---|
| 1037 | −152.0 | −131.5 | −152.2 | −131.5 | −121.1 | −120.8 | −152.0 | −131.6 |
| 2037 | −135.3 | −116.3 | −104.1 | −103.9 | −79.1 | −79.1 | −136.1 | −116.3 |
| 3037 | −110.5 | −108.8 | −85.2 | −85.2 | −66.7 | −66.7 | −138.4 | −113.8 |
| 4037 | −97.3 | −97.1 | −77.0 | −77.0 | −59.8 | −59.8 | −128.5 | −111.2 |
| 5037 | −89.9 | −89.9 | −71.9 | −71.9 | −56.8 | −56.8 | −128.0 | −112.3 |
| 6037 | −84.8 | −84.8 | −68.7 | −68.7 | −53.5 | −53.5 | −123.3 | −121.4 |
| 7037 | −82.2 | −82.1 | −65.2 | −65.2 | −51.7 | −51.7 | −123.3 | −107.3 |
| 8037 | −79.0 | −79.0 | −63.3 | −63.3 | −46.2 | −46.2 | −119.1 | −101.2 |
| 9037 | −76.3 | −76.2 | −62.3 | −62.3 | −47.6 | −47.6 | −120.3 | −101.8 |
| 10037 | −74.9 | −74.9 | −59.8 | −59.8 | −44.0 | −44.0 | −117.8 | −102.5 |
| 11037 | −73.0 | −73.0 | −58.1 | −58.1 | −44.0 | −44.0 | −112.4 | −113.3 |
| 12037 | −72.6 | −72.6 | −58.1 | −58.1 | −37.5 | −37.5 | −113.3 | −105.7 |
| 13037 | −71.0 | −71.0 | −55.3 | −55.3 | −41.6 | −41.6 | −110.1 | −100.9 |
| 14037 | −69.7 | −69.7 | −55.1 | −55.1 | −37.1 | −37.1 | −107.7 | −101.0 |
| 15037 | −68.2 | −68.2 | −54.9 | −54.9 | −36.9 | −36.9 | −106.9 | −105.9 |
| 16037 | −67.2 | −67.2 | −53.3 | −53.3 | −35.7 | −35.7 | −104.1 | −99.5 |
| 17037 | −66.2 | −66.2 | −52.0 | −52.0 | −40.4 | −40.4 | −103.9 | **−99.5** |
| 18037 | −66.7 | −66.8 | −52.2 | −52.2 | −34.8 | −34.8 | −102.8 | −102.3 |
| 19037 | **−66.0** | **−66.0** | **−51.7** | **−51.7** | **−32.6** | **−32.6** | **−101.0** | −100.2 |

- 8 V: every setting is real stage aliasing, 34–68 dB above 64× at its worst tone. FIR and IIR agree within 0.02 dB
  from 5 kHz up; below that the IIR's resampler floor shows (1037 Hz: −131.5 vs −152.0 dBc at hq).
  Each doubling of the rate is worth 14.3 dB (standard → hq) and 19.1 dB (eco → standard) at 19037 Hz.
- 2 V: hq and standard sit on the resampler floor. Their worst tone reads within 0.2 dB of 64× (FIR) and within
  2.3 dB with the IIR, whose floor differs a little between factors. Some real folding shows at standard (FIR) from
  7 to 15 kHz: up to 8.4 dB above 64× (9037 Hz), internal share ≤ −131.8 dBc. Eco at 2 V is real aliasing:
  −75.7 dBc at 16037 Hz against −124.8 at 64×.
- These reproduce the 8 V worst cases quoted in #6 (4× −32.7, 8× −51.7, 16× −66.0 dBc): −32.65 / −51.70 / −66.03.

**Limits** (`aliasing.LIMIT_DBC`): the worst total over the full sweep for each setting, filter and drive, plus a
**3 dB margin**, rounded up to 0.1 dB. "CI subset" is the worst reading of the pytest subset with its short buffers.

| setting | filter | drive | worst tone | worst | internal / resampler | 64× at that tone | limit | margin | CI subset |
|---|---|---|---|---|---|---|---|---|---|
| hq | FIR | 2 V | 19037 | −121.22 | −220.6 / −121.2 | −121.18 | **−118.2** | 3.02 dB | −121.27 at 19040 Hz |
| hq | FIR | 8 V | 19037 | −66.03 | −66.0 / −105.7 | −100.96 | **−63.0** | 3.03 dB | −66.03 at 19040 Hz |
| hq | IIR | 2 V | 14037 | −115.20 | −203.8 / −115.2 | −115.06 | **−112.2** | 3.00 dB | −115.23 at 14040 Hz |
| hq | IIR | 8 V | 19037 | −66.03 | −66.0 / −102.4 | −100.16 | **−63.0** | 3.03 dB | −66.03 at 19040 Hz |
| standard | FIR | 2 V | 19037 | −121.36 | −151.0 / −121.4 | −121.18 | **−118.3** | 3.06 dB | −121.40 at 19040 Hz |
| standard | FIR | 8 V | 19037 | −51.70 | −51.7 / −105.7 | −100.96 | **−48.6** | 3.10 dB | −51.69 at 19040 Hz |
| standard | IIR | 2 V | 13037 | −114.04 | −133.8 / −114.1 | −116.36 | **−111.0** | 3.04 dB | −114.00 at 13040 Hz |
| standard | IIR | 8 V | 19037 | −51.70 | −51.7 / −102.8 | −100.16 | **−48.6** | 3.10 dB | −51.69 at 19040 Hz |
| eco | FIR | 2 V | 16037 | −75.74 | −75.7 / −125.5 | −124.85 | **−72.7** | 3.04 dB | −75.71 at 16040 Hz |
| eco | FIR | 8 V | 19037 | −32.65 | −32.6 / −105.5 | −100.96 | **−29.6** | 3.05 dB | −32.60 at 19040 Hz |
| eco | IIR | 2 V | 16037 | −75.74 | −75.7 / −116.4 | −114.97 | **−72.7** | 3.04 dB | −75.70 at 16040 Hz |
| eco | IIR | 8 V | 19037 | −32.65 | −32.6 / −108.4 | −100.16 | **−29.6** | 3.05 dB | −32.60 at 19040 Hz |

Why 3 dB: the simulation is deterministic (re-runs are bit-identical), so the margin is not noise headroom. It covers
the CI subset's short buffers (≤ 0.05 dB here), floating-point differences between platforms and library versions,
and changes too small to matter. Doubling the rate is worth 14–19 dB at 19 kHz / 8 V, so any real loss of oversampling
benefit fails. The limits are sweep-wide, so tones below the worst one have more room (e.g. hq at 10037 Hz / 8 V:
−74.9 dBc under a −63.0 limit). The 2 V limits for hq and standard sit 3 dB above the resampler floor, so they guard
the half-band filters, not the stage. `validate.py` also flags a setting whose worst tone is no longer in its CI tones.

Cost: the full sweep is 228 points, 324 s on 8 cores (`python -m dk.validate` now takes 6.2 min in total, was ~50 s).
The pytest subset is 18 points, ~50 s on one core, against 44 s for the old guard: the fullstage suite goes from
109.4 s to 114.0 s (105 tests).

### CI guard and baseline ratchet (#35)
`dk/aliasing_guard.py`, run by the blocking `aliasing` CI job (separate from `test`). It runs the same lean subset as
`test_aliasing.py` (`CI_TONES`, `CI_SETTLE_S` / `CI_WINDOW_S`: 18 points, 12 setting / filter / drive combinations)
in parallel processes, and fails if any combination
- reads above its absolute limit (`LIMIT_DBC`, above), or
- reads more than **1.5 dB** worse than the committed baseline `dk/aliasing_baseline.json`
  (`aliasing_guard.TOLERANCE_DB`), even under the limit.

Reading more than 1.5 dB better passes, with a note suggesting a baseline update. A combination with no baseline
entry, or whose CI tones have changed since the baseline, also fails. The job writes the table below to the step
summary (measured, baseline, delta = measured − baseline, limit, margin = limit − measured, status) and uploads the
full results (every record, the baseline used, the rows) as the `aliasing-guard-results` artifact.

Why 1.5 dB: the run is deterministic and the baseline is that same subset, so on a given platform delta is
exactly 0.00 dB. The tolerance covers platform and library floating-point differences and changes too small to
matter. It sits halfway into the ~3 dB margin of every limit, so the ratchet trips before the limit does. Losing an
octave of oversampling costs 14–19 dB at 8 V. A slightly weaker half-band shows directly in the 2 V resampler floor
(the demo below: +2.2 dB for Kaiser β 9.0 → 8.8 in the first FIR stage).

Baseline (`--update-baseline --reason "Initial baseline (#35): CI subset at 330ee9a"`, delta 0.00 dB everywhere):

| setting / filter / drive | worst CI tone | baseline dBc | limit dBc | margin dB |
|---|---|---|---|---|
| hq / FIR / 2 V | 19040 Hz | −121.27 | −118.2 | 3.07 |
| hq / FIR / 8 V | 19040 Hz | −66.03 | −63.0 | 3.03 |
| hq / IIR / 2 V | 14040 Hz | −115.23 | −112.2 | 3.03 |
| hq / IIR / 8 V | 19040 Hz | −66.03 | −63.0 | 3.03 |
| standard / FIR / 2 V | 19040 Hz | −121.40 | −118.3 | 3.10 |
| standard / FIR / 8 V | 19040 Hz | −51.69 | −48.6 | 3.09 |
| standard / IIR / 2 V | 13040 Hz | −114.00 | −111.0 | 3.00 |
| standard / IIR / 8 V | 19040 Hz | −51.69 | −48.6 | 3.09 |
| eco / FIR / 2 V | 16040 Hz | −75.71 | −72.7 | 3.01 |
| eco / FIR / 8 V | 19040 Hz | −32.60 | −29.6 | 3.00 |
| eco / IIR / 2 V | 16040 Hz | −75.70 | −72.7 | 3.00 |
| eco / IIR / 8 V | 19040 Hz | −32.60 | −29.6 | 3.00 |

**Proof by deliberate degradation** (local only, reverted, never committed; `git diff` of `dk/stage.py` and
`dk/resample.py` empty afterwards):

| change | result | exit |
|---|---|---|
| none | all 12 ok, delta 0.00 dB | 0 |
| standard forced to 2× (`QUALITY["standard"] = (2, None)`) | standard FIR / IIR at 2 V and 8 V **FAIL limit**: 8 V −15.70 dBc (+35.98 dB, limit −48.6); 2 V −49.28 (FIR) / −44.83 (IIR) dBc. Other settings ok | 1 |
| first FIR half-band Kaiser β 9.0 → 8.8 | hq and standard FIR 2 V **FAIL regression**: −119.07 / −119.21 dBc, +2.20 / +2.19 dB, still 0.87 / 0.91 dB **under** the limit. Only the ratchet catches it | 1 |
| (β 8.6, for scale) | same two over the limit: −117.02 / −117.15 dBc, +4.25 dB | 1 |
| baseline (temporary copy) 3 dB worse at standard / FIR / 8 V | that row "improved" (−3.00 dB) with the update note; passes | 0 |

Lowering the IIR's design attenuation (96 → 87–90 dB per stage) changes nothing: the coefficient count is quantised (6
coefficients either way), so the output is bit-identical and the guard correctly reads 0.00 dB.

**Updating the baseline** (an intended trade-off, or locking in an improvement):
`python -m dk.aliasing_guard --update-baseline --reason "..."` reruns the subset and rewrites the baseline with the
reason, date and git revision. Put the same reason in the commit message. It refuses while any combination is over
its absolute limit; that needs a deliberate `LIMIT_DBC` change, documented here.

Cost: 7.8 s wall on 8 cores (54 s CPU). CI runs it with `--workers $(nproc)` (4 on `ubuntu-latest`, so expect
~15–20 s plus install). The unit tests of the comparison logic (`dk/tests/test_aliasing_guard.py`, synthetic
numbers, no DSP) take 0.6 s.

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
IIR is `run(filt="iir")` in the same harness. Aliasing: 10 kHz at 8 V with the metric of that time (symmetric Hann,
the guard that #6 replaced); 16× and 4× measured the same way for reference.

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
