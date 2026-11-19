> **Reference implementation only**, not the shipping core. The AU / VST 3 plugin and the realtime C++ path use the nodal DK stage (`../fullstage/cpp/`). This tree is kept so the same circuit can be compared as a wave digital filter. See [README.md](README.md).

# WDF stage vs DK stage: accuracy, CPU, parameter-change cost (#12, 18 Nov 2026)

`python -m wdf.validate --workers 4` (from `prototypes/wdf`) → `wdf/results.json`; `python -m pytest -q` → **79 passed**.
**The WDF stage passes every DK criterion in every quality setting, with the same numbers as the DK stage**, and
its output equals the DK stage's to ≤ 7.4e-8 V on outputs that swing to ±132 V. This is expected, not a coincidence:
with trapezoidal capacitors and the same Newton at the root, the WDF is the same discrete-time system as the DK
stage, computed in a different order (below). A bias change costs the WDF 0.12× what it costs the DK stage, because
the DK stage also solves for a new DC operating point. Per internal sample in Python, the WDF takes 0.92–0.94× the
DK stage's time.

## What was built
`wdf/stage.py`, class `WdfStage`: the same 12AX7 common-cathode stage as `fullstage/dk/`. It has the same interface
as `dk.stage.Stage`, so `dk.validate`, `dk.linear`, `dk.aliasing` and `dk.aliasing_guard` run on it unchanged, and it
reuses these parts of the DK stage directly:
- the component values `dk/circuit.VALUES` (example values, not Culture Vulture values) and `dk/circuit.netlist()`;
- the Dempwolf–Zölzer 12AX7 with grid current, RSD-1 parameters (`dk/triode.py`);
- the damped Newton `dk/solver.solve` (pnjlim, line search, TOL 1e-6, best-iterate fallback, #7 reporting);
- the quality settings `dk/stage.QUALITY` (hq 16×, standard 8×, eco 4× with the small-cap pre-warp at 17.3 kHz),
  the FIR / IIR half-band resamplers (`dk/resample.py`), the Drive / Bias / Output controls and the warm start.

Nothing in `fullstage/` was changed except one README line. The ngspice netlists and committed references are
`fullstage/spice/`'s.

**Tree** (`wdf/adaptors.py`; voltage waves A = v + R i down, B = v − R i up, Fettweis 1986). The three valve
capacitances CCG, CGP and CCP, together with the triode's own ports, connect every pair of the nodes g, k, a and
ground. That bridge cannot be built from series and parallel adaptors alone, so the root is an R-type adaptor on g, k
and a with eight ports:

| R-type port | subtree below it |
|---|---|
| [g, 0] grid | Series(Rgs, Parallel(Rg, Series(Cin, source(vin, Rs)))) |
| [k, 0] cathode | Parallel(Rk, Ck) |
| [a, 0] plate | Parallel(source(HT, Ra), Series(Cout, Rl)) |
| [g, k], [g, a], [a, k] | the capacitor leaves CCG, CGP, CCP |
| [g, k], [a, k] (unadapted) | the triode: (Vgk, Ig) and (Vak, Ia) |

The scattering matrix S is the MNA-derived one of Werner, Smith and Abel [1] and Werner, Nangia, Smith and Abel [2,
eq. (19)–(20)]. `rtype_scattering()` uses it in nodal-reduced form, S = 2 incᵀ Y⁻¹ inc G − I with Y = inc G incᵀ,
and `test_adaptors.py` checks this against eq. (20) built element by element. The delay-free loop through the two
nonlinear ports is resolved with the K-method of [2]. The w-K converter C of eq. (18) and S give E, F, M and N by eq.
(14)–(15), and the root then solves

  v = p + F i(v),  p = E a_E,  b_E = M a_E + N i,

for v = (Vgk, Vak). This is the equation `dk/solver.solve` solves, with K = −F. Newton on a grouped WDF root is the
approach of Olsen, Werner and Smith [3]. The internal port resistances R_I are arbitrary, as in [2]; the code sets
them to 1 kΩ, the value [2] uses in its case study. `test_adaptors.py` checks that E, F, M and N do not depend on
R_I between 1 Ω and 10 MΩ (to 1e-9 relative). Every capacitor is the trapezoidal one-port B[n] = A[n−1] with
R = 1/Gc, where Gc is the DK stage's companion conductance (`circuit.companion_g`). That includes the pre-warped Gc
of the eco setting, so the quality settings mirror the DK stage's exactly.

**State and DC.** The WDF runs on absolute waves: the 300 V HT and the bias currents flow through the tree, and the
state is the six capacitor waves. `operating_point()` is the fixed point of the WDF's own sample map at zero input.
It is found by probing the (linear) tree into DK form, eliminating the state, and running the DK stage's 10-step HT
source-stepping Newton. `reset()` starts the capacitors there. The probed map `WdfStage.m` is used only by the
small-signal criterion (`dk.linear`); processing never uses it.

## Accuracy against ngspice: the DK criteria (#5, #38), reference (ii) `reference_dz.json`
The criteria are those in `fullstage/dk/RESULTS.md`, run through `dk.validate.run` with `WdfStage` in place of
`Stage` (as `cpp/validate_cpp.py` does for the C++ core). All rows use the FIR resampler, at Drive = Bias =
Output = 1. **WDF and DK give the same value in every cell** to the digits shown; their differences are in the next
table.

| criterion (limit) | hq 16× | standard 8× | eco 4× |
|---|---|---|---|
| operating point vs ngspice | 2.15e-9 rel | 2.15e-9 rel | 2.15e-9 rel |
| small signal, worst 20 Hz–20 kHz (0.1 dB; #38 chain) | 0.0133 dB at 20 kHz | 0.0520 dB at 20 kHz | 0.0538 dB at 20 kHz |
| H2–H5 worst tested deviation (0.1 dB, ref > −90 dBc) | 0.00023 dB (H5, 2 V) | 0.00076 dB (H5, 2 V) | 0.0165 dB (H5, 2 V) |
| 4 V burst: shift / τ / trajectory (0.5 % / 1 % / 0.5 %) | 0.031 % / 0.041 % / 0.0035 % | 0.031 % / 0.041 % / 0.0034 % | 0.0091 % / 0.041 % / 0.0078 % |
| Newton iterations, 8 V sine: mean / max | 2.18 / 3 | 2.45 / 4 | 2.55 / 5 |
| non-converged samples | 0 | 0 | 0 |
| **all criteria** | **pass** | **pass** | **pass** |

Recovery τ is 12.755 ms in both stages, against ngspice's 12.750 ms. `test_e2e_and_aliasing.py` also runs the #38
multitone end-to-end check (through `process()` and both resamplers) on the WDF in every setting: within 0.1 dB of
ngspice and within 0.002 dB / 0.5° of the linearised chain.

### WDF vs DK directly (`validate.direct`)
These are the same inputs through both stages. The sines are 1 kHz at 0.05 / 0.5 / 2 / 8 V for 0.05 s and the burst
is the criteria's 4 V burst, compared sample by sample after `process()`. The harmonics and AC come from the
criteria runs above.

| max \|WDF − DK\| | hq | standard | eco |
|---|---|---|---|
| operating point (relative) | 4.2e-12 | 3.6e-12 | 4.0e-12 |
| AC magnitude / phase, 20 Hz–20 kHz (reference grid) | 2.8e-9 dB / 8.9e-9° | 3.5e-9 dB / 8.1e-9° | 3.7e-10 dB / 9.4e-10° |
| output, 8 V sine (output peak ≈ 132 V) | 3.8e-8 V | 6.1e-8 V | 6.4e-9 V |
| output / Vgk, 4 V burst | 5.1e-8 V / 1.0e-10 V | 7.4e-8 V / 3.2e-11 V | 7.9e-9 V / 7.1e-12 V |
| H2–H5, tested harmonics | 8.3e-10 dB | 1.5e-9 dB | 1.6e-10 dB |
| H2–H5, all (worst: H5 at 0.05 V, −178 dBc) | 1.1e-6 dB | 1.1e-5 dB | 3.2e-6 dB |
| Newton iteration histograms (8 V sine, burst) | identical | identical | identical |

The differences are rounding: the two stages compute the same quantities in a different order. The tests pin them
with wide margins (operating point 1e-9 relative, AC 1e-7 dB and 1e-6°, outputs 1e-5 V, burst Vgk 1e-8 V,
`test_wdf_stage.py`), and also check that the probed map has DK's K and the same eigenvalues of A.

### Aliasing (#6, #35)
This uses the blocking CI subset from `dk/aliasing_guard.py`, run on the WDF by swapping the stage in the workers.
The validator now uses the shared format-version-2 loader and per-tone comparison: all 18 CI points are compared
with their own DK baseline in `dk/aliasing_baseline.json`, with unchanged limits and 1.5 dB tolerance.
During the per-tone migration, the targeted 18-point WDF subset and all nine tests in
`test_e2e_and_aliasing.py` pass, including a synthetic non-worst-tone regression through the validator.
The full WDF reference JSON was not regenerated for this format change.

The historical table below summarizes the worst point of each setting. In that original validation,
**all 12 setting rows were within their limits, and the largest difference from the DK baseline was 8.5e-8 dB.**

| measured (limit), dBc | FIR 2 V | FIR 8 V | IIR 2 V | IIR 8 V |
|---|---|---|---|---|
| hq | −121.27 (−118.2) | −66.03 (−63.0) | −115.23 (−112.2) | −66.03 (−63.0) |
| standard | −121.40 (−118.3) | −51.69 (−48.6) | −114.00 (−111.0) | −51.69 (−48.6) |
| eco | −75.71 (−72.7) | −32.60 (−29.6) | −75.70 (−72.7) | −32.60 (−29.6) |

`test_e2e_and_aliasing.py` runs standard / FIR at 2 V and 8 V in the pytest job.

### Silence
At zero input the WDF output is a constant offset of 3.2e-11 V (hq), 1.3e-12 V (standard) and 6.1e-11 V (eco).
Over 0.2 s it does not grow: the first and second halves give the same maximum. The DK stage, in its deviation form
since #70, gives exactly 0. Before #70 its absolute form started at the DC point and drifted, on the M1, to the
discretised map's own rest point, 1.6 µV away at hq (`fullstage/RESEARCH-numerics.md`). The WDF also runs on
absolute waves, but its `operating_point()` is by construction the fixed point of its own sample map (via the probed
map), not the continuous-time DC point. That is presumably why it does not drift; the remaining offset was not
analysed further. This was measured on Linux x86_64 only, not on the M1, where #70 was seen. The tests require
< 1e-9 V over 10 ms and the capacitor states at the operating point to 1e-9 relative.

## Python CPU per sample (interpreted prototype: relative numbers only)
These were measured on a shared Linux x86_64 VM with 4 CPUs, Python 3.13.18 and NumPy 2.5.4, one process per
measurement. Both stages are scalar Python loops over the same Newton and the same triode evaluation (`libm` math
path), so the ratio reflects the per-sample structure outside Newton plus interpreter overhead. It is **not** a
prediction for C++. Nothing here was measured on Apple silicon or in C++.

**(a) Per internal sample, resamplers excluded (the comparison to use).** `process_internal()` runs on a 2 V 1 kHz
sine at the internal rate for 20 ms. WDF and DK alternate, each is timed with `time.perf_counter()` and reset before
every run, and the median of 7 runs is taken. Both make the same Newton iterations (mean 2.02 / 2.36 / 2.91).

| µs per internal sample | WDF | DK | WDF / DK |
|---|---|---|---|
| hq 16× | 15.49 | 16.83 | 0.92 |
| standard 8× | 16.64 | 18.01 | 0.92 |
| eco 4× | 18.52 | 19.76 | 0.94 |

eco costs more per sample because it takes more Newton iterations per sample.

**(b) Whole `process()`, FIR resamplers included, as `dk.validate` reports it** (seconds of CPU per second of
48 kHz audio, 0.5 s 1 kHz sines). Each value is a single timed run, so it is noisier than (a).

| s/s at 0.05 / 0.5 / 2 / 8 V | WDF | DK |
|---|---|---|
| hq | 12.13 / 12.12 / 12.45 / 12.42 | 13.25 / 13.24 / 13.58 / 13.32 |
| standard | 6.11 / 6.08 / 6.44 / 6.67 | 6.66 / 6.74 / 6.91 / 7.03 |
| eco | 3.15 / 3.07 / 3.72 / 3.33 | 3.59 / 3.31 / 3.87 / 3.71 |

**(c) Multiplies per internal sample outside Newton**, counted from the structures, not timed
(`validate.multiplies`). The DK stage uses 77: 14 for p, 9 for the outputs and 54 for the state update. The WDF uses
73: 12 for p = E a_E (2×6), 48 for the root scatter b_E = M a_E + N i (6×6 + 6×2), 12 in the three series and
three parallel adaptors, and 1 for the output. Newton and the triode evaluation are identical in both and, by
`fullstage/research/octatrack-budget.md`, dominate the cost (≈ 8 transcendentals per evaluation). On these counts, a
C++ WDF would be expected to cost about the same as the C++ DK core; that is not measured.

## Parameter-change cost (standard setting)
Bias scales Rk; Drive and Output are scalar gains on the input and output in both stages, so they cost nothing to
change in either. Each `swap_bias` time below is the median of 7 passes through biases 0.5, 0.75, 1.25, 1.5, 2.0 and
1.0, divided by 6.

| per bias change | DK `swap_bias` | WDF `swap_bias` |
|---|---|---|
| time (Python) | **341 µs** | **39.7 µs** (0.12× DK) |
| recomputed | `circuit.build` (inverse of the 10×10 MNA matrix, 9 state-space products): 61 µs. DC operating point: a second build with the capacitors open, then 10 source-stepped Newton solves (46–47 iterations): 268 µs. The rest constants of the deviation form | the Rk leaf; the cathode parallel adaptor (2 coefficients); the R-type S (a 3×3 nodal solve over 8 ports); E, F, M, N (one 2×2 inverse) |
| DC solve / Newton | yes | none |
| state across the change | kept (deviation form, re-referenced) | kept (capacitor waves) |

A bias step from 1.0 to 1.5 at 20 ms into a 2 V sine gives the same output in both stages: the largest difference is
2.9e-8 V, on a 101 V peak. `test_wdf_stage.py` checks this at 0.5, 1.5 and 2.0, and also checks that the WDF swap
makes no Newton calls.

Most of the DK cost is the DC solve. The deviation form needs it because its rest constants (#70) depend on the new
operating point; it is not inherent to the DK method. An absolute-form DK stage would need only the 61 µs build,
against the WDF's 40 µs (0.65×). The WDF's advantage here is locality: a change to Rk touches one leaf, its adaptor
and the root, and the capacitor waves carry the circuit through the change. In a C++ port both would run off the
audio thread (`cpp/`'s lock-free coefficient swap); neither is ported.

## Unverified / not done
- **C++.** There is no WDF C++ port, so there are no realtime CPU figures, and nothing was measured on Apple silicon.
  The CPU numbers above are Python on a shared VM.
- **ngspice references** were not regenerated for this work; the tests use the committed `reference_dz.json`
  (made with ngspice-41). As a spot check, `spice/run_refs.run("dz", write=False)` run here with ngspice-42
  reproduced its operating point, AC, H1–H5 at all four levels and burst metrics exactly (difference 0). The Koren
  reference (i) was not re-run.
- **Silence** was measured on Linux x86_64 only. The #70 drift was seen on the M1 (its size depends on the
  BLAS/LAPACK rounding), and the WDF's ≤ 6e-11 V there is not measured. The WDF has no macOS CI job.
- The **IIR resampler** is covered by the aliasing subset only. The criteria table above uses the FIR, as the DK
  settings are defined.
- The pure-WDF alternative, a D'Angelo–Pakarinen–Välimäki triode element [5] with a series/parallel tree, was not
  built. The valve capacitances make a bridge, which that tree cannot hold without approximations.
- Dunkel et al., "The Fender Bassman 5F6-A Family of Preamplifier Circuits — A Wave Digital Filter Case Study"
  (DAFx-16), is the closest prior art, but no link to it is verified (`fullstage/RESEARCH-numerics.md`). It was not
  read and is not relied on here.

## References
The links were fetched and checked on 18 Nov 2026; all open-access PDFs except [4] (DOI resolves; paywalled).
1. K. J. Werner, J. O. Smith III, J. S. Abel, "Wave Digital Filter Adaptors for Arbitrary Topologies and Multiport
   Linear Elements", Proc. DAFx-15, Trondheim, 2015. <https://dafx.de/paper-archive/2015/DAFx-15_submission_53.pdf>
2. K. J. Werner, V. Nangia, J. O. Smith III, J. S. Abel, "Resolving Wave Digital Filters with Multiple/Multiport
   Nonlinearities", Proc. DAFx-15, Trondheim, 2015. <https://www.dafx.de/paper-archive/2015/DAFx-15_submission_54.pdf>
   (eq. (14)–(16) K-method root, (18) w-K converter, (19)–(20) MNA scattering; 1 kΩ internal port resistances in the
   case study)
3. M. J. Olsen, K. J. Werner, J. O. Smith III, "Resolving Grouped Nonlinearities in Wave Digital Filters Using
   Iterative Techniques", Proc. DAFx-16, Brno, 2016.
   <https://dafx.de/paper-archive/2016/dafxpapers/39-DAFx-16_paper_14-PN.pdf>
4. A. Fettweis, "Wave Digital Filters: Theory and Practice", Proc. IEEE 74(2), 1986.
   DOI [10.1109/PROC.1986.13458](https://doi.org/10.1109/PROC.1986.13458)
5. S. D'Angelo, J. Pakarinen, V. Välimäki, "New Family of Wave-Digital Triode Models", IEEE TASLP 21(2), 2013.
   DOI [10.1109/TASL.2012.2224340](https://doi.org/10.1109/TASL.2012.2224340) (listed in
   `references/tube-modelling-papers.md`; not used in this implementation)
6. K. Dempwolf, U. Zölzer, "A Physically-Motivated Triode Model for Circuit Simulations", Proc. DAFx-11, Paris,
   2011. <https://dafx.de/paper-archive/2011/Papers/76_e.pdf> (the valve model, via `dk/triode.py`)

## Files
| file | what |
|---|---|
| `wdf/adaptors.py` | leaves, series / parallel adaptors, R-type scattering, K-method root matrices |
| `wdf/stage.py` | `WdfStage`: tree, root Newton, operating point, bias swap, `process()` with the DK resamplers |
| `wdf/validate.py` | criteria through `dk.validate`, WDF-vs-DK comparison, aliasing subset, CPU, parameter change |
| `wdf/results.json` | the output of `python -m wdf.validate` that this file quotes |
| `tests/` | `test_adaptors.py` (adaptor algebra against the papers' definitions), `test_wdf_stage.py` (DK criteria per setting, WDF = DK, bias swap, non-convergence), `test_e2e_and_aliasing.py` (multitone end-to-end, aliasing) |
