# M1 timing of the shipping stages, and the v0.1 default Quality (#143, 27 Nov 2026)

**Not a clean timing, so #143's clean-M1 requirement is still open.** This Mac was shared with other work for the whole measurement. Another agent job was running
at the same time, for #115 according to the dispatcher. The load average was 5–19 on 8 cores (4 P + 4 E), and at the
start one interactive app alone used about 1.5 cores. The P-cores ran at 2.0–2.4 GHz, against
3.15–3.17 GHz in the quiet 13 Nov record. The thread was on a CPU for 86–98 % of the wall time, and 2–25 % of its
cycles (median pass) ran on E-cores. By the test in `cpp/m1_spread.py` ("clean"), **0 of the 84 configurations is
clean**, and the three run medians of a configuration spread by 1.07–1.41×. The wall-time figures below therefore
overstate what the stages cost on a quiet M1, and are reported as measured. They are not presented as clean.

## Method
- **Harness:** the #20 benchmark unchanged (`cpp/BENCHMARK.md`): `python -m cpp.bench`, `-O3`, Apple clang 17,
  48 kHz, the 2 s Karplus–Strong guitar phrase, one warm-up and 11 timed round-robin passes per run, the determinism
  check, `QOS_CLASS_USER_INTERACTIVE`, AC power, Low Power Mode off, no thermal warning recorded.
- **Machine:** Apple M1 MacBook Air (MacBookAir10,1), macOS 15.6.1. Source at `main` 1124111, clean tree.
- **What is timed:** each stage alone, one instance per configuration, not the plugin and not Live.
  - 12AX7: `--only libm`, the plugin's maths (`dkcore::StereoStage` with `TriodeMath::libm`).
    Rows: s2, the `StereoStage` the plugin runs, with two different signals on its lanes; x1, one scalar `Stage`,
    a reference that the plugin never runs; x2, two scalar stages.
    - **The plugin's 12AX7 mono is not timed here.** On a mono track, `Engine::process_chunk` feeds the one input
      to both lanes of the same `StereoStage` (`plugin/src/map_engine.cpp`). That is the s2 kernel with one signal
      on both lanes: the s2 work, with per-sample Newton iterations max(itL, itR) = itL instead of the max over two
      signals. So the s2 row is expected to be its upper bound, but that is an inference, not a measurement.
  - 6AS6: the default setting (P1, 0.45 mA, grid current, filter off, k140), x1 and x2. x2 is the plugin's
    stereo, two `dk6as6::Stage` on one thread. x1 is the plugin's mono, which runs the left stage only. Also
    T 0.5 mA, the top of T's envelope, x1.
  - Both block sizes, 64 and 512, at 0.5 / 2 / 8 V peak.
- **Repeats:** three runs per core, one after another and interleaved (12AX7 r1, 6AS6 r1, 12AX7 r2, ...), from
  08:13 to 09:13 UTC. The records are `bench_12ax7_r{1,2,3}.json` and `bench_6as6_r{1,2,3}.json`, the full tables
  come from `python -m cpp.m1_spread cpp/m1_timing/bench_<core>_r*.json`, and `cpp/tests/test_m1_spread.py` tests
  the summary and the records.
- **Three figures per configuration**, all as % of one core at 48 kHz real time:
  - **measured:** the median over the three runs of each run's median pass (`pct_core`), with the range of the
    three run medians. Load inflates it.
  - **best pass:** the fastest single pass of any run (`ns_min`). It is still an upper bound.
  - **est. quiet:** an estimate, not a measurement. It is the median counted cycles per sample at the 3.16 GHz the
    quiet record held, `cycles × 48 000 / 3.16 GHz`. Cycles count only while the thread runs, so preemption does
    not inflate them.
    - On the quiet record it reproduces the measured median to −1.5 / +0.3 % (`test_m1_spread.py`).
    - Under today's load it errs high. The 12AX7 rows' cycles read 1.00–1.13× the quiet record's for the same
      configurations: median 1.04, 1.03–1.11 for HQ stereo. Their instructions read 0.995–1.007×, so the core did
      the same work, and the excess is the E-core share and the loaded machine.
    - That bias is bounded on the 12AX7 only. Nothing here bounds it for the 6AS6, whose loaded cycles have no
      quiet record to compare with.
    - Read it as the closest figure to a quiet M1 that these runs give, likely a few % high. It is not a
      substitute for a clean timing.

