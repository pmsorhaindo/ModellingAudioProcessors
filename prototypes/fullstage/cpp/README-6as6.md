# cpp/dk6as6_core.h — header-only realtime C++ core of the 6AS6 pentode DK stage (#104)

Gyre's v1 distortion stage (#75): a port of the Python 6AS6 stage (`dk6as6/`: `stage.py`, `solver.py`,
`circuit.py`, `pentode.py`, `lpf.py`) to the realtime C++ core. The Python stage stays the reference. The 12AX7
stage (`dk_core.h`, [README.md](README.md)) stays in the core, unchanged, as the optional swap-in; how the plugin
exposes the two is #114, and parameter smoothing / mode switching is #115. The plugin (`plugin/`) still wraps the
12AX7 stage.

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
| `bench_6as6.cpp` (with `bench_common.h`, `bench.py --core 6as6`) → `bench_6as6_*.json` | the CPU benchmark, the #20 method |

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
NaN / inf input samples are replaced by 0 and counted, as in `dk_core.h`.

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
the caller's mode restored). Latency: the resampler's, as the 12AX7 stage (72 base samples at standard / 48 kHz,
116 at 44.1 kHz; hq 73 / 117).

## Resampler: shipped FIR or #113's k140

The stage uses `dk_core.h`'s resampler. Its design is now a parameter (`FirDesign`): `SHIPPED_FIR` is
`dk/resample.py`'s (Kaiser β 9; first stage 127 taps, 215 at 44.1 kHz; later stages 23), the resampler of every
validated Python result and of the 12AX7 core, whose vectors still pass bit for bit. `K140_FIR` is #113's
recommendation (PR #129, still open): β 14.47, 123 / 219 first-stage taps (48 / 44.1 kHz), 47 later, re-derived from
#113's rule by `test_k140_derivation`. `dk/resample.py` gained the same `fir=` option as PR #129 (byte-identical
hunk), so the exporter writes k140 vectors.

The 6AS6 stage runs **`DEFAULT_FIR`, one line in `dk6as6_core.h`, now `SHIPPED_FIR`**: the Python reference on
`main` has no k140, and #129 is not merged. Adopting k140 is that one line, then the guard's baselines
(`aliasing_6as6 guard --update-baseline`) and the Python side (#129's follow-up). `Settings::fir` selects either at
run time for measurements. The stage's resampler instance holds either design (`ResamplerT<double, 219, 47>`).

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
fallbacks (finite output with every sample failing, every failure counted, ≤ 3 × max_it), NaN / inf input, the
supported envelope and the refusals (P2, T above its envelope, a bias Rk cannot set, a pre-warped quality),
`process()` restoring the caller's FP mode, float I/O, every host rate 44.1–192 kHz at both qualities, silence.

**The ngspice validation reproduced** (`validate_cpp_6as6.py` → `results_6as6.json`). `dk6as6.validate.run` with the
C++ core in place of the Python stage, for all 44 committed results (models dual and dualg, every mode and reference
current, standard and hq). Every time-domain number (harmonics, THD, burst, Newton iterations) comes from the C++
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

**Mutation check** (21 Nov 2026, by hand: one-line changes to `dk6as6_core.h`, then `test_core_6as6` on the
vectors):
- the restart order (previous sample's currents before the warm start): 85 checks fail;
- LS_MAX 10 → 9: the iteration histogram of the 15 kHz T case;
- `LPF_XFADE_S` 15 → 14 ms: the post-filter vectors (4 checks);
- CG1G3 from g1–g3 to g1–k: the packs of T and P2 (41 checks);
- CPython's compensated `sum()` replaced by a plain one: no vector check (the root moves by < 4e-14 V), but the
  solvers' bit-equal fraction falls from 1 to 0.946, which `test_bit_exact_solvers` asserts.

## Antialiasing: oversampling alone, or with ADAA (#104), and the guard (#98)

PENDING: filled in from `aliasing_6as6.json`.

## CPU

PENDING: filled in from `bench_6as6_*.json`.

## Limitations

- One valve parameter set tested (`DUAL_CONTROL`), the `circuit.VALUES` circuit, standard and hq. Eco does not exist.
- Mode, bias and grid hardness are fixed for a prepared stage (#115). The post-filter position can change live.
- No stereo SIMD kernel; stereo is two mono stages (see CPU).
- FIR resampler only. 44.1 and 48 kHz are the rates with aliasing measurements; others run with the 48 kHz-rate
  geometry and are only checked to prepare and run.
- The core is validated against the Python stage and, through it, the same-model ngspice references; the valve fit,
  the grid law and the circuit values carry their own labels (`dk6as6/RESULTS.md`). Nothing here is a statement about
  sound.
