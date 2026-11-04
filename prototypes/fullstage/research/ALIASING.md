# Aliasing in the 12AX7 DK stage: diagnosis, experiments, recommendations (4 Nov 2026)

Scope: `prototypes/fullstage/dk` (not edited). All work is in the experiment folder (a scratch folder on the lab box, not in this repo):
`fast.py` (numba port of `dk.stage.Stage`, same equations, NR, pnjlim, line search; matches the Python stage
to 6e-12 V on a 4 V burst, `check_fast.py`), `resamplers.py`, `alias.py`, `diag.py`, `rebase.py`, `eval.py`,
`accuracy.py` (runs the unchanged `dk/validate.run` criteria against the same ngspice DZ reference), own venv `.venv`.

## Method
- **Aliasing metric:** same as `hf_fix/run_option.py`: non-harmonic energy 20 Hz–20 kHz relative to the fundamental
  (Hann, 1 s window, ±3 bins masked). Per the audit (FIR tail truncated at the signal end), the input now runs
  2 s + 20 ms and the window is samples [1 s, 2 s), so the tail is outside it. Re-baselining gave **identical**
  numbers (the Hann window already zeroed the tail), so the old aliasing figures stand.
- **Sweep:** 1037…19037 Hz (offset by 37 Hz so folded harmonics don't land on harmonic bins; 1 kHz-type
  frequencies that divide 48 kHz hide aliasing) at 2 V and 8 V.
- **Measurement floor:** about −110 to −113 dBc (2 V is at the floor at ≥ 8× everywhere).
- **Accuracy:** `validate.run` (op point, small signal 20 Hz–20 kHz incl. resampling chain, H2–H5 at 4 drives,
  4 V burst) vs ngspice DZ. **Caveat:** the validate harness still has the FIR-tail issue for FIR variants
  (H5 at 0.5 V bias); IIR variants are causal and untrimmed, so their harmonic numbers are not affected.
- **CPU:** numba, one core, best of 3, 1 s of audio at 48 kHz; split into solver core and resampling.
  Relative to the baseline (A). Python/numba timings are only indicative of C++ ratios.

## 1. Diagnosis — where the aliasing comes from

| 10 kHz / 8 V total alias (dBc) | 4× | 8× | 16× | 32× | 64× |
|---|---|---|---|---|---|
| baseline (Kaiser 64·os−1) | −44.2 | −59.7 | −75.0 | −92.5 | **−101.6** (ground truth, near floor) |
| grid current **off** (Gg = 0) | −50.9 | −64.8 | −78.9 | | |
| near-ideal resampler (512·os−1 taps, β 14, fc 21 kHz) | −44.2 | −59.7 | | | |

- **The resampling filters are not the limit.** A filter with ~8× more taps and much deeper stopband gives the
  same aliasing to 0.1 dB. All aliasing is folding *inside* the stage at the internal rate. Measured image
  rejection of the current filter is −97 dB and decimation stopband ≤ −97 dB above 28 kHz; transition band
  20–24 kHz leakage is irrelevant at these levels.
- **Grid-current onset is not the main source.** Removing grid current entirely only buys 4–7 dB. The bulk
  comes from the plate-current law going into cutoff / saturation along the load line at 8 V drive
  (the softplus corner, scale 1/C ≈ 0.29 V in Vak/µ+Vgk). So smoothing the grid onset (changing Cg) would buy
  at most ~5 dB while changing the physics — **not recommended, not done**.
- Slope ≈ 15–16 dB per doubling of the internal rate (≈ 2.6 dB more than a 1/f² spectrum would give), consistent
  with a smooth-but-steep nonlinearity whose harmonics decay steadily; 64× reaches the measurement floor.
- 2 V drive: below the floor at ≥ 8× at all sweep frequencies. Aliasing only matters at high drive, high
  frequency (8 V: −52 dBc at 19 kHz at 8×).

## 2. Experiments