## Results (block 64; block 512 is within −2 / +2.4 % on est. quiet)
| stage | quality | channels (plugin path) | peak | measured % core, median of 3 run medians (range) | best pass % | est. quiet % |
|---|---|---|---|---|---|---|
| 12AX7 | eco 4× | scalar reference (1 `Stage`) | 0.5 V | 10 (10–11) | 8 | 7 |
| 12AX7 | eco 4× | scalar reference (1 `Stage`) | 2 V | 11 (11–11) | 9 | 8 |
| 12AX7 | eco 4× | scalar reference (1 `Stage`) | 8 V | 12 (11–14) | 9 | 8 |
| 12AX7 | eco 4× | `StereoStage` (the plugin's stereo) | 0.5 V | 16 (15–18) | 14 | 11 |
| 12AX7 | eco 4× | `StereoStage` (the plugin's stereo) | 2 V | 18 (17–21) | 16 | 13 |
| 12AX7 | eco 4× | `StereoStage` (the plugin's stereo) | 8 V | 20 (18–21) | 14 | 13 |
| 12AX7 | standard 8× | scalar reference (1 `Stage`) | 0.5 V | 19 (18–22) | 13 | 13 |
| 12AX7 | standard 8× | scalar reference (1 `Stage`) | 2 V | 20 (18–24) | 13 | 13 |
| 12AX7 | standard 8× | scalar reference (1 `Stage`) | 8 V | 20 (20–26) | 15 | 15 |
| 12AX7 | standard 8× | `StereoStage` (the plugin's stereo) | 0.5 V | 30 (30–34) | 23 | 20 |
| 12AX7 | standard 8× | `StereoStage` (the plugin's stereo) | 2 V | 32 (29–41) | 24 | 22 |
| 12AX7 | standard 8× | `StereoStage` (the plugin's stereo) | 8 V | 37 (34–41) | 28 | 25 |
| 12AX7 | hq 16× | scalar reference (1 `Stage`) | 0.5 V | 36 (34–41) | 26 | 25 |
| 12AX7 | hq 16× | scalar reference (1 `Stage`) | 2 V | 35 (35–45) | 28 | 25 |
| 12AX7 | hq 16× | scalar reference (1 `Stage`) | 8 V | 38 (37–44) | 28 | 27 |
| 12AX7 | hq 16× | `StereoStage` (the plugin's stereo) | 0.5 V | 56 (56–69) | 41 | 39 |
| 12AX7 | hq 16× | `StereoStage` (the plugin's stereo) | 2 V | 59 (55–69) | 42 | 40 |
| 12AX7 | hq 16× | `StereoStage` (the plugin's stereo) | 8 V | 66 (60–74) | 55 | 44 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage, the plugin's mono) | 0.5 V | 46 (39–47) | 37 | 29 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage, the plugin's mono) | 2 V | 53 (43–53) | 39 | 32 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage, the plugin's mono) | 8 V | 57 (46–57) | 38 | 35 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's stereo) | 0.5 V | 94 (76–94) | 75 | 58 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's stereo) | 2 V | 100 (82–108) | 80 | 62 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's stereo) | 8 V | 111 (91–116) | 88 | 70 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage, the plugin's mono) | 0.5 V | 88 (76–93) | 71 | 56 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage, the plugin's mono) | 2 V | 90 (75–92) | 59 | 58 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage, the plugin's mono) | 8 V | 100 (83–101) | 63 | 62 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's stereo) | 0.5 V | 185 (149–189) | 131 | 113 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's stereo) | 2 V | 182 (150–186) | 115 | 116 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's stereo) | 8 V | 192 (162–209) | 144 | 123 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage, the plugin's mono) | 0.5 V | 44 (37–45) | 30 | 28 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage, the plugin's mono) | 2 V | 50 (40–52) | 30 | 30 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage, the plugin's mono) | 8 V | 56 (44–62) | 37 | 35 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage, the plugin's mono) | 0.5 V | 85 (70–96) | 62 | 53 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage, the plugin's mono) | 2 V | 91 (72–95) | 59 | 56 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage, the plugin's mono) | 8 V | 95 (80–100) | 65 | 60 |

What these loaded runs support, and what they do not:
- **12AX7:** every quality's `StereoStage` stays under one core even as measured on this loaded Mac. HQ is 56–66 %
  measured and 39–44 % est. quiet. The quiet 13 Nov record read 38–42 % (`bench_apple_m1_simd_math.json`).
- **6AS6 Standard costs about half as much as HQ.** In cycles it is 0.50–0.58× HQ, in every run and configuration.
  - Stereo, est. quiet: 58–70 %. That suggests it fits one core on a quiet M1 with headroom, but no quiet run
    confirms it.
  - Stereo, as measured on this loaded Mac: 94–111 % (run medians 76–116 %). On a Mac this busy it did not keep up
    at 2 and 8 V.
- **6AS6 HQ stereo is likely over one core at 48 kHz.** It reads 113–123 % est. quiet, 182–192 % measured and
  115–144 % on the best pass of any run.
  - If the estimate's largest 12AX7 bias (13 %) applied to the 6AS6 too, the lowest point would be 100.2 %. That
    is at the limit, not clearly over it, and that bias is not bounded for the 6AS6.
  - The other records of this stage are also loaded or virtual and point the same way: the 21 Nov M1 record
    (best pass 107–119 %, `cpp/bench_6as6_apple_m1_loaded.json`) and the CI EPYC 7763 VM (122–137 %,
    `cpp/README-6as6.md`).
  - A clean M1 run is what would settle it.
- **6AS6 HQ mono** reads 56–62 % est. quiet and 88–100 % measured.
- **Not timed:** Live itself. No dropout was observed or tested. Whether HQ stereo drops out in Live on a quiet M1
  is inferred from the figures above, not measured.
