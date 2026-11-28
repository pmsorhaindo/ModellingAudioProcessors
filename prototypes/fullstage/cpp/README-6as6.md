# cpp/dk6as6_core.h — header-only realtime C++ core of the 6AS6 pentode DK stage (#104)

Gyre's v1 distortion stage (#75): a port of the Python 6AS6 stage (`dk6as6/`: `stage.py`, `solver.py`,
`circuit.py`, `pentode.py`, `lpf.py`) to the realtime C++ core. The Python stage stays the reference. The 12AX7
stage (`dk_core.h`, [README.md](README.md)) stays in the core, unchanged, as the optional swap-in; how the plugin
exposes the two is #114 (`plugin/README.md`, "Valve paths": one Valve parameter), and parameter smoothing / mode
switching is #115.

| file | what |
|---|---|
| `dk6as6_core.h` | the core: valve law, solvers, netlist and matrices, Rk solve, operating point, post-filter, `dk6as6::Stage` |
| `dk_core.h` | shared infrastructure: the streaming half-band FIR resampler (`ResamplerT`, now with a `FirDesign`), `ScopedFlushDenormals`, `DZ_12AX7_RSD1` (the grid law's shape) |
| `export_vectors_6as6.py` → `vectors_6as6.txt` | reference vectors from the Python stage |
| `test_core_6as6.cpp` | the C++ tests; also the `cli` used by the Python harnesses, and the `law` / `solve*` pipes for the bit-exact tests |
| `tests/test_cpp_core_6as6.py` | pytest: vectors, bit-exact checks, freshness, k140 derivation, the ngspice validation reproduced, benchmark smoke, aliasing records |
| `validate_cpp_6as6.py` → `results_6as6.json` | `dk6as6.validate` (the ngspice criteria) on the C++ core |
| `matrix_rounding_6as6.py` | why the C++-built stage differs from Python where it does: matrix rounding (both builds), not the per-sample code |
| `aliasing_6as6.py` → `aliasing_6as6.json`, `aliasing_6as6_baseline[_44100].json` | the aliasing sweep, the antialiasing decision and the per-tone guard |
| `bench_6as6.cpp` (with `bench_common.h`, `bench.py --core 6as6`) → `bench_6as6_*.json` | the CPU benchmark, the #20 method (records: x86-64 CI runners, the loaded M1) |

## What is ported

Every value keeps the source label it has in Python (copied into the header's comments).

- **Circuit (#102, PR #118).** `circuit.VALUES`: HT 200 V, Ra 270k, Rl 1M, Rg 1M, Cin = Cout = 100n, EC2 120 V, the
  GE-sheet valve capacitances, Rs 1k and Rgs 68k kept from the 12AX7 bench, Rstrap 100 Ω. The cathode is an
  unbypassed rheostat to ground; there is no Ck. Every passive is inferred, none measured (`dk6as6/RESULTS.md`).
- **Bias (#110, PR #120).** A target DC cathode current in mA, 0.1–0.6 mA, default 0.45. `prepare()` solves the Rk
  that draws it: Brent's method on log10 Rk over 10^2.7–10^6 Ω, xtol 1e-13, rtol 4 eps, a port of scipy's
  `brentq` (`stage.cathode_resistor`). Rk is then fixed for the run (live bias changes are #115). `knob_bias()` /
  `bias_knob()` port the knob law (our choice, #110). `Pack::rk`, `op.nodes[CATH]` (Vk = Rk Ik) and `op.ik` are
  what a bias meter needs (`stage.bias_point`).
- **Modes (#111, PR #121).** P1 (default), T and P2, exactly `circuit.MODES`: the pins, the screen feed and where
  CG3 goes. Fixed for the run (switching is #115). P2 is our hypothesis on the fit's weakest region and is never
  supported; `prepare()` refuses it and the other unsupported settings unless `Settings::experimental` is set
  (Python raises `UnsupportedSettingError`), and `Pack::supported` records which it is.
- **Grid current (#112, PR #128).** The optional g1 current: Dempwolf & Zoelzer's 12AX7 law scaled by the fitted
  cathode-perveance ratio, `grid()` = `pentode.grid`; borrowed and unvalidated (no 6AS6 grid-current data). With it,
  `cathode_share` / `currents_grid` (Ip = (Ik − Ig1) f, Ig2 = (Ik − Ig1)(1 − f)), the 3×3 Newton step by Cramer's
  rule (`solver._step3`), an evaluation that overflows treated as a failed trial (`_resid3_safe`), and the restart
  contract (`solve_grid_restarts`): the extrapolated warm start, then the previous sample's currents, then the
  operating point; if none converges, the candidate with the smallest finite residual over all three, flagged.
  Iterations of a sample are bounded by 3 × `Newton::max_it` (150); the worst measured on any validated signal is
  15 (PR #128), and no recorded signal restarts at all (`grid.json`). `grid_hardness = 0` is the #111 two-port
  stage exactly.
- **Post-filter (#103, PR #119).** `lpf.PostFilter`: both TPT SVFs (Q 0.4277, f0 9535 × 4/7 and 9535 Hz, pre-warped
  at 15.7 kHz) run inside the oversampled block after the nonlinearity and before decimation; Off / 4k / 7k with the
  15 ms linear crossfade on a change (`set_lpf()`, any thread). "off" is the unfiltered output bit for bit.
- **Quality.** Standard 8× and hq 16×, no pre-warp (`stage.QUALITY`). There is no eco: eco is not a 6AS6 setting,
  and `prepare()` refuses a pre-warped quality. The 12AX7's `PREWARP_HZ` is not used.
- **Solver.** `solver.py`'s method and tolerances (dk/solver.py's TOL_V = TOL_F = 1e-6 V, MAX_IT 50, LS_MAX 10):
  Newton on the currents, R(i) = i − i(p − K i), dR/di = I + J K, converged when ‖K Δi‖∞ < TOL_V and ‖K R‖∞ < TOL_F,
  backtracking line search, no step limiter. Warm start: linear extrapolation of the currents, 2 i[n−1] − i[n−2].
  The 2-current solve keeps the best finite iterate at the cap (#7).
- **State.** The deviation from the operating point (#70): 7 capacitor histories, the currents of the last two
  samples, the SVF states, the resampler. Silence stays on the out node's DC value (measured within 1.7e-14 V).

What is not ported: `filt="iir"` (FIR only, as the plugin), the out-of-law and non-convergence *warnings* (counted
instead: `Stats::out_of_law`, `nonconverged`, `restarted`), Python's REFIT_51 comparison fit (any `PentodeParams`
can be passed, but only `DUAL_CONTROL` is tested).

Deliberate differences, all outside what the Python stage computes on a validated signal: where an evaluation
overflows, Python's 2-current solve raises (the stage errors out); the core keeps the best iterate and counts it.
NaN / inf input samples (checked after the drive gain, so a finite sample times a gain that overflows is caught too)
are replaced by 0 and counted; `set_drive()` / `set_output()` ignore a non-finite gain. If the state still goes
non-finite (a finite input far outside any signal, such as the largest double: #104 review), that sample outputs 0 and
the state goes back to the operating point with the resampler and filter states cleared (`Stats::recoveries`),
realtime-safe and without `reset()`; `test_nonfinite_recovery` checks the output is finite and back on the operating
point (to 5e-26 V) in later silent blocks. An output sample whose gained value does not fit the destination type
(a finite but huge `set_output()` gain; for float I/O anything beyond `FLT_MAX`) is range-checked in double before the
conversion, written as 0 and counted (`Stats::nonfinite_outputs`); the state is not touched (`test_output_overflow`,
double and float I/O). A finite signal at a sane gain never takes either path (the vectors are bit-identical).
The Python stage has no such path (NaN would propagate).

## Using it

```cpp
#include "dk6as6_core.h"          // includes dk_core.h (and ../dk/fastmath.h through it)

dk6as6::Stage s;                                   // one per channel (no SIMD 6AS6 kernel: "CPU" below)
dk6as6::Settings set;                              // P1, 0.45 mA, grid current on, filter off
set.bias_ma = 0.3;
if (s.prepare(48000.0, dk6as6::STANDARD, set) != dk6as6::Status::ok) { /* unsupported, out of range, ... */ }
s.set_drive(1.0); s.set_output(1.0);               // any thread
s.set_lpf(dk6as6::Lpf::k7);                        // any thread: 15 ms crossfade
s.process(in, out, n);                             // audio thread; latency_samples() of delay
```

Threads: `prepare()` / `prepare_pack()` on a non-RT thread, never concurrently with `process()` (they run Brent,
the matrix build and the DC solve). `set_drive()`, `set_output()`, `set_lpf()` from any thread (relaxed atomics, read
once per block). `process()`, `process_internal()`, `reset()` on the audio thread: no allocation, locks or syscalls
(allocation counted in the tests), `noexcept`, holding `ScopedFlushDenormals` (FTZ / DAZ on x86, FPCR.FZ on AArch64,
the caller's mode restored). Latency: the resampler's. With the default k140 design 79 base samples at standard /
48 kHz, 127 at 44.1 kHz (hq 82 / 130); with the shipped design, the 12AX7 stage's 72 / 116 (hq 73 / 117).

## Resampler: #113's k140 by default

The stage uses `dk_core.h`'s resampler. Its design is a parameter (`FirDesign`): `SHIPPED_FIR` is `dk/resample.py`'s
(Kaiser β 9; first stage 127 taps, 215 at 44.1 kHz; later stages 23), the resampler of every committed Python result
and of the 12AX7 core, whose vectors still pass bit for bit. `K140_FIR` is #113's recommendation (PR #129): β 14.47,
123 / 219 first-stage taps (48 / 44.1 kHz), 47 later, re-derived from #113's rule by `test_k140_derivation`.
`dk/resample.py` gained the same `fir=` option as PR #129 (byte-identical hunk), so the exporter writes k140 vectors.
An invalid or oversized design (not 4m + 3 taps, longer than the instance holds, or an overflowing delay) gives no
geometry and `prepare()` returns `bad_argument` (`test_fir_design_rejected`).

**The 6AS6 stage runs `DEFAULT_FIR = K140_FIR`** (one line in `dk6as6_core.h`; Michael's decision on PR #133, #134):
with the shipped FIR, P1 0.6 mA at 2 V and 44.1 kHz, a supported setting, is over the aliasing gate from ~19957 Hz up
("Antialiasing" below). `Settings::fir` selects either at run time. **The Python stage's default is k140 too**
(`dk6as6.stage.FIR = "k140"`, `K140`, `Stage(fir=)`; #134, after #129): the validation reproduced compares k140 with
k140, and the Python 6AS6 aliasing guard (`dk6as6/aliasing_guard.py`) measures k140. The #102–#113 sweeps and the helpers
that reproduce them (`dk6as6.aliasing.measure`, `lpf_aliasing.measure`, `validate_4x`) name the shipped FIR
explicitly, so those committed records keep their meaning. `test_k140_design_is_one_design` checks that the Python
table, #113's derived candidate and `dk_core.h` agree.
The stage's resampler instance holds either design (`ResamplerT<double, 219, 47>`).

## Tests and tolerances

`python -m pytest -q cpp/tests/test_cpp_core_6as6.py` (from `prototypes/fullstage`). CI runs it as its own shard
on Linux (g++) and macOS (Apple clang).

**Vectors** (`export_vectors_6as6.py`, 45 stage cases: every mode at 0.1 / 0.25 / 0.45 / 0.6 mA with and without
the grid current at 8 V, the 0.45 mA cases at 2 V, hq, 44.1 kHz, the post-filter in both positions and a switch, gains,
noise, a burst, a 15 kHz 8 V tone in T at 0.6 mA, forced non-convergence with and without grid current, and k140).
Measured on an Apple M1, Apple clang 17, `-O2` (FP contraction on: FMA) and `-O2 -ffp-contract=off`:

| vectors | metric | measured (contraction on / off) | tolerance |
|---|---|---|---|
| `DUAL_CONTROL`, `grid()` | bits | equal to `fit_results.json` / `pentode.GRID` | bit-equal |
| valve law, 840 points (floors, cut-off, positive g1) | per column / its max | 4.3e-16 / 0 (bit-equal) | 1e-13 |
| `solve`, `solve_grid`, `solve_grid_restarts`, 278 problems | V; A / 1 mA | 6.0e-13 / 0 (bit-equal); iterations identical | 1e-11 |
| matrices A..K of 28 packs (C++-built vs numpy) | max \|ΔM\| / max \|M\| | 9.8e-12 / 7.9e-12 | 1e-10 |
| Rk (Brent) | relative | 1.1e-13 | 1e-11 |
| operating point, nodes, rest constants | relative | 5.3e-12 | 1e-10 |
| post-filter, k140 resampler | / max \|out\| | 1.3e-16 / 0; 2.4e-15 | 1e-13 |
| stage on the **Python pack** ("pinned"), 45 cases + internal | V | 6.8e-13 | 1e-9 |
| stage on the **C++-built pack** ("built") | V | 4.2e-8 | 1e-5 |
| Newton iteration sums, histograms, non-converged, restarted, out-of-law | fraction | 0 (identical) | 1e-3 |

*Why the tolerance is what it is.* The per-sample code is the Python arithmetic in the same order, so on Python's
own pack it agrees to 6.8e-13 V (the remaining difference is numpy's BLAS summation order in `G @ x`, `A @ x`). With
FP contraction off, the valve law and the three solvers equal the live Python functions **bit for bit** on 4000 random
points and 2000 random problems (`test_bit_exact_*`), including where Python raises (`OverflowError`) and the 3-current
solve treats the trial as failed. That needs two details of CPython: its `sum()` of floats is Neumaier-compensated
since 3.12 (ported, `detail::pysum4`), and `x ** 2` calls libm `pow`, which compilers would fold into `x * x`
(`detail::opaque`). The C++-built pack differs because its matrices round differently: `matrix_rounding_6as6.py`
measures both builds against the exact matrices (`fractions.Fraction`). Both round at ~1e-12, the floor of
representing the MNA matrix in double; the stage's slow coupling-cap modes integrate that, most at hq. The C++ build
uses compensated (Dot2) products and an iteratively refined inverse, which brought it from 5–18× numpy's error at hq to
numpy's level (P2, 16×, 0.1 s at 4 V: C++ vs exact 3.8e-7 V, numpy vs exact 3.0e-7 V; it was 1.4e-6 V). So "built"
checks the whole core end to end at 1e-5 V (−140 dB re 100 V), as `dk_core.h` does, and "pinned" catches a change in
the per-sample code at 1e-9 V.

Also tested: no allocation in `process()` / `process_internal()` (global `operator new` / `delete` replaced, glibc
`malloc` interposed: `alloc_hooks.h`), bit-identical output for blocks of 1, 37 and the whole signal, the forced
fallbacks (finite output with every sample failing, every failure counted, ≤ 3 × max_it), NaN / inf input and
gains and the recovery from a non-finite state without `reset()`, invalid FIR designs rejected (clean under UBSan), the
supported envelope and the refusals (P2, T above its envelope, a bias Rk cannot set, a pre-warped quality),
`process()` restoring the caller's FP mode, float I/O, every host rate 44.1–192 kHz at both qualities, silence.

**The ngspice validation reproduced** (`validate_cpp_6as6.py` → `results_6as6.json`). `dk6as6.validate.run` with the
C++ core in place of the Python stage, for all 44 committed results (models dual and dualg, every mode and reference
current, standard and hq), k140 on both sides: the committed `dk6as6/results*.json` were re-run with the Python
stage's k140 default (#134), and every supported setting passes there too. Every time-domain number (harmonics, THD, burst, Newton iterations) comes from the C++
`process()`, the small-signal numbers from the C++ matrices and operating point. **Every criterion (dk/validate.CRIT,
unchanged) reads as it does for Python, and every supported setting passes at standard and hq.** Worst |C++ − Python|:

| quantity | all settings (setting) | supported settings | limit | CRIT |
|---|---|---|---|---|
| operating point, relative | 8.6e-12 | 8.6e-12 | 1e-9 | 1e-3 |
| AC (every reported number) | 3.8e-8 dB | 3.8e-8 dB | 1e-6 dB | 0.1 dB |
| H2–H5 above −90 dBc | 9.6e-6 dB (P2 0.45 mA hq) | 1.0e-6 dB | 3e-4 dB | 0.1 dB |
| H1, THD, relative | 1.3e-7, 1.2e-7 (P2 0.6 mA hq) | 2.5e-8, 9.6e-8 | 5e-6 | — |
| burst Vg1k rest / min / trajectory | 3.4e-9 V | 2.8e-9 V | 1e-7 V | 0.5 % / 1 % |
| burst tail tau and criteria, relative | 5.2e-7 (P2 0.6 mA hq) | 2.7e-7 | 2e-5 | 1 % |
| Newton histograms | identical | identical | 1e-3 | — |

The largest are in the experimental P2 at hq, where both builds' matrix rounding is integrated most (above).

**With the shipped FIR** (`validate_cpp_6as6.py --fir shipped` → `results_6as6_shipped.json`, the #102–#113
resampler, which the committed Python results no longer use, so this run is judged on `dk/validate.CRIT` alone): every
supported setting passes every criterion at standard and hq, and all 44 statuses read as the k140 Python results'. For
information, the largest differences from them: AC 0.0011 dB (the FIR passband), H2–H5 4.7e-4 dB, H1 2.7e-6
relative, burst criteria 3.8e-5 (supported settings: 0.0011 dB, 4.0e-4 dB, 1.5e-6, 2.5e-5), against CRIT's 0.1 dB,
0.1 dB and 0.5–1 %.

**Mutation check** (21 Nov 2026, by hand: one-line changes to `dk6as6_core.h`, then `test_core_6as6` on the
vectors):
- the restart order (previous sample's currents before the warm start): 85 checks fail;
- LS_MAX 10 → 9: the iteration histogram of the 15 kHz T case;
- `LPF_XFADE_S` 15 → 14 ms: the post-filter vectors (4 checks);
- CG1G3 from g1–g3 to g1–k: the packs of T and P2 (41 checks);
- CPython's compensated `sum()` replaced by a plain one: no vector check (the root moves by < 4e-14 V), but the
  solvers' bit-equal fraction falls from 1 to 0.946, which `test_bit_exact_solvers` asserts.

## Antialiasing: oversampling alone, or with ADAA (#104), and the guard (#98)

**Method** (`aliasing_6as6.py`, `aliasing_6as6.json`). `dk/aliasing.py`'s metric on the C++ core: offset tones on
coherent bins, periodic-Hann analysis, the total non-harmonic energy in 20 Hz–20 kHz relative to the fundamental
(dBc), split by source: *internal* (harmonics above the internal Nyquist folded inside the stage, what oversampling and
ADAA act on), *resampler* (in-band harmonics reaching 0–20 kHz through the half-band filters) and *other* (no fold,
e.g. settling). 1.25 s settle (`dk6as6.aliasing.SETTLE_S`, the #102 coupling caps), then a 1 s window; one C++ run per
point, 256-sample blocks. The supported envelope with the default grid current: P1 at 0.1 / 0.25 / 0.45 / 0.6 mA, T at
0.1 / 0.25 / 0.5 mA; standard and hq; 2 and 8 V; filter off (the post-filter only lowers absolute alias energy,
`dk6as6/RESULTS-lpf.md`); the 19 sweep tones plus the band edge, 19937 / 19957 / 19977 / 19997 Hz; 48 and 44.1 kHz;
both designs at every point (2576 points). The gate is the one the Python stage's supported envelope uses: the shipped
12AX7 stage's committed budget for the same quality and drive (`dk.aliasing.limits_for_rate`, FIR), an engineering
budget, not an audibility threshold (the repo has none).

**Parity.** On the 98 points it shares with the committed Python sweeps (`dk6as6/aliasing_grid[_T].json`, 48 kHz,
standard, shipped), the C++ core reads the same to 1.1e-5 dB.

**Results**: the worst total over every supported setting and tone, its split, and the worst once the internal term
is removed from every point (what an ideal ADAA would leave). k140 is the core's default:

| rate | quality | drive | design | worst total dBc (where) | internal / resampler there, dBc | worst with internal removed, dBc | an ideal ADAA lowers the worst by, dB | gate dBc | margin dB |
|---|---|---|---|---|---|---|---|---|---|
| 48 kHz | standard | 2 V | **k140** | −162.8 (P1 0.6 mA 19997 Hz) | −200.5 / −162.8 | −162.8 | 0.0 | −118.3 | 44.5 |
| 48 kHz | standard | 2 V | shipped | −123.6 (P1 0.6 mA 19037 Hz) | −205.3 / −123.6 | −123.6 | 0.0 | −118.3 | 5.3 |
| 48 kHz | standard | 8 V | **k140** | −63.4 (T 0.5 mA 19997 Hz) | −63.4 / −150.5 | −150.5 | 87.1 | −48.6 | 14.8 |
| 48 kHz | standard | 8 V | shipped | −63.4 (T 0.5 mA 19997 Hz) | −63.4 / −110.8 | −110.8 | 47.4 | −48.6 | 14.8 |
| 48 kHz | hq | 2 V | **k140** | −162.8 (P1 0.6 mA 19997 Hz) | −200.5 / −162.8 | −162.8 | 0.0 | −118.2 | 44.6 |
| 48 kHz | hq | 2 V | shipped | −123.6 (P1 0.6 mA 19037 Hz) | −205.3 / −123.6 | −123.6 | 0.0 | −118.2 | 5.4 |
| 48 kHz | hq | 8 V | **k140** | −84.9 (T 0.5 mA 19037 Hz) | −84.9 / −156.1 | −150.5 | 65.6 | −63.0 | 21.9 |
| 48 kHz | hq | 8 V | shipped | −84.9 (T 0.5 mA 19037 Hz) | −84.9 / −110.9 | −110.8 | 25.9 | −63.0 | 21.9 |
| 44.1 kHz | standard | 2 V | **k140** | −155.3 (T 0.5 mA 19997 Hz) | −155.7 / −166.2 | −162.9 | 7.6 | −118.3 | 37.0 |
| 44.1 kHz | standard | 2 V | shipped | **−117.0** (P1 0.6 mA 19997 Hz) | −203.6 / −117.0 | −117.0 | 0.0 | −118.3 | **−1.3** |
| 44.1 kHz | standard | 8 V | **k140** | −59.9 (T 0.5 mA 19037 Hz) | −59.9 / −153.3 | −150.1 | 90.2 | −46.8 | 13.1 |
| 44.1 kHz | standard | 8 V | shipped | −59.9 (T 0.5 mA 19037 Hz) | −59.9 / −108.8 | −103.3 | 43.4 | −46.8 | 13.1 |
| 44.1 kHz | hq | 2 V | **k140** | −162.9 (P1 0.6 mA 19997 Hz) | −203.6 / −162.9 | −162.9 | 0.0 | −118.2 | 44.7 |
| 44.1 kHz | hq | 2 V | shipped | **−117.0** (P1 0.6 mA 19997 Hz) | −203.6 / −117.0 | −117.0 | 0.0 | −118.2 | **−1.2** |
| 44.1 kHz | hq | 8 V | **k140** | −80.4 (T 0.5 mA 19997 Hz) | −80.4 / −150.1 | −150.1 | 69.8 | −63.0 | 17.4 |
| 44.1 kHz | hq | 8 V | shipped | −80.3 (T 0.5 mA 19997 Hz) | −80.4 / −103.3 | −103.3 | 23.0 | −63.0 | 17.3 |

No point had a non-converged or out-of-law sample, and "other" (settling) stays at or below −178.2 dBc. **With k140
every supported point is inside the gate.** With the shipped FIR only P1 0.6 mA at 2 V and 44.1 kHz, 19957 Hz and up,
is over it (both qualities; `test_aliasing_records` asserts exactly that set).

**Band-edge scan** (`python -m cpp.aliasing_6as6 scan`): P1 0.6 mA, 2 V, 10 Hz steps above the band edge.

| rate | quality | design | 19937 Hz | 19947 | 19957 | 19967 | 19977 | 19987 | 19997 | gate dBc |
|---|---|---|---|---|---|---|---|---|---|---|
| 48 kHz | standard | shipped | −131.7 | −129.9 | −128.5 | −127.2 | −126.1 | −125.2 | −124.3 | −118.3 |
| 48 kHz | standard | k140 | −167.2 | −166.1 | −165.2 | −164.5 | −163.8 | −163.3 | −162.8 | −118.3 |
| 44.1 kHz | standard | shipped | −119.2 | −118.6 | **−118.1** | **−117.7** | **−117.4** | **−117.1** | **−117.0** | −118.3 |
| 44.1 kHz | standard | k140 | −175.6 | −171.1 | −168.3 | −166.2 | −164.7 | −163.7 | −162.9 | −118.3 |
| 44.1 kHz | hq | shipped | −119.2 | −118.6 | **−118.1** | **−117.7** | **−117.4** | **−117.1** | **−117.0** | −118.2 |
| 44.1 kHz | hq | k140 | −175.6 | −171.1 | −168.3 | −166.2 | −164.7 | −163.7 | −162.9 | −118.2 |

The shipped FIR's failure is the resampler's (the internal term there is ≤ −199.9 dBc), so neither more oversampling
nor ADAA changes it; k140 puts the same points 44.6–57.3 dB inside the gate. That is why it is the default.

**Decision** (recommended here, accepted by Michael on PR #133): oversampling alone, no ADAA, for this stage.
- The cases that bind are at 2 V, and all of them are the resampler's: internal folding is ≤ −155.7 dBc at 2 V at
  every point, and an ideal ADAA lowers no 2 V worst case with either design (0.0 dB, except 7.6 dB at 44.1 kHz
  standard with k140, 37 dB inside the gate). The failing 44.1 kHz band edge is fixed by the resampler: k140, the
  default.
- At 8 V the worst cases are internal folding (T 0.5 mA near 19–20 kHz). Oversampling alone is already 13.1–14.8 dB
  inside the gate at standard and 17.4–21.9 dB at hq. An ideal ADAA could lower those worst cases by 87–90 dB at
  standard with k140 (43–47 dB with the shipped FIR), and hq already lowers them by 20.5–21.5 dB at the measured CPU
  cost ("CPU" below).
- What this measures: oversampling alone at every point, and ADAA's ceiling at every point (the internal term, which is
  all ADAA acts on). It does not measure an ADAA implementation: none was built, so its CPU cost and its effect on the
  validated small-signal and harmonic accuracy are not known. Building and measuring one (a stateful formulation, also
  as a way to run a lower oversampling factor for CPU) is #136.

**Guard (CI, `aliasing (6as6 C++ core, 48000 / 44100 Hz)`).** Per rate, every supported setting × standard / hq ×
2 / 8 V at its full sweep's worst tone, 19037 Hz and the four band-edge tones (19937–19997 Hz), on the stage's own
default design: the CLI runs `DEFAULT_FIR` ("default") and reports its name, which is part of every key, so changing
the default fails the guard until the baselines are updated on purpose. Full 1 s windows after the 1.25 s settle (140
points per rate). Two checks per tone, as `dk.aliasing_guard`: the gate above, and each tone's own committed baseline
(`aliasing_6as6_baseline[_44100].json`, #98 per-tone format, `format_version` 2, 1.5 dB). Smallest margins: 14.8 dB
at 48 kHz and 13.1 dB at 44.1 kHz (T 0.5 mA, 8 V, standard). To update a baseline intentionally:
`python -m cpp.aliasing_6as6 guard --fs N --update-baseline --reason "..."`.

**The Python guard** (`dk6as6/aliasing_guard.py`, CI jobs `aliasing (6as6, <rate> Hz)`, from #113) measures the Python
stage with k140 too since #134, at #113's keys (7 per rate, including P1 0.6 mA at 2 V, standard and hq, at 44.1 kHz,
where the shipped FIR failed the gate), in the 0.1 s CI window. Its limits keep #113's rule (the worst over the full
grid + 3 dB, the highest ≤ 3 tones within 3 dB of it). The k140 grid at those keys (#113's grid and scan plus 19957 /
19977 / 19997 Hz, 29 tones, 1 s windows) is measured on this core (`python -m cpp.aliasing_6as6 guardgrid` →
`dk6as6/aliasing_guard_k140.json`), because a Python 1 s full grid costs hours per rate. The Python baselines equal this
core's CI-window readings at the same points to ≤ 0.01 dB (`test_committed_baseline_equals_the_cpp_core_in_the_ci_window`).
One change to the rule: with k140 at 2 V the 0.1 s window's analysis floor (−149.6 dBc) lies above the 1 s worst
(−162.9 dBc), so each limit also counts its tones' CI-window readings. The P1 0.6 mA 2 V keys' limit is therefore
−146.5 dBc, 28 dB inside the −118.3 dBc gate. The real −162.9 dBc is resolved by this core's guard, with 1 s windows.

## CPU

**#135** measured three ways to cut this cost: fast maths for the valve law (`Settings::math = LawMath::fast`), a lazier
Jacobian (now always on, bit-exact with the libm path) and a two-lane stereo kernel (`dk6as6::StereoStage`,
`cpp/dk6as6_stereo.h`). Speed, accuracy evidence and what was adopted: `cpp/RESULTS-cpu-6as6.md`. The numbers below
are #104's, before #135.

**Method**: the #20 / #67 harness and method unchanged (`cpp/BENCHMARK.md`): `bench_6as6.cpp` shares `bench.cpp`'s
signal (the Karplus–Strong guitar phrase), counters, clocks, warm-up, round-robin passes, determinism check and
percentiles through `bench_common.h`, and is run by `python -m cpp.bench --core 6as6`, which records the host, the
build and the host state before and after. `-O3`, no `-march` (x86-64: the SSE2 baseline; Apple clang: apple-m1).
Configurations: the default setting (P1, 0.45 mA, grid current, filter off, the default k140 FIR) at standard / hq ×
0.5 / 2 / 8 V × blocks 64 / 512 × one instance (x1) or two on one thread (x2, the stereo cost: there is no 6AS6 SIMD
kernel), plus x1 / block-64 rows that change one thing: T 0.5 mA, P1 0.1 and 0.6 mA, no grid current, the 7k filter,
the shipped FIR. No cycle budget is set (none exists for this stage; the Octatrack figure is for a later cut-down
model).

**x86-64: GitHub's ubuntu-latest runner** (`.github/workflows/bench-6as6.yml`, g++ 15.2, 5 × 1 s passes, idle VM,
no hardware counters). Two runs, which landed on different runner CPUs: `bench_6as6_x86_64_linux_ci_epyc9v45.json`
(this head's core, k140 default) and `bench_6as6_x86_64_linux_ci_epyc7763.json` (the previous head, shipped FIR
default; the two designs cost the same to 0.98–1.00 in both runs). % of one core at 48 kHz real time, median,
block 64 (block 512 within 1 %):

| runner CPU | quality | instances | 0.5 V | 2 V | 8 V | ns per sample (2 V) |
|---|---|---|---|---|---|---|
| EPYC 9V45 | standard 8× | x1 | 20.8 % | 23.0 % | 24.7 % | 4793 |
| EPYC 9V45 | standard 8× | x2 (stereo) | 41.6 % | 45.9 % | 49.4 % | 9572 |
| EPYC 9V45 | hq 16× | x1 | 39.5 % | 40.9 % | 44.4 % | 8519 |
| EPYC 9V45 | hq 16× | x2 (stereo) | 78.9 % | 81.8 % | 88.6 % | 17038 |
| EPYC 7763 | standard 8× | x1 | 32.3 % | 35.8 % | 38.9 % | 7450 |
| EPYC 7763 | standard 8× | x2 (stereo) | 64.5 % | 71.6 % | 77.1 % | 14919 |
| EPYC 7763 | hq 16× | x1 | 61.1 % | 63.2 % | 69.0 % | 13159 |
| EPYC 7763 | hq 16× | x2 (stereo) | **121.7 %** | **126.1 %** | **137.2 %** | 26269 |

So whether hq stereo fits one core depends on the host: it needs more than one core of the EPYC 7763 VM and 79–89 %
of one of the EPYC 9V45 VM. These are shared VMs, a statement about those hosts only. One change from the default
(x1, b64, ns / default, EPYC 9V45 run): no grid current 0.78–0.79; T 0.5 mA 0.95–0.99; P1 0.1 mA 0.90–0.94; P1 0.6 mA
0.99–1.02; the 7k filter 0.97–0.99; the shipped FIR 0.98–1.00.

**Apple M1** (MacBookAir10,1, macOS 15.6.1, Apple clang 17, 11 × 2 s passes; `bench_6as6_apple_m1_loaded.json`).
**Not a clean timing**: this machine is shared with other work and was loaded during the run (load average 70 → 37 on
8 cores); the thread was on a CPU for 82–99 % of the wall time, and in each row's worst pass up to half of its cycles
ran on E-cores (marked * in the tables). Its wall times and cycle counts therefore overstate the cost and are not
quoted as the stage's; the clean M1 timing is #135 (Michael's decision on PR #133). What does not depend on the core
type or on preemption is the instruction count, and against the 12AX7 core's committed M1 record
(`bench_apple_m1_simd_math.json`, libm, x1, block 64, same harness) it gives the relative cost:

| quality | 0.5 V | 2 V | 8 V |
|---|---|---|---|
| standard: 6AS6 / 12AX7 instructions per sample | 54 117 / 21 139 = 2.56 | 57 799 / 21 750 = 2.66 | 63 360 / 23 467 = 2.70 |
| hq: 6AS6 / 12AX7 instructions per sample | 104 882 / 40 989 = 2.56 | 107 136 / 41 107 = 2.61 | 113 124 / 42 510 = 2.66 |

The grid current costs 18 % of the instructions (the third current and the 3×3 step).

**Why no SIMD or fast path here.** #104 asks for SIMD only where it measurably pays, and none was measured: the core
mirrors the Python reference's libm arithmetic (that is what makes the law and solvers bit-exact), and a two-lane
kernel would have to carry the per-lane Newton and restart contract. The one change made for speed keeps the bits:
`softplus` and `sigmoid` share their `exp(-|x|)` (~4 % fewer instructions). Fast maths, a lazier Jacobian and a
two-lane kernel are #135.

## Limitations

- One valve parameter set tested (`DUAL_CONTROL`), the `circuit.VALUES` circuit, standard and hq. Eco does not exist.
- Mode, bias and grid hardness are fixed for a prepared stage (#115). The post-filter position can change live.
- No stereo SIMD kernel; stereo is two mono stages (see CPU).
- FIR resampler only. 44.1 and 48 kHz are the rates with aliasing measurements; others run with the 48 kHz-rate
  geometry and are only checked to prepare and run.
- The core is validated against the Python stage and, through it, the same-model ngspice references; the valve fit,
  the grid law and the circuit values carry their own labels (`dk6as6/RESULTS.md`). Nothing here is a statement about
  sound.
