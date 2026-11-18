# Parameter smoothing: Drive, Output and Bias (#73)

**Recommendation: the bias line.** Bias moves on the audio thread, one exact pack per base sample, built from two
packs prepared in advance. There is no matrix build, no lock and no allocation on the audio thread. A fast full-range
bias move then steps by at most 0.119 mV in silence and 9.30 mV with an 8 V sine (stage output, against a continuous
reference). The unsmoothed swap steps by 61–108 mV and 0.127–7.35 V on the same moves. Drive and Output keep the
wrapper's 20 ms per-sample ramps from #91. The Drive ramp already runs at the host rate, before the stage's upsampler,
and adds no measurable aliasing.

All numbers here were measured on an Apple M1 (macOS, Apple clang 17, `-O2`) on 18 Nov 2026, with the commands under
"Reproduce". Voltages are at the stage output. The plugin maps 1 V there to 1/64 of full scale at Output 1
(`plugin/src/map_params.h`), so 1 mV = −96 dBFS. Timings are informal in-test medians, not a benchmark
([BENCHMARK.md](BENCHMARK.md)).

## Why a bias move clicks, and what smoothing can and cannot remove

Bias scales Rk, the cathode resistor. Before #73, a bias change rebuilt the pack (matrices plus a 10-step DC solve)
off the audio thread, and the stage adopted it at the next block boundary. The state was kept, so the circuit
then moved to the new operating point on its own. Two things reach the output:

1. **The step at the swap.** All the matrices change at once, so one sample's output changes abruptly. This is the
   click, and smoothing removes it.
2. **The circuit's own transient.** The anode and cathode move to the new operating point through Cout and Ck. The
   output sees this as a thump: on a full-range move over 20 ms, it is 22–36 V at the stage output (0.35–0.57 FS at
   Output 1) away from the constant-bias output (last row of the options table). The continuous reference has it
   too. A real pot turned that fast does the same. A step-free smoother does not remove it; only a slower move
   shrinks it.

## Method

**Continuous reference.** This is the same bias move, 20 ms linear from b0 to b1, with an **exact pack
(`build_coeffs`) at every internal sample**. A `Stage` runs through `process_internal()` one internal sample at a time.
Before each sample, the pack for that sample's bias is `publish()`ed. The stage has its own `Resampler` around it.
This is the same up → stage → down path as `Stage::process`, and it equals it bit for bit at a constant bias (checked
in `test_core.cpp`). The method under test reaches b1 at the same sample.

**Click metric ("worst step").** max over n of |r[n] − r[n−1]|, with r = y − y_ref, at the base rate. It is the step
that the update method adds on top of the continuous move. "max |y − ref|" is given beside it. A smooth difference,
such as the thump arriving slightly earlier, shows up there and hardly at all in the worst step.

