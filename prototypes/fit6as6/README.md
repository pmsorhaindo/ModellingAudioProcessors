# fit6as6: a 6AS6 model fitted to datasheet curves

The lab uses this in place of the EF86 stand-in. Needs Python with `../valvelab/requirements.txt`
(numpy, scipy, matplotlib, pytest) plus Pillow, and `pdftoppm` (poppler-utils). Run from this folder:
```bash
datasheets/fetch_datasheets.sh      # PDFs are not redistributed; fetched and SHA256-checked
mkdir -p plots
python digitise.py && python fit.py && python heldout.py && python emit_spice.py && python -m pytest -q tests
```
The pipeline is deterministic: rerunning it from the fetched PDFs reproduces `data/` and `results/`
byte for byte (checked 5 Nov 2026, after the Raytheon refit). Plots (`plots/`) are regenerated, not committed.
`fit.py` takes about 5 s; CI runs only the tests, from the committed `data/`. `heldout.py` (issue #48) compares
the committed parameters with the Raytheon curves that no fit uses ([Held-out checks](#held-out-checks-issue-48)).

**Current model (issue #25):** the dual-control model below, refitted to the Raytheon 5725/6AS6W 1960
curves (Ec2 60–140 V, Ec1 down to −5 V) with a log-scale error, so the 0.1–0.6 mA region the Culture
Vulture runs in ([research note](../research/culture-vulture-controls.md)) counts. The earlier fit to the
GE sheet (Ec2 = 120 V only) is kept in `results/fit_results.json` as `before`. Summary in
[Refit](#refit-to-the-raytheon-curves-issue-25).

## Sources (`datasheets/`, fetched by `fetch_datasheets.sh`, not committed)
| file | source URL | used for |
|---|---|---|
| `138_5_5725.pdf` | https://frank.pocnet.net/sheets/138/5/5725.pdf (Raytheon 5725/6AS6W, 15 Dec 1960, 13 pp.), SHA256 `6f226c6b…49c8` | **the refit**: pp. 8–11 (six graphs, below); held-out checks (#48): p.8 bottom and the gm graphs on pp. 11–13 |
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
p.8 bottom and the gm graphs on pp. 11–13 were added in issue #48 (next section). Pp. 1–7 are MIL test tables
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
are in separate files because no fit uses them (`fit.py` reads only `raytheon_5725_p8-11.csv`) and the gm file is
in µmho. These graphs are drawn on a 2–3 px grid with a 4–7 px pen. A 3×3 opening leaves parts of the grid, so they
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
  `scipy.optimize.least_squares` (TRF, bounded, several starts). For the refit, 10 of 12 random restarts reach
  the same minimum and none goes lower.
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
- **Fitted parameters** (`dual_control.params`, also in the SPICE file; `before.params` is the GE fit):

| param | MU | EX | KG1 | KP | FMAX | MU3 | V3C | S3 | P3 | RI | KVB |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BEFORE | 25.33 | 1.832 | 1492 | 132.4 | 0.7754 | 98.81 | -9.969 | 2.699 | 9.545 | 99.76 | 8.564 |
| AFTER | 29.92 | 1.509 | 716 | 151.3 | 0.8215 | 225.4 | -6.523 | 2.475 | 3.198 | 63.73 | 17.92 |

- **GE p.1 typical-operation cross-check** (not used in any fit; Eb = Ec2 = 120 V, Ec1 = −2 V, Ec3 = −3 / 0 V;
  gm and rp by central differences):

| | sheet (GE) | BEFORE | AFTER |
|---|---|---|---|
| Ib (mA) | 3.6 / 5.2 | 3.49 / 5.26 | 3.13 / 5.00 |
| Ic2 (mA) | 4.8 / 3.5 | 5.24 / 3.48 | 5.26 / 3.39 |
| gm(g1) (µmho) | 1850 / 3200 | 1773 / 3084 | 1827 / 3217 |
| gm(g3) (µmho) | 810 / 470 | 772 / 399 | 785 / 432 |
| rp (MΩ) | – / 0.11 | 0.11 / 0.16 | 0.16 / 0.16 |

  Raytheon itself reads Ib 3.32 (p.11) / 3.45 (p.10) mA and Ic2 4.94 mA at Ec3 = −3 V, so the AFTER Ib there is
  6–9 % below Raytheon and 13 % below GE. The test tolerance is 15 % for that reason. (The BEFORE values in the
  previous README, e.g. Ib 3.48 / 5.22, did not quite match its own parameters; the table above recomputes them.)

## Refit to the Raytheon curves (issue #25)
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
`heldout.py` writes `results/heldout_checks.json` and `plots/heldout_gm.png`. Nothing is refitted: it takes the
committed `dual_control` (AFTER) parameters from `fit_results.json` and compares them with Raytheon data that no
fit has seen.
- Model gm(g1) = ∂Ib/∂Ec1 and gm(g3) = ∂Ib/∂Ec3 come from central differences (±0.01 V).
- The errors are RMS of ln((model + 10 µmho)/(sheet + 10 µmho)), median |relative error| and mean relative
  error (bias). For currents the offset is 0.02 mA, as in the fit.

**The parameters were not changed.** The gm check alone would not call for a refit. The p.8-bottom check
(below) shows the AFTER model is clearly wrong in one region, g3 well below cut-off at plate voltages away from
120 V. That is reported here for a decision, not fixed.

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
  function. That was out of scope here and has not been done. The model is only reliable for Ec3 ≲ −5 V close to
  Eb = 120 V.

## SPICE
`results/6AS6_fit.inc` defines `.SUBCKT 6AS6_FIT P G2 G3 G1 K` using B-sources (ln, exp, atan, pwr, max) and the
GE datasheet capacitances, with the refit parameters. The pin order and expressions are unchanged, so existing
netlists keep working. A test checks it against the Python model by evaluating the same expression strings over
Ec2 60–140 V. **Checked in ngspice 41 (5 Nov 2026, refit parameters):** `spice_check/check_6as6.cir` DC-sweeps the
subcircuit at the GE conditions (p.3: Ec2 120 V, Ec3 0, Eb 0–240 V, Ec1 0…−4 V; p.4: Eb = Ec2 = 120 V, Ec1 −1…−3 V,
Ec3 −10…+10 V) and the Raytheon p.9 transfer condition (Eb = 120 V, Ec2 = 60 and 140 V, Ec1 −5…0 V).
`spice_check/compare.py` compares Ib and Ic2 with the Python model. Worst deviation over all 20 curves (10 sweeps,
Ib and Ic2): 0.14 µA absolute, 4.6×10⁻⁵ relative (from the 6-significant-figure parameters in the `.inc`). Run: `cd spice_check && mkdir -p out &&
ngspice -b check_6as6.cir && python compare.py` → `spice_check.json`.

## Limitations
- **Valid range (fitted data):** Ec2 60–140 V, Ec1 −5 to 0 V, Ec3 about −9 to +12 V, Eb 20–400 V, currents down to
  about 0.1 mA. Below Eb ≈ 20–40 V (the knee, KVB) the curves bunch together and few points survive. There is no
  data for a positive g1 and no grid-current model. The fitted data vary Ec3 only at Eb = 120 V. Held-out p.8
  bottom shows that for Ec3 ≲ −5 V the model is only valid near that plate voltage (see g3 cut-off below).
- **Low current:** about 10 % median and RMS log 0.18 in 0.1–0.6 mA, with individual curves up to 20–27 % off (see
  above). Below 0.1 mA the model is extrapolation of Koren's exponential tail; the sheets have no usable curves there.
- **g3 cut-off:**
  - Cathode current does not depend on g3 in this model. Ic2 is over-predicted by up to ~40 % for Ec3 ≲ −6 V.
  - The Ib toe near g3 cut-off on p.10 bottom is up to 3× high (at Ec2 = 140 V up to 38 % low).
  - **The cut-off does not move with plate voltage as it should** (p.8 bottom,
    [held-out checks](#held-out-checks-issue-48)). For Ec3 ≲ −5 V, Ib is only right near Eb = 120 V, and at Ec3 ≤ −10 V the model gives almost no plate
    current.
- **Source data:** "average characteristics" from manufacturers' sheets, not a measured valve. Raytheon and GE
  differ by about 10 % (production spread is far larger: Ib 2.5–9 mA, research note). The Raytheon graphs agree
  with each other to 2 % at a few mA but only 9–23 % at 0.4–0.7 mA.
- **Not yet used:**
  - Not digitised: the GE p.4 Ic2-vs-Ec3 plot, the Tung-Sol curves, and the parts of Raytheon p.8 bottom
    listed under issue #48 above.
  - Digitised but in no fit (held-out checks only): Raytheon p.8 bottom and the pp. 11–13 gm curves.
  - Ic3 (g3 current) is digitised, but the model has no g3 current.
- **Tests:** importing `fit.py` in the tests reruns all three fits (about 5 s), which are deterministic. The tests
  check that the committed parameters and per-curve errors reproduce, the error bounds, the low-current
  improvement, the split check and the cathode-current data.
  - `tests/test_raytheon_heldout.py` (#48) checks the new data's point counts and calibration, and p.8 bottom
    against p.10/p.11.
  - It also checks that `heldout_checks.json` reproduces, bounds on the gm and sheet-consistency numbers, and
    the documented cut-off limitation.
