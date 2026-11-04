# Full-stage audit and retrospective (12AX7 DK stage), 4 Nov 2026

Scope: `prototypes/fullstage/` (dk/, spice/, analysis.py) plus `experiments/hf_fix/` (a scratch folder on the lab box, not in this repo). Nothing in the repo was edited.
Fresh run: `python -m pytest -q dk/tests` gave **12 passed in 18 s**. Paths below are relative to `prototypes/fullstage/`.

## Verified correct (with evidence)
- **DK derivation** (`dk/circuit.py:47-67`): nodal-DK as in Holters & Zölzer, with a trapezoidal companion model (i_c = Gc·v − x, x' = 2Gc·v − x). A, B, C, G, H and K match the standard form. Padding the source rows into the MNA is correct, and the sign convention for i_nl is consistent with F = −No·Si·Nn^T.
- **Capacitor history at t=0** (`dk/stage.py:65`): x = Gx·Nx·v_dc, so every i_c is 0. That gives no thump, and the silence test confirms it.
- **Dempwolf–Zölzer law** (`dk/triode.py`): matches DAFx-11 eq. 7–9 with the RSD-1 parameters. The ngspice subckt (`spice/models_dz/dz_12ax7.inc`) uses the same equations and parameters plus the same three Koren capacitances. The Koren subckt also has CCG/CGP/CCP, so both references describe the same circuit. The circuit values in `run_refs.py:20-31` match `circuit.VALUES`.
- **Jacobian:** I ran a central finite-difference check over Vgk from −30 to +10 V and Vak from 0.5 to 400 V. The worst relative error was **4.3e-6**, which is finite-difference noise.
- **Overflow safety:** softplus and sigmoid are written to be stable, and the power base is always ≥ 0, so there is no pow of a negative number and no exp overflow.

## Findings, by severity

### BUG
1. **Measurement artefact behind the "H5 at 0.5 V, −0.645 dB" error (diagnosed).** `Stage.process` (`dk/stage.py:106,112`) truncates both FIR passes at the end of the block, so the final ~1.3 ms of output is the filter tail ringing against zero padding. `validate.py:75-77` and `harmonics()` (`analysis.py`, which takes the last N cycles) analyse a window that includes this tail.
   Evidence at 0.5 V:
   - Internal-rate output at 8× gives H5 −97.70 dBc, against −97.73 for the ngspice reference.
   - Decimated output gives −98.38 dBc. That's the reported −0.645 dB.
   - Running 10 ms longer and analysing a window that stops before the tail gives **−97.870 dBc, against −97.866 from a tightened ngspice run (reltol 1e-6, 0.5 µs step). That's 0.004 dB.** H4 agrees to 0.004 dB too.

   It is not a reference-precision problem: tightening ngspice moved H5 by only 0.13 dB. It is probably also the cause of the 0.22 dB H3 deviation at 0.05 V, where the reference is at −96 dBc.
   **Fix:** add settle/guard padding in `validate.run`, simulating T + 20 ms and analysing a window that ends before the tail. Better, make `process` streaming, with persistent FIR state and fixed latency (see #6), so no edge exists. Apply the same fix to `experiments/hf_fix/run_option.py:15`, where the alias spectrum also includes the tail; the Hann window softens it but doesn't remove it.
2. **The documentation misstates the number:** `dk/RESULTS.md:25` says "H5 at 0.5 V is ~−80 dBc". It is −97.7 dBc. Fix it once #1 is fixed.

### RISK
3. **The AC test does not exercise the runtime loop** (`validate.py:24-45, 58-66`). `small_signal()` linearises the matrices analytically and multiplies by |H_fir|². A bug in `process_internal` or the resampling path (delay, gain, the DC trick) would pass. **Fix:** add a time-domain small-signal check through `process()`, using a 0.01 V multi-tone or stepped sines at 20/100/1k/10k/20 kHz, and compare it to the AC reference.
4. **Tolerances are 10–1000× looser than what we actually achieve,** so real regressions would pass: op 1% (achieved ~1e-4), harm 1 dB (≤0.005 dB after #1), burst 5%/10%/5% (achieved 0.03%/0.04%/0.004%). **Fix:** once #1 lands, set op 0.1%, harmonics 0.1 dB above −90 dBc, and burst 0.5%/1%/0.5%. Set the harmonic "tested" floor (`CRIT harm_floor_dbc=-100`) to −90 dBc, because the reference's own precision is about 0.1 dB at −98 dBc.
5. **Circuit values are duplicated in five places:** `circuit.VALUES`, the `run_refs.BODY` template, `stage_12ax7.cir`, `stage_12ax7_burst.cir` and `stage_12ax7_sine_long.cir`. The `.cir` files also still hold the obsolete 60 ms sine runs (`stage_12ax7.cir:39-45`). The capacitances live only in the subckts. **Fix:** generate every netlist from `circuit.VALUES` in `run_refs.py`, delete the hand-written duplicates (or keep one illustrative copy marked as generated), and add a test that parses the generated netlist and checks it against VALUES.
6. **Resampling is offline-only** (`stage.py:104-115`). There is no filter state across calls, so block-by-block processing clicks at every block boundary. Zero latency comes from trimming in hindsight, while the real latency is 2×255 taps at 384 kHz, about 1.33 ms. Each call also rebuilds the filter with `firwin`. **Fix:** a streaming polyphase interpolator and decimator with persistent state and reported latency. For C++, use cascaded half-band stages (2× three times), which cost much less than a 511-tap FIR at 384 kHz. Consider a minimum-phase option to cut latency on the Octatrack.
7. **The convergence criterion is absolute 1e-9 V on both ports** (`solver.py:14-15,62`). That is about 5e-12 relative on Vak, so almost every sample takes exactly 3 iterations (0.5 V: 191,500 of 192,000). The third iteration is probably spent below audio relevance. It's also counted even when the warm start is already converged. **Fix:** use a scaled tolerance (for example |dv| < 1e-6 V on Vgk and 1e-5 V on Vak, plus a residual check), then re-measure the iteration histogram and harmonic deviations. This is likely about 30% less solver CPU, which matters for Octatrack and for multiple instances.
8. **The line search re-evaluates the full Jacobian on every trial** (`solver.py:31-35,55`), with up to 10 extra exp/log/pow calls per halving. **Fix:** evaluate currents only during the search, and compute the Jacobian once after a step is accepted.
9. **Non-convergence is silent at runtime.** The loop counts it but keeps the last iterate. The DC solve uses `assert` (`stage.py:56`), which is stripped under `-O`. **Fix:** raise explicitly in the DC solve. In the C++ core, define the fallback (keep the best iterate, flag it, and keep the state finite).
10. **The CPU numbers in validate/RESULTS are pure-Python wall time** and say nothing about C++ or the Octatrack. Keep them labelled as such (they are), and don't use them for the effect-slot decision.
11. **The aliasing figure (−60 dBc at 8×, 10 kHz, 8 V) is unverified as genuine.** The Kaiser β = 8 FIR gives about −80 dB stopband, and its transition runs from about 20 to 24 kHz, so the filter is not the limit. The residual is most likely high-order harmonics folding at 384 kHz. Re-measure once #1 is fixed, against a 32× or 64× run, so we know how much of it is algorithmic before picking a mitigation.

### CLEANUP
12. `process_internal` (`stage.py:70-96`) allocates `np.array` for u and inl, plus five-output matvecs, on every sample. Only the outputs that get recorded need computing. That's fine for a Python prototype, but the C++ port should precompute p = G·x + H_vin·vin + (H_ht·HT constant), because HT is constant, fold it into a bias vector, and keep a 6-state fixed array.
13. `Gu, Hu = G, H` (`stage.py:79`) is a leftover alias. Remove it.
14. In `analysis.load_wrdata`, the header sniff reads only the first character. `run_refs.analyse` uses positional op indices (`op[1], op[3]...`), which is fragile. Write named vectors and parse by name.
15. The pnjlim `VCRIT = 0` (`solver.py:19`) is not SPICE's vcrit (vt·ln(vt/√2·Is)). It is a defensible choice for the DZ law, but document it as ours, not "SPICE style".
16. The test fixture runs the whole of `validate.run()` (18 s). Split it into fast unit tests (Jacobian FD, op, silence, a streaming-equals-whole-buffer check after #6) and a slow marker for the ngspice comparison.
17. Metric naming: the burst "recovery τ" (1/e of the cycle-averaged Vgk, 12.75 ms) differs from the "≈100 ms recovery" quoted in chat and in the earlier ngspice summary, which used a visual full-recovery time. Pick one, define it in `run_refs.burst_metrics`, and use it everywhere.

## C++ / realtime-readiness checklist
- **State:** 6 capacitor states, 2 NR unknowns, and fixed 6×6, 2×6, 2×2 matrices. Store them row-major in `std::array`, with no allocation.
- **Per sample:** 1 solve, about 3 iterations today and about 2 after #7. Each iteration costs 2 softplus, 2 sigmoid and 2 pow. Use `log1p`/`exp`, pow as exp(γ·log s) guarded for s = 0, and possibly fast approximations validated against the same tests.
- **Denormals:** set FTZ/DAZ on the audio thread. The capacitor states decay towards the DC value, not zero, so the risk is low, but the input-history paths can go denormal in silence.
- **Parameter changes:** rebuilding a matrix is a 10×10 inverse. Do it off the audio thread and swap atomically (agreed). For a smoothed Bias, Rk lives in S, so either interpolate tables of matrices over a Bias grid, or use Sherman–Morrison, since Rk is a rank-1 update.
- **Branches:** only the pnjlim and line-search loops. Cap the count, and make the common path branch-light.

## Retrospective
**Went well:** the method was isolated from the model by giving ngspice the identical DZ law, which meant model error (Koren H3 6 dB) was separated from method error. Failing tests were kept failing instead of loosened (the 20 kHz case). Small, logical commits were made. The scaling claims were checked (warping ∝ 1/os², aliasing −15 dB per doubling).
**Change:** (a) test the measurement code itself. A synthetic signal with known harmonics and a pass through the full process() chain would have caught #1 before we spent effort on an "OS-dependent error". (b) Generate references from one source of truth. (c) Set tolerances relative to what is achieved, once something works. (d) Write the numbers in docs from results.json automatically, not by hand.

## Technical debt (short list)
Edge artefact/offline resampling (#1, #6), duplicated netlists (#5), loose tolerances (#4), AC test not end-to-end (#3), absolute NR tolerance (#7), RESULTS.md hand-copied numbers (#2, #17), Python CPU numbers (#10).

## Prioritised next steps
1. **Fix the measurement** (#1, #2): guard padding, re-run validate and the hf_fix comparison, and correct RESULTS.md/COMPARISON.md. This is cheap and changes the reported numbers.
2. **Streaming resampler** (#6) with a test that streaming equals whole-buffer output, plus the end-to-end AC test (#3).
3. **Tighten the tolerances** (#4) and **add the aliasing criterion:** total alias ≤ −60 dBc at 10 kHz/8 V, measured against a 64× ground truth (#11).
4. **NR tolerance and line-search cost** (#7, #8): measure the iteration histogram and harmonic error before and after.
5. **Aliasing reduction research,** each option measured with the same harness: (i) a cheaper half-band oversampling chain so 8× costs less; (ii) 4× + warping correction as the "Eco" quality setting; (iii) state-space ADAA (Holters DAFx-19) on a 1-D reduced nonlinearity, valid only where grid current is negligible; (iv) a smoother grid-current onset, which is a model change, so it gets labelled.
6. **C++ header-only core:** fixed-size state, the same reference JSON as test vectors, and a per-instance benchmark at 48 kHz across buffer size and OS factor on the laptop. Then an Octatrack effect-slot budget check from the modwork repo constraints.
7. **WDF version** of the same stage (Werner 2015 R-type adaptor with a 2-port triode at the root), validated with the same harness, comparing accuracy, CPU and parameter-change cost against DK.
8. **6AS6 low-current refit:** Raytheon 1960 families (Vg2 60–140 V), log-scale error so the 0.1–0.6 mA region counts, and a check of the screen/plate split against the suppressor curves. Then the 6AS6 DK stage.
