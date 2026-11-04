# fit6as6: a 6AS6 model fitted to datasheet curves

The lab uses this in place of the EF86 stand-in. Needs Python with `../valvelab/requirements.txt`
(numpy, scipy, matplotlib, pytest) plus Pillow, and `pdftoppm` (poppler-utils). Run from this folder:
```bash
datasheets/fetch_datasheets.sh      # PDFs are not redistributed; fetched and SHA256-checked
mkdir -p plots
python digitise.py && python fit.py && python emit_spice.py && python -m pytest -q tests
```
The pipeline is deterministic: rerunning it from the fetched PDFs reproduces `data/` and `results/`
byte for byte (checked 4 Nov 2026). Plots (`plots/`) are regenerated, not committed.

## Sources (`datasheets/`, fetched by `fetch_datasheets.sh`, not committed)
| file | source URL | used for |
|---|---|---|
| `084_6_6AS6.pdf` | https://frank.pocnet.net/sheets/084/6/6AS6.pdf (General Electric 6AS6, ET-T706, 12-49, 4 pp.) | **all digitised data**: p.3 plate and screen characteristics; p.4 plate current vs suppressor voltage |
| `127_6_6AS6.pdf` | https://frank.pocnet.net/sheets/127/6/6AS6.pdf (Tung-Sol 6AS6, 5 pp.) | has curves; not digitised yet, kept for cross-validation |
| `049_5_5725.pdf` | https://frank.pocnet.net/sheets/049/5/5725.pdf (RCA 5725, 6 pp.) | ratings and characteristic limits only, no curves |

(`093_6_6AS6.pdf` was byte-identical to 084 and was deleted.) `ge-N.png` are the pdftoppm renders at 200 dpi.

### GE sheet p.1, as stated
**Maximum ratings (design centre):** plate 180 V, screen 140 V, positive suppressor 27 V, plate dissipation 1.7 W,
screen dissipation 0.75 W, total cathode current 18 mA, heater–cathode 90 V, bulb 120 °C. Heater is 6.3 V at 0.175 A.
**Class A1 typical operation**, given as two columns:

| | Ec3 = −3 V | Ec3 = 0 V |
|---|---|---|
| Eb / Ec2 / Ec1 | 120 / 120 / −2 V | 120 / 120 / −2 V |
| Ib / Ic2 | 3.6 / 4.8 mA | 5.2 / 3.5 mA |
| gm (g1) | 1850 µmho | 3200 µmho |
| gm (g3) | 810 µmho | 470 µmho |
| rp | not stated | 0.11 MΩ |
| Ec1 for Ib = 10 µA | not stated | −7.5 V |
| Ec3 for Ib = 10 µA | −10 V | not stated |

**Capacitances** (with external shield): g1–p 0.02 max, input g1 4.0, input g3 3.4, output 3.0, g1–g3 0.15 max (pF).

## Digitising (`digitise.py`, outputs in `data/`)
- **Calibration** comes from detected grid-line centres. On p.3 these are 29 verticals at 10 V and 21 horizontals at 1 mA, fitted by least squares. On p.4 it uses the heavy axis and major lines at −10, 0 and +10 V and at 0, 5 and 10 mA. The constants are in `data/calibration.json`.
- **p.3** (Ec2 = 120 V, Ec3 = 0) has solid Ib and dashed Ic2 curves for Ec1 = 0, −1, −2, −3 and −4 V.
  - Each curve has a coarse guide polyline whose nodes I read from a printed list of dark-pixel runs per column. The guide only chooses which run belongs to which curve.
  - Every saved value is the centroid of a dark-pixel run, sampled every 2.5 V. Samples on vertical grid columns are skipped.
  - Points are dropped at crossings, where another curve is within 6 px (0.16 mA). They are also dropped if they sit more than 3 px from a degree-4 fit of their own curve, which catches label strokes.
  - `on_gridline=1` marks a point whose run touches a horizontal grid line, which can bias it by up to about 1 px (0.03 mA).
- **p.4, top right** (Eb = Ec2 = 120 V) has Ib against Ec3 for Ec1 = −1, −2 and −3 V. The fine graph paper is removed with a 4×4 morphological opening, then each curve is tracked column by column from its right-hand end. Points are sampled every 0.5 V from −7.5 to +9.5 V. Below about −7.5 V the three curves merge, so that region is not used.
- **Proof overlays:** `plots/overlay_page3_plate.png` (red = Ib, blue = Ic2) and `plots/overlay_page4_suppressor.png`.

Points per curve:

