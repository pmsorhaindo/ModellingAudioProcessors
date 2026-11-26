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
| `plugin/` | the AU v2 + VST 3 wrapper of that core (code name Gyre), its build, tests and checks (`plugin/README.md`) |
| `prototypes/wdf/` | **reference only**, not the shipping core: the same 12AX7 stage as a wave digital filter in Python (R-type root, triode solved by Newton), compared with the DK stage. The plugin does not depend on it |

## Status (18 Nov 2026)
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
  stage with the circuit values inferred in #102 (HT 200 V, Ra 270k, unbypassed cathode set by a target current over
  0.1–0.6 mA) and the T / P1 / P2 Distortion-switch wirings of #111 (inferred; P2 is our hypothesis). Its supported
  envelope, P1 at 0.1–0.6 mA and T at 0.1–0.3 mA, passes the 12AX7 stage's criteria against same-model ngspice
  references and the aliasing quality gate (P1 ≤ −98 dBc at 8 V, standard 8× / 48 kHz); P2 and T above 0.3 mA are
  experimental only (`prototypes/fullstage/dk6as6/RESULTS.md`). It is not ported to C++.
- **Plugin, first loadable build** (`plugin/README.md`, #22). AU v2 and VST 3 wrappers under the code name Gyre (#116),
  with a Valve parameter that picks the 6AS6 stage (the default) or the stereo 12AX7 stage (#114). Parameters: Drive,
  Output, Quality, Bypass and Valve; 12AX7 Bias; 6AS6 Bias, Mode (P2 experimental only, T capped at 0.5 mA) and
  Filter. On the M1 it passes the Steinberg validator and `auval`, and offline renders through both plugins null
  exactly against each path's C++ core.
- **WDF reference** (#12, `prototypes/wdf/README.md`, `prototypes/wdf/RESULTS.md`). A wave digital filter version of
  the 12AX7 stage, kept as a reference implementation for comparison, not as the shipping core. It passes the DK
  stage's criteria in every setting, with the same output as the DK stage to ≤ 7.4e-8 V. In Python it is 0.92–0.94×
  the DK stage's time per sample, and a bias change costs it 0.12× (no DC solve). The plugin and the C++ core do not
  depend on it.
- Research notes: [grid current](prototypes/research/grid-current.md),
  [panel controls](prototypes/research/culture-vulture-controls.md),
  [6AS6 datasheets](prototypes/research/6as6-datasheets.md).

**Not done** (see the issues)
- **The plugin is a first build only.** Still open: a working name, after which the IDs change (#75); host
  sample rates other than 48 kHz, which are untested against the reference (#74); and a
  calibrated mapping from host full scale to the stage's volts (the current one is provisional).
- Stereo vector maths (#67, draft PR #68) and the x86-64 stereo benchmark (#76).
- Octatrack effect-slot port: a cut-down table model (#23) and a DSP56300 cycle count (#24). Nothing has run on
  that hardware; by the estimate in `prototypes/fullstage/research/octatrack-budget.md` the full model does not fit
  a slot at any oversampling factor.
**Known problems**
- CI runs the Linux jobs on pull requests (including drafts) and on pushes to main. The two blocking macOS
  jobs — a fullstage test on arm64 (#72, #85) and a plugin build-and-check (#22) — run only on pull requests
  that are ready for review, and on manual dispatch. They are skipped on draft PRs and on pushes, including
  to main, because macOS minutes bill at 10× on this private repository. Open PRs as drafts while iterating;
  mark a PR ready for review to run the macOS jobs.

Nothing is signed, notarised, published or released.

## Rules
- Circuit values in the prototypes are example values, not values from any commercial unit.
- No code, impulse responses or presets from commercial plugins.
- Third-party valve models and datasheets are fetched by scripts, not redistributed.

## Licence
The project's own code is MIT ([LICENSE](LICENSE)). Third-party SDKs keep their own licences; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and [docs/licensing-and-wrapper-plan.md](docs/licensing-and-wrapper-plan.md) (#22).

## Lint
CI runs `ruff check prototypes` (ruff 0.17.3; pyflakes and pycodestyle errors, 120 columns) as a blocking job. The
settings are in `ruff.toml`. To get the same check on every local commit, install the pre-commit hook once per
checkout: `pre-commit install` (pre-commit from Homebrew or pipx; `.pre-commit-config.yaml` pins the same ruff).
`pre-commit run --all-files` checks the whole tree.
