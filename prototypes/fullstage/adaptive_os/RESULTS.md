# Adaptive oversampling investigation (#148)

**Neither prototype is suitable for the 16×/32× policy.** With the 4k filter at T 0.5 mA, nominal fixed
32× misses the stationary limit by 23.47 dB at 44.1 kHz and 17.08 dB at 48 kHz. Both published grid
shapes also fail at that endpoint at both rates. This rules out the policy even with an ideal handoff.
The transition measurements also show a cold-path transient in both methods. In the 44.1 kHz down
sweep with a 19,831 Hz stimulus, the peak residual relative to the always-warm mix was −6.7 dBFS
for the fade and −4.8 dBFS for the warm handoff.

The 0.35 mA crossover remains provisional. The experiment lives in a standalone probe and the
product's T range extends to 0.5 mA.

The standalone C++ probe compares two 16×/32× handoffs for the #146 decision. Both use the k140
resampler and the current 6AS6 solver. The probe requests 32× at or above 0.35 mA in T and 16× below it.
P1 requests 16× at either bias, and the probe's HQ control requests 32× in either mode.

## How the handoffs work

- **Crossfade.** Start the incoming stage at its current bias and mode's DC operating point. Run both stages for 15 ms while the incumbent remains audible. Then linearly crossfade the two latency-aligned outputs for 15 ms and suspend the old stage. A request reversal during the fade reverses the current blend.
- **Warm handoff.** Start the incoming stage the same way and keep it muted for 15 ms. Switch to its output in one sample and suspend the old stage. Both methods cancel an unfinished warm-up when the request returns to the incumbent.
- **References.** Run fixed 16× and fixed 32× continuously from the same initial conditions and with the same control timeline. An offline always-warm mix applies each method's exact weights to those two references. Subtracting it isolates the effect of starting the incoming path from rest. The one-sample weight-change term in that mix quantifies the rate handoff even with both paths warm.

The k140 16× FIR delay is 130 samples at 44.1 kHz and 82 at 48 kHz. The probe delays that path by one sample to match the 32× delays of 131 and 83. The nonlinear circuit models still have their own phase responses. Bias changes use prebuilt packs at 16-sample control intervals and keep the absolute capacitor histories. The probe's discrete P1 ↔ T tests preserve histories for capacitors with the same connections and reset the two reconnected histories. The fixed references make the same topology changes at the same sample. These switches are a repeatable stimulus for this experiment. Issue [#115](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/115) owns the production control and mode smoothing path.

The resampler's size is a compile-time parameter. The shipped core instantiations still use their original 16× storage limit and the probe uses its own 64× capacity for measurements. During a processing segment the probe uses prebuilt matrices, fixed storage and lock-free stage controls. The offline program handles timing, pack preparation and file I/O between processing calls.

## Measurements

