# Adaptive oversampling investigation (#148)

**The 15 ms warm-up exposed the incoming circuit's settling transient.** With 16× and 32× already running
on the same input and control history, the largest recorded mix-edge step was −114.5 dBFS for the fade
and −58.1 dBFS for the hard handoff. Starting the incoming lane from DC rest increased the edge
residuals to −39.0 and −4.9 dBFS respectively. The 44.1 kHz rising sweep at 1037 Hz has a low-frequency
tail with a time constant of roughly 110–120 ms. The 15 ms warm-up is much shorter than that.

Fixed 32× independently fails the stationary rule at T 0.5 mA. With the 4k filter, the nominal fit
misses by 23.47 dB at 44.1 kHz and 17.08 dB at 48 kHz. Both published grid shapes also fail at that
endpoint. The 16×/32× policy therefore fails the stationary rule even with both lanes fully warm.

The 16×/64× policy clears the stationary probes through 0.5 mA. A 750 ms warm-up followed by
the fade cuts the 16×/64× held-sweep residual by about 65 dB. Its low-frequency tail still exceeds
the short-window diagnostic limit, and the method completes no transfer during the fast automation
test. The experiments support a negative result for deployment. The 0.35 mA crossover remains provisional. The experiment lives in a standalone probe and the
product's T range extends to 0.5 mA.

The standalone C++ probe uses the k140 resampler and the current 6AS6 solver. It requests the higher
factor at or above 0.35 mA in T and 16× below it. P1 requests 16× at either bias, and the probe's HQ
control requests the higher factor in either mode.

## How the handoffs work

- **Crossfade.** Start the incoming stage at its current bias and mode's DC operating point. Run both stages for 15 ms while the incumbent remains audible. Then linearly crossfade the two latency-aligned outputs for 15 ms and suspend the old stage. A request reversal during the fade reverses the current blend.
- **Warm handoff.** Start the incoming stage the same way and keep it muted for 15 ms. Switch to its output in one sample and suspend the old stage. Both methods cancel an unfinished warm-up when the request returns to the incumbent.
- **Long warm-up crossfade.** Keep the incoming stage muted for 750 ms, then apply the same 15 ms fade. This waits about six of the measured low-frequency time constants and requires the request to persist. Bias and mode apply to both lanes throughout, including the audible incumbent.
- **References.** Run fixed 16× and fixed 32× continuously from the same initial conditions and with the same control timeline. An offline always-warm mix applies each method's exact weights to those two references. Subtracting it isolates the effect of starting the incoming path from rest. The one-sample weight-change term in that mix quantifies the rate handoff even with both paths warm.

The k140 16× FIR delay is 130 samples at 44.1 kHz and 82 at 48 kHz. The probe delays that path by one sample to match the 32× delays of 131 and 83, or by two to match the 64× delays of 132 and 84. The nonlinear circuit models still have their own phase responses. Bias changes use prebuilt packs at 16-sample control intervals and keep the absolute capacitor histories. The probe's discrete P1 ↔ T tests preserve histories for capacitors with the same connections and reset the two reconnected histories. The fixed references make the same topology changes at the same sample. These switches are a repeatable stimulus for this experiment. Issue [#115](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/115) owns the production control and mode smoothing path.

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

The 4k filter strongly attenuates the approximately 19.8–20 kHz carrier while passing much more of the
approximately 3 kHz alias. With output peak as full scale, this raises the alias in dBFS relative to the
filtered signal. The pinned #146 records already show 32× failing at 0.45 mA with the 4k filter on
both rates and at 0.5 mA with the filter off at 44.1 kHz. At 0.5 mA filter off, the pinned margins
are −3.63 dB and +3.21 dB at 44.1 and 48 kHz. The fresh 4k endpoint measurements give −23.47 and
−17.08 dB.

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
16.0 dB across the three shapes.

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

### Switch measurements with the 15 ms warm-up

The one-sample step from changing the mix weight with both paths already warm is separate from the
first difference of the cold-start residual. These are maxima over the first and last changing mix
samples across the 24 short-warm cases. They can occur on different events.

