# C++ 6AS6 core CPU (#135): fast maths, a lazier Jacobian, a two-lane stereo kernel

#135 measured three ways to make `cpp/dk6as6_core.h` cheaper: fast maths for the valve law, a lazier Jacobian and a
two-lane stereo kernel. Each option's speed and accuracy evidence are side by side below, with what was adopted.

## Summary

Speed is the default setting (P1, 0.45 mA, grid current, filter off, k140), block 64. **M1**: instructions per
base-rate sample, median of 11 passes. Instruction counts do not depend on load, unlike wall-clock and cycles.
**x86**: % of one core at 48 kHz on CI's runner (an AMD EPYC 9V74 VM, g++ 15.2 `-O3`), median of 5 passes. Ratios
are to main's two libm mono instances ("x2"), which is today's stereo cost.

| option | M1 instructions / sample, std 2 V (/ main) | x86 % core std 2 V mono / stereo | x86 % core hq 2 V mono / stereo | accuracy | CRIT (44 settings) | status |
|---|---|---|---|---|---|---|
| main (#104), libm | x1 57 758, x2 115 088 | — (other runner CPU) | — | Python bit-exact | pass | — |
| lazier Jacobian (libm) | x1 54 872 (0.95) | 32.4 / 64.8 | 57.5 / 114.0 | bit-identical to main; 0 of 191 411 640 skipped Jacobians non-finite | pass, = main | **adopted** (always on) |
| fast maths, mono (`LawMath::fast`) | x1 48 862 (0.85) | 27.6 / 55.3 | 49.2 / 98.0 | law ≤ 3e-12, output ≤ −205 dBc vs libm | pass, within libm's LIMITS | **available**, default libm |
| two-lane kernel, libm (s2) | 99 028 (0.86 of main x2) | — / 47.5 | — / 81.0 | bit-identical to 2 × mono (clang, SSE2 / NEON builds) | pass, = mono | **available** |
| two-lane kernel, fast vector (v2) | 59 690 (0.52 of main x2) | — / 36.9 | — / 63.3 | as fast mono, bit-identical to 2 × fast mono (clang) | pass, within LIMITS | **available**, recommended for the plugin |
| fast `atan` (table + polynomial) | +0 % cycles of the law (288 vs 290) | — | — | — | — | **rejected** (no gain) |
| reciprocal constants in the fast law | −7.5 % cycles of the law | — | — | ~1 ulp per division | — | **not adopted** (a second expression tree for ~5 %) |

On x86 the stereo path goes from 64.8 % to 36.9 % of a core at standard (0.57×) and from 114.0 % to 63.3 % at hq
(0.56×), so **hq stereo now fits in one core of this VM**. That VM is a different CPU from #104's EPYC 9V45 / 7763
runs, so its numbers cannot be compared with theirs. M1 instruction counts against main, from the same harness, are
in "Speed" below.

## What changed

**The lazier Jacobian (adopted, always on).** The valve law is now computed in two passes:
`detail::values2/3` produces the currents and keeps the intermediates, and `detail::jacobian2/3` computes the
gradients from them. Run back to back, the two passes are `pentode.currents(_grid)` operation for operation, so
nothing changes bit-wise: `test_bit_exact_law` / `test_bit_exact_solvers` against the live Python, and every vector.
The solvers evaluate a trial without its Jacobian and add it only where the next Newton step uses it: at the start,
and at an accepted trial that has not converged. The line search takes the Jacobian from the accepted point's own
values pass instead of evaluating the law again.

- **2-current solve:** it equals `solver.solve` bit for bit, since Python never reads a converged trial's Jacobian.
- **3-current solve:** it differs from Python in one case only. `solver._resid3_safe` rejects a trial whose values
  are finite but whose Jacobian is not. So a converging trial with a non-finite Jacobian fails there and converges
  here.
- **Measured** (`python -m cpp.fastlaw_6as6 domain`, a research probe compiled into the tests only): across the 44
  validation settings and the 192 deviation points below, 191 411 640 converging 3-current trials had their Jacobian
  skipped, with libm and with fast maths alike. **None of those Jacobians would have been non-finite**, so skipping
  them changed no result. The fuzzed solver tests, with wild and non-finite starts, also agree with Python bit for bit.
- The #112 restart contract is untouched. The skip is a no-op on everything measured, and every restart and
  fallback path is the same code.

**Fast maths (`Settings::math = LawMath::fast`, available; the core's default stays libm).** The law's `exp`,
`log1p` and `pow` use `dk/fastmath.h` (#18). Two identities replace Python's extra `pow` calls:
E1^(EX−1) = E1^EX / E1, and (Va / KVB)^2 = q·q. `atan` stays libm, because the measured fast `atan` gained nothing:
its two divisions cost what libm's `atan` does. The DC solves stay libm, as with `dk_core.h`'s `TriodeMath`. The
choice is made per pack and dispatched once per block.

**The two-lane kernel (`dk6as6::StereoStage`, `cpp/dk6as6_stereo.h`, available).** It runs left and right as the two
lanes of one 2 × double SIMD kernel on one pack, as `dk_core.h`'s StereoStage does for the 12AX7 (#21, #67):
- the law, both solvers and the tick are written on `V2` in the mono expression order;
- Newton runs per lane under masks, with the lazier Jacobian per lane;
- the #112 restarts are per lane: each start runs on the lanes that have not converged yet, so lanes diverge in
  restarts as well as in iterations;
- recovery, gains and the post-filter are per lane;
- lane maths: per-lane libm (the only libm option, as there is no vector libm), per-lane fast (the bit-exact
  reference) or vector fast (`VecFastMath`, the default with `LawMath::fast`).

The ~2× win comes from running two independent dependency chains at once. The law is latency-bound: divisions and
chains of transcendentals.

## Speed

Method: `python -m cpp.bench --core 6as6`, the #20 / #67 harness unchanged (`cpp/BENCHMARK.md`), with 120
configurations. #135 adds x1 / x2 fast, s2, s2 fast per-lane and v2 rows at the default setting for every quality,
peak and block. Each stereo row records whether its output is bit-identical to its x2 twin (`matches_x2`).

**Apple M1** (`bench_6as6_apple_m1_loaded_135.json`, 11 × 2 s, Apple clang 17 `-O3`). **Loaded, shared machine**:
load average 8.8 / 10.0 / 8.2 (1 / 5 / 15 min) before the run, 7.8 / 6.0 / 6.0 after, with other agent jobs running. Wall-clock and cycles are not quoted as timings; a clean M1 timing is #143 / PR #162.
Instructions per base-rate sample, block 64; main is `1124111`, same harness and flags, 5 × 2 s:

| setting | peak | main x1 | x1 libm (lazier J.) | x1 fast | main x2 | s2 libm | s2 fast per-lane | v2 fast |
|---|---|---|---|---|---|---|---|---|
| standard 8× | 0.5 V | 54 043 | 51 023 (0.94) | 45 632 (0.84) | 107 881 | 90 315 (0.84) | 78 288 (0.73) | 54 959 (0.51) |
| standard 8× | 2 V | 57 758 | 54 872 (0.95) | 48 862 (0.85) | 115 088 | 99 028 (0.86) | 85 296 (0.74) | 59 690 (0.52) |
| standard 8× | 8 V | 63 276 | 60 589 (0.96) | 53 687 (0.85) | 126 282 | 110 293 (0.87) | 94 421 (0.75) | 65 837 (0.52) |
| hq 16× | 0.5 V | 104 672 | 98 478 (0.94) | 88 278 (0.84) | 209 373 | 173 465 (0.83) | 150 855 (0.72) | 106 143 (0.51) |
| hq 16× | 2 V | 107 075 | 100 888 (0.94) | 90 321 (0.84) | 213 673 | 178 581 (0.84) | 154 979 (0.73) | 108 931 (0.51) |
| hq 16× | 8 V | 112 987 | 107 101 (0.95) | 95 546 (0.85) | 225 158 | 192 733 (0.86) | 166 456 (0.74) | 116 682 (0.52) |

(x1 columns / main x1, stereo columns / main x2, in brackets.) Every stereo row is bit-identical to its x2 twin
(`matches_x2`). In the same run, v2 took 0.50–0.51× the cycles of this branch's x2 libm, with every pass ≥ 96 % on
P-cores. That ratio is indicative only, because the machine was loaded.

**x86-64, CI** (`bench_6as6_x86_64_linux_ci_epyc9v74_135.json`, from `.github/workflows/bench-6as6.yml`: AMD EPYC
9V74 VM, g++ 15.2 `-O3`, 5 × 1 s, no hardware counters). % of one core at 48 kHz, median, block 64:

| setting | peak | x1 libm | x1 fast | x2 libm | x2 fast | s2 libm | s2 fast per-lane | v2 fast |
|---|---|---|---|---|---|---|---|---|
| standard 8× | 0.5 V | 29.2 | 25.3 | 58.1 | 49.8 | 41.6 | 36.2 | 32.5 (0.56) |
| standard 8× | 2 V | 32.4 | 27.6 | 64.8 | 55.3 | 47.5 | 41.2 | 36.9 (0.57) |
| standard 8× | 8 V | 35.1 | 30.0 | 70.2 | 60.2 | 50.8 | 44.0 | 39.5 (0.56) |
| hq 16× | 0.5 V | 55.2 | 47.3 | 110.5 | 94.5 | 76.8 | 67.5 | 62.3 (0.56) |
| hq 16× | 2 V | 57.5 | 49.2 | 114.0 | 98.0 | 81.0 | 70.7 | 63.3 (0.56) |
| hq 16× | 8 V | 62.6 | 54.2 | 125.0 | 111.7 | 91.4 | 79.8 | 71.4 (0.57) |

(v2 / x2 libm in brackets.) This branch's x1 / x2 libm rows already include the lazier Jacobian. On this runner the
stereo rows are not bit-identical to x2, because Ubuntu's g++ 15 targets FMA and contracts scalar and vector code
differently (`fma: true`). That is the documented "within tolerance" case (cpp/README.md, "Stereo"), checked by
`test_stereo_parity_6as6`.

**Where the time goes** (M1, `values3` per call, a 2 V trajectory, informal micro-benchmark). The law is ~70 % of the
stage. In libm, `pow` costs ~140 instructions, and the Jacobian law calls it 5 times (≈ 700 of 1 549 instructions).
`fm_pow` is ~117 instructions, ~30 of them constant loads, so fast maths cuts cycles (290 against 349 per values pass)
much more than it cuts instructions. Cycles are bounded by the dependency chain: divisions, then exp → log1p → pow →
exp. That is why the two-lane kernel, which runs two chains at once, pays the most.

## Accuracy evidence

**Fast maths: kernel bounds over the 6AS6's domain** (`test_fastlaw_kernel_bounds`). Relative error against libm,
on grids covering the recorded domain (`cpp/fastlaw_domain_6as6.json`: every argument `FastLaw` received in the 44
validation settings and the 192 deviation points; #18's method):

| kernel | recorded domain | measured | asserted |
|---|---|---|---|
| exp | x ∈ [−317 002, −1.3e−36]; below EXP_LO (−708.39) libm, bit-equal | 3.87e−13 | 1e−12 (#18) |
| log1p | e ∈ [0, 1] | 9.24e−14 | 3e−13 (#18) |
| pow, EX = 1.4905 | base ∈ [0, 5.27] (grid to 1e−300 … 6.6) | 5.17e−13 (3.93e−13 where \|EX log s\| ≤ 50) | 1e−12 |
| pow, XA = 0.5743 | base ∈ [8.3e−6, 1.66] | 3.88e−13 | 1e−12 |
| pow, XI = 1.314 | base ∈ [2.4e−56, 2.42] | 4.13e−13 | 1e−12 |

**Fast maths: the whole law** (`test_fastlaw_law_bounds`, 200 006 points in Vg1k −30…5, Vg2k −5…250, Vg3k −30…30,
Vak −10…450 V, plus the floors and edges). Each current is measured against its terms (#18's convention, as
`dk/fastmath.py` BOUND["currents"] for the triode):
- **grid law:** Ip = (Ik − Ig1)·f and Ig2 = (Ik − Ig1)·(1 − f), so they are measured against Ik + Ig1, with Ik the
  two-port law's Ip + Ig2 at the same point. Measured 1.17e−12, asserted 3e−12. This holds at every random point
  (Ig1 never underflows in the box); the edge rows below the cutoff (Vg1k = −800 V, all-zero voltages) are absolute.
  A plain relative error, or one against Ik alone, has no bound where Ig1 ≈ Ik or Ik ≪ Ig1. #135's review found a
  point with Ik = 2.4e−283 A and Ig1 ≫ Ik.
- **two-port law:** against Ik, measured 1.21e−12, asserted 3e−12.
- **Ig1:** relative, 1.01e−12 (3e−12).
- **Jacobian entries:** against their row's L1 norm, 8.4e−12 (3e−11).

1e−290 A is a test cutoff, not the subnormal range. Below it (the two-port law near E1^EX's underflow, where
E1^EX / E1 loses relative precision) the absolute error stays below 1e−290 A. In the stage, values below 2.2e−308
flush to 0 (FTZ / DAZ). Where Python would raise, the fast law flags the same points.

**Fast maths: the stage.** On every vector case (`test_core_6as6`): at most 3.46e−11 V from the libm stage
(3.7e−12 of the case's output peak), with the same Newton iteration sums, no allocation and bit-exact block-size
invariance. Deviation from the libm path in dBc (`python -m cpp.fastlaw_6as6 deviation` →
`results_fastlaw_6as6.json`): sines at 0.05 / 0.5 / 2 / 8 V and 100 Hz / 1 kHz / 15 kHz, every supported setting
plus P1 0.45 mA without grid current, standard and hq, 192 points. The worst point is **−205.4 dBc peak, −210.4 dBc
RMS, and the difference's largest spectral line −211.8 dBc** (3.55e−11 V). Mono fast and stereo vector give the same
numbers, and every point keeps libm's convergence and iterations.

**CRIT and DK-vs-ngspice** (`python -m cpp.validate_cpp_6as6 --math fast [--stereo]`, `test_validate_fast_6as6`).
With `--stereo`, the output and the internal Vg1k / Vg2k that the burst criteria use all come from the two-lane
kernel's left lane, one run (`StereoStage`'s per-lane `Record`):
dk6as6.validate on the C++ core reproduces every committed Python result within the libm path's `LIMITS`. All 44
settings ran (both models, every mode and reference current, standard and hq). Every dk/validate.CRIT criterion
reads as Python's, and every supported setting passes. The worst deviations are libm's own, to 3 digits: op 8.6e−12,
AC 3.8e−8 dB, H2..H5 9.6e−6 dB. Newton histograms differ by 4.3e−5 (limit 1e−3). Below CRIT's −90 dBc harmonic
floor, H5 at −177 dBc moves by 0.013 dB, an error component around −233 dBc. Records: `results_6as6_fast.json`,
`results_6as6_stereo.json` (libm, identical to the mono record) and `results_6as6_stereo_fast.json`. Since #135's
review these come from the stereo lane's own internal signals. They regenerated byte-identical, because those signals
equal mono's bit for bit (below).

**Aliasing** (`python -m cpp.aliasing_6as6 guard --math fast [--stereo]`; CI now runs the fast stereo guard as well):
at 48 and 44.1 kHz, all 140 guard rows pass the absolute gate and the per-tone ratchet against the existing libm
baselines. The worst per-tone delta is 2.7e−5 dB and the smallest margin 13.1 dB, mono fast and stereo vector alike,
with nothing non-converged. The baselines are only ever written from the libm mono path.

**Two-lane kernel parity** (`test_stereo_parity_6as6`, `test_core_6as6 stereo`). Each of the 45 vector cases runs on
the stereo kernel with left = the case and right = 0.6 × the case reversed, so the lanes diverge (129 of 135 runs
differ in iterations), against two mono Stages. This covers libm, per-lane fast and vector fast. The solvers alone
run on 32 400 fuzzed lanes; 5 427 pairs differ in iterations and 740 in restarts, with non-finite starts and
max_it 1…50. Each lane's internal record (Vg1k, Vg2k, Vak, Ig1, Ip + Ig2, iterations) equals its mono Stage's on all
135 case runs. The kernel's iteration count (`Solution2::kernel`, `kernel_iterations()`: per start the longer lane's,
summed) matches a per-start count from the mono solver on every pair, including 144 where the longer lane swaps
between starts. Per-lane recovery from ±DBL_MAX / NaN / an overflowing drive leaves the other lane untouched. Float
I/O and the FP mode are also covered. Every value the masked blends read is defined: `Law2`, `LawWork2` and
`Solution2` are value-initialised (#135 review).

| build | SIMD | stereo vs 2 × mono |
|---|---|---|
| Apple clang 17, M1: `-O2`, `-O3`, `-O2 -ffp-contract=off`, scalar `-ffp-contract=off` | neon / scalar | bit-exact (all lane maths) |
| Apple clang 17, `-arch x86_64` (Rosetta 2): `-O2`, scalar `-O2`, `-O2 -march=x86-64-v3` | sse2 / scalar / sse2 | bit-exact |
| Apple clang 17, M1: scalar `-O2` (contraction on) | scalar | within tolerance: 4.5e−13 V; solver lanes 27 % bit-equal, flags all equal, converged lanes 1.1e−13 |
| g++ 15 on CI (x86-64, FMA): `-O2`, `-O3` | sse2 | within tolerance (`TOL_PINNED_ABS`) |

No allocation in `process()`, and the FP mode is restored.

## Realtime

No new allocation, lock or syscall on the audio thread: the stereo kernel and the fast path are covered by the
`CountAllocs` checks. `ScopedFlushDenormals` is held as before. The research probes exist only in builds with
`-DDK6AS6_PROBES`: the test binary, never the plugin or the bench.

## Adopted, and what is Michael's call

- **Adopted in the core:** the lazier Jacobian, always on. It is bit-identical on everything measured.
- **Available, measured, not switched on:** `LawMath::fast` and `StereoStage`. The core's defaults are unchanged
  (libm, mono `Stage`), so Python parity and every existing record stand.
- **Recommendation for Gyre:** run the 6AS6 path as one `StereoStage` with `LawMath::fast` (vector lane maths). It
  costs 0.56–0.57× two libm mono stages on x86, and its output is within −205 dBc of libm with every gate passing.
  This touches the plugin engine, which PR #161 (#115, live bias and mode on `LiveStage`) rewrites for the 6AS6 path.
  So the wiring is a follow-up once #161 lands, and `LiveStage` will need the same stereo / fast treatment.

## Reproduce

From prototypes/fullstage:
- `python -m pytest -q cpp/tests/test_cpp_core_6as6.py`
- `python -m cpp.validate_cpp_6as6 --math fast` (and the same with `--stereo` added)
- `python -m cpp.aliasing_6as6 guard --fs 48000 --math fast --stereo`
- `python -m cpp.fastlaw_6as6 domain` (~3.5 min; it also writes the deviation record)
- `python -m cpp.bench --core 6as6 --out ...`
