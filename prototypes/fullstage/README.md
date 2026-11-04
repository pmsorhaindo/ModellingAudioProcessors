# fullstage: one valve gain stage with its reactive parts

Goal: model a full common-cathode stage (coupling caps, bypassed cathode, plate load, grid current) so
bias shift, blocking and recovery come out of the circuit — first with the nodal DK method, then with
wave digital filters for comparison, before the 6AS6 stage and a C++ core.

| folder | what | status |
|---|---|---|
| `RESEARCH-numerics.md` | pitfalls and published fixes (NR robustness, DC init, aliasing, WDF multiport) | done |
| `spice/` | ngspice references: Koren 12AX7 (i) and Dempwolf–Zölzer (ii) on the same netlist | done; settled 500 ms sine data |
| `analysis.py` | shared measurements (DFT harmonics, THD, bias shift, recovery τ) | done |
| `dk/` | nodal DK prototype in Python/NumPy, 8× oversampled (parameter) with streaming half-band resamplers, Drive/Bias/Output | all tests pass at 8× (small-signal 0.052 dB worst, at 20 kHz; failed at 4× with 0.21 dB from trapezoidal warping) — see `dk/RESULTS.md` |
| WDF version | — | next |

**Circuit values are example values, not Culture Vulture values** (see `spice/stage_12ax7.cir`).

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

## Run
```bash
cd prototypes/fullstage
spice/models/fetch_models.sh                       # Koren model (not redistributed)
ln -s /path/to/ngspice-env .ngenv                  # or set NGSPICE=/path/to/ngspice
python spice/run_refs.py                           # regenerates spice/reference_{koren,dz}.json (~2 min)
python -m dk.validate                              # -> dk/results.json (~45 s; also times 4× for comparison)
python -m pytest -q dk/tests
```
Python needs numpy and scipy (see `../valvelab/requirements.txt`). Plots are not committed (the
connector used for commits only accepts text); `results.json` carries the numbers.

## Parameters (our choices)
- **Drive**: linear gain on the input voltage. **Output**: linear gain on the output.
- **Bias**: scales the cathode resistor Rk (1.0 = 1.5 kΩ). It is a set-before-processing control here
  (rebuilds matrices and re-solves the operating point); smoothing comes later.
