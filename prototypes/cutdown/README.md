# cutdown: 3-state, 2-D LUT, 2× IIR 12AX7 stage prototype (#23)

Float Python/NumPy prototype of the cut-down 12AX7 common-cathode stage for the Octatrack target (#11, #23):

- **3 states (`cutdown/circuit.py`):** reuses `prototypes/fullstage/dk/circuit.VALUES`, dropping the three pF inter-electrode valve capacitances (`CCG`, `CGP`, `CCP`) and keeping `Cin` (22 nF), `Ck` (22 µF), and `Cout` (22 nF).
- **2-D lookup table (`cutdown/table.py`):** replaces per-sample Newton-Raphson with a precomputed `(128, 32, 2)` table of `(Ig, Ia)` over `(p0, p1)` (8,192 words total, same memory budget as `64 × 64 × 2`), using Catmull-Rom cubic interpolation along `p0` and linear interpolation along `p1` (`cubic0_lin1`), with exact DC-rest offset correction at `p_dc`.
- **2× IIR resampling (`cutdown/stage.py`):** reuses `prototypes/fullstage/dk/resample.Resampler(fs, 2, "iir")` (6 first-order allpass sections per direction = 12 MACs/base sample, >= 96 dB stopband, 3 base samples nominal latency).

## Run

From `prototypes/cutdown`:

```bash
python -m pytest -q
python -m cutdown.validate
```

- `python -m pytest -q` runs the unit, end-to-end multitone, and representative aliasing tests under `tests/`.
- `python -m cutdown.validate` runs the full validation against `prototypes/fullstage/spice/reference_{dz,koren}.json`, the ablations (`64×64 bilinear`, `3-state 2× IIR Newton`), and the 38-point offset-tone aliasing sweep (`prototypes/fullstage/dk/aliasing.py`), writing `results.json`.

Validation metrics, aliasing tables, and the paper op-count estimate are documented in [`RESULTS.md`](RESULTS.md).
