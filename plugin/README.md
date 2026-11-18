# plugin/ — AU v2 + VST 3 wrapper of the C++ DK core (#22)

The first loadable plugin. It wraps `dkcore::StereoStage` from
[`prototypes/fullstage/cpp/dk_core.h`](../prototypes/fullstage/cpp/dk_core.h): the validated 12AX7
common-cathode stage, #75 option A. The wrapper is a thin one of our own, written directly against the Steinberg
VST® 3 SDK and Apple's AudioUnitSDK ([licensing plan](../docs/licensing-and-wrapper-plan.md), section 6). It does not
use iPlug2, JUCE, VSTGUI or Steinberg's AUWrapper. The host shows its generic parameter UI; there is no custom GUI.
Nothing is notarised or published, and no signing identity is used. The only signatures are ad-hoc, with no
identity: the linker's automatic one on both binaries, and the VST 3 SDK's post-build `codesign --sign -` of the
.vst3 bundle (its "Adhoc codesign workaround for Xcode 15" in `smtg_add_vst3plugin`). The AU is not signed
beyond the linker's signature, and auval did not need more.

**The name is temporary.** "MAP Valve Stage (temporary)" is a placeholder until #75 picks a name. Every identifier
lives in one file, [`src/map_identity.h`](src/map_identity.h): names, bundle identifiers, AU type / subtype /
manufacturer (`aufx` `vls1` `MapT`) and the VST 3 class IDs. CMake reads the same file to write both Info.plist
files. All of these will change once a name is picked. Hosts cache them, so the renamed plugin will look like a new
plugin to them, and old sessions will not find it.

| file | what |
|---|---|
| `CMakeLists.txt` | the build: fetches both SDKs at pinned tags, builds the bundles, the validator and the tests |
| `src/map_identity.h` | the identity, single source of truth (temporary) |
| `src/map_params.h` | the parameters, their ranges, the **provisional volts mapping**, the VST 3 state layout |
| `src/map_engine.{h,cpp}` | the realtime engine both formats share: smoothing, bypass, quality switch, bias worker |
| `src/vst3/map_vst3.cpp` | VST 3 processor, controller and factory |
| `src/au/map_au.cpp` | AU v2 effect (`ausdk::AUEffectBase`) |
| `src/*/Info.plist.in` | bundle plists, filled from `map_identity.h` |
| `tests/smoothing_test.cpp` | #73: full-range Drive / Output / Bias moves during a sine and silence (worst step vs a continuous reference, stated limits), block sizes 16 / 64 / 512, the Drive ramp vs aliasing ([results](../prototypes/fullstage/cpp/RESULTS-smoothing.md)) |
| `tests/engine_test.cpp` | engine tests: exact null vs the core (3 qualities × 44.1 / 48 / 96 kHz, stereo and mono), no allocation in `process()`, bypass, live quality / bias switch, NaN input |
| `tests/null_vst3.cpp`, `tests/null_au.cpp` | offline renders **through the built plugins** (VST 3 hosting classes; AudioComponent API), compared to the direct core |
| `tests/run_checks.sh` | runs all of the above, plus the Steinberg validator and (with `--au`) auval |

## Build and check (macOS)

```bash
cmake -S plugin -B plugin/build -DCMAKE_BUILD_TYPE=Release   # fetches the SDKs (git) on first configure
cmake --build plugin/build -j
plugin/tests/run_checks.sh plugin/build          # engine test, VST 3 validator, VST 3 null
plugin/tests/run_checks.sh plugin/build --au     # + installs the AU to ~/Library/Audio/Plug-Ins/Components, auval, AU null
```

