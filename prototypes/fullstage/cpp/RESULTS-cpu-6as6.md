# C++ 6AS6 core CPU (#135): fast maths, a lazier Jacobian, a two-lane stereo kernel

#135 measured three ways to make `cpp/dk6as6_core.h` cheaper: fast maths for the valve law, a lazier Jacobian and a
two-lane stereo kernel. Each option's speed and accuracy evidence are side by side below, with what was adopted.

## Summary

Speed is the default setting (P1, 0.45 mA, grid current, filter off, k140), block 64, 2 V.
- **M1**: instructions per base-rate sample, median of 11 passes. Instruction counts do not depend on load, unlike
  wall-clock and cycles.
- **x86**: CI's runner, a VM whose CPU varies from run to run (`.github/workflows/bench-6as6.yml`, g++ 15.2 `-O3`,
  5 passes). Every ratio is against main's x1 / x2 rows measured **in the same job**, before and after (the paired
  control below), so ratios from different CPUs can be compared but absolute numbers cannot.
- "x2" is two libm mono instances, today's stereo cost.

| option | M1 instr., std (vs main) | x86 vs main on the same runner (Intel Xeon 6975P-C / 8573C; AMD EPYC 9V74) | accuracy | CRIT (44 settings) | status |
|---|---|---|---|---|---|
| main (#104), libm | x1 57 758, x2 115 088 | 1 | Python bit-exact | pass | — |
| libm path, this PR | x1 57 195 (0.99) | core 0.99–1.01 (all three) | Python bit-exact (#104's code) | = main | unchanged |
| **fast maths, mono** (`LawMath::fast`) | x1 45 076 (0.78) | 0.81–0.83 / 0.82–0.83; 0.84–0.86 | kernels ≤ 5.2e−13, law ≤ 1.2e−12 (currents) / 8.4e−12 (Jacobian), output ≤ −205 dBc vs libm | pass, within libm's `LIMITS` | **available**, default libm |
| **two-lane kernel, libm** (s2) | 105 377 (0.92 of x2) | 0.78–0.82 / 0.79–0.82; 0.75–0.79 of x2 | bit-identical to 2 × mono (clang, NEON / SSE2) | = mono | **available** |
| **two-lane kernel, fast vector** (v2) | 61 705 (0.54 of x2) | 0.58–0.61 / 0.59–0.61; 0.60–0.63 of x2 | as fast mono; bit-identical to 2 × fast mono (clang) | pass, within `LIMITS` | **available, recommended for Gyre** |
| lazier Jacobian | −0.4 to −0.7 % (fast mono) | lazy / eager 0.997–1.008 / 1.000–1.002; 1.000–1.009 | changes no measured result (0 of 191 411 640 skipped Jacobians non-finite) | = eager | **not adopted**: research switch `-DDK6AS6_LAZY_JACOBIAN` |
| two-pass law on the libm path | 0.95 | 0.98 (8573C); **1.11–1.15** (AMD 9V74, 7763) | bit-exact | = main | **not adopted** (libm keeps #104's code) |
| fast `atan` (table + polynomial) | +0 % of the law's cycles | — | — | — | **rejected** |
| reciprocal constants in the fast law | −7.5 % of the law's cycles | — | ~1 ulp per division | — | **not adopted** (a second expression tree for ~5 %) |

On x86, hq stereo at 2 V goes from 107.3 % to 65.3 % of a core on the EPYC 9V74 run, 95.5 % to 56.7 % on the Xeon
6975P-C, and 126.8 % to 80.8 % on the EPYC 7763: **hq stereo fits in one core on all three** (main's did not on two).

## What changed

**Fast maths (`Settings::math = LawMath::fast`, available; the core's default stays libm).** The law's `exp`,
`log1p` and `pow` use `dk/fastmath.h` (#18). Two identities replace Python's extra `pow` calls:
E1^(EX−1) = E1^EX / E1, and (Va / KVB)^2 = q·q. `atan` stays libm, because the measured fast `atan` gained nothing:
its two divisions cost what libm's `atan` does. The DC solves stay libm, as with `dk_core.h`'s `TriodeMath`. The
choice is made per pack and dispatched once per block. The fast law is computed in two passes (namespace `twopass`):
the values pass computes the currents and the gradients' parts that need no `pow`, and the Jacobian pass adds
E1^(EX−1), (Va/KVB)^2 and the arithmetic after them. The two passes run back to back, so the result is the same as
one pass.

**The libm path is #104's code (namespace `fused`), unchanged.** #135 first moved it to the two-pass law too. That
was bit-exact and cheaper on the M1 (0.95× the instructions) and on an Intel Xeon 8573C (0.98×). But on CI's AMD
EPYC 9V74 and 7763 runners it took 1.11–1.15× main's time in five paired runs, with fewer instructions executed
(callgrind: `solve_grid` 155M against 171M, libm `pow` 130M against 144M) and with the eager and the lazy Jacobian
alike. Forcing inline (GCC had run out of its per-unit inlining budget: 188 misses, 0 on main), computing the values
pass in locals, and moving the Jacobian's pow-free parts into the values pass all left it at 1.12–1.15× on AMD. The
cause was not found, and the plugin's path is libm, so that path went back to #104's code. On every CPU sampled it
now measures 0.99–1.01× main with main's harness. The forced inlining (`DK6AS6_HOT`, `FM_INLINE`) is kept: it
changes no arithmetic and removes the inlining misses. Note that this PR's own bench harness reads ~2 % slower than
main's on every mono row (its translation unit holds the stereo kernel too), so the ratios above that use it are
conservative.

**The lazier Jacobian (not adopted; `-DDK6AS6_LAZY_JACOBIAN`).** solver.py evaluates the law with its Jacobian at
every accepted trial, the converging one included, whose Jacobian is never used. With the switch, the twopass solvers
add the Jacobian only where the next Newton step uses it.
- **Effect on results:** none measured. The 2-current solve equals `solver.solve` bit for bit either way. The
  3-current solve could differ in one case: `solver._resid3_safe` rejects a converging trial whose values are finite
  but whose Jacobian is not. Across the 44 validation settings and the 192 deviation points below, 191 411 640
  converging trials had their Jacobian skipped and **none of those Jacobians would have been non-finite**
  (`python -m cpp.fastlaw_6as6 domain`, with a research probe compiled into the tests only).
  `test_bit_exact_solvers` passes against the live Python solvers in both builds.
- **Speed:** the paired control (below) puts lazy / eager at 0.997–1.008 and 1.000–1.002 on the Intel runners and
  1.000–1.009 on the AMD one (fast rows), and at −0.4 to −0.7 % of the instructions on the M1. That is no
  measurable gain, so by #135's rule it is not adopted: by default every path evaluates the Jacobian at every
  accepted trial, as solver.py does, with `_resid3_safe`'s check there too. Across two separate runs on the same
  Intel model, the two-lane libm kernel was 3–4 % faster lazy than eager (v2 ~1.5 %). That is indicative only (not
  a paired control) and is noted for a later decision on the stereo kernel.
- The #112 restart contract is unchanged on every path.

**The two-lane kernel (`dk6as6::StereoStage`, `cpp/dk6as6_stereo.h`, available).** It runs left and right as the two
lanes of one 2 × double SIMD kernel on one pack, as `dk_core.h`'s StereoStage does for the 12AX7 (#21, #67):
- the twopass law, both solvers and the tick are written on `V2` in the mono expression order;
- Newton runs per lane under masks; the Jacobian follows the mono solvers' switch;
- the #112 restarts are per lane: each start runs on the lanes that have not converged yet, so lanes diverge in
  restarts as well as in iterations. `Solution2::kernel` / `kernel_iterations()` count, per start, the longer lane's
  iterations, summed;
- recovery, gains, the post-filter and the internal `Record` are per lane;
- lane maths: per-lane libm (there is no vector libm), per-lane fast (the bit-exact reference) or vector fast
  (`VecFastMath`, the default with `LawMath::fast`);
- every value a masked blend reads is defined: the `Law2` whose Jacobian is blended before its first evaluation is
  value-initialised, and `blend_work` blends only the fields the law in use writes (#135 review).

The ~2× win comes from running two independent dependency chains at once. The law is latency-bound: divisions and
chains of transcendentals.

## Speed

Method: `python -m cpp.bench --core 6as6`, the #20 / #67 harness unchanged (`cpp/BENCHMARK.md`), with 120
configurations. #135 adds x1 / x2 fast, s2, s2 fast per-lane and v2 rows at the default setting for every quality,
peak and block. Each stereo row records whether its output is bit-identical to its x2 twin (`matches_x2`). On CI's
g++ 15 build they are within tolerance, not bit-identical: the target has FMA and GCC contracts scalar and vector
code differently (`fma: true`; cpp/README.md, "Stereo"; checked by `test_stereo_parity_6as6`).

**The paired control** (#135 review; in the bench job, `bench.py --pair`). This runs on the same runner, in one
job, for the default setting's x1 / x2 rows (plus the fast rows), block 64:
1. main (the merge base);
2. this head's core built with main's harness (`bench_6as6.cpp`);
3. this head;
4. this head built with `-DDK6AS6_LAZY_JACOBIAN`;
5. main again, to show the VM's drift (≤ 1 % in every run used here).

| runner CPU (head) | head core / main, libm | fast mono / main | s2 / main x2 | v2 / main x2 | lazy / eager (fast) |
|---|---|---|---|---|---|
| Intel Xeon 8573C (`66fc001`) | 0.997–1.003 | 0.82–0.83 | 0.79–0.82 | 0.59–0.61 | 1.000–1.002 |
| Intel Xeon 6975P-C (`66fc001`, two runs) | 0.988–1.009 | 0.81–0.83 | 0.78–0.82 | 0.58–0.61 | 0.997–1.008 |
| AMD EPYC 9V74 (`cfbac89`: libm code as the head; fast / stereo lazy) | 0.990–1.008 | 0.84–0.86 | 0.75–0.79 | 0.60–0.63 | 1.000–1.009 |
| AMD EPYC 7763 (`2532950`: libm on the two-pass law then) | (1.11–1.14, the rejected two-pass libm) | 0.85–0.86 | 0.80–0.84 | 0.63–0.65 | — |

The fast / stereo rows come from this PR's harness, ~2 % slow on mono rows (above), and are against main's harness:
conservative. Full records: `bench_6as6_x86_64_linux_ci_*_135.json`, and the paired runs under `bench_pair_6as6/`.

**Apple M1** (`bench_6as6_apple_m1_loaded_135.json`, 11 × 2 s, Apple clang 17 `-O3`). **Loaded, shared machine**:
load average 7.7 / 11.6 / 12.6 (1 / 5 / 15 min) before the run, 6.0 / 7.2 / 7.5 after, with other agent jobs running. Wall-clock and cycles are not quoted as timings. Instructions per base-rate sample, block 64; main is
`1124111`, same harness and flags, 5 × 2 s:

| setting | peak | main x1 | x1 libm | x1 fast | main x2 | s2 libm | s2 fast per-lane | v2 fast |
|---|---|---|---|---|---|---|---|---|
| standard 8× | 0.5 V | 54 043 | 53 550 (0.99) | 42 370 (0.78) | 107 881 | 96 878 (0.90) | 80 770 (0.75) | 57 106 (0.53) |
| standard 8× | 2 V | 57 758 | 57 195 (0.99) | 45 076 (0.78) | 115 088 | 105 377 (0.92) | 87 683 (0.76) | 61 705 (0.54) |
| standard 8× | 8 V | 63 276 | 62 608 (0.99) | 49 151 (0.78) | 126 282 | 116 380 (0.92) | 96 680 (0.77) | 67 699 (0.54) |
| hq 16× | 0.5 V | 104 672 | 103 708 (0.99) | 82 170 (0.79) | 209 373 | 186 705 (0.89) | 155 869 (0.74) | 110 508 (0.53) |
| hq 16× | 2 V | 107 075 | 105 989 (0.99) | 83 876 (0.78) | 213 673 | 191 714 (0.90) | 159 943 (0.75) | 113 208 (0.53) |
| hq 16× | 8 V | 112 987 | 111 873 (0.99) | 88 311 (0.78) | 225 158 | 205 528 (0.91) | 171 285 (0.76) | 120 757 (0.54) |

(x1 columns / main x1, stereo columns / main x2, in brackets; x1 libm is #104's code, the 1 % is this PR's
harness.) Every stereo row is bit-identical to its x2 twin (`matches_x2`). Some passes ran partly on E-cores, so
cycles are not quoted.

**The clean M1 timing is still to do.** Michael asked for it on #135 (a quiet-machine `python -m cpp.bench --core
6as6`), and #143's acceptance also lists it ("Clean M1 figures (percent of one core, method stated) for the 12AX7
stage and … the 6AS6 stage"); PR #162 records it as still open. This machine stayed loaded (load average 5–17)
through #135, so no clean figure could be taken here. The PR that adds this report is therefore *part of* #135, which
stays open for that timing.

**Where the time goes** (M1, `values3` per call, a 2 V trajectory, an informal micro-benchmark). The law is ~70 % of
the stage. In libm, `pow` costs ~140 instructions, and the Jacobian law calls it 5 times (≈ 700 of 1 549
instructions). `fm_pow` is ~117 instructions, ~30 of them constant loads, so fast maths cuts cycles (290 against 349
per values pass) much more than it cuts instructions. Cycles are bounded by the dependency chain: divisions, then
exp → log1p → pow → exp. That is why the two-lane kernel, which runs two chains at once, pays the most.

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

A `pow` with a positive normal base whose result is subnormal comes from libm `exp` of the fast g·log s. Its absolute
error is asserted below 1e−12 × DBL_MIN; the stage flushes such values to 0.

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
kernel's left lane, one run (`StereoStage`'s per-lane `Record`). dk6as6.validate on the C++ core reproduces every
committed Python result within the libm path's `LIMITS`. All 44 settings ran (both models, every mode and reference
current, standard and hq). Every dk/validate.CRIT criterion reads as Python's, and every supported setting passes.
The worst deviations are libm's own, to 3 digits: op 8.6e−12, AC 3.8e−8 dB, H2..H5 9.6e−6 dB. Newton histograms
differ by 4.3e−5 (limit 1e−3). Below CRIT's −90 dBc harmonic floor, H5 at −177 dBc moves by 0.013 dB, an error
component around −233 dBc. Records: `results_6as6_fast.json`, `results_6as6_stereo.json` (libm, identical to the
mono record) and `results_6as6_stereo_fast.json`. Since #135's review these come from the stereo lane's own
internal signals. They regenerated byte-identical, because those signals equal mono's bit for bit (below).

**Aliasing** (`python -m cpp.aliasing_6as6 guard --math fast [--stereo]`; CI runs the fast stereo guard as well):
at 48 and 44.1 kHz, all 140 guard rows pass the absolute gate and the per-tone ratchet against the existing libm
baselines. The worst per-tone delta is 2.7e−5 dB and the smallest margin 13.1 dB, mono fast and stereo vector alike,
with nothing non-converged. The baselines are only ever written from the libm mono path, and the selectors apply to
`guard` only (`sweep` / `scan` / `guardgrid` refuse them: their cache and records are the libm mono path's).

**Two-lane kernel parity** (`test_stereo_parity_6as6`, `test_core_6as6 stereo`).
- **Cases:** each of the 45 vector cases runs on the stereo kernel with left = the case and right = 0.6 × the case
  reversed, so the lanes diverge (129 of 135 runs differ in iterations). It is compared with two mono Stages for
  libm, per-lane fast and vector fast.
- **Internal records:** each lane's record (Vg1k, Vg2k, Vak, Ig1, Ip + Ig2, iterations) equals its mono Stage's on
  all 135 runs.
- **Solvers:** 32 400 fuzzed lanes; 5 524 pairs differ in iterations and 782 in restarts, with non-finite starts
  and max_it 1…50. The kernel's iteration count matches a per-start count from the mono solver on every pair,
  including 144 where the longer lane swaps between starts.
- **Other:** per-lane recovery from ±DBL_MAX / NaN / an overflowing drive leaves the other lane untouched; float I/O
  and the FP mode are covered.

| build | SIMD | stereo vs 2 × mono |
|---|---|---|
| Apple clang 17, M1: `-O2`, `-O3`, `-O2 -ffp-contract=off`, scalar `-ffp-contract=off`, `-O2 -DDK6AS6_LAZY_JACOBIAN` | neon / scalar | bit-exact (all lane maths) |
| Apple clang 17, `-arch x86_64` (Rosetta 2): `-O2`, scalar `-O2`, `-O2 -march=x86-64-v3` | sse2 / scalar / sse2 | bit-exact |
| Apple clang 17, M1: scalar `-O2` (contraction on) | scalar | within tolerance: ≤ 9.1e−13 V; solver flags all equal, converged lanes ≤ 1.1e−13 |
| g++ 15 on CI (x86-64, FMA): `-O2`, `-O3` | sse2 | within tolerance (`TOL_PINNED_ABS`) |

## Realtime

No new allocation, lock or syscall on the audio thread: the stereo kernel and the fast path are covered by the
`CountAllocs` checks. `ScopedFlushDenormals` is held as before. The research probes exist only in builds with
`-DDK6AS6_PROBES`: the test binary, never the plugin or the bench.

## Adopted, and what is Michael's call

- **Adopted:** nothing on the libm path, which is #104's code, bit-exact with Python, at 0.99–1.01× main on every
  x86 CPU sampled.
- **Available, measured, not switched on:** `LawMath::fast` and `StereoStage`. The core's defaults are unchanged
  (libm, mono `Stage`), so Python parity and every existing record stand.
- **Not adopted:** the lazier Jacobian (no measurable gain; a research switch), the two-pass law on the libm path
  (1.11–1.15× on AMD), fast `atan` and reciprocal constants.
- **Recommendation for Gyre:** run the 6AS6 path as one `StereoStage` with `LawMath::fast` (vector lane maths). It
  costs 0.58–0.63× two libm mono stages on x86, and its output is within −205 dBc of libm with every gate passing.
  This touches the plugin engine, which PR #161 (#115, live bias and mode on `LiveStage`) rewrites for the 6AS6 path,
  so the wiring is follow-up #164, once #161 lands.

## Reproduce

From prototypes/fullstage:
- `python -m pytest -q cpp/tests/test_cpp_core_6as6.py`
- `python -m cpp.validate_cpp_6as6 --math fast` (and the same with `--stereo` added)
- `python -m cpp.aliasing_6as6 guard --fs 48000 --math fast --stereo`
- `python -m cpp.fastlaw_6as6 domain` (~3.5 min; it builds the lazy research build and also writes the deviation
  record)
- `python -m cpp.bench --core 6as6 --out ...`
- `python -m cpp.bench --pair base.json build1.json ... base.json`
