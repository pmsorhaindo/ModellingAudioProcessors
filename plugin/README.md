# plugin/ — AU v2 + VST 3 wrapper of the C++ DK cores (#22, #114)

The first loadable plugin. It wraps two valve stages behind one Valve parameter ([Valve paths](#valve-paths-114)):
the 6AS6 pentode stage `dk6as6::Stage` from
[`prototypes/fullstage/cpp/dk6as6_core.h`](../prototypes/fullstage/cpp/dk6as6_core.h) (Gyre's v1 distortion stage,
#104), and `dkcore::StereoStage` from [`prototypes/fullstage/cpp/dk_core.h`](../prototypes/fullstage/cpp/dk_core.h),
the validated 12AX7 common-cathode stage (#75 option A). The wrapper is a thin one of our own, written directly against the Steinberg
VST® 3 SDK and Apple's AudioUnitSDK ([licensing plan](../docs/licensing-and-wrapper-plan.md), section 6). It does not
use iPlug2, JUCE, VSTGUI or Steinberg's AUWrapper. The host shows its generic parameter UI; there is no custom GUI.
The builds use ad-hoc signatures from the linker on both binaries and the VST 3 SDK's post-build `codesign --sign -` of the
.vst3 bundle (its "Adhoc codesign workaround for Xcode 15" in `smtg_add_vst3plugin`). The AU is not signed
beyond the linker's signature, and auval did not need more.

**Gyre is the working code name** (#75; renamed from the temporary identity in #116), not yet the final shipping
name. Every identifier lives in one file, [`src/map_identity.h`](src/map_identity.h): names, bundle identifiers, AU
type / subtype / manufacturer (`aufx` `Gyr1` `MapT`) and the VST 3 class IDs. CMake reads the same file to write
both Info.plist files, and `tests/run_checks.sh` reads it for the bundle file name and the AU codes. Hosts cache
these, so a renamed plugin looks like a new plugin to them, and old sessions will not find it.

| file | what |
|---|---|
| `CMakeLists.txt` | the build: fetches both SDKs at pinned tags, builds the bundles, the validator and the tests |
| `src/map_identity.h` | the identity, single source of truth |
| `src/map_params.h` | the parameters of both paths, their ranges, the **provisional volts mapping**, the saved-state layout |
| `src/map_engine.{h,cpp}` | the realtime engine both formats share: the two paths, smoothing, bypass, the slot switch and its worker |
| `src/vst3/map_vst3.cpp` | VST 3 processor, controller and factory |
| `src/au/map_au.cpp` | AU v2 effect (`ausdk::AUEffectBase`) |
| `src/*/Info.plist.in` | bundle plists, filled from `map_identity.h` |
| `tests/smoothing_test.cpp` | #73: full-range Drive / Output / Bias moves during a sine and silence (worst step vs a continuous reference, stated limits), block sizes 16 / 64 / 512, the Drive ramp vs aliasing ([results](../prototypes/fullstage/cpp/RESULTS-smoothing.md)) |
| `tests/engine_test.cpp` | engine tests, both paths: exact null vs each path's core (3 qualities × 44.1 / 48 / 96 kHz, stereo and mono; 6AS6 modes, the T cap, the filter), bypass, live quality / bias / valve / mode switches with no allocation, lock or listed syscall in `process()`, null after a switch, NaN / inf input and parameters, saved-state decoding |
| `tests/rt_hooks.cpp` | test-only dylib linked into the engine test: counts locks, waits, allocations and common syscalls on the audio thread (dyld interposing) |
| `tests/null_vst3.cpp`, `tests/null_au.cpp` | offline renders **through the built plugins** (VST 3 hosting classes; AudioComponent API), compared to the direct core of either path; live valve switch; saved state (round trip, pre-#114 states, refused / sanitized values) |
| `tests/run_checks.sh` | runs the tests, the Steinberg validator, auval with `--au`, and strictness-10 pluginval when `PLUGINVAL` points to the external executable |
| `install.sh` | installs both macOS bundles for a local smoke test |

## Build and check (macOS)

```bash
cmake -S plugin -B plugin/build -DCMAKE_BUILD_TYPE=Release   # fetches the SDKs (git) on first configure
cmake --build plugin/build -j
plugin/tests/run_checks.sh plugin/build          # engine test, VST 3 validator, VST 3 null
plugin/tests/run_checks.sh plugin/build --au     # + installs the AU to ~/Library/Audio/Plug-Ins/Components, auval, AU null
```

- The hosted macOS plugin job runs on pushes to `main`, ready PRs and manual dispatches. It validates both
  bundles with pluginval in addition to the Steinberg validator and auval. Only successful manual runs upload
  the `Gyre.vst3` and `Gyre.component` Actions artifacts, retained for seven days. Each artifact contains a
  `.tar.gz` with its bundle and licenses. Extract the `.tar.gz` after downloading to preserve the bundle's
  executable permissions.
- Output: `plugin/build/VST3/Release/Gyre.vst3` and `plugin/build/AU/Gyre.component`. The build is universal
  (`arm64;x86_64`) with a macOS 11.0 deployment target (AudioUnitSDK's minimum). Use
  `-DCMAKE_OSX_ARCHITECTURES=arm64` for arm64 only.
- Toolchain: CMake ≥ 3.25 and Apple clang with C++23 (AudioUnitSDK 1.4.0 needs `std::expected`). Built here with
  Apple clang 17 from the Command Line Tools alone, with no Xcode.app. The VST 3 SDK's CMake asks `xcodebuild` for
  a version; with the CLT alone, `CMakeLists.txt` passes the CLT version through the SDK's own `XCODE_VERSION`
  override.
- The VST 3 SDK's `SMTG_CREATE_PLUGIN_LINK` and its post-build validator run are off. The build leaves
  `~/Library/Audio/Plug-Ins` alone. `run_checks.sh --au` installs the AU for validation.
- `dk_core.h` must not be built with `-ffast-math` (it `#error`s).

## Install for a local Ableton Live smoke test (#142)

Quit Live before installing. From the repo root, install an existing local build with `plugin/install.sh`
(or `plugin/install.sh path/to/build` for a custom build directory).

To reuse the validated #140 CI outputs, download `Gyre.vst3` and `Gyre.component` from the same successful
manual CI run and extract each `.tar.gz`. Pass the extracted bundles to the installer:

```bash
plugin/install.sh /path/to/Gyre.vst3 /path/to/Gyre.component
```

The installer replaces only the two Gyre bundles in `~/Library/Audio/Plug-Ins/VST3` and
`~/Library/Audio/Plug-Ins/Components`. It applies an ad-hoc signature to each staged copy without a developer
identity or `sudo`. To test the copy in isolation, run the same command with `HOME` set to a temporary directory.

### Ableton Live checklist (macOS)

- [ ] Launch Live. In **Settings → Plug-Ins**, enable **Use Audio Units v2** and **Use VST3 Plug-In System
  Folders** (this also scans the user VST3 folder). Click **Rescan** and wait for the scan to finish. If either
  format is missing, hold **Option** and click **Rescan** for a full scan. [Ableton's scan instructions](https://help.ableton.com/hc/en-us/articles/115000349184-VST-AU-plug-in-doesn-t-appear-in-Live-s-Browser)
- [ ] Find Gyre under both **VST3** and **Audio Units** in the Plug-Ins browser. Load each format on an audio
  track with a looping clip. Test one instance at a time.
- [ ] For each format, start playback with the device enabled and confirm audible output and track meter activity.
  Increase Drive and listen for a change, then toggle Live's device activator to compare with the dry clip.
- [ ] Once [#105](https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/105) is present in the tested
  build, open the custom editor in each format. In the dev view, check that the available path, quality, mode
  and filter controls can be changed. Open a fresh instance in the user view, check its defaults and try Drive
  and Output. Record the UI check as pending for builds without the custom editor.

Record the source commit or CI run, Live and macOS versions, and the result for each format.

## Parameters

IDs are what hosts store; 0–4 are the pre-#114 IDs, unchanged, and 5–8 are new (`src/map_params.h`).

| ID | parameter | path | range (default) | what |
|---|---|---|---|---|
| 0 | Drive | both | 0–8 × (1) | linear gain on the input, before the stage (20 ms ramp in the wrapper) |
| 1 | 12AX7 Bias | 12AX7 | 0.5–2.0 × Rk (1) | cathode resistor scale (1.0 = 1.5 kΩ); 20 ms ramp in the core (`set_bias`, #73) |
| 2 | Output | both | 0–4 × (1) | linear gain after the stage (20 ms ramp in the wrapper) |
| 3 | Quality | both | Eco / Standard / HQ (Standard) | 12AX7: 4× / 8× / 16× (`dkcore::ECO`, `STANDARD`, `HQ`). 6AS6: 8× / 8× / 16×: it has no eco (`dk6as6::STANDARD`, `HQ`) |
| 4 | Bypass | both | off / on | wrapper-level, 10 ms crossfade to the dry input delayed by the reported latency (the host's AU bypass property and the VST 3 `kIsBypass` parameter both drive it) |
| 5 | Valve | — | 6AS6 DK / 12AX7 DK (6AS6 DK) | the path ([Valve paths](#valve-paths-114)) |
| 6 | 6AS6 Bias | 6AS6 | 0.1–0.6 mA (0.45) | target DC cathode current (#110); the core solves the Rk that draws it. **T caps it at 0.5 mA** |
| 7 | 6AS6 Mode | 6AS6 | P1 / T / P2 (experimental) (P1) | the Distortion switch (#111) |
| 8 | 6AS6 Filter | 6AS6 | Off / 4k / 7k (Off) | the post-distortion low-pass (#103), 15 ms crossfade in the core |

**Latency: one constant per sample rate**, whatever valve and quality run: **130 samples at 44.1 kHz, 82 elsewhere**
(the 48 kHz-rate geometry), the largest stage latency there (6AS6 HQ). The stages' own latencies are 12AX7 113 / 69
(Eco), 116 / 72 (Standard), 117 / 73 (HQ) (#96) and 6AS6 with its k140 resampler 127 / 79 (Standard, and Eco), 130 /
82 (HQ); the engine delays each stage's output by the difference (an exact integer pad, at most 17 samples, 0.39 ms, for
12AX7 Eco at 44.1 kHz). So no switch ever changes what the host compensates: nothing has to tell the host, nothing can
leave its delay compensation stale, and nothing depends on a host passing a notification on. The pre-#114 12AX7-only
plugin reported the stage's own latency, so its latency grows by that pad.

- **12AX7 Bias range.** The core's reference vectors cover bias 1.0 and 1.5. The range ends 0.5 and 2.0 build a pack and
  solve the DC point (checked in `engine_test`), but have not been validated further. A bias whose build fails is
  never published: the stage keeps its last pack.
- **12AX7 Bias smoothing (#73).** The audio thread passes the target to the running stage once per 256-sample chunk.
  The core ramps there over 20 ms, one exact pack per base sample from its bias line. It does no matrix build, takes
  no lock and allocates nothing, and the worker is not involved. A full-range move steps by at most 1.2e-4 FS at Drive
  8, against 0.108 FS for the old single swap. The circuit's own operating-point transient (a thump) remains: it is
  physical and present in the continuous reference. Details:
  [RESULTS-smoothing.md](../prototypes/fullstage/cpp/RESULTS-smoothing.md).
- **6AS6 Bias and Mode** are fixed for a prepared 6AS6 stage (the core solves Rk and builds the matrices for them).
  A change goes through the worker and the slot switch below: a 5 ms fade out, the new stage from its operating point,
  a 5 ms fade in. Smoothing those, and a measured click-free mode switch, are #115. A bias drag therefore rebuilds
  repeatedly, with a fade each time.
- **T cap (Michael, #114, 22 Nov 2026).** T's supported envelope ends at 0.5 mA with the grid current
  (`dk6as6::envelope`, PR #128), so in T the plugin builds at most 0.5 mA: a 6AS6 Bias above 0.5 mA in T runs at
  0.5 mA. The parameter keeps its value, so switching back to P1 restores it. Whether the T panel range should be
  narrowed further is #146's open question (Michael's call); it stays 0.5 mA here.
- **P2 is experimental only.** P2 is our hypothesis on the fit's weakest region and is outside every supported
  envelope; the core builds it only with `Settings::experimental`. The plugin labels it "P2 (experimental)" and builds
  it that way; the engine reports `active_experimental()`. P1 is supported over the whole 0.1–0.6 mA range.
- **Grid current** stays on (the core's default hardness, the supported envelope's assumption); it is not a
  parameter.
- **Drive and aliasing.** The Drive ramp multiplies the host-rate input before the stage, so it runs before the
  upsampler. Against the same ramp applied per internal sample after the upsampler, the in-band difference is
  −99.5 dB or less (`tests/smoothing_test.cpp`, the 12AX7 path).

## Default Quality and CPU (#143)

The full record is `prototypes/fullstage/cpp/m1_timing/README.md`. On an Apple M1, one instance is estimated from
cycles at the quiet clock, because the Mac was loaded and no run was clean:

| path | Standard, stereo | HQ, stereo | HQ, mono |
|---|---|---|---|
| 12AX7 | 20–25 % | 39–44 % | 25–27 % |
| 6AS6 | 58–70 % | **113–123 %** | 56–62 % |

At the default setting (6AS6, P1 0.45 mA, filter off), only HQ meets the #113 inaudibility criterion. Standard fails
it at 8 V by 18.7 / 12.7 dB (48 / 44.1 kHz). The recommended user-view default is therefore HQ. But 6AS6 HQ stereo
does not fit one M1 core, so that default would drop out on a stereo track. Until #135 lowers the cost, choosing
between HQ, Standard or a 12AX7 user-view default is Michael's decision (the record lists the options).
`kQualityDefault` stays Standard until then.

## Valve paths (#114)

One plugin, one parameter (Valve), as #75's "optional swap-in" and Michael's v0.1 slice ask: not a compile-time or
separate binary. A 12AX7-only build is #145's fallback and is not built here.

**Shared by both paths:** Drive, Output, Quality, Bypass and their ramps; the host full scale ↔ volts map below
(unchanged, the same constants for both); the engine's 256-sample chunking, dry-delay ring and bypass crossfade; the
worker thread and the two-slot switch; latency reporting. **Per path:** the stage objects; 12AX7 Bias (Rk scale) on
the 12AX7 path; 6AS6 Bias (mA), Mode and Filter on the 6AS6 path. A control of the other path is kept but has no
effect: the 12AX7 Rk-scale Bias and the 6AS6 cathode-current Bias are different quantities, so neither is mapped onto
the other.

**The switch.** The engine owns two slots, each holding both kinds of stage, allocated once in the constructor. The
requested configuration is (valve, quality, and on the 6AS6 path mode and bias). When it differs from the running
one, the worker builds the idle slot and publishes it; the audio thread fades out (5 ms), switches at a block
boundary, acknowledges, and fades in (5 ms). That is the existing quality switch, now carrying a valve change and a
6AS6 mode / bias change too. The new stage starts from its operating point. Nothing on the audio thread allocates,
locks, waits or makes a syscall: the build runs on the worker; the audio thread only reads relaxed / acquire atomics
and the published slot (`engine_test` counts operator new, and through `tests/rt_hooks.cpp` the libSystem lock,
wait, allocation and common syscall entry points, during every switch; every atomic is checked lock-free at compile
time). A switch is not click-free beyond those fades, and the output level changes with the path (below): #115.
On the 6AS6 path a change between Eco and Standard builds nothing (both run 8×).

**Saved state and presets.** Every save carries both paths' values, so switching the path and back restores the
same sound, and a session reopens on the path it was saved on.
- VST 3: the pre-#114 layout first (`int32` 1, then the 5 normalized values Drive … Bypass), then the #114 extension
  (`int32` magic `GYR2`, `int32` extension version 1, the 4 values Valve … Filter). A pre-#114 build checks the
  version, reads the 5 values and ignores the rest, so a session saved by this build still opens there with its
  shared and 12AX7 values (rolling back keeps them). A state without the extension is the 12AX7 plugin: it loads on
  the 12AX7 path with the 6AS6 values at their defaults. Another head version, a stream short of its values, or a
  NaN / inf value is refused and nothing changes; a finite value outside 0..1 is clamped.
- VST 3 host values: a NaN / inf normalized value from the host (processor queue or controller) is ignored, so it
  never reaches the saved state or an integer conversion. List parameters (Quality, Valve, Mode, Filter) round a
  normalized value to the nearest entry on both sides: the controller's display uses the processor's
  `index_from_norm` rather than `StringListParameter`'s floor, so the two agree between entries too.
- AU: AUBase's ClassInfo plus a `gyre-state-version` key (a pre-#114 build ignores the key and the IDs it lacks).
  RestoreState first sets every parameter to its default, so a preset defines all of them; a ClassInfo without the key
  is from before #114 and loads on the 12AX7 path (set before AUBase announces the preset); NaN and out-of-range values
  are sanitized (`sanitize_plain`). If AUBase refuses the preset, or throws while decoding it, the previous values come
  back (an RAII scope). Render is the only place that publishes parameters to the engine (and Initialize, before
  prepare), so no older snapshot can overwrite a newer one, and it publishes nothing while a restore runs (a seqlock
  that the same scope always ends).
- AU scheduled parameters (`AudioUnitScheduleParameters`) keep AUBase's slicing: an immediate event takes effect at its
  buffer offset. AUBase's event list is a `std::vector`; Initialize reserves 1024 entries, and an immediate event past
  that in one render cycle is not appended, so the render thread never grows it. It is applied at once and, after the
  render's slices, again unless a queued event of the same parameter has a later offset, so the last value requested
  for each parameter is always the one kept; only its timing is lost (at the latest it takes effect from the next
  render). Ramped events are dropped: the pinned SDK sets nothing for them anyway, but would split slices at them and
  log a warning through `os_log` on the render thread for each slice a ramp covers.
- A new instance starts on the 6AS6 path (Gyre's v1 stage), with each path's defaults. User-view presets are #144.

**Rates the 6AS6 core refuses.** Its post-filter cannot run at an internal rate below about 32 kHz (host rates below
about 4 kHz at Standard; the Steinberg validator activates at 1234.5678 Hz). There the engine runs the 12AX7 path
instead of failing activation (`active_valve()`, `build_status()`); the reported latency is the rate's constant, as
everywhere.

## Host full scale ↔ volts (provisional)

The stage works in volts, and the mapping to digital full scale is still undefined (#22). The wrapper sets it in one
place, `src/map_params.h`:

- **Input:** a host sample of 1.0 (0 dBFS) at Drive 1 is 1.0 V at the stage input. This is the convention
  `demo/render.py` uses for a single render. Drive 8 at 0 dBFS gives 8 V, the demo's "driven" level.
- **Output:** 1/64 of full scale per volt. With that, small signals through the 12AX7 path at Drive = Bias = Output =
  1 come out at about the level they went in. The stage's small-signal gain, measured through the core for this choice
  (1 kHz, 0.05 V peak, Standard, 48 kHz), is 64.5× (36.2 dB).
- **The 6AS6 path uses the same map** (#114 does not invent a new one). Its small-signal gain, measured the same way
  through `dk6as6::Stage` (grid current on, filter off), is 9.8× (19.9 dB) at P1 0.45 mA, 6.9× (16.8 dB) at P1 0.3 mA
  and 12.9× (22.2 dB) at T 0.45 mA. So at the same Output it plays about 14–19 dB quieter than the 12AX7 path at
  small signals. Level matching between the paths belongs with the volts mapping (#22) and the user-mode defaults,
  not here.

Both are placeholders, not a calibration. The mapping is folded into the wrapper's drive and output ramps; the
cores' own drive and output stay 1.0.

## Threads and realtime rules

| call | thread | notes |
|---|---|---|
| `Engine::prepare()` / `release()` | non-RT, never concurrently with `process()` | VST 3 `setActive`, AU `Initialize` / `Cleanup`; prepares at the host's actual sample rate |
| `Engine::set_*()` / `request_reset()` | any, including the audio thread | relaxed atomics; NaN ignored, other values clamped to the range |
| `Engine::process()` | audio thread | no allocation, locks or syscalls (counted in `engine_test`: operator new, and `tests/rt_hooks.cpp`); holds `ScopedFlushDenormals`; works in fixed 256-sample chunks |
| worker thread | the engine's own, started by `prepare()` | polls every 5 ms; builds the idle slot for a quality, valve, or 6AS6 mode / bias change (12AX7 bias and the 6AS6 filter do not go through it) |

**Quality, valve and 6AS6 mode / bias changes without stopping the host.** `prepare()` of a stage must not run while
that stage is processed, so the engine owns two slots ([Valve paths](#valve-paths-114)). The worker builds the idle one
and publishes it. The audio thread then fades out for 5 ms, switches at a block boundary, acknowledges and fades back
in for 5 ms. The worker reuses a slot only after that acknowledgement. Latency reporting: `kAudioUnitProperty_Latency`
and `getLatencySamples()` return the rate's constant (above), before and after activation, so neither format notifies
the host of anything. The AU tail time and VST 3 `getTailSamples()` follow the running valve.

## Known limits

- 6AS6 Bias and Mode changes are a 5 ms fade out / in around a rebuilt stage, not smoothed; a switch of path or mode
  is not click-free beyond those fades, and the paths differ in level. Both are #115 (and #22 for levels).
- The 6AS6 path costs about 2.6× the 12AX7's instructions per sample and has no stereo SIMD kernel (README-6as6.md,
  "CPU"); HQ stereo does not fit one Apple M1 core (113–123 %, estimated; [Default Quality and CPU](#default-quality-and-cpu-143)).
- On the 6AS6 path a mono instance runs the left stage only; a host that changes from mono to stereo without
  re-activating would get a stale right stage until the next reset.
- Sample rates other than 48 kHz are untested against the reference (#74). The plugin prepares at whatever rate the
  host gives. The wrapper's nulls were run at 44.1, 48 and 96 kHz, and the validators process at many more rates;
  neither tells whether the model is accurate there.
- Quality, valve and 6AS6 mode / bias changes reach the stage through the worker. They land about 5 ms (wall clock)
  plus the build time after the parameter changes, also in an offline render that runs faster than real time. 12AX7
  Bias changes are sample-aligned (since #73).
- Reported tail: latency + 0.2 s on the 12AX7 path, about 6 × the slowest time constant of its circuit values
  (Ck · Rk = 33 ms); latency + 0.8 s on the 6AS6 path, about 6 × its slowest coupling RC (Cout · (Ra + Rl) =
  100 nF · 1.27 MΩ = 0.127 s, `dk6as6/circuit.py`, inferred values). Provisional choices, not measured decays.
- 32-bit float processing only (`kSample32`); mono → mono and stereo → stereo.

VST is a registered trademark of Steinberg Media Technologies GmbH.
