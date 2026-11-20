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

Float32 vs float64 (same float32 input; error energy over output energy) [≤ −125 dBc]:

| internal rate | position | 1 kHz sine | white noise |
|---|---|---|---|
| 8 × 44.1 kHz | 4k | −133.3 dBc | −130.1 dBc |
| 8 × 44.1 kHz | 7k | −134.3 dBc | −132.9 dBc |
| 8 × 48 kHz | 4k | −132.0 dBc | −129.2 dBc |
| 8 × 48 kHz | 7k | −133.7 dBc | −132.6 dBc |

Modulation: f0 changed every sample (random over 20 Hz–0.45 fs, and alternating 4k / 7k), Q 0.4277 and 5, both pre-warp
choices: finite and bounded, and below 1e-9 over the last 2.6 ms of 52 ms of silence.

### Aliasing, filter on vs off (`lpf_aliasing.json`)
The `dk/aliasing` metric and full sweep (1037…19037 Hz, 1 s window after 0.25 s), standard 8×, FIR, on **main's 6AS6
stage values** (the #26 example values; the #102 values are PR #118). "dBc" is relative to each output's own
fundamental (the repo metric); "abs" is the same alias energy relative to the **unfiltered** fundamental. Worst over the
sweep, with its tone:

| rate, drive | off | 4k: dBc / abs | 7k: dBc / abs | largest change on − off, abs, any tone |
|---|---|---|---|---|
| 48 kHz, 2 V | −110.4 (19037) | −97.0 (16037) / −117.2 (16037) | −102.6 (19037) / −117.0 (16037) | −0.01 dB |
| 48 kHz, 8 V | −59.7 (19037) | −42.3 / −65.2 (19037) | −47.9 / −62.8 (19037) | −2.54 dB |
| 44.1 kHz, 2 V | −108.0 (19037) | −98.7 (19037) / −120.3 (15037) | −101.4 / −116.3 (19037) | −0.01 dB |
| 44.1 kHz, 8 V | −52.6 (19037) | −42.4 / −65.3 (19037) | −44.7 / −59.6 (19037) | −2.20 dB |

The filter never raises the alias energy on any tone (it is linear with |H| ≤ 1, so each alias line can only drop); the
dBc figures rise only because the filter cuts the fundamental too (14.9 dB at 19 kHz in 7k, 22.8 dB in 4k). The 8 V
figures are main's stage at 58 % THD; the research's −102 dBc (off) / −106.9 dB abs (7k on) are for the #102 values.
CI runs the absolute check at 10037 and 19037 Hz, 8 V, 48 and 44.1 kHz with the guard's short buffers.

## Not in this PR
- **C++ port**: the 6AS6 C++ core is #104; the SVF (no allocation, two state variables, denormal-safe under the core's
  FTZ/DAZ guard) goes in with it.
- **ngspice reference / digitised-curve check**: the chosen topology is an ideal decoupled 2-pole, so the analog
  prototype formula is the reference (tested). The digitised S5 data is in the box lab, not the repo; F-a's fit is the
  check.
- Smoothing of the other controls (#115); mode interaction and the "Off" roll-off (#111).