| # | variant | alias 10k/8V | 7k/8V | sweep 8V worst (19 kHz) | small-signal max dev | worst H2–H5 dev | burst τ / traj | mean NR it (8 V) | rel. CPU (10k 8V / 1k 0.5V) | resampler MACs per base sample (up, down) |
|---|---|---|---|---|---|---|---|---|---|---|
| A | **8× Kaiser 511 (current default)** | −59.7 | −65.3 | −51.7 | 0.050 dB | 0.645 dB* | 0.04 % / 0.004 % | 3.06 | **1.00 / 1.00** | 511 / 511 |
| B | 8× polyphase IIR halfband cascade (12 + 4 + 4 coefs) | −59.7 | −65.3 | −51.7 | 0.052 | 0.137 | 5.2 % / 0.47 % | 3.06 | 0.72 / 0.66 | 36 / 36 |
| C | 8× linear-phase FIR halfband cascade (127 + 31 + 31) | −59.7 | −65.3 | −51.7 | 0.052 | 0.747* | 0.04 % / 0.003 % | 3.06 | 0.75 / 0.70 | 167 / 167 |
| D | B + NR tol 1e-6 V (was 1e-9) | −59.7 | −65.3 | −51.7 | 0.052 | 0.137 | same as B | 2.74 | 0.67 / 0.64 | 36 / 36 |
| E | D + linear-extrapolation predictor for the NR start | −59.7 | −65.3 | −51.7 | 0.052 | 0.137 | same as B | 2.45 | **0.55 / 0.50** | 36 / 36 |
| F | **16×** IIR HB + tol 1e-6 + predictor | **−74.8** | −81.1 | **−66.0** | 0.013 | 0.137 | 5.6 % / 0.49 % | 2.17 | **0.99 / 1.02** | 68 / 68 |
| G | 12× Kaiser (fractional factor) | −68.5 | −74.6 | −60.5 | 0.022 | 0.660* | 0.04 % | 2.98 | 1.58 / 1.75 | 767 / 767 |
| H | 16× Kaiser | −75.0 | −81.4 | −66.0 | 0.012 | 0.667* | 0.03 % | 2.92 | 2.32 / 2.69 | 1023 / 1023 |
| I | 4× IIR HB + small-cap prewarp (17.5 kHz) + tol + predictor | −44.1 | −51.5 | −32.7 | 0.055 | 0.142 | 4.6 % / 0.41 % | 2.55 | **0.31 / 0.25** | 20 / 20 |
| J | 4× Kaiser + prewarp (current "eco" option) | −44.1 | −51.5 | −32.7 | 0.055 | 0.563* | 0.04 % | 3.25 | 0.58 / 0.44 | 255 / 255 |

All variants pass every `validate` criterion vs ngspice DZ (op, small-signal 0.1 dB, harmonics 1 dB, burst 5/10/5 %).
\* FIR variants carry the audit's tail artefact in `validate` (H5 at 0.5 V); the IIR rows show what the
harmonics look like without it (≤ 0.14 dB). Full sweeps (2 V and 8 V, 9 frequencies) are in `eval.json`;
2 V is at the −110…−113 dBc floor for every ≥ 8× variant.

Findings:
1. **The filter is pure cost, no benefit, at its current size.** 511-tap (8×) / 1023-tap (16×) direct FIRs were
   30 % / 50 % of total CPU. Halfband cascades give identical aliasing and response at 7–15× fewer MACs.
2. **The solver tolerance (1e-9 V) is tighter than anything float32 C++ can use**, and loosening to 1e-6 V
   changes no reported metric at the shown precision. A first-order predictor for the NR start cuts mean
   iterations from ~3 to ~2 (quiet signals: exactly 2). Iterations drop further as oversampling rises.
3. Together, (1)+(2) make **16× cost the same as today's 8× (F vs A: 0.99×) with 15 dB less aliasing** (−75 dBc at
   10 kHz/8 V; −66 dBc worst at 19 kHz) and 4× smaller small-signal error (0.013 dB).
4. **IIR halfbands have a fidelity caveat:** their nonlinear phase near 20 kHz changes the burst-recovery τ by
   ~5 % (still inside the 10 % criterion, but from 0.04 %). The linear-phase FIR halfband (C) keeps 0.04 % at
   ~1/3 of the baseline filter cost. So: IIR for the Octatrack / eco path; FIR halfband where phase-exact
   matching to the reference matters (or as a plugin "HQ" mode).
5. Fractional factors work (12×: −68.5 dBc) but rule out halfband cascades, so they cost more per dB than 16× HB.

### Not done (and why)
- **Stateful ADAA:** Holters' method needs the nonlinearity's input to be scalar or very low-dimensional after
  the delay-free loop is solved; ours is 2-D (Vgk, Vak) and coupled through K, so the ADAA integral becomes a
  2-D table or a learned approximation (Köper & Holters 2023 do exactly that with a network). Diagnosis also
  says the plate law dominates, so a 1-D ADAA on grid current alone would cap at the ~5 dB the grid-off test
  shows. Not worth implementing before C++; revisit only if CPU forces 4×.
- **Grid-onset smoothing:** ≤ 5 dB available, changes physics. Not done.
- **Pre-/de-emphasis, input band-limiting:** the input is already band-limited by the upsampler, and the circuit's
  own Rgs·Cmiller low-pass is part of the model; an extra emphasis pair would change the sound at high drive
  (it changes which frequencies are driven into the nonlinearity), so it is a design choice, not a fidelity fix.
- **BLAMP/polyBLAMP:** designed for hard clipping with detectable clip points in a memoryless signal path;
  our nonlinearity is smooth and inside a feedback loop. Not applicable.