**Stationary aliasing.** The [#146 metric 3](https://github.com/pmsorhaindo/ModellingAudioProcessors/blob/1c0a7f4b9d666768ffdd159d0543810f4141276a/prototypes/fullstage/dk6as6/inaudible.py) uses a one-second coherent, unwindowed DFT after 1.25 s of settling, 8 V peak input, output peak as full scale, and every non-harmonic bin from 20 Hz to 20 kHz. The limit is the lower of −100 dBFS and the Terhardt hearing threshold minus 10 dB, at the same assumed 105 dB SPL per full-scale sine as #146. A negative margin fails. Aliases coincident with a harmonic bin are indistinguishable from that harmonic. Fresh rows and pinned #146 29-tone records are identified separately. Inputs in the interpolator's 20 kHz–Nyquist transition band have known failures at all measured factors in [#147](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/147).

**Controls and transients.** Both host rates, nominal RSD-1, 8 V peak, 1037 Hz with the filter off and 19831 Hz (44.1 kHz) or 19987 Hz (48 kHz) with the filter at 4k. The near sweeps cover 0.31–0.39 mA in 440 ms, and the high-frequency full sweeps cover 0.30–0.50 mA in 500 ms. Automation crosses the threshold at 9 Hz and includes a faster 65 Hz reversal test. Mode changes use 0.38 mA and quality changes use 0.349 mA. The transient comparisons share the larger peak of their two fixed-rate references.

- **Click diagnostic.** Peak first difference of the adaptive-minus-always-warm residual near each event. The raw measurements also include the one-sample change from the ideal mix itself, which quantifies the handoff with both fixed paths warm.
- **Level and phase.** Change in the fitted carrier level and phase from immediately before to immediately after the first or last sample of a mix change. Each fit covers about 2 ms or three carrier cycles, whichever is longer. The reference's own change over the same interval is subtracted.
- **Transient spectrum.** Sliding 8 ms Hann windows with a 1 ms hop, excluding the carrier and harmonic main lobes. The tables give the smallest margin of the residual relative to the same hearing curve. The recorded folds also track 3199/8316 Hz at 44.1 kHz and 2999/8494 Hz at 48 kHz, identified in #146's hot-T records for the two high-tone stimuli. A moving bias, cold capacitor state, finite window and mix modulation produce off-harmonic energy as well. The short-window figures screen for excess energy and cannot establish alias-only levels or certify transient inaudibility.
- **Zipper diagnostic.** RMS carrier-envelope modulation above 100 Hz in the adaptive-minus-reference signal over the driven sweep or automation take. The demodulator uses 2 ms Hann windows and a 0.5 ms hop. This is a comparative measurement. The issue specifies no separate click, step or zipper limit.

The two sensitivity shapes are the published RSD-2 and EHX-1 12AX7 fits, scaled by the same perveance ratio as in [#131](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/131) and read from the repository's grid model. All three grid-current laws are estimated, unvalidated for 6AS6.

## Results

### Stationary margins

P1 at 16× has a minimum margin of +17.95 dB at 44.1 kHz and +23.94 dB at 48 kHz in the
pinned #146 grid across 0.1 / 0.25 / 0.45 / 0.6 mA and all three post-filter positions.

T, nominal RSD-1. Each cell gives the worst measured margin in dB across the available tones and filter
positions. At 0.349 and 0.351 mA, the 4k position ran 29 tones at 44.1 kHz and 10 at 48 kHz,
and off / 7k ran two probes. The 0.375, 0.4 and 0.5 mA rows combine the full #146 filter-off sweep and fresh
hot-T tone probes with the filter enabled.

| T bias (mA) | Factor | 44.1 kHz | 48 kHz |
|---:|---:|---:|---:|
| 0.1 | 16× | +28.64 | +32.75 |
| 0.25 | 16× | +24.38 | +32.37 |
| 0.3 | 16× | +22.85 | +32.14 |
| 0.349 | 16× | +8.79 | +21.41 |
| 0.35 | 32× | +23.55 | +32.47 |
| 0.351 | 32× | +23.79 | +31.96 |
| 0.375 | 32× | +27.86 | +28.62 |
| 0.4 | 32× | +7.74 | +15.44 |
| 0.45 | 32× | -12.79 | -7.28 |
| 0.5 | 32× | -23.47 | -17.08 |
| 0.5 | 64× | +20.03 | +20.04 |

Published shape sensitivity at the 4k filter position, dB margin on the measured probes. The shape
sweep uses 2–5 tones per cell. Positive rows cover those tones. RSD-2 already fails at 0.4 mA
and 44.1 kHz. At 0.5 mA, 64× passed the selected stationary probes with a minimum margin of
16.0 dB across the three shapes. The switching measurements use 16× and 32×.

| T bias (mA) | Factor | RSD-2 44.1 | RSD-2 48 | EHX-1 44.1 | EHX-1 48 |
|---:|---:|---:|---:|---:|---:|
| 0.3 | 16× | +22.85 | +32.15 | +22.85 | +32.15 |
| 0.325 | 16× | +22.00 | +31.96 | +22.01 | +31.99 |
| 0.349 | 16× | +6.00 | +8.06 | +5.49 | +18.65 |
| 0.35 | 16× | +4.08 | +5.73 | +4.25 | +23.20 |
| 0.351 | 32× | +23.04 | +35.19 | +23.70 | +33.66 |
| 0.375 | 16× | -27.16 | -22.12 | -25.20 | -14.66 |
| 0.375 | 32× | +14.90 | +22.50 | +22.35 | +27.20 |
| 0.4 | 32× | -4.01 | +1.97 | +3.03 | +10.19 |
| 0.45 | 32× | -19.28 | -14.26 | -15.28 | -10.39 |
| 0.5 | 32× | -29.00 | -25.97 | -25.93 | -20.74 |
| 0.5 | 64× | +16.00 | +20.27 | +20.05 | +20.05 |

- With RSD-1 at 44.1 kHz, 19831 Hz at 0.5 mA and the 4k filter produced a 3199 Hz alias at -96.45 dBFS (-23.47 dB margin).
- With RSD-2 at 44.1 kHz, 19831 Hz at 0.5 mA and the 4k filter produced a 3199 Hz alias at -90.92 dBFS (-29.00 dB margin).
- With EHX-1 at 44.1 kHz, 19831 Hz at 0.5 mA and the 4k filter produced a 3199 Hz alias at -93.98 dBFS (-25.93 dB margin).
- With RSD-1 at 48 kHz, 19987 Hz at 0.5 mA and the 4k filter produced a 2999 Hz alias at -102.48 dBFS (-17.08 dB margin).
- With RSD-2 at 48 kHz, 19987 Hz at 0.5 mA and the 4k filter produced a 2999 Hz alias at -93.59 dBFS (-25.97 dB margin).
- With EHX-1 at 48 kHz, 19987 Hz at 0.5 mA and the 4k filter produced a 2999 Hz alias at -98.83 dBFS (-20.74 dB margin).

### Switch measurements

Worst measured values across the host rates and driven stimuli. Each pair or triplet follows the
reference order in the header. Values from different columns can occur on different events. The
automation zipper result uses the entire take and is common to both directions. A dash indicates
a metric outside the scope of that stimulus.

| Control | Direction | Method | First difference (dBFS), 16 / 32 / warm | Level step (dB), 16 / 32 | Phase step (°), 16 / 32 | Off-harmonic margin (dB), 16 / 32 / warm | Zipper (dBFS), 16 / 32 |
|---|---|---|---:|---:|---:|---:|---:|
| Near sweep | up | Fade | -54.1 / -54.8 / -65.1 | 0.0011 / 0.0012 | 0.009 / 0.009 | -72.7 / -72.7 / -72.7 | -83.1 / -83.1 |
| Near sweep | up | Warm handoff | -27.0 / -27.0 / -27.0 | 0.0139 / 0.0140 | 0.031 / 0.031 | -73.6 / -73.6 / -73.6 | -78.0 / -78.1 |
| Near sweep | down | Fade | -41.5 / -42.6 / -41.5 | 0.0135 / 0.0135 | 0.075 / 0.075 | -93.2 / -93.2 / -93.2 | -69.1 / -69.1 |
| Near sweep | down | Warm handoff | -4.9 / -4.9 / -4.9 | 0.0705 / 0.0705 | 0.015 / 0.015 | -95.5 / -95.5 / -95.5 | -60.8 / -60.8 |
| Full sweep | up | Fade | -60.9 / -54.6 / -59.7 | 0.0065 / 0.0065 | 0.054 / 0.054 | -87.4 / -87.4 / -87.4 | -107.9 / -116.2 |
| Full sweep | up | Warm handoff | -11.6 / -11.6 / -11.6 | 0.0048 / 0.0048 | 0.027 / 0.028 | -88.9 / -88.9 / -88.9 | -77.3 / -77.3 |
| Full sweep | down | Fade | -38.3 / -39.1 / -38.3 | 0.0094 / 0.0092 | 0.051 / 0.051 | -90.7 / -90.7 / -90.7 | -93.4 / -93.6 |
| Full sweep | down | Warm handoff | -6.7 / -6.7 / -6.7 | 0.1313 / 0.1313 | 0.050 / 0.050 | -93.6 / -93.6 / -93.6 | -66.3 / -66.3 |
| Automation | up | Fade | -41.3 / -19.6 / -41.4 | 0.0046 / 0.0047 | 0.038 / 0.038 | -83.9 / -83.9 / -83.9 | -67.4 / -67.3 |
| Automation | up | Warm handoff | -15.7 / -15.7 / -15.7 | 0.0087 / 0.0090 | 0.034 / 0.034 | -84.8 / -84.8 / -84.8 | -56.9 / -56.9 |
| Automation | down | Fade | -45.6 / -18.8 / -45.6 | 0.0165 / 0.0164 | 0.076 / 0.076 | -87.2 / -87.2 / -87.2 | -67.4 / -67.3 |
| Automation | down | Warm handoff | -11.1 / -11.1 / -11.1 | 0.0769 / 0.0767 | 0.035 / 0.035 | -89.7 / -89.7 / -89.7 | -56.9 / -56.9 |
| Mode | up | Fade | -42.6 / -9.8 / -42.6 | 0.0572 / 0.0572 | 0.446 / 0.446 | -87.6 / -87.6 / -87.6 | — |
| Mode | up | Warm handoff | -11.4 / -9.8 / -11.4 | 0.1245 / 0.1245 | 0.046 / 0.046 | -89.2 / -89.2 / -89.2 | — |
| Mode | down | Fade | -7.5 / -36.3 / -36.3 | 0.0549 / 0.0549 | 0.603 / 0.603 | -90.2 / -90.2 / -90.2 | — |
| Mode | down | Warm handoff | -7.5 / -8.1 / -8.1 | 0.3872 / 0.3872 | 0.091 / 0.091 | -91.9 / -91.9 / -92.0 | — |
| Quality | up | Fade | -62.7 / -62.7 / -62.7 | 0.0063 / 0.0063 | 0.054 / 0.054 | -71.9 / -71.9 / -71.9 | — |
| Quality | up | Warm handoff | -26.6 / -26.6 / -26.6 | 0.0205 / 0.0205 | 0.010 / 0.010 | -73.8 / -73.8 / -73.8 | — |
| Quality | down | Fade | -62.7 / -62.7 / -62.7 | 0.0063 / 0.0063 | 0.054 / 0.054 | -71.9 / -71.9 / -71.9 | — |
| Quality | down | Warm handoff | -26.6 / -26.6 / -26.6 | 0.0205 / 0.0205 | 0.010 / 0.010 | -73.8 / -73.8 / -73.8 | — |

Tracked fold-frequency peaks during the near-sweep handoffs, dBFS. The adaptive columns
subtract the always-warm mix. The fixed column subtracts the continuous 32× output from the
continuous 16× output. Each pair follows the frequency order shown. These windows include
any broadband cold-start transient at the probe frequency.

| Host | Direction | Frequencies (Hz) | Fixed difference | Fade | Warm handoff | Limits |
|---|---|---|---:|---:|---:|---:|
| 44.1 kHz | up | 3199 / 8316 | -90.1 / -88.6 | -107.5 / -123.3 | -59.5 / -67.4 | -119.9 / -109.5 |
| 44.1 kHz | down | 3199 / 8316 | -88.4 / -87.4 | -86.3 / -102.0 | -36.9 / -44.8 | -119.9 / -109.5 |
| 48 kHz | up | 2999 / 8494 | -79.9 / -118.3 | -106.2 / -124.6 | -58.5 / -67.1 | -119.6 / -109.1 |
| 48 kHz | down | 2999 / 8494 | -78.9 / -144.7 | -85.1 / -102.5 | -36.4 / -45.0 | -119.6 / -109.1 |

### CPU

Apple M1, Apple Clang 17 at `-O2`, one mono channel, max block 64 samples, timing the calling
thread around each processing segment. Median of three runs of the same 1037 Hz automation
timeline, including a 100 ms preroll. Times include dispatch to the stage and mixing during overlap,
and exclude pack preparation and file I/O. The host was under load. Core percentage is the processing time per sample
divided by the host sample period.

| Host | Method | Whole (µs/sample) | One core | × fixed 16 / fixed 32 | Overlap (µs/sample) | One core in overlap |
|---|---|---:|---:|---:|---:|---:|
| 44.1 kHz | Fixed 16× | 22.7 | 100% | 1.00 / 0.49 | — | — |
| 44.1 kHz | Fixed 32× | 46.1 | 203% | 2.03 / 1.00 | — | — |
| 44.1 kHz | Fade | 40.1 | 177% | 1.77 / 0.87 | 70.0 | 309% |
| 44.1 kHz | Warm handoff | 35.3 | 156% | 1.56 / 0.77 | 70.0 | 309% |
| 48 kHz | Fixed 16× | 23.4 | 112% | 1.00 / 0.51 | — | — |
| 48 kHz | Fixed 32× | 45.5 | 218% | 1.95 / 1.00 | — | — |
| 48 kHz | Fade | 40.6 | 195% | 1.74 / 0.89 | 71.4 | 343% |
| 48 kHz | Warm handoff | 35.2 | 169% | 1.51 / 0.77 | 68.3 | 328% |

- At 44.1 kHz, dual processing covers 31.0% of the take for the fade and 18.3% for the warm handoff.
- At 48 kHz, dual processing covers 30.9% of the take for the fade and 18.3% for the warm handoff.

### Records and verification

The stationary screen has 232 fresh points and 128 pinned #146 summary rows. The switch study
has 24 driven cases with a WAV for each of four paths. The fixed 16× path above the validated
range is a diagnostic comparison. The static probe matches the selected continuous rate sample-for-sample
on the two sides of the threshold. Automation has 22 requests per take, including the fast reversals,
and each method begins a mix or handoff on eight of them.

There were no solver failures, out-of-law samples or state recoveries in the fresh measurements. The
transient and CPU runs recorded zero allocations with the repository's allocation hooks. The local
C++ suites passed 53 tests with 3 skips. A separate ASan/UBSan smoke run covered the automation and
a 64× stationary render. Exact-zero residuals in the records can appear at the numerical logarithm floor
of −6000 dBFS. The assessment here is numerical. The WAVs are available for audition.

[Stationary records](steady.json) · [Transient records](transient.json) · [CPU records](cpu.json)

## Reproduction

From the worktree root with the fullstage dependencies installed:

```sh
git fetch origin 1c0a7f4b9d666768ffdd159d0543810f4141276a
cd prototypes/fullstage
python -m adaptive_os.run steady --workers 4
python -m adaptive_os.run transient
python -m adaptive_os.run bench
python -m adaptive_os.run verify
python -m pytest -q adaptive_os/test_transition.py
```

Use `--output` to choose a results directory and `--resume` to reuse completed sweep or render rows from an interrupted run of the same probe. The default writes to the worktree's excluded `.dispatch/issue148` directory. The transient command writes four mono float WAVs per case with the same gain within a case and about 1 dB headroom at the largest peak. Filename components are sample rate, control scenario, stimulus tone and method. Each WAV retains 820 ms starting at the end of the preroll.
