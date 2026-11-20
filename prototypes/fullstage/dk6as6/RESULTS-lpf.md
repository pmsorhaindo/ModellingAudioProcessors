# 6AS6 post-distortion low-pass (#103): results

`dk6as6/lpf.py`, tests in `dk6as6/tests/test_lpf.py`, aliasing sweep `python -m dk6as6.lpf_aliasing` →
`lpf_aliasing.json`. Every value traces to the #103 research comment
(https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/103#issuecomment-7021776633); S1–S9 are the source table
of the #102 comment (https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/102#issuecomment-7021775895).

## What is modelled, and where each value comes from

| item | value | label / source |
|---|---|---|
| order, placement | 2-pole low-pass (12 dB/oct), after the 6AS6 stage | sourced: S1, S2, S4, S7 ("12dB/octave", "second-order"); S6 ("at the output of the 6AS6 … circuit") |
| positions | Off / 4k / 7k | sourced: S1, S7 (2003 revision). Other revisions: 5 / 9 kHz (S2), 6 / 9 kHz (S6), 7 kHz only (S4) |
| shape | Q 0.4277 (the comment's table gives 0.428; 0.4277 to four digits in the brief) | fitted to the digitised S5 (Super 15) LPF curve, F-a: RMS 0.023 dB, max 0.12 dB over 300 Hz–7.5 kHz |
| 7k position | f0 9535 Hz (−3 dB at 4.93 kHz with these rounded values; the comment says 4.92 kHz), −5.0 dB at 7 kHz | **inferred**: 7k = the S5 curve (revisit 2; the alternative, label = −3 dB, puts f0 near 13.6 kHz) |
| 4k position | f0 9535 × 4/7 = 5449 Hz | **inferred**: the S5 shape scaled by 4/7 |
| topology | linear, decoupled (buffered) from the plate | **inferred** from the fixed published corners: a plate-loaded RC would move its corner up to ~2.2× with mode and bias (#102 E5) and change P1's H2 by 0.7–3.6 dB. No source gives a circuit, component values, or a nonlinear filter |
| "Off" HF roll-off (S6, except in P2) | not modelled | revisit 5; #111 |

Changing revision (5/9 or 6/9 kHz) is one line per position in `lpf.F0_HZ`.

## Method
- **TPT state-variable filter** (Zavalishin; Simper 2013): g, k = 1/Q, a1 = 1/(1 + g(g + k)), a2 = g a1, a3 = g a2. Chosen
  for F-c (lowest float32 noise at 8×) and F-d (stable under modulation).
- **Increment form.** Simper's state update ic ← 2v − ic is computed as ic ← ic + 2(v − ic), with v − ic formed directly
  from a2, a3 and b1 = 1 − a1 = g(g + k)a1. It is the same filter in exact arithmetic (the transfer-function and
  modulation tests pass unchanged), but in float32 the constant-input fixed point no longer depends on coefficient
  rounding and the states take small increments instead of being re-formed from two near-equal terms. It replaces the
  direct form, which missed the float32 limit at some frequencies (#119 review: −117.2 dBc at 19.6 kHz, 8 × 44.1 kHz).
- **Inside the oversampled block.** `lpf.Stage` (a subclass of `dk6as6.stage.Stage`) filters the stage's internal-rate
  `out` in `process_internal`, so `process` decimates the filtered signal. The stage's own solve is untouched (the filter
  does not load the plate), so the class is independent of the stage's values (#102 / PR #118) and works on any of them.
  With `lpf="off"` the output is the unfiltered stage's, bit for bit (tested).
- **Pre-warp at 15.7 kHz**, not at f0. g = tan(π fp/fs) · f0/fp. The textbook choice fp = f0 leaves 0.1533 dB / 0.3507°
  worst error at 8 × 44.1 kHz, over the brief's 0.15 dB / 0.35° limits. PREWARP_HZ minimises the worst complex error
  |H_digital/H_analog − 1| over 20 Hz–20 kHz, both positions, 8 × 44.1 and 8 × 48 kHz (`lpf.prewarp_minimax()`:
  15.685 kHz, −42.3 dB). It costs nothing at runtime. `SVF(prewarp_hz=None)` keeps the f0 pre-warp, for modulation use.
- **Position changes**: both filters run all the time; the output crossfades linearly over 15 ms (F-d: an instant switch
  gives a −31 dBFS transient at 384 kHz in every structure). Cost: two SVF ticks per internal sample.

## Results (brief limits in brackets)

Magnitude and phase vs the analog prototype, 20 Hz–20 kHz (worst at 20 kHz); measured on the implementation's impulse
response. The host-rate column is the same SVF run at 44.1 / 48 kHz, for comparison (F-b).

| internal rate | position | PREWARP_HZ [≤ 0.15 dB / ≤ 0.35°] | fp = f0 | host rate (BLT), at 10 / 20 kHz |
|---|---|---|---|---|
| 8 × 44.1 kHz | 4k | 0.0638 dB / 0.2932° | 0.1533 dB / 0.3052° | 1.80 / 25.29 dB |
| 8 × 44.1 kHz | 7k | 0.0541 dB / 0.2611° | 0.1087 dB / 0.3507° | 0.18 / 21.71 dB |
| 8 × 48 kHz | 4k | 0.0537 dB / 0.2473° | 0.1293 dB / 0.2575° | 1.47 / 16.58 dB |
| 8 × 48 kHz | 7k | 0.0456 dB / 0.2202° | 0.0916 dB / 0.2957° | 0.14 / 13.60 dB |

End to end (`Stage(lpf="4k")` over the unfiltered stage, 10 mV multitone, base-rate output): within the same limits at
1, 5, 10 and 19 kHz.

Float32 vs float64 (same float32 input, 50 ms, error energy over output energy, DC included) [≤ −125 dBc]: 31
third-octave sines over 20 Hz–20 kHz, 1 kHz, the review's 3.2 / 13.6 / 19.6 kHz, and white noise. The worst sine is at
the top of the band, where the 4k output is 22 dB below its input, so the error relative to the output is largest.

| internal rate | position | 1 kHz sine | worst sine | white noise | direct form (before): 1 kHz / worst sine / noise |
|---|---|---|---|---|---|
| 8 × 44.1 kHz | 4k | −141.6 dBc | −131.5 dBc (20 kHz) | −138.5 dBc | −133.3 / −117.2 (19.6 kHz) / −130.1 |
| 8 × 44.1 kHz | 7k | −144.7 dBc | −139.2 dBc (20 kHz) | −140.4 dBc | −134.3 / −131.5 (13.6 kHz) / −133.2 |
| 8 × 48 kHz | 4k | −142.4 dBc | −132.3 dBc (19.6 kHz) | −138.2 dBc | −132.0 / −123.4 (13.6 kHz) / −129.5 |
| 8 × 48 kHz | 7k | −145.8 dBc | −137.3 dBc (19.6 kHz) | −139.8 dBc | −133.7 / −129.9 (19.6 kHz) / −132.1 |

Where the direct form's excess came from: with float32 coefficients but float64 states it already reads up to
−133.2 dBc in the 4k position (coefficient rounding moves its response), against −146.1 dBc worst for the increment form.
A float32 step settles 4–8 ulp low in the increment form (the states' rounding deadband), 11–18 ulp in the direct form.
The C++ core runs in double, so float32 here shows the brief's float32 option is safe; it is not the port's precision.

Modulation: f0 changed every sample (random over 20 Hz–0.45 fs, and alternating 4k / 7k), Q 0.4277 and 5, both pre-warp
choices: finite and bounded, and below 1e-9 over the last 2.6 ms of 52 ms of silence.

### Aliasing, filter on vs off (`lpf_aliasing.json`)
The `dk/aliasing` metric and full sweep (1037…19037 Hz, 1 s window), standard 8×, FIR, on the **#102 stage values** (PR
#118) at the four reference biases of #110 (`dk6as6.aliasing.BIASES`: 0.1, 0.25, 0.45, 0.6 mA), after
`dk6as6.aliasing.SETTLE_S` = 1.25 s: the #102 stage's coupling caps settle 4–5× slower than the 12AX7 stage's, and dk's
0.2–0.25 s leaves the startup drift dominating the metric (−82 dB in the CI's 0.1 s window, whatever the filter
position). The off column reproduces `dk6as6/aliasing.json` exactly at every bias. "dBc" is relative to each output's
own fundamental (the repo metric); "abs" is the same total relative to the **unfiltered** fundamental. Worst over the
sweep, dBc (tone) / abs (tone):

| rate, bias, drive | off | 4k: dBc / abs | 7k: dBc / abs | largest change on − off, abs, any tone |
|---|---|---|---|---|
| 48 kHz, 0.1 mA, 2 V | −140.3 (19037) | −131.1 (19037) / −154.0 (19037) | −133.1 (19037) / −148.0 (19037) | +0.002 dB (floor) |
| 48 kHz, 0.1 mA, 8 V | −112.5 (19037) | −99.6 (19037) / −122.5 (19037) | −103.8 (19037) / −118.7 (19037) | −0.01 dB |
| 48 kHz, 0.25 mA, 2 V | −148.0 (19037) | −132.8 (16037) / −153.0 (16037) | −140.1 (19037) / −153.0 (16037) | −0.01 dB |
| 48 kHz, 0.25 mA, 8 V | −107.2 (19037) | −92.3 (19037) / −115.2 (19037) | −97.4 (19037) / −112.3 (19037) | −0.11 dB |
| 48 kHz, 0.45 mA, 2 V | −130.4 (19037) | −121.1 (19037) / −144.0 (19037) | −123.2 (19037) / −138.1 (19037) | −0.05 dB |
| 48 kHz, 0.45 mA, 8 V | −101.0 (19037) | −83.9 (19037) / −106.8 (19037) | −89.5 (19037) / −104.4 (19037) | −0.17 dB |
| 48 kHz, 0.6 mA, 2 V | −123.6 (19037) | −114.4 (19037) / −137.3 (19037) | −116.4 (19037) / −131.3 (19037) | −0.10 dB |
| 48 kHz, 0.6 mA, 8 V | −98.4 (19037) | −80.9 (19037) / −103.8 (19037) | −86.5 (19037) / −101.4 (19037) | −0.18 dB |
| 44.1 kHz, 0.1 mA, 2 V | −143.1 (19037) | −129.6 (19037) / −152.5 (19037) | −133.4 (19037) / −148.3 (19037) | +0.002 dB (floor) |
| 44.1 kHz, 0.1 mA, 8 V | −109.8 (18037) | −100.5 (19037) / −122.8 (18037) | −102.8 (18037) / −117.0 (18037) | −0.01 dB |
| 44.1 kHz, 0.25 mA, 2 V | −149.9 (8037) | −137.6 (15037) / −156.9 (15037) | −144.5 (15037) / −156.5 (15037) | −0.01 dB |
| 44.1 kHz, 0.25 mA, 8 V | −104.8 (19037) | −93.8 (17037) / −115.0 (17037) | −96.8 (19037) / −110.8 (17037) | −0.11 dB |
| 44.1 kHz, 0.45 mA, 2 V | −133.1 (19037) | −119.6 (19037) / −142.5 (19037) | −123.4 (19037) / −138.3 (19037) | −0.05 dB |
| 44.1 kHz, 0.45 mA, 8 V | −95.2 (19037) | −85.2 (19037) / −108.1 (19037) | −87.5 (19037) / −102.4 (19037) | −0.23 dB |
| 44.1 kHz, 0.6 mA, 2 V | −126.3 (19037) | −112.7 (19037) / −135.5 (19037) | −116.5 (19037) / −131.4 (19037) | −0.10 dB |
| 44.1 kHz, 0.6 mA, 8 V | −90.8 (19037) | −81.1 (19037) / −103.9 (19037) | −83.2 (19037) / −98.2 (19037) | −0.19 dB |

Over the 304 points × 2 positions, the absolute total alias energy with the filter on is lower than off in 604 cases
and higher in 4, by +0.0014 to +0.0024 dB: 1037 Hz, 0.1 mA, 2 V, 4k and 7k, both rates, where every output reads
−209.9 dB, below `dk.aliasing.ANALYSIS_FLOOR_DBC` (−200 dBc). In the 540 cases above that floor the filter lowered the
total every time (by 0.006 dB at least). That is a measurement of the total, not a
proof for each line: |H| ≤ 1 before decimation does not by itself bound every output bin, since components from
several internal frequencies can fold onto one bin and their filtered phases change how they add. The dBc figures rise
because the filter cuts the fundamental too (14.9 dB at 19 kHz in 7k, 22.8 dB in 4k). The source split (`internal` /
`resampler` / `other` per position, and `abs_other_db`) is in every record: "other" (energy on no harmonic fold, e.g. a
settling transient) reads ≤ −184.1 dB re the unfiltered fundamental at every point (≤ −194.6 dB at 0.1–0.45 mA), so the
sweep measures aliasing, not the startup. For comparison, the research's figures (−102.2 dBc off, −106.9 dB abs with 7k
on, #102 values) sit close to the 48 kHz, 0.45 mA, 8 V row (−101.0 / −104.4).

CI (`test_lpf.py`) runs the absolute check at 10037 and 19037 Hz, 8 V, 0.45 mA, 48 and 44.1 kHz, in the 0.1 s window
after the same 1.25 s settle. It also requires "other" to be at least 20 dB under the off total, so a short settle that
measures the startup tail (as 0.2 s did: −81.6 dB at every position, which passed the +0.01 dB allowance) now fails.
Settled CI values, abs dB (off / 4k / 7k): 48 kHz 10037 −134.6 / −148.3 / −142.5, 19037 −101.0 / −106.7 / −104.4;
44.1 kHz 10037 −127.5 / −141.6 / −136.8, 19037 −95.2 / −108.2 / −102.4.

## Not in this PR (still open under #103)
This PR is the Python prototype step of #103; the issue stays open for the rest.
- **C++ port** with the 6AS6 C++ core (#104): the SVF in the core (no allocation, two state variables), realtime and
  denormal tests under the core's FTZ/DAZ guard, and a benchmark.
- **Check against the digitised Super 15 curve**: the S5 data is in the box lab, not the repo. The tests here check the
  discretisation against the analog prototype formula, which is not an independent check of the shape; F-a's fit to
  the curve (RMS 0.023 dB) is the only one so far.
- **Topology reference**: the chosen topology is an ideal decoupled 2-pole, so there is no circuit to put in ngspice
  yet. A topology reference is still owed if evidence for a real circuit (or a plate-loaded one) turns up.
- Smoothing of the other controls (#115); mode interaction and the "Off" roll-off (#111).
