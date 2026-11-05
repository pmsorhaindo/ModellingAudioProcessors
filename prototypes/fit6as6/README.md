# fit6as6: a 6AS6 model fitted to datasheet curves

The lab uses this in place of the EF86 stand-in. Needs Python with `../valvelab/requirements.txt`
(numpy, scipy, matplotlib, pytest) plus Pillow, and `pdftoppm` (poppler-utils). Run from this folder:
```bash
datasheets/fetch_datasheets.sh      # PDFs are not redistributed; fetched and SHA256-checked
mkdir -p plots
python digitise.py && python fit.py && python heldout.py && python emit_spice.py && python ik_g3.py && python -m pytest -q tests
```
The pipeline is deterministic: rerunning it from the fetched PDFs reproduces `data/` and `results/`
byte for byte (checked 5 Nov 2026, after the Raytheon refit; after the #51 refit `fit.py`, `heldout.py` and
`emit_spice.py` were rerun from the committed `data/` and reproduced `results/` byte for byte). Plots (`plots/`)
are regenerated, not committed. `fit.py` takes about 10 s; CI runs only the tests, from the committed `data/`.
`heldout.py` (issue #48) compares the committed parameters with the Raytheon curves that no fit uses (the gm graphs,
the p.8 bottom Ic2 curves) and reports the p.8 bottom Ib fit ([Held-out checks](#held-out-checks-issue-48)).

**Current model (issue #51):** the dual-control model below, fitted to the Raytheon 5725/6AS6W 1960
curves (Ec2 60–140 V, Ec1 down to −5 V) with a log-scale error, so the 0.1–0.6 mA region the Culture
Vulture runs in ([research note](../research/culture-vulture-controls.md)) counts. Since #51 the fit also uses
the p.8 bottom plate family (Ib vs Eb at Ec3 = −1…−14 V), which pins the plate-to-g3 μ (MU3: 225 → 43; the
sheet's is 39–46). Summary and BEFORE/AFTER in [Refit with p.8 bottom](#refit-with-p8-bottom-issue-51).
The #25 refit (no p.8 bottom) is kept in `results/fit_results.json` as `refit_25` and the earlier GE fit
(Ec2 = 120 V only) as `before`; [Refit](#refit-to-the-raytheon-curves-issue-25) describes #25.
Issue #53 extended the model with a g3-dependent cathode current and a non-linear plate term
(`model6as6.py`, off by default), but did not change the current fit: on the #51 data split the new cathode-current
parameter is not determined ([g3-dependent cathode current](#g3-dependent-cathode-current-issue-53)). A decision
on the data split is needed there.

## Sources (`datasheets/`, fetched by `fetch_datasheets.sh`, not committed)
| file | source URL | used for |
|---|---|---|
| `138_5_5725.pdf` | https://frank.pocnet.net/sheets/138/5/5725.pdf (Raytheon 5725/6AS6W, 15 Dec 1960, 13 pp.), SHA256 `6f226c6b…49c8` | **the refit**: pp. 8–11 (six graphs, below) and, since #51, the p.8 bottom Ib curves; held-out checks (#48): the p.8 bottom Ic2 curves and the gm graphs on pp. 11–13 |
| `084_6_6AS6.pdf` | https://frank.pocnet.net/sheets/084/6/6AS6.pdf (General Electric 6AS6, ET-T706, 12-49, 4 pp.) | the earlier fit (`before`) and now a cross-check: p.3 plate and screen characteristics; p.4 plate current vs suppressor voltage |
| `127_6_6AS6.pdf` | https://frank.pocnet.net/sheets/127/6/6AS6.pdf (Tung-Sol 6AS6, 5 pp.) | has curves; not digitised yet, kept for cross-validation |
| `049_5_5725.pdf` | https://frank.pocnet.net/sheets/049/5/5725.pdf (RCA 5725, 6 pp.) | ratings and characteristic limits only, no curves |

(`093_6_6AS6.pdf` was byte-identical to 084 and was deleted.) `ge-N.png` are the pdftoppm renders at 200 dpi and
`ray-NN.png` those of the Raytheon pages at 300 dpi. `digitise.py` makes them from the fetched PDFs (and checks the
Raytheon SHA256 first). Full SHA256s are in `datasheets/SHA256SUMS`. The other sheets that exist are reviewed in
[../research/6as6-datasheets.md](../research/6as6-datasheets.md).

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
### GE sheet (`plate_characteristics_p3.csv`, `suppressor_characteristics_p4.csv`)
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

### Raytheon sheet (`raytheon_5725_p8-11.csv`, 3434 points)
The pages are 1-bit 300 ppi scans (`pdfimages -list`), rendered at 300 dpi, so there is no resampling. Six graphs
(page numbers as printed on the sheet, which match the PDF pages):

| graph (`graph` column) | page | conditions | curves | points (0.1–0.6 mA) | calibration: px per unit (x, y); affine RMS / max px |
|---|---|---|---|---|---|
| `p8_plate_vs_Eb_Ec2` | 8 top | Ib, Ic2 vs Eb; Ec1 = Ec3 = 0 | Ec2 = 60, 80, 100, 120, 140 V | 498 (0) | 2.48 /V, 48.9 /mA; 1.2 / 3.8 |
| `p9_plate_vs_Eb_Ec1` | 9 top | Ib, Ic2 vs Eb; Ec2 = 120 V, Ec3 = 0 | Ib: Ec1 = 0…−4 in 0.5 V steps and −5; Ic2: 0, −1, −2, −3, −4 V | 766 (95) | 3.49 /V, 69.2 /mA; 0.6 / 1.6 |
| `p9_Ib_vs_Ec1_Ec2` | 9 bottom | Ib vs Ec1; Eb = 120 V, Ec3 = 0 | Ec2 = 60…140 V | 400 (74) | 279 /V, 69.4 /mA; 0.9 / 2.6 |
| `p10_Ic2_vs_Ec1_Ec2` | 10 top | Ic2 vs Ec1; Eb = 120 V, Ec3 = 0 | Ec2 = 60…140 V | 375 (84) | 278 /V, 69.2 /mA; 0.5 / 1.2 |
| `p10_vs_Ec3_Ec2` | 10 bottom | Ib, Ic2 vs Ec3; Eb = 120 V, Ec1 = −2 V | Ec2 = 60…140 V | 679 (153) | 49.0 /V, 96.5 /mA; 1.3 / 3.6 |
| `p11_vs_Ec3_Ec1` | 11 top | Ib, Ic2 vs Ec3; Eb = Ec2 = 120 V | Ib: Ec1 = 0…−5 V; Ic2: 0…−4 V | 716 (96) | 49.8 /V, 49.0 /mA; 1.6 / 4.2 |

Points per curve are the `n` column of the per-curve table under [Refit](#refit-to-the-raytheon-curves-issue-25).
p.8 bottom and the gm graphs on pp. 11–13 were added in issue #48 (next section); the p.8 bottom Ib curves are in
the fit since #51. Pp. 1–7 are MIL test tables
with no curves.

- **Calibration.** Every major grid line is located in the scan as a straight, slightly tilted line, using the
  darkness profile in 60 px strips. The thin-line residue (scan minus curve strokes) is used, so a curve next to a
  line cannot pull it; heavy lines fall back to the raw scan (listed in `data/calibration.json`). The scan is
  sheared by about 0.3° and the printed grid is not uniform: a single affine map leaves up to 4 px residuals. So
  pixel → data interpolates between the two located lines either side of each point. The x tick labels sit within
  ±15 px of the located lines on every graph (about 0.05 V on the Ec1 graphs), which confirms the lattice values.
- **Grid removal.** The fine-grid graphs (p.9, p.10 top) have 5–9 px strokes on a 1–5 px grid, removed by a
  morphological opening with a 5 px disc. The coarse-grid graphs (p.8 top, p.10 bottom, p.11 top) are drawn with
  a thinner pen (3.5–5 px) on a 1–2 px grid and use a 3×3 opening.
- **Solid vs dashed.** Solid (Ib; on p.9 bottom and p.10 top all curves) and dashed (Ic2) strokes are traced on
  separate masks, so a tracer cannot switch families at a shallow crossing. Solid strokes are the long connected
  components after a 2 px dilation, which rejoins thin strokes the opening broke. Dashes are the short pieces of
  the 5 px-disc opening once the pixels around the traced solid curves are removed.
- **Tracing.** Each curve starts from a seed (a hint where the stroke is isolated, chosen from `ray_probe()`
  listings of the runs in a column) and is followed column by column. The tracer takes the run nearest a
  linear/quadratic prediction from the last accepted columns and rejects runs taller than 22 px. Curves interrupted
  by labels get a second seed. Where a stroke ends near another curve (converging toes, the origin bundle on the
  plate families, the thick zero axis on p.11), its range is cut at the point read from the scan; the cuts are
  commented in `RAY_GRAPHS`. Every saved value is the centre of a dark run in the scan, one per sweep step (5 V,
  0.05 V or 0.25 V).
- **Filters.** A point is dropped if its column deviates more than 2.5 px from a local quadratic of its own trace
  (labels, grid remnants), if another curve's stroke is within 10 px (crossings), or if its run reaches within
  4 px of the zero-current line. A guard fails the run if two curves follow the same stroke for more than
  30 columns; it caught every hijacked trace while the seeds were being set up.
- **Proof overlays:** `plots/overlay_raytheon_<graph>.png` show every saved point (red = Ib, blue = Ic2) and the
  trace (green). All six were inspected, including at full resolution in the 0–1 mA bands.
- **Not recovered:** the lowest parts of some toes (below about 0.1 mA, or merging with the axis or with
  neighbouring curves), e.g. the Ec2 = 100 V Ib toe on p.10 bottom between Ec3 = −7.1 and −6.5 V.
- **The sheet's own consistency.** The same operating point read off different graphs (`fit_results.json`,
  `raytheon_internal_consistency`; values from p.9 top / p.9 or p.10 transfer / p.10 bottom / p.11, in mA):

| point | readings (mA) | spread |
|---|---|---|
| Ib at Eb = Ec2 = 120 V, Ec1 = -2 V, Ec3 = 0 | 5.165 / 5.149 / 5.131 / 5.075 | 2 % |
| Ib at Eb = Ec2 = 120 V, Ec1 = -4 V, Ec3 = 0 | 0.687 / 0.700 / 0.754 | 9 % |
| Ic2 at Eb = Ec2 = 120 V, Ec1 = -2 V, Ec3 = 0 | 3.480 / 3.449 / 3.488 / 3.437 | 1 % |
| Ic2 at Eb = Ec2 = 120 V, Ec1 = -4 V, Ec3 = 0 | 0.403 / 0.388 / 0.487 | 23 % |

  At 3–5 mA the graphs agree to 2 %. At 0.4–0.7 mA they disagree by 9–23 %, which is roughly the floor any fit
  can reach there. The Raytheon readings at 120/120/−2 V (Ib 5.08–5.17, Ic2 3.44–3.49 mA) match the GE typical
  values (5.2 / 3.5 mA).

### Raytheon p.8 bottom and the gm graphs (issue #48; `raytheon_5725_p8bottom.csv`, `raytheon_5725_gm_p11-13.csv`)
Same pipeline, PDF and SHA256 as above (`RAY_GRAPHS` in `digitise.py`), one overlay per graph in `plots/`. These
are in separate files because in #48 no fit used them, and the gm file is in µmho. Since #51 `fit.py` also reads
the p.8 bottom **Ib** curves (not its Ic2 or Ic3 curves); nothing was re-digitised for that. These graphs are drawn on a 2–3 px grid with a 4–7 px pen. A 3×3 opening leaves parts of the grid, so they
use a 4 px disc opening. On the gm graphs the heavy zero-µmho line is blanked before tracing (`clear_axis`) so
toes are not followed along it. The heavy Ec3 = 0 line hides every curve where it crosses, so no gm point
lies exactly at Ec3 = 0.

| graph (`graph` column) | page | conditions | curves digitised | points | calibration: px per unit (x, y); affine RMS / max px |
|---|---|---|---|---|---|
| `p8_plate_vs_Eb_Ec3` | 8 bottom | Ib, Ic2, Ic3 vs Eb; Ec2 = 120 V, Ec1 = −2 V | Ib: Ec3 = −1…−14 V; Ic2: −14, −4 V; Ic3: +2, +1, +0.5 V | 930 | 2.47 /V, 97.5 /mA; 1.7 / 4.2 |
| `p11_gm1_vs_Ec3_Ec2` | 11 bottom | gm(g1) vs Ec3; Eb = 120 V, Ec1 = −2 V | Ec2 = 60…140 V | 363 | 49.8 /V, 0.196 /µmho; 1.2 / 2.9 |
| `p12_gm1_vs_Ec1_Ec2` | 12 top | gm(g1) vs Ec1; Eb = 120 V, Ec3 = 0 | Ec2 = 60…140 V | 504 | 194 /V, 0.244 /µmho; 2.0 / 5.7 |
| `p12_gm1_vs_Ec3_Ec1` | 12 bottom | gm(g1) vs Ec3; Eb = Ec2 = 120 V | Ec1 = 0…−5 V (0.5 V steps below −3) | 313 | 48.3 /V, 0.195 /µmho; 1.5 / 5.0 |
| `p13_gm3_vs_Ec3_Ec2` | 13 top | gm(g3) vs Ec3; Eb = 120 V, Ec1 = −2 V | Ec2 = 60…140 V | 300 | 48.7 /V, 0.485 /µmho; 1.2 / 3.7 |
| `p13_gm3_vs_Ec3_Ec1` | 13 bottom | gm(g3) vs Ec3; Eb = Ec2 = 120 V | Ec1 = 0…−4 V | 287 | 48.5 /V, 0.486 /µmho; 1.2 / 3.6 |

Points per curve:
- p.8 bottom Ib, Ec3 = −1…−14 V: 71, 68, 74, 73, 69, 66, 61, 61, 54, 46, 41, 33, 26, 20.
  Ic2 at −14 / −4 V: 16 / 12. Ic3 at +2 / +1 / +0.5 V: 45 / 50 / 44.
- p.11 bottom (Ec2 = 140…60 V): 77, 76, 75, 70, 65.
- p.12 top (Ec2 = 140…60 V): 136, 119, 101, 83, 65.
- p.12 bottom (Ec1 = 0, −1, −2, −3, −3.5, −4, −4.5, −5 V): 10, 10, 20, 45, 51, 57, 59, 61.
- p.13 top (Ec2 = 140…60 V): 78, 74, 70, 51, 27.
- p.13 bottom (Ec1 = 0…−4 V): 61, 62, 61, 71, 32.

What was **not** digitised, and why (no point was guessed; each reason is also in `RAY_GRAPHS`):
- **p.8 bottom, Ib at Ec3 = +10, +6, +4, +2 and 0 V.**
  - All five leave the knee as one stroke. +10 and +6 stay together up to Eb ≈ 330 V and are 6 px apart after
    that; +6, +4 and +2 are 6–14 px apart, below the 10 px crossing filter.
  - The inline labels "+6.0", "+4.0", "+2.0" and "Ib @ Ec3 = 0" cut the strokes.
  - Tracers seeded on +6/+4 and on +2/0 ended on the same strokes, which the shared-stroke guard caught.
  - Ec3 = 0 at these conditions is p.9 top's Ec1 = −2 V curve, so nothing is lost there.
- **p.8 bottom, Ic2 at Ec3 = −12, −10, −8, −6, −2, 0, +2, +4 and +10 V.**
  - The eleven dashed curves cross 14 solid curves and each other, and the dashes are cut at every crossing.
  - Letting the dashed tracer bridge gaps longer than 30 columns made it hop to other curves (checked against
    the right-hand labels).
  - With 30-column gaps only −14 and −4 V give 10 or more points. −14 V is used only above Eb = 240 V,
    where it leaves −12 V; both run along the 7.5 mA line below that.
- **p.8 bottom, Ic3 at Ec3 = +10 and +4 V:** 6–9 px apart from Eb = 200 to 300 V and one stroke below 120 V.
- **Parts of curves:**
  - Ib toes: −12 / −13 V below Eb = 270 / 300 V, where they run together along the axis. The steep −1 / −2 V
    toes below the knee.
  - p.12 bottom: Ec1 = 0 / −1 V below Ec3 = 6.5 V and −2 V below 4 V. These three cross repeatedly between
    Ec3 = −7 and +5.5 V.
  - p.12 bottom, −3…−5 V: below where each leaves the common toe.
  - p.13 bottom: Ec1 = −4 V above Ec3 = 0.5 V, where it runs into the −3 V tail.
  - The near-vertical rising edges on p.13 (runs taller than 22 px).
- **Check of the identity and calibration:** Ib at Eb = 120 V read off p.8 bottom matches p.10 bottom within 5 %
  and p.11 top within 10 % for Ec3 = −1…−7 V (`heldout_checks.json`, `Ib_at_Eb_120_vs_transfer_graphs_mA`).

## Models (`model6as6.py`, `fit.py`, output `results/fit_results.json`, `plots/fit_comparison.png`)
- **Residuals.** The current fit (#25) uses ln((I_model + I0)/(I_data + I0)), I0 = 0.02 mA (1–2 px of
  digitising resolution), so equal relative errors cost the same at 0.2 mA and at 10 mA. The earlier GE fit used
  (model − data)/max(data, 0.3 mA), which gives 0.1–0.6 mA almost no weight. Both are solved with
  `scipy.optimize.least_squares` (TRF, bounded, several starts). For the #25 refit, 10 of 12 random restarts
  reach the same minimum and none goes lower; for the #51 refit, all 7 seeded starts and 11 of 12 random
  (log-uniform, in bounds) restarts do.
- **Plain Koren pentode** (Koren 1996, eq. 5 plus the screen eq. 3) has no g3 input. Fitting it to p.3 Ib gives 3–5 % RMS relative error. Its screen equation cannot follow the fall of Ic2 with Eb, so it misses the screen data by 7–100 %. EX and EX_G2 both end on the lower bound of 1.0. It is kept only as a baseline.
- **Dual-control extension.** This is our own empirical model, not a published one:
  `Ik = 2·E1^EX/KG1`, where E1 is Koren's term in Vg1 and Vg2.
  `f = FMAX·(2/π)atan(Va/KVB)·(1+exp(−(Vg3 + Va/MU3 − V3C − RI·Ik)/S3))^(−P3)`
  `Ip = Ik·f`, `Ig2 = Ik·(1−f)`
  - The idea is current division. The cathode current is set by g1 and g2, and g3 and the plate decide how much of it reaches the plate. That matches the GE sheet: across p.3, Ib + Ic2 stays roughly constant at a given Ec1 (about 23 mA at Ec1 = 0). The Raytheon p.11 curves show where it stops holding (split check below).
  - **The model form is unchanged by the refit** (same equations and parameter bounds). Only the data and the residual changed.
  - Published background I checked:
    - Koren's equations assume g3 sits at cathode potential.
    - The uTracer modelling note "Spice models for vacuum tubes using the uTracer" (https://www.dos4ever.com/uTracer3/Theory.pdf) models heptode g3 control as a pentode stacked on top of the g1–g2 section.
    - Radiomuseum's "Dual control Pentodes" thread says a 6AS6's g3 moves plate and screen current in opposite directions, with roughly constant cathode current.
  - None of these gives a ready-made 6AS6 model.
- **Fitted parameters** (`dual_control.params` is the current #51 fit, also in the SPICE file; `refit_25.params`
  the #25 fit; `before.params` the GE fit):

| param | MU | EX | KG1 | KP | FMAX | MU3 | V3C | S3 | P3 | RI | KVB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| GE fit (#8, `before`) | 25.33 | 1.832 | 1492 | 132.4 | 0.7754 | 98.81 | -9.969 | 2.699 | 9.545 | 99.76 | 8.564 |
| #25 refit (`refit_25`) | 29.92 | 1.509 | 716 | 151.3 | 0.8215 | 225.4 | -6.523 | 2.475 | 3.198 | 63.73 | 17.92 |
| **#51 refit (`dual_control`, current)** | 30.14 | 1.494 | 697.6 | 149.5 | 0.7107 | 43.22 | -3.031 | 2.075 | 2.302 | 53.93 | 5.231 |

- **GE p.1 typical-operation cross-check** (not used in any fit; Eb = Ec2 = 120 V, Ec1 = −2 V, Ec3 = −3 / 0 V;
  gm and rp by central differences):

| | sheet (GE) | GE fit (#8) | #25 refit | #51 refit |
|---|---|---|---|---|
| Ib (mA) | 3.6 / 5.2 | 3.49 / 5.26 | 3.13 / 5.00 | 3.04 / 4.88 |
| Ic2 (mA) | 4.8 / 3.5 | 5.24 / 3.48 | 5.26 / 3.39 | 5.32 / 3.47 |
| gm(g1) (µmho) | 1850 / 3200 | 1773 / 3084 | 1827 / 3217 | 1798 / 3171 |
| gm(g3) (µmho) | 810 / 470 | 772 / 399 | 785 / 432 | 820 / 381 |
| rp (MΩ) | – / 0.11 | 0.11 / 0.16 | 0.16 / 0.16 | 0.051 / 0.10 |

  Raytheon itself reads Ib 3.32 (p.11) / 3.45 (p.10) mA and Ic2 4.94 mA at Ec3 = −3 V. The #25 Ib there was
  6–9 % below Raytheon and 13 % below GE; the #51 Ib (3.04 mA) is 8–12 % below Raytheon and 16 % below GE. The
  test checks Ib at −3 V to 15 % against both Raytheon readings and to 20 % against GE (15 % for everything else).
  The Raytheon plate curves at Eb = 120 V give rp ≈ 64 kΩ at Ec3 = −3 V (slope of p.8 bottom, local quadratic over
  ±20–30 V) and ≈ 134 kΩ at Ec3 = 0 (p.9 top, Ec1 = −2 V). So the low #51 rp at −3 V follows the sheet; #25
  (0.16 MΩ) did not. (The GE-fit values in an earlier README, e.g. Ib 3.48 / 5.22, did not quite match its own
  parameters; the table recomputes them.)

## Refit to the Raytheon curves (issue #25)
*This section describes the #25 refit (`refit_25` in `fit_results.json`); "AFTER" here means #25. The current
#51 refit is compared with it in [Refit with p.8 bottom](#refit-with-p8-bottom-issue-51).*

**Data split.** The fit uses every Raytheon point except the Ic2-vs-Ec3 curves of p.10 bottom and p.11 top
(2779 points, 391 in 0.1–0.6 mA). Those Ic2 curves (655 points, 111 in 0.1–0.6 mA) are **held out** as the
plate/screen split check, because in this model screen current under g3 control follows only from the share
function. The GE points are a cross-check only.

**Errors** — RMS log error / median |relative error| / mean relative error (bias), from
`fit_results.json`. "0.1–0.6 mA" selects points whose *datasheet* current is in that range:

| | BEFORE (GE fit) | ablation: Raytheon, old residual | AFTER (refit #25) |
|---|---|---|---|
| Raytheon fit set, all (n=2779) | 0.182 / 8 % / 9 % | 0.091 / 3 % / 0 % | 0.090 / 3 % / 0 % |
| Raytheon fit set, 0.1-0.6 mA (n=391) | 0.342 / 32 % / 35 % | 0.177 / 9 % / 0 % | 0.179 / 10 % / 3 % |
| held-out Ic2 vs Ec3, all (n=655) | 0.164 / 11 % / 13 % | 0.138 / 8 % / 7 % | 0.134 / 8 % / 6 % |
| held-out Ic2 vs Ec3, 0.1-0.6 mA (n=111) | 0.125 / 9 % / 2 % | 0.181 / 15 % / -8 % | 0.179 / 14 % / -7 % |
| GE p.3/p.4 (cross-check), all (n=581) | 0.039 / 2 % / 0 % | 0.157 / 5 % / -9 % | 0.149 / 5 % / -9 % |
| GE p.3/p.4 (cross-check), 0.1-0.6 mA (n=39) | 0.088 / 5 % / 4 % | 0.250 / 19 % / -19 % | 0.229 / 18 % / -17 % |

- **Low-current region (0.1–0.6 mA), fitted Raytheon curves:** RMS log error 0.342 → 0.179, median error
  32 % → 10 %, and the bias (+35 % before) is gone. Overall 0.182 → 0.090.
- **What did it:** the ablation column fits the same Raytheon data with the old relative residual and gets
  practically the same result (0.177 in 0.1–0.6 mA). So the improvement comes from the new data (screen
  voltages 60–140 V, Ec1 to −5 V, cut-off tails), not from the log residual. With this model form, the
  low-current curves are already pinned by the larger currents of the same families. The log residual is kept
  because it states the objective honestly, and it is marginally better overall and on the held-out curves.
- **Where the remaining low-current error sits** (per-curve table):
  - The Ec2 = 80 and 100 V transfer tails on p.9 bottom: model 19–21 % low (60 V: 7 % low).
  - The Ec1 = −5 V plate curve on p.9 top: 16 % high.
  - The Ec1 = −4 V curves on p.11: Ib 27 % low, Ic2 21 % low. p.11 reads up to 23 % above p.9/p.10 there.
  - A few g3 cut-off toe points on p.10 bottom. 3 / 2 / 5 points at Ec2 = 120 / 100 / 80 V, Ec3 −8.0…−4.7 V,
    are more than 1.5× high, up to 3.1× at Ec2 = 120 V, Ec3 = −8 V. At Ec2 = 140 V the toe is instead up to
    38 % low (Ec3 −7.5…−5.5 V).

  The sheet's own graph-to-graph spread at these currents is 9–23 %.
- **GE cross-check:** the refit is 9 % low on the GE curves overall and 17 % low at 0.1–0.6 mA (the two makers'
  average curves differ; before, it was fitted to GE and matched to 4 %).

**Split check (held out, never fitted).** Overall the refit predicts Ic2 vs Ec3 to RMS log 0.134 (before 0.164),
median 8 %, bias +6 %. The bias comes from deep g3 cut-off: the model holds cathode current independent of g3,
but the sheet does not. On p.11 (`raytheon_p11_cathode_current_mA`), Ib + Ic2 in mA at Ec3 = −4, 0, +4, +10 V and
Ic2 alone at Ec3 = −10 V (where Ib ≈ 0), and the ratio of the last to Ec3 = 0:

| Ec1 | −4 | 0 | +4 | +10 | −10 (Ic2 only) | ratio |
|---|---|---|---|---|---|---|
| Ec1_0 | 20.08 | 22.00 | 20.55 | 20.79 | 16.51 | 0.75 |
| Ec1_-1 | 13.85 | 14.81 | 14.30 | 14.68 | 11.95 | 0.81 |
| Ec1_-2 | 8.01 | 8.51 | 8.50 | 8.71 | 7.17 | 0.84 |
| Ec1_-3 | 3.31 | 3.69 | 3.69 | 3.71 | 3.19 | 0.87 |

Cathode current stays within 10 % from Ec3 = −4 to +10 V but drops 13–25 % at Ec3 = −10 V, so the model
over-predicts Ic2 there: by 38 / 24 / 16 / 15 % at Ec1 = 0 / −1 / −2 / −3 V (mean +12 % over all held-out points
with Ec3 ≤ −6 V). In the 0.1–0.6 mA part of the held-out set the refit is *worse* than before (0.179 vs 0.125).
That is mostly p.11 Ic2 at Ec1 = −4 V (21 % low), which disagrees with p.9/p.10 by about the same amount (see the
consistency table). **The model form was not changed:** representing this needs a g3-dependent cathode-current
term (space charge from electrons turned back at g3), which is a new model. It only matters if the circuit drives
g3 well below about −4 V.

<details><summary>Per-curve errors (AFTER, with BEFORE RMS log for the fitted curves)</summary>

| graph | curve | n | RMS log | mean rel | max abs rel | n in 0.1-0.6 mA | RMS log there | BEFORE RMS log |
|---|---|---|---|---|---|---|---|---|
| p8_plate_vs_Eb_Ec2 | Ib_Ec2_140 | 60 | 0.046 | +2 % | 18 % | 0 | - | 0.045 |
| p8_plate_vs_Eb_Ec2 | Ib_Ec2_120 | 52 | 0.055 | +3 % | 18 % | 0 | - | 0.057 |
| p8_plate_vs_Eb_Ec2 | Ib_Ec2_100 | 66 | 0.054 | +3 % | 18 % | 0 | - | 0.045 |
| p8_plate_vs_Eb_Ec2 | Ib_Ec2_80 | 71 | 0.061 | +2 % | 19 % | 0 | - | 0.095 |
| p8_plate_vs_Eb_Ec2 | Ib_Ec2_60 | 71 | 0.072 | +1 % | 21 % | 0 | - | 0.185 |
| p8_plate_vs_Eb_Ec2 | Ic2_Ec2_140 | 33 | 0.026 | +2 % | 4 % | 0 | - | 0.158 |
| p8_plate_vs_Eb_Ec2 | Ic2_Ec2_120 | 33 | 0.027 | -2 % | 5 % | 0 | - | 0.039 |
| p8_plate_vs_Eb_Ec2 | Ic2_Ec2_100 | 37 | 0.048 | -5 % | 6 % | 0 | - | 0.111 |
| p8_plate_vs_Eb_Ec2 | Ic2_Ec2_80 | 36 | 0.050 | -5 % | 6 % | 0 | - | 0.204 |
| p8_plate_vs_Eb_Ec2 | Ic2_Ec2_60 | 39 | 0.026 | +2 % | 4 % | 0 | - | 0.231 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_0 | 44 | 0.066 | +5 % | 24 % | 0 | - | 0.068 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-0.5 | 50 | 0.047 | +3 % | 15 % | 0 | - | 0.063 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-1 | 52 | 0.030 | +1 % | 10 % | 0 | - | 0.061 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-1.5 | 59 | 0.031 | -2 % | 5 % | 0 | - | 0.042 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-2 | 62 | 0.058 | -5 % | 10 % | 0 | - | 0.025 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-2.5 | 68 | 0.042 | -4 % | 13 % | 0 | - | 0.065 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-3 | 70 | 0.029 | -2 % | 14 % | 0 | - | 0.129 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-3.5 | 66 | 0.035 | -2 % | 13 % | 0 | - | 0.202 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-4 | 71 | 0.035 | +2 % | 11 % | 4 | 0.074 | 0.320 |
| p9_plate_vs_Eb_Ec1 | Ib_Ec1_-5 | 51 | 0.163 | +16 % | 47 % | 51 | 0.163 | 0.557 |
| p9_plate_vs_Eb_Ec1 | Ic2_Ec1_0 | 31 | 0.028 | -2 % | 4 % | 0 | - | 0.035 |
| p9_plate_vs_Eb_Ec1 | Ic2_Ec1_-1 | 33 | 0.055 | -5 % | 8 % | 0 | - | 0.049 |
| p9_plate_vs_Eb_Ec1 | Ic2_Ec1_-2 | 37 | 0.044 | -3 % | 9 % | 0 | - | 0.030 |
| p9_plate_vs_Eb_Ec1 | Ic2_Ec1_-3 | 32 | 0.069 | -1 % | 18 % | 0 | - | 0.081 |
| p9_plate_vs_Eb_Ec1 | Ic2_Ec1_-4 | 40 | 0.073 | +4 % | 17 % | 40 | 0.073 | 0.266 |
| p9_Ib_vs_Ec1_Ec2 | Ib_Ec2_140 | 98 | 0.043 | +3 % | 11 % | 1 | 0.100 | 0.210 |
| p9_Ib_vs_Ec1_Ec2 | Ib_Ec2_120 | 99 | 0.028 | +0 % | 9 % | 17 | 0.040 | 0.193 |
| p9_Ib_vs_Ec1_Ec2 | Ib_Ec2_100 | 89 | 0.119 | -6 % | 37 % | 23 | 0.229 | 0.082 |
| p9_Ib_vs_Ec1_Ec2 | Ib_Ec2_80 | 68 | 0.150 | -6 % | 53 % | 19 | 0.276 | 0.095 |
| p9_Ib_vs_Ec1_Ec2 | Ib_Ec2_60 | 46 | 0.127 | +1 % | 50 % | 14 | 0.219 | 0.128 |
| p10_Ic2_vs_Ec1_Ec2 | Ic2_Ec2_140 | 99 | 0.044 | +2 % | 20 % | 9 | 0.113 | 0.196 |
| p10_Ic2_vs_Ec1_Ec2 | Ic2_Ec2_120 | 96 | 0.077 | +3 % | 27 % | 23 | 0.140 | 0.217 |
| p10_Ic2_vs_Ec1_Ec2 | Ic2_Ec2_100 | 77 | 0.037 | -1 % | 10 % | 18 | 0.042 | 0.123 |
| p10_Ic2_vs_Ec1_Ec2 | Ic2_Ec2_80 | 61 | 0.050 | -2 % | 18 % | 18 | 0.069 | 0.117 |
| p10_Ic2_vs_Ec1_Ec2 | Ic2_Ec2_60 | 42 | 0.104 | +7 % | 21 % | 16 | 0.099 | 0.135 |
| p10_vs_Ec3_Ec2 | Ib_Ec2_140 | 79 | 0.179 | -8 % | 38 % | 3 | 0.144 | 0.218 |
| p10_vs_Ec3_Ec2 | Ib_Ec2_120 | 74 | 0.178 | +3 % | 207 % | 3 | 0.636 | 0.186 |
| p10_vs_Ec3_Ec2 | Ib_Ec2_100 | 69 | 0.128 | +1 % | 64 % | 5 | 0.344 | 0.196 |
| p10_vs_Ec3_Ec2 | Ib_Ec2_80 | 71 | 0.192 | +10 % | 149 % | 9 | 0.529 | 0.262 |
| p10_vs_Ec3_Ec2 | Ib_Ec2_60 | 59 | 0.120 | +3 % | 39 % | 59 | 0.120 | 0.146 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_0 | 69 | 0.115 | +6 % | 15 % | 0 | - | 0.147 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_-1 | 69 | 0.046 | +1 % | 13 % | 0 | - | 0.037 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_-2 | 69 | 0.061 | -2 % | 34 % | 1 | 0.285 | 0.099 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_-3 | 68 | 0.033 | -1 % | 12 % | 1 | 0.110 | 0.154 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_-4 | 67 | 0.158 | +1 % | 38 % | 11 | 0.312 | 0.314 |
| p11_vs_Ec3_Ec1 | Ib_Ec1_-5 | 46 | 0.080 | -5 % | 18 % | 46 | 0.080 | 0.366 |
| p10_vs_Ec3_Ec2 (held out) | Ic2_Ec2_140 | 61 | 0.108 | +9 % | 24 % | 0 | - | - |
| p10_vs_Ec3_Ec2 (held out) | Ic2_Ec2_120 | 73 | 0.087 | +7 % | 16 % | 0 | - | - |
| p10_vs_Ec3_Ec2 (held out) | Ic2_Ec2_100 | 71 | 0.143 | +13 % | 29 % | 0 | - | - |
| p10_vs_Ec3_Ec2 (held out) | Ic2_Ec2_80 | 62 | 0.088 | +4 % | 17 % | 30 | 0.103 | - |
| p10_vs_Ec3_Ec2 (held out) | Ic2_Ec2_60 | 60 | 0.195 | +3 % | 87 % | 44 | 0.166 | - |
| p11_vs_Ec3_Ec1 (held out) | Ic2_Ec1_0 | 70 | 0.176 | +14 % | 38 % | 0 | - | - |
| p11_vs_Ec3_Ec1 (held out) | Ic2_Ec1_-1 | 77 | 0.113 | +9 % | 25 % | 0 | - | - |
| p11_vs_Ec3_Ec1 (held out) | Ic2_Ec1_-2 | 66 | 0.083 | +5 % | 18 % | 0 | - | - |
| p11_vs_Ec3_Ec1 (held out) | Ic2_Ec1_-3 | 61 | 0.098 | +7 % | 21 % | 0 | - | - |
| p11_vs_Ec3_Ec1 (held out) | Ic2_Ec1_-4 | 54 | 0.198 | -12 % | 29 % | 37 | 0.233 | - |

</details>

## Held-out checks (issue #48)
*The numbers in this section are those of the #25 parameters, as reported in #48. `heldout_checks.json` now holds
the same checks for the #51 parameters at the top level and for the #25 parameters under `before_refit_25`; the
#51 values are in the table in [Refit with p.8 bottom](#refit-with-p8-bottom-issue-51). The sheet
self-consistency numbers do not depend on the model.*

`heldout.py` writes `results/heldout_checks.json` and `plots/heldout_gm.png`. Nothing is refitted: it takes the
committed `dual_control` parameters from `fit_results.json` (and, for comparison, `refit_25`) and compares them
with Raytheon data that no fit has seen (in #48, also p.8 bottom).
- Model gm(g1) = ∂Ib/∂Ec1 and gm(g3) = ∂Ib/∂Ec3 come from central differences (±0.01 V).
- The errors are RMS of ln((model + 10 µmho)/(sheet + 10 µmho)), median |relative error| and mean relative
  error (bias). For currents the offset is 0.02 mA, as in the fit.

**The parameters were not changed in #48.** The gm check alone would not call for a refit. The p.8-bottom check
(below) showed the #25 model is clearly wrong in one region, g3 well below cut-off at plate voltages away from
120 V. That was reported for a decision and is addressed by the #51 refit.

**gm check** (all digitised points, toes included):

| graph | n | RMS log | median | bias |
|---|---|---|---|---|
| all gm(g1) | 1180 | 0.241 | 9 % | +3 % |
| all gm(g3) | 587 | 0.419 | 23 % | +0 % |
| p.11 bottom, gm(g1) vs Ec3, Ec2 family | 363 | 0.342 | 8 % | +16 % |
| p.12 top, gm(g1) vs Ec1, Ec2 family | 504 | 0.204 | 12 % | −1 % |
| p.12 bottom, gm(g1) vs Ec3, Ec1 family | 313 | 0.132 | 8 % | −4 % |
| p.13 top, gm(g3) vs Ec3, Ec2 family | 300 | 0.472 | 27 % | −2 % |
| p.13 bottom, gm(g3) vs Ec3, Ec1 family | 287 | 0.355 | 20 % | +3 % |

- **gm(g1) near the fitted operating point is good.**
  - Ec2 = 120 V, Ec1 = −2 V on p.11: RMS log 0.099, median 4 %. Ec1 = −2 V on p.12 bottom: 5 %.
  - The large RMS on p.11 comes mostly from the cut-off toes. There gm is a few tens of µmho and a 0.1 V
    shift is a factor of several.
- **gm(g1) goes wrong toward Ec1 = 0 and high screen voltage.**
  - On p.12 top the sheet's gm(g1) peaks at 2860–3400 µmho, 0.4–1.7 V below Ec1 = 0. The model's peaks
    12–24 % higher (3410–3860 µmho), at or near Ec1 = 0.
  - With positive g3 it overshoots: Ec1 = 0 / −1 V on p.12 bottom are 41 / 24 % high, and Ec2 = 140 V on
    p.11 levels off about 15 % high (Ec3 ≥ 8 V).
  - At Ec2 = 60–80 V the model's gm(g1) is low on p.12 top (bias −11 / −16 %) and high on p.11 bottom
    (+21 / +35 %, mostly toe points).
- **gm(g3) has the right size but the wrong shape.**
  - The sheet's gm(g3) peaks at Ec3 = −3.5…−5.3 V and falls steeply below. For Ec2 ≥ 100 V the model's peak
    is 0.4–1.9 V less negative.
  - Peak heights are 0.54–1.29× the sheet's: low at Ec2 = 60–100 V, high at Ec1 = 0 / −1 V and at
    Ec2 = 140 V, within 1 % at Ec1 = −2 V, Ec2 = 120 V.
- **Sheet self-consistency (the floor).** The sheet's gm curves against the slope (local quadratic) of its own
  current curves at the same conditions:
  - p.12 top vs p.9 bottom: n 363, RMS log 0.179, median 7 %, bias −7 %.
  - p.13 top vs p.10 bottom: n 230, RMS log 0.320, median 12 %, bias −6 %.
  - p.13 bottom vs p.11 top: n 230, RMS log 0.341, median 12 %, bias +2 %.

  So the drawn gm and current curves agree to about 10 % (gm(g3) worse at low current). The model's gm(g1) is
  near that floor, and its gm(g3) is about twice it.
- **GE p.1 typical gm vs the Raytheon gm curves** (Eb = Ec2 = 120 V, Ec1 = −2 V):

  | Ec3 | gm(g1) GE / Raytheon p.11 (p.12 top) / model | gm(g3) GE / Raytheon p.13 top, bottom / model |
  |---|---|---|
  | −3 V | 1850 / 1962 / 1827 | 810 / 699, 703 / 785 |
  | 0 V | 3200 / 3168 (3188) / 3217 | 470 / 404, 386 / 432 |

  GE's and Raytheon's gm(g1) agree to 6 %, and the model is within 7 % of both. Raytheon's gm(g3) is 13–18 %
  below GE's, and the model sits in between (7–12 % above Raytheon). The Ec3 = 0 values are read across the heavy zero line, within 0.3 V on each side.

**p.8 bottom: plate family at fixed Ec3** (Ec2 = 120 V, Ec1 = −2 V; `p8_bottom` in the JSON):

| Ib points | n | RMS log | median | bias |
|---|---|---|---|---|
| Ec3 = −1…−9 V | 597 | 0.857 | 33 % | −11 % |
| Ec3 = −10…−14 V | 166 | 2.502 | 96 % | −94 % |
| 0.1–0.6 mA | 132 | 1.883 | 96 % | +1 % |

- At Eb = 120 V the model is about as close to these curves as to p.10/p.11. Model / p.8 bottom in mA:
  4.50 / 4.71 at Ec3 = −1 V, 3.13 / 3.31 at −3 V, 1.56 / 1.60 at −5 V, 0.49 / 0.37 at −7 V.
- **Away from 120 V the plate voltage moves the g3 cut-off on the sheet much more than in the model.**
  - Ib reaches 0.3 mA at Eb = 66 V for Ec3 = −5 V and at 413 V for −14 V. A straight-line fit gives an
    effective plate-to-g3 μ of 39 (46 at 1 mA; `g3_cutoff_vs_plate_voltage` in the JSON).
  - The model's MU3 is 225. So for Ec3 ≤ −7 V it is far low at high Eb (−54 to −99 % per curve).
  - For Ec3 = −3…−5 V it is too high in the toe at low Eb (+6 to +43 % per curve).
  - The #25 data varied Ec3 only at Eb = 120 V, so it could not pin MU3. p.8 bottom is the data that would.
- The two Ic2 curves (Ec3 = −14 / −4 V, Eb 240–380 V) are over-predicted by 15 / 26 %.
- Fixing this needs a refit with p.8 bottom in the fit set, and probably a stronger plate term in the share
  function. That was out of scope in #48. The #25 model is only reliable for Ec3 ≲ −5 V close to Eb = 120 V.
  #51 did the refit (same model form; next section).

## Refit with p.8 bottom (issue #51)
**Why.** In the #25 data, Ec3 is varied only at Eb = 120 V, so nothing pinned MU3, the plate-to-g3 μ in the
share function. It ended at 225, and on p.8 bottom (held out in #48) the model's g3 cut-off barely moved with
plate voltage (RMS log 2.50, bias −94 % at Ec3 ≤ −10 V). p.8 bottom is the only digitised graph that varies Eb at
fixed negative Ec3.

**What changed.** `fit.py` adds the 763 p.8 bottom Ib points (Ec3 = −1…−14 V, Ec2 = 120 V, Ec1 = −2 V,
Eb 15–435 V) to the 2779 #25 fit points. Nothing else changed: the model form, the log residual (I0 = 0.02 mA)
and the bounds are the same. Nothing was re-digitised. The curves #48 skipped (Ib at Ec3 = 0…+10 V, the other
nine Ic2 curves, parts of toes) stay out.
- **Weighting:** each p.8 bottom point has weight 1 (`W_P8`), like every other Raytheon point. It is one 5 V Eb
  step, as on the p.8 top and p.9 top plate families, so a p.8 bottom curve counts as much as one of those.
  It gets no extra weight, even though it alone constrains MU3.
- **Sensitivity check:** a refit with weight 0.25 is kept as `sensitivity_p8_weight_0_25`.
- **What stays held out:** the two p.8 bottom Ic2 curves (screen current under g3 control, like the #25 split
  check). Ic3 is not modelled.
- **Convergence:** all 7 seeded starts reach the same minimum (MU3 = 43.22), and so do 11 of 12 random restarts.
  None goes lower.

**Parameters** (`refit_25.params` → `dual_control.params`):

| param | MU | EX | KG1 | KP | FMAX | MU3 | V3C | S3 | P3 | RI | KVB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BEFORE (#25, `refit_25`) | 29.92 | 1.509 | 716 | 151.3 | 0.8215 | 225.4 | -6.523 | 2.475 | 3.198 | 63.73 | 17.92 |
| AFTER (#51, `dual_control`) | 30.14 | 1.494 | 697.6 | 149.5 | 0.7107 | 43.22 | -3.031 | 2.075 | 2.302 | 53.93 | 5.231 |
| sensitivity: p.8 weight 0.25 | 30.01 | 1.5 | 703.4 | 150.8 | 0.7026 | 44.17 | -3.422 | 2.158 | 2.456 | 61.18 | 0.5 |

- **MU3 lands in the sheet's range.** The straight-line fit of the Eb at which each p.8 bottom curve reaches a
  fixed current gives a plate-to-g3 μ of 39.0 (at 0.3 mA) and 45.5 (at 1 mA). MU3 = 43.2 lies between them.
  - Measured the same way on the model's curves (`model_plate_mu` in `heldout_checks.json`), the #51 model gives
    42.7 / 43.0 and the weight-0.25 refit 44.1 / 44.1.
  - The #25 model gave 119 / 161, and only from Ec3 = −5…−9 / −5…−7 V. For more negative Ec3 its Ib never
    reached the level below 450 V.
  - With weight 0.25, MU3 = 44.2, so MU3 does not depend on the weighting. That refit puts KVB on its lower bound
    (0.5 V), one reason to prefer weight 1.
- **Other parameters that moved:**
  - V3C −6.5 → −3.0 V: at Eb = 120 V, Va/MU3 is now 2.8 V instead of 0.5 V, and V3C largely offsets that.
    Vg3 + Va/MU3 − V3C at Ec3 = 0 goes from 7.1 to 5.8 V, and S3 and P3 adjust the shape.
  - FMAX 0.82 → 0.71, KVB 17.9 → 5.2 V, P3 3.2 → 2.3, S3 2.47 → 2.07 V, RI 64 → 54.
  - MU, EX, KG1 and KP (cathode current) change by less than 3 %.

**Errors**: RMS log error / median |relative error| / mean relative error (bias). Current errors are from
`fit_results.json`; gm and p.8 bottom Ic2 errors from `heldout_checks.json` (`before_refit_25` for #25). "0.1–0.6 mA"
selects points whose datasheet current is in that range. The weight-0.25 column is in `fit_results.json` for the
current rows only.

| | n | BEFORE (#25) | AFTER (#51) | weight 0.25 |
|---|---|---|---|---|
| p.8 top–p.11 fit points (the #25 fit set), all | 2779 | 0.090 / 3 % / +0 % | 0.102 / 5 % / -1 % | 0.097 / 4 % / +0 % |
| p.8 top–p.11 fit points, 0.1–0.6 mA | 391 | 0.179 / 10 % / +3 % | 0.181 / 11 % / +3 % | 0.179 / 10 % / +3 % |
| **p.8 bottom Ib, all** (fitted since #51) | 763 | 1.391 / 53 % / -29 % | 0.233 / 11 % / +10 % | 0.248 / 9 % / +12 % |
| p.8 bottom Ib, Ec3 = −1…−9 V | 597 | 0.857 / 33 % / -11 % | 0.248 / 10 % / +11 % | 0.268 / 8 % / +15 % |
| **p.8 bottom Ib, deep cut Ec3 = −10…−14 V** | 166 | 2.502 / 96 % / -94 % | 0.167 / 14 % / +4 % | 0.155 / 13 % / +3 % |
| p.8 bottom Ib, 0.1–0.6 mA | 132 | 1.883 / 96 % / +1 % | 0.395 / 16 % / +22 % | 0.442 / 15 % / +31 % |
| held-out split check (Ic2 vs Ec3), all | 655 | 0.134 / 8 % / +6 % | 0.184 / 16 % / +15 % | 0.175 / 15 % / +14 % |
| held-out split check, 0.1–0.6 mA (Raytheon held-out low current) | 111 | 0.179 / 14 % / -7 % | 0.177 / 13 % / +5 % | 0.173 / 13 % / +2 % |
| GE p.3/p.4 cross-check, all | 581 | 0.149 / 5 % / -9 % | 0.168 / 9 % / -10 % | 0.152 / 6 % / -9 % |
| GE p.3/p.4 cross-check, 0.1–0.6 mA | 39 | 0.229 / 18 % / -17 % | 0.251 / 16 % / -17 % | 0.232 / 16 % / -17 % |
| held-out gm(g1), pp. 11–12 | 1180 | 0.241 / 9 % / +3 % | 0.242 / 13 % / -1 % | – |
| held-out gm(g3), p. 13 | 587 | 0.419 / 23 % / +0 % | 0.625 / 30 % / -16 % | – |
| &nbsp; p.11 bottom gm(g1) vs Ec3, Ec2 family | 363 | 0.342 / 8 % / +16 % | 0.325 / 11 % / +8 % | – |
| &nbsp; p.12 top gm(g1) vs Ec1, Ec2 family | 504 | 0.204 / 12 % / -1 % | 0.213 / 14 % / -2 % | – |
| &nbsp; p.12 bottom gm(g1) vs Ec3, Ec1 family | 313 | 0.132 / 8 % / -4 % | 0.157 / 13 % / -9 % | – |
| &nbsp; p.13 top gm(g3) vs Ec3, Ec2 family | 300 | 0.472 / 27 % / -2 % | 0.651 / 33 % / -18 % | – |
| &nbsp; p.13 bottom gm(g3) vs Ec3, Ec1 family | 287 | 0.355 / 20 % / +3 % | 0.598 / 25 % / -14 % | – |
| held-out p.8 bottom Ic2, Ec3 = −14 V | 16 | 0.139 / 15 % / +15 % | 0.129 / 14 % / +14 % | – |
| held-out p.8 bottom Ic2, Ec3 = −4 V | 12 | 0.229 / 26 % / +26 % | 0.240 / 23 % / -21 % | – |

- **The deep-cut error is fixed.** On p.8 bottom at Ec3 = −10…−14 V, RMS log 2.502 → 0.167 and bias −94 % → +4 %.
  Over all of p.8 bottom it is 1.391 → 0.233.
  - Per curve, RMS log goes from 0.08–2.79 to 0.05–0.40 (`per_curve_p8_bottom`). The −10…−14 V curves are now
    0.14–0.18, inside the 0.03–0.25 spread of the per-curve errors on the #25 fit points.
  - The model's Eb for 1 mA at Ec3 = −10 V is 308 V; the sheet's is 302 V (#25: never reached).
- **What it costs.** With one share function, the fit trades a little elsewhere:
  - **#25 fit points:** RMS log 0.090 → 0.102. The 0.1–0.6 mA part is unchanged (0.179 → 0.181).
  - **Screen current:** the plate families' Ic2 (p.8 top, p.9 top) goes from 0.03–0.07 to 0.08–0.13 per curve
    (Ec2 = 60 V on p.8 top stays at 0.03). The lower FMAX gives more screen current.
  - **p.10 bottom, Ib at Ec2 = 140 V:** 0.179 → 0.250.
  - **Held-out split check:** RMS log 0.134 → 0.184, bias +6 % → +15 %. Its 0.1–0.6 mA part improves slightly
    (0.179 → 0.177).
  - **GE cross-check:** 0.149 → 0.168.
  - **Ib at Eb = 120 V on p.8 bottom** (mA, sheet / #25 / #51):

    | Ec3 (V) | −1 | −3 | −5 | −7 |
    |---|---|---|---|---|
    | sheet / #25 / #51 | 4.71 / 4.50 / 4.43 | 3.31 / 3.13 / 3.04 | 1.60 / 1.56 / 1.41 | 0.37 / 0.49 / 0.39 |

    So #51 is 6–12 % low at −1…−5 V.
- **gm(g3) got worse (held out):** RMS log 0.419 → 0.625, bias 0 → −16 %. The weight-0.25 refit gives 0.580 /
  −12 % (computed with `heldout.py`'s functions, not stored).
  - The loss is at positive g3. By Ec3 band, the #51 / #25 values are (stored since #53 as `gm_check.by_Ec3_band`
    and `before_refit_25.gm_by_Ec3_band` in `heldout_checks.json`):

    | Ec3 band | n | RMS log | bias |
    |---|---|---|---|
    | < −6 V | 59 | 0.60 / 0.68 | +66 / +100 % |
    | −6…0 V | 228 | 0.23 / 0.24 | +4 / +4 % |
    | 0…+5 V | 158 | 0.37 / 0.20 | −22 / +6 % |
    | ≥ +5 V | 142 | 1.11 / 0.63 | −76 / −52 % |

    With MU3 = 43 the share reaches saturation at a lower Ec3, so the model's plate current flattens too early
    above Ec3 ≈ 0.
  - Peak gm(g3) position and height hardly move (#51 peaks 0.58–1.35× the sheet's, #25 0.54–1.29×). At the
    GE/Raytheon point (Ec3 = −3 V) gm(g3) is now 820 µmho: +1 % vs GE, +17 % vs Raytheon (test tolerance 20 %).
- **gm(g1):** RMS log 0.241 → 0.242, median 9 → 13 %, bias +3 → −1 %. At Ec2 = 120 V on p.11 bottom it is
  0.094 / 3 %.

**Remaining deep-g3 limitations** (model form, not data; not changed here):
- **The cut-off moves correctly with Eb, but its shape is wrong.**
  - At fixed deep Ec3 the model's Ib rises too steeply with Eb at the top end: Ec3 = −6 V at Eb = 405 V is
    5.19 mA vs the sheet's 4.17 (+25 %), and −10 V at 420 V is 3.05 vs 2.18 (+40 %).
  - Low-Eb toes are too high: Ec3 = −3 V at Eb = 15 V is 0.90 vs 0.08 mA. These toes are the worst p.8 bottom
    curves: −3 / −4 V have RMS log 0.40 / 0.31 and +31 / +19 % bias, and the p.8 bottom 0.1–0.6 mA points are
    0.395 / +22 %.
  - The single logistic in (Vg3 + Va/MU3) cannot give both the sheet's slope in Eb and its slope in Ec3.
- **Ec3 ≤ −10 V is only measured at Ec2 = 120 V, Ec1 = −2 V.** Elsewhere it is extrapolated.
- **Cathode current is still independent of g3.** Ic2 at Ec3 = −10 V on p.11 is over-predicted by 37 / 24 / 16 /
  16 % at Ec1 = 0 / −1 / −2 / −3 V (#25: 38 / 24 / 16 / 15 %).
- **gm(g3) is too low for Ec3 > 0** (above).

A g3-dependent cathode-current term and a share function with separate plate and g3 slopes would address these.
That is a new model, not attempted here. (#53 tried it; next section.)

## g3-dependent cathode current (issue #53)
**Status: study only. `dual_control` (the #51 fit) and `results/6AS6_fit.inc` are unchanged.** The refit the
issue asked for (new form, #51 fit set) cannot determine the new cathode-current parameter. At its optimum the
cathode current is wrong by about a factor of 2 in its g3 drop, and the held-out screen check is twice as bad as
#51's. Making it the current model would be a regression. Options are at the end of this section.
`ik_g3.py` runs every fit below and writes `results/ik_g3_study.json` (about 45 s); `tests/test_ik_g3.py` checks it.

**Why.** After #51 (previous section): gm(g3) got worse (RMS log 0.419 → 0.625, −76 % at Ec3 ≥ +5 V). The
held-out screen bias rose from +6 % to +15 %. The g3 cut-off has the wrong shape in Eb. The model's cathode
current does not depend on g3, but on Raytheon p.11 (`raytheon_p11_cathode_current_mA`, split-check table above)
Ib + Ic2 stays within 10 % from Ec3 = −4 to +10 V and is 13–25 % lower at Ec3 = −10 V.

**Model form** (`model6as6.py`; KR = 0 and XA = 1, the defaults, give the #51 equations exactly, and fit.py /
emit_spice.py reproduce `results/` byte for byte; `heldout_checks.json` only gains the gm-by-Ec3-band entries):

  `Ik0 = 2·E1^EX/KG1` (Koren E1 in Vg1, Vg2, as before)
  `z = (Vg3 + Up(Va) − V3C − RI·Ik0)/S3`,  `Up = (VA0/MU3)·(1 + ((Va/VA0)^XA − 1)/XA)`,  VA0 = 120 V
  `s = (1 + exp(−z))^(−P3)`,  `f = FMAX·(2/π)atan(Va/KVB)·s`
  `Ik = Ik0·(1 − KR/(1 + exp(z)))`,  `Ip = Ik·f`,  `Ig2 = Ik·(1 − f)`

- **Cathode-current term (KR, one parameter).** The current-division picture (Radiomuseum "Dual control
  Pentodes" thread: roughly constant cathode current under g3 control) holds on p.11 only while g3 passes
  electrons. Past cut-off the electrons turned back at g3 lower the cathode current (the space-charge explanation
  given since #25; I have no publication that gives its size). `1/(1 + e^z)` reuses the share's own cut-off
  variable. While g3 passes electrons (z > 0) it is ≈ 0, so Ik ≈ Ik0. Past cut-off it goes to 1, so Ik → (1 − KR)·Ik0,
  which is bounded below. It is overflow-safe as `exp(−softplus(z))`, like the existing `exp(−P3·softplus(−z))`.
  - Three other one-parameter forms were fitted in a scratch study (not committed): `1 − KR·(1 − s)`,
    `exp(−KR·softplus(−z))` and `1/(1 + KR·softplus(−z))`. All three gave the same identifiability result as
    below.
  - `1 − KR·(1 − s)` also moves Ik above cut-off: Ik(+10 V)/Ik(0) = 1.03–1.05, where the sheet's is 0.95–1.02.
  - The exp form is exactly degenerate with P3 on every Ib point (Ib ∝ exp(−(P3 + KR)·softplus(−z))). The exp and
    1/(1 + …) forms also send Ik → 0 as Vg3 → −∞.
  - The form's *shape* was chosen knowing the p.11 cathode-current table, which comes from the held-out Ic2 curves
    and has been documented since #25. Its *size* (KR) is fitted.
- **Plate term (XA, one parameter): why the share function changed too.**
  - The KR-only refit leaves the p.8 bottom cut-off shape where it was (RMS log 0.233 → 0.230, 0.1–0.6 mA 0.395 →
    0.389). A cathode-current factor acts through the same z, so it cannot change how the cut-off moves with Eb.
  - On the sheet that movement is not linear. Eb for Ib = 0.3 mA on p.8 bottom (`g3_cutoff_vs_plate_voltage`)
    is 25, 44, 66, 88 V at Ec3 = −3…−6 V, about 21 V per volt of Ec3. At −11…−14 V it is 265, 310, 353, 413 V,
    about 49 V/V.
  - Va/MU3 gives straight iso-current lines: #51 has 43 V/V everywhere and reaches 0.3 mA at Eb = 25 V for
    Ec3 = −5 V, where the sheet needs 66 V.
  - With XA < 1 the local plate-to-g3 μ is MU3·(Va/VA0)^(1−XA). It rises with Eb, and the g3 slope (S3) stays.
    Those are the "separate plate and g3 slopes" of the #51 residual.
  - The form is a Box–Cox power about VA0 = 120 V, so at Va = 120 V the plate term and its slope are #51's
    (Va/MU3), and XA = 1 is exactly #51.
  - The power's base is floored at 1 mV, so its slope stays finite at Va → 0.
  - The empirical basis is the sheet behaviour measured in this repo (p.8 bottom). No publication is cited for it.

**Fits** (the #51 residual ln((I_model + I0)/(I_data + I0)), I0 = 0.02 mA, and bounds; KR ∈ [0, 0.95],
XA ∈ [0.2, 1.5]):
- `refit_53`: KR + XA on the #51 fit set, which is p.8 top–p.11 without the held-out Ic2-vs-Ec3 curves
  (2779 points) plus p.8 bottom Ib (763, weight 1). This is the refit the issue asked for.
- `refit_53_kr_only` and `refit_53_xa_only`: each new parameter alone, same data.
- `diagnostic_p11_ic2_in_fit`: KR + XA with the p.11 top Ic2-vs-Ec3 curves (328 points) added to the fit.
  - It is **not a candidate under the #53 data split**, because it fits half of the held-out screen check.
  - It shows what KR does once screen current under g3 control is in the data.
  - p.10 bottom Ic2 vs Ec3 (327) and p.8 bottom Ic2 (28) stay held out.
- **Restarts:** every fit uses fit.py's 7 seeded starts (KR = 0.15, XA = 0.7 appended where those are fitted).
  `refit_53` and the diagnostic also use 12 random starts (log-uniform in bounds, uniform for V3C/RI/KR, seed 53).
  "Same minimum" means a cost within 10⁻⁶ (relative) of the best; no start goes lower.
  - `refit_53`: 7/7 seeded and 11/12 random reach it.
  - Diagnostic: 7/7 and 10/12.
  - KR-only and XA-only: 7/7 seeded.
- **KVB is on its lower bound** (0.5 V) in every fit with XA. With the power term the plate term itself carries the
  low-Eb roll-off that the knee (KVB) carried in #51, so the two are partly redundant.

**Parameters**:

| param | MU | EX | KG1 | KP | FMAX | MU3 | V3C | S3 | P3 | RI | KVB | KR | XA |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BEFORE (#51, `dual_control`) | 30.14 | 1.494 | 697.6 | 149.5 | 0.7107 | 43.22 | -3.031 | 2.075 | 2.302 | 53.93 | 5.231 | (0) | (1) |
| `refit_53` (KR + XA, #51 fit set) | 30.11 | 1.506 | 684.5 | 148.5 | 0.684 | 33.36 | -3.816 | 2.285 | 3.192 | 45.49 | 0.5 | 0.7914 | 0.5647 |
| `refit_53_kr_only` | 30.09 | 1.508 | 681.3 | 148.8 | 0.707 | 43.41 | -3.879 | 2.246 | 2.372 | 52.67 | 7.839 | 0.7467 | (1) |
| `refit_53_xa_only` | 30.14 | 1.494 | 698.6 | 149.3 | 0.6916 | 33.62 | -3.086 | 2.136 | 3.063 | 44.95 | 0.5 | (0) | 0.5761 |
| diagnostic: + p.11 top Ic2 in the fit | 30.12 | 1.490 | 696.5 | 149.6 | 0.708 | 33.60 | -4.077 | 2.347 | 3.992 | 46.54 | 0.5 | 0.2737 | 0.5743 |

**Plate μ (MU3).**
- **With XA = 1** (#51, KR-only), MU3 is the plate μ of a straight cut-off line: 43.2 / 43.4.
- **With XA < 1**, MU3 is the *local* plate-to-g3 μ at Eb = 120 V. It is 33.4–33.6, below the 39–46 sheet range
  quoted since #51. That range is not a local value, though: it is the slope of a straight line through the p.8
  bottom cut-off points at Ec3 = −5…−14 V, which spans Eb 66–413 V (39.0 at 0.3 mA, 45.5 at 1 mA).
  - Measured the same way on the model's curves, every XA fit gives 42.1–42.2 / 44.2 (#51: 42.7 / 43.0).
  - The local μ of the XA fits is 21 at 40 V, 33.5 at 120 V, 45.9 at 250 V and 56 at 400 V
    (`local_plate_mu_at_Eb_V`). The sheet's 0.3 mA line has 21 V/V at Eb 25–88 V and 49 V/V at 265–413 V.
  - So the plate μ stays in the sheet's range wherever the sheet defines it. MU3 has a different meaning and its
    number is not comparable with #51's.
  - `test_plate_mu_stays_in_the_sheet_range` checks:
    - the chord μ against the sheet's (15 %);
    - MU3 in [39.0, 45.5] when XA = 1;
    - the local μ at 250 V in that range (−5 / +15 %) when XA < 1.

**Errors**: RMS log error / median |relative error| / mean relative error (bias); from `ik_g3_study.json` (currents
as in fit.py, gm and p.8 bottom Ic2 as in heldout.py). "0.1–0.6 mA" selects points whose datasheet current is in
that range. *Diagnostic column: the p.11 top Ic2 curves are fitted there, so its "held-out split" rows are not
held out; the p.10 bottom rows are.*

| | n | BEFORE (#51) | `refit_53` | KR only | XA only | diagnostic |
|---|---|---|---|---|---|---|
| fit set (p.8 top–p.11), all | 2779 | 0.102 / 5 % / -1 % | 0.104 / 5 % / -1 % | 0.101 / 5 % / -1 % | 0.106 / 5 % / -1 % | 0.107 / 5 % / -1 % |
| fit set, 0.1–0.6 mA | 391 | 0.181 / 11 % / +3 % | 0.186 / 12 % / +3 % | 0.183 / 12 % / +3 % | 0.185 / 11 % / +4 % | 0.184 / 12 % / +4 % |
| **held-out split (Ic2 vs Ec3), all** | 655 | 0.184 / 16 % / +15 % | 0.397 / 26 % / +0 % | 0.415 / 29 % / +0 % | 0.184 / 16 % / +15 % | (0.157 / 12 % / +6 %) |
| held-out split, 0.1–0.6 mA | 111 | 0.177 / 13 % / +5 % | 0.399 / 29 % / +0 % | 0.414 / 31 % / +1 % | 0.179 / 13 % / +5 % | (0.205 / 23 % / -0 %) |
| &nbsp; p.10 bottom Ic2 (held out in all), all | 327 | 0.206 / 16 % / +17 % | 0.441 / 33 % / +3 % | 0.461 / 36 % / +4 % | 0.207 / 16 % / +17 % | 0.199 / 15 % / +9 % |
| &nbsp; p.10 bottom Ic2, 0.1–0.6 mA | 74 | 0.206 / 25 % / +12 % | 0.487 / 37 % / +3 % | 0.506 / 39 % / +3 % | 0.210 / 25 % / +12 % | 0.236 / 25 % / +5 % |
| &nbsp; p.11 top Ic2, all | 328 | 0.158 / 16 % / +14 % | 0.347 / 22 % / -3 % | 0.365 / 26 % / -3 % | 0.156 / 16 % / +13 % | (fitted) 0.101 / 10 % / +4 % |
| GE p.3/p.4 cross-check, all | 581 | 0.168 / 9 % / -10 % | 0.179 / 9 % / -10 % | 0.170 / 9 % / -11 % | 0.181 / 10 % / -10 % | 0.183 / 11 % / -11 % |
| GE cross-check, 0.1–0.6 mA | 39 | 0.251 / 16 % / -17 % | 0.245 / 14 % / -16 % | 0.240 / 14 % / -16 % | 0.253 / 16 % / -17 % | 0.257 / 17 % / -18 % |
| p.8 bottom Ib, all | 763 | 0.233 / 11 % / +10 % | 0.160 / 9 % / +7 % | 0.230 / 11 % / +9 % | 0.163 / 9 % / +7 % | 0.161 / 8 % / +6 % |
| p.8 bottom Ib, Ec3 = −1…−9 V | 597 | 0.248 / 10 % / +11 % | 0.159 / 8 % / +8 % | 0.245 / 10 % / +11 % | 0.163 / 8 % / +9 % | 0.160 / 7 % / +8 % |
| p.8 bottom Ib, deep cut Ec3 = −10…−14 V | 166 | 0.167 / 14 % / +4 % | 0.164 / 12 % / +0 % | 0.164 / 14 % / +3 % | 0.166 / 12 % / +0 % | 0.162 / 12 % / +0 % |
| p.8 bottom Ib, 0.1–0.6 mA | 132 | 0.395 / 16 % / +22 % | 0.238 / 15 % / +6 % | 0.389 / 16 % / +21 % | 0.244 / 14 % / +6 % | 0.246 / 15 % / +7 % |
| held-out p.8 bottom Ic2, Ec3 = −14 V | 16 | 0.129 / 14 % / +14 % | 0.745 / 52 % / -52 % | 0.790 / 55 % / -54 % | 0.130 / 14 % / +14 % | 0.094 / 9 % / -8 % |
| held-out p.8 bottom Ic2, Ec3 = −4 V | 12 | 0.240 / 23 % / -21 % | 0.223 / 20 % / -20 % | 0.243 / 22 % / -21 % | 0.230 / 21 % / -20 % | 0.248 / 23 % / -22 % |
| held-out gm(g1), all | 1180 | 0.242 / 13 % / -1 % | 0.242 / 13 % / +1 % | 0.243 / 13 % / -1 % | 0.241 / 13 % / +1 % | 0.239 / 12 % / +1 % |
| &nbsp; gm(g1), Ec3 < −6 V | 18 | 0.718 / 51 % / +47 % | 0.708 / 50 % / +53 % | 0.726 / 52 % / +48 % | 0.693 / 48 % / +54 % | 0.707 / 49 % / +54 % |
| &nbsp; gm(g1), −6…0 V | 199 | 0.389 / 16 % / +10 % | 0.382 / 14 % / +13 % | 0.390 / 16 % / +9 % | 0.381 / 14 % / +14 % | 0.383 / 14 % / +13 % |
| &nbsp; gm(g1), 0…+5 V (incl. p.12 top at 0) | 702 | 0.191 / 13 % / -3 % | 0.197 / 13 % / -2 % | 0.193 / 13 % / -4 % | 0.196 / 13 % / -2 % | 0.191 / 12 % / -3 % |
| &nbsp; gm(g1), ≥ +5 V | 261 | 0.125 / 11 % / -5 % | 0.123 / 11 % / -4 % | 0.123 / 11 % / -4 % | 0.126 / 12 % / -5 % | 0.115 / 11 % / -3 % |
| **held-out gm(g3), all** | 587 | 0.625 / 30 % / -16 % | 0.540 / 28 % / -12 % | 0.524 / 27 % / -11 % | 0.636 / 32 % / -17 % | 0.507 / 26 % / -11 % |
| &nbsp; gm(g3), Ec3 < −6 V | 59 | 0.604 / 39 % / +66 % | 0.604 / 39 % / +71 % | 0.606 / 38 % / +67 % | 0.601 / 39 % / +70 % | 0.610 / 38 % / +74 % |
| &nbsp; gm(g3), −6…0 V | 228 | 0.234 / 19 % / +4 % | 0.228 / 17 % / +4 % | 0.236 / 18 % / +4 % | 0.227 / 17 % / +4 % | 0.223 / 17 % / +3 % |
| &nbsp; gm(g3), 0…+5 V | 158 | 0.366 / 26 % / -22 % | 0.288 / 20 % / -17 % | 0.262 / 20 % / -11 % | 0.399 / 31 % / -27 % | 0.253 / 17 % / -13 % |
| &nbsp; gm(g3), ≥ +5 V | 142 | 1.109 / 78 % / -76 % | 0.936 / 70 % / -69 % | 0.904 / 69 % / -67 % | 1.124 / 78 % / -76 % | 0.871 / 67 % / -65 % |

**Cathode current on p.11** (Eb = Ec2 = 120 V; Ik(−10 V)/Ik(0) and Ik(+10 V)/Ik(0); sheet from
`raytheon_p11_cathode_current_mA`, with Ic2 alone at −10 V):

| Ec1 | sheet | BEFORE (#51) | `refit_53` | KR only | diagnostic |
|---|---|---|---|---|---|
| 0 V | 0.75 / 0.95 | 1 / 1 | 0.36 / 1.05 | 0.36 / 1.07 | 0.79 / 1.02 |
| −1 V | 0.81 / 0.99 | 1 / 1 | 0.38 / 1.04 | 0.37 / 1.05 | 0.80 / 1.01 |
| −2 V | 0.84 / 1.02 | 1 / 1 | 0.39 / 1.04 | 0.39 / 1.05 | 0.80 / 1.01 |
| −3 V | 0.87 / 1.01 | 1 / 1 | 0.40 / 1.03 | 0.40 / 1.04 | 0.81 / 1.01 |

**KR profile** (`kr_profile`: KR held fixed, every other parameter of `refit_53` refitted on the #51 fit set):

| KR | 0 | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 |
|---|---|---|---|---|---|---|---|---|---|---|
| fit cost | 25.85 | 25.58 | 25.35 | 25.16 | 25.02 | 24.91 | 24.83 | 24.78 | 24.77 | 24.79 |
| held-out split RMS log | 0.184 | 0.180 | 0.184 | 0.197 | 0.220 | 0.253 | 0.294 | 0.343 | 0.402 | 0.472 |
| Ik(−10)/Ik(0) at Ec1 = −2 V (sheet 0.84) | 1 | 0.92 | 0.84 | 0.76 | 0.68 | 0.60 | 0.53 | 0.45 | 0.39 | 0.32 |

**Findings.**
- **On the #51 fit set, KR is not determined.**
  - Between KR = 0 and 0.9 the cost changes by 4.4 %. The values that match the sheet's cathode current
    (0.2–0.3) cost only 1.6–2.4 % more than the minimum.
  - The minimum (0.79; KR alone 0.75) is set by the shape of the Ib toe. Ib = Ik·f, and every Ib point sees only
    the product, so a g3-dependent Ik is traded against the share's cut-off position and slope (KR alone: V3C
    −3.03 → −3.88 V, S3 2.07 → 2.25 V).
  - The only digitised data that separate Ik(g3) from f(g3) are screen currents under g3 control. Those are the
    p.10 bottom / p.11 top Ic2 and p.8 bottom Ic2 curves, and this split holds all of them out.
  - At the minimum the cathode current falls to 0.36–0.40 of its Ec3 = 0 value at −10 V (sheet 0.75–0.87).
    Held-out screen current gets much worse: split RMS log 0.184 → 0.397, p.8 bottom Ic2 at −14 V +14 % → −52 %.
- **With screen data under g3 in the fit, KR is determined** (diagnostic):
  - KR = 0.27 from 17 of 19 starts.
  - Ik(−10)/Ik(0) = 0.79–0.81 (sheet 0.75–0.87); Ik(+10)/Ik(0) = 1.01–1.02 (sheet 0.95–1.02).
  - The curves still held out improve or hold: p.10 bottom Ic2 0.206 → 0.199 with bias +17 % → +9 %, and p.8
    bottom Ic2 at −14 V +14 % → −8 %.
  - gm(g3) improves (0.625 → 0.507), mostly at 0…+5 V (−22 % → −13 %).
  - It costs some low current: p.10 bottom Ic2 at 0.1–0.6 mA goes 0.206 → 0.236.
  - The sheet's drop shrinks with current faster than the model's (sheet 0.75 at Ec1 = 0 → 0.87 at −3 V; model
    0.79 → 0.81).
- **The plate term (XA) helps whatever happens to KR.** p.8 bottom Ib 0.233 → 0.160–0.163, at 0.1–0.6 mA
  0.395 → 0.238–0.246, toe bias +22 % → +6 %. The other fitted curves change little: fit set 0.102 → 0.104–0.107,
  GE 0.168 → 0.179–0.183.
- **No variant fixes gm(g3) far from the operating point.**
  - At Ec3 ≥ +5 V every variant is still −65 to −76 %. The share saturates at FMAX there, which the cathode
    current cannot change.
  - Below −6 V every variant is still +66 to +74 %.
  - Held-out p.8 bottom Ic2 at Ec3 = −4 V is −20 % in every variant.

**Decision needed** (which parameter set becomes `dual_control`):
1. **Fit p.11 top Ic2 too** (`diagnostic_p11_ic2_in_fit`) and keep p.10 bottom Ic2 and p.8 bottom Ic2 as the
   held-out split check (327 + 28 points). This is the only option that makes Ik depend on g3 with a size the sheet
   supports. It changes the data split agreed for #25/#51/#53.
2. **XA only** (`refit_53_xa_only`): it fixes the p.8 bottom cut-off shape, but cathode current stays independent
   of g3, so #53 is not addressed.
3. **Keep #51.**

To adopt 1 or 2, `fit.py` would compute that fit as `dual_control`, with #51 kept as `refit_51`. Then rerun
`fit.py`, `heldout.py` and `emit_spice.py`, and update the `.inc` header text.
- `emit_spice.expressions(p)` already writes the KR/XA form: an extra node `ik0` holds Ik0, and the pins and
  subcircuit name are unchanged.
- I ran `spice_check/check_6as6.cir` and `compare.py` in ngspice 41 on scratch includes (not committed). The worst
  deviation from the Python model over the 20 curves, including the sweep start at Eb = 0 V, was 0.08 µA /
  9×10⁻⁶ relative for `refit_53` and 0.02 µA / 1.1×10⁻⁵ for the diagnostic.

## SPICE
`results/6AS6_fit.inc` defines `.SUBCKT 6AS6_FIT P G2 G3 G1 K` using B-sources (ln, exp, atan, pwr, max) and the
GE datasheet capacitances, with the #51 refit parameters (#53 did not change the file). The pin order and expressions are unchanged, so existing
netlists keep working. A test checks it against the Python model by evaluating the same expression strings over
Ec2 60–140 V, Ec3 −14…+12 V, Eb 20–420 V. **Checked in ngspice 41 (5 Nov 2026, #51 parameters):** `spice_check/check_6as6.cir` DC-sweeps the
subcircuit at the GE conditions (p.3: Ec2 120 V, Ec3 0, Eb 0–240 V, Ec1 0…−4 V; p.4: Eb = Ec2 = 120 V, Ec1 −1…−3 V,
Ec3 −10…+10 V) and the Raytheon p.9 transfer condition (Eb = 120 V, Ec2 = 60 and 140 V, Ec1 −5…0 V).
`spice_check/compare.py` compares Ib and Ic2 with the Python model. Worst deviation over all 20 curves (10 sweeps,
Ib and Ic2): 0.11 µA absolute, 6.0×10⁻⁶ relative (from the 6-significant-figure parameters in the `.inc`; #25:
0.14 µA, 4.6×10⁻⁵). Run: `cd spice_check && mkdir -p out &&
ngspice -b check_6as6.cir && python compare.py` → `spice_check.json`.

## Limitations
- **Valid range (fitted data):** Ec2 60–140 V, Ec1 −5 to 0 V, Ec3 about −9 to +12 V (−14 V at Ec2 = 120 V,
  Ec1 = −2 V only), Eb 20–435 V, currents down to about 0.1 mA. Below Eb ≈ 20–40 V (the knee, KVB) the curves
  bunch together and few points survive. There is no data for a positive g1 and no grid-current model. Away from
  Eb = 120 V, Ec3 is varied only on p.8 bottom (Ec2 = 120 V, Ec1 = −2 V).
- **Low current:** about 11 % median and RMS log 0.18 in 0.1–0.6 mA, with individual curves up to 20–31 % off
  (per-curve tables). Below 0.1 mA the model is extrapolation of Koren's exponential tail; the sheets have no usable
  curves there.
- **g3 cut-off:**
  - Cathode current does not depend on g3 in the current fit (#51). Ic2 is over-predicted by up to ~40 % for
    Ec3 ≲ −6 V. #53 added the term (KR), but the current data split cannot determine it
    ([g3-dependent cathode current](#g3-dependent-cathode-current-issue-53)).
  - The Ib toe near g3 cut-off on p.10 bottom is up to 2.4× high (at Ec2 = 140 V up to 50 % low).
  - Since #51 the cut-off moves with plate voltage at the sheet's rate (MU3 = 43; sheet 39–46). Its shape in Eb
    is still off: at deep Ec3, Ib rises too steeply at high Eb (+25…+40 % at 400–420 V), and the low-Eb toes at
    Ec3 = −3…−6 V are too high ([Refit with p.8 bottom](#refit-with-p8-bottom-issue-51)).
  - gm(g3) is too low for Ec3 > 0: −22 % at 0…+5 V and −76 % at ≥ +5 V (held-out p.13).
- **Source data:** "average characteristics" from manufacturers' sheets, not a measured valve. Raytheon and GE
  differ by about 10 % (production spread is far larger: Ib 2.5–9 mA, research note). The Raytheon graphs agree
  with each other to 2 % at a few mA but only 9–23 % at 0.4–0.7 mA.
- **Not yet used:**
  - Not digitised: the GE p.4 Ic2-vs-Ec3 plot, the Tung-Sol curves, and the parts of Raytheon p.8 bottom
    listed under issue #48 above.
  - Digitised but in no fit (held-out checks only): the Raytheon pp. 11–13 gm curves and the p.8 bottom Ic2
    curves. The p.8 bottom Ib curves are in the fit since #51.
  - Ic3 (g3 current) is digitised, but the model has no g3 current.
- **Tests:** importing `fit.py` in the tests reruns all five fits (about 10 s), which are deterministic. The tests
  check that the committed parameters and per-curve errors reproduce (including p.8 bottom), the error bounds, the
  low-current improvement, the split check and the cathode-current data.
  - `tests/test_raytheon_heldout.py` (#48) checks the new data's point counts and calibration, and p.8 bottom
    against p.10/p.11.
  - It also checks that `heldout_checks.json` reproduces (#51 and `before_refit_25`), and bounds on the gm and
    sheet-consistency numbers.
  - #51 adds: MU3 lies in the sheet's plate-μ range, the model's cut-off line has the sheet's slope (15 %), and
    the p.8 bottom deep-cut error (Ec3 ≤ −10 V) is below 0.25 RMS log and less than a tenth of the #25 value.
  - Tolerances widened in #51, each stated in the test:
    - Ib at Ec3 = −3 V vs GE: 20 %.
    - Model gm(g3) vs Raytheon: 20 %.
    - gm(g3) bounds: RMS log < 0.7, median < 35 %, bias −25…0 %.
    - Per-curve RMS log on the #25 fit points: < 0.27.
  - `tests/test_ik_g3.py` (#53) checks that KR = 0, XA = 1 is exactly the #51 model and that the extension is
    bounded and finite. It reruns `refit_53` and the diagnostic from their best starts, and checks that every
    reported #53 metric reproduces and the restart counts hold. It also checks the identifiability findings
    (flat KR profile, KR too large without screen data, sheet-sized with them), the plate μ against the sheet's,
    and the KR/XA SPICE expressions against Python.