| p.3 | Ec1 0 | −1 | −2 | −3 | −4 |
|---|---|---|---|---|---|
| Ib | 51 | 47 | 59 | 57 | 61 |
| Ic2 | 31 | 45 | 39 | 45 | 41 |

p.4: 35 points for each of Ec1 = −1, −2 and −3.

## Models (`model6as6.py`, `fit.py`, output `results/fit_results.json`, `plots/fit_comparison.png`)
- **The residual** is (model − data) / max(data, 0.3 mA), solved with `scipy.optimize.least_squares` (TRF, bounded, several starts).
- **Plain Koren pentode** (Koren 1996, eq. 5 plus the screen eq. 3) has no g3 input. Fitting it to p.3 Ib gives 3–5 % RMS relative error. Its screen equation cannot follow the fall of Ic2 with Eb, so it misses the screen data by 7–100 %. EX and EX_G2 both end on the lower bound of 1.0. It is kept only as a baseline.
- **Dual-control extension.** This is our own empirical model, not a published one:
  `Ik = 2·E1^EX/KG1`, where E1 is Koren's term in Vg1 and Vg2.
  `f = FMAX·(2/π)atan(Va/KVB)·(1+exp(−(Vg3 + Va/MU3 − V3C − RI·Ik)/S3))^(−P3)`
  `Ip = Ik·f`, `Ig2 = Ik·(1−f)`
  - The idea is current division. The cathode current is set by g1 and g2, and g3 and the plate decide how much of it reaches the plate. That matches the datasheet: across p.3, Ib + Ic2 stays roughly constant at a given Ec1 (about 23 mA at Ec1 = 0).
  - Published background I checked:
    - Koren's equations assume g3 sits at cathode potential.
    - The uTracer modelling note "Spice models for vacuum tubes using the uTracer" (https://www.dos4ever.com/uTracer3/Theory.pdf) models heptode g3 control as a pentode stacked on top of the g1–g2 section.
    - Radiomuseum's "Dual control Pentodes" thread says a 6AS6's g3 moves plate and screen current in opposite directions, with roughly constant cathode current.
  - None of these gives a ready-made 6AS6 model.
- **Fitted parameters**, which also go into the SPICE file: see `results/fit_results.json`.
- **Datasheet cross-check** (not used in the fit), Ec3 −3 / 0 V: Ib 3.48 / 5.22 mA (sheet 3.6 / 5.2), Ic2 5.27 / 3.53 (4.8 / 3.5), gm 1768 / 3068 µmho (1850 / 3200), gm(g3) 762 / 397 (810 / 470), rp 0.14 MΩ at Ec3 = 0 (0.11).

## SPICE
`results/6AS6_fit.inc` defines `.SUBCKT 6AS6_FIT P G2 G3 G1 K` using B-sources (ln, exp, atan, pwr, max) and the datasheet capacitances. A test checks it against the Python model by evaluating the same expression strings. **Checked in ngspice 41 (4 Nov 2026):** `spice_check/check_6as6.cir` DC-sweeps the subcircuit at the
datasheet conditions (p.3: Ec2 120 V, Ec3 0, Eb 0–240 V, Ec1 0…−4 V; p.4: Eb = Ec2 = 120 V, Ec1 −1…−3 V,
Ec3 −10…+10 V) and `spice_check/compare.py` compares Ib and Ic2 with the Python model. Worst deviation
over all 16 curves: 0.094 µA absolute, 1.1×10⁻⁵ relative (from the 6-significant-figure parameters in the
`.inc`). No changes to the `.inc` were needed. Run: `cd spice_check && mkdir -p out &&
ngspice -b check_6as6.cir && python compare.py` → `spice_check.json`.

## Limitations
- **Screen voltage:** all data is at Ec2 = 120 V, so MU and KP are poorly constrained. Any other screen voltage is extrapolation.
- **Valid range:** Ec1 −4 to 0 V, Ec3 −7.5 to +10 V, Eb 20 to 240 V. Below 20 V the knee (KVB) is set by only a few points. There is no data for a positive g1 and no grid-current model.
- **Source data:** these are "average characteristics" from a single manufacturer, not a measured valve. Scan line width is about 4 px, roughly 0.1 mA or 1 V.
- **Toe region:** at Ec3 below −6 V the relative error reaches 35 % on Ec1 = −3, because the currents there are tiny.
- **Not yet used:** the screen-current-vs-Ec3 plot on p.4 and the Tung-Sol curves. They are the next validation step.
- **Tests:** importing `fit.py` in the tests reruns the fit, which is deterministic.