## 3. Ranked recommendations
1. **Replace the oversampling filters with halfband cascades** (IIR polyphase allpass for embedded/eco; linear-phase
   FIR halfband for HQ). Zero aliasing/accuracy cost, ~25–35 % CPU saved at 8×. Also fixes the end-of-signal
   tail issue structurally if implemented as streaming filters with reported latency.
2. **Loosen NR tolerance to ~1e-6 V and add the linear-extrapolation predictor.** ~20 % further CPU saving,
   no measurable accuracy change. (Keep the iteration cap and pnjlim/line search.)
3. **Make 16× the plugin default at the CPU of today's 8×** (variant F): −75 dBc at 10 kHz/8 V, −66 dBc worst
   in the sweep, small-signal 0.013 dB. Keep 8× (variant E, ~0.55× of today) as "standard" and
   4× + prewarp (variant I, ~0.3× of today) as "eco"/Octatrack candidate, accepting −44 dBc at 10 kHz/8 V.
4. Add aliasing to the pass criteria at the chosen default (e.g. ≤ −60 dBc at 10 kHz/8 V for 8×, ≤ −72 dBc for 16×),
   measured with the offset-frequency sweep, not 1 kHz multiples.
5. Fix the validate harness tail (audit item), then re-check that FIR variants also land at ~0.14 dB H2–H5.
6. Only if 4× is forced on the Octatrack and its aliasing is audible: prototype a reduced stateful ADAA (2-D table)
   or a hybrid (plate law tabulated, ADAA on that), per Holters 2020 §vector-valued input.

## Sources (fetched/verified 4 Nov 2026)
- M. Holters, "Antiderivative Antialiasing for Stateful Systems", DAFx-19, https://www.dafx.de/paper-archive/2019/DAFx2019_paper_4.pdf ; extended in Appl. Sci. 10(1):20 (2020), https://www.mdpi.com/2076-3417/10/1/20 (applies to nonlinearities with scalar or very-low-dimensional input; delay compensated via coefficients; vector-valued input discussed). Code: https://github.com/martinholters/ADAA_Examples.jl
- D. Albertini, A. Bernardini, A. Sarti, "Antiderivative antialiasing in nonlinear wave digital filters", DAFx-20, https://dafx2020.mdw.ac.at/proceedings/papers/DAFx2020_paper_35.pdf ; "Antiderivative Antialiasing Techniques in Nonlinear Wave Digital Structures", JAES 69(7/8):448–464 (2021), https://re.public.polimi.it/bitstream/11311/1208018/1/21115.pdf (one-port and multiport, incl. BJT).
- J. D. Parker, V. Zavalishin, E. Le Bivic, "Reducing the aliasing of nonlinear waveshaping using continuous-time convolution", DAFx-16, https://www.dafx.de/paper-archive/2016/dafxpapers/20-DAFx-16_paper_41-PN.pdf
- S. Bilbao, F. Esqueda, J. D. Parker, V. Välimäki, "Antiderivative Antialiasing for Memoryless Nonlinearities", IEEE SPL 24(7) 2017, author copy https://aaltodoc.aalto.fi/server/api/core/bitstreams/87839e85-c165-4bd9-aeed-7c96ff64026a/content
- F. Esqueda, S. Bilbao, V. Välimäki, "Aliasing Reduction in Clipped Signals" (BLAMP/polyBLAMP), IEEE TSP 64(20) 2016, https://www.pure.ed.ac.uk/ws/files/26997332/07499828.pdf
- L. Köper, M. Holters, "Antialiased State Trajectory Neural Networks for Virtual Analog Modeling", DAFx-23, https://www.dafx.de/paper-archive/2023/DAFx23_paper_53.pdf (neural approximation of the multivariate ADAA integral).
- A. Carson et al., "Sample Rate Independent Recurrent Neural Networks for Audio Effects Processing", DAFx-24, https://www.dafx.de/paper-archive/2024/papers/DAFx24_paper_68.pdf (seen in search; not read in full).
- P. P. La Pastina, S. D'Angelo, L. Gabrielli, "Arbitrary-order IIR antiderivative antialiasing", DAFx-21 — cited from the reference list at https://ryukau.github.io/filter_notes/antiderivative_antialiasing/antiderivative_antialiasing.html ; paper itself not fetched.
- Polyphase IIR halfband: L. de Soras, HIIR library (two-path polyphase IIR, ×2 up/down), http://ldesoras.free.fr/prod.html . Coefficient formulas reimplemented in `resamplers.py` from memory of HIIR's designer and **verified numerically** (passband flat to < 0.001 dB to 20 kHz, image rejection −86 dB, stopband ≤ −84 dB above 28 kHz).
- Not verified this session (no claims made from them): Valenzuela & Constantinides 1983 original halfband paper; Werner 2015 thesis.