- Output: `plugin/build/VST3/Release/MAPValveStage-temporary.vst3` and
  `plugin/build/AU/MAPValveStage-temporary.component`. The build is universal (`arm64;x86_64`) with a macOS 11.0
  deployment target (AudioUnitSDK's minimum). Use `-DCMAKE_OSX_ARCHITECTURES=arm64` for arm64 only.
- Toolchain: CMake ≥ 3.25 and Apple clang with C++23 (AudioUnitSDK 1.4.0 needs `std::expected`). Built here with
  Apple clang 17 from the Command Line Tools alone, with no Xcode.app. The VST 3 SDK's CMake asks `xcodebuild` for
  a version; with the CLT alone, `CMakeLists.txt` passes the CLT version through the SDK's own `XCODE_VERSION`
  override.
- The VST 3 SDK's `SMTG_CREATE_PLUGIN_LINK` and its post-build validator run are off. The build does not touch
  `~/Library/Audio/Plug-Ins`; only `run_checks.sh --au` copies the AU there.
- `dk_core.h` must not be built with `-ffast-math` (it `#error`s).

## Parameters

v1 scope (#75 option A): these and no more.

| parameter | range (default) | what |
|---|---|---|
| Drive | 0–8 × (1) | linear gain on the input, before the stage (20 ms ramp in the wrapper) |
| Bias | 0.5–2.0 × Rk (1) | cathode resistor scale (1.0 = 1.5 kΩ); 20 ms ramp in the core (`set_bias`, #73) |
| Output | 0–4 × (1) | linear gain after the stage (20 ms ramp in the wrapper) |
| Quality | Eco 4× / Standard 8× / HQ 16× (Standard) | `dkcore::ECO`, `STANDARD`, `HQ`; latency 69 / 72 / 73 samples |
| Bypass | off / on | wrapper-level, 10 ms crossfade to the dry input delayed by the latency (the host's AU bypass property and the VST 3 `kIsBypass` parameter both drive it) |

- **Bias range.** The core's reference vectors cover bias 1.0 and 1.5. The range ends 0.5 and 2.0 build a pack and
  solve the DC point (checked in `engine_test`), but have not been validated further. A bias whose build fails is
  never published: the stage keeps its last pack.
- **Bias smoothing (#73).** The audio thread passes the target to the running stage once per 256-sample chunk.
  The core ramps there over 20 ms, one exact pack per base sample from its bias line. It does no matrix build, takes
  no lock and allocates nothing, and the worker is not involved. A full-range move steps by at most 1.2e-4 FS at Drive
  8, against 0.108 FS for the old single swap. The circuit's own operating-point transient (a thump) remains: it is
  physical and present in the continuous reference. Details:
  [RESULTS-smoothing.md](../prototypes/fullstage/cpp/RESULTS-smoothing.md).
- **Drive and aliasing.** The Drive ramp multiplies the host-rate input before the stage, so it runs before the
  upsampler. Against the same ramp applied per internal sample after the upsampler, the in-band difference is
  −99.5 dB or less (`tests/smoothing_test.cpp`).

## Host full scale ↔ volts (provisional)

The stage works in volts, and the mapping to digital full scale is still undefined (#22). The wrapper sets it in one
place, `src/map_params.h`:

- **Input:** a host sample of 1.0 (0 dBFS) at Drive 1 is 1.0 V at the stage input. This is the convention
  `demo/render.py` uses for a single render. Drive 8 at 0 dBFS gives 8 V, the demo's "driven" level.
- **Output:** 1/64 of full scale per volt. With that, small signals at Drive = Bias = Output = 1 come out at about
  the level they went in. The stage's small-signal gain, measured through the core for this choice (1 kHz, 0.05 V
  peak, Standard, 48 kHz), is 64.5× (36.2 dB).

Both are placeholders, not a calibration. The mapping is folded into the wrapper's drive and output ramps; the
core's own drive and output stay 1.0.

## Threads and realtime rules

| call | thread | notes |
|---|---|---|
| `Engine::prepare()` / `release()` | non-RT, never concurrently with `process()` | VST 3 `setActive`, AU `Initialize` / `Cleanup`; prepares at the host's actual sample rate |
| `Engine::set_*()` / `request_reset()` | any, including the audio thread | relaxed atomics |
| `Engine::process()` | audio thread | no allocation, locks or syscalls (allocation counted in `engine_test`); holds `ScopedFlushDenormals`; works in fixed 256-sample chunks |
| worker thread | the engine's own, started by `prepare()` | polls every 5 ms; runs the stage `prepare()` for a quality change (bias no longer goes through it, #73) |

**Quality change without stopping the host.** `prepare()` of a stage must not run while that stage is processed,
so the engine owns two stages. The worker prepares the idle one at the new quality and publishes it. The audio thread
then fades out for 5 ms, switches at a block boundary, acknowledges and fades back in for 5 ms. The worker reuses a
stage only after that acknowledgement. Latency reporting:
- **AU:** `kAudioUnitProperty_Latency` follows the running stage. The worker sends the property notification once the
  switch is in effect.
- **VST 3:** the controller calls `restartComponent(kLatencyChanged)` when Quality changes, and
  `getLatencySamples()` returns the latency of the requested quality.

## Known limits

- Sample rates other than 48 kHz are untested against the reference (#74). The plugin prepares at whatever rate the
  host gives. The wrapper's nulls were run at 44.1, 48 and 96 kHz, and the validators process at many more rates;
  neither tells whether the model is accurate there.
- Quality changes reach the stage through the worker. They land about 5 ms (wall clock) after the parameter changes,
  also in an offline render that runs faster than real time. Bias changes are sample-aligned (since #73).
- VST 3: the host may re-read the latency right after `restartComponent` and before the Quality change has reached
  the processor through `process()`. It then sees the old value until it asks again (e.g. on the next activation).
- Reported tail: latency + 0.2 s, about 6 × the slowest time constant of the circuit values (Ck · Rk = 33 ms). This is
  a provisional choice, not a measured decay.
- 32-bit float processing only (`kSample32`); mono → mono and stereo → stereo.

VST is a registered trademark of Steinberg Media Technologies GmbH.