- **Not measured: 44.1 kHz.** The per-second cost scales with the host rate at a fixed factor, so it would be about
  0.92× these figures (an inference).
- T 0.5 mA reads 1–6 % below P1 0.45 mA (mono, est. quiet).
- Every figure is one instance with nothing else on its thread. A Live set adds its own tracks and the host's
  overhead, so "under one core" here is a necessary condition for real time, not a sufficient one.

## Default Quality: 6AS6 at Standard (Michael's decision, 27 Nov 2026)

**Decision.** The v0.1 shipping default is the **6AS6 path at Standard (8×)**: P1, 0.45 mA, filter off. Michael
chose it knowing that it trades the #113 inaudibility criterion for CPU. In `plugin/src/map_params.h` this is
`kValveDefault = k6AS6` and `kQualityDefault = kStandard`, and `plugin/tests/engine_test.cpp` ("defaults (#143)")
checks it. The rest of this section records what the decision trades. Quality is shared by both paths, so the 12AX7
path also opens at its own Standard 8× (#45).

**What it gives up: the #113 criterion at 8 V.** The criterion (`dk6as6/inaudible.py`) is that every alias product
sits below −100 dBFS and ≥ 10 dB under the threshold of hearing, at 2 and 8 V, 44.1 and 48 kHz. The margins at the
default setting come from PR #146's C++ sweep on the shipping core (k140, grid current, filter off). They cover
#146's in-scope grid: 29 input tones below 20 kHz at each rate. They were not re-run here; negative fails:

| default setting, P1 0.45 mA | 48 kHz, 8 V | 44.1 kHz, 8 V | 48 kHz, 2 V | 44.1 kHz, 2 V |
|---|---|---|---|---|
| **Standard 8× (the default)** | **−18.7 dB** | **−12.7 dB** | +59.1 dB | +52.0 dB |
| HQ 16× | +41.7 dB | +40.8 dB | +59.1 dB | +52.0 dB |

- At 8 V drive the default's worst alias products sit 18.7 dB (48 kHz) and 12.7 dB (44.1 kHz) over the limit.
- At 2 V it passes with ≥ 52 dB to spare.
- On main, #136's record (`dk6as6/RESULTS.md`, "State-space antiderivative antialiasing") reads 8× the same way at
  P1 0.6 mA: −21.26 / −21.10 dB.
- HQ meets the criterion at the default setting **for inputs below 20 kHz**, and is still offered.
- **Neither quality meets it for inputs between 20 kHz and Nyquist.** In the same record, HQ at the default setting
  fails there by −72.4 / −76.7 dB at 48 kHz (2 / 8 V, 23,299 Hz input) and −70.7 / −73.8 dB at 44.1 kHz
  (21,799 Hz). #146 did not run Standard 8× above 20 kHz. That mechanism is #147, out of #143's scope.

**What it buys: about half the CPU of HQ.** These figures are % of one M1 core at 48 kHz, one instance, from the
tables above. They come from the same loaded runs: load average 5–19, another agent job (#115) running concurrently,
no run clean. The est. quiet figure is an estimate from counted cycles, likely a few % high, with its bias bounded
on the 12AX7 only. The measured figure is the median of the three run medians, with their range, on the loaded Mac:

| 6AS6, P1 0.45 mA | stereo, est. quiet | stereo, measured (range of 3 runs) | mono, est. quiet |
|---|---|---|---|
| **Standard 8× (the default)** | 58–70 % | 94–111 % (76–116 %) | 29–35 % |
| HQ 16× | 113–123 % | 182–192 % (149–209 %) | 56–62 % |

- **Standard costs 0.50–0.58× HQ** in cycles. That ratio is the most robust figure here: it holds in every run.
- **HQ stereo is likely over one core at 48 kHz** on this evidence. A clean M1 run and a test in Live would
  confirm it; neither has been done, so a dropout in Live is an inference.
- **Standard stereo is estimated under one core on a quiet M1 (58–70 %),** but no quiet run confirms it. On this
  loaded Mac its median reached 94–111 %, so on a machine this busy it did not keep up either, at 2 and 8 V.
- Ranges are over 0.5 / 2 / 8 V peak, block 64.

**Revisit when** a clean M1 timing exists (still open), or when #135 brings 6AS6 HQ stereo clearly under one core.
On the estimate that needs a cut of about 20 % for zero headroom, and about 40 % for ~70 % of a core. Or revisit when an eco or ADAA setting meets the criterion at
Standard's cost (#113 / #136). Changing the default is one line, `kQualityDefault`, plus the engine test's
expectation.

## Not covered
- **A clean timing, which #143 requires.** This Mac was never quiet during the work. A 2-configuration probe at
  11:50 UTC still ran at 2.39 GHz, and an interactive app was using about 1.5 cores. It is not committed. The
  est. quiet figures stand in, with their bias stated above; they are not a clean timing.
- The plugin's 12AX7 mono workload (`StereoStage`, one signal on both lanes): the s2 row is its expected upper bound.
- The plugin inside Live, 44.1 kHz, and other block sizes (Live's are 128–2048). Blocks 64 and 512 agree to within −2 / +2.4 %.
- Machines other than this M1.
