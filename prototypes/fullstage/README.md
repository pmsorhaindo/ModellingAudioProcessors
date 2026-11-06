# fullstage: one valve gain stage with its reactive parts

Goal: model a full common-cathode stage (coupling caps, bypassed cathode, plate load, grid current) so
bias shift, blocking and recovery come out of the circuit — first with the nodal DK method, then with
wave digital filters for comparison, before the 6AS6 stage and a C++ core.

| folder | what | status |
|---|---|---|
| `RESEARCH-numerics.md` | pitfalls and published fixes (NR robustness, DC init, aliasing, WDF multiport) | done |
| `spice/` | ngspice references: Koren 12AX7 (i) and Dempwolf–Zölzer (ii) on the same netlist, generated from `dk/circuit.VALUES` (#4) | done; settled 500 ms sine data, tight solver (#5) |
| `analysis.py` | shared measurements (DFT harmonics, THD, bias shift, recovery τ) | done |
| `dk/` | nodal DK prototype in Python/NumPy with streaming half-band resamplers, Drive/Bias/Output, quality settings hq 16× / standard 8× (default) / eco 4× + small-cap pre-warp | all tests pass in every setting (small-signal worst 0.014 / 0.052 / 0.054 dB; plain 4× fails with 0.21 dB from trapezoidal warping) — see `dk/RESULTS.md` |
| `dk6as6/`, `spice6as6/` | the 6AS6 stage (#26): same passives as the 12AX7 stage, screen / suppressor at Ec2 = 120 V / Ec3 = 0 V, the fit6as6 `dual_control` law (#55); nodal DK in `dk6as6/` (reusing `dk/`'s resampler, stage controls and tolerances), its same-model ngspice reference in `spice6as6/` | standard 8× and hq 16× pass the 12AX7 criteria against the same-model reference — see `dk6as6/RESULTS.md` |
| WDF version | — | next |

**Circuit values are example values, not Culture Vulture values** (`dk/circuit.VALUES`, the single source for every
ngspice netlist, which `spice/run_refs.py` generates; see `spice/README.md`).

## Process so far
1. Research the numerics → `RESEARCH-numerics.md`.
2. ngspice reference with Koren's 12AX7; rerun the sines for 500 ms so the caps settle → `spice/RESULTS.md`.
3. DK derivation (`dk/circuit.py`), Dempwolf–Zölzer triode with grid current (`dk/triode.py`),
   damped Newton (`dk/solver.py`), stage + oversampling + DC init (`dk/stage.py`).
4. A second ngspice reference using the DK model's own valve equations, to separate method error
   from valve-model error (`spice/run_refs.py`, `spice/models_dz/`).
5. Validation and tests against both (`dk/validate.py`, `dk/tests/`, `dk/RESULTS.md`).
6. Default oversampling raised from 4× to 8× (Michael's choice) to clear the 20 kHz small-signal target
   without touching tolerances; ~2× CPU.
7. Streaming half-band resamplers with reported latency (#14, `dk/resample.py`): linear-phase FIR by
   default, polyphase IIR allpass as the cheap option; `Stage.process` now works block by block.
8. Quality settings (#17): hq 16×, standard 8×, eco 4× with the valve capacitances pre-warped at the
   frequency that minimises the worst small-signal error (17.3 kHz); the resampler choice is separate.
9. Aliasing pass criterion (#6, `dk/aliasing.py`): offset-tone sweep 1037…19037 Hz at 2 V and 8 V against a
   64× ground truth, with a limit for each setting and resampler (worst case + 3 dB).
10. CI aliasing guard (#35, `dk/aliasing_guard.py`): a blocking `aliasing` CI job runs the lean subset for every
    setting × resampler at 2 V and 8 V. It fails over the absolute limit, and also on any regression of more than
    1.5 dB against the committed baseline (`dk/aliasing_baseline.json`). Proven with deliberately degraded local changes,
    never committed (`dk/RESULTS.md`).
11. Netlists generated from `dk/circuit.VALUES` (#4): `spice/run_refs.py` builds every ngspice netlist from VALUES and
    the shared `SOLVER`; the hand-written `.cir` duplicates are gone, and `spice/tests/test_netlists.py` fails on any
    divergence. The regenerated references are bit-identical to the committed ones.

## Run
```bash
cd prototypes/fullstage
bash spice/models/fetch_models.sh                  # Koren model (not redistributed)
ln -s /path/to/ngspice-env .ngenv                  # or set NGSPICE=/path/to/ngspice
python spice/run_refs.py                           # regenerates spice/reference_{koren,dz}.json (~2 min)
python spice/run_refs.py --write-netlist           # rewrites the GENERATED spice/stage_12ax7.cir (no ngspice)
python -m dk.validate                              # -> dk/results.json (~6 min on 8 cores; all settings + aliasing sweep)
python -m dk.aliasing                              # regenerates the 64x aliasing ground truth (~11 min on 8 cores)
python -m pytest -q                                # dk/tests + spice/tests (netlists vs VALUES, #4)
python -m dk.aliasing_guard                        # CI aliasing guard: limits + baseline ratchet (~8 s on 8 cores)
python spice6as6/run_refs.py                       # 6AS6: regenerates spice6as6/reference_{dual,r51}.json
python -m dk6as6.validate                          # 6AS6: -> dk6as6/results.json (standard, hq, plain 4x)
python -m dk6as6.compare_fits                      # 6AS6: #51 vs #55 harmonics -> dk6as6/fit_comparison.json
```

### Aliasing guard and its baseline (#35)
The `aliasing` CI job runs `python -m dk.aliasing_guard`. For each setting / resampler / drive it fails if the
worst total is over `aliasing.LIMIT_DBC`, or if it is more than **1.5 dB** worse than `dk/aliasing_baseline.json`
(`aliasing_guard.TOLERANCE_DB`), even under the limit. More than 1.5 dB better passes, with a note to update the
baseline. The job writes a per-setting table to the run's step summary and uploads `aliasing_guard_results.json` as
the `aliasing-guard-results` artifact.

To change the baseline on purpose (an intended trade-off, or locking in an improvement):
```bash
python -m dk.aliasing_guard --update-baseline --reason "first FIR half-band 127 -> 159 taps: 2 V floor 4 dB lower"
git commit -m "Aliasing baseline: <the same reason>" dk/aliasing_baseline.json
```
The reason is stored in the baseline JSON. **Put the same reason in the commit message**, so `git log
dk/aliasing_baseline.json` tells you why each change was made. The update is refused while any setting is over its
absolute limit: change `LIMIT_DBC` (and RESULTS.md) for that, deliberately.
Python needs numpy and scipy (see `../valvelab/requirements.txt`). Plots are not committed (the
connector used for commits only accepts text); `results.json` carries the numbers.

## Parameters (our choices)
- **Drive**: linear gain on the input voltage. **Output**: linear gain on the output.
- **Bias**: scales the cathode resistor Rk (1.0 = 1.5 kΩ). It is a set-before-processing control here
  (rebuilds matrices and re-solves the operating point); smoothing comes later.
