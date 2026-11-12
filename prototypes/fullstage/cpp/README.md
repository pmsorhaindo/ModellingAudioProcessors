# cpp/ — header-only realtime C++ core of the 12AX7 DK stage (#19)

`dk_core.h` is a C++17, header-only, framework-free port of the validated Python stage in `../dk/`
(`stage.py`, `solver.py`, `circuit.py`, `triode.py`, and the default `"fir"` path of `resample.py`). The Python stage
stays the reference. Everything here is checked against it.

| file | what |
|---|---|
| `dk_core.h` | the core: `dkcore::Stage`, `StereoStage` (#21), `build_coeffs`, `solve` / `solve2`, `Triode` / `Triode2`, `Resampler`, `simd`, `ScopedFlushDenormals`, `TripleBuffer` |
| `test_core.cpp` | test harness: vector tests, allocation counting, FTZ, threaded swap, #7 fallback, stereo parity; plus `cli` / `solve` / `triode` / `swap` / `stereo` modes |
| `export_vectors.py` | writes `vectors.txt` from the Python stage (`python -m cpp.export_vectors`, ~3 s) |
| `vectors.txt` | the reference vectors (generated, exact shortest-repr floats, 365 kB) |
| `validate_cpp.py` | runs `dk.validate.run` on the C++ core and compares it to `dk/results.json`; writes `results.json` |
| `matrix_rounding.py` | the measurement behind the "built" tolerance below |
| `tests/test_cpp_core.py` | the pytest that CI runs (part of `python -m pytest -q` in `prototypes/fullstage`) |
| `results.json` | committed run of `validate_cpp` (6–7 Nov 2026, this lab VM) |
| `bench.cpp`, `bench.py`, `BENCHMARK.md` | the #20 per-instance CPU benchmark: harness, runner, method and results |
| `build.py` | the compile helper shared by `validate_cpp.py`, `bench.py` and the tests |

## What the core is

* **Fixed-size state, as in `Stage`.** It holds the 6 capacitor history states `x`, the 2 Newton unknowns (Vgk, Vak)
  and the previous solution for the #15 predictor. The matrices are fixed-size, as in `Stage.m`: A 6×6, B 6×2, C 6×2,
  D 5×6, E 5×2, F 5×2, G 2×6, H 2×2, K 2×2. They live in one trivially copyable `Coeffs` pack, together with the DC
  operating point, x0, the triode parameters, the Newton settings and the math switch. No `std::vector`, no `new`,
  no exceptions in the core. `sizeof(Stage)` is 27 kB, most of it the FIR state; `sizeof(Coeffs)` is 1.3 kB.
* **Quality settings.** `HQ` 16×, `STANDARD` 8× (default) and `ECO` 4× with the 17.3 kHz small-cap pre-warp are
  `stage.QUALITY` (tested). The core owns the oversampling: the same streaming half-band FIR cascade (127 / 23 taps,
  Kaiser β 9, polyphase), the same pad delay, latency (72 base samples at 8×) and decimator priming.
  `filt="iir"` is **not ported**: the Python stage stays the reference for the IIR resampler.
* **Same arithmetic as Python.** The triode, the solver (pnjlim, line search, best iterate) and the resampler's
  transposed direct form II (scipy `lfilter` order) do the same operations in the same order. In a build without
  FMA contraction, `solve()` and the triode currents are bit-identical to the Python functions (tested against live
  Python, below).
* **Math path.** The default is `TriodeMath::libm`, as in Python. `TriodeMath::fast` uses `../dk/fastmath.h` (#18).
  It is chosen per pack and dispatched once per block, so the libm path has no per-sample switch. The DC solve
  always uses libm. **The default stays libm**: in the full `process()` here, libm time / fast time is
  0.94–1.21× (standard 0.96–1.06×, the informal #19 run below). That is no measured justification to switch.
* **Do not build with `-ffast-math`.** The header `#error`s, because the fallback compares NaN / inf.

### Realtime protocol

| call | thread | notes |
|---|---|---|
| `prepare(fs, quality, bias, math, newton)` | non-RT, never concurrently with `process()` (a host's prepare call) | builds the pack, designs the FIR, resets |
| `request_bias(b)` / `publish(coeffs)` | any non-RT thread | builds a new pack (matrices + DC operating point) off the audio thread, then publishes it lock-free |
| `process(in, out, n)` (double or float), `process_internal`, `reset()` | the audio thread | noexcept, no allocation, no locks, wait-free |
| `set_drive(g)` / `set_output(g)` / `nonconverged_total()` | any thread | relaxed atomics, read once per block |

**Coefficient swap.** `TripleBuffer<Coeffs>` is a single-producer / single-consumer triple buffer with three slots
inside the `Stage` object:
1. The producer fills its back slot and publishes it with one `exchange` (acq_rel) on an atomic index that has a
   "fresh" bit.
2. At the start of each block, `process()` checks the bit. If it is set, `process()` takes the new pack with one
   `exchange` and counts it (`Stats::swaps`).
3. No slot is written while the audio thread can read it. Nothing is allocated or freed on either side. If the
   producer publishes twice before the next block, the audio thread gets the newer pack.

A `std::mutex` serialises producers only; the audio thread never touches it. A pack whose build fails is never
published (`BuildStatus::dc_nonconvergence`, `bad_argument`, `singular_matrix`), and a pack for another internal rate
is refused. On a swap, the state x and v is **kept, not reset**. Bias changes only Rk, so the companion
conductances, and with them the meaning of x, are unchanged: the stage moves from its present state to the new
operating point, as a real pot would. `reset()` goes to the active pack's operating point. This is the same as the
Python reference for a swap (`export_vectors._run_case`: replace `m` and `K`, keep `x` and `v`). Drive, output and
bias are not smoothed (zipper noise is possible on fast moves), as in the Python prototype.

**Denormals.** `process()` and `process_internal()` hold a `ScopedFlushDenormals`. On x86 it sets MXCSR FTZ (bit 15)
and DAZ (bit 6); on AArch64, FPCR.FZ (bit 24). On scope exit it restores the caller's mode (RESEARCH-numerics.md c).
Elsewhere it is a no-op, and `active()` is false.

**Non-convergence (#7).** `solve()` carries `dk/solver.py`'s contract and comments. At the iteration cap it returns
the best finite iterate, i.e. the one with the smallest finite ‖F‖∞ (the warm start counts), flagged. A NaN / inf
iterate never wins. The stage keeps that iterate as the sample's state and counts it in `Stats::nonconverged`,
`Stats::nonconv_block` (last call) and `nonconverged_total()` (atomic, for the UI thread). Python warns on each
call; the C++ core only counts, and the host polls the counters. The DC solve has no state to fall back on, so it
fails the build instead. The core adds one rule of its own: NaN / inf input samples are replaced by 0 and counted
(`nonfinite_inputs`). Python has no such guard.

## Stereo (#21)

`dkcore::StereoStage` processes left and right as the two lanes of one 2 × double SIMD kernel. Both channels use
one shared coefficient pack, since the matrices are the same for both. Drive and output gain are per channel
(`set_drive(l, r)`, `set_output(l, r)`; the one-argument forms set both). The API, threads and realtime rules are
`Stage`'s:
- `prepare` / `request_bias` / `publish` / `latest` act on both channels. That plumbing now lives in
  `detail::StageBase`, shared by both classes.
- `process(inL, inR, outL, outR, n)`, double or float. No allocation, locks or syscalls.
- The same `ScopedFlushDenormals`.
- NaN / inf inputs are replaced and counted per channel.
- `stats(ch)` gives the per-channel `Stats`. `nonconverged_total()` counts both channels.
- `sizeof(StereoStage)` is 45 kB; two `Stage`s take 55 kB.

**The wrapper (`dkcore::simd`).** `V2` holds the values and `M2` the per-lane masks. There are three versions:
- **AArch64:** NEON `float64x2_t`.
- **x86 (GCC / clang):** SSE2 `__m128d`. No AVX: two lanes are all the problem has, and AVX's 4 lanes would need
  two stereo pairs.
- **Elsewhere, or with `-DDKCORE_SIMD_SCALAR`:** a struct of two doubles, which any C++17 compiler builds.

The native types keep `+ - * /` as built-in operators. Only the masks, select, abs and compares are wrapper
functions over intrinsics.

**Each lane does exactly the mono operations, in the mono order.** `Triode2`, `solve2` and `StereoStage::tick` are
`Triode`, `solve` and `Stage::tick` written with `V2` for `double`: the same expression trees, with branches turned
into per-lane selects. The FIR resampler is the same template (`ResamplerT<double>` is the mono `Resampler`,
`ResamplerT<V2>` the stereo one).
- exp / log1p / pow (libm or `dk/fastmath.h`) and pnjlim run per lane, as scalar calls to the very same functions.
  No vector libm gives libm's bits, and fastmath stays untouched.
- The SIMD work is therefore everything else: the matrices, the Jacobian and Newton step, the residuals, the line
  search arithmetic and the resampler.

**Per-lane Newton.** The lanes may need different iteration counts, and each must stop where mono stops. Both lanes
iterate in lock step under an "active" mask:
- When a lane converges, its iteration count is recorded and the lane is **frozen**: every later update to it is
  `select(active, new, old)`.
- The line search is masked the same way: a lane leaves the halving loop when its own trial is accepted.
- The loop ends when both lanes have converged, or at the cap. A lane still active at the cap returns its best
  iterate, flagged (#7), as mono does.

So each lane's result, iteration count and flag are mono's, and the per-channel `Stats` are identical. The price
is that a sample costs max(itL, itR) iterations of both lanes. Freezing was chosen over running each lane to its own
count in scalar code because it keeps one code path and no per-lane branches. What it costs is for the benchmark to
say (BENCHMARK.md, PENDING).

**Exactness.** Stereo equals two mono `Stage`s bit for bit wherever the build contracts (FMA) the scalar and the
vector code at the same places:
- **No contraction at all:** `-ffp-contract=off`, or x86-64 without `-march`, where SSE2 has no FMA.
- **clang's per-expression contraction on the native types.** It fuses `a*b + c` in a vector expression exactly
  where it fuses it in the same scalar expression. Checked on Apple clang 17: scalar `fmul`+`fmadd`, vector
  `fmul.2d`+`fmla.2d`.

It is not guaranteed in two cases:
- **The struct fallback with contraction on.** Its operators are functions, so nothing is fused, while mono is.
- **GCC's default `-ffp-contract=fast` with FMA available** (e.g. `-march=x86-64-v3`). GCC fuses after
  optimisation, and the scalar and vector code can come out differently.

Then the arithmetic is the same and only the rounding differs. That is the situation `TOL_PINNED_ABS` already
bounds between builds (same pack, same per-sample code: 1e-9 V, measured 2.7e-11 V), so it is the stereo
tolerance too, with `TOL_ITER_FRAC` for the Newton statistics.

| build (measured 12 Nov 2026) | SIMD | stereo vs 2 × mono |
|---|---|---|
| Apple clang 17, M1: `-O2`, `-O3`, `-O2 -ffp-contract=off` | neon | bit-exact |
| Apple clang 17, M1: `-O2 -ffp-contract=off -DDKCORE_SIMD_SCALAR` | scalar | bit-exact |
| Apple clang 17, M1: `-O2 -DDKCORE_SIMD_SCALAR` (contraction on) | scalar | 3.6e-12 V worst; solver lanes 69 % bit-equal, 8.9e-16 worst, iteration counts all equal |
| Apple clang 17, `-arch x86_64` under Rosetta 2: `-O2`, `-O2 -DDKCORE_SIMD_SCALAR`, `-O2 -march=x86-64-v3` | sse2 / scalar / sse2 | bit-exact |
| GCC on ubuntu x86-64 (CI) | sse2 / scalar | asserted bit-exact except `-march=x86-64-v3` (tolerance) |

Mono output is unchanged by #21. A bit hash of mono `process()` output, internal Vgk, float I/O, state and stats
over 60 configurations, plus the resampler alone, is identical to main at `-O0`, `-O2`, `-O3` and
`-O2 -ffp-contract=off` (scratch check, not committed).

**Parity tests** (`test_core stereo`, also part of the full `test_core` run). Each check compares one `StereoStage`
with two independent mono `Stage`s: output, per-channel `Stats` (iterations, histogram, non-converged, swaps,
non-finite inputs) and final state.
- Every vectors case (all three settings, libm and fast, bias 1.5, the live bias swap, forced non-convergence, drive
  and output) runs three ways:
  - L = R;
  - L ≠ R: R is the left input reversed, plus a tone that gets loud halfway, with NaN / inf samples;
  - L ≠ R with R's drive × 2.5 and output × 0.4, in ragged blocks of 37.

  That is 42 runs. In 29 of them the two lanes' iteration histograms differ, so the masking is exercised.
- A capped Newton (3 iterations): the loud lane falls back (#7) while the quiet one converges.
- `solve2` against `solve`, lane by lane: the 146 vector problems (pnjlim, line search, caps) plus 4000 random hard
  problems, paired, 8292 lanes in total. libm and fast math.
- Float I/O, in place, an unprepared stage, DC non-convergence, MXCSR restore (x86), and 0 allocations.

The output prints `stereo_exact`. `test_stereo_parity` (pytest) runs it in six builds:
- SIMD and forced scalar, with and without `-ffp-contract=off`;
- `-O3`, the bench flags;
- `-march=x86-64-v3` on x86.

It asserts the SIMD path in use (neon / sse2 / scalar), and `stereo_exact == 1` wherever exactness is guaranteed
(above). Mutation check (by hand, 12 Nov 2026):
- Caught: halving lanes that never leave the line search, and converged lanes that stay active. Both fail the
  tolerance.
- Caught by `stereo_exact` only: no freeze of a converged lane's v. The lane moves by less than tol_v.
- Not caught, and removed: an `act &` mask on the best-iterate update. Dead logic: best is only read for lanes that
  are still active at the cap.

## Tests and tolerances

`python -m pytest -q cpp/tests` (from `prototypes/fullstage`, ~40 s, 18 tests) compiles `test_core.cpp` with `$CXX`,
else `g++` / `c++` / `clang++`. Without a compiler it skips, except under CI (`CI` set), where it fails. GitHub's
ubuntu runners have g++, so the existing `test (fullstage)` job runs these tests with **no `ci.yml` change**.

| test | what it proves |
|---|---|
| `test_core_vectors` (`-O2`) | every check in `test_core.cpp` against `vectors.txt` (below), plus allocation, FTZ, swap, fallback, block-size invariance, float I/O |
| `test_core_vectors_fma` | the same with `-O2 -march=x86-64-v3` (FMA contraction; x86-64 only) |
| `test_swap_tsan` | the threaded swap test under `-fsanitize=thread`: no race reported (skips only if TSan cannot start) |
| `test_bit_exact_solver` / `_triode` | `solve()` on the vector rows + 2000 random hard problems, and the currents (libm, currents_only, fast) on 3000 random points, equal **bit for bit** to the live Python functions on the same machine (`-O2 -ffp-contract=off`) |
| `test_vectors_are_fresh` | the committed `vectors.txt` still matches what the Python stage produces now |
| `test_validate_reproduced[hq/standard/eco]` | `dk.validate.run` on the C++ core reproduces `dk/results.json` within `validate_cpp.LIMITS`, and every `pass_vs_dz` criterion holds |
| `test_stereo_parity[...]` | `StereoStage` = two mono `Stage`s (section "Stereo"), six builds; bit-exact where guaranteed |
| `test_bench_smoke` / `test_bench_records` | `bench.cpp` (#20, #21) builds and runs all 108 configurations once on 512 samples, with bit-identical output across passes and s2 = x2 on exact builds (no timing checked); every committed `bench_*.json` is complete (the #20 72-row or the #21 108-row schema) |

**Vectors** (`export_vectors.py`; tolerances in `test_core.cpp`). The tolerances were set from the worst error measured
over four x86-64 builds: gcc 14 `-O0`, `-O2`, `-O2 -march=x86-64-v3` and `-O3 -march=native`. A fifth run nudged
every libm result by ±1 ulp, to stand in for another libm, and did not change the stage numbers.

| vectors | metric | measured worst | tolerance |
|---|---|---|---|
| FIR taps (127, 23) | abs | 1.1e-16 (1 ulp) | 1e-14 |
| matrices A..K, op point, nodes, x0 (4 packs) | max \|ΔM\| / max \|M\| | 9.4e-13 | 1e-11 |
| triode currents, 60 points | per column / its max | 0 (bit-equal) | 1e-13 |
| `solve()`, 146 problems incl. pnjlim / 5-halving edge cases | V; currents relative | 0 (bit-equal; FMA build 3.2e-15) | 1e-12 |
| resampler up / down, os 4 / 8 / 16 | / max \|out\| | 1.1e-15 | 1e-13 |
| stage on the **Python pack** ("pinned"), 14 cases + internal | abs V | 2.7e-11 | 1e-9 |
| stage on the **C++-built pack** ("built"), the same | abs V | 7.7e-7 | 1e-5 |
| Newton iteration sums / histograms | fraction | 0 (identical) | 1e-3 |

The 14 cases cover:
- all three settings;
- sines at 0.05 / 2 / 4 / 8 V, a 4 V burst, uniform noise, and drive 2.5 with output 0.4;
- bias 1.5, and a live bias swap 1 → 1.5 at sample 240;
- fast math;
- forced non-convergence (1 iteration, zero tolerance) for 240 samples, then the defaults.

Each case is 10–20 ms.

*Why "built" needs 1e-5 V.* The C++ 10×10 inverse (Gauss–Jordan) and numpy's LAPACK round the matrices differently,
by ~1e-13 relative. The "out" rows of D and F hold entries of ~7.5×10⁴ that cancel to volts, and the slow
coupling-cap modes integrate any difference. `python -m cpp.matrix_rounding` measures this at standard over the
10 ms vectors:
- **Python vs Python with the exact matrices**: 2.5×10⁻⁷ V. This is Python's own rounding floor. The exact matrices
  are built with `fractions.Fraction` from the same double inputs.
- **C++ vs Python**: 3.2×10⁻⁷ V.
- **C++ vs Python with the exact matrices**: 6.9×10⁻⁸ V.

The C++ matrices are as close to exact as numpy's, or closer (A: 6.5e-14 vs numpy 2.3e-13). With Python's matrices
loaded ("pinned"), the per-sample code agrees to 2.7×10⁻¹¹ V. So the pinned tolerance (1e-9 V) is the one that
catches a change in the per-sample code. The built tolerance (1e-5 V, i.e. −140 dB re 100 V) checks the whole core
end to end.

**`dk/results.json` reproduced** (`validate_cpp.py`, `results.json`). Every time-domain number comes from the C++
`process()`: H1–H5, THD, burst, Newton iterations. The small-signal numbers come from the C++ matrices and operating
point; the analysis code (`dk/linear.py`, the resampler's analytical response) stays Python. Worst |C++ − Python|
over hq / standard / eco:

| quantity | measured | limit | CRIT it belongs to |
|---|---|---|---|
| operating point (relative) | 0 | 1e-9 | 1e-3 |
| AC deviations (all reported numbers) | 8.8e-9 dB | 1e-6 dB | 0.1 dB |
| H2–H5 above −90 dBc | 5.5e-8 dB | 1e-5 dB | 0.1 dB |
| H1 amplitude / THD (relative) | 7.1e-10 / 3.0e-9 | 1e-7 / 1e-6 | — |
| burst Vgk rest / min / trajectory | 5.1e-9 V | 1e-6 V | 0.5 % / 1 % / 0.5 % |
| burst t_min, recovery τ | 0 | 1e-9 s / 1e-6 | — |
| Newton histograms (every run) | identical | 1e-4 | — |

Every `pass_vs_dz` criterion holds for the C++ core in all three settings. Harmonics below the −90 dBc test floor
agree to 7.2e-5 dB (reported, not asserted).

**Allocation.** `test_core.cpp` replaces global `operator new` / `delete` in every form. On glibc it also interposes
`malloc` / `calloc` / `realloc` / `free` / `memalign` / `aligned_alloc` / `posix_memalign`, forwarding to glibc's
`__libc_*` entry points. Both count while a flag is set, and every `process()` / `process_internal()` call in the
tests runs with the flag set: 0 allocations over all cases, the NaN-input block and float I/O. `request_bias()`
(the producer side) also allocates nothing.

**Mutation check** (by hand, 6 Nov 2026; each a one-line change to `dk_core.h`). Caught:
- Newton tol_v 1e-6 → 1e-5 (iteration counts);
- no predictor;
- Kaiser β 9 → 8.9;
- Rgs 68 → 68.1 kΩ;
- the eco pre-warp constant 17300 → 17000 Hz;
- returning the last iterate instead of the best (NaN test);
- LS_MAX 10 → 5 (solver vectors);
- pnjlim threshold 2 VT → 3 VT (`test_bit_exact_solver` only: it changes the Newton path, not the root).

Not caught, and harmless: the DC start Vak 200 → 150 V, which converges to the same operating point.

**Portability.** It runs and is tested only on x86-64 Linux (glibc). With clang + libc++ (zig 0.17.1), `-Wall -Wextra
-pedantic` compiles clean for x86_64-linux, aarch64-linux, aarch64-macos and x86_64-windows. The AArch64 objects
contain the FPCR read, set and restore. None of those targets were run.

## Speed: the informal #19 run (not the #20 benchmark)

The per-instance benchmark of #20 is [`BENCHMARK.md`](BENCHMARK.md) (`bench.cpp`, `bench.py`). The numbers below
are the earlier informal run, kept for history: the `test_core bench` mode, which #20 removed together with its
`bench.json` (both in git at 70ceacc). They are not comparable with `BENCHMARK.md`, whose signal, flags and method
differ.

`test_core bench`: `process()` on 1 s of a 1 kHz sine at 48 kHz, blocks of 64, median of 7 runs.
gcc 14.4 `-O2`, one core of the lab VM (Intel Xeon, shared; load average 0.3–0.8). The range is over three runs on
6–7 Nov 2026; the committed JSON is the last run.

| setting | libm, ns / base sample | realtime × (one channel) | libm time / fast time |
|---|---|---|---|
| hq 16× | 5610–7060 | 3.0–3.7 | 0.94–1.21× |
| standard 8× | 2980–3640 | 5.7–7.0 | 0.96–1.06× |
| eco 4× | 1550–2080 | 10–13.5 | 1.00–1.09× |

At standard, the stage alone (Newton + matrices) is ~3.0 µs of ~3.3 µs per base sample, ~375 ns per internal sample
at 2.29 iterations. The resampler is ~0.24 µs, ~7 %: it still multiplies the half-band's ~1e-17 "zero" taps, so the
arithmetic stays the Python order. The Python stage takes 6.4 s per second of audio at standard (dk/RESULTS.md);
the C++ core takes 0.14–0.18 s (about 40× less).

## Limitations

- FIR resampler only; `filt="iir"` stays Python-only.
- os ∈ {2, 4, 8, 16} (fixed buffers). The vectors and the CLI are at 48 kHz. `prepare()` accepts any base rate, but
  only 48 kHz is tested.
- One triode parameter set, one circuit (`Values` = `circuit.VALUES`). pnjlim's VT is `1/par.Cg`. Python uses the
  module constant, which is the same for that set.
- Double precision inside. The float overloads convert per sample. Stereo is `StereoStage` (#21): 2 lanes, with
  per-lane scalar transcendentals. Its cost is not measured yet (BENCHMARK.md, PENDING). No plugin wrapper (#22).
- The NEON path was run only with Apple clang, and SSE2 only with Apple clang under Rosetta 2 here, plus GCC on CI.
  GCC on AArch64 and MSVC (which gets the struct fallback) were not built.
- No smoothing of drive / output / bias, as in Python. A bias swap jumps the matrices at a block boundary.
- Allocation hooks are glibc-specific; elsewhere only `operator new` / `delete` are counted.
- TSan, FMA and every run were on x86-64 Linux only. AArch64 / macOS / Windows were compile-checked, not run.
  Since then (11 Nov 2026, #20) `pytest cpp/tests` was run once on an Apple M1, macOS 15.6.1, Apple clang 17. Every
  test passed, including the bit-exact and TSan ones, except one check in `test_core_vectors`: "ScopedFlushDenormals
  flushes in scope and restores". It fails the same way at 70ceacc (before #20) and is not investigated here.

## Build and run

```bash
cd prototypes/fullstage
python -m pytest -q cpp/tests                     # everything above (~20 s); CXX=... to pick the compiler
python -m cpp.export_vectors                      # regenerate cpp/vectors.txt after a change to the Python stage
python -m cpp.validate_cpp                        # dk.validate on the core -> cpp/results.json (~7 s)
python -m cpp.matrix_rounding                     # the matrix-rounding measurement (~6 s)
g++ -std=c++17 -O2 -Wall -Wextra -pedantic -pthread -o test_core cpp/test_core.cpp
./test_core cpp/vectors.txt                       # one JSON line of measured errors; exit 1 on any failure
python3 -m cpp.bench --help                       # the #20 CPU benchmark (BENCHMARK.md; official runs only as described there)
```
Using the core: `#include "dk_core.h"` (it includes `../dk/fastmath.h`), then:
```cpp
dkcore::Stage s;
s.prepare(48000.0);           // standard 8x, bias 1, libm
s.process(in, out, n);        // audio thread
s.request_bias(1.5);          // any non-RT thread

dkcore::StereoStage st;       // #21: L and R in one SIMD kernel, the same results as two Stages
st.prepare(48000.0);
st.set_drive(1.0, 1.0);       // per channel (or set_drive(g) for both)
st.process(inL, inR, outL, outR, n);
```
