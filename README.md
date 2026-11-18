# ModellingAudioProcessors
Research and C/C++ implementations of analog audio processor models. The first target is a fast valve saturator
plugin (AU / VST3) *inspired by* the Thermionic Culture Vulture.

It is not an emulation of that unit, and nothing here is measured from one. No schematic is public, so the circuit
values are example values (`prototypes/fullstage/dk/circuit.py` says where each one comes from). The product name
will not use a brand or device name.

## Structure
| path | what |
|---|---|
| `references/` | manuals, datasheets and modelling papers (links; free author copies where found) |
| `prototypes/valvelab/` | static valve-stage lab: Koren / Dempwolf models, bias vs harmonics, ADAA vs oversampling, C benchmark |
| `prototypes/fit6as6/` | our 6AS6 pentode model, fitted to the Raytheon 1960 datasheet curves (the dual-control g3 law is our own empirical model), verified in ngspice |
| `prototypes/research/` | research notes: grid-current models, the panel controls of the unit that inspires the project, 6AS6 / 5725 datasheet survey |
| `prototypes/fullstage/` | one valve gain stage with its reactive parts: ngspice references, the nodal DK model in Python (`dk/`, `dk6as6/`), demo renders (`demo/`) |
| `prototypes/fullstage/cpp/` | **the realtime core**: header-only C++17 port of the 12AX7 stage (`dk_core.h`), its tests and CPU benchmark |

## Status (14 Nov 2026)
**Done**
- **Model.** One 12AX7 common-cathode stage (nodal DK method, Dempwolf–Zölzer triode with grid current), checked
  against an ngspice reference that runs the same valve equations. Three quality settings: hq 16×, standard 8×
  (default) and eco 4× with pre-warped valve capacitances. All three pass every criterion; the worst small-signal
  error over 20 Hz–20 kHz is 0.013 / 0.052 / 0.054 dB (`prototypes/fullstage/dk/RESULTS.md`).
- **Aliasing.** A pass criterion against a 64× ground truth, enforced by a blocking CI job with a baseline ratchet.
- **C++ core** (`prototypes/fullstage/cpp/README.md`). No allocation or locks in `process()`, lock-free coefficient
  swap, denormal guard, reported latency. It reproduces the Python stage's validation results in every setting.
- **CPU** (`prototypes/fullstage/cpp/BENCHMARK.md`, Apple M1, 48 kHz). One mono instance at standard 8× takes
  11.1–14.3 % of one core, depending on level and math path. A stereo instance (`StereoStage`, two SIMD lanes) costs
  1.25–1.35 × one mono instance with fast math and 1.57–1.67 × with libm, over the three quality settings.
- **6AS6.** A datasheet fit that covers the low-current region (`prototypes/fit6as6/README.md`), and a Python DK
  stage that passes the 12AX7 stage's criteria at standard and hq (`prototypes/fullstage/dk6as6/RESULTS.md`). Its
  aliasing is not assessed, and it is not ported to C++.
- Research notes: [grid current](prototypes/research/grid-current.md),
  [panel controls](prototypes/research/culture-vulture-controls.md),
  [6AS6 datasheets](prototypes/research/6as6-datasheets.md).

**Not done** (see the issues)
- **There is no plugin yet.** The AU / VST3 wrapper and its licensing note are #22. Before it can be called done:
  the v1 scope and a working name (#75), parameter smoothing (#73), host sample rates other than 48 kHz (#74).
- Stereo vector maths (#67, draft PR #68) and the x86-64 stereo benchmark (#76).
- Octatrack effect-slot port: a cut-down table model (#23) and a DSP56300 cycle count (#24). Nothing has run on
  that hardware; by the estimate in `prototypes/fullstage/research/octatrack-budget.md` the full model does not fit
  a slot at any oversampling factor.
- WDF version of the stage, for comparison (#12). An exploration; the plugin does not wait for it.

**Known problems**
- CI runs the Linux jobs on pull requests and pushes to main, and a blocking macOS arm64 fullstage job on pull
  requests and manual runs (#72, #85). The macOS job is skipped on pushes, including to main, to limit billed macOS
  minutes.

Nothing is signed, notarised, published or released.

## Rules
- Circuit values in the prototypes are example values, not values from any commercial unit.
- No code, impulse responses or presets from commercial plugins.
- Third-party valve models and datasheets are fetched by scripts, not redistributed.

## Licence
The project's own code is MIT ([LICENSE](LICENSE)). Third-party SDKs keep their own licences; see
[docs/licensing-and-wrapper-plan.md](docs/licensing-and-wrapper-plan.md) (#22).

## Lint
CI runs `ruff check prototypes` (ruff 0.17.3; pyflakes and pycodestyle errors, 120 columns) as a blocking job. The
settings are in `ruff.toml`. To get the same check on every local commit, install the pre-commit hook once per
checkout: `pre-commit install` (pre-commit from Homebrew or pipx; `.pre-commit-config.yaml` pins the same ruff).
`pre-commit run --all-files` checks the whole tree.
