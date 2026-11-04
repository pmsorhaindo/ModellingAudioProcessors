# C benchmark of the DK kernel (4 Nov 2026)

The laptop/box CPU figures in `../octatrack-budget.md` §4 come from this benchmark. It is a straight C port of the
per-sample kernel in `dk/solver.py` + `dk/stage.py` **as they were at af94e3f** (before #14 and #15): 6 states,
2 nonlinear ports, pnjlim + line search, libm `exp`/`log1p`/`pow`, Newton tolerance `TOL` (1e-9 V, as Python then),
50-iteration cap, and a polyphase version of the one-shot Kaiser FIR of that time (64·os−1 taps) up and down.
Mono, 1 kHz sine, 48 kHz base rate, 1 s of audio, best of 5.

It does **not** model current main: no extrapolated warm start or 1e-6 V tolerance (#15, PR #30), no half-band
resamplers (#14, PR #31). Re-measuring belongs to #20.

| file | what |
|---|---|
| `gen.py` | writes `mats.h` (DK matrices, initial state and FIR taps for os = 2, 4, 8, 16) from `dk.stage.Stage` |
| `bench.c` | the benchmark. `R` sets the sample type, `R_IS_FLOAT` selects the float libm calls, `TOL` the Newton tolerance. The `bench()` stub and the first `RUN` macro are unused leftovers |

Generated files (`mats.h`, binaries) are not committed (`.gitignore`).

## Rebuild
`gen.py` needs `dk.stage.os_filter` and `Stage(..., os=)`, which #14 and #17 removed, so run it against a checkout
of af94e3f:
```bash
git worktree add /tmp/af94e3f af94e3f
python gen.py /tmp/af94e3f/prototypes/fullstage                                 # -> mats.h
gcc -O2 -o bd bench.c -lm                                                       # double, TOL 1e-9 V
gcc -O2 -DR=float -DR_IS_FLOAT=1 -DTOL=1e-4f -o bf bench.c -lm                  # float, TOL 1e-4 V
./bd; ./bf
```
Checked on 4 Nov 2026 (gcc 14.2.0, Debian): `gen.py` at af94e3f regenerates the original `mats.h` byte for
byte, and both commands rebuild the original `bd` / `bf` binaries byte for byte. The original command lines
were not recorded; they are the ones that reproduce the binaries. Float uses 1e-4 V because 1e-9 V is not reachable
in float32 at ~200 V and the solver then runs to the 50-iteration cap.

## Recorded output (the box: Intel Xeon VM, 8 vCPU, clock not exposed)
`bd` (double):
```
double os= 2 amp= 0.5V   1422.0 ns/base-sample  (FIR 128.3 ns)  mean NR iters/internal sample 3.75  CPU% of one core @48k: 6.83%  [sink 2.87244e+09]
double os= 4 amp= 0.5V   2610.1 ns/base-sample  (FIR 276.1 ns)  mean NR iters/internal sample 3.39  CPU% of one core @48k: 12.53%  [sink 1.43621e+09]
double os= 8 amp= 0.5V   4767.9 ns/base-sample  (FIR 531.2 ns)  mean NR iters/internal sample 3.00  CPU% of one core @48k: 22.89%  [sink 7.18098e+08]
double os=16 amp= 0.5V   9437.7 ns/base-sample  (FIR 1055.3 ns)  mean NR iters/internal sample 2.99  CPU% of one core @48k: 45.30%  [sink 3.59047e+08]
double os= 2 amp= 4.0V   1526.8 ns/base-sample  (FIR 129.1 ns)  mean NR iters/internal sample 4.07  CPU% of one core @48k: 7.33%  [sink 2.87255e+09]
double os= 4 amp= 4.0V   2690.6 ns/base-sample  (FIR 273.4 ns)  mean NR iters/internal sample 3.71  CPU% of one core @48k: 12.91%  [sink 1.43632e+09]
double os= 8 amp= 4.0V   5109.3 ns/base-sample  (FIR 532.0 ns)  mean NR iters/internal sample 3.52  CPU% of one core @48k: 24.52%  [sink 7.1821e+08]
double os=16 amp= 4.0V   9744.3 ns/base-sample  (FIR 1055.5 ns)  mean NR iters/internal sample 3.31  CPU% of one core @48k: 46.77%  [sink 3.59159e+08]
```
`bf` (float):
```
float  os= 2 amp= 0.5V   1038.8 ns/base-sample  (FIR 128.2 ns)  mean NR iters/internal sample 2.84  CPU% of one core @48k: 4.99%  [sink 2.87225e+09]
float  os= 4 amp= 0.5V   1954.1 ns/base-sample  (FIR 264.7 ns)  mean NR iters/internal sample 2.56  CPU% of one core @48k: 9.38%  [sink 1.43584e+09]
float  os= 8 amp= 0.5V   3349.7 ns/base-sample  (FIR 534.4 ns)  mean NR iters/internal sample 2.00  CPU% of one core @48k: 16.08%  [sink 7.17915e+08]
float  os=16 amp= 0.5V   6697.8 ns/base-sample  (FIR 1051.1 ns)  mean NR iters/internal sample 2.00  CPU% of one core @48k: 32.15%  [sink 3.59042e+08]
float  os= 2 amp= 4.0V   1281.6 ns/base-sample  (FIR 127.4 ns)  mean NR iters/internal sample 3.25  CPU% of one core @48k: 6.15%  [sink 2.87439e+09]
float  os= 4 amp= 4.0V   2276.1 ns/base-sample  (FIR 264.5 ns)  mean NR iters/internal sample 2.83  CPU% of one core @48k: 10.93%  [sink 1.4368e+09]
float  os= 8 amp= 4.0V   4320.5 ns/base-sample  (FIR 531.4 ns)  mean NR iters/internal sample 2.64  CPU% of one core @48k: 20.74%  [sink 7.1764e+08]
float  os=16 amp= 4.0V   8417.2 ns/base-sample  (FIR 1053.3 ns)  mean NR iters/internal sample 2.51  CPU% of one core @48k: 40.40%  [sink 3.58773e+08]
```
`sink` only keeps the compiler from removing the work. The FIR column times the resampler alone on a dummy signal.
