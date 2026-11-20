# cutdown: 3-state, 2-D LUT, 2× IIR 12AX7 stage prototype (#23)

Float Python/NumPy prototype of the cut-down 12AX7 common-cathode stage for the Octatrack target (#11, #23):

- **3 states (`cutdown/circuit.py`):** reuses `prototypes/fullstage/dk/circuit.VALUES`, dropping the three pF inter-electrode valve capacitances (`CCG`, `CGP`, `CCP`) and keeping `Cin` (22 nF), `Ck` (22 µF), and `Cout` (22 nF).
- **2-D lookup table (`cutdown/table.py`):** replaces per-sample Newton-Raphson with a precomputed `(128, 32, 2)` table of `(Ig, Ia)` over `(p0, p1)` (8,192 words total, same memory budget as `64 × 64 × 2`), using Catmull-Rom cubic interpolation along `p0` and linear interpolation along `p1` (`cubic0_lin1`), with exact DC-rest offset correction at `p_dc`.
- **2× IIR resampling (`cutdown/stage.py`):** reuses `prototypes/fullstage/dk/resample.Resampler(fs, 2, "iir")` (6 allpasses/way = 12 MACs/base sample at 48 kHz; 8 allpasses/way = 16 MACs/base sample at 44.1 kHz).
- **Named presets (`CutdownStage(fs, preset=...)`, default `preset="method_ab1c"`):**
  - `"method_ab1c"` (**default**, `A + B1 + comp`): enables Method A + ADAA1 + minimax base-rate treble droop comp (`-62.0 dBc` @ 2 V, `-33.2 dBc` @ 8 V, `0.061 dB` AC max dev at 48 kHz; `-60.5 dBc` @ 2 V, `-31.2 dBc` @ 8 V, `0.069 dB` AC max dev at 44.1 kHz).
  - `"baseline"`: 3-state `128×32` LUT only (`miller_lpf=False, adaa=0, treble_comp=False`).
  - `"method_a"` (**A only**, low-cost DSP56300 fallback): one-pole Miller IIR LPF (`14,171.2 Hz`, `0` divs, `8,192` words).
  - `"method_b1"` (**B1 only**): 1st-order ADAA on the 2-D LUT (deviation-form + same-cell divided differences).
  - `"method_b2"` (**B2**, over budget): 2nd-order ADAA ablation (`adaa=2`, ruled out for shipping).
  - `"method_ab1"` (**A + B1**, no comp): `miller_lpf=True, adaa=1, treble_comp=False` (`treble_comp=True` requires `adaa=1`).
  - Individual keyword overrides (`miller_lpf`, `adaa`, `treble_comp`) are also accepted and validated against `PRESETS`.

## Run

From `prototypes/cutdown`:

```bash
python -m pytest -q
python -m cutdown.validate
```

- `python -m pytest -q` runs the unit, end-to-end multitone, and representative aliasing tests under `tests/`.
- `python -m cutdown.validate` runs the full validation against `prototypes/fullstage/spice/reference_{dz,koren}.json`, the ablations (`64×64 bilinear`, `3-state 2× IIR Newton`), and the 38-point offset-tone aliasing sweep (`prototypes/fullstage/dk/aliasing.py`), writing `results.json`.

Validation metrics, aliasing tables, and the paper op-count estimate are documented in [`RESULTS.md`](RESULTS.md).