| Method | Already-warm mix edge (dBFS) | Cold-start residual edge (dBFS) |
|---|---:|---:|
| Fade | −114.5 | −39.0 |
| Warm handoff | −58.1 | −4.9 |

The 44.1 kHz HQ switch at T 0.349 mA and 1037 Hz is a control for this attribution. Its two continuous
reference WAVs differ by about −143 dB relative to their peak, while the handoff residual reaches
−26.4 dBFS. In the 1037 Hz rising sweep, low-pass filtering the handoff-minus-fixed-32× WAV at 300 Hz
with a zero-phase fourth-order Butterworth filter and fitting 50 ms RMS windows centered at
0.40–0.75 s gives a slope of approximately −80 dB/s and a time constant of 109 ms. The reviewer
estimated about 120 ms from the same settling tail.

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

### The 16×/64× stationary screen

At the 4k position, the new 64× probes use 19,831 Hz at 44.1 kHz and 19,987 Hz at 48 kHz.
These are the nominal 32× endpoint stress tones. The 0.5 mA row uses the prior multi-tone screen.
Cells show the smallest margin across RSD-1, RSD-2 and EHX-1. The low-side 16× results remain
in the tables above. The positive margins apply to the sampled tones and biases.

| T bias (mA) | 64× at 44.1 kHz (dB) | 64× at 48 kHz (dB) |
|---:|---:|---:|
| 0.35 | +26.37 | +35.83 |
| 0.351 | +26.61 | +35.13 |
| 0.4 | +31.80 | +27.21 |
| 0.45 | +24.47 | +24.35 |
| 0.5 | +16.00 | +20.04 |

### Causal long warm-up and the 16×/64× handoff

The round trips ramp between 0.349 and 0.351 mA in 32 ms and hold each side long enough for the
750 ms warm-up and the 15 ms fade. That keeps the outgoing 16× lane near the crossover while it
remains audible. All paths receive the same control timing. Each event window follows the actual
mix edge, so the late handoff is included. The 16×/32× case uses 1037 Hz with the filter off.
The 16×/64× tests use the high tones and the 4k filter at both host rates.

The first two level columns are relative to the always-warm mix. **Mix step** is the largest
rate-only, one-sample term across the entire changing weight. Level and phase are maxima relative
to the always-warm mix at the edges. The spectrum column is the smallest off-harmonic residual
margin by the short-window diagnostic. It includes cold-circuit transients and sidebands.

| Host / factors | Direction | Method | Peak residual (dBFS) | First difference (dBFS) | Mix step (dBFS) | Level / phase (dB / °) | Spectrum margin (dB) |
|---|---|---|---:|---:|---:|---:|---:|
| 44.1 kHz · 16/32× | up | 15 ms fade | -27.9 | -58.6 | -136.3 | 0.0050 / 0.039 | -71.6 |
| 44.1 kHz · 16/32× | up | 15 ms handoff | -26.4 | -27.1 | -119.9 | 0.0216 / 0.008 | -73.6 |
| 44.1 kHz · 16/32× | up | 750 ms fade | -93.5 | -133.3 | -204.3 | 0.0000 / 0.000 | -6.3 |
| 44.1 kHz · 16/32× | down | 15 ms fade | -27.1 | -57.7 | -137.0 | 0.0046 / 0.049 | -72.5 |
| 44.1 kHz · 16/32× | down | 15 ms handoff | -25.6 | -26.4 | -120.2 | 0.0270 / 0.010 | -74.5 |
| 44.1 kHz · 16/32× | down | 750 ms fade | -95.2 | -134.3 | -204.1 | 0.0000 / 0.000 | -4.6 |
| 44.1 kHz · 16/64× | up | 15 ms fade | -5.3 | -47.7 | -110.7 | 0.0005 / 0.009 | -94.7 |
| 44.1 kHz · 16/64× | up | 15 ms handoff | -3.8 | -3.8 | -56.1 | 0.0196 / 0.015 | -96.6 |
| 44.1 kHz · 16/64× | up | 750 ms fade | -69.6 | -114.9 | -112.2 | 0.0000 / 0.000 | -30.3 |
| 44.1 kHz · 16/64× | down | 15 ms fade | -4.4 | -45.7 | -110.7 | 0.0006 / 0.007 | -95.6 |
| 44.1 kHz · 16/64× | down | 15 ms handoff | -2.9 | -2.9 | -79.7 | 0.0255 / 0.018 | -97.6 |
| 44.1 kHz · 16/64× | down | 750 ms fade | -71.1 | -115.7 | -112.3 | 0.0000 / 0.000 | -28.8 |
| 48 kHz · 16/64× | up | 15 ms fade | -5.2 | -48.0 | -112.2 | 0.0069 / 0.050 | -94.7 |
| 48 kHz · 16/64× | up | 15 ms handoff | -3.8 | -3.8 | -73.9 | 0.0186 / 0.022 | -96.7 |
| 48 kHz · 16/64× | up | 750 ms fade | -69.6 | -115.5 | -114.1 | 0.0000 / 0.000 | -30.4 |
| 48 kHz · 16/64× | down | 15 ms fade | -4.3 | -46.0 | -112.1 | 0.0074 / 0.034 | -95.6 |
| 48 kHz · 16/64× | down | 15 ms handoff | -2.8 | -2.8 | -59.8 | 0.0262 / 0.012 | -97.6 |
| 48 kHz · 16/64× | down | 750 ms fade | -71.1 | -116.3 | -114.1 | 0.0000 / 0.000 | -28.8 |