**Moves.** Full range, 0.5 → 2.0 and 2.0 → 0.5 (the plugin's Bias range), at 0.1 s into 0.3 s of signal, 48 kHz.
Signals are silence and a 220 Hz sine of 1 V or 8 V peak at the stage input (Drive 1 and 8 at 0 dBFS in the plugin).

## The options

Command: `./bias_options` ([bias_options.cpp](bias_options.cpp)), standard 8×, blocks of 64 unless stated. Each cell
is the worst step, with max |y − ref| in brackets, in V.

| option | up, silence | up, 1 V | up, 8 V | down, silence | down, 1 V | down, 8 V |
|---|---|---|---|---|---|---|
| unsmoothed (one `request_bias` swap, before #73) | 0.0617 (8.46) | 0.127 (7.8) | 1.67 (4.52) | 0.108 (25.3) | 0.305 (29.9) | 7.32 (26.3) |
| 1. rate-limited swaps, one per 512-sample block | 0.0336 (6.18) | 0.122 (4.79) | 1.01 (2.93) | 0.058 (9.03) | 0.115 (10.6) | 3.58 (10.1) |
| 1. … per 240 samples (the wrapper's 5 ms worker period) | 0.0349 (2.83) | 0.0519 (2.37) | 0.48 (1.36) | 0.0486 (7.4) | 0.0852 (8.33) | 2.8 (8.01) |
| 1. … per 64-sample block | 0.0131 (0.819) | 0.0181 (0.739) | 0.154 (0.437) | 0.0164 (1.88) | 0.0287 (2.21) | 0.678 (2.01) |
| 1. … per 16-sample block | 0.00339 (0.209) | 0.0044 (0.188) | 0.0391 (0.112) | 0.00391 (0.457) | 0.00669 (0.537) | 0.159 (0.451) |
| 2. crossfade of two stages over 20 ms | 0.0123 (3.87) | 0.0841 (4.36) | 0.997 (2.56) | 0.0652 (16.5) | 0.288 (19.5) | 5.32 (15.4) |
| 3a. exact matrices, operating point and x0 interpolated | 0.0117 (3.52) | 0.053 (3.54) | 1.37 (3.6) | 0.0112 (3.65) | 0.0493 (4.12) | 1.4 (3.74) |
| **3b. bias line (chosen)** | **0.000112** (0.0116) | **0.00026** (0.0105) | **0.00218** (0.00624) | **9.86e-05** (0.025) | **0.000289** (0.0293) | **0.00868** (0.0246) |
| the move itself: max \|ref − constant bias\| | 22.4 | 23.1 | 25.5 | 29.9 | 36.2 | 31.2 |

Timings, from the same command: informal medians of repeated calls with `std::chrono::steady_clock`. A second run
earlier in the session gave `build_coeffs` 9.2 µs and matrices 1.7 µs, so read these as ±30 %.

| work | time | where it runs |
|---|---|---|
| `build_coeffs` (what `request_bias` builds) | 7.25 µs | worker thread |
| … of which the matrices (2 × `build_full`) | 1.38 µs | |
| … so the 10-step source-stepped DC solve | ≈ 5.9 µs (81 %) | |
| one bias-line update (matrices + re-reference) | 0.167 µs | audio thread, per base sample while moving |
| … with the warm-started DC solve | 0.542 µs | audio thread, once at the end of a ramp |
| warm-started DC solve for a full-range jump (0.5 → 2.0) | 7 Newton iterations, converged | |

| option | complexity | realtime safety | deterministic / block-size invariant | verdict |
|---|---|---|---|---|
| unsmoothed | none | yes (worker builds) | no: lands at a block boundary, ~5 ms of wall clock after the change | clicks |
| 1. rate-limited swaps | small (worker loop) | yes (worker builds) | no: the step size depends on the host block and the worker's wall-clock timing; at 512 or the 5 ms poll it is 0.28–0.96× the unsmoothed step | clicks less, unpredictably |
| 2. crossfade of two stages | large: a state copy API (resampler included), and the wrapper's two slots are taken by quality switching | yes | no (worker timing) | 2× CPU for the fade, and still 0.6–0.94× the unsmoothed step under a sine: the swapped stage's response is faded in, not avoided |
| 3a. interpolated operating point | small | yes | yes | the operating point is not linear in bias: it is wrong by volts mid-range, and the stage is pulled by an inconsistent rest point |
| 5. warm-started DC solve | small | yes | — | it makes a build cheaper (one solve, 7 iterations, instead of 10 source steps), but it does not change the swap, so on its own it does nothing for the click. Used inside 3b, once per ramp |
| **3b. bias line** | medium, all in `dk_core.h` (≈ 300 lines with comments) | yes: bounded work, no lock, no allocation | yes, bit for bit (below) | **chosen** |

**WDF (context only, PR #93, open and unmerged).** `prototypes/wdf/RESULTS.md` on the branch
`cursor/wdf-12ax7-stage-4a41` measures a bias change at 39.7 µs for the WDF against 341 µs for the DK `swap_bias`, in
Python: 61 µs matrix build plus 268 µs DC solve. The same file reports that "a bias step from 1.0 to 1.5 at 20 ms into a
2 V sine gives the same output in both stages" (largest difference 2.9e-8 V). So a WDF swap would be cheaper, but it
would click just the same: the click is in the step of Rk, not in the solver. The bias line gets the cheap,
operating-point-free update inside the DK core. There, too, the deviation form (#70) makes the DC solve look necessary
when it is not (below). A WDF port is not the v1 fix, and nothing from #93 is used or changed here.

## The chosen approach: the bias line

`dk_core.h`, section "bias smoothing", `detail::BiasLine`, `detail::rereference`, `StageBase::set_bias`.

- **Matrices.** Bias changes only Rk, one resistor from the cathode to ground. It adds dg at S[k][k] of the MNA
  matrix. By Sherman–Morrison, every matrix A..K of a pack is affine in β(g) = dg / (1 + dg · Si(g0)[k][k]). So all
  packs lie on one line in matrix space, and two packs, built at prepare() at the range ends 0.5 and 2.0, give the
  exact matrices for any bias in between: M = M(0.5) + w (M(2.0) − M(0.5)), with w = β(g)/β(g(2.0)).
  - Measured against `build_coeffs` at nine biases: 7.9e-13 relative (`bias_line_matrix_rel`). That is inside
    `TOL_COEF` = 1e-11, the tolerance of the C++-vs-Python matrix check.
- **Operating point.** Not needed. The deviation form (#70) is exact for any reference currents i0, provided x0 is the
  matching fixed point of the linear part: x0 = (I − A)⁻¹ (B_HT·HT + C·i0), p_dc = G·x0 + H_HT·HT, and
  y_dc = D_out·x0 + E·HT + F·i0. Each update is one 6×6 LU with partial pivoting (three right-hand sides), so the
  system is exact whatever i0 is.
  - Check: the variant that also re-solved the operating point at every update gave the same output to every printed
    digit (scratch run, 18 Nov). The variant that interpolated it (3a) did not.
  - i0 only sets how far the state sits from x = 0. At the end of a ramp, one Newton solve of the DC problem
    re-centres i0 on the true operating point, so the stage comes to rest at x = 0 as after `prepare()`. The solve is
    at full HT, warm-started from the previous point (option 5).
  - Recentred operating point vs `build_coeffs`: 5.3e-8 V (`bias_line_op_abs`), inside the Newton tolerance
    tol_v = 1e-6 V.
- **Ramp.** `set_bias(b)` from any thread is a relaxed atomic, read once per block like the gains. The audio thread
  ramps linearly to it over `set_bias_ramp()` seconds (default 20 ms, as Drive / Output), one pack per base sample.
  It writes into one of two work slots and keeps the absolute history x0 + x, as a pack swap does. A new target
  re-plans the ramp from where it is. `reset()` jumps to the target. A pack adopted from `request_bias()` /
  `publish()` ends any ramp and takes over.
- **At rest after a move** (to 1.37, then `reset()`) against `prepare(1.37)`: 8.3e-7 V on a 2 V sine
  (`bias_rest_vs_prepared_abs`), inside `TOL_BUILT_ABS` = 1e-5 V, the matrix-rounding tolerance of the built packs.
- **Realtime.** Per base sample while moving: 130 multiply-adds for the matrices, the 6×6 LU and a few small products.
  Once per ramp: one `solve()`, capped at `newton.max_it` (50) iterations like any sample.
  - No `build_coeffs`, no mutex, no allocation (counted in both test programs: 0). Nothing at rest.
  - CPU, as an informal ratio only (no new benchmark): 0.167 µs per update, against the official M1 cost of one mono
    libm stage per base sample in [BENCHMARK.md](BENCHMARK.md): eco 1361 ns, standard 2553 ns, hq 5055 ns
    (0.5 V, b64). That is roughly +12 %, +7 % and +3 % while a ramp runs, and nothing otherwise. A StereoStage does
    one update for both channels.
  - Memory: `sizeof(Stage)` 27 496 → 32 480 bytes (two endpoint matrix sets and two work packs).
- **Wrapper.** `Engine::begin_chunk` passes the Bias target, clamped to `kBiasRange`, to the running stage. The core
  ramps it over the wrapper's `kRampSeconds` (20 ms). The worker no longer touches bias, so bias changes are
  deterministic and sample-aligned, also offline. Quality switching is unchanged: the idle slot is prepared at the
  current target.

## Done-when results

### Fast full-range moves, core (`test_core.cpp`, `test_bias_smoothing`)

Worst step against the continuous reference, with the unsmoothed swap in brackets. All 18 moves, 20 ms ramp:

| quality | move | silence | 1 V sine | 8 V sine |
|---|---|---|---|---|
| eco | 0.5 → 2.0 | 0.0956 mV (61.2 mV) | 0.223 mV (128 mV) | 1.86 mV (1.67 V) |
| eco | 2.0 → 0.5 | 0.0854 mV (108 mV) | 0.247 mV (306 mV) | 7.34 mV (7.35 V) |
| standard | 0.5 → 2.0 | 0.112 mV (61.7 mV) | 0.26 mV (127 mV) | 2.18 mV (1.67 V) |
| standard | 2.0 → 0.5 | 0.0986 mV (108 mV) | 0.289 mV (305 mV) | 8.68 mV (7.32 V) |
| hq | 0.5 → 2.0 | 0.119 mV (61.3 mV) | 0.278 mV (127 mV) | 2.34 mV (1.67 V) |
| hq | 2.0 → 0.5 | 0.106 mV (108 mV) | 0.31 mV (305 mV) | 9.3 mV (7.33 V) |

Stated limits, all met:

| limit | value | measured worst |
|---|---|---|
| `LIMIT_STEP_SILENCE` | 0.3 mV (−107 dBFS at Output 1) | 0.119 mV |
| `LIMIT_STEP_SINE` | 20 mV (−70 dBFS at Output 1) | 9.30 mV |
| `MIN_STEP_REDUCTION` (every move) | 200× | 458× |

- The 8 V output peaks at 140 V (`bias_move_out_peak_8V`), so 9.3 mV is −84 dB relative to the signal.
- The largest |y − ref| over all moves is 31 mV (`bias_move_maxdiff_vs_continuous`). That comes from the per-base-sample
  update grain, set against the 22–36 V of the move itself.

### Fast full-range moves, plugin (`plugin/tests/smoothing_test.cpp`)

These run through `mapvs::Engine` in stereo (L 220 Hz, R 330 Hz at 0.7×), standard, 48 kHz, host blocks of 64, with
host steps at sample 5120. The worst step is over both channels, in full-scale units at the plugin output. The
references:

- **Drive / Output:** the direct core with the ideal 20 ms per-sample ramp.
- **Bias:** the continuous reference above, per channel.

| move | silence | sine 0.9 FS | unsmoothed (instant jump / one swap), sine | limit |
|---|---|---|---|---|
| Drive 0 → 8 / 8 → 0 | 0 / 0 | 0 / 0 | 1.74 / 1.99 FS | 1e-6 FS |
| Output 0 → 4 / 4 → 0 | 0 / 0 | 0 / 0 | 2.89 / 2.89 FS | 1e-6 FS |
| Bias 0.5 → 2 / 2 → 0.5, Drive 1 | 1.75e-6 / 1.52e-6 FS | 2.98e-6 / 3.84e-6 FS | 1.40e-3 / 3.88e-3 FS | 4.69e-6 FS silence, 3.13e-4 FS sine |
| Bias 0.5 → 2 / 2 → 0.5, Drive 8 | 1.75e-6 / 1.52e-6 FS | 3.53e-5 / 1.16e-4 FS | 2.53e-2 / 0.108 FS | same |

- The Bias limits are the core limits through the 1/64 mapping.
- Drive and Output are bit-equal to the ideal per-sample ramp on this build. That is what the 20 ms wrapper ramps
  are; their limit covers float output rounding on other builds.
- Every move beats its unsmoothed jump by at least 470×. In silence, a gain has nothing to step.

### Block-size invariance (16 / 64 / 512)

- **A parameter step at a sample common to the three block sizes gives the same output bit for bit.** This holds for
  Bias alone in the core, and for Drive, Bias and Output together through the engine.
- **A host automation curve** is handed over once per block. The plugin test moves all three parameters linearly over
  0.1 s: Drive 1 → 6, Bias 0.5 → 2, Output 1 → 0.5. A block of 512 reads that curve up to 511 samples late. That is
  host sampling, not smoothing.
  - **Stated tolerance:** the block sizes may differ by no more than the lag does on its own. The lag bound is the
    block-16 run against the same run with the curve read 512 samples late.
  - Engine, all three parameters: max difference 0.0615 FS (5.3 % of the 1.16 FS peak), against a lag bound of
    0.177 FS (15.3 %).
  - Core, Bias alone, 2 V sine: 0.618 V (0.54 % of the 114.7 V peak), against a bound of 2.18 V.

### StereoStage

`stereo_bias_case` in `test_core.cpp` checks a StereoStage against two mono Stages under the same set_bias moves. The
moves are 0.5 → 2.0, then re-planned mid-ramp to 1.1. L is a 1 V 220 Hz sine, R an 8 V 330 Hz sine.

- It runs twice: libm per-lane, and fast maths on the vector lanes (#67).
- The rule is `compare_channel`'s: bit-equal on the exact builds, else `TOL_STEREO_ABS`. On this M1 (NEON) both are
  bit-equal (`stereo_exact` = `vector_exact` = 1).
- It also runs in every build of `test_stereo_parity`.
- The plugin test is stereo throughout.

### Drive smoothing and aliasing

The wrapper's Drive ramp (`Engine::process_chunk`, step 1) multiplies each host-rate input sample by the ramp value
before `StereoStage::process`, which then upsamples. So it already runs at the base rate before upsampling, as the
issue asks.

The plugin test compares it with the alternative: the same ramp applied per internal sample after the upsampler,
delayed by the interpolator's delay so that the two line up. Drive moves 1 → 8 on a 0.9 FS tone. The difference is
measured over a 4800-sample window around the move (periodic Hann, plain DFT), in dB relative to the output's energy:

| tone | difference in 20 Hz–20 kHz | 20–24 kHz | an unsmoothed jump instead, in band |
|---|---|---|---|
| 1037 Hz | −114.4 dB | −119.9 dB | −15.1 dB |
| 9037 Hz | −108.9 dB | −115.4 dB | −17.4 dB |
| 19037 Hz | −99.5 dB | −101.4 dB | −17.5 dB |

- **Limit:** −90 dB in band. That is 38 dB below the stage's own worst aliasing at standard 8 V (−51.7 dBc at
  19037 Hz, the measurement behind `dk/aliasing.py` LIMIT_DBC).
- The base-rate ramp is the more band-limited of the two: its product is filtered by the interpolator, while the
  internal-rate ramp's corners are not. Either way, the two placements differ by less than −99 dB in band.

## Limits and open points

- **The thump stays.** A full-range bias move within 20 ms moves the output by 22–36 V at the stage output
  (0.35–0.57 FS at Output 1) through the circuit's own coupling. That is physical, and present in the continuous
  reference. A longer bias ramp, or a slower host move, makes it smaller. That trade-off was not explored; 20 ms
  matches Drive / Output.
- The ramp has a fixed duration, whatever the distance moved, as the wrapper's gain ramps do.
- `process_internal()` (tests and analysis only) does not advance a ramp.
- Using `request_bias()` and `set_bias()` on the same stage is allowed but not useful: an adopted pack ends a ramp, and
  the next `set_bias()` value that differs from the last one seen ramps from there.
- Targets outside the line's range are clamped to it (`set_bias_range`, default 0.5–2.0). The core's reference vectors
  still cover only bias 1.0 and 1.5. The line's packs at other biases equal `build_coeffs` (above), and that is all
  that was checked.
- The limits were set from M1 runs. CI runs `test_core` on x86-64 Linux and macOS arm64, and the plugin test on macOS
  arm64 only. The sine limit has 2.15× margin.

## Reproduce

```bash
cd prototypes/fullstage
c++ -std=c++17 -O2 -pthread -o test_core cpp/test_core.cpp && ./test_core cpp/vectors.txt   # bias_* metrics in the JSON
c++ -std=c++17 -O2 -o bias_options cpp/bias_options.cpp && ./bias_options                 # the options tables
python -m pytest -q cpp/tests                                                             # incl. test_bias_options_smoke
cd ../.. && cmake --build plugin/build --target map_smoothing_test && plugin/build/bin/Release/map_smoothing_test
```
