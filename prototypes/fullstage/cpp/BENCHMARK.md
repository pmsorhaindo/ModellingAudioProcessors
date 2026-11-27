# cpp/ — per-instance CPU benchmark of the C++ core (#20)

> **Status (11 Nov 2026): official #20 results are below**, in `cpp/bench_apple_m1.json` and
> `cpp/bench_x86_64_linux.json`. The informal #19 number (README "Speed", the removed `bench.json`) is **not** the
> #20 result (see the last section).
>
> **#21 (12 Nov 2026): the M1 stereo SIMD run is below** ("Stereo SIMD results (#21)", `cpp/bench_apple_m1_stereo.json`).
> One StereoStage costs 1.25–1.35 × one mono instance with fast math and 1.57–1.67 × with libm, so the 1.1–1.3 ×
> estimate does not hold; it is 0.63–0.83 × two mono instances. The x86-64 stereo run is below (#76,
> `cpp/bench_x86_64_linux_stereo.json`): 1.37–1.55 × one mono instance (fast) and 1.38–1.53 × (libm);
> 0.69–0.77 × two mono instances.
>
> **#67 (13 Nov 2026): the M1 vector-maths run is below** ("Vector-maths stereo results (#67)",
> `cpp/bench_apple_m1_simd_math.json`). With `LaneMath::vector` one StereoStage costs 1.13–1.21 × one mono instance
> (fast math), against 1.27–1.35 × for the #21 path in the same run; the ~1.1 × goal is not met.
>
> **#143 (27 Nov 2026): repeated M1 runs of the shipping stages** (12AX7 libm, 6AS6 default) are in
> `cpp/m1_timing/README.md`, summarised by `cpp/m1_spread.py`. That Mac was loaded, so none is clean: the spread and
> an estimate from cycles at the quiet clock are reported instead.
>
> **Default (18 Nov 2026):** with `TriodeMath::fast`, `StereoStage` now defaults to `LaneMath::vector` (the v2 path
> measured above). `LaneMath::per_lane` remains the bit-exact reference, and the libm path is unchanged (there is no
> vector libm). The M1 figures still compare the two fast-math options. Accuracy and CPU of vector vs per-lane
> fast math vs libm still need a dedicated comparison across platforms (#90). The x86-64 stereo
> figures (s2 and v2) are below (#76).

| file | what |
|---|---|
| `bench.cpp` | the harness: includes `dk_core.h` unchanged, generates the test signal, times `Stage::process()`, prints JSON |
| `bench.py` | the runner: builds `bench.cpp`, records host / build / power / thermal / load state, writes `bench_<machine>.json`, prints the tables below (standard library only, no numpy) |
| `build.py` | the shared compile helper (also used by `validate_cpp.py` and the tests) |
| `bench_apple_m1.json`, `bench_x86_64_linux.json` | committed official #20 results, one file per machine |
| `bench_apple_m1_stereo.json` | committed official #21 stereo run (x1 + x2 + s2, 108 rows), M1 |
| `bench_apple_m1_simd_math.json` | committed official #67 run (x1 + x2 + s2 + v2, 126 rows), M1 |
| `bench_x86_64_linux_stereo.json` | committed official #76 run (x1 + x2 + s2 + v2, 126 rows), x86-64 Linux VM |

## What is measured

**The unit is one mono instance.** The issue asks for "per stereo instance", but the #20 core is mono: one
`dkcore::Stage`, double precision, one channel. Stereo SIMD came later, with #21 (`dkcore::StereoStage`). So each
configuration runs three ways:
- `x1`: one mono `Stage`.
- `x2`, the **stereo-equivalent**: two independent mono `Stage`s on one thread. In each block the harness processes
  the left one, then the right one, as a host would. The right channel gets a differently plucked copy of the
  signal. This is measured, not 2 × `x1`. It shows what a stereo track costs with the mono core, and whether two
  instances' state (2 × 27 kB) changes the per-instance cost on a given cache. It is not a stereo code path.
- `s2` (#21), the **stereo SIMD path**: one `StereoStage` (45 kB), the same left and right signals as `x2`, as the
  two lanes of one kernel. Its output must equal `x2`'s bit for bit on the exact builds (cpp/README.md, "Stereo").
  The row's `matches_x2` records whether it did in that run. The question it answers is #21's: s2 / x2 (2 × mono
  costs 1.00 here), and s2 / x1 (the budget note's estimate is 1.1–1.3; an estimate to test, not a result).
  Both ratios come from the same run.
- `v2` (#67, fast math only), the **vector-maths stereo path**: the `s2` StereoStage with
  `set_lane_math(LaneMath::vector)`, i.e. `dk/fastmath.h` on both lanes at once (cpp/README.md, "Vector maths").
  This is also the default for a `StereoStage` prepared with `TriodeMath::fast`. The harness still sets the mode
  explicitly so that s2 and v2 can be compared in one run. The same signals; `matches_x2` again. Its question is
  #67's: v2 / x1 against the ~1.1 × goal, and v2 / s2.

**Matrix: 126 configurations** (#20 records: 72, without `s2`; #21 records: 108, without `v2`), all at 48 kHz, bias 1, drive 1, output 1, default
Newton settings:

| axis | values | why |
|---|---|---|
| quality | eco (4× + 17.3 kHz pre-warp), standard (8×), hq (16×) | the three `dk_core.h` settings (`stage.QUALITY`) |
| math | `libm` (default), `fast` (`dk/fastmath.h`, #18) | the two triode math paths |
| input peak | 0.5 V, 2 V, 8 V | clean / edge of breakup / driven; cost follows the Newton iteration count, which follows the level |
| block | 64, 512 samples (1.33 ms, 10.67 ms) | 64: a common low-latency tracking buffer, where the deadline is tightest. 512: a common mixing default. Per-block work in `process()` is constant (FTZ set and restore, the triple-buffer check, two relaxed atomic loads, the math dispatch), so two sizes bracket that overhead. The per-sample work does not depend on the block size. |
| mode | `x1`, `x2`, `s2`, `v2` (fast math only: 18 rows) | above |

## Signal

The signal is generated by `bench.cpp make_signal()`, so no audio fixture is committed. It is a 2 s guitar-like DI
phrase of Karplus–Strong plucks: an LCG noise burst in a one-period delay line, with the two-point average in the
loop. The phrase contains:
- a sustained low E;
- three single notes;
- a six-string E minor strum, 8 ms between strings;
- a lead A4 over the chord;
- a release to near silence.

Notes are damped at their ends. The whole signal is scaled so that its peak equals the configured level. Its crest
factor is 10.5 dB (`meta.signal_crest_db`, a property of the generated samples). The signal is deterministic: the
same samples on every machine, so the Newton iteration counts are reproducible.

It is meant to exercise what a sine does not: attacks, a chord (intermodulation), sustained low notes, and decay to
near silence. It is still synthetic, not a recorded performance.

## Method

* **Build** (`bench.py`, default `-O3`): `$CXX -std=c++17 -Wall -Wextra -pedantic -pthread -O3`, without
  `-march`.
  - x86-64: the portable baseline (SSE2, no FMA). Pass `--flags "-O3 -march=x86-64-v3"` for an AVX2 / FMA build,
    as a separately named results file.
  - Apple clang: the default target is already `-target-cpu apple-m1` (checked with `c++ -###`).
  - The compiler's default FP contraction applies. On AArch64 that contracts to FMA.
  - Never `-ffast-math`: `dk_core.h` `#error`s on it.
  - The exact compiler version, the flags and the git commit go into the results file (`build`). `build.dirty`
    must be false for a committed record.
* **Instances.** Each configuration owns its `Stage`(s), prepared once.
* **Passes.** A pass is `reset()`, then the whole signal through `process()` in blocks: 95 744 samples (2 s rounded
  down to a multiple of 512, so both block sizes have whole blocks).
* **Warm-up.** One untimed pass of every configuration. It warms the caches, the branch predictors and the clocks,
  and gives the reference output checksum and the Newton statistics.
* **Repetitions.** 11 timed passes per configuration (`--reps`). They run **round-robin over all configurations**:
  pass 1 of each, then pass 2 of each, and so on. Slow drift (thermal, background load) therefore spreads over all
  configurations instead of biasing the last ones.
* **Determinism check.** Every timed pass's output must be bit-identical to the warm-up's. If one is not, the harness
  exits 1 and `bench.py` writes no file.
* **Timing.** `std::chrono::steady_clock`, read once at every block boundary. On macOS it is backed by
  `mach_absolute_time` (24 MHz on Apple silicon, 41.7 ns ticks); on Linux, by `CLOCK_MONOTONIC`. A pass's time runs
  from the first read to the last. A block's time runs from one read to the next. The clock reads sit outside
  `process()`: one per block.
* **Scheduling.**
  - macOS has no thread-affinity API on Apple silicon. The harness sets `QOS_CLASS_USER_INTERACTIVE`, which makes the
    scheduler prefer P-cores. Where the passes actually ran is measured (`p_core_fraction`, below).
  - Linux: default policy, no pinning in the harness. Pin from outside with `taskset -c N`. The size of the
    affinity mask is recorded (`meta.sched`).
  - Neither OS runs the harness as a realtime audio thread (no time-constraint policy or audio workgroup on macOS,
    no `SCHED_FIFO` on Linux). This is a throughput benchmark at user priority.
* **Counters.** Where they are unavailable, the JSON says `null`. No cycle count is estimated from time × a clock
  rate.
  - *macOS:* `proc_pid_rusage(RUSAGE_INFO_V6)` gives `ri_cycles`, `ri_instructions` and `ri_pcycles`. These are the
    kernel's per-thread fixed-counter totals for the process. The bench is single-threaded, and the reads sit outside
    the timed region. The totals may include the few cycles the kernel spends on the thread (interrupts).
  - *Checked on this M1 before relying on it:* a fixed dependent loop counted 3.3–3.5 × 10⁹ cycles both on P-cores
    (`USER_INTERACTIVE`) and on E-cores (`BACKGROUND` QoS), while its wall time differed 3.6×. So the counter counts
    core cycles, not time. `ri_pcycles` was 0 for the E-core run.
  - *Linux:* `perf_event_open`, hardware cycles and instructions, this thread, user space only. A pass is rejected if
    the kernel multiplexed the counters, and the result is `null` if they read 0 (e.g. a VM without a virtual PMU)
    or cannot be opened (`meta.counter_error`, e.g. `perf_event_paranoid`). `rdtsc` is not used: the TSC ticks at a
    constant rate, not in core cycles.
* **Host state.** `bench.py` records the state before and after the run: the load average, plus on macOS
  `pmset -g batt` / `therm` and Low Power Mode, on Linux the cpufreq governor and thermal zone 0. It also records the
  CPU (sysctl / lscpu), the OS and the memory.

### Reported per configuration (`results[]` in the JSON)

| field | meaning |
|---|---|
| `instances`, `path` | 1 or 2 channels; `mono` (x1, x2), `stereo_simd` (s2) or `stereo_simd_vecmath` (v2). The #20 records have no `path`: mono |
| `matches_x2` | s2 and v2 only: output bit-identical to the x2 row with the same settings (`null` if that row is not in the run) |
| `ns_per_sample`, `ns_min`, `ns_max` | wall ns per base-rate sample frame (for `x2` and `s2`, both channels), median / min / max over the passes |
| `pct_core` | `ns_per_sample` × 48 000 / 10⁹ × 100: **% of one core at real time** (median) |
| `block_pct_p50`, `_p99`, `_max` | one block's wall time as % of its real-time period, over all blocks of all passes |
| `cpu_on_fraction` | thread CPU time / wall time per pass (median); below 1 means the thread was preempted |
| `cycles_per_sample`, `instr_per_sample` | counter deltas per base-rate sample frame (median); `null` without counters |
| `ghz_on_cpu` | cycles / thread CPU ns (median): a clock sanity check (P-core max on the M1 is 3.2 GHz) |
| `p_core_fraction`, `_min` | macOS: the share of the cycles counted on P-cores (median, worst pass) |
| `newton_mean`, `nonconverged` | the mean Newton iterations per internal sample (left instance / channel), and the non-converged samples (warm-up pass) |
| `newton_mean_r`, `newton_mean_max` | #67 records: the right instance / channel's mean (`null` for x1), and for s2 / v2 the iterations the 2-lane kernel ran, per sample max(itL, itR) (`StereoStage::kernel_iterations`; `null` for mono) |
| `deterministic` | always true in a written file |

## Caveats

- **Fanless M1 Air.** Nothing guarantees that it holds the P-core clock under sustained single-core load.
  `ghz_on_cpu` and `pmset -g therm` (before and after) show it if the clock dropped. Run on AC power with Low Power
  Mode off. The battery charge state is recorded, because charging adds heat.
- **Interactive machine.** The Air may be in use: a DAW, `coreaudiod`, browsers. Close other apps first. Wall time
  includes preemption, and the medians over 11 interleaved passes only reduce it. Cycles and instructions count only
  while the thread runs, so they are the stabler figure on a loaded machine.
- **Per-block percentiles are wall time at user priority.** They include preemption and clock changes, so they bound
  what a realtime audio thread would see from above. They are not the DSP's own worst case. `block_pct_max` in
  particular is mostly scheduler noise.
- **P / E cores.** Cycles on an E-core (Icestorm) are not comparable with cycles on a P-core (Firestorm). The tables
  mark a cycle figure with * if any of its passes had under 99 % of its cycles on P-cores.
- **One machine, one compiler per results file.** An M1 number says nothing about M2+, and the VM number nothing about
  a desktop x86. On a shared VM the clock is not exposed and neighbours share the host.
- **Octatrack context only.** The ~411 cycles/sample slot budget (`research/octatrack-budget.md`) is a target for a
  later cut-down model, not for this full-precision core. It can be set next to an honest cycle figure once one
  exists, and is not a pass / fail criterion here.
- The core is validated against the Python and ngspice references, not against a physical amplifier. Nothing here is
  a statement about sound.

## Running the official benchmark

From `prototypes/fullstage`, on a committed checkout (`build.dirty` must be false; `test_bench_records` rejects
dirty records). Use the system Python 3; no packages are needed.

**Apple silicon, on the M1 Air, only after the go-ahead:**
1. Close the other apps (DAW, browsers, chat). Use AC power with Low Power Mode off, and let the machine idle for a
   few minutes.
2. Run:
   ```bash
   cd ~/Developer/ModellingAudioProcessors/prototypes/fullstage
   python3 -m cpp.bench --flags=-O3 --seconds 2 --reps 11 --out cpp/bench_apple_m1.json
   python3 -m cpp.bench --table cpp/bench_apple_m1.json       # paste below, with the state_before / after summary
   ```
   `--flags -O3` (unquoted) is also accepted; `--flags=-O3` and `--flags "-O3"` always were. The default is already
   `-O3`.

**x86-64 Linux, on the VM:**
```bash
cd prototypes/fullstage
taskset -c 2 python3 -m cpp.bench --flags=-O3 --seconds 2 --reps 11 --out cpp/bench_x86_64_linux.json
python3 -m cpp.bench --table cpp/bench_x86_64_linux.json
# optional, as its own record: --flags="-O3 -march=x86-64-v3" --out cpp/bench_x86_64_linux_v3.json
```

**#21 stereo run (108 configurations, x1 + x2 + s2).** The same commands, into new files, so that the #20 records
and their tables below stay as they are. The `s2` ratios must come from one run (round-robin), never from s2 of one
run divided by x1 / x2 of another.

Apple silicon, on the M1 Air, only after the go-ahead (same preparation as above):
```bash
cd ~/Developer/ModellingAudioProcessors/prototypes/fullstage
python3 -m cpp.bench --flags=-O3 --seconds 2 --reps 11 --out cpp/bench_apple_m1_stereo.json
python3 -m cpp.bench --table cpp/bench_apple_m1_stereo.json
```
Expected duration: about 9–11 minutes. The #20 run (72 configurations) took 6 min 18 s on this machine, and the sum
of its own medians gives the same (31.2 s per round × 12 passes). The 36 `s2` rows add 0.55–1.0 × the `x2` time,
20.9 s per round. That range is an assumption for the duration only, not a result.

x86-64 Linux, on the VM: `taskset -c 2 python3 -m cpp.bench --flags=-O3 --seconds 2 --reps 11 --out
cpp/bench_x86_64_linux_stereo.json`, then `--table`. Expect about 9–11 minutes by the same arithmetic; #20 took
6 min 34 s there.

**#67 run (126 configurations, x1 + x2 + s2 + v2).** Again into a new file, the #20 and #21 records stay as they
are; all four modes in one round-robin run:
```bash
cd ~/Developer/ModellingAudioProcessors/prototypes/fullstage
python3 -m cpp.bench --flags=-O3 --seconds 2 --reps 11 --out cpp/bench_apple_m1_simd_math.json
python3 -m cpp.bench --table cpp/bench_apple_m1_simd_math.json
```

**File names.** Results files are named `cpp/bench_<machine>[_<variant>].json`. `test_bench_records` checks every
`cpp/bench_*.json`: all 72 rows (#20 schema, no `path`), all 108 rows (#21 schema, every `s2` row with a
`matches_x2`) or all 126 rows (#67 schema, also 18 `v2` rows with `matches_x2`, and `newton_mean_r` /
`newton_mean_max` consistent with max(itL, itR)), deterministic, at least 5 repetitions, not dirty.

**Not for the record.** `--only` subsets and the functional smoke (`--seconds 0.011 --reps 1`, which is what
`test_bench_smoke` runs in CI) produce numbers that mean nothing. CI checks that the harness builds and is
deterministic. It never checks a timing.

## Results

### Apple silicon — Apple M1 MacBook Air (MacBookAir10,1), macOS 15.6.1 (24G90), Apple clang 17.0.0

Recorded 11 Nov 2026, 21:20:49–21:27:07 CET (Europe/Amsterdam), from `cpp/bench_apple_m1.json`. Git `dbd6205`,
`build.dirty` false.

- **CPU:** Apple M1, 4 P-cores + 4 E-cores, 16 GB.
- **Compiler:** Apple clang 17.0.0 (clang-1700.6.3.2) as `/usr/bin/g++`, `-std=c++17 -Wall -Wextra -pedantic -pthread -O3`.
  The default target is already `apple-m1`.
- **Harness:** 72 configurations, 11 timed passes after one warm-up, 1.99467 s (95 744 samples) per pass, round-robin,
  `QOS_CLASS_USER_INTERACTIVE`. Counters: `proc_pid_rusage` RUSAGE_INFO_V6 (`ri_cycles` / `ri_instructions` /
  `ri_pcycles`). All 72 outputs were bit-identical across passes.
- **Power:** AC power, Low Power Mode off, no thermal warning recorded. Battery 69 % charging at the start, 74 %
  charging at the end.
- **Load:** 10.36, 5.09, 4.05 at the start (1 / 5 / 15 min); 2.88, 3.53, 3.64 at the end. The 1-minute load was
  already high when the run began. Other user apps had been closed after a go-ahead at 21:19 CET, but WindowServer
  and this Grok Bot session were still using CPU. Treat the wall-time figures, especially the per-block percentiles
  and the two `x2` outliers below, with that in mind. Cycle counts exclude preemption and are the stabler figure
  here.
- **Clock and preemption:** `ghz_on_cpu` (cycles / thread CPU ns, median) ranged from 3.156 to 3.170
  (P-core max is 3.2 GHz). `cpu_on_fraction` ranged from 0.9940 to 0.9977.
- **P-cores:** `p_core_fraction_min` was 0.9673–0.9979. A cycle figure marked * had at least
  one pass under 99 % of its cycles on P-cores.
- **Spread to note:** the stereo-equivalent (`x2`) at eco / fast / 8 V was 17.04 % of a core at block 64 vs 14.62 %
  at block 512; at hq / fast / 0.5 V it was 44.12 % vs 50.93 %. The matching `x1` rows do not show that gap. These
  are reported as measured.

**one mono instance** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 6.53 | 6.51 | 1361 (1348–1374) | 1356 (1348–1364) | 4283 | 4275 | 2.22 |
| eco 4× | libm | 2 V | 7.17 | 7.14 | 1493 (1487–1552) | 1488 (1480–1501) | 4709 | 4700 | 2.56 |
| eco 4× | libm | 8 V | 7.91 | 7.92 | 1648 (1640–1672) | 1650 (1636–1655) | 5201 | 5195 | 2.97 |
| eco 4× | fast | 0.5 V | 5.94 | 5.94 | 1237 (1234–1246) | 1237 (1225–1341) | 3907 | 3896* | 2.22 |
| eco 4× | fast | 2 V | 6.56 | 6.55 | 1367 (1364–1387) | 1365 (1357–1381) | 4314 | 4312 | 2.56 |
| eco 4× | fast | 8 V | 7.32 | 7.30 | 1526 (1520–1547) | 1521 (1516–1540) | 4810 | 4806 | 2.97 |
| standard 8× | libm | 0.5 V | 12.26 | 12.22 | 2553 (2536–2581) | 2546 (2538–2574) | 8039 | 8030 | 2.04 |
| standard 8× | libm | 2 V | 12.78 | 12.73 | 2663 (2634–2833) | 2652 (2632–2667) | 8394* | 8355 | 2.18 |
| standard 8× | libm | 8 V | 14.26 | 14.33 | 2970 (2959–3385) | 2984 (2972–3017) | 9382* | 9404 | 2.60 |
| standard 8× | fast | 0.5 V | 11.11 | 11.12 | 2315 (2292–2328) | 2317 (2291–2363) | 7307 | 7298* | 2.04 |
| standard 8× | fast | 2 V | 11.68 | 11.65 | 2434 (2413–2470) | 2426 (2410–2811) | 7654 | 7634* | 2.18 |
| standard 8× | fast | 8 V | 13.19 | 13.23 | 2748 (2726–3143) | 2756 (2729–2784) | 8657* | 8672 | 2.60 |
| hq 16× | libm | 0.5 V | 24.26 | 24.26 | 5055 (5011–5110) | 5053 (5007–5365) | 15928 | 15896* | 2.00 |
| hq 16× | libm | 2 V | 24.44 | 24.43 | 5092 (5083–5150) | 5090 (5049–5120) | 16046 | 16042 | 2.03 |
| hq 16× | libm | 8 V | 25.95 | 25.95 | 5406 (5372–5459) | 5407 (5370–5777) | 17054 | 17018* | 2.25 |
| hq 16× | fast | 0.5 V | 22.01 | 22.08 | 4586 (4572–4610) | 4599 (4551–5025) | 14478 | 14448* | 2.00 |
| hq 16× | fast | 2 V | 22.77 | 22.24 | 4745 (4730–4799) | 4634 (4609–5062) | 14953 | 14624* | 2.03 |
| hq 16× | fast | 8 V | 23.96 | 23.91 | 4991 (4929–5375) | 4981 (4942–5443) | 15685* | 15671 | 2.25 |

**two mono instances, one after the other (stereo-equivalent)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 13.02 | 13.01 | 2713 (2698–2728) | 2711 (2684–3082) | 8556 | 8522* | 2.22 |
| eco 4× | libm | 2 V | 14.31 | 14.30 | 2981 (2957–3474) | 2980 (2962–2991) | 9380* | 9384 | 2.56 |
| eco 4× | libm | 8 V | 15.86 | 15.80 | 3303 (3277–3641) | 3291 (3269–3374) | 10386* | 10366 | 2.97 |
| eco 4× | fast | 0.5 V | 11.86 | 11.83 | 2472 (2449–2726) | 2464 (2451–2814) | 7771* | 7765* | 2.22 |
| eco 4× | fast | 2 V | 13.10 | 13.16 | 2730 (2715–3162) | 2741 (2700–3109) | 8612* | 8602* | 2.56 |
| eco 4× | fast | 8 V | 17.04 | 14.62 | 3549 (3528–3871) | 3045 (3020–3072) | 11177* | 9591 | 2.97 |
| standard 8× | libm | 0.5 V | 24.50 | 24.41 | 5105 (5075–5492) | 5086 (5059–5133) | 16063 | 16036 | 2.04 |
| standard 8× | libm | 2 V | 25.50 | 25.53 | 5312 (5270–5847) | 5318 (5275–5682) | 16703* | 16717* | 2.18 |
| standard 8× | libm | 8 V | 28.50 | 28.50 | 5938 (5879–5965) | 5937 (5892–5969) | 18685 | 18674 | 2.60 |
| standard 8× | fast | 0.5 V | 22.26 | 22.16 | 4637 (4614–5005) | 4618 (4590–4634) | 14645* | 14558 | 2.04 |
| standard 8× | fast | 2 V | 23.20 | 23.19 | 4832 (4810–5182) | 4830 (4799–5177) | 15231* | 15216* | 2.18 |
| standard 8× | fast | 8 V | 26.26 | 26.21 | 5471 (5440–5841) | 5461 (5447–5490) | 17234* | 17231 | 2.60 |
| hq 16× | libm | 0.5 V | 48.52 | 48.65 | 10109 (10060–10561) | 10136 (10070–10538) | 31855 | 31833 | 2.00 |
| hq 16× | libm | 2 V | 49.52 | 48.72 | 10316 (10216–10728) | 10150 (10109–10599) | 32367 | 32024 | 2.03 |
| hq 16× | libm | 8 V | 51.97 | 51.71 | 10826 (10740–11178) | 10774 (10697–11148) | 33997 | 33935 | 2.25 |
| hq 16× | fast | 0.5 V | 44.12 | 50.93 | 9191 (9153–9546) | 10610 (10574–11064) | 28951 | 33441 | 2.00 |
| hq 16× | fast | 2 V | 44.54 | 44.52 | 9279 (9250–9603) | 9276 (9223–9710) | 29229 | 29207* | 2.03 |
| hq 16× | fast | 8 V | 47.57 | 47.56 | 9910 (9876–10076) | 9908 (9884–10300) | 31240 | 31213 | 2.25 |

**one mono instance, per block** — block time as % of the block's real-time period (b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes

| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |
|---|---|---|---|---|---|---|
| eco | libm | 0.5 V | 6.2 | 8.8 | 6.3 | 8.1 |
| eco | libm | 2 V | 7.3 | 9.7 | 7.2 | 8.8 |
| eco | libm | 8 V | 7.8 | 10.7 | 7.8 | 10.0 |
| eco | fast | 0.5 V | 5.7 | 7.9 | 5.7 | 7.8 |
| eco | fast | 2 V | 6.6 | 8.5 | 6.6 | 8.2 |
| eco | fast | 8 V | 7.2 | 9.9 | 7.2 | 9.3 |
| standard | libm | 0.5 V | 11.9 | 15.8 | 12.0 | 14.6 |
| standard | libm | 2 V | 12.2 | 16.8 | 12.3 | 16.0 |
| standard | libm | 8 V | 14.3 | 18.8 | 14.4 | 17.3 |
| standard | fast | 0.5 V | 10.7 | 14.1 | 10.9 | 13.3 |
| standard | fast | 2 V | 11.2 | 15.4 | 11.2 | 15.0 |
| standard | fast | 8 V | 13.2 | 17.5 | 13.3 | 16.1 |
| hq | libm | 0.5 V | 23.8 | 29.9 | 23.9 | 26.8 |
| hq | libm | 2 V | 23.8 | 30.4 | 24.0 | 28.7 |
| hq | libm | 8 V | 25.2 | 33.0 | 25.4 | 31.6 |
| hq | fast | 0.5 V | 21.6 | 26.3 | 21.7 | 24.6 |
| hq | fast | 2 V | 22.2 | 27.9 | 21.9 | 26.1 |
| hq | fast | 8 V | 23.1 | 31.5 | 23.2 | 29.4 |

### x86-64 Linux — shared Intel Xeon VM, conda-forge gcc 14.4.0

Recorded 11 Nov 2026, 20:52:33–20:59:07 CET (Europe/Amsterdam), from `cpp/bench_x86_64_linux.json`. Git `dbd6205`,
`build.dirty` false. The machine is a shared KVM guest; the numbers are indicative, not a dedicated-host result.

- **CPU:** `Intel(R) Xeon(R) Processor`, 8 cores, 1 thread per core, hypervisor KVM (full). `lscpu` does not expose
  a clock or a more specific model. L1d 384 KiB (8 instances), L2 16 MiB (8 instances), L3 320 MiB. The cpufreq
  governor and thermal zone were not exposed.
- **Compiler:** conda-forge gcc 14.4.0-7 (`x86_64-conda-linux-gnu-g++`), `-std=c++17 -Wall -Wextra -pedantic -pthread -O3`,
  no `-march` (portable SSE2 baseline, no FMA).
- **Harness:** the same 72 configurations, 11 timed passes after one warm-up, 1.99467 s per pass, round-robin, pinned
  with `taskset -c 2` (`meta.sched`: affinity mask has 1 CPU). All 72 outputs were bit-identical across passes.
- **Counters:** unavailable (`meta.counter_error`: `No such file or directory` from `perf_event_open`). Cycle and
  instruction counts are `null`; they were not estimated from time × a clock.
- **Load:** 0.31, 0.45, 0.51 at the start (1 / 5 / 15 min); 1.21, 1.00, 0.76 at the end. `cpu_on_fraction` ranged
  from 0.9999 to 1.0000.

**one mono instance** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 6.76 | 6.76 | 1408 (1402–1554) | 1408 (1397–1415) | — | — | 2.22 |
| eco 4× | libm | 2 V | 7.42 | 7.40 | 1546 (1540–1615) | 1542 (1534–1586) | — | — | 2.56 |
| eco 4× | libm | 8 V | 8.03 | 8.03 | 1673 (1662–1723) | 1674 (1663–1756) | — | — | 2.97 |
| eco 4× | fast | 0.5 V | 6.21 | 6.26 | 1294 (1284–1363) | 1303 (1284–1333) | — | — | 2.22 |
| eco 4× | fast | 2 V | 6.84 | 6.86 | 1424 (1412–1615) | 1430 (1415–1450) | — | — | 2.56 |
| eco 4× | fast | 8 V | 7.62 | 7.58 | 1587 (1573–1684) | 1580 (1572–1662) | — | — | 2.97 |
| standard 8× | libm | 0.5 V | 12.90 | 12.99 | 2687 (2677–2824) | 2706 (2670–3507) | — | — | 2.04 |
| standard 8× | libm | 2 V | 13.53 | 13.36 | 2819 (2766–2952) | 2783 (2762–2943) | — | — | 2.18 |
| standard 8× | libm | 8 V | 14.64 | 14.81 | 3049 (3020–3084) | 3085 (3023–3182) | — | — | 2.60 |
| standard 8× | fast | 0.5 V | 11.88 | 11.88 | 2475 (2450–2542) | 2476 (2451–2549) | — | — | 2.04 |
| standard 8× | fast | 2 V | 12.40 | 12.30 | 2584 (2566–2713) | 2562 (2550–2625) | — | — | 2.18 |
| standard 8× | fast | 8 V | 13.81 | 13.82 | 2877 (2870–2989) | 2880 (2867–3006) | — | — | 2.60 |
| hq 16× | libm | 0.5 V | 25.66 | 25.43 | 5346 (5260–5544) | 5299 (5252–5441) | — | — | 2.00 |
| hq 16× | libm | 2 V | 25.88 | 25.45 | 5392 (5288–6126) | 5302 (5276–5373) | — | — | 2.03 |
| hq 16× | libm | 8 V | 26.85 | 26.49 | 5593 (5486–6263) | 5519 (5468–6149) | — | — | 2.25 |
| hq 16× | fast | 0.5 V | 23.41 | 23.33 | 4878 (4837–4971) | 4860 (4834–5070) | — | — | 2.00 |
| hq 16× | fast | 2 V | 23.61 | 23.58 | 4919 (4896–5295) | 4913 (4900–5580) | — | — | 2.03 |
| hq 16× | fast | 8 V | 25.02 | 24.98 | 5213 (5182–5410) | 5204 (5193–5942) | — | — | 2.25 |

**two mono instances, one after the other (stereo-equivalent)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 13.45 | 13.47 | 2802 (2793–3035) | 2807 (2795–3450) | — | — | 2.22 |
| eco 4× | libm | 2 V | 14.80 | 14.92 | 3083 (3070–3797) | 3108 (3070–3388) | — | — | 2.56 |
| eco 4× | libm | 8 V | 16.06 | 16.09 | 3345 (3324–3615) | 3353 (3319–3739) | — | — | 2.97 |
| eco 4× | fast | 0.5 V | 12.39 | 12.41 | 2582 (2556–2724) | 2586 (2563–3372) | — | — | 2.22 |
| eco 4× | fast | 2 V | 13.93 | 13.61 | 2902 (2829–3158) | 2836 (2819–2970) | — | — | 2.56 |
| eco 4× | fast | 8 V | 15.13 | 15.12 | 3152 (3138–3397) | 3150 (3129–3366) | — | — | 2.97 |
| standard 8× | libm | 0.5 V | 25.87 | 25.77 | 5390 (5331–6006) | 5369 (5317–6597) | — | — | 2.04 |
| standard 8× | libm | 2 V | 27.14 | 27.19 | 5653 (5527–6020) | 5665 (5518–5956) | — | — | 2.18 |
| standard 8× | libm | 8 V | 29.34 | 29.27 | 6113 (6051–6905) | 6098 (6042–6312) | — | — | 2.60 |
| standard 8× | fast | 0.5 V | 24.00 | 23.61 | 5001 (4908–5288) | 4919 (4900–5706) | — | — | 2.04 |
| standard 8× | fast | 2 V | 24.72 | 24.58 | 5150 (5086–5226) | 5121 (5089–5442) | — | — | 2.18 |
| standard 8× | fast | 8 V | 27.65 | 27.62 | 5761 (5737–5799) | 5754 (5722–5975) | — | — | 2.60 |
| hq 16× | libm | 0.5 V | 51.05 | 50.95 | 10635 (10490–10927) | 10614 (10523–10858) | — | — | 2.00 |
| hq 16× | libm | 2 V | 51.32 | 51.01 | 10692 (10612–11539) | 10627 (10574–10819) | — | — | 2.03 |
| hq 16× | libm | 8 V | 52.86 | 52.67 | 11012 (10943–11482) | 10974 (10912–11393) | — | — | 2.25 |
| hq 16× | fast | 0.5 V | 46.81 | 46.77 | 9753 (9668–10236) | 9744 (9661–10551) | — | — | 2.00 |
| hq 16× | fast | 2 V | 46.84 | 46.95 | 9759 (9731–10184) | 9782 (9734–10251) | — | — | 2.03 |
| hq 16× | fast | 8 V | 50.08 | 50.24 | 10434 (10364–11247) | 10467 (10344–11064) | — | — | 2.25 |

**one mono instance, per block** — block time as % of the block's real-time period (b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes

| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |
|---|---|---|---|---|---|---|
| eco | libm | 0.5 V | 6.5 | 9.1 | 6.5 | 8.5 |
| eco | libm | 2 V | 7.5 | 9.7 | 7.5 | 9.0 |
| eco | libm | 8 V | 7.9 | 10.8 | 7.9 | 10.6 |
| eco | fast | 0.5 V | 6.0 | 8.7 | 6.1 | 8.0 |
| eco | fast | 2 V | 7.0 | 9.1 | 6.9 | 8.7 |
| eco | fast | 8 V | 7.5 | 10.3 | 7.5 | 9.6 |
| standard | libm | 0.5 V | 12.6 | 16.1 | 12.6 | 15.8 |
| standard | libm | 2 V | 12.8 | 17.8 | 13.2 | 17.1 |
| standard | libm | 8 V | 14.6 | 18.9 | 14.8 | 18.4 |
| standard | fast | 0.5 V | 11.5 | 14.6 | 11.5 | 14.3 |
| standard | fast | 2 V | 11.8 | 16.4 | 11.9 | 15.7 |
| standard | fast | 8 V | 13.9 | 18.0 | 14.0 | 16.8 |
| hq | libm | 0.5 V | 24.9 | 31.4 | 25.0 | 30.0 |
| hq | libm | 2 V | 24.9 | 32.6 | 25.0 | 29.3 |
| hq | libm | 8 V | 25.8 | 35.6 | 26.1 | 34.4 |
| hq | fast | 0.5 V | 22.9 | 28.5 | 23.0 | 26.9 |
| hq | fast | 2 V | 23.1 | 29.9 | 23.1 | 27.3 |
| hq | fast | 8 V | 24.3 | 32.3 | 24.4 | 36.8 |

### Stereo SIMD results (#21)

#### Apple silicon — Apple M1 MacBook Air (MacBookAir10,1), macOS 15.6.1 (24G90), Apple clang 17.0.0

Recorded 12 Nov 2026, 22:03:39–22:13:16 CET (Europe/Amsterdam, 9 min 37 s), from `cpp/bench_apple_m1_stereo.json`.
Git `127e5dd`, `build.dirty` false. 108 configurations: x1, x2 and s2 in one round-robin run, so every ratio below
compares rows of the same passes.

- **CPU / compiler / harness:** as for #20 above (Apple M1, 4 P + 4 E cores, 16 GB; Apple clang 17.0.0 as
  `/usr/bin/g++`, `-std=c++17 -Wall -Wextra -pedantic -pthread -O3`; 11 timed passes after one warm-up, 1.99467 s per
  pass). `meta.simd` = `neon`, `meta.fma` = true. `sizeof(Stage)` 27 400 B, `sizeof(StereoStage)` 45 264 B.
- **Scheduling:** `QOS_CLASS_USER_INTERACTIVE`, i.e. a normal user-priority thread, not a realtime audio thread. No
  pinning (macOS has no API for it).
- **Correctness:** all 108 rows deterministic across passes. Every `s2` row has `matches_x2: true`, so the stereo
  output was bit-identical to the two mono Stages on these signals. Non-converged samples: 0 in every row.
- **Power:** AC power, battery 100 % and charged (not charging) before and after, Low Power Mode off. `pmset -g therm`
  recorded no thermal, performance or CPU-power warning, before or after.
- **Load: the machine was in interactive use.** Load average 2.89, 2.80, 2.81 (1 / 5 / 15 min) at the start
  (`state_before`; `uptime` in the run log a second earlier: 3.05, 2.83, 2.82), and 8.06, 4.59, 3.60 at the end.
  The process list taken just before the run (run log, `ps` %CPU): WindowServer 41.4, coreaudiod 40.3, OBS 28.3,
  Grok Bot Helper (Renderer) 25.6, Grok Bot Helper 22.7, Camo Studio 19.0, appleh13camerad 3.7, all others ≤ 2.5.
  Those apps were not closed. The wall-time figures, and the per-block percentiles most of all, include that
  competition. Cycle counts exclude preemption and are the stabler figure.
- **Clock and preemption:** `ghz_on_cpu` 3.092–3.119 (lower than #20's 3.156–3.170). `cpu_on_fraction`
  0.9661–0.9963. The x1 rows were 0.7–3.4 % slower in wall time than the #20 x1 rows, but within −1.3 / +1.5 % in
  cycles.
- **P-cores:** `p_core_fraction_min` 0.7518–0.9950 over all rows, 0.8953–0.9941 over the s2 rows. The lowest were
  eco / fast / 2 V / b512 x2 (0.75) and hq / fast / 2 V / b512 x1 (0.80); those two rows also have the largest
  per-block p99 (62.3 % and 89.3 %). Cycle figures marked * had a pass under 99 % on P-cores.

**Standard 8× (the default), s2 against x2 and x1** — % of one core at 48 kHz (median); ns per base-rate sample frame
(both channels for x2 and s2):

| math | peak | block | x1 ns / % core | x2 ns / % core | s2 ns / % core | s2 / x1 | s2 / x2 |
|---|---|---|---|---|---|---|---|
| libm | 0.5 V | 64 | 2608 / 12.52 | 5205 / 24.99 | 4168 / 20.01 | 1.60 | 0.80 |
| libm | 0.5 V | 512 | 2612 / 12.54 | 5187 / 24.90 | 4151 / 19.93 | 1.59 | 0.80 |
| libm | 2 V | 64 | 2708 / 13.00 | 5406 / 25.95 | 4397 / 21.11 | 1.62 | 0.81 |
| libm | 2 V | 512 | 2738 / 13.14 | 5407 / 25.95 | 4396 / 21.10 | 1.61 | 0.81 |
| libm | 8 V | 64 | 3033 / 14.56 | 6048 / 29.03 | 5034 / 24.16 | 1.66 | 0.83 |
| libm | 8 V | 512 | 3042 / 14.60 | 6049 / 29.04 | 5042 / 24.20 | 1.66 | 0.83 |
| fast | 0.5 V | 64 | 2381 / 11.43 | 4757 / 22.84 | 3102 / 14.89 | 1.30 | 0.65 |
| fast | 0.5 V | 512 | 2374 / 11.40 | 4744 / 22.77 | 3080 / 14.78 | 1.30 | 0.65 |
| fast | 2 V | 64 | 2488 / 11.94 | 5002 / 24.01 | 3281 / 15.75 | 1.32 | 0.66 |
| fast | 2 V | 512 | 2480 / 11.91 | 4942 / 23.72 | 3283 / 15.76 | 1.32 | 0.66 |
| fast | 8 V | 64 | 2806 / 13.47 | 5635 / 27.05 | 3794 / 18.21 | 1.35 | 0.67 |
| fast | 8 V | 512 | 2804 / 13.46 | 5672 / 27.23 | 3778 / 18.14 | 1.35 | 0.67 |

**Ranges over drive (0.5 / 2 / 8 V) and block (64 / 512)**, median ns ratios. The cycle ratios agree within 0.07 per row; the largest gap is hq / libm / 0.5 V / b512, 1.64 in
ns but 1.57 in cycles, a wall-time outlier:

| quality | math | s2 % core | s2 / x1 ("× mono") | s2 / x2 |
|---|---|---|---|---|
| eco 4× | libm | 10.85–13.34 | 1.63–1.67 | 0.82–0.83 |
| eco 4× | fast | 8.00–10.01 | 1.31–1.34 | 0.66–0.67 |
| standard 8× | libm | 19.93–24.20 | 1.59–1.66 | 0.80–0.83 |
| standard 8× | fast | 14.78–18.21 | 1.30–1.35 | 0.65–0.67 |
| hq 16× | libm | 38.79–42.61 | 1.57–1.64 | 0.78–0.81 |
| hq 16× | fast | 28.59–32.27 | 1.25–1.32 | 0.63–0.66 |

**Verdict on the estimate (stereo ≈ 1.1–1.3 × mono): it does not hold on the M1.** Stereo SIMD costs 1.25–1.35 ×
one mono instance with fast math and 1.57–1.67 × with libm. Only 5 of the 36 s2 rows are at or below 1.30, all fast
math at 0.5 V or 2 V (standard 0.5 V b512, hq 0.5 V and 2 V); none is near 1.1. The block size changes nothing
(within 0.07). Stereo SIMD is still clearly cheaper than two mono Stages: 0.63–0.67 × x2 with fast math, 0.78–0.83 ×
with libm. At the default (standard, fast) a stereo instance takes 14.8–18.2 % of a core instead of 22.8–27.2 %.

Why it is above the estimate, from the counters of this run:
- **SIMD removes few instructions.** s2 executes 0.88–0.92 × x2's instructions with fast math and 0.90–0.95 × with
  libm (1.75–1.89 × x1). The per-lane scalar exp / log1p / pow / pnjlim, plus moving values between the vector and
  the scalar calls, keep most of the work at 2 ×. The gain comes mainly from instructions per cycle: x1 and x2 run at
  2.49–3.01, s2 at 3.93–4.07 with fast math, but only at 2.86–3.01 with libm.
- **libm costs far more in s2 than in two monos.** The libm-minus-fast difference is 726–2780 cycles per sample in x2
  but 1796–6640 in s2, 2.3–3.3 × as much. In instructions, s2 libm runs 695–2670 more per sample relative to fast
  than x2 libm does. fast math (`dk/fastmath.h`) is inlined; libm is out-of-line calls. A likely cause, *not verified
  by disassembly*: AAPCS64 preserves only the low 64 bits of v8–v15 across a call, so live 2-lane values must be
  spilled and reloaded around every libm call, and the calls stop the two lanes' work from overlapping.
- **max(itL, itR) Newton iterations.** The right channel is an independently plucked signal, so the lanes can need
  different counts. `newton_mean` records the left channel only (identical in x1, x2 and s2, as the parity requires),
  so this run cannot separate that overhead. The fast-math ratio rises with drive (standard 1.30 → 1.35, hq
  1.25–1.27 → 1.31–1.32), where the left channel's mean also rises (2.04 → 2.60 and 2.00 → 2.25). That is consistent
  with a lane-divergence cost, but does not prove it.

Caveats specific to this run: one machine, one compiler, a normal user-priority thread on a machine in interactive
use (above). The per-block percentiles are wall time under that load and bound a realtime thread's view from above
only. The x86-64 stereo run (SSE2) is below.

`--table` output, verbatim from the run log (the per-block table is x1 only; the s2 per-block figures are in the
JSON):

**one mono instance** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 6.65 | 6.63 | 1386 (1372–1424) | 1382 (1370–1418) | 4281 | 4284* | 2.22 |
| eco 4× | libm | 2 V | 7.29 | 7.28 | 1518 (1508–1553) | 1516 (1500–1551) | 4709 | 4694 | 2.56 |
| eco 4× | libm | 8 V | 8.10 | 8.09 | 1688 (1661–1712) | 1686 (1665–2140) | 5207 | 5218* | 2.97 |
| eco 4× | fast | 0.5 V | 6.11 | 6.07 | 1272 (1256–1305) | 1265 (1251–1284) | 3924* | 3915 | 2.22 |
| eco 4× | fast | 2 V | 6.75 | 6.71 | 1405 (1392–1442) | 1397 (1385–2157) | 4334 | 4326* | 2.56 |
| eco 4× | fast | 8 V | 7.53 | 7.51 | 1569 (1553–1774) | 1566 (1544–1958) | 4830* | 4821* | 2.97 |
| standard 8× | libm | 0.5 V | 12.52 | 12.54 | 2608 (2576–2934) | 2612 (2571–3016) | 8051* | 8043* | 2.04 |
| standard 8× | libm | 2 V | 13.00 | 13.14 | 2708 (2674–3078) | 2738 (2713–2797) | 8376* | 8481 | 2.18 |
| standard 8× | libm | 8 V | 14.56 | 14.60 | 3033 (3006–3076) | 3042 (3013–3084) | 9395 | 9401 | 2.60 |
| standard 8× | fast | 0.5 V | 11.43 | 11.40 | 2381 (2348–2534) | 2374 (2360–2782) | 7347* | 7332 | 2.04 |
| standard 8× | fast | 2 V | 11.94 | 11.91 | 2488 (2448–2525) | 2480 (2461–2526) | 7676 | 7672 | 2.18 |
| standard 8× | fast | 8 V | 13.47 | 13.46 | 2806 (2782–2889) | 2804 (2775–3142) | 8684 | 8688* | 2.60 |
| hq 16× | libm | 0.5 V | 24.68 | 24.66 | 5141 (5113–5474) | 5138 (5112–5243) | 15921* | 15900 | 2.00 |
| hq 16× | libm | 2 V | 24.99 | 24.90 | 5206 (5162–5434) | 5188 (5140–5633) | 16074 | 16042* | 2.03 |
| hq 16× | libm | 8 V | 26.64 | 26.44 | 5550 (5457–6050) | 5509 (5463–5550) | 17083* | 17013 | 2.25 |
| hq 16× | fast | 0.5 V | 22.57 | 22.83 | 4702 (4668–5142) | 4756 (4681–5119) | 14544 | 14665* | 2.00 |
| hq 16× | fast | 2 V | 22.93 | 22.87 | 4777 (4696–6883) | 4764 (4728–16816) | 14755* | 14705* | 2.03 |
| hq 16× | fast | 8 V | 24.46 | 24.57 | 5095 (5067–6070) | 5118 (5057–5530) | 15761* | 15759* | 2.25 |

**two mono instances, one after the other (stereo-equivalent)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 13.24 | 13.25 | 2759 (2730–3204) | 2761 (2728–3220) | 8550* | 8520* | 2.22 |
| eco 4× | libm | 2 V | 14.60 | 14.60 | 3042 (3020–3096) | 3042 (3004–3124) | 9393 | 9389* | 2.56 |
| eco 4× | libm | 8 V | 16.13 | 16.12 | 3361 (3332–3782) | 3358 (3323–3396) | 10389* | 10373 | 2.97 |
| eco 4× | fast | 0.5 V | 12.12 | 12.14 | 2525 (2487–2669) | 2529 (2500–2561) | 7812* | 7794* | 2.22 |
| eco 4× | fast | 2 V | 13.41 | 13.54 | 2795 (2769–3057) | 2820 (2762–10958) | 8642* | 8647* | 2.56 |
| eco 4× | fast | 8 V | 15.06 | 14.95 | 3137 (3088–7463) | 3114 (3091–3606) | 9660* | 9628* | 2.97 |
| standard 8× | libm | 0.5 V | 24.99 | 24.90 | 5205 (5136–5723) | 5187 (5161–5545) | 16064 | 16034* | 2.04 |
| standard 8× | libm | 2 V | 25.95 | 25.95 | 5406 (5355–5818) | 5407 (5374–5586) | 16696* | 16683* | 2.18 |
| standard 8× | libm | 8 V | 29.03 | 29.04 | 6048 (6002–6131) | 6049 (5957–6115) | 18685 | 18696 | 2.60 |
| standard 8× | fast | 0.5 V | 22.84 | 22.77 | 4757 (4711–4809) | 4744 (4704–5161) | 14660 | 14642* | 2.04 |
| standard 8× | fast | 2 V | 24.01 | 23.72 | 5002 (4961–5142) | 4942 (4908–5248) | 15443 | 15312* | 2.18 |
| standard 8× | fast | 8 V | 27.05 | 27.23 | 5635 (5606–6001) | 5672 (5631–5810) | 17378* | 17527 | 2.60 |
| hq 16× | libm | 0.5 V | 49.51 | 49.55 | 10316 (10254–10716) | 10323 (10243–10438) | 31851 | 31809 | 2.00 |
| hq 16× | libm | 2 V | 49.73 | 49.82 | 10361 (10297–10484) | 10380 (10320–10427) | 32056 | 32084 | 2.03 |
| hq 16× | libm | 8 V | 52.71 | 52.66 | 10981 (10929–11444) | 10972 (10899–11208) | 33934* | 33924 | 2.25 |
| hq 16× | fast | 0.5 V | 45.16 | 45.16 | 9408 (9362–9798) | 9409 (9334–10440) | 29071* | 29043* | 2.00 |
| hq 16× | fast | 2 V | 45.69 | 45.63 | 9520 (9437–11183) | 9507 (9448–14526) | 29395* | 29362* | 2.03 |
| hq 16× | fast | 8 V | 48.98 | 49.77 | 10204 (10085–11037) | 10368 (10082–11424) | 31456* | 31411* | 2.25 |

**stereo SIMD: one StereoStage, the x2 signals as two lanes of one kernel (#21)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 10.86 | 10.86 | 2261 (2230–2304) | 2263 (2226–2347) | 6967* | 6961* | 2.22 |
| eco 4× | libm | 2 V | 12.14 | 12.09 | 2528 (2510–2913) | 2519 (2504–2958) | 7827* | 7812* | 2.56 |
| eco 4× | libm | 8 V | 13.34 | 13.33 | 2779 (2764–3179) | 2776 (2735–3034) | 8594* | 8586* | 2.97 |
| eco 4× | fast | 0.5 V | 8.00 | 8.01 | 1666 (1650–1716) | 1669 (1648–1710) | 5169 | 5165* | 2.22 |
| eco 4× | fast | 2 V | 9.02 | 8.95 | 1879 (1857–2261) | 1864 (1848–2404) | 5791* | 5785* | 2.56 |
| eco 4× | fast | 8 V | 9.92 | 10.01 | 2066 (2049–2156) | 2085 (2042–2200) | 6394* | 6394* | 2.97 |
| standard 8× | libm | 0.5 V | 20.01 | 19.93 | 4168 (4130–4550) | 4151 (4116–4576) | 12845* | 12815* | 2.04 |
| standard 8× | libm | 2 V | 21.11 | 21.10 | 4397 (4369–4780) | 4396 (4335–4888) | 13589* | 13561* | 2.18 |
| standard 8× | libm | 8 V | 24.16 | 24.20 | 5034 (4979–5434) | 5042 (4998–5578) | 15539* | 15552* | 2.60 |
| standard 8× | fast | 0.5 V | 14.89 | 14.78 | 3102 (3077–3487) | 3080 (3052–3464) | 9567* | 9544* | 2.04 |
| standard 8× | fast | 2 V | 15.75 | 15.76 | 3281 (3237–3401) | 3283 (3240–3653) | 10120 | 10110* | 2.18 |
| standard 8× | fast | 8 V | 18.21 | 18.14 | 3794 (3755–4210) | 3778 (3741–3839) | 11691* | 11680 | 2.60 |
| hq 16× | libm | 0.5 V | 38.79 | 40.33 | 8081 (8043–8802) | 8403 (8012–8612) | 24946* | 24979* | 2.00 |
| hq 16× | libm | 2 V | 39.27 | 39.39 | 8182 (8146–8546) | 8207 (8129–9378) | 25270 | 25251* | 2.03 |
| hq 16× | libm | 8 V | 42.45 | 42.61 | 8843 (8782–9221) | 8878 (8777–9224) | 27310* | 27298 | 2.25 |
| hq 16× | fast | 0.5 V | 28.75 | 28.59 | 5990 (5933–6436) | 5956 (5939–9298) | 18499* | 18455* | 2.00 |
| hq 16× | fast | 2 V | 29.20 | 29.15 | 6084 (6025–8570) | 6073 (6032–6560) | 18796* | 18772* | 2.03 |
| hq 16× | fast | 8 V | 32.17 | 32.27 | 6703 (6630–6984) | 6724 (6632–8048) | 20670* | 20677* | 2.25 |

**stereo SIMD cost** — s2 / x1 and s2 / x2 from the same run: median ns per base-rate sample, and cycles where counted (2.00 = two mono instances, 1.00 = one); matches x2 = s2 output bit-identical to x2's

| setting | math | peak | ns s2/x1 b64 | ns s2/x1 b512 | ns s2/x2 b64 | ns s2/x2 b512 | cycles s2/x1 b64 | cycles s2/x2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 1.63 | 1.64 | 0.82 | 0.82 | 1.63 | 0.81 | yes |
| eco 4× | libm | 2 V | 1.67 | 1.66 | 0.83 | 0.83 | 1.66 | 0.83 | yes |
| eco 4× | libm | 8 V | 1.65 | 1.65 | 0.83 | 0.83 | 1.65 | 0.83 | yes |
| eco 4× | fast | 0.5 V | 1.31 | 1.32 | 0.66 | 0.66 | 1.32 | 0.66 | yes |
| eco 4× | fast | 2 V | 1.34 | 1.33 | 0.67 | 0.66 | 1.34 | 0.67 | yes |
| eco 4× | fast | 8 V | 1.32 | 1.33 | 0.66 | 0.67 | 1.32 | 0.66 | yes |
| standard 8× | libm | 0.5 V | 1.60 | 1.59 | 0.80 | 0.80 | 1.60 | 0.80 | yes |
| standard 8× | libm | 2 V | 1.62 | 1.61 | 0.81 | 0.81 | 1.62 | 0.81 | yes |
| standard 8× | libm | 8 V | 1.66 | 1.66 | 0.83 | 0.83 | 1.65 | 0.83 | yes |
| standard 8× | fast | 0.5 V | 1.30 | 1.30 | 0.65 | 0.65 | 1.30 | 0.65 | yes |
| standard 8× | fast | 2 V | 1.32 | 1.32 | 0.66 | 0.66 | 1.32 | 0.66 | yes |
| standard 8× | fast | 8 V | 1.35 | 1.35 | 0.67 | 0.67 | 1.35 | 0.67 | yes |
| hq 16× | libm | 0.5 V | 1.57 | 1.64 | 0.78 | 0.81 | 1.57 | 0.78 | yes |
| hq 16× | libm | 2 V | 1.57 | 1.58 | 0.79 | 0.79 | 1.57 | 0.79 | yes |
| hq 16× | libm | 8 V | 1.59 | 1.61 | 0.81 | 0.81 | 1.60 | 0.80 | yes |
| hq 16× | fast | 0.5 V | 1.27 | 1.25 | 0.64 | 0.63 | 1.27 | 0.64 | yes |
| hq 16× | fast | 2 V | 1.27 | 1.27 | 0.64 | 0.64 | 1.27 | 0.64 | yes |
| hq 16× | fast | 8 V | 1.32 | 1.31 | 0.66 | 0.65 | 1.31 | 0.66 | yes |

**one mono instance, per block** — block time as % of the block's real-time period (b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes

| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |
|---|---|---|---|---|---|---|
| eco | libm | 0.5 V | 6.3 | 9.8 | 6.4 | 8.6 |
| eco | libm | 2 V | 7.4 | 10.4 | 7.4 | 9.3 |
| eco | libm | 8 V | 7.9 | 11.6 | 8.0 | 11.7 |
| eco | fast | 0.5 V | 5.8 | 9.1 | 5.8 | 7.9 |
| eco | fast | 2 V | 6.8 | 9.8 | 6.9 | 16.3 |
| eco | fast | 8 V | 7.4 | 12.7 | 7.4 | 10.9 |
| standard | libm | 0.5 V | 12.2 | 21.1 | 12.2 | 16.8 |
| standard | libm | 2 V | 12.4 | 18.7 | 12.7 | 16.4 |
| standard | libm | 8 V | 14.6 | 19.9 | 14.7 | 18.7 |
| standard | fast | 0.5 V | 11.1 | 17.8 | 11.1 | 14.3 |
| standard | fast | 2 V | 11.4 | 16.2 | 11.4 | 15.7 |
| standard | fast | 8 V | 13.5 | 18.6 | 13.6 | 19.5 |
| hq | libm | 0.5 V | 24.3 | 33.4 | 24.4 | 29.7 |
| hq | libm | 2 V | 24.4 | 34.0 | 24.4 | 30.8 |
| hq | libm | 8 V | 25.6 | 39.4 | 25.7 | 33.0 |
| hq | fast | 0.5 V | 22.2 | 30.4 | 22.5 | 28.0 |
| hq | fast | 2 V | 22.4 | 44.6 | 22.5 | 89.3 |
| hq | fast | 8 V | 23.7 | 38.9 | 23.9 | 31.9 |

| machine | file | status |
|---|---|---|
| Apple M1 MacBook Air | `cpp/bench_apple_m1_stereo.json` | recorded 12 Nov 2026 (above) |
| x86-64 Linux VM | `cpp/bench_x86_64_linux_stereo.json` | recorded 18 Nov 2026 (below) |

#### x86-64 Linux — shared Intel Xeon VM, conda-forge gcc 14.4.0

Recorded 18 Nov 2026, 10:23:22–10:36:13 CET (Europe/Amsterdam, 12 min 51 s), from
`cpp/bench_x86_64_linux_stereo.json`. Git `9139efd`, `build.dirty` false. 126 configurations: x1, x2, s2 and v2
(the merged #67 harness) in one round-robin run, so every ratio below compares rows of the same passes. The
vector-maths ratios are under "Vector-maths stereo results (#67)" below; this section reports s2 / x1 and s2 / x2
next to the M1 #21 figures.

- **CPU / compiler / harness:** as for #20 above (`Intel(R) Xeon(R) Processor`, 8 cores, 1 thread per core,
  hypervisor KVM (full); `lscpu` does not expose a clock or a more specific model; family 6, model 207, stepping 2).
  L1d 384 KiB (8 instances), L2 16 MiB (8 instances), L3 320 MiB. The cpufreq governor and thermal zone were not
  exposed. conda-forge gcc 14.4.0-7 (`x86_64-conda-linux-gnu-g++`), `-std=c++17 -Wall -Wextra -pedantic -pthread -O3`,
  no `-march` (portable SSE2 baseline, `meta.simd` = `sse2`, `meta.fma` = false). 11 timed passes after one warm-up,
  1.99467 s per pass, pinned with `taskset -c 2` (`nproc` was 8; `meta.sched`: affinity mask has 1 CPU).
  `sizeof(Stage)` 27 472 B, `sizeof(StereoStage)` 45 360 B.
- **Correctness:** all 126 rows deterministic across passes. Every `s2` and `v2` row has `matches_x2: true`, so both
  stereo paths were bit-identical to the two mono Stages on these signals (as expected on this no-FMA build).
  Non-converged samples: 0 in every row.
- **Counters:** unavailable (`meta.counter_error`: `No such file or directory` from `perf_event_open`), as in the
  #20 x86-64 run. Cycle and instruction counts are `null`; they were not estimated from time × a clock.
- **Load:** 0.26, 0.28, 0.33 at the start (1 / 5 / 15 min); 1.37, 1.32, 1.03 at the end. `cpu_on_fraction` ranged
  from 0.9999 to 1.0000.
- **Absolute x1 times** are 1.11–1.21 × the official #20 x86-64 x1 rows (`bench_x86_64_linux.json`, 11 Nov, git
  `dbd6205`, same compiler flags on the same class of VM). The ratios below come from this run only. Possible
  contributors, not separated here: shared-VM neighbours, and the core has changed since that record (#70's
  deviation-from-operating-point state among them).

**s2 / x1 and s2 / x2 next to the M1 #21 figures** (median ns per base-rate sample, ranges over quality, drive and
block). All from one run on each machine:

| machine | math | s2 / x1 | s2 / x2 |
|---|---|---|---|
| Apple M1 Air (#21, 12 Nov) | fast | 1.25–1.35 | 0.63–0.67 |
| Apple M1 Air (#21, 12 Nov) | libm | 1.57–1.67 | 0.78–0.83 |
| x86-64 Linux VM (this run) | fast | 1.37–1.55 | 0.69–0.77 |
| x86-64 Linux VM (this run) | libm | 1.38–1.53 | 0.69–0.77 |

**Standard 8× (the default), s2 against x2 and x1** — % of one core at 48 kHz (median); ns per base-rate sample frame
(both channels for x2 and s2):

| math | peak | block | x1 ns / % core | x2 ns / % core | s2 ns / % core | s2 / x1 | s2 / x2 |
|---|---|---|---|---|---|---|---|
| libm | 0.5 V | 64 | 3070 / 14.73 | 6105 / 29.30 | 4292 / 20.60 | 1.40 | 0.70 |
| libm | 0.5 V | 512 | 3098 / 14.87 | 6120 / 29.37 | 4276 / 20.52 | 1.38 | 0.70 |
| libm | 2 V | 64 | 3194 / 15.33 | 6381 / 30.63 | 4612 / 22.14 | 1.44 | 0.72 |
| libm | 2 V | 512 | 3159 / 15.16 | 6369 / 30.57 | 4562 / 21.90 | 1.44 | 0.72 |
| libm | 8 V | 64 | 3454 / 16.58 | 6934 / 33.28 | 5292 / 25.40 | 1.53 | 0.76 |
| libm | 8 V | 512 | 3469 / 16.65 | 6947 / 33.34 | 5230 / 25.10 | 1.51 | 0.75 |
| fast | 0.5 V | 64 | 2818 / 13.53 | 5703 / 27.38 | 4022 / 19.30 | 1.43 | 0.71 |
| fast | 0.5 V | 512 | 2818 / 13.52 | 5664 / 27.19 | 3987 / 19.14 | 1.42 | 0.70 |
| fast | 2 V | 64 | 2862 / 13.73 | 5754 / 27.62 | 4176 / 20.04 | 1.46 | 0.73 |
| fast | 2 V | 512 | 2856 / 13.71 | 5795 / 27.82 | 4083 / 19.60 | 1.43 | 0.70 |
| fast | 8 V | 64 | 3302 / 15.85 | 6597 / 31.67 | 4861 / 23.33 | 1.47 | 0.74 |
| fast | 8 V | 512 | 3261 / 15.65 | 6557 / 31.47 | 4880 / 23.42 | 1.50 | 0.74 |

**Ranges over drive (0.5 / 2 / 8 V) and block (64 / 512)**, median ns ratios:

| quality | math | s2 % core | s2 / x1 ("× mono") | s2 / x2 |
|---|---|---|---|---|
| eco 4× | libm | 11.48–14.34 | 1.42–1.53 | 0.72–0.77 |
| eco 4× | fast | 10.60–13.50 | 1.46–1.55 | 0.73–0.77 |
| standard 8× | libm | 20.52–25.40 | 1.38–1.53 | 0.70–0.76 |
| standard 8× | fast | 19.14–23.42 | 1.42–1.50 | 0.70–0.74 |
| hq 16× | libm | 40.15–46.03 | 1.39–1.50 | 0.69–0.76 |
| hq 16× | fast | 37.10–43.14 | 1.37–1.49 | 0.69–0.73 |

**Verdict.** On this x86-64 VM, stereo SIMD costs **1.37–1.55 × one mono instance** and **0.69–0.77 × two mono
instances**. The 1.1–1.3 × estimate does not hold here either. Unlike the M1, **libm and fast math have almost the
same stereo/mono ratio** (libm 1.38–1.53, fast 1.37–1.55). On the M1, libm was clearly more expensive in stereo
(1.57–1.67) than fast math (1.25–1.35); the #21 spill hypothesis for that gap is about AAPCS64 and was not expected
to carry over unchanged. The kernel ran max(itL, itR) = 1.00–1.07 × the left channel's iterations, as on the M1.
No instruction or cycle counts were available to split SIMD, maths and Newton cost.

Caveats: one shared KVM guest, one compiler, a normal user-priority thread pinned to one core. The numbers are
indicative, not a dedicated-host result. The per-block percentiles are wall time under that load.

`--table` output, verbatim from the run (the per-block table is x1 only; the s2 / v2 per-block figures are in the
JSON). The v2 tables are repeated under #67 below.
**one mono instance** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 8.18 | 7.94 | 1704 (1555–1899) | 1653 (1538–2045) | — | — | 2.22 |
| eco 4× | libm | 2 V | 8.79 | 8.55 | 1832 (1688–1904) | 1781 (1675–1928) | — | — | 2.56 |
| eco 4× | libm | 8 V | 9.43 | 9.45 | 1965 (1806–2735) | 1968 (1808–2113) | — | — | 2.97 |
| eco 4× | fast | 0.5 V | 7.27 | 7.09 | 1515 (1406–1614) | 1477 (1433–1641) | — | — | 2.22 |
| eco 4× | fast | 2 V | 7.95 | 7.84 | 1657 (1577–1938) | 1633 (1549–1814) | — | — | 2.56 |
| eco 4× | fast | 8 V | 8.68 | 8.88 | 1808 (1718–1992) | 1849 (1720–2040) | — | — | 2.97 |
| standard 8× | libm | 0.5 V | 14.73 | 14.87 | 3070 (2924–3281) | 3098 (2856–3689) | — | — | 2.04 |
| standard 8× | libm | 2 V | 15.33 | 15.16 | 3194 (2971–3498) | 3159 (2943–3432) | — | — | 2.18 |
| standard 8× | libm | 8 V | 16.58 | 16.65 | 3454 (3187–3842) | 3469 (3238–3714) | — | — | 2.60 |
| standard 8× | fast | 0.5 V | 13.53 | 13.52 | 2818 (2708–2950) | 2818 (2674–3030) | — | — | 2.04 |
| standard 8× | fast | 2 V | 13.73 | 13.71 | 2862 (2776–3303) | 2856 (2796–3223) | — | — | 2.18 |
| standard 8× | fast | 8 V | 15.85 | 15.65 | 3302 (3136–3559) | 3261 (3130–3454) | — | — | 2.60 |
| hq 16× | libm | 0.5 V | 28.48 | 29.30 | 5934 (5787–6880) | 6104 (5773–6684) | — | — | 2.00 |
| hq 16× | libm | 2 V | 29.37 | 29.56 | 6119 (5873–6709) | 6159 (5816–6812) | — | — | 2.03 |
| hq 16× | libm | 8 V | 30.27 | 30.77 | 6305 (6049–6952) | 6410 (6039–7352) | — | — | 2.25 |
| hq 16× | fast | 0.5 V | 27.09 | 26.87 | 5643 (5315–6198) | 5597 (5377–5810) | — | — | 2.00 |
| hq 16× | fast | 2 V | 26.70 | 27.38 | 5563 (5372–5904) | 5705 (5386–6296) | — | — | 2.03 |
| hq 16× | fast | 8 V | 29.08 | 28.95 | 6058 (5757–6369) | 6031 (5848–6784) | — | — | 2.25 |

**two mono instances, one after the other (stereo-equivalent)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 16.01 | 16.03 | 3335 (3074–3574) | 3340 (3066–3419) | — | — | 2.22 / 2.20 |
| eco 4× | libm | 2 V | 17.40 | 17.17 | 3624 (3399–3834) | 3577 (3305–3854) | — | — | 2.56 / 2.54 |
| eco 4× | libm | 8 V | 18.88 | 18.57 | 3934 (3596–5030) | 3869 (3646–4240) | — | — | 2.97 / 2.95 |
| eco 4× | fast | 0.5 V | 14.46 | 14.81 | 3012 (2825–3448) | 3085 (2831–3730) | — | — | 2.22 / 2.20 |
| eco 4× | fast | 2 V | 16.05 | 15.82 | 3343 (3095–3577) | 3296 (3085–3772) | — | — | 2.56 / 2.54 |
| eco 4× | fast | 8 V | 17.65 | 17.98 | 3678 (3412–3925) | 3745 (3452–4026) | — | — | 2.97 / 2.95 |
| standard 8× | libm | 0.5 V | 29.30 | 29.37 | 6105 (5702–6631) | 6120 (5657–6354) | — | — | 2.04 / 2.03 |
| standard 8× | libm | 2 V | 30.63 | 30.57 | 6381 (5864–7471) | 6369 (6040–6694) | — | — | 2.18 / 2.16 |
| standard 8× | libm | 8 V | 33.28 | 33.34 | 6934 (6716–7654) | 6947 (6605–7440) | — | — | 2.60 / 2.57 |
| standard 8× | fast | 0.5 V | 27.38 | 27.19 | 5703 (5408–6196) | 5664 (5271–6164) | — | — | 2.04 / 2.03 |
| standard 8× | fast | 2 V | 27.62 | 27.82 | 5754 (5627–6364) | 5795 (5704–6727) | — | — | 2.18 / 2.16 |
| standard 8× | fast | 8 V | 31.67 | 31.47 | 6597 (6249–7064) | 6557 (6284–8070) | — | — | 2.60 / 2.57 |
| hq 16× | libm | 0.5 V | 57.06 | 58.84 | 11888 (11314–12988) | 12257 (11641–13963) | — | — | 2.00 / 2.00 |
| hq 16× | libm | 2 V | 59.50 | 57.52 | 12396 (12003–14271) | 11983 (11478–13888) | — | — | 2.03 / 2.02 |
| hq 16× | libm | 8 V | 61.34 | 60.49 | 12780 (12026–13960) | 12602 (12255–14134) | — | — | 2.25 / 2.23 |
| hq 16× | fast | 0.5 V | 53.71 | 53.14 | 11189 (10623–11677) | 11071 (10730–12138) | — | — | 2.00 / 2.00 |
| hq 16× | fast | 2 V | 53.74 | 55.12 | 11196 (10774–11812) | 11484 (10771–11712) | — | — | 2.03 / 2.02 |
| hq 16× | fast | 8 V | 58.81 | 58.69 | 12252 (11402–12660) | 12228 (11563–12894) | — | — | 2.25 / 2.23 |

**stereo SIMD: one StereoStage, the x2 signals as two lanes of one kernel (#21)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 11.62 | 11.48 | 2421 (2270–2856) | 2391 (2265–2542) | — | — | 2.22 / 2.20 / 2.29 |
| eco 4× | libm | 2 V | 13.01 | 13.07 | 2711 (2522–2873) | 2722 (2421–3078) | — | — | 2.56 / 2.54 / 2.71 |
| eco 4× | libm | 8 V | 14.34 | 14.23 | 2988 (2757–3162) | 2964 (2881–3441) | — | — | 2.97 / 2.95 / 3.12 |
| eco 4× | fast | 0.5 V | 10.60 | 10.91 | 2209 (2095–2442) | 2272 (2086–3031) | — | — | 2.22 / 2.20 / 2.29 |
| eco 4× | fast | 2 V | 11.97 | 12.18 | 2494 (2311–2744) | 2538 (2357–2783) | — | — | 2.56 / 2.54 / 2.71 |
| eco 4× | fast | 8 V | 13.12 | 13.50 | 2734 (2493–2983) | 2812 (2595–3046) | — | — | 2.97 / 2.95 / 3.12 |
| standard 8× | libm | 0.5 V | 20.60 | 20.52 | 4292 (3994–4482) | 4276 (3994–4686) | — | — | 2.04 / 2.03 / 2.06 |
| standard 8× | libm | 2 V | 22.14 | 21.90 | 4612 (4244–5010) | 4562 (4268–5423) | — | — | 2.18 / 2.16 / 2.24 |
| standard 8× | libm | 8 V | 25.40 | 25.10 | 5292 (4968–5956) | 5230 (4970–5525) | — | — | 2.60 / 2.57 / 2.77 |
| standard 8× | fast | 0.5 V | 19.30 | 19.14 | 4022 (3823–4482) | 3987 (3667–4584) | — | — | 2.04 / 2.03 / 2.06 |
| standard 8× | fast | 2 V | 20.04 | 19.60 | 4176 (4019–5031) | 4083 (4009–4529) | — | — | 2.18 / 2.16 / 2.24 |
| standard 8× | fast | 8 V | 23.33 | 23.42 | 4861 (4638–5271) | 4880 (4647–5927) | — | — | 2.60 / 2.57 / 2.77 |
| hq 16× | libm | 0.5 V | 40.15 | 40.69 | 8365 (8042–9134) | 8477 (8174–8882) | — | — | 2.00 / 2.00 / 2.00 |
| hq 16× | libm | 2 V | 43.31 | 41.15 | 9023 (8545–10157) | 8573 (8133–8885) | — | — | 2.03 / 2.02 / 2.05 |
| hq 16× | libm | 8 V | 45.33 | 46.03 | 9444 (8942–10031) | 9590 (9022–11127) | — | — | 2.25 / 2.23 / 2.36 |
| hq 16× | fast | 0.5 V | 37.10 | 37.56 | 7730 (7375–8132) | 7824 (7399–9758) | — | — | 2.00 / 2.00 / 2.00 |
| hq 16× | fast | 2 V | 38.16 | 38.01 | 7949 (7483–8341) | 7918 (7635–8332) | — | — | 2.03 / 2.02 / 2.05 |
| hq 16× | fast | 8 V | 41.68 | 43.14 | 8683 (8275–9388) | 8987 (8382–9723) | — | — | 2.25 / 2.23 / 2.36 |

**stereo SIMD with vector maths: the s2 StereoStage with LaneMath::vector (#67)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 10.00 | 10.16 | 2084 (1950–2339) | 2117 (1890–2382) | — | — | 2.22 / 2.20 / 2.29 |
| eco 4× | fast | 2 V | 10.87 | 11.20 | 2266 (2182–2554) | 2333 (2171–2598) | — | — | 2.56 / 2.54 / 2.71 |
| eco 4× | fast | 8 V | 12.35 | 12.27 | 2572 (2388–2788) | 2557 (2380–2762) | — | — | 2.97 / 2.95 / 3.12 |
| standard 8× | fast | 0.5 V | 18.06 | 17.59 | 3762 (3570–4021) | 3666 (3506–3895) | — | — | 2.04 / 2.03 / 2.06 |
| standard 8× | fast | 2 V | 18.53 | 19.25 | 3860 (3728–4242) | 4009 (3720–4094) | — | — | 2.18 / 2.16 / 2.24 |
| standard 8× | fast | 8 V | 21.88 | 21.75 | 4558 (4224–4820) | 4530 (4336–5653) | — | — | 2.60 / 2.57 / 2.77 |
| hq 16× | fast | 0.5 V | 34.49 | 34.40 | 7185 (7003–7586) | 7166 (6850–7770) | — | — | 2.00 / 2.00 / 2.00 |
| hq 16× | fast | 2 V | 36.40 | 36.53 | 7584 (7029–8151) | 7610 (7060–8025) | — | — | 2.03 / 2.02 / 2.05 |
| hq 16× | fast | 8 V | 39.18 | 39.95 | 8162 (7880–8635) | 8323 (7725–9774) | — | — | 2.25 / 2.23 / 2.36 |

**vector-maths stereo cost (#67)** — v2 / x1, v2 / x2 and v2 / s2 from the same run: median ns per base-rate sample, and cycles where counted (1.00 = one mono instance for v2/x1); matches x2 = v2 output bit-identical to x2's

| setting | math | peak | ns v2/x1 b64 | ns v2/x1 b512 | ns v2/x2 b64 | ns v2/x2 b512 | ns v2/s2 b64 | ns v2/s2 b512 | cycles v2/x1 b64 | cycles v2/s2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 1.38 | 1.43 | 0.69 | 0.69 | 0.94 | 0.93 | — | — | yes |
| eco 4× | fast | 2 V | 1.37 | 1.43 | 0.68 | 0.71 | 0.91 | 0.92 | — | — | yes |
| eco 4× | fast | 8 V | 1.42 | 1.38 | 0.70 | 0.68 | 0.94 | 0.91 | — | — | yes |
| standard 8× | fast | 0.5 V | 1.33 | 1.30 | 0.66 | 0.65 | 0.94 | 0.92 | — | — | yes |
| standard 8× | fast | 2 V | 1.35 | 1.40 | 0.67 | 0.69 | 0.92 | 0.98 | — | — | yes |
| standard 8× | fast | 8 V | 1.38 | 1.39 | 0.69 | 0.69 | 0.94 | 0.93 | — | — | yes |
| hq 16× | fast | 0.5 V | 1.27 | 1.28 | 0.64 | 0.65 | 0.93 | 0.92 | — | — | yes |
| hq 16× | fast | 2 V | 1.36 | 1.33 | 0.68 | 0.66 | 0.95 | 0.96 | — | — | yes |
| hq 16× | fast | 8 V | 1.35 | 1.38 | 0.67 | 0.68 | 0.94 | 0.93 | — | — | yes |

**stereo SIMD cost** — s2 / x1 and s2 / x2 from the same run: median ns per base-rate sample, and cycles where counted (2.00 = two mono instances, 1.00 = one); matches x2 = s2 output bit-identical to x2's

| setting | math | peak | ns s2/x1 b64 | ns s2/x1 b512 | ns s2/x2 b64 | ns s2/x2 b512 | cycles s2/x1 b64 | cycles s2/x2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 1.42 | 1.45 | 0.73 | 0.72 | — | — | yes |
| eco 4× | libm | 2 V | 1.48 | 1.53 | 0.75 | 0.76 | — | — | yes |
| eco 4× | libm | 8 V | 1.52 | 1.51 | 0.76 | 0.77 | — | — | yes |
| eco 4× | fast | 0.5 V | 1.46 | 1.54 | 0.73 | 0.74 | — | — | yes |
| eco 4× | fast | 2 V | 1.51 | 1.55 | 0.75 | 0.77 | — | — | yes |
| eco 4× | fast | 8 V | 1.51 | 1.52 | 0.74 | 0.75 | — | — | yes |
| standard 8× | libm | 0.5 V | 1.40 | 1.38 | 0.70 | 0.70 | — | — | yes |
| standard 8× | libm | 2 V | 1.44 | 1.44 | 0.72 | 0.72 | — | — | yes |
| standard 8× | libm | 8 V | 1.53 | 1.51 | 0.76 | 0.75 | — | — | yes |
| standard 8× | fast | 0.5 V | 1.43 | 1.42 | 0.71 | 0.70 | — | — | yes |
| standard 8× | fast | 2 V | 1.46 | 1.43 | 0.73 | 0.70 | — | — | yes |
| standard 8× | fast | 8 V | 1.47 | 1.50 | 0.74 | 0.74 | — | — | yes |
| hq 16× | libm | 0.5 V | 1.41 | 1.39 | 0.70 | 0.69 | — | — | yes |
| hq 16× | libm | 2 V | 1.47 | 1.39 | 0.73 | 0.72 | — | — | yes |
| hq 16× | libm | 8 V | 1.50 | 1.50 | 0.74 | 0.76 | — | — | yes |
| hq 16× | fast | 0.5 V | 1.37 | 1.40 | 0.69 | 0.71 | — | — | yes |
| hq 16× | fast | 2 V | 1.43 | 1.39 | 0.71 | 0.69 | — | — | yes |
| hq 16× | fast | 8 V | 1.43 | 1.49 | 0.71 | 0.73 | — | — | yes |

**one mono instance, per block** — block time as % of the block's real-time period (b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes

| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |
|---|---|---|---|---|---|---|
| eco | libm | 0.5 V | 7.8 | 11.1 | 7.6 | 10.5 |
| eco | libm | 2 V | 8.7 | 11.6 | 8.6 | 10.9 |
| eco | libm | 8 V | 9.5 | 15.1 | 9.4 | 12.3 |
| eco | fast | 0.5 V | 7.1 | 9.5 | 7.1 | 9.5 |
| eco | fast | 2 V | 8.0 | 11.3 | 7.9 | 10.2 |
| eco | fast | 8 V | 8.7 | 11.6 | 8.8 | 12.4 |
| standard | libm | 0.5 V | 14.6 | 18.7 | 14.7 | 18.8 |
| standard | libm | 2 V | 15.1 | 21.0 | 15.1 | 20.0 |
| standard | libm | 8 V | 16.6 | 22.0 | 16.6 | 21.2 |
| standard | fast | 0.5 V | 13.2 | 16.8 | 13.4 | 16.7 |
| standard | fast | 2 V | 13.8 | 18.4 | 13.6 | 18.1 |
| standard | fast | 8 V | 15.9 | 20.2 | 15.9 | 19.7 |
| hq | libm | 0.5 V | 28.7 | 38.6 | 29.1 | 38.2 |
| hq | libm | 2 V | 29.5 | 39.4 | 29.5 | 39.3 |
| hq | libm | 8 V | 29.8 | 40.3 | 30.1 | 39.8 |
| hq | fast | 0.5 V | 26.7 | 34.0 | 26.7 | 31.5 |
| hq | fast | 2 V | 26.5 | 32.2 | 27.3 | 33.1 |
| hq | fast | 8 V | 28.6 | 37.0 | 28.7 | 39.3 |


### Vector-maths stereo results (#67)

#### Apple silicon — Apple M1 MacBook Air (MacBookAir10,1), macOS 15.6.1 (24G90), Apple clang 17.0.0

Recorded 13 Nov 2026, 09:22:43–09:33:11 CET (10 min 28 s), from `cpp/bench_apple_m1_simd_math.json`. Git
`04fb85c`, `build.dirty` false. 126 configurations: x1, x2, s2 (the #21 per-lane path, the reference) and v2 (the
same StereoStage with `LaneMath::vector`, fast math only) in one round-robin run, so every ratio below compares rows
of the same passes.

- **CPU / compiler / harness:** as for #21 above (Apple clang 17.0.0 as `/usr/bin/g++`, `-std=c++17 -Wall -Wextra
  -pedantic -pthread -O3`; 11 timed passes after one warm-up, 1.99467 s per pass). `meta.simd` = `neon`,
  `meta.fma` = true. `sizeof(StereoStage)` 45 280 B (+16 B for the lane-maths switch and the kernel counter).
- **Scheduling:** `QOS_CLASS_USER_INTERACTIVE`, a normal user-priority thread, not a realtime audio thread. No pinning.
- **Correctness:** all 126 rows deterministic. Every s2 and every v2 row has `matches_x2: true`: on these signals the
  vector-maths stereo output was bit-identical to two mono fast Stages. Non-converged samples: 0 in every row.
- **Power:** AC power, battery 100 % and charged (not charging) before and after, Low Power Mode off. `pmset -g therm`
  recorded no thermal, performance or CPU-power warning, before or after.
- **Load: the machine was in interactive use, apps not closed.** Load average 1.69, 1.86, 1.92 at the start
  (`state_before`; `uptime` in the run log 8 s earlier: 1.62, 1.86, 1.91) and 3.60, 2.69, 2.27 at the end (both
  `state_after` and `uptime` right after the `--table` call). The process list taken just before the run (run log,
  `ps` %CPU): WindowServer 25.2, Grok Bot Helper 22.4, Grok Bot Helper (Renderer) 21.7, coreaudiod 15.1, zsh 5.5,
  claude 4.8, Camo Studio 1.5, all others ≤ 1.1. Lighter than during #21 (load 2.9 → 8.1, OBS running), but not idle.
- **Clock and preemption:** `ghz_on_cpu` 3.150–3.173, `cpu_on_fraction` 0.9965–0.9989. `p_core_fraction_min`
  0.933–0.9999 over all rows, 0.945 or more over the v2 rows; cycle figures marked * had a pass under 99 % on P-cores.
- **Same code, same cost as #21:** the x1, x2 and s2 rows are the #21 code (mono and the per-lane path are
  bit-identical to main). Their cycles per sample are within −1.7 / +2.9 % of the #21 run, except one row (+5.7 %, below).

**Standard 8× (the default), fast math** — ns per base-rate sample frame / % of one core at 48 kHz (median; both
channels for x2, s2 and v2):

| peak | block | x1 | x2 | s2 (#21 path) | v2 (vector maths) | v2 / x1 | s2 / x1 | v2 / x2 | v2 / s2 |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 V | 64 | 2312 / 11.10 | 4620 / 22.18 | 3028 / 14.54 | 2730 / 13.10 | 1.18 | 1.31 | 0.59 | 0.90 |
| 0.5 V | 512 | 2312 / 11.10 | 4626 / 22.20 | 3036 / 14.57 | 2723 / 13.07 | 1.18 | 1.31 | 0.59 | 0.90 |
| 2 V | 64 | 2421 / 11.62 | 4832 / 23.20 | 3211 / 15.41 | 2888 / 13.86 | 1.19 | 1.33 | 0.60 | 0.90 |
| 2 V | 512 | 2415 / 11.59 | 4844 / 23.25 | 3210 / 15.41 | 2879 / 13.82 | 1.19 | 1.33 | 0.59 | 0.90 |
| 8 V | 64 | 2744 / 13.17 | 5468 / 26.25 | 3695 / 17.74 | 3316 / 15.92 | 1.21 | 1.35 | 0.61 | 0.90 |
| 8 V | 512 | 2744 / 13.17 | 5462 / 26.22 | 3707 / 17.80 | 3314 / 15.91 | 1.21 | 1.35 | 0.61 | 0.89 |

**Ranges over drive (0.5 / 2 / 8 V) and block (64 / 512)**, fast math, median ns ratios from this run (cycle ratios
agree within 0.005 per row):

| quality | v2 % core | v2 / x1 ("× mono") | s2 / x1 (#21 path, this run) | v2 / x2 | v2 / s2 |
|---|---|---|---|---|---|
| eco 4× | 7.06–8.71 | 1.13–1.21 | 1.27–1.35 | 0.60–0.61 | 0.89–0.90 |
| standard 8× | 13.07–15.92 | 1.18–1.21 | 1.31–1.35 | 0.59–0.61 | 0.89–0.90 |
| hq 16× | 25.29–28.23 | 1.14–1.18 | 1.27–1.32 | 0.57–0.59 | 0.89–0.90 |

libm, for reference (s2 only; v2 does not apply to libm): s2 / x1 1.57–1.67, s2 / x2 0.78–0.84, as in #21.

The eco 4× low end (1.13) is one row, eco / fast / 8 V / b512, and comes from its x1 denominator: that x1 row took
1608 ns (min 1604 over 11 passes) against 1522 ns for the same row at b64, and 6 % more cycles than in #21, on
P-cores (0.9997). It was not investigated (the per-sample work does not depend on the block). Without it eco is
1.19–1.21.

**Newton iterations** per internal sample (warm-up pass, v2 rows, b64; identical in x2 / s2 / v2 and at b512), left
channel, right channel, and what the 2-lane kernel ran (per sample max(itL, itR)):

| quality | peak | left | right | kernel max | max / left | max / max(left, right) |
|---|---|---|---|---|---|---|
| eco | 0.5 V | 2.222 | 2.198 | 2.295 | 1.033 | 1.033 |
| eco | 2 V | 2.564 | 2.541 | 2.708 | 1.056 | 1.056 |
| eco | 8 V | 2.967 | 2.949 | 3.118 | 1.051 | 1.051 |
| standard | 0.5 V | 2.041 | 2.033 | 2.058 | 1.009 | 1.009 |
| standard | 2 V | 2.179 | 2.158 | 2.240 | 1.028 | 1.028 |
| standard | 8 V | 2.599 | 2.574 | 2.771 | 1.066 | 1.066 |
| hq | 0.5 V | 2.000 | 1.999 | 2.001 | 1.000 | 1.000 |
| hq | 2 V | 2.032 | 2.025 | 2.047 | 1.007 | 1.007 |
| hq | 8 V | 2.253 | 2.229 | 2.363 | 1.049 | 1.049 |

**Where the cost goes** (counters of this run):
- **The vector maths removes the duplicated maths.** v2 executes 0.61–0.63 × s2's instructions and only 1.09–1.14 ×
  one mono instance's (s2: 1.76–1.84 ×). But its IPC is 2.69–2.85, against 3.93–4.05 for s2 and 2.72–3.01 for x1:
  s2's two independent scalar lanes gave the core parallel work that v2 has fused into one dependency chain per
  call (the table lookups need vector-to-integer moves and per-lane loads on that chain). So the cycles fall less
  than the instructions: v2 / s2 = 0.89–0.90.
- **max(itL, itR).** The kernel runs 1.00–1.07 × the left channel's iterations (here the left is always the larger). If
  the cost were all Newton iterations, that would account for at most 0–7 % of v2 / x1. The rest (about 1.10–1.15 ×)
  is the vector maths and the per-sample work around it. That split is an estimate from the iteration counts, not a
  measurement: the cost per sample is not purely proportional to the iterations (resampler, matrices).
- **libm spill (#21 hypothesis):** see cpp/README.md "Vector maths (#67)". The static disassembly agrees (the per-lane
  libm kernel spills and reloads 128-bit registers around its 48 libm call sites, mono libm spills only 64-bit ones),
  but no counter measured the spills' cost. The libm path is unchanged by #67.

**Verdict against the #67 goal (stereo ≈ 1.1 × one mono instance, fast math): not met on the M1.** The vector-maths
path costs **1.13–1.21 × one mono instance** (1.14–1.21 excluding the eco 8 V b512 outlier, 3 of 18 rows ≤ 1.15,
none ≤ 1.10). That is down from 1.27–1.35 × for the #21 per-lane path in the same run (v2 / s2 = 0.89–0.90),
and **0.57–0.61 × two mono instances** (#21: 0.63–0.67). At the default (standard, fast) a stereo instance takes
13.1–15.9 % of a core, against 14.5–17.8 % with the #21 path and 22.2–26.3 % as two mono Stages.

**Against the #21 numbers** (`bench_apple_m1_stereo.json`, 12 Nov): #21 measured s2 / x1 1.25–1.35 (fast) and
1.57–1.67 (libm), s2 / x2 0.63–0.67 (fast) and 0.78–0.83 (libm). This run reproduces those for s2 (fast 1.27–1.35,
libm 1.57–1.67) and adds v2 at 1.13–1.21 / 0.57–0.61.

Caveats specific to this run: one machine, one compiler, a normal user-priority thread on a machine in interactive
use (load 1.7 → 3.6, the apps above running). It is a fanless M1 Air on AC power. The per-block percentiles are wall
time under that load and bound a realtime thread's view from above only. The x86-64 stereo run (SSE2) has still not
been made at the time of this M1 run. The x86-64 stereo run (SSE2), including the vector-maths rows, is below (#76).

`--table` output, verbatim from the run log (the per-block table is x1 only; the s2 / v2 per-block figures are in
the JSON):

**one mono instance** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 6.48 | 6.46 | 1349 (1347–1610) | 1346 (1340–1580) | 4272* | 4265* | 2.22 |
| eco 4× | libm | 2 V | 7.12 | 7.11 | 1484 (1479–1753) | 1482 (1475–1730) | 4694* | 4692* | 2.56 |
| eco 4× | libm | 8 V | 7.88 | 7.85 | 1642 (1634–1868) | 1636 (1633–1663) | 5195* | 5184 | 2.97 |
| eco 4× | fast | 0.5 V | 5.92 | 5.92 | 1233 (1232–1257) | 1232 (1230–1280) | 3907 | 3905 | 2.22 |
| eco 4× | fast | 2 V | 6.56 | 6.54 | 1367 (1361–1628) | 1363 (1360–1387) | 4322* | 4314 | 2.56 |
| eco 4× | fast | 8 V | 7.30 | 7.72 | 1522 (1519–1532) | 1608 (1604–1620) | 4815 | 5094 | 2.97 |
| standard 8× | libm | 0.5 V | 12.23 | 12.14 | 2548 (2536–2573) | 2529 (2525–2790) | 8054 | 8007* | 2.04 |
| standard 8× | libm | 2 V | 12.66 | 12.65 | 2638 (2635–2874) | 2635 (2626–2656) | 8354* | 8338 | 2.18 |
| standard 8× | libm | 8 V | 14.22 | 14.18 | 2963 (2954–3233) | 2953 (2942–2985) | 9376* | 9346 | 2.60 |
| standard 8× | fast | 0.5 V | 11.10 | 11.10 | 2312 (2306–2573) | 2312 (2306–2571) | 7324* | 7316* | 2.04 |
| standard 8× | fast | 2 V | 11.62 | 11.59 | 2421 (2415–2616) | 2415 (2406–2439) | 7658 | 7644 | 2.18 |
| standard 8× | fast | 8 V | 13.17 | 13.17 | 2744 (2728–3008) | 2744 (2730–2966) | 8667* | 8660* | 2.60 |
| hq 16× | libm | 0.5 V | 24.08 | 24.06 | 5017 (4996–5301) | 5013 (5005–5260) | 15886* | 15868* | 2.00 |
| hq 16× | libm | 2 V | 24.34 | 24.32 | 5071 (5048–5330) | 5066 (5046–5355) | 16014* | 16013* | 2.03 |
| hq 16× | libm | 8 V | 25.79 | 25.83 | 5373 (5359–5395) | 5381 (5361–5695) | 17001 | 16976* | 2.25 |
| hq 16× | fast | 0.5 V | 22.18 | 22.05 | 4621 (4606–4875) | 4593 (4588–4614) | 14620* | 14550 | 2.00 |
| hq 16× | fast | 2 V | 22.39 | 22.28 | 4665 (4634–4894) | 4642 (4612–4913) | 14695* | 14653* | 2.03 |
| hq 16× | fast | 8 V | 23.95 | 23.88 | 4991 (4956–5219) | 4976 (4952–5244) | 15721* | 15701* | 2.25 |

**two mono instances, one after the other (stereo-equivalent)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 13.34 | 12.89 | 2779 (2771–2804) | 2686 (2678–2986) | 8797 | 8501* | 2.22 / 2.20 |
| eco 4× | libm | 2 V | 14.24 | 14.16 | 2967 (2953–3210) | 2951 (2943–2980) | 9373* | 9345 | 2.56 / 2.54 |
| eco 4× | libm | 8 V | 15.78 | 16.12 | 3287 (3268–3426) | 3359 (3352–3566) | 10357* | 10629* | 2.97 / 2.95 |
| eco 4× | fast | 0.5 V | 11.81 | 11.80 | 2460 (2452–2489) | 2458 (2453–2693) | 7789 | 7775* | 2.22 / 2.20 |
| eco 4× | fast | 2 V | 13.05 | 13.06 | 2719 (2717–2747) | 2720 (2709–2965) | 8613 | 8600* | 2.56 / 2.54 |
| eco 4× | fast | 8 V | 14.58 | 14.54 | 3037 (3030–3063) | 3028 (3020–3051) | 9606 | 9591 | 2.97 / 2.95 |
| standard 8× | libm | 0.5 V | 24.30 | 24.28 | 5063 (5052–5328) | 5059 (5050–5078) | 16012* | 15997 | 2.04 / 2.03 |
| standard 8× | libm | 2 V | 25.32 | 25.50 | 5274 (5252–5549) | 5312 (5280–5544) | 16659* | 16763* | 2.18 / 2.16 |
| standard 8× | libm | 8 V | 28.36 | 28.39 | 5909 (5884–5971) | 5915 (5890–6264) | 18670 | 18674* | 2.60 / 2.57 |
| standard 8× | fast | 0.5 V | 22.18 | 22.20 | 4620 (4602–4901) | 4626 (4599–4876) | 14621* | 14596* | 2.04 / 2.03 |
| standard 8× | fast | 2 V | 23.20 | 23.25 | 4832 (4825–4873) | 4844 (4814–5109) | 15301 | 15283* | 2.18 / 2.16 |
| standard 8× | fast | 8 V | 26.25 | 26.22 | 5468 (5445–5710) | 5462 (5437–5730) | 17266* | 17243* | 2.60 / 2.57 |
| hq 16× | libm | 0.5 V | 48.43 | 48.29 | 10090 (10072–10501) | 10060 (10025–10347) | 31868* | 31777* | 2.00 / 2.00 |
| hq 16× | libm | 2 V | 48.65 | 48.55 | 10135 (10097–10444) | 10115 (10082–10405) | 32007 | 31947* | 2.03 / 2.02 |
| hq 16× | libm | 8 V | 51.58 | 51.39 | 10746 (10725–10984) | 10706 (10694–10973) | 33944* | 33845 | 2.25 / 2.23 |
| hq 16× | fast | 0.5 V | 44.12 | 44.03 | 9192 (9168–9451) | 9173 (9140–9434) | 29022 | 28987* | 2.00 / 2.00 |
| hq 16× | fast | 2 V | 44.54 | 44.52 | 9279 (9257–9520) | 9276 (9236–9554) | 29328 | 29273* | 2.03 / 2.02 |
| hq 16× | fast | 8 V | 47.62 | 47.69 | 9921 (9886–10259) | 9936 (9888–10187) | 31326* | 31390* | 2.25 / 2.23 |

**stereo SIMD: one StereoStage, the x2 signals as two lanes of one kernel (#21)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 10.56 | 10.56 | 2201 (2186–2249) | 2199 (2187–2440) | 6956 | 6945* | 2.22 / 2.20 / 2.29 |
| eco 4× | libm | 2 V | 11.90 | 11.84 | 2479 (2463–2725) | 2467 (2463–2496) | 7829* | 7819 | 2.56 / 2.54 / 2.71 |
| eco 4× | libm | 8 V | 13.04 | 13.03 | 2717 (2708–2950) | 2715 (2705–2742) | 8588* | 8582 | 2.97 / 2.95 / 3.12 |
| eco 4× | fast | 0.5 V | 7.92 | 7.88 | 1650 (1637–1913) | 1643 (1636–1912) | 5202* | 5192* | 2.22 / 2.20 / 2.29 |
| eco 4× | fast | 2 V | 8.83 | 8.82 | 1839 (1832–1846) | 1838 (1829–2081) | 5819 | 5815* | 2.56 / 2.54 / 2.71 |
| eco 4× | fast | 8 V | 9.75 | 9.78 | 2031 (2024–2062) | 2038 (2031–2296) | 6422 | 6434* | 2.97 / 2.95 / 3.12 |
| standard 8× | libm | 0.5 V | 19.52 | 19.48 | 4066 (4042–4318) | 4057 (4040–4455) | 12827* | 12810* | 2.04 / 2.03 / 2.06 |
| standard 8× | libm | 2 V | 20.66 | 20.61 | 4304 (4275–4629) | 4293 (4271–4524) | 13567* | 13551* | 2.18 / 2.16 / 2.24 |
| standard 8× | libm | 8 V | 23.65 | 23.58 | 4928 (4886–5144) | 4913 (4885–5199) | 15529* | 15506* | 2.60 / 2.57 / 2.77 |
| standard 8× | fast | 0.5 V | 14.54 | 14.57 | 3028 (3020–3303) | 3036 (3017–3279) | 9583* | 9578* | 2.04 / 2.03 / 2.06 |
| standard 8× | fast | 2 V | 15.41 | 15.41 | 3211 (3192–3492) | 3210 (3193–3439) | 10139* | 10119* | 2.18 / 2.16 / 2.24 |
| standard 8× | fast | 8 V | 17.74 | 17.80 | 3695 (3689–3736) | 3707 (3688–3976) | 11711 | 11694* | 2.60 / 2.57 / 2.77 |
| hq 16× | libm | 0.5 V | 37.85 | 37.82 | 7886 (7857–8236) | 7879 (7848–8128) | 24909 | 24880* | 2.00 / 2.00 / 2.00 |
| hq 16× | libm | 2 V | 38.34 | 38.33 | 7988 (7954–8247) | 7986 (7942–8252) | 25227* | 25197* | 2.03 / 2.02 / 2.05 |
| hq 16× | libm | 8 V | 41.67 | 41.48 | 8682 (8635–8951) | 8641 (8598–8915) | 27393 | 27254* | 2.25 / 2.23 / 2.36 |
| hq 16× | fast | 0.5 V | 28.13 | 28.55 | 5861 (5838–6159) | 5947 (5839–6150) | 18515* | 18512* | 2.00 / 2.00 / 2.00 |
| hq 16× | fast | 2 V | 28.58 | 28.59 | 5955 (5929–6224) | 5955 (5919–6214) | 18790* | 18772* | 2.03 / 2.02 / 2.05 |
| hq 16× | fast | 8 V | 31.44 | 31.45 | 6550 (6511–6878) | 6552 (6508–6832) | 20655* | 20630* | 2.25 / 2.23 / 2.36 |

**stereo SIMD with vector maths: the s2 StereoStage with LaneMath::vector (#67)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 7.09 | 7.06 | 1478 (1471–1730) | 1471 (1467–1498) | 4668* | 4658 | 2.22 / 2.20 / 2.29 |
| eco 4× | fast | 2 V | 7.91 | 7.90 | 1647 (1643–1675) | 1645 (1641–1900) | 5214 | 5204* | 2.56 / 2.54 / 2.71 |
| eco 4× | fast | 8 V | 8.71 | 8.71 | 1815 (1809–2172) | 1816 (1811–2198) | 5752* | 5746* | 2.97 / 2.95 / 3.12 |
| standard 8× | fast | 0.5 V | 13.10 | 13.07 | 2730 (2722–2987) | 2723 (2718–2754) | 8630* | 8618 | 2.04 / 2.03 / 2.06 |
| standard 8× | fast | 2 V | 13.86 | 13.82 | 2888 (2867–3216) | 2879 (2860–2901) | 9108* | 9096 | 2.18 / 2.16 / 2.24 |
| standard 8× | fast | 8 V | 15.92 | 15.91 | 3316 (3311–3344) | 3314 (3304–3583) | 10500 | 10483* | 2.60 / 2.57 / 2.77 |
| hq 16× | fast | 0.5 V | 25.39 | 25.29 | 5290 (5246–5589) | 5269 (5253–5291) | 16677* | 16662 | 2.00 / 2.00 / 2.00 |
| hq 16× | fast | 2 V | 25.75 | 25.76 | 5366 (5337–5610) | 5367 (5322–5628) | 16930* | 16910* | 2.03 / 2.02 / 2.05 |
| hq 16× | fast | 8 V | 28.23 | 28.18 | 5881 (5856–6182) | 5871 (5850–6108) | 18568* | 18543* | 2.25 / 2.23 / 2.36 |

**vector-maths stereo cost (#67)** — v2 / x1, v2 / x2 and v2 / s2 from the same run: median ns per base-rate sample, and cycles where counted (1.00 = one mono instance for v2/x1); matches x2 = v2 output bit-identical to x2's

| setting | math | peak | ns v2/x1 b64 | ns v2/x1 b512 | ns v2/x2 b64 | ns v2/x2 b512 | ns v2/s2 b64 | ns v2/s2 b512 | cycles v2/x1 b64 | cycles v2/s2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 1.20 | 1.19 | 0.60 | 0.60 | 0.90 | 0.90 | 1.19 | 0.90 | yes |
| eco 4× | fast | 2 V | 1.20 | 1.21 | 0.61 | 0.60 | 0.90 | 0.90 | 1.21 | 0.90 | yes |
| eco 4× | fast | 8 V | 1.19 | 1.13 | 0.60 | 0.60 | 0.89 | 0.89 | 1.19 | 0.90 | yes |
| standard 8× | fast | 0.5 V | 1.18 | 1.18 | 0.59 | 0.59 | 0.90 | 0.90 | 1.18 | 0.90 | yes |
| standard 8× | fast | 2 V | 1.19 | 1.19 | 0.60 | 0.59 | 0.90 | 0.90 | 1.19 | 0.90 | yes |
| standard 8× | fast | 8 V | 1.21 | 1.21 | 0.61 | 0.61 | 0.90 | 0.89 | 1.21 | 0.90 | yes |
| hq 16× | fast | 0.5 V | 1.14 | 1.15 | 0.58 | 0.57 | 0.90 | 0.89 | 1.14 | 0.90 | yes |
| hq 16× | fast | 2 V | 1.15 | 1.16 | 0.58 | 0.58 | 0.90 | 0.90 | 1.15 | 0.90 | yes |
| hq 16× | fast | 8 V | 1.18 | 1.18 | 0.59 | 0.59 | 0.90 | 0.90 | 1.18 | 0.90 | yes |

**stereo SIMD cost** — s2 / x1 and s2 / x2 from the same run: median ns per base-rate sample, and cycles where counted (2.00 = two mono instances, 1.00 = one); matches x2 = s2 output bit-identical to x2's

| setting | math | peak | ns s2/x1 b64 | ns s2/x1 b512 | ns s2/x2 b64 | ns s2/x2 b512 | cycles s2/x1 b64 | cycles s2/x2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | libm | 0.5 V | 1.63 | 1.63 | 0.79 | 0.82 | 1.63 | 0.79 | yes |
| eco 4× | libm | 2 V | 1.67 | 1.67 | 0.84 | 0.84 | 1.67 | 0.84 | yes |
| eco 4× | libm | 8 V | 1.66 | 1.66 | 0.83 | 0.81 | 1.65 | 0.83 | yes |
| eco 4× | fast | 0.5 V | 1.34 | 1.33 | 0.67 | 0.67 | 1.33 | 0.67 | yes |
| eco 4× | fast | 2 V | 1.35 | 1.35 | 0.68 | 0.68 | 1.35 | 0.68 | yes |
| eco 4× | fast | 8 V | 1.33 | 1.27 | 0.67 | 0.67 | 1.33 | 0.67 | yes |
| standard 8× | libm | 0.5 V | 1.60 | 1.60 | 0.80 | 0.80 | 1.59 | 0.80 | yes |
| standard 8× | libm | 2 V | 1.63 | 1.63 | 0.82 | 0.81 | 1.62 | 0.81 | yes |
| standard 8× | libm | 8 V | 1.66 | 1.66 | 0.83 | 0.83 | 1.66 | 0.83 | yes |
| standard 8× | fast | 0.5 V | 1.31 | 1.31 | 0.66 | 0.66 | 1.31 | 0.66 | yes |
| standard 8× | fast | 2 V | 1.33 | 1.33 | 0.66 | 0.66 | 1.32 | 0.66 | yes |
| standard 8× | fast | 8 V | 1.35 | 1.35 | 0.68 | 0.68 | 1.35 | 0.68 | yes |
| hq 16× | libm | 0.5 V | 1.57 | 1.57 | 0.78 | 0.78 | 1.57 | 0.78 | yes |
| hq 16× | libm | 2 V | 1.58 | 1.58 | 0.79 | 0.79 | 1.58 | 0.79 | yes |
| hq 16× | libm | 8 V | 1.62 | 1.61 | 0.81 | 0.81 | 1.61 | 0.81 | yes |
| hq 16× | fast | 0.5 V | 1.27 | 1.29 | 0.64 | 0.65 | 1.27 | 0.64 | yes |
| hq 16× | fast | 2 V | 1.28 | 1.28 | 0.64 | 0.64 | 1.28 | 0.64 | yes |
| hq 16× | fast | 8 V | 1.31 | 1.32 | 0.66 | 0.66 | 1.31 | 0.66 | yes |

**one mono instance, per block** — block time as % of the block's real-time period (b64 = 1.33 ms, b512 = 10.67 ms), p50 / p99 over all blocks of all passes

| setting | math | peak | b64 p50 | b64 p99 | b512 p50 | b512 p99 |
|---|---|---|---|---|---|---|
| eco | libm | 0.5 V | 6.2 | 8.4 | 6.2 | 8.3 |
| eco | libm | 2 V | 7.2 | 9.9 | 7.2 | 8.5 |
| eco | libm | 8 V | 7.8 | 10.5 | 7.8 | 9.9 |
| eco | fast | 0.5 V | 5.7 | 7.5 | 5.7 | 7.4 |
| eco | fast | 2 V | 6.7 | 8.3 | 6.6 | 7.7 |
| eco | fast | 8 V | 7.2 | 9.4 | 7.7 | 9.5 |
| standard | libm | 0.5 V | 11.9 | 14.9 | 12.0 | 14.5 |
| standard | libm | 2 V | 12.2 | 16.1 | 12.2 | 15.6 |
| standard | libm | 8 V | 14.3 | 17.9 | 14.3 | 17.1 |
| standard | fast | 0.5 V | 10.8 | 13.8 | 10.9 | 13.5 |
| standard | fast | 2 V | 11.2 | 14.8 | 11.2 | 14.5 |
| standard | fast | 8 V | 13.3 | 17.1 | 13.3 | 15.9 |
| hq | libm | 0.5 V | 23.8 | 27.4 | 23.9 | 26.3 |
| hq | libm | 2 V | 23.8 | 29.0 | 23.9 | 28.5 |
| hq | libm | 8 V | 25.1 | 31.8 | 25.2 | 31.8 |
| hq | fast | 0.5 V | 21.9 | 24.6 | 21.9 | 23.3 |
| hq | fast | 2 V | 21.8 | 26.9 | 21.9 | 26.1 |
| hq | fast | 8 V | 23.2 | 30.0 | 23.2 | 29.4 |

| machine | file | status |
|---|---|---|
| Apple M1 MacBook Air | `cpp/bench_apple_m1_simd_math.json` | recorded 13 Nov 2026 (above) |
| x86-64 Linux VM | `cpp/bench_x86_64_linux_stereo.json` | recorded 18 Nov 2026 (below) |

#### x86-64 Linux — shared Intel Xeon VM, conda-forge gcc 14.4.0

Recorded 18 Nov 2026, 10:23:22–10:36:13 CET (Europe/Amsterdam, 12 min 51 s), from the same
`cpp/bench_x86_64_linux_stereo.json` as the #21 x86-64 section above (git `9139efd`, `build.dirty` false). Method,
machine, compiler (`-O3`, portable SSE2, no FMA), pinning and correctness are those of that section: 126
configurations in one round-robin run, every s2 and v2 row bit-identical to x2, counters unavailable.

**v2 / x1, v2 / x2 and v2 / s2 next to the M1 #67 figures** (median ns, fast math only, ranges over quality, drive
and block):

| machine | v2 / x1 | v2 / x2 | v2 / s2 |
|---|---|---|---|
| Apple M1 Air (#67, 13 Nov) | 1.13–1.21 | 0.57–0.61 | 0.89–0.90 |
| x86-64 Linux VM (this run) | 1.27–1.43 | 0.64–0.71 | 0.91–0.98 |

**Ranges over drive and block**, median ns ratios:

| quality | v2 % core | v2 / x1 | v2 / x2 | v2 / s2 |
|---|---|---|---|---|
| eco 4× | 10.00–12.35 | 1.37–1.43 | 0.68–0.71 | 0.91–0.94 |
| standard 8× | 17.59–21.88 | 1.30–1.40 | 0.65–0.69 | 0.92–0.98 |
| hq 16× | 34.40–39.95 | 1.27–1.38 | 0.64–0.68 | 0.92–0.96 |

**Verdict.** On this x86-64 VM the vector-maths path costs **1.27–1.43 × one mono instance**, against 1.37–1.55 ×
for the per-lane path in the same run (v2 / s2 = 0.91–0.98) and **0.64–0.71 × two mono instances**. The ~1.1 × goal
of #67 is not met here either. The vector path is still cheaper than per-lane fast math, but the gain is smaller
than on the M1 (there v2 / s2 was 0.89–0.90, about 10 %). The kernel again ran max(itL, itR) = 1.00–1.07 × the left
channel's iterations. No counters were available.

`--table` output for the v2 rows, verbatim from the same run (the full x1 / x2 / s2 tables are in the #21 x86-64
section above):
**stereo SIMD with vector maths: the s2 StereoStage with LaneMath::vector (#67)** — % of one core at 48 kHz real time (median), ns per base-rate sample (median, min–max over passes), cycles per base-rate sample (median; * = not all on P-cores)

| setting | math | peak | % core b64 | % core b512 | ns b64 | ns b512 | cycles b64 | cycles b512 | Newton it. |
|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 10.00 | 10.16 | 2084 (1950–2339) | 2117 (1890–2382) | — | — | 2.22 / 2.20 / 2.29 |
| eco 4× | fast | 2 V | 10.87 | 11.20 | 2266 (2182–2554) | 2333 (2171–2598) | — | — | 2.56 / 2.54 / 2.71 |
| eco 4× | fast | 8 V | 12.35 | 12.27 | 2572 (2388–2788) | 2557 (2380–2762) | — | — | 2.97 / 2.95 / 3.12 |
| standard 8× | fast | 0.5 V | 18.06 | 17.59 | 3762 (3570–4021) | 3666 (3506–3895) | — | — | 2.04 / 2.03 / 2.06 |
| standard 8× | fast | 2 V | 18.53 | 19.25 | 3860 (3728–4242) | 4009 (3720–4094) | — | — | 2.18 / 2.16 / 2.24 |
| standard 8× | fast | 8 V | 21.88 | 21.75 | 4558 (4224–4820) | 4530 (4336–5653) | — | — | 2.60 / 2.57 / 2.77 |
| hq 16× | fast | 0.5 V | 34.49 | 34.40 | 7185 (7003–7586) | 7166 (6850–7770) | — | — | 2.00 / 2.00 / 2.00 |
| hq 16× | fast | 2 V | 36.40 | 36.53 | 7584 (7029–8151) | 7610 (7060–8025) | — | — | 2.03 / 2.02 / 2.05 |
| hq 16× | fast | 8 V | 39.18 | 39.95 | 8162 (7880–8635) | 8323 (7725–9774) | — | — | 2.25 / 2.23 / 2.36 |

**vector-maths stereo cost (#67)** — v2 / x1, v2 / x2 and v2 / s2 from the same run: median ns per base-rate sample, and cycles where counted (1.00 = one mono instance for v2/x1); matches x2 = v2 output bit-identical to x2's

| setting | math | peak | ns v2/x1 b64 | ns v2/x1 b512 | ns v2/x2 b64 | ns v2/x2 b512 | ns v2/s2 b64 | ns v2/s2 b512 | cycles v2/x1 b64 | cycles v2/s2 b64 | matches x2 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| eco 4× | fast | 0.5 V | 1.38 | 1.43 | 0.69 | 0.69 | 0.94 | 0.93 | — | — | yes |
| eco 4× | fast | 2 V | 1.37 | 1.43 | 0.68 | 0.71 | 0.91 | 0.92 | — | — | yes |
| eco 4× | fast | 8 V | 1.42 | 1.38 | 0.70 | 0.68 | 0.94 | 0.91 | — | — | yes |
| standard 8× | fast | 0.5 V | 1.33 | 1.30 | 0.66 | 0.65 | 0.94 | 0.92 | — | — | yes |
| standard 8× | fast | 2 V | 1.35 | 1.40 | 0.67 | 0.69 | 0.92 | 0.98 | — | — | yes |
| standard 8× | fast | 8 V | 1.38 | 1.39 | 0.69 | 0.69 | 0.94 | 0.93 | — | — | yes |
| hq 16× | fast | 0.5 V | 1.27 | 1.28 | 0.64 | 0.65 | 0.93 | 0.92 | — | — | yes |
| hq 16× | fast | 2 V | 1.36 | 1.33 | 0.68 | 0.66 | 0.95 | 0.96 | — | — | yes |
| hq 16× | fast | 8 V | 1.35 | 1.38 | 0.67 | 0.68 | 0.94 | 0.93 | — | — | yes |


## The earlier informal number (#19) is not this benchmark

`cpp/README.md` "Speed" quotes the `test_core bench` mode from #19. That run used a 1 kHz sine, blocks of 64, gcc 14
`-O2`, the median of 7 runs on the VM, and no counters. #20 removed that mode and its `bench.json`; both are in git
at 70ceacc. That run differs from this harness in signal, flags, repetitions and ordering. It is kept for history
only and is not comparable with the results above.
