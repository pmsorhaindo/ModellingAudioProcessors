# Live Bias, Mode and Filter on the 6AS6 stage (#115)

**What the 6AS6 path does now.** The bias ramps on the audio thread over 20 ms along the mode's bias line. That is
#73's method, ported, with one exact pack per internal sample. A T / P1 / P2 switch crossfades two voices: the new
mode warms up for 10 ms on the live input at zero weight, then a 20 ms linear crossfade follows. The filter keeps
#103's 15 ms crossfade. Drive and Output keep the wrapper's 20 ms ramps. None of this involves the worker. Nothing on
the audio thread builds or inverts a matrix, takes a lock or allocates.

| control | method | worst step, silence | worst step, sine | unsmoothed, sine | limit (silence / sine) |
|---|---|---|---|---|---|
| Bias, full range | the bias line, per internal sample | 6.4e-5 V (T), 4.5e-7 V (P1) | 7.5e-5 V (T), 5.0e-7 V (P1) | 2.4–73 V | 3e-4 V / 2e-2 V |
| Mode, at 1 V | 10 ms warm-up + 20 ms crossfade | 2.2e-13 V | 5.3e-4 V | 0.32–11.6 V (hard), 0.55–3.0 V (5 ms fade) | 3e-4 V / 2e-2 V |
| Mode, at 8 V | the same | — | ≤ 1.3e-8 V for the switch itself; 0.015–2.0 V against "always running" (the new mode's settling, below; reported) | 19.9–86 V | the #73 limits on the switch itself (Michael, 27 Nov 2026) |
| Filter | #103's 15 ms crossfade, unchanged | 1e-25 V | 2.1e-14 V | 1.4–30 V | 3e-4 V / 2e-2 V |
| Drive / Output | the wrapper's 20 ms ramps, unchanged | 0 FS | 0 FS | 0.34–0.62 FS | 1e-6 FS |

The voltages are at the stage output (core, standard 8×, 48 kHz). The plugin maps 1 V there to 1/64 FS at Output 1
(`plugin/src/map_params.h`), so 1 mV = −96 dBFS. The plugin's own numbers, in full-scale units, are under "Through
the plugin". Everything was measured on an Apple M1 (macOS, Apple clang 17, `-O2`) on 27 Nov 2026, with the commands
under "Reproduce". Timings are informal in-test medians and were taken while other runs shared the machine.

## Method

This is #73's method ([RESULTS-smoothing.md](RESULTS-smoothing.md)), unchanged:
- a fast full-range move during a sine (220 Hz, 1 V and 8 V peak at the stage input) and during silence;
- 0.1 s into 0.3 s of signal at 48 kHz, host blocks of 64;
- worst step = max |r[n] − r[n−1]| with r = y − y_ref at the base rate;
- max |y − ref| reported beside it;
- the same block sizes (16 / 64 / 512);
- the same limits.

The issue asks to start from the 12AX7 figures and to record any change. The limits are #73's `LIMIT_STEP_SILENCE`
(0.3 mV), `LIMIT_STEP_SINE` (20 mV) and `MIN_STEP_REDUCTION` (200× core, 30× plugin), and none was changed. One
reading is recorded: the reduction ratio is required only where the unsmoothed change itself exceeds the step
limit. In silence a filter change or a mode change has nothing to step, and both methods then measure 1e-25 V of
rounding.

**References.**
- *Bias* (`live_ref_6as6.h`, `bias_reference`): the same 20 ms line in mA, with an exact pack at every internal sample.
  Each pack is built at the Rk that draws its bias to 1e-14 in log Ik: a secant on the source-stepped DC solve that
  Brent uses, then `build_pack_at_rk`. Each pack is adopted keeping the absolute capacitor histories (`adopt_pack`),
  and the stage runs with its own resampler. This reference is independent of the bias line, its table and its rest
  maps. Unsmoothed: one exact pack (`build_pack`) adopted at the block boundary.
- *Mode* (`crossfade`): two references.
  - *Always running*: (1 − w) A + w B, where A and B are the two modes each run continuously from the start and w is
    the method's weight. This is the best any switch could do.
  - *Started at the switch*: the same crossfade to B′, the new mode started from its operating point at the switch
    instant. This measures what the switch itself adds.

  The unsmoothed baselines are a hard switch to B′ at the block boundary, and the engine's pre-#115 behaviour: a
  5 ms fade-out, a swap to a B′ started then, and a 5 ms fade-in.
- *Filter*: the ideal crossfade of the two positions at the internal rate, where `PostFilter` mixes them. It uses two
  stages run through `process_internal`, mixed with weight min(1, (t + 1)/n), and one decimator. Unsmoothed: the
  same with the weight stepped at the switch.

## Bias: the bias line, per internal sample

`dk6as6_core.h`, section "live bias": `BiasLine`, `build_line`, `StageT::line_plan` / `line_follow`.

- **Matrices.** As in the 12AX7 stage, Rk is the one resistor from the cathode to ground. By Sherman–Morrison every
  AC matrix A..K is affine in β(g) = dg / (1 + dg Si(g0)[k][k]). The packs at the two ends of the mode's range
  (exact, `build_pack`) give the matrices at any Rk. Measured against `build_pack` at Brent's Rk, at ten biases per
  mode: 4.7e-12 relative (tolerance `TOL_COEF` = 1e-10, the 6AS6 core's).
- **Rest constants: on the DC network, not from (I − A)⁻¹.** The first build of this PR took x0 = (I − A)⁻¹ (B s + C i0)
  from the trapezoidal matrices, as #73 does. On the 6AS6 stage that is ill-conditioned: Cout's τ = 0.127 s at
  384 kHz puts an eigenvalue of A within 2e-5 of 1. The operating point came out up to 2.7e-5 V from `build_pack`'s
  (scratch run, 27 Nov). The DC network has Rk as the same rank-one term, with its own Si. So the DC node voltages for
  given valve currents i0, and with them x0 = Gx Nx nodes, v0, p_dc and y_dc, are affine in a DC weight β_dc
  (`BiasLine::rest`). Against `build_pack`: 3.3e-10 V / mA.
- **Rk(Ik).** A 65-node table, uniform in log Rk, interpolates log Rk against log Ik with a cubic Hermite. Its error
  is 1.3e-6 relative in Rk (worst in T, where Rk spans 4.1–80 kΩ), against Brent. `BiasLine::exact()` refines it on
  the audio thread: Newton on log Rk, each step one DC solve (the stage's solver on the DC network at full supply,
  warm-started from the table's currents). It takes at most 3 steps and lands at 4.2e-14 relative to Brent's Rk; the
  operating point agrees with `build_pack`'s to 3.3e-10 V / mA. A move plans its end point with `exact()` and corrects
  the table's Rk linearly along the move, so the move starts on the running pack's Rk and lands on the exact one.
  There is no jump at either end.
- **Why per internal sample.** The 6AS6 stage has no cathode bypass capacitor, so Rk is directly in the signal path:
  a step of Rk steps the output at once. In the 12AX7 stage Ck smooths it, and #73 updates per base sample. An
  earlier build of this PR updated the 6AS6 line per base sample, as #73 does, and stepped by 17–64 mV on these moves
  (scratch run, 27 Nov): over the 20 mV sine limit, and 100× over the silence limit. One pack per internal sample
  removes that. Within a base sample the bias steps linearly, as in the reference.
- **The ramp** (`LiveStage`): `set_bias()` is read once per block and ramps linearly over 20 ms of base-rate time
  (#73's, and the wrapper's Drive / Output ramps). A new target re-plans the ramp from where it is. The ramp runs in
  the union of the modes' ranges (0.1–0.6 mA), and each voice clamps it to its own mode: T stops at the plugin's
  0.5 mA cap (#114 / #132), so a T stage asked for 0.6 mA runs at 0.5 mA. `reset()` jumps to the target.
- **At rest after a move** (then `reset()`), against a stage prepared at the target, on a 2 V sine: 3.6e-7 V
  (`TOL_BUILT_ABS` = 1e-5 V).

### Fast full-range moves, core (`test_live_6as6`)

Standard 8×, 48 kHz, 20 ms. Each cell is the worst step, with max |y − ref| and the unsmoothed swap in brackets.

| mode | move | silence | 1 V sine | 8 V sine | the move itself (vs constant bias) |
|---|---|---|---|---|---|
| P1 | 0.1 → 0.6 mA | 0.45 µV (2.9 µV; 46.5 V) | 0.50 µV (3.2 µV; 39.2 V) | 0.45 µV (3.1 µV; 2.45 V) | 58–106 V |
| P1 | 0.6 → 0.1 mA | 0.43 µV (4.5 µV; 44.9 V) | 0.37 µV (4.5 µV; 38.1 V) | 0.25 µV (4.3 µV; 2.37 V) | 56–100 V |
| T | 0.1 → 0.5 mA | 63.5 µV (225 µV; 73.4 V) | 74.5 µV (263 µV; 64.9 V) | 45.7 µV (91 µV; 15.8 V) | 91–136 V |
| T | 0.5 → 0.1 mA | 60.6 µV (200 µV; 62.3 V) | 51.0 µV (171 µV; 54.3 V) | 38.8 µV (74 µV; 2.48 V) | 81–126 V |
| P2 (experimental) | 0.1 → 0.6 mA | 0.26 µV (5.5 µV; 29.4 V) | — | 0.10 µV (11.6 µV; 1.83 V) | 36–39 V |
| P2 (experimental) | 0.6 → 0.1 mA | 0.24 µV (1.6 µV; 27.8 V) | — | 0.12 µV (0.56 µV; 1.80 V) | 35–38 V |

HQ 16× (`--hq`, the same 18 moves with P2 at 1 V too): the worst step is 68.9 µV in silence and 80.7 µV with a sine
(both T), at least 63 930× below the unsmoothed swap. P1 and P2 are ≤ 0.51 µV. At rest against a prepared stage the
difference is 3.9e-7 V. The line's matrices are within 6.4e-12 relative and its rest constants within 4.1e-10 V / mA.

- Every limit is met. The worst cases are T's: 74.5 µV against 20 mV with a sine, and 63.5 µV against 300 µV in
  silence (4.7× margin). Every move beats its unsmoothed swap by at least 63 849×.
- T's residual is the table's mid-move Rk error (1.3e-6 relative). It is smooth, and the ends are exact. P1 and P2
  are at the 1e-7 V level.
- **The thump stays.** A full-range bias move in 20 ms moves the stage output by 36–136 V (0.56–2.1 FS at Output 1).
  This is the circuit's own operating-point transient, as #73 found on the 12AX7 path, and it is in the continuous
  reference. It is larger here than on the 12AX7 path (22–36 V): the 6AS6 bias range spans 6×, against the 12AX7's
  4× Rk range, and there is no Ck. A slower host move shrinks it.

## Mode: two voices, a warm-up and a crossfade

`LiveStage`: two `StageT` voices per channel. The bias lines of all three modes are built once per slot (`build_lines`)
and shared by both channels.

1. `set_mode()` is read once per block. A switch installs the idle voice at the new mode and the current bias
   (`install_line`). That is a pack copy and `exact()`, bounded at 6 Newton steps of one DC solve each; there is no
   matrix build. The voice starts from its operating point, with its resampler and filter states cleared and the
   filter at the requested position.
2. The new voice runs for 10 ms on the live input at zero weight, so its resampler and fast valve states fill.
3. A 20 ms linear crossfade of the two voices follows, at the base rate. Then the old voice stops.

A bias ramp that is running moves both voices. A mode change during a switch waits for it to finish. The CPU is twice
the 6AS6 stage's for those 30 ms.

**Why not a hard switch, or the old fade.** The modes differ hugely in their operating points and gains: a hard
switch steps by 7.7–11.6 V at 1 V and by 20–86 V at 8 V. The engine's pre-#115 5 ms fade-out / fade-in dips the
output to silence for 10 ms and fades in a cold stage. Against the always-running reference that is a step of
0.55–3.0 V at 1 V. A topology morph ("source stepping") would move the anode's operating point and so thump through
Cout, by tens of volts, like the bias thump. The crossfade avoids that thump.

### Core results (`test_live_6as6`, all six ordered pairs, 0.45 and 0.6 mA)

| signal | against always running | the switch itself (vs B′ started at the switch) | hard switch | 5 ms fade out / in |
|---|---|---|---|---|
| silence | 2.2e-13 V | ≤ 7.8e-9 V | 1e-11–1.9e-10 V | 1e-13–1e-12 V |
| 1 V sine | 7.3e-5–5.3e-4 V (max \|y − ref\| ≤ 0.38 V) | ≤ 7.8e-9 V | 0.32–11.6 V (≥ 1 596×) | 0.55–3.0 V (≥ 1 231×) |
| 8 V sine | 1.5e-2–2.0 V (max \|y − ref\| ≤ 17.7 V) | ≤ 7.8e-9 V | 19.9–86 V | 8.3–19.1 V |

At HQ 16× (`--hq`, all six pairs at both biases) the numbers are the same:
- silence 1.7e-13 V;
- 1 V: 5.3e-4 V against always running, at least 1 849× below the hard switch and 1 257× below the 5 ms fade;
- reduction minimums over the full run (`--hq`, which also runs every Standard case): Standard 1 596× against the
  hard switch (P2 → P1 at 0.60 mA, 1 V: 0.391 V hard, 2.45e-4 V smoothed) and 1 231× against the 5 ms fade; HQ
  1 849× and 1 257×. The default run (CI) skips some 0.60 mA pairs, so its Standard minimum against the hard switch
  is 2 255×. All are above the 200× limit (PR #161 review, round 1);
- 8 V: 2.05 V against always running;
- the switch itself: 1.3e-8 V.

**At 8 V the residual against "always running" is the new mode's settling, not a step of the switch.** The
measurements say so in three ways:
- **The same mode alone shows it.** The mode started at the switch, compared with the same mode always running (no
  switch at all), has a worst step after the fade of 1.29 V (P1 → T at 0.45 mA), 1.88 V (T at 0.6 mA) and
  0.015–0.03 V into P1 or P2. The switch's residual is 1.39 V, 2.04 V and 0.015–0.036 V. In the last 50 ms
  of the run, 150–200 ms after the switch, the new mode is still up to 4.3–6.7 V from "always running" (T).
- **The switch itself adds nothing measurable.** It adds 7.8e-9 V against B′. During the warm-up the residual is 0
  by construction.
- **It is the circuit's slow memory.** It does not shrink when the stage has run longer before the switch (1.42 V at
  1.0 s, 1.39 V at 0.1 s), and carrying Cin's charge or the old voice's capacitor deviations into the new voice does
  not change it (1.39 V either way; below). At 8 V the grid conducts and the plate swings asymmetrically. The mode's
  own coupling states, Cout (Ra + Rl) = 0.127 s and the grid-leak charge on Cin, take ~0.1–0.3 s to reach where an
  always-running mode is. A new mode started at any instant has to build them from rest. A hardware switch would add
  its operating-point thump on top.

Only a new mode that has always been running (three voices, three times the CPU, always) would meet the #73 sine
limit against that reference at 8 V. The issue does not define a mode-switch limit at 8 V. Asserting #73's against
"always running" would need a method that is not in this scope. Asserting it only against "started at the switch"
would be a change of reference, so it was put to Michael on PR #161.

**Decision (Michael, 27 Nov 2026, on PR #161): "accept 10 ms + 20 ms switch, report the numbers".**
- The switch stays at a 10 ms warm-up and a 20 ms crossfade. The 100 ms warm-up from the sweep below (0.087 V, at
  the cost of 120 ms latency per switch) is not adopted.
- Acceptance for v0.1 is on the switch itself: it adds ≤ 1.3e-8 V (standard and HQ; ≤ 1.8e-7 FS through the
  plugin), within the #73 limits on every signal.
- The post-switch settling against the always-running reference is reported, not asserted: 0.015–2.0 V at 8 V in
  the core, and ≤ 0.018 FS at Drive 8 through the plugin.

The tests follow the decision:
- against "always running", the #73 limits are asserted in silence and at 1 V / Drive 1;
- against "started at the switch", they are asserted everywhere;
- at 8 V / Drive 8, the always-running numbers are printed without being asserted.

**Option sweep** (`test_live_6as6 --options`; worst step against always running, over P1 ↔ T, P1 ↔ P2 and T ↔ P2
at 0.45 mA; standard):

| warm-up | fade | 1 V | 8 V | max \|y − ref\|, 8 V | switch latency |
|---|---|---|---|---|---|
| 0 | 20 ms | 0.031 V | 1.71 V | 11.3 V | 20 ms |
| 0 | 40 ms | 0.015 V | 1.10 V | 10.0 V | 40 ms |
| 5 ms | 20 ms | 4.7e-4 V | 1.58 V | 10.7 V | 25 ms |
| **10 ms** | **20 ms** | **4.5e-4 V** | **1.39 V** | **10.5 V** | **30 ms** |
| 10 ms | 40 ms | 2.4e-4 V | 0.85 V | 9.6 V | 50 ms |
| 20 ms | 20 ms | 4.0e-4 V | 1.07 V | 10.0 V | 40 ms |
| 50 ms | 40 ms | 1.6e-4 V | 0.24 V | 7.3 V | 90 ms |
| 100 ms | 20 ms | 2.0e-4 V | 0.087 V | 5.7 V | 120 ms |
| 100 ms | 40 ms | 9.9e-5 V | 0.051 V | 4.8 V | 140 ms |

- Without a warm-up the new voice's onset is faded in: its resampler fills from zero, and the input steps into a
  resting stage. That is 0.031 V at 1 V, over the sine limit. 5 ms removes it, and 10 ms leaves margin (45× under
  the limit).
- Beyond that, a longer warm-up only buys settling at 8 V, at the cost of latency. 10 ms + 20 ms is the default.
- Carrying state (scratch run, 27 Nov, 10 ms + 20 ms) did not help:

  | what is carried | 1 V | 8 V | max \|y − ref\|, 8 V |
  |---|---|---|---|
  | nothing (the default) | 4.5e-4 V | 1.39 V | 10.5 V |
  | Cin's absolute history | 4.2e-4 V | 1.39 V | 15.3 V |
  | Cin's and Cout's deviation | 3.8e-4 V | 1.39 V | 10.0 V |
  | every capacitor's deviation | 3.8e-4 V | 1.39 V | 10.0 V |

  The default carries nothing.

## Filter

There is no change: #103's `PostFilter` already runs both filters all the time and crossfades its output linearly
over 15 ms at the internal rate. Against the ideal internal-rate crossfade it is exact to rounding (2.1e-14 V). A hard
switch steps by 1.4–30 V on a 3150 Hz tone. With a voice switch, both voices get the position, and a newly installed
voice starts settled at it.

## Drive and Output

These are unchanged: the wrapper's 20 ms per-sample ramps, with Drive applied before the stage and so before its
upsampler. There is no measured reason to change them, and through the 6AS6 path they are bit-equal to the ideal
ramps (0 FS). The aliasing check of `plugin/tests/smoothing_test.cpp` now runs on the 6AS6 path too. Drive moves
1 → 8 on a 0.9 FS tone, and the base-rate ramp is compared with the same ramp per internal sample after the
upsampler:

| tone | 12AX7 path (#73) | 6AS6 path | an unsmoothed jump, 6AS6 |
|---|---|---|---|
| 1037 Hz | −114.4 dB | −115.2 dB | −14.0 dB |
| 9037 Hz | −108.9 dB | −115.4 dB | −14.1 dB |
| 19037 Hz | −99.5 dB | −107.0 dB | −14.1 dB |

The limit is −90 dB in band (#73's, unchanged).

## Through the plugin (`plugin/tests/smoothing_test.cpp`, section 5)

The engine runs in stereo (L 220 Hz, R 330 Hz at 0.7×, 0.9 FS), standard, 48 kHz, host blocks of 64, with host
steps at sample 5120. Each value is the worst step over both channels, in FS at the plugin output, against references
built per channel from the direct core. The limits are the plugin's #73 limits (the core limits through 1/64 FS per
volt).

| change | silence | sine, Drive 1 | sine, Drive 8 | unsmoothed | limit |
|---|---|---|---|---|---|
| P1 Bias 0.1 ↔ 0.6 mA | 6.0e-8 FS | 1.2e-7 FS | 1.2e-7 FS | 0.70–1.27 FS | 4.69e-6 / 3.13e-4 FS |
| T Bias 0.1 ↔ 0.5 mA | 9.5e-7 FS | 1.1e-6 FS | — | 0.97–1.28 FS | same |
| Mode P1 ↔ T, P1 ↔ P2, vs always running | 2.9e-15 FS | ≤ 1.2e-5 FS | 3.3e-4–1.8e-2 FS (reported) | 0.085–0.84 FS (hard) | same (silence, Drive 1) |
| Mode, the switch itself | ≤ 1.8e-7 FS | ≤ 1.8e-7 FS | ≤ 1.8e-7 FS | | same |
| Drive 0 ↔ 8, Output 0 ↔ 4 | 0 | 0 | — | 0.34–0.62 FS | 1e-6 FS |

**Block sizes 16 / 64 / 512.**
- Drive, Bias, Output, Mode and Filter stepped together at sample 5120 give the same output bit for bit. In the core
  too: bias, mode and filter changes, including a re-plan mid-ramp and a mode change during a switch, are bit-exact.
- Every step happens at a base sample of the stream, never at a block boundary the host chose.
- An automation curve of Drive, Bias and Output differs between block sizes by 0.022 FS (4.3 % of peak). The lag
  bound, the same run with the curve read 512 samples late, is 0.074 FS, as #73 states its tolerance.

**Engine (`engine_test`, 4c).** A P1 → T and 0.45 → 0.35 mA change on the running stage lands within 40 ms of
audio, without a rebuild or the worker. It makes no allocation, lock or hooked syscall (the `rt_hooks` tripwire), and
after a reset it nulls against a core stage prepared there to 1.5e-8 FS. The existing live-switch section, run under
the hooks, now goes through the live path for every 6AS6 mode and bias change.

## Realtime and cost

- **No allocation, no lock, no syscall** in `LiveStage::process` / `Engine::process`. Allocations are counted in
  `test_live_6as6` and `smoothing_test`, and locks and syscalls through `rt_hooks` in `engine_test`: 0 everywhere.
- **Bounded work.**
  - Per internal sample of a bias move: a table lookup (a 6-step binary search and a Hermite) plus 280
    multiply-adds, 0.23–0.44 µs.
  - Per plan (a new target) and per voice install: `exact()`, 2.1–2.3 µs typical, at most 6 DC solves of at most
    3 × 50 Newton iterations.
  - Per mode switch: twice the stage for 30 ms.
- **CPU while a bias ramp runs**, an informal ratio only: one line update is about 150 ns (table 32 ns, matrices
  85 ns, rest constants 30 ns; `-O2`, M1), so 8 per base sample add about 1.2 µs to the 7–9 µs of one base sample of
  the stage (1 V, standard). That is roughly +15 % for the 20 ms of the ramp, and nothing at rest. The first figure
  this file gave, 0.23–0.44 µs per update (+25–40 %), included loop overhead and was taken on a loaded machine.
- **Callback CPU around a mode switch: over budget on this M1 in stereo** (PR #161 review, round 1;
  `plugin/tests/switch_cpu.cpp`, `map_switch_cpu`, the review's measurement: 48 kHz, host blocks of 64 = 1.333 ms,
  stereo L 220 Hz 0.9 FS / R 330 Hz 0.63 FS, 30 P1 ↔ T switches at 0.45 mA, thread CPU per `process()` call; load
  average 5–10 on this shared machine, so the absolute figures move by ±25 % between runs):

  | quality | before switching (median, % of the period) | during a switch | during a switch with a bias ramp |
  |---|---|---|---|
  | Standard 8× | 0.91–1.03 ms (68–77 %) | 1.78–2.34 ms (133–176 %), every call over | 2.20–2.78 ms (165–208 %) |
  | HQ 16× | 2.0–2.3 ms (150–171 %): over before any switch | 4.2–4.7 ms | 4.7–5.0 ms |

  - The two voices make every switch callback cost twice the stage. The 6AS6 stereo Standard stage alone already
    takes 68–77 % of a 64-sample period here, so twice that cannot fit, whatever the buffer size: the cost per sample
    is the same, and so is the share of real time.
  - Under a Mach time-constraint policy, as an audio I/O thread runs (`--rt`), the overrunning thread is demoted, and
    a switch callback takes 3.7–3.8 ms.
  - Expect a dropout per mode switch on this Mac in stereo at Standard. Mono (one voice pair), and hosts where the
    stage takes under about 45 % of the period, have the headroom. The cost of the stage itself is #135's (fast
    maths, a lazier Jacobian, a two-lane stereo kernel). HQ stereo does not fit this machine even without switching.
  - The one-voice alternative was measured and **not** adopted, because it fails the quality limit: fade the old
    voice into a linear stand-in (the input, delayed by the stage latency, times the old voice's least-squares gain),
    warm the new voice up behind it, fade the new voice in. It never runs two voices, but its worst step at 1 V is
    0.023–0.23 V against #73's 20 mV sine limit, 1–11× over (scratch run, 27 Nov, offline model, P1 ↔ T and P1 → P2,
    5–10 ms phases). The two-voice crossfade passes that limit by 38×.
  - Which way to go is Michael's call on PR #161: keep the crossfade, which meets every step limit and needs twice
    the stage's CPU for 30 ms, until #135 makes the stage cheaper; or a one-voice switch that fits the CPU but
    exceeds the step limit.
- **Non-RT.** `build_lines` (three modes) takes about 20 ms on the M1. It runs on the worker for a quality or valve
  change, and in `Engine::prepare`.
- **Memory.** A slot holds `Lines` (26 KB) and two `LiveStage` (44 KB each), on the heap, allocated once.

## NaN / inf and out-of-range values

- NaN / inf audio during a switch and a ramp gives finite output: each voice replaces non-finite input samples by 0
  and recovers a non-finite state, as `Stage` does.
- `set_bias()` ignores NaN / inf. A finite out-of-range bias is clamped to the union of the ranges, then to each
  mode's.
- `set_mode()` ignores values outside P1 / T / P2. The engine also clamps every target before passing it on.
- A failed voice install (not seen) keeps the old mode, counts it (`switch_failures`) and is not retried until the
  target changes. A failed end point of a bias plan keeps the bias where it is (`Stats::line_failures`).

## Out of scope (left)

- Valve and quality changes still go through the worker and the 5 ms fade-out / fade-in.
- The paths' different levels are #22.
- The T panel range beyond 0.5 mA is #132 / #146.

## Reproduce

```bash
cd prototypes/fullstage
c++ -std=c++17 -O2 -pthread -o test_live_6as6 cpp/test_live_6as6.cpp && ./test_live_6as6   # --hq, --options
python -m pytest -q cpp/tests/test_cpp_live_6as6.py
cd ../.. && cmake --build plugin/build --target map_smoothing_test map_engine_test \
  && plugin/build/bin/Release/map_smoothing_test && plugin/build/bin/Release/map_engine_test
```
