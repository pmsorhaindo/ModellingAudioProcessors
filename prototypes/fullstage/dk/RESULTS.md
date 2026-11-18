# DK stage — validation of the quality settings (4 Nov 2026)

Host-rate extension: [#74 results](#host-sample-rates-74-18-nov-2026), including the unchanged-limit failures at 44.1 kHz.

`python -m dk.validate` → `results.json` (one entry per setting, plus the aliasing sweep); `python -m pytest -q dk/tests`
→ **124 passed, all criteria pass for every setting** (criteria tightened in #5, aliasing limits from #6, CI guard #35, small-signal budget #38, below).

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
| small signal, worst 20 Hz–20 kHz (target 0.1 dB; #38 budget below) | 0.0133 dB at 20 kHz | 0.0520 dB at 20 kHz | **0.0538 dB** at 20 kHz; **+0.0535 dB at 11 kHz** (plain 4×: 0.213 dB, fail) |
| H2–H5 worst tested dev (target 0.1 dB, ref > −90 dBc) | 0.00023 dB (H5, 2 V) | 0.00076 dB (H5, 2 V) | 0.0165 dB (H5, 2 V): 6× margin |
| burst shift / τ / trajectory (0.5 % / 1 % / 0.5 %) | 0.031 % / 0.041 % / 0.0035 % | 0.031 % / 0.041 % / 0.0035 % | 0.009 % / 0.041 % / 0.0078 % |
| aliasing, sweep worst, 8 V / 2 V (limit) | −66.03 (−63.0) / −121.22 dBc (−118.2) | −51.70 (−48.6) / −121.36 dBc (−118.3) | −32.65 (−29.6) / −75.74 dBc (−72.7) |
| Newton iterations, 8 V sine: mean / max | 2.18 / 3 | 2.45 / 4 | 2.55 / 5 |
| CPU, pure Python (informational) | 12.2–12.9 s/s | 6.4–6.6 s/s | 3.1–3.6 s/s |

Operating point identical in all three (2×10⁻⁹ relative). Standard is bit-identical to the pre-#17 stage. Eco
has the least margin on the #5 harmonic limit (0.0165 dB at H5, 2 V, vs 0.1 dB); its harmonics carry the 4× and
pre-warp error that standard and hq do not. With `filt="iir"` every setting also passes every criterion above
(small signal 0.0129 / 0.0518 / 0.0537 dB, H2–H5 worst 0.00013 / 0.00053 / 0.0167 dB, burst shift ≤ 0.031 %,
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

| f₀ | none | 15 kHz | 17.0 kHz | 17.3 kHz (used) | **17.310 kHz (optimum)** | 17.5 kHz | 18 kHz | 20 kHz |
|---|---|---|---|---|---|---|---|---|
| worst dev | 0.213 dB | 0.094 | 0.059 | 0.0538 | **0.0536** | 0.0556 | 0.061 | 0.085 |
| at | 20 kHz | 20 kHz | 20 kHz | 20 kHz | both | 11 kHz | 11 kHz | 12 kHz |

Below the optimum the worst case is still the warped roll-off at 20 kHz. Above it the error is set by the overshoot
around 11 kHz, where pre-warping makes the response slightly too high. The 17.5 kHz in #17 is the same optimum on a
0.5 kHz grid. (Re-measured in #38, now that the reference includes the exact 20 kHz band edge and the resampler
is counted once: the optimum moved from 17.267 to 17.310 kHz, 10 Hz from the 17.3 kHz used.) The price is below
1 kHz: the pre-warped small caps act ~2.7 % small there, and CGP's Miller feedback still matters at 100 Hz, so eco is +0.0012 dB at 20 Hz and +0.0028 dB at 1 kHz (8×: < 10⁻⁴ dB). Its H2–H5
deviations are within 0.02 dB of standard's. The optimum depends on the circuit values and the base rate, so
re-derive it (one call) when either changes.

## Aliasing (#6): offset-tone sweep against a 64× ground truth
`dk/aliasing.py`. `python -m dk.validate` runs the full sweep for every setting and both resamplers, writes it to
`results.json` (`"aliasing"`) and exits with status 1 if any setting exceeds its limit; `dk/aliasing_guard.py` (the
blocking `aliasing` CI job, below) enforces the same limits on a lean subset. `python -m dk.aliasing` regenerates the 64× ground truth
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
The lean subset was a pytest test at first: 18 points, ~50 s on one core, against 44 s for the old guard (the
fullstage suite went from 109.4 s to 114.0 s, 105 tests). It now runs only in `dk.aliasing_guard` (#35, #37), so
pytest keeps just the metric, limit-coverage and ground-truth unit tests.

### CI guard and baseline ratchet (#35)
`dk/aliasing_guard.py`, run by the blocking `aliasing` CI job (separate from `test`). It runs the lean subset
(`CI_TONES`, `CI_SETTLE_S` / `CI_WINDOW_S`: 18 points, 12 setting / filter / drive combinations) in parallel
processes, and fails if any combination
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
| small signal 20 Hz–20 kHz (unchanged) | 0.1 dB | 0.1 dB | 0.0520 dB, trapezoidal warping (#38 budget below) | 1.9× |

With `filt="iir"` the worst values are 2.1×10⁻⁹ / 0.00053 dB / 0.030 % / 0.082 % / 0.0047 %, also passing.

## Against (ii), same valve equations — the test

| criterion | target | result | |
|---|---|---|---|
| operating point (Va, Vk, Ia, Vgk) | within 0.1 % | all within 2×10⁻⁹ relative (Va 204.536 V, Vk 1.4321 V, Ia 0.9546 mA, Vgk −1.5178 V) | pass |
| small signal, 20 Hz–20 kHz | within 0.1 dB | **0.0520 dB** worst, at 20 kHz (with resampling filters; stage alone 0.0518 dB; before #38, on a grid without the band edge, 0.0517 dB at 19.95 kHz). −0.0067 dB at 10 kHz, < 0.001 dB at 1 kHz and below. At 4× it is 0.213 dB (fail). Breakdown: "Small-signal error budget" below | pass |
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

## Small-signal error budget (#38): the 1.9× headroom is the trapezoidal warping
`python -m dk.budget [--e2e]` prints the tables below; `dk/tests/test_smallsignal_budget.py` pins them. The deviation
from (ii) that `validate.small_signal` measures splits exactly into four sources (signed dB; the rows add up to the
total to 1e-14 dB). FIR resampler, the default:

| source | hq @ 20 kHz | standard @ 20 kHz | eco @ 20 kHz | eco @ 10.96 kHz | worst hq / standard / eco |
|---|---|---|---|---|---|
| reference: ngspice `.ac` vs exact analog solve (7-decimal rounding) | +3.3e-8 | +3.3e-8 | +3.3e-8 | −2.1e-8 | 5.9e-8 (all) |
| operating point: DK vs ngspice | +2.4e-9 | +2.4e-9 | +2.4e-9 | +4.2e-9 | 6.5e-9 (all) |
| **warping**: discretised stage vs exact analog, same op point | **−0.01287** | **−0.05184** | **−0.05371** | **+0.05339** | 0.01287 / 0.05184 / 0.05371 |
| resampler passband (FIR ripple) | −4.4e-4 | −1.2e-4 | −9.3e-5 | +1.3e-4 | 1.1e-3 / 8.1e-4 / 6.4e-4 (17.4 kHz) |
| total = chain − reference | −0.01331 | −0.05196 | −0.05380 | +0.05352 | 0.01331 / 0.05196 / 0.05380 |

With `filt="iir"` the resampler's magnitude is flat (3e-9 dB) and the totals are the warping alone (0.01287 / 0.05184 /
0.05371 dB). So 99.8 % of standard's 0.0520 dB is the stage's own discretisation; nothing else is above 0.0011 dB.

**Why the warping is 0.052 dB at 8×.** Every capacitor is trapezoidal with the same step T, so the linearised DK stage
is exactly the bilinear transform of the analog circuit: H_d(f) = H_a(f_w) with f_w = (fs/π)·tan(πf/fs)
(`test_stage_is_the_bilinear_warped_analog_response`: equal to 4e-9 dB, 1e-8°). The stage rolls off inside the audio
band: the Miller capacitance (CGP × (1 + gain), gain 36.2 dB) against the 68k grid stopper puts its −3 dB point at
14.3 kHz, so at 20 kHz it is already 4.70 dB below 1 kHz and falling at 4.0 dB/octave. At 384 kHz the bilinear map
evaluates 20 kHz at analog 20.180 kHz (+0.90 %, ≈ (πf/fs)²/3), and the response there is 0.05184 dB lower: the
measured stage error to all printed digits. That shift scales as 1/fs², and so does the error:

| internal rate | 96 kHz (2×) | 192 kHz (4×) | 384 kHz (8×) | 768 kHz (16×) | 1.536 MHz (32×) | 3.072 MHz (64×) | 6.144 MHz (128×) |
|---|---|---|---|---|---|---|---|
| stage alone at 20 kHz (= worst) | −0.9626 dB | −0.2133 | −0.05184 | −0.01287 | −0.003212 | −0.000803 | −0.000201 |
| ratio to previous | | 4.51 | 4.12 | 4.03 | 4.01 | 4.002 | 4.001 |

The 1/fs² (Richardson) extrapolation from 64× and 128× leaves 6.5e-8 dB at fs → ∞, i.e. the reference's rounding.

**The other suspects in #38, measured.** DK core without resamplers: 0.05184 dB, the chain 0.05196. FIR vs IIR: above.
ngspice AC vs transient: the AC reference is ngspice's linear solve at the operating point and agrees with an
independent s-domain solve (`circuit.analog_response`) to 7e-8 dB over 1 Hz–100 kHz (`spice/RESULTS.md` v3.1; the
transient tolerances of #5 do not enter it). Measurement method, two real errors, both fixed in #38, and two that
only `test_e2e_response` sees:
- the reference's 50/decade grid had no point at either band edge, so the worst case was read at 19.95 kHz; the
  reference now includes 20 Hz and 20 kHz exactly, which moves the reported worst *up* by 0.0004 dB;
- `validate.small_signal` squared `chain_response`, which is already up → down, so the FIR passband was counted twice
  (an extra −1.2e-4 dB at 20 kHz at standard, −4.4e-4 dB at hq; 0 with the IIR);
- the 1 mV multitone through `process()` differs from the linearised chain by 7.5e-4 dB (second-order
  intermodulation landing on tone bins, proportional to level: 7.5e-5 dB at 0.1 mV). `test_e2e_gain_matches_linearised_chain`
  now checks process() against the same matrices to 0.002 dB;
- log-f interpolation of the reference at the multitone's frequencies: 7.9e-4 dB.

| worst small-signal deviation, 20 Hz–20 kHz (FIR) | hq | standard | eco |
|---|---|---|---|
| before #38 (grid without the band edges, resampler counted twice) | 0.01358 dB at 19.95 kHz | 0.05166 dB at 19.95 kHz (1.94×) | 0.05365 dB at 10.96 kHz |
| after (`results.json`) | 0.01331 dB at 20 kHz | 0.05196 dB at 20 kHz (1.92×) | 0.05380 dB at 20 kHz |

**Decision: documented as inherent, not changed.** The headroom is not a defect. It is the exact, predicted error of
the trapezoidal rule at 384 kHz for this circuit, deterministic (no noise, no drift), and only the internal rate or the
circuit can move it. To check after a change of `circuit.VALUES` (e.g. the real Culture Vulture values), rerun
`python -m dk.budget`; the warping row is ≈ (slope at 20 kHz in dB/octave) × log2(1 + (πf/fs)²/3).
The cheap fix exists and was measured: pre-warping the small capacitances at 8× as eco does at 4× (minimax optimum
17.31 kHz). It brings standard to 0.0130 dB (7.7× headroom), but it is not free: the worst harmonic error grows from
0.00076 to 0.00409 dB (H5, 2 V), 1 kHz moves by +0.0007 dB and 20 Hz by +0.0003 dB, and the default setting's
output changes. That reverses the 4 Nov decision above (8×, no warping correction) and would make the #43 demo
renders stale, so it is left to Michael. Its aliasing was not measured. Where more small-signal accuracy is wanted
today, hq gives 0.0133 dB (7.5×).

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

## Triode evaluation cost (#16)
`dk/triode.currents()` now shares one exp(−|z|) between each softplus and its sigmoid (`softplus_sigmoid`), and
forms the derivative powers as sk^(γ−1) = sk^γ / sk and sg^(ξ−1) = sg^ξ / sg. libm calls executed per evaluation:

| | exp | log1p | pow | total | the issue's count |
|---|---|---|---|---|---|
| `currents()` before | 4 | 2 | 4 | 10 | 8 (2 softplus + 2 sigmoid + 4 pow) |
| `currents()` after | 2 | 2 | 2 | 6 | 4 (2 softplus/sigmoid pairs + 2 pow) |
| `currents_only()` | 2 | 2 | 2 | 6 | unchanged: it had no sigmoid and no derivative powers |

The C port's disassembly has the same counts (each `log1p` is on one of two branches, so one runs per pair).

**Accuracy** (`dk/tests/test_triode.py`, against a verbatim copy of the pre-#16 formulas on 511 × 453 points,
Vgk −20…+5 V plus the cutoff tail down to −300 V, Vak 0…450 V plus 1000 V): softplus, sigmoid, `currents_only()`,
Ig and Ia are bit-identical. The Jacobian is within 4 ulps (worst 3 ulps on dIg/dVgk, 4 on dIa/dVgk and dIa/dVak;
the test allows 8). Entries below 1e-300 agree to 1e-300 absolute: where exp(−|z|) underflows, softplus is 0 and the
new code returns a zero derivative where the old one had 0^(γ−1) = 0. Everything downstream is unchanged:
`python -m pytest -q` → 238 passed, 1 skipped (the Koren model is not fetched in this worktree), including every
criterion against the ngspice references, and `python -m dk.aliasing_guard` passes with 0.00 dB deltas.

**Speed** (`python -m dk.bench_triode` → `bench_triode.json`): 930 operating points (Vgk −4…+1 V × 30, Vak
50–350 V × 31), before and after timed in interleaved blocks, median of 41 blocks. The C part is `dk/bench_triode.c`,
a port of both forms as noinline functions, built with gcc 14.2.0 at
`-O2 -fno-builtin-exp -fno-builtin-log1p -fno-builtin-pow` so that every call stays a real libm call. The figures
are from the committed JSON run. The ranges are over all runs of 6 Nov 2026 on the lab VM: 5 for Python, 6 for C
(including a first hand-built run). The VM was not idle (load average 2–3.5 from other work).

| eval | before | after | speedup (committed run) | speedup range (all runs) |
|---|---|---|---|---|
| C double, ns/call | 97.0 | 58.4 | 1.66× | 1.57–1.66× |
| C float, ns/call | 64.1 | 39.6 | 1.62× | 1.62× |
| Python `currents()`, ns/call | 625.8 | 578.5 | 1.08× | 1.04–1.10× |
| Python `currents_only()`, ns/call | — | — | 1.01× (unchanged code) | 0.98–1.01× |

The C figures show what #16 saves on the evaluation itself: 1.57–1.66× in double and 1.62× in float, at the low
end of the issue's 1.6–1.8× estimate.
In Python, interpreter overhead (dict lookups, tuple building, calls) dominates and the saving is about 8 %.
Python's timings are noise for the stage as a whole; re-measure the C++ core (#20).

## Fast exp / log / pow (#18)
`dk/fastmath.py` (Python reference) and `dk/fastmath.h` (C twin, same ops / tables, no FMA) replace libm
`exp` / `log1p` / `pow` in `currents_fast()` / `currents_only_fast()`. Algorithm: table reduction (N=128) +
minimax polynomials fitted by `dk/fastmath_fit.py`; `pow(s,g) = exp(g·log s)`. Stage switch:
`Stage(triode_math="libm"|"fast")` (default remains `"libm"` — in Python the fast path is slower than a C
libm call; it exists to run the C arithmetic against the criteria). Domain of every argument the stage hits
is recorded in `dk/fastmath_domain.json` (`python -m dk.fastmath_domain`).

**Error bounds** (`dk/tests/test_fastmath.py` asserts `fastmath.BOUND`; corrected in #60 after an independent
review of #59). Kernels: relative error against glibc libm. Currents: componentwise against the term magnitudes
(below). Measured worst on the test grids (6 Nov 2026, this box), then ~2.5–3.5× margin. These are measured
bounds, not proofs:

| fn | grid | measured worst | BOUND | before #60 |
|---|---|---|---|---|
| exp | `[EXP_LO, EXP_HI]`, dense at the `EXP_LO` edge | 3.87×10⁻¹³ | 1×10⁻¹² | 1×10⁻¹² |
| log1p | `(0, 1]` | 9.21×10⁻¹⁴ | 3×10⁻¹³ | 3×10⁻¹³ |
| log | positive normals: logspace 10⁻³⁰⁷…10³⁰⁸, every reduction bin, 1 ∓ 2⁻ᵏ, 1 ∓ 10⁻¹⁶…10⁻² | 9.21×10⁻¹⁴ | 3×10⁻¹³ | 5×10⁻¹² (false near 1⁻) |
| pow(s, 1.26) | stage domain (and a bit beyond), s → 1 | 3.97×10⁻¹³ | 1×10⁻¹² | 1×10⁻¹² |
| pow(s, 1.314) | stage domain (and a bit beyond) | 4.21×10⁻¹³ | 1×10⁻¹² | 1×10⁻¹² |
| currents_fast vs currents (Ig, Ia, J) | Vgk −16…+2 V × Vak 0…450 V (121 × 121) + 108 points on and next to Ia = 0 | 8.78×10⁻¹³ | 3×10⁻¹² | 1×10⁻⁹ (per element, grid-dependent) |

- *log near 1 (#60, M1).* Before #60 the reduction bin [1 − 2⁻⁸, 1) had centre 0.998046875. As x → 1⁻,
  log x = ln c + P(r) cancelled, and the relative error had no bound: 1.1×10⁻¹⁰ at 1 − 2⁻²⁰ and 0.95 at
  `nextafter(1, 0)`, against the published 5×10⁻¹². The old grid's 1.56×10⁻¹² was this cancellation at
  x = 0.99975. That bin now has centre 1, as the bin [1, 1 + 2⁻⁷) above it already did. Near 1, log x = P(x − 1)
  with x − 1 exact, so the bound is relative from both sides; it is exact (equal to libm) at 1 − 2⁻ᵏ for k ≥ 17.
- *Polynomial refit.* The log polynomial's r range widens from [−0.003861, 0.0078125] to [−2⁻⁸, 0.0078125]. It was
  refitted with `dk/fastmath_fit.py`: poly_err goes from 9.03×10⁻¹⁴ to 9.21×10⁻¹⁴. This is the only change to the
  arithmetic. The other bins next to 1 cancel by at most about 2×.
- *Stage impact.* pow only ever saw log's absolute error, so it and the stage were never affected.
- *Currents metric (#60, M2).* Ia = Ik − Ig and dIa/dVgk = dIk − dIg cross zero inside the stage's domain. A
  per-element |Δ|/|·| has no bound there: about 3×10³ on the Ia = 0 curve, and 1.5×10⁻⁸ on a dense grid next to it
  with the pre-#60 code. Each output is now divided by the sum of the magnitudes of the terms it is formed from
  (`currents_err`): Ia by Ik + Ig, dIa/dVgk by |dIk| + |dIg|. That denominator is ≥ Ig0 > 0.
- *Currents box.* It covers the recorded (Vgk, Vak), which `dk/fastmath_domain.py` now records as well: Vgk
  −13.87…+0.20 V and Vak 12.75…296.8 V over hq/standard/eco. The test also checks every grid against the recorded
  domain. 2×10⁵ random points in the box give 9.22×10⁻¹³.

**C.** With `-O2 -ffp-contract=off`, C matches Python bit for bit, in `test_c_matches_python_kernels` and
`test_c_matches_python_currents`. Those tests cover:
- every bound grid above, including the `EXP_LO` edge, where C adds to the exponent bits and Python calls `ldexp`;
- every log bin, and x → 1;
- `cur_fast` on the whole currents grid.

A separate 6.9×10⁵-point spot check also agreed bit for bit.

With FMA contraction (`-O2 -march=x86-64-v3`, the 1.39× build below), some bits differ:
- 848 of the 14749 currents points;
- 1 of the pow(s, 1.314) grid points;
- 12 / 10 of 10⁶ random log / log1p inputs.

`test_c_fma_kernels_within_bound` and `test_c_currents_within_bound` hold that build to the same BOUND against
libm. The measured worst is the same as in the table. `test_c_fma_build_is_contracted` checks that the build really
differs. The bench's `max_rel_diff_fast` is the old per-element metric; it is informational and not a bound.

**Criteria** (`python -m dk.validate --triode-math fast` → `results_fastmath.json`, rerun with the #60 tables):
- hq / standard / eco: all `pass_vs_dz` true (op, AC stage/chain, harmonics, burst), with CRIT unchanged.
- Worst AC vs DZ: hq 0.013 dB, standard 0.052 dB, eco 0.054 dB (CRIT 0.1 dB). These are libm's own deviations to
  about 10⁻¹² dB.
- Newton iteration statistics are identical to `results.json`.

`dk/tests/test_fastmath_stage.py` (#60) is the lean pytest of the fast path. It runs:
- the full `validate.run` on eco (~20 s; the cheapest setting, and the one with the largest AC deviation);
- the op point and AC against CRIT on every setting.

`python -m dk.aliasing_guard --triode-math fast` (CI subset, #35) passes: 12 of 12 rows ok, every delta vs the
libm baseline 0.00 dB.

**Speed** (`python -m dk.bench_triode` → `bench_triode.json`, same 930-point method as #16; fast timed against
the #16 libm `after` baseline):

| eval | after (#16 libm) | fast (#18) | speedup |
|---|---|---|---|
| C double `-O2`, ns/call (throughput) | 58.1 | 51.9 | 1.12× |
| C double `-O2`, ns/call (chain / Newton-like) | 75.0 | 72.6 | 1.03× |
| C double `-O2 -march=x86-64-v3`, throughput | 58.4 | 42.0 | 1.39× |
| C double `-O2 -march=x86-64-v3`, chain | 74.6 | 58.6 | 1.27× |
| Python `currents_fast` vs `currents` | — | — | 0.23× (slower; expected) |

This is one run on the lab VM; 1.12× is a gap of about 6 ns per call. `bench_triode.json` was measured with the
pre-#60 tables. #60 changed table entries and coefficients, not operations, so the timings still apply.
The issue hoped ~3–5× on the model eval; on this box the C double path is 1.12× (baseline ISA) to 1.39× (FMA).
Kernel throughput of `fm_exp` alone can look better, but the full `currents` path still spends time on the
non-transcendental arithmetic and call overhead. No float fast path (not needed for the double stage).

## C++ core (#19)
`cpp/dk_core.h` is the header-only realtime port of this stage; details, protocol and limitations are in
`cpp/README.md`. Measured on this lab VM (gcc 14.4, x86-64, 6–7 Nov 2026):
- `dk.validate.run` on the C++ core (`python -m cpp.validate_cpp` → `cpp/results.json`) reproduces this file in hq,
  standard and eco. Op point identical. AC within 8.8e-9 dB, H2–H5 within 5.5e-8 dB, burst Vgk within 5.1e-9 V,
  Newton histograms identical. Every `pass_vs_dz` criterion holds.
- Sample vectors (`cpp/vectors.txt`): with Python's matrices loaded, the C++ stage agrees to 2.7e-11 V (tolerance
  1e-9 V). With its own matrices it agrees to 7.7e-7 V (tolerance 1e-5 V). That gap is matrix rounding: Python's own
  matrices put its output 2.5e-7 V from the exact-matrix stage (`python -m cpp.matrix_rounding`). `solve()` and the
  triode currents are bit-identical to Python without FMA contraction.
- `process()` makes 0 allocations (counted by replaced `operator new` / `delete` and interposed glibc `malloc`).
- Speed: standard 8× costs 2.98–3.64 µs per base sample, one channel (5.7–7.0× realtime; Python 6.4 s/s). libm time /
  fast time is 0.96–1.06× there (0.94–1.21× over all settings), so the core's default stays libm, as in the Python
  stage.

## Host sample rates (#74, 18 Nov 2026)

Historical #95 measurements with the original 127-tap first FIR. The #96 report below supersedes the 44.1 kHz
DSP, acceptance budget and latency. The #95 JSONs are retained unchanged as the before measurements.

The shipped quality policy stays **fixed factor** at every tested host rate: HQ 16× without pre-warp, standard 8×
without pre-warp, eco 4× with the existing 17,300 Hz small-cap pre-warp. The 48 kHz settings, coefficients and
output are unchanged. The alternative internal-rate policy was measured separately below; it is not enabled by
`Stage` or `prepare()`.

**Method and scope.** `python -m dk.sample_rates --workers 8` and, serially, `python -m dk.sample_rates --backend
cpp --resume` write `dk/results_sample_rates.json` and `cpp/results_sample_rates.json`. Each calls the existing
`dk.validate.run(fs=...)`: operating point, 20 Hz–20 kHz analytical small signal (stage and full chain), 0.5 s 1
kHz sines at 0.05 / 0.5 / 2 / 8 V (last 100 cycles, H2–H5 above −90 dBc), and the 4 V, 10–30 ms burst followed
through 120 ms. All `validate.CRIT` thresholds are unchanged. The ngspice DZ reference (ii) remains the test
reference; Koren remains informational. These references describe the analog circuit, rather than a 48 kHz discrete
signal. A 100-cycle 1 kHz window has an integer sample count at every listed rate, and H2–H5 are below every base
Nyquist, so this harmonic comparison is fair at every rate. No new harmonic reference was made.

Every setting also starts with 100 ms of silence from prepare/reset, measuring the maximum over the entire startup
and the last 10 ms against the existing #70 **1e-9 V** rest criterion (stricter than the 1e-6 V startup criterion).
The full offset sweep is 1037, 2037, …, 19037 Hz × 2 / 8 V, 0.25 s settling and 1 s analysis, periodic Hann, ±3-bin
harmonic masks and total non-harmonic energy in 20 Hz–20 kHz, exactly as in `dk.aliasing`. Its
coherent-bin/collision-order safeguard is retained; records include actual `f_hz` and collision order. Latency
compensation is the existing extended-input/drop-output method. Each backend measures 114 sweep points per rate,
684 in total. The C++ time-domain data are actual `process()` output; its small-signal analysis uses C++ matrices
with the shared Python analytical code, as before.

This extension measures **FIR / libm / double precision**, the common Python/C++ path, Drive = Bias = Output = 1.
The full reference checks and offset sweeps are mono. C++ native prepare/process tests additionally cover
mono/stereo and libm/fast at all rates. Full fast-math and Python IIR reference/aliasing validation at the new
rates, demo renders, live rate switching, and other platforms were not measured. The existing 48 kHz IIR checks and
aliasing guard remain. No timed benchmarks were run; `validate.run` timing is now opt-in (`measure_cpu=True`, CLI
`--measure-cpu`).

**Aliasing policy.** Apply the existing `aliasing.LIMIT_DBC` unchanged at all rates: FIR HQ −118.2 / −63.0,
standard −118.3 / −48.6, eco −72.7 / −29.6 dBc at 2 / 8 V. These are acceptance checks against the existing 48 kHz
budget, not newly fitted rate-specific limits. No failed limit was raised and no new 3 dB margin was added. The
validators finish all rates, record the failed measurements and return nonzero when a criterion fails.
`dk/aliasing_64x.json` is still a 48 kHz reference; it is not a ground truth for another rate. No new 64× sweep was
run, so the very low high-rate readings should not be taken as a newly established accuracy floor or a comparison
against high-rate ground truth.

 Measured environment: `macOS-15.6.1-arm64-arm-64bit-Mach-O`, Python 3.15.2, NumPy 2.5.4, SciPy 1.18.1; C++ `Apple
clang version 17.0.0 (clang-1700.6.3.2)`, `-O2` with the existing `cpp.build` warning flags.

 **Fixed quality definitions.** All internal rates in kHz; pre-warp is none / none / 17.3 kHz for HQ / standard /
eco.

| host kHz | HQ factor / internal kHz | standard factor / internal kHz | eco factor / internal kHz |
|---|---|---|---|
| 44.1 | 16× / 705.6 | 8× / 352.8 | 4× / 176.4 |
| 48 | 16× / 768 | 8× / 384 | 4× / 192 |
| 88.2 | 16× / 1411.2 | 8× / 705.6 | 4× / 352.8 |
| 96 | 16× / 1536 | 8× / 768 | 4× / 384 |
| 176.4 | 16× / 2822.4 | 8× / 1411.2 | 4× / 705.6 |
| 192 | 16× / 3072 | 8× / 1536 | 4× / 768 |

 **Reference validation.** Each paired cell is **Python / C++**. AC and tested H2–H5 errors are maxima in dB;
limits are 0.1 dB. Burst is shift / recovery τ / trajectory error in %, limits 0.5 / 1 / 0.5 %.

| host kHz | setting | AC stage dB P / C | AC chain dB P / C | H2–H5 dB P / C | burst % Python | burst % C++ |
|---|---|---|---|---|---|---|
| 44.1 | hq | 0.01525 / 0.01525 | 0.01492 / 0.01492 | 0.000247 / 0.000247 | 0.0633 / 0.0400 / 0.0063 | 0.0633 / 0.0400 / 0.0063 |
| 44.1 | standard | 0.06152 / 0.06152 | 0.06085 / 0.06085 | 0.000877 / 0.000877 | 0.0630 / 0.0622 / 0.0079 | 0.0630 / 0.0622 / 0.0079 |
| 44.1 | eco | 0.06421 / 0.06421 | 0.06359 / 0.06359 | 0.019561 / 0.019561 | 0.0977 / 0.0400 / 0.0168 | 0.0977 / 0.0400 / 0.0168 |
| 48 | hq | 0.01287 / 0.01287 | 0.01331 / 0.01331 | 0.000228 / 0.000228 | 0.0308 / 0.0408 / 0.0035 | 0.0308 / 0.0408 / 0.0035 |
| 48 | standard | 0.05184 / 0.05184 | 0.05196 / 0.05196 | 0.000760 / 0.000760 | 0.0306 / 0.0408 / 0.0034 | 0.0306 / 0.0408 / 0.0034 |
| 48 | eco | 0.05371 / 0.05371 | 0.05380 / 0.05380 | 0.016495 / 0.016495 | 0.0091 / 0.0408 / 0.0078 | 0.0091 / 0.0408 / 0.0078 |
| 88.2 | hq | 0.00381 / 0.00381 | 0.00417 / 0.00417 | 0.000197 / 0.000197 | 0.0022 / 0.0178 / 0.0023 | 0.0022 / 0.0178 / 0.0023 |
| 88.2 | standard | 0.01525 / 0.01525 | 0.01547 / 0.01547 | 0.000213 / 0.000213 | 0.0355 / 0.0178 / 0.0038 | 0.0355 / 0.0178 / 0.0038 |
| 88.2 | eco | 0.01563 / 0.01563 | 0.01529 / 0.01529 | 0.004900 / 0.004900 | 0.0289 / 0.0178 / 0.0057 | 0.0289 / 0.0178 / 0.0057 |
| 96 | hq | 0.00321 / 0.00321 | 0.00367 / 0.00367 | 0.000195 / 0.000195 | 0.0075 / 0.0102 / 0.0011 | 0.0075 / 0.0102 / 0.0011 |
| 96 | standard | 0.01287 / 0.01287 | 0.01320 / 0.01320 | 0.000184 / 0.000184 | 0.0074 / 0.0102 / 0.0009 | 0.0074 / 0.0102 / 0.0009 |
| 96 | eco | 0.01319 / 0.01319 | 0.01296 / 0.01296 | 0.004171 / 0.004171 | 0.0018 / 0.0204 / 0.0022 | 0.0018 / 0.0204 / 0.0022 |
| 176.4 | hq | 0.00095 / 0.00095 | 0.00143 / 0.00143 | 0.000179 / 0.000179 | 0.0045 / 0.0067 / 0.0013 | 0.0045 / 0.0067 / 0.0013 |
| 176.4 | standard | 0.00381 / 0.00381 | 0.00425 / 0.00425 | 0.000176 / 0.000176 | 0.0045 / 0.0067 / 0.0020 | 0.0045 / 0.0067 / 0.0020 |
| 176.4 | eco | 0.00389 / 0.00389 | 0.00410 / 0.00410 | 0.001357 / 0.001357 | 0.0272 / 0.0067 / 0.0032 | 0.0272 / 0.0067 / 0.0032 |
| 192 | hq | 0.00080 / 0.00080 | 0.00123 / 0.00123 | 0.000183 / 0.000183 | 0.0018 / 0.0026 / 0.0009 | 0.0018 / 0.0026 / 0.0009 |
| 192 | standard | 0.00321 / 0.00321 | 0.00360 / 0.00360 | 0.000180 / 0.000180 | 0.0018 / 0.0051 / 0.0005 | 0.0018 / 0.0051 / 0.0005 |
| 192 | eco | 0.00329 / 0.00329 | 0.00347 / 0.00347 | 0.001180 / 0.001180 | 0.0004 / 0.0000 / 0.0005 | 0.0004 / 0.0000 / 0.0005 |

 All reference criteria pass in both backends at all rates. Worst operating-point error (Va / Vk / Ia / Vgk, all
settings and rates) is 2.15e-09 relative, against 0.001. No reference-validation or rest sample failed Newton
convergence.

 **Startup and rest.** Worst over the three settings, volts; all pass 1e-9 V.

| host kHz | 100 ms max Python | 100 ms max C++ | last 10 ms max Python | last 10 ms max C++ |
|---|---|---|---|---|
| 44.1 | 3.37e-14 | 0 | 2.5e-14 | 0 |
| 48 | 0 | 1.22e-13 | 0 | 8.93e-15 |
| 88.2 | 3.21e-14 | 0 | 7.31e-16 | 0 |
| 96 | 0 | 1.37e-13 | 0 | 8.79e-15 |
| 176.4 | 0 | 2.29e-13 | 0 | 7.55e-16 |
| 192 | 0 | 0 | 0 | 0 |

 **Full aliasing sweep.** Each cell is Python / C++ worst total dBc, with tone in Hz. The verdict uses the
unchanged limit at that drive.

| host kHz | setting | 2 V dBc P / C (tones P / C) | 2 V | 8 V dBc P / C (tones P / C) | 8 V |
|---|---|---|---|---|---|
| 44.1 | hq | -112.51 / -112.51 (19037 / 19037) | **FAIL** | -64.59 / -64.59 (19037 / 19037) | pass |
| 44.1 | standard | -112.61 / -112.61 (19037 / 19037) | **FAIL** | -47.53 / -47.53 (16037 / 16037) | **FAIL** |
| 44.1 | eco | -68.22 / -68.22 (13037 / 13037) | **FAIL** | -29.74 / -29.74 (18037 / 18037) | pass |
| 48 | hq | -121.22 / -121.22 (19037 / 19037) | pass | -66.03 / -66.03 (19037 / 19037) | pass |
| 48 | standard | -121.36 / -121.36 (19037 / 19037) | pass | -51.70 / -51.70 (19037 / 19037) | pass |
| 48 | eco | -75.74 / -75.74 (16037 / 16037) | pass | -32.65 / -32.65 (19037 / 19037) | pass |
| 88.2 | hq | -149.07 / -149.07 (15037 / 15037) | pass | -79.69 / -79.69 (19037 / 19037) | pass |
| 88.2 | standard | -149.29 / -149.29 (15037 / 15037) | pass | -64.59 / -64.59 (19037 / 19037) | pass |
| 88.2 | eco | -123.75 / -123.75 (11037 / 11037) | pass | -47.52 / -47.52 (16037 / 16037) | pass |
| 96 | hq | -152.95 / -152.95 (16037 / 16037) | pass | -81.55 / -81.55 (19037 / 19037) | pass |
| 96 | standard | -153.19 / -153.19 (16037 / 16037) | pass | -66.03 / -66.03 (19037 / 19037) | pass |
| 96 | eco | -131.06 / -131.06 (11037 / 11037) | pass | -51.74 / -51.74 (19037 / 19037) | pass |
| 176.4 | hq | -173.88 / -173.88 (1037 / 1037) | pass | -99.68 / -99.68 (19037 / 19037) | pass |
| 176.4 | standard | -173.88 / -173.88 (1037 / 1037) | pass | -79.69 / -79.69 (19037 / 19037) | pass |
| 176.4 | eco | -173.87 / -173.87 (1037 / 1037) | pass | -64.57 / -64.57 (19037 / 19037) | pass |
| 192 | hq | -173.88 / -173.88 (1037 / 1037) | pass | -102.77 / -102.77 (19037 / 19037) | pass |
| 192 | standard | -173.88 / -173.88 (1037 / 1037) | pass | -81.55 / -81.55 (19037 / 19037) | pass |
| 192 | eco | -173.87 / -173.87 (1037 / 1037) | pass | -66.02 / -66.02 (19037 / 19037) | pass |

 At **44.1 kHz**, HQ / 2 V, standard / 2 V and 8 V, and eco / 2 V fail. The unchanged limits are exceeded by
approximately 5.69, 5.69, 1.07 and 4.48 dB respectively. Eco / 8 V passes with only 0.14 dB margin. All other
rate/setting/drive combinations pass. The records retain the internal / resampler / other attribution from the
existing method. At 44.1 kHz the HQ and standard 2 V worst cases are dominated by the resampler share: the fixed
127-tap first FIR has a narrower 20 kHz-to-Nyquist transition than at 48 kHz. The 8 V standard and 2 V eco failures
contain internal folding. Retuning eco pre-warp does not address that filter transition or substitute for more
oversampling. A 44.1 kHz FIR redesign and renewed sweeps would be needed before claiming the existing aliasing
budget at that rate.

 **Eco pre-warp.** Existing `validate.prewarp_minimax(fs=...)`, same DZ grid and band edges, bounded minimisation
(1 Hz search tolerance), FIR and factor 4.

| host kHz | internal kHz | optimum Hz | worst dB at optimum | worst dB at 17300 Hz | without pre-warp dB |
|---|---|---|---|---|---|
| 44.1 | 176.4 | 17302.0 | 0.063550 | 0.063594 | 0.253891 |
| 48 | 192 | 17309.7 | 0.053621 | 0.053803 | 0.213409 |
| 88.2 | 352.8 | 17296.5 | 0.015275 | 0.015285 | 0.061400 |
| 96 | 384 | 17309.1 | 0.012919 | 0.012960 | 0.051851 |
| 176.4 | 705.6 | 17478.5 | 0.003864 | 0.004101 | 0.015552 |
| 192 | 768 | 17475.3 | 0.003278 | 0.003475 | 0.013136 |

 At **176.4 kHz internal** (44.1 kHz × 4), the optimum is 17,302 Hz: keeping 17,300 Hz costs only about 0.000044 dB
in worst error and passes 0.1 dB. Keep 17.3 kHz. At host rates 88.2 kHz and above (eco internal rate ≥ 352.8 kHz),
even plain trapezoidal passes; the optimum drifts slightly as FIR ripple becomes more significant, with no accuracy
requirement to retune.

 **Oversampling recommendation: retain the fixed factors in this change.** The alternative below keeps internal
rates close to the original 768 / 384 / 192 kHz, with the existing minimum factor 2 and eco pre-warp still 17.3
kHz. It was measured with C++ at all four high rates using the same complete reference, rest and offset-sweep
validation (`cpp/results_internal_rate_proposal.json`); its minimax search uses the Python analytical stage at the
proposed eco factor. HQ / standard / eco order throughout:

| host kHz | proposed factors | internal kHz | worst AC chain dB | H2–H5 max dB | aliasing failures |
|---|---|---|---|---|---|
| 88.2 | 8 / 4 / 2 | 705.6 / 352.8 / 176.4 | 0.01547 / 0.06140 / 0.06415 | 0.00021 / 0.00084 / 0.01960 | standard 8V, eco 2V |
| 96 | 8 / 4 / 2 | 768 / 384 / 192 | 0.01320 / 0.05185 / 0.05370 | 0.00018 / 0.00068 / 0.01658 | none |
| 176.4 | 4 / 2 / 2 | 705.6 / 352.8 / 352.8 | 0.01555 / 0.06148 / 0.01563 | 0.00016 / 0.00071 / 0.00503 | standard 8V |
| 192 | 4 / 2 / 2 | 768 / 384 / 384 | 0.01314 / 0.05179 / 0.01319 | 0.00017 / 0.00056 / 0.00428 | none |

 The 44.1 / 48 kHz proposal retains 16 / 8 / 4 and the existing pre-warp values. The proposal passes the reference
and rest criteria at each high rate, but **fails the unchanged aliasing limits at 88.2 kHz (standard / 8 V, eco / 2
V) and 176.4 kHz (standard / 8 V)**. At the proposed 88.2 kHz × 2, also 176.4 kHz internal, the minimax frequency
is 17,321 Hz; worst error is 0.063688 dB at the optimum and 0.064146 dB at 17,300 Hz. The difference from 44.1 kHz
× 4 comes from the changed resampler cascade/normalised frequencies. This also supports keeping 17.3 kHz; its
measured aliasing failure remains. Merely matching a nearby internal rate does not preserve the aliasing budget:
folding depends on the actual internal rate and tone, as the offset sweep shows. 96 and 192 kHz are promising for a
later selective reduction: HQ/standard at both rates, and eco at 96 kHz, can match their validated 48 kHz internal
rates exactly. That selective policy still needs Python time-domain/sweep validation and a decision on eco at 176.4
/ 192 kHz: factor 1 is unsupported by the current core, so eco at factor 2 costs the same internal steps as
standard (and retains its distinct pre-warp). The unimplemented proposal does not include those missing Python
sweeps.

 With fixed factors, internal steps per second scale with host rate: 88.2 / 96 / 176.4 / 192 kHz require 1.8375 / 2
/ 3.675 / 4 times the 48 kHz internal samples. Those are sample counts, not measured CPU ratios. The proposed
reductions halve that count at 88.2 / 96 kHz and quarter HQ/standard at 176.4 / 192 kHz; eco is limited to factor
2. No performance claim is inferred from an unrun benchmark.

 **Latency.** The fixed FIR geometry gives HQ / standard / eco = **73 / 72 / 69 base-rate samples at every rate**,
checked in Python and native C++ mono/stereo tests. The host must use `latency_samples()` after prepare;
milliseconds change with the session rate:

| host kHz | HQ ms | standard ms | eco ms |
|---|---|---|---|
| 44.1 | 1.6553 | 1.6327 | 1.5646 |
| 48 | 1.5208 | 1.5000 | 1.4375 |
| 88.2 | 0.8277 | 0.8163 | 0.7823 |
| 96 | 0.7604 | 0.7500 | 0.7188 |
| 176.4 | 0.4138 | 0.4082 | 0.3912 |
| 192 | 0.3802 | 0.3750 | 0.3594 |

 A future factor mapping changes the sample count as well: factor 8 / 4 / 2 reports 72 / 69 / 63 samples. A host
must refresh its reported delay after any prepare that changes the factor or rate; converting the old 48 kHz
milliseconds to samples would be wrong. No wrapper changes or live transition behavior are part of this
measurement.

**C++ / Python agreement.** All 18 reference-result comparisons pass the existing `cpp.validate_cpp.LIMITS`.
Worst AC difference is 1.26e-7 dB (limit 1e-6); tested H2–H5 difference 9.12e-8 dB (limit 1e-5), H1 relative
1.80e-9 (limit 1e-7), THD relative 1.97e-9 (limit 1e-6), and burst voltage difference 5.00e-9 V (limit 1e-6 V).
Burst minimum times and recovery times match exactly, as do all Newton histograms. Over all 684 offset-sweep
points, the largest difference in total dBc is 6.48e-6 dB; both backends give the same pass/fail decisions.
These are reproduction differences, separate from the analog-reference errors and aliasing acceptance limits.

**Verification on this host.** `python -m pytest -q dk/tests cpp/tests -k 'not bench_smoke'`: **227 passed,
2 skipped** (the x86-only C++ builds on arm64), **1 deselected** (the timed benchmark smoke test, not run).
This includes the new Python rate/minimax/rest/metric checks, C++/Python short waveform parity at every rate,
and native C++ mono/stereo prepare/process, rest, convergence, latency and allocation checks at all six rates
with libm and fast math. `python -m dk.aliasing_guard --workers 8` passes every existing 48 kHz FIR/IIR limit
and baseline ratchet. The multi-rate validators return 1 for the four genuine 44.1 kHz aliasing failures;
the experimental factor proposal returns 1 for its three documented failures. All numerical suites ran serially.
Ruff 0.17.3 passes with the unchanged 120-character E/F configuration.

## 44.1 kHz aliasing (#96)

**Recommendation: combine a rate-appropriate internal-folding budget with a longer first FIR.** At 44.1 kHz,
ship 231 / 23 taps (first / later stages, Kaiser β 9), keep HQ / standard / eco at 16× / 8× / 4× and keep eco's
17.3 kHz pre-warp. Preserve the existing HQ/standard 2 V limits: the FIR addresses those resampler-floor misses.
Use the independently derived octave-headroom limits below for internal folding. This accepts a measured rate
trade-off; it does **not** claim to meet every original 48 kHz aliasing limit or establish inaudibility. No 48 kHz
limit, baseline, guard, factor, coefficient or delay is changed. The #95 recommendation against halving factors
at higher host rates still stands; #74's broader rate-policy work is not settled by this change.

### Method and baseline confirmation

`dk/experiments/issue96.py` generates isolated C++ research headers from the exact #95 core (`bb588c4`), then
uses the existing `cpp/test_core.cpp` CLI and `dk.aliasing.measure` metric. It measures the original design,
a 231-tap first FIR, eco pre-warp at 20 kHz, and a first-order ADAA prototype. All points are **44,100 Hz,
FIR / libm / double / mono**, Drive = Bias = Output = 1, sine peak 2 / 8 V, the complete 19-tone offset grid
1037, 2037, …, 19037 Hz. Settling is 0.25 s and analysis 1 s, periodic Hann, ±3-bin harmonic masks,
20 Hz–20 kHz total non-harmonic energy relative to the fundamental. The coherent-bin/collision-order safeguard
and extended-input/drop-output latency compensation are unchanged. Each candidate starts from its DC rest point.
The experimental pre-warp applies only to eco; other qualities are not re-labelled as new measurements.

`results_issue96.json` retains every sweep record, the A calculation and the initial short CPU runs.
`results_issue96_cost.json` contains the comparative CPU repeats, measured group delay and ADAA quadrature check.
`results_issue96_shipped.json` separately measures the **actual shipped Python and C++ implementations**, full
reference validation and full offset sweeps, plus a complete short-buffer C++ grid to verify CI coverage.
The #95 sample-rate JSONs and 48 kHz 64× ground truth remain unchanged. No 44.1 kHz 64× ground truth was generated.
Suites ran serially; the shipped Python sweep alone uses independent worker processes within its suite.

The pinned baseline full sweep reproduces the #95 worst values below (both Python and C++ #95 records agree at
this displayed precision). It confirms the four misses and eco/8 V's tight pass before making any comparison.

| setting | drive V | original 44.1 kHz worst dBc | tone Hz | 48 kHz limit dBc | margin dB |
|---|---|---|---|---|---|
| hq | 2 | −112.51 | 19037 | −118.2 | −5.69 |
| hq | 8 | −64.59 | 19037 | −63.0 | 1.59 |
| standard | 2 | −112.61 | 19037 | −118.3 | −5.69 |
| standard | 8 | −47.53 | 16037 | −48.6 | −1.07 |
| eco | 2 | −68.22 | 13037 | −72.7 | −4.48 |
| eco | 8 | −29.74 | 18037 | −29.6 | 0.14 |

### A: a headroom budget, with audibility considered and rejected as the acceptance basis

Use an empirical power-law model of alias **energy relative to the fundamental**, calibrated independently of
44.1 kHz: for each setting/drive, let `W48` and `W96` be the full-sweep worst total dBc already committed in #95
with fixed factors. Define `s = W48 − W96` dB per octave, equivalent to energy proportional to internal rate
raised to `−s/(10 log10(2))`. Losing `log2(48000/44100) = 0.122256` octaves then costs
`Δ = s log2(48000/44100)` dB. Transfer the **existing limit**, including its existing margin, by that Δ and round
up to 0.1 dB: `L44 = ceil(10 (L48 + Δ)) / 10`. Do not add another 3 dB. This rule predicts acceptance before
using the 44.1 kHz sweep; it is not a fit of the acceptance line to that sweep's maximum.

| setting | drive V | s dB/octave | predicted Δ dB | pure headroom proposal dBc | original 44.1 margin dB |
|---|---|---|---|---|---|
| hq | 2 | 31.7266 | 3.8788 | −114.3 | **−1.79** |
| hq | 8 | 15.5174 | 1.8971 | −61.1 | 3.49 |
| standard | 2 | 31.8271 | 3.8911 | −114.4 | **−1.79** |
| standard | 8 | 14.3287 | 1.7518 | −46.8 | 0.73 |
| eco | 2 | 55.3200 | 6.7632 | −65.9 | 2.32 |
| eco | 8 | 19.0897 | 2.3338 | −27.2 | 2.54 |

This is a local engineering budget, not a universal spectral law: harmonic crossings move the worst tone and
make rate dependence non-smooth. The untouched 88.2 kHz fixed-factor sweep is an intermediate held-out check:
for the four internal-folding rows (HQ/8 V, standard/8 V, eco/2 V, eco/8 V), measured minus predicted is
−0.044 / −0.316 / +0.550 / +1.884 dB, within the original approximately 3 dB margin. At 44.1 kHz,
standard/8 V is 2.42 dB worse than the predicted *measurement*, leaving only 0.73 dB against the transferred
limit. That uncertainty is explicit; no extra allowance was added to cover it.

**Reject applying this model to the HQ/standard 2 V resampler floor.** Their original 44.1 kHz results fail even
the pure headroom proposals. The filter transition changes disproportionately when the base Nyquist approaches
20 kHz; internal-rate scaling alone does not model it. Retain −118.2 / −118.3 dBc there and redesign the FIR.
For the four internal-folding rows, choose the headroom proposals as the 44.1 kHz limits. Attribution in the
original sweep supports this distinction: HQ/standard 2 V are resampler dominated; the other four are internal
folding dominated. Tests derive the selected numbers from the independent 48/96 kHz records.

An **audibility-based budget** would require output calibration, playback SPL, alias frequencies, masking by
the fundamental/harmonics and a stated listening criterion. dBc alone supplies none of those: the same relative
alias level can correspond to different absolute listening levels. The low-drive HQ/standard floor and the
strongly driven eco result also cannot share a credible universal audibility threshold. No calibrated listening
experiment or perceptual reference was measured here, so do not invent an “inaudible” dBc cutoff or substitute
one for the engineering budget. The headroom argument is reproducible from existing independent measurements;
the audibility argument is rejected as an acceptance basis with the available evidence.

### B: FIR, pre-warp and ADAA experiments

**FIR design.** The first half-band's protected transition is from 20 kHz to `fs − 20 kHz`: 8 kHz at 48 kHz,
4.1 kHz at 44.1 kHz. Scale the existing **tap length** inversely with normalised transition width:
`127 × (8000/96000) / (4100/88200) = 227.6707`; round upward to a half-band length `4m+3`, giving **231 taps**.
Scaling length rather than order (`N−1`) is conservative; this is not a minimum-tap optimisation. Keep cutoff
at half-band and β = 9, and keep the later 23-tap stages. Filter-spec tests enforce the existing 0.01 dB
passband / 85 dB image-rejection requirements at 44.1 kHz. This restores the 2 V resampler rejection, but does
not remove aliases already folded inside the circuit solver.

**Pre-warp.** Test 20 kHz, the top of the protected audio band, as an alternative to the existing 17.3 kHz
minimax compromise. It changes the small-cap companion coefficients without adding per-sample operations or
buffering. It makes eco aliasing slightly worse. The direct 1 mV / 20 kHz gain error changes from −0.06359 dB
to +0.00148 dB, but that single-frequency result is not a full-band accuracy claim. #95's independent minimax
search still favours 17,302 Hz at this internal rate. Retain 17.3 kHz; retuning it does not repair FIR transition
rejection or substitute for oversampling.

**ADAA applicability and honest prototype.** The stage is an implicit two-port nonlinear feedback system,
`v = p − K i(v)`, with capacitor history updated from the solved currents. Applying a scalar waveshaper's ADAA
formula only to the final audio output would not anti-alias this stage. However, the DZ currents decompose into
two scalar laws: grid current depends on Vgk; cathode current depends on `Vgk + Vak/μ`; plate current is cathode
minus grid current. A first-order divided difference of each law's antiderivative equals its average along the
segment from the previous solved port value to the current unknown.

The prototype evaluates that integral by eight-point Gauss–Legendre quadrature, avoiding subtraction of nearby
antiderivatives of fractional softplus powers. It integrates both currents and the **matching Jacobian**
`∫ t J(v_previous + t(v − v_previous)) dt`; history stays fixed during every Newton solve and line search.
DC still uses the original law. This is a changed feedback discretisation, with its own frequency response,
not a circuit-equivalent drop-in. The research generator changes the mono tick only; no production ADAA or
stereo ADAA option is exposed. The `currents_only` prototype also evaluates the Jacobian, so its measured cost
is the cost of this honest prototype, not an optimised lower bound for all ADAA implementations.

Worst full-sweep dBc for every implemented candidate; “—” means the pre-warp candidate does not apply to that
quality. The two limit columns allow direct comparison with both the original budget and A's pure proposal.

| setting / drive | 48 kHz limit | pure A limit | baseline | FIR 231 | eco pre-warp 20k | ADAA 8-node |
|---|---|---|---|---|---|---|
| hq / 2 V | −118.2 | −114.3 | −112.51 | −122.13 | — | −113.00 |
| hq / 8 V | −63.0 | −61.1 | −64.59 | −64.59 | — | −71.78 |
| standard / 2 V | −118.3 | −114.4 | −112.61 | −122.09 | — | −113.41 |
| standard / 8 V | −48.6 | −46.8 | −47.53 | −47.53 | — | −45.74 |
| eco / 2 V | −72.7 | −65.9 | −68.22 | −68.22 | −67.74 | −72.38 |
| eco / 8 V | −29.6 | −27.2 | −29.74 | −29.74 | −29.72 | −24.47 |

The longer FIR passes all six chosen limits; it still fails the original standard/8 V and eco/2 V limits.
Pre-warp 20k leaves both resampler-floor misses and standard/8 V unchanged, and still fails eco/2 V against
the original budget. ADAA passes only HQ/8 V against the original budget; against the pure A proposal it passes
HQ/8 V and eco/2 V, while standard/8 V and eco/8 V worsen. In particular, ADAA eco/2 V at −72.38 dBc still
misses −72.7 dBc. There is no claim of universal ADAA improvement.

ADAA's directly measured 1 mV / 20 kHz errors versus the existing DZ AC reference are **−0.14089 / −0.31350 /
−0.58280 dB** (HQ / standard / eco), exceeding the unchanged 0.1 dB criterion in every setting. These come from
actual processed audio; using the old DK matrices' analytical small-signal result for ADAA would be invalid.
Together with the aliasing regressions and CPU cost, this rejects this ADAA discretisation. More elaborate
state/integrator compensation or a different ADAA method remains research work, not an unmeasured shipped option.

### Comparative M1 CPU and latency cost

`python -m dk.experiments.cost_issue96` uses the existing `cpp/bench.cpp` signal, block timers, counters and
round-robin configuration scheduling, with generated headers for each option. Apple M1 (MacBookAir10,1,
4 P + 4 E cores), macOS 15.6.1, Apple clang 17, project warning flags and **`-O3`**; no fast-math switch.
Mono/libm, block 64, same quality and 2 / 8 V input peak for baseline and candidate. Each pass processes
22,016 base samples (0.499229 s), the beginning of the existing Karplus–Strong phrase, with one untimed warm-up
and **three timed passes**. Repeat three short rounds, forward / reverse / forward candidate order; report the
median of the three run medians. Runs are serial, on the same machine, without simultaneous numerical suites.
Raw run ranges, host power/thermal/load state, counters and P-core fractions are committed in
`results_issue96_cost.json`; the earlier five-pass exploratory timings remain in `results_issue96.json`.
This is a targeted comparison, not a replacement for the official CPU benchmarks.

Wall ns per base sample; parentheses are candidate / same-quality, same-level baseline:

| quality / input peak | baseline ns | FIR 231 ns (ratio) | pre-warp 20k ns (ratio) | ADAA 8-node ns (ratio) |
|---|---|---|---|---|
| hq / 2 V | 5271.3 | 5219.3 (0.990×) | — | 27525.5 (5.222×) |
| hq / 8 V | 5853.0 | 5866.6 (1.002×) | — | 29232.1 (4.994×) |
| standard / 2 V | 2881.1 | 2897.6 (1.006×) | — | 14985.6 (5.201×) |
| standard / 8 V | 3220.6 | 3246.7 (1.008×) | — | 16261.7 (5.049×) |
| eco / 2 V | 1635.0 | 1662.7 (1.017×) | 1630.9 (0.997×) | 8586.2 (5.251×) |
| eco / 8 V | 1772.6 | 1795.9 (1.013×) | 1771.6 (0.999×) | 8735.4 (4.928×) |

FIR ratios span 0.990–1.017: small effects relative to run variability, not evidence of an HQ speedup. For
example HQ/2 V run medians span 5213.9–5360.9 ns baseline and 5190.0–5290.8 ns with the longer FIR. Pre-warp
has the same runtime operation structure and its measured ratios are approximately 1. ADAA costs 4.93–5.25×
in this prototype. Every timed configuration was deterministic and had zero counted non-converged samples.
Some passes migrated between core types (lowest reported P-cycle fraction 0.9226); do not turn these short
wall-clock comparisons into precise hardware-throughput or cross-machine claims.

| option | reported HQ / standard / eco base samples | buffering increase | measured 1 kHz group-delay change, base samples |
|---|---|---|---|
| original 44.1 kHz | 73 / 72 / 69 | reference | 0 / 0 / 0 |
| first FIR 231 taps | 125 / 124 / 121 | +52 each = +1.17914 ms | +52.000000 / +52.000000 / +52.000000 |
| eco pre-warp 20k | — / — / 69 | 0 | — / — / −0.005206 |
| ADAA prototype | 73 / 72 / 69 | 0 whole output-delay samples | +0.011275 / +0.022544 / +0.045118 |

The group-delay measurement processes coherent 999 and 1001 Hz tones, 10 µV each, with 0.25 s settling and a
1 s window, and differentiates their complex transfer phase. It includes the circuit's phase, whereas reported
latency is the FIR delay. ADAA's port averaging changes frequency-dependent phase through feedback, so its
unchanged reported FIR delay must not be described as unchanged transfer phase or as an exact half-sample
output delay. Pre-warp also changes circuit phase without adding buffering. The FIR's exact added delay is
confirmed by the group measurement and impulse tests. Its structural non-zero coefficient count per direction
rises by 52 (247/143/91 → 299/195/143 for HQ/standard/eco); this is not an instruction or CPU estimate.

The sixteen-node ADAA check repeats all six eight-node sweep worst cases and each quality/drive at 19,037 Hz
(nine unique points). Largest total-dBc change is **0.001901 dB**, far smaller than the aliasing regressions
that reject this candidate. This is a targeted integration-convergence check, not a sixteen-node full sweep.

### Shipped measurements, CI and scope

Full shipped sweeps (114 points per backend), Python / C++ worst dBc; both give the same tone and verdict.
The chosen column is `LIMIT_DBC_44100`, not a change to `LIMIT_DBC`.

| setting / drive | Python / C++ dBc | worst tone Hz | chosen limit dBc | full-sweep margin dB |
|---|---|---|---|---|
| hq / 2 V | −122.13 / −122.13 | 18037 | −118.2 | 3.93 |
| hq / 8 V | −64.59 / −64.59 | 19037 | −61.1 | 3.49 |
| standard / 2 V | −122.09 / −122.09 | 18037 | −118.3 | 3.79 |
| standard / 8 V | −47.53 / −47.53 | 16037 | −46.8 | 0.73 |
| eco / 2 V | −68.22 / −68.22 | 13037 | −65.9 | 2.32 |
| eco / 8 V | −29.74 / −29.74 | 18037 | −27.2 | 2.54 |

Maximum Python/C++ total-dBc difference across the full grid is **1.85e-7 dB** (rounded upward). Complete
operating-point, AC stage/chain, tested H2–H5 and burst criteria pass against the existing DZ reference in both
backends, with no tolerance changes. Worst AC-chain error per quality is 0.015138 / 0.061068 / 0.063814 dB.
All Python/C++ comparison tolerances pass; Newton histograms match for these reference checks. First-100-ms
rest stays below 1e-9 V (worst Python 3.384e-14 V; C++ zero). A separate comparison against the pinned original
C++ binary at **48 kHz**, 8192 samples per quality and math path, is bit-identical for all six mono cases
(libm/fast × HQ/standard/eco); this is a waveform check, not a new 48 kHz full sweep.

The original **18-point 48 kHz FIR/IIR guard and its baseline are retained**. The same 15-minute CI job then
runs `--fs 44100` with **11 FIR points**, the chosen limits and an independent
`aliasing_baseline_44100.json` using the same 1.5 dB ratchet. The new baseline is intentionally established with
`--update-baseline --fs 44100 --reason "Establish 44.1 kHz FIR ratchet after transition-scaled 231-tap design and #96 octave-headroom limits; 48 kHz untouched"`.
No 48 kHz baseline is refreshed. A baseline from another host rate is refused before measuring.

| quality | 2 V CI tones Hz | 8 V CI tones Hz |
|---|---|---|
| hq | 18037, 19037 | 10037, 19037 |
| standard | 18037, 19037 | 10037, 16037 |
| eco | 13037 | 10037, 18037 |

The **entire** 114-point short-buffer C++ sweep (0.2 s settling / 0.1 s window) confirms short-buffer worst
coverage as well as full-sweep coverage. HQ/standard 2 V peak at **19037** with short buffers, versus **18037**
in the full sweep, so both tones are retained. Standard/8 V's short-buffer worst is −47.42561 dBc, leaving
0.62561 dB against −46.8. Other short-buffer worst totals are −122.08612 / −64.59069 / −122.14770 / −68.22701 /
−29.72205 dBc (HQ2/HQ8/standard2/eco2/eco8). The mid-band 10037 Hz points at 8 V are retained. The guard also
checks its actual Python short-buffer records and committed baseline, independently of the C++ coverage sweep.
The coverage tests check the complete committed grids, so a changed subset cannot quietly omit a measured worst.

The longer FIR changes **latency**, so the shared plugin engine's formerly rate-independent target-latency
table required a narrow change. It now derives 44.1 and other-rate tables from the core, reads the prepared
rate atomically for any-thread target queries, and keeps the existing active-stage query for AU. The 512-sample
dry ring already holds the new maximum (256 + 125 < 512). Null-test expectations pass the real rate; engine
bypass and live quality-switch tests cover both 44.1 and 48 kHz. No AU/VST3 DSP or parameter flow is changed.

Not measured: calibrated audibility/listening, a new 44.1 kHz 64× truth sweep, full 44.1 kHz IIR or fast-math
aliasing sweeps, stereo aliasing sweeps, all drive/bias positions, compensated/higher-order ADAA, official CPU
benchmarks, other machines, or live host sample-rate transitions. Native mono/stereo and libm/fast checks cover
prepare/process and latency at the existing six rates. Positive rates other than exactly 44,100 Hz retain the
original FIR geometry; the new budget/CI subset certifies only 44.1 kHz FIR at the settings above. The GitHub
Actions runtime and packaged AU/VST3 host checks still need the normal CI/review run; no push is made here.

**Verification on this M1.** `python -m pytest -q dk/tests cpp/tests` → **241 passed, 2 skipped** (x86-only
builds on arm64), including the short benchmark functional smoke, native mono/stereo rate/latency/allocation
checks, current Python/C++ rate parity, and the new budget/baseline/filter/coverage tests. The initial new
metadata assertion compared a JSON list with a Python tuple; normalising its JSON representation corrected
the assertion, and the complete suite was rerun successfully. No DSP tolerance was changed in response.
Standalone `clang++ -std=c++17 -O2 -Wall -Wextra -pedantic -pthread` build of `map_engine.cpp` +
`plugin/tests/engine_test.cpp` → **PASS (0 failed)**, including exact nulls at 44.1 / 48 / 96 kHz and bypass/live
switch checks at 44.1 and 48 kHz. Packaged AU/VST3 validators/null tools were not built or run locally.

Both Python guards pass every absolute limit and ratchet row: **13.52 s at 48 kHz** (18 points) and **8.82 s at
44.1 kHz** (11 points), serially on this M1, recorded in `results_issue96_guard_{48000,44100}.json`.
Their approximately 22.34 s local total is not a hosted-runner timing claim. CI retains its 15-minute timeout;
no full sweep, C++ build or CPU benchmark is added to the aliasing job. The #95 results and original
`aliasing_baseline.json` have no diff. `dk.sample_rates --resume` now refuses older DSP/aliasing-policy metadata
before writing, preventing an old 127-tap sweep from being relabelled with the new policy. This refusal was
checked against the historical #95 JSON, with its bytes verified unchanged. Ruff 0.17.3 passes all prototypes
with the unchanged 120-character E/F configuration.

Reproduction (from `prototypes/fullstage`, dependencies from `requirements.txt`; run suites serially):

```bash
mkdir -p ../../.scratch/tmp
export TMPDIR="$PWD/../../.scratch/tmp"
python -m dk.experiments.issue96 --scratch ../../.scratch/issue96
python -m dk.experiments.verify_issue96 --workers 8 --scratch ../../.scratch/issue96
python -m dk.experiments.cost_issue96 --scratch ../../.scratch/issue96
python -m dk.aliasing_guard --workers 8
python -m dk.aliasing_guard --fs 44100 --workers 8
python -m pytest -q dk/tests cpp/tests
```

Changing just A's acceptance budget adds no DSP operations or sample delay; A alone does not solve the two
resampler-floor misses. The chosen combination spends 52 samples and a small measured CPU difference on
restoring those limits, while retaining a principled rate trade-off and a regression ratchet for internal
folding. The rejected pre-warp and ADAA measurements remain committed alongside the recommendation.