With the 750 ms fade at 16×/64×, all four high-tone edge windows put the worst residual near
125 Hz. At the tracked fold frequencies, the largest extra component relative to the
always-warm mix is −148.4 dBFS. The low-frequency residual still drives the negative
short-window margins, and the fold probes cover only the frequencies listed above.

Carrier-envelope zipper diagnostic over each take, dBFS relative to the always-warm mix. The
automation row uses the same 9 Hz crossings and 65 Hz reversals as the short-warm experiment.

| Host / factors | Control | 15 ms fade | 15 ms handoff | 750 ms fade |
|---|---|---:|---:|---:|
| 44.1 kHz · 16/32× | roundtrip | -73.6 | -64.9 | -139.2 |
| 44.1 kHz · 16/64× | roundtrip | -105.4 | -67.1 | -175.6 |
| 48 kHz · 16/64× | roundtrip | -105.7 | -67.6 | -176.8 |
| 48 kHz · 16/64× | automation | -97.8 | -67.2 | zero |

The 750 ms method began no audible transfer on the 22 fast automation requests.
Its output matched the fixed 16× lane exactly. A causal 750 ms wait leaves the low-rate lane
audible at the new bias, which can cross into the measured 16× failure region. A control hold
near the crossover therefore demonstrates settling but supplies no solution for arbitrary
automation. The transition diagnostics do not support a production switch. A transferable
running state would require handling capacitor histories and rate-specific filter histories.

### CPU on the Apple M1

The repeated 16×/32× automation and the new 16×/64× tests use Apple Clang 17 at `-O3`,
one mono lane, max block 64 and thread CPU time around processing segments. Preparation and
file I/O are outside the timed regions. Compare within-test ratios under the host load in
this run. The original `-O2` automation data remain in `cpu.json`.

| Host | Method | Whole (µs/sample) | × fixed 16× | Dual only (µs/sample) |
|---|---|---:|---:|---:|
| 44.1 kHz | Fixed 16× | 22.9 | 1.00 | — |
| 44.1 kHz | Fixed 32× | 43.9 | 1.91 | — |
| 44.1 kHz | 15 ms fade | 39.0 | 1.70 | 67.9 |
| 44.1 kHz | 15 ms handoff | 35.3 | 1.54 | 68.7 |
| 48 kHz | Fixed 16× | 23.0 | 1.00 | — |
| 48 kHz | Fixed 32× | 45.4 | 1.98 | — |
| 48 kHz | 15 ms fade | 39.7 | 1.73 | 70.3 |
| 48 kHz | 15 ms handoff | 35.1 | 1.53 | 68.3 |

The following handoff timings include the common preroll and use one pass per take.
Each adaptive cell shows whole / dual-only thread microseconds per sample.

