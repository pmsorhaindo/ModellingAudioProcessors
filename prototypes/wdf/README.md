# WDF 12AX7 stage (reference only)

This tree is a **reference implementation** of the 12AX7 common-cathode stage as a wave digital filter (WDF).
It exists so the nodal Discrete K-method (DK) stage in `../fullstage/` can be compared against a second
discretisation of the same circuit.

It is **not the shipping core**. The realtime C++ path and the AU / VST 3 plugin use the DK stage
(`../fullstage/cpp/`, `plugin/`). Nothing in the plugin depends on this tree.

The WDF uses an R-type adaptor root (Werner, Smith, Abel, DAFx-15) and solves the triode at that root with the
DK stage's damped Newton (Olsen, Werner, Smith, DAFx-16). It reuses the DK stage's example component values,
Dempwolf–Zölzer 12AX7, quality settings, resamplers, and Drive / Bias / Output controls.

## Results

See [RESULTS.md](RESULTS.md) and `wdf/results.json`. In every quality setting the WDF passes the DK stage's
ngspice criteria, and its output matches the DK stage to ≤ 7.4e-8 V. A bias change costs it 0.12× what the DK
stage pays (no DC operating-point solve). Those figures come from `python -m wdf.validate`, not from a live
C++ port.

## How to run

From this directory, with the same Python dependencies as `../fullstage/`:

```
python -m pip install -r requirements.txt
python -m pytest -q
python -m wdf.validate --workers 4
```

The tests compare against the committed ngspice references in `../fullstage/spice/`; they do not need a live
ngspice install. CI runs this suite on Linux as the `test (wdf)` job.
