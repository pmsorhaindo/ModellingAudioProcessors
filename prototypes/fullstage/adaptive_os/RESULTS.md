# Adaptive oversampling investigation (#148)

The standalone C++ probe compares two 16×/32× handoffs for the #146 decision. Both use the k140 resampler and the current 6AS6 solver. The probe requests 32× at or above 0.35 mA in T and 16× below it. P1 requests 16× at either bias, and the probe's HQ control requests 32× in either mode. The 0.35 mA crossover is an experimental setting. T remains available through 0.5 mA in the existing stage.

## How the handoffs work

- **Crossfade.** Start the incoming stage at its current bias and mode's DC operating point. Run both stages for 15 ms while the incumbent remains audible. Then linearly crossfade the two latency-aligned outputs for 15 ms and suspend the old stage. A request reversal during the fade reverses the current blend.
- **Warm handoff.** Start the incoming stage the same way and keep it muted for 15 ms. Switch to its output in one sample and suspend the old stage. Both methods cancel an unfinished warm-up when the request returns to the incumbent.
- **References.** Run fixed 16× and fixed 32× continuously from the same initial conditions and with the same control timeline. An offline always-warm mix applies each method's exact weights to those two references. Subtracting it isolates the effect of starting the incoming path from rest. The one-sample weight-change term in that mix quantifies the rate handoff even with both paths warm.

The experiment pads to the higher FIR group delay, while the two nonlinear circuit models still have their own phase responses. Bias changes use prebuilt packs at 16-sample control intervals and keep the absolute capacitor histories. The probe's discrete P1 ↔ T tests preserve histories for capacitors with the same connections and reset the two reconnected histories. The fixed references make the same topology changes at the same sample. These switches are a repeatable stimulus for this experiment. Issue [#115](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/115) owns the production control and mode smoothing path.

The resampler's size is a compile-time parameter. The shipped core instantiations still use their original 16× storage limit and the probe uses its own 64× capacity for measurements. During a processing segment the probe uses prebuilt matrices, fixed storage and lock-free stage controls. The offline program handles timing, pack preparation and file I/O between processing calls.

## Measurements

**Stationary aliasing.** The [#146 metric 3](https://github.com/pmsorhaindo/ModellingAudioProcessors/blob/1c0a7f4b9d666768ffdd159d0543810f4141276a/prototypes/fullstage/dk6as6/inaudible.py) uses a one-second coherent, unwindowed DFT after 1.25 s of settling, 8 V peak input, output peak as full scale, and every non-harmonic bin from 20 Hz to 20 kHz. The limit is the lower of −100 dBFS and the Terhardt hearing threshold minus 10 dB, at the same assumed 105 dB SPL per full-scale sine as #146. A negative margin fails. Aliases coincident with a harmonic bin are indistinguishable from that harmonic. Fresh rows and pinned #146 29-tone records are identified separately. Inputs in the interpolator's 20 kHz–Nyquist transition band have known failures at all measured factors in [#147](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/147).

**Controls and transients.** Both host rates, nominal RSD-1, 8 V peak, 1037 Hz with the filter off and 19831 Hz (44.1 kHz) or 19987 Hz (48 kHz) with the filter at 4k. The near sweeps cover 0.31–0.39 mA in 440 ms, and the high-frequency full sweeps cover 0.30–0.50 mA in 500 ms. Automation crosses the threshold at 9 Hz and includes a faster 65 Hz reversal test. Mode changes use 0.38 mA and quality changes use 0.349 mA. The transient comparisons share the larger peak of their two fixed-rate references.

- **Click diagnostic.** Peak first difference of the adaptive-minus-always-warm residual near each event. The raw measurements also include the one-sample change from the ideal mix itself, which quantifies the handoff with both fixed paths warm.
- **Level and phase.** Change in the fitted carrier level and phase from immediately before to immediately after the first or last sample of a mix change. Each fit covers at least 2 ms and at least three carrier cycles. The reference's own change over the same interval is subtracted.
- **Transient spectrum.** Sliding 8 ms Hann windows with a 1 ms hop, excluding the carrier and harmonic main lobes. The tables give the smallest margin of the residual relative to the same hearing curve. The recorded folds also track 3199/8316 Hz at 44.1 kHz and 2999/8494 Hz at 48 kHz, identified in #146's hot-T records for the two high-tone stimuli. A moving bias, cold capacitor state, finite window and mix modulation produce off-harmonic energy as well. The short-window figures screen for excess energy and cannot establish alias-only levels or certify transient inaudibility.
- **Zipper diagnostic.** RMS carrier-envelope modulation above 100 Hz in the adaptive-minus-reference signal over the driven sweep or automation take. The demodulator uses 2 ms Hann windows and a 0.5 ms hop. This is a comparative measurement. The issue specifies no separate click, step or zipper limit.

The two sensitivity shapes are the published RSD-2 and EHX-1 12AX7 fits, scaled by the same perveance ratio as in [#131](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/131) and read from the repository's grid model. All three grid-current laws are estimated, unvalidated for 6AS6.

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