| Host / factors | Control | Fixed 16× | Fixed high | 15 ms fade | 15 ms handoff | 750 ms fade |
|---|---|---:|---:|---:|---:|---:|
| 44.1 kHz · 16/32× | roundtrip | 22.1 | 44.6 | 29.3 / 67.0 | 29.0 / 65.9 | 44.9 / 68.4 |
| 44.1 kHz · 16/64× | roundtrip | 34.5 | 109.9 | 60.1 / 151.5 | 59.1 / 150.0 | 92.8 / 149.3 |
| 48 kHz · 16/64× | roundtrip | 33.3 | 106.3 | 58.0 / 143.1 | 57.5 / 140.8 | 89.9 / 145.0 |
| 48 kHz · 16/64× | automation | 32.9 | 107.8 | 52.7 / 141.4 | 49.0 / 143.2 | 48.5 / 143.0 |

The fixed-rate cross-check uses the repository's 48 kHz T 0.5 mA, 8 V peak guitar phrase,
filter off and block 64, with `-O3` and three timed two-second passes. Wall throughput in
this run includes extensive descheduling. The CPU-on and performance-core cycle fractions
record that difference. The historical stock 16× run measured 13.4 µs/sample with 93.3%
CPU-on time and 96.4% of cycles on performance cores. Use that record for absolute time under
its host conditions. The continuous-tone probe and this run's scheduling both differ from it.
[Historical M1 benchmark](../cpp/bench_6as6_apple_m1_loaded.json)

| Build / factor | Wall µs/sample | CPU-on fraction | Performance-core cycle fraction |
|---|---:|---:|---:|
| stock 16× | 273.8 | 8.7% | 54.4% |
| extended 16× | 165.3 | 14.9% | 52.6% |
| extended 32× | 338.4 | 13.7% | 52.5% |
| extended 64× | 619.9 | 14.6% | 51.7% |

### Records and verification

The stationary screen has 232 fresh points and 128 pinned #146 summary rows. The switch study
has 24 original cases with four WAVs each and four additional cases with five WAVs each. The fixed 16× path above the validated
range is a diagnostic comparison. The static probe matches the selected continuous rate sample-for-sample
on the two sides of the threshold. Automation has 22 requests per take, including the fast reversals,
and each method begins a mix or handoff on eight of them.

There were no solver failures, out-of-law samples or state recoveries in the fresh measurements. The
transient and CPU runs recorded zero allocations with the repository's allocation hooks. The local
C++ suites passed 53 tests with 3 skips. A separate ASan/UBSan smoke run covered the automation and
a 64× stationary render in round one. The eight focused tests in round two include 32× and 64×
interpolator and round-trip impulse parity against the Python resampler at both rates, plus the timed
16×/64× hold and reversal tests. Exact-zero residuals in the records can appear at the numerical logarithm floor
of −6000 dBFS. The assessment here is numerical. The WAVs are available for audition.

[Stationary records](steady.json) · [Transient records](transient.json) · [Round-one CPU records](cpu.json) · [Review-round measurements](review-round-2.json)

## Reproduction

From the worktree root with the fullstage dependencies installed:

```sh
git fetch origin 1c0a7f4b9d666768ffdd159d0543810f4141276a
cd prototypes/fullstage
python -m adaptive_os.run steady --workers 4
python -m adaptive_os.run transient
python -m adaptive_os.run bench
python -m adaptive_os.run review-steady --workers 3
python -m adaptive_os.run review-transient --workers 3
python -m adaptive_os.run review-fixed-bench
python -m adaptive_os.run verify
python -m pytest -q adaptive_os/test_transition.py
```

Use `--output` to choose a results directory and `--resume` to reuse completed sweep or render rows from an interrupted run of the same probe. The default writes to the worktree's excluded `.dispatch/issue148` directory. The transient command writes four mono float WAVs per case with the same gain within a case and about 1 dB headroom at the largest peak. Filename components are sample rate, control scenario, stimulus tone and method. Each original WAV retains 820 ms starting at the end of the preroll. The review and bench commands
default to `.dispatch/issue148/round2`. The review WAVs retain the full 2.15 s held take or 0.85 s
automation take after the preroll.
