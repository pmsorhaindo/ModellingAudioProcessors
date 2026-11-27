# M1 timing of the shipping stages, and the default Quality for the user view (#143, 27 Nov 2026)

**Not a clean timing.** This Mac was shared with other work for the whole measurement. The load average was 5–19 on
8 cores (4 P + 4 E), and at the start one interactive app alone used about 1.5 cores. The P-cores ran at 2.0–2.4 GHz, against
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
    Rows: x1, one mono `Stage`; s2, the `StereoStage` the plugin runs; x2, two mono stages.
  - 6AS6: the default setting (P1, 0.45 mA, grid current, filter off, k140), x1 and x2. x2 is the plugin's
    stereo, two `dk6as6::Stage` on one thread. Also T 0.5 mA, the top of T's envelope, x1.
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
    - Read it as the closest figure to a quiet M1 that these runs give, **probably a few % high**.

## Results (block 64; block 512 is within −2 / +2.4 % on est. quiet)
| stage | quality | channels (plugin path) | peak | measured % core, median of 3 run medians (range) | best pass % | est. quiet % |
|---|---|---|---|---|---|---|
| 12AX7 | eco 4× | mono (1 Stage) | 0.5 V | 10 (10–11) | 8 | 7 |
| 12AX7 | eco 4× | mono (1 Stage) | 2 V | 11 (11–11) | 9 | 8 |
| 12AX7 | eco 4× | mono (1 Stage) | 8 V | 12 (11–14) | 9 | 8 |
| 12AX7 | eco 4× | stereo (StereoStage, the plugin's) | 0.5 V | 16 (15–18) | 14 | 11 |
| 12AX7 | eco 4× | stereo (StereoStage, the plugin's) | 2 V | 18 (17–21) | 16 | 13 |
| 12AX7 | eco 4× | stereo (StereoStage, the plugin's) | 8 V | 20 (18–21) | 14 | 13 |
| 12AX7 | standard 8× | mono (1 Stage) | 0.5 V | 19 (18–22) | 13 | 13 |
| 12AX7 | standard 8× | mono (1 Stage) | 2 V | 20 (18–24) | 13 | 13 |
| 12AX7 | standard 8× | mono (1 Stage) | 8 V | 20 (20–26) | 15 | 15 |
| 12AX7 | standard 8× | stereo (StereoStage, the plugin's) | 0.5 V | 30 (30–34) | 23 | 20 |
| 12AX7 | standard 8× | stereo (StereoStage, the plugin's) | 2 V | 32 (29–41) | 24 | 22 |
| 12AX7 | standard 8× | stereo (StereoStage, the plugin's) | 8 V | 37 (34–41) | 28 | 25 |
| 12AX7 | hq 16× | mono (1 Stage) | 0.5 V | 36 (34–41) | 26 | 25 |
| 12AX7 | hq 16× | mono (1 Stage) | 2 V | 35 (35–45) | 28 | 25 |
| 12AX7 | hq 16× | mono (1 Stage) | 8 V | 38 (37–44) | 28 | 27 |
| 12AX7 | hq 16× | stereo (StereoStage, the plugin's) | 0.5 V | 56 (56–69) | 41 | 39 |
| 12AX7 | hq 16× | stereo (StereoStage, the plugin's) | 2 V | 59 (55–69) | 42 | 40 |
| 12AX7 | hq 16× | stereo (StereoStage, the plugin's) | 8 V | 66 (60–74) | 55 | 44 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage) | 0.5 V | 46 (39–47) | 37 | 29 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage) | 2 V | 53 (43–53) | 39 | 32 |
| 6AS6 P1 0.45 mA | standard 8× | mono (1 Stage) | 8 V | 57 (46–57) | 38 | 35 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's) | 0.5 V | 94 (76–94) | 75 | 58 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's) | 2 V | 100 (82–108) | 80 | 62 |
| 6AS6 P1 0.45 mA | standard 8× | stereo (2 Stages, the plugin's) | 8 V | 111 (91–116) | 88 | 70 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage) | 0.5 V | 88 (76–93) | 71 | 56 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage) | 2 V | 90 (75–92) | 59 | 58 |
| 6AS6 P1 0.45 mA | hq 16× | mono (1 Stage) | 8 V | 100 (83–101) | 63 | 62 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's) | 0.5 V | 185 (149–189) | 131 | 113 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's) | 2 V | 182 (150–186) | 115 | 116 |
| 6AS6 P1 0.45 mA | hq 16× | stereo (2 Stages, the plugin's) | 8 V | 192 (162–209) | 144 | 123 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage) | 0.5 V | 44 (37–45) | 30 | 28 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage) | 2 V | 50 (40–52) | 30 | 30 |
| 6AS6 T 0.5 mA | standard 8× | mono (1 Stage) | 8 V | 56 (44–62) | 37 | 35 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage) | 0.5 V | 85 (70–96) | 62 | 53 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage) | 2 V | 91 (72–95) | 59 | 56 |
| 6AS6 T 0.5 mA | hq 16× | mono (1 Stage) | 8 V | 95 (80–100) | 65 | 60 |

- **12AX7** fits one core at every quality, in stereo too. HQ stereo is 39–44 % (est. quiet), 56–66 % measured. That
  agrees with the quiet 13 Nov record (38–42 %, `bench_apple_m1_simd_math.json`) to within the bias above.
- **6AS6 Standard** fits one core in stereo with headroom on a quiet M1: 58–70 % (est.). On today's loaded machine
  its stereo median was 94–111 %, so it would not keep up there at 2 and 8 V.
- **6AS6 HQ stereo does not fit one core.** It reads 113–123 % est. quiet, 182–192 % measured and 115–144 % on the
  best pass. Taking off the largest bias seen on the 12AX7 (13 %) still leaves 100–109 %.
  - The same holds in every other record of this stage: the loaded 21 Nov M1 record (best pass 107–119 %,
    `cpp/bench_6as6_apple_m1_loaded.json`) and the CI EPYC 7763 VM (122–137 %, `cpp/README-6as6.md`).
  - Mono HQ fits: 56–62 % est.
  - Not measured: 44.1 kHz. The per-second cost scales with the host rate at a fixed factor, so it would be about
    0.92× these figures (an inference). HQ stereo would then be about 104–113 % est., still over one core.
  - T 0.5 mA reads 1–6 % below P1 0.45 mA (mono, est. quiet).
- Every figure is one instance on an otherwise busy machine, with nothing else on its thread. A Live set adds its
  own tracks and the host's overhead, so "fits one core" here is a necessary condition for real time, not a
  sufficient one.

## Default Quality for the user view

The user view does not exist yet (#105). A new instance starts on the 6AS6 path, P1 0.45 mA, filter off
(`plugin/src/map_params.h`), and Quality is shared by both paths. So the default that matters is the 6AS6 one.

**Sound first.** The #113 criterion (`dk6as6/inaudible.py`): every alias product below −100 dBFS and ≥ 10 dB under
the threshold of hearing, at 2 and 8 V, 44.1 and 48 kHz. At the default setting, PR #146's C++ sweep on the shipping
core (k140, grid current, filter off) gives these margins (negative fails; not re-run here):

| default setting, P1 0.45 mA | 48 kHz, 8 V | 44.1 kHz, 8 V | 48 kHz, 2 V | 44.1 kHz, 2 V |
|---|---|---|---|---|
| Standard 8× | **−18.7 dB** | **−12.7 dB** | +59.1 dB | +52.0 dB |
| HQ 16× | +41.7 dB | +40.8 dB | +59.1 dB | +52.0 dB |

- On main, #136's record (`dk6as6/RESULTS.md`, "State-space antiderivative antialiasing") reads 8× at P1 0.6 mA the
  same way: −21.26 / −21.10 dB.
- So **only HQ meets the criterion at the default setting.** Standard does not, and this is the measured reason the
  issue asks for if HQ stereo does not fit one core.

**CPU second.** HQ stereo does not fit one M1 core (above). On a stereo track in Live, a default of HQ would not run
in real time on this M1: the output would drop out, which is louder than any of the aliasing the criterion is about.

**Recommendation.**
- The user-view default Quality for the 6AS6 path is **HQ**, because it is the only offered quality that meets the
  #113 criterion at the default setting.
- That default cannot ship on an M1 in stereo until the stage's HQ stereo cost is below one core with headroom.
  That needs a cut of about 20 % for zero headroom, and about 40 % to reach ~70 % of a core. Lane maths, a lazier
  Jacobian and a stereo kernel are #135; this PR does no CPU work.
- Until then, no offered quality meets both the criterion and real-time stereo on this M1. Choosing between the
  options below is **Michael's decision**:
  1. **HQ as the default anyway.** It meets #113 and works on mono tracks, but overloads one M1 core on stereo tracks.
  2. **Standard as the default.** It fits (58–70 % est.), but fails #113 at 8 V by up to 18.7 dB; it passes at 2 V
     by ≥ +52 dB. This trades the criterion for CPU, which the brief rules out without Michael's say-so.
  3. **The 12AX7 path as the v0.1 user-view default** (as in the #145 fallback), at its own Standard 8× (#45).
     - HQ also fits there: 39–44 % est. in stereo.
     - The 6AS6 stays in the dev view until #135 lands.
     - The 12AX7 path meets its own dBc aliasing gate (#6 / #98). It has not been assessed against the #113
       criterion, which was set for the 6AS6 path.
- `kQualityDefault` stays Standard in this PR, so nothing changes in the plugin until Michael picks. Changing it is
  one line in `plugin/src/map_params.h`.

## Not covered
- A clean timing: this Mac was never quiet during the work. The est. quiet figures are the substitute, with their bias
  stated above.
- The plugin inside Live, 44.1 kHz, and other block sizes (Live's are 128–2048). Blocks 64 and 512 agree to within −2 / +2.4 %.
- Machines other than this M1.
