# Grid current and positive-grid behaviour (12AX7 DK stage → 6AS6 stage)

Status: first pass, 2026-11-04. Every source below was fetched this session unless it's marked
**(not fetched)**. Parameter values are quoted only where the source prints them.

## Sources fetched
- [DZ] Dempwolf & Zölzer, *A physically-motivated triode model for circuit simulations*, DAFx-11.
  https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf
- [CH] Cohen & Hélie, *Measures and parameter estimation of triodes, for the real-time simulation of a
  multi-stage guitar preamplifier*, AES 129th Conv. 2010. https://hal.science/hal-00631747/document
- [VW] Blencowe, *Designing Valve Preamps for Guitar and Bass*, ch.1 (common gain stage) PDF:
  https://www.valvewizard.co.uk/Common_Gain_Stage.pdf (grid-current text on pp. ~20–22 of the PDF)
- [NK] Norman Koren, *Improved vacuum tube models for SPICE simulations*:
  https://www.normankoren.com/Audio/Tubemodspice_article.html
- 12AX7A datasheet scan at frank.pocnet.net: https://frank.pocnet.net/sheets/127/1/12AX7A.pdf
- 6AS6 / 5725 scans (not in repo): https://frank.pocnet.net/sheets/084/6/6AS6.pdf (GE), https://frank.pocnet.net/sheets/127/6/6AS6.pdf (Tung-Sol), https://frank.pocnet.net/sheets/049/5/5725.pdf (RCA 5725); full list in `6as6-datasheets.md`.

## 1. Physics
- **Onset below 0 V.** [VW]: "Forward grid current actually begins to flow even before the grid
  reaches the cathode voltage, around Vgk = –1V, though this varies considerably between valve
  types." Datasheets may quote this as Vgk(max), "the point at which grid current will begin to exceed
  0.3µA, and for the ECC83 this is –0.9V." [DZ §3.3/5.2] measured 12AX7 Ig rising **exponentially**
  near and below 0 V. They attribute this to the initial-velocity current. Above 0 V it rises less steeply,
  following a **space-charge-like power law** `Ig ≈ Gg·Vg^ξ`, the same form as Child–Langmuir but with a
  fitted exponent. (I didn't fetch Langmuir/Child originals, RCA RC-30 or RDH4. tubebooks.org didn't
  respond.)
- **Current division.** [DZ §2]: with the grid positive, "the cathode current divides into anode and grid
  current", modelled as `Ia = Ik − Ig`. Ig depends mostly on Vg and only weakly on Va ([DZ], [CH] Fig. 5).
  [VW]: grid current *increases as anode voltage falls*, because the anode draws fewer electrons away
  from the grid. That's a second-order Va dependence that DZ and CH both ignore.
- **Clipping mechanism.** [VW]: the grid current drops a voltage across any series resistance (source
  impedance or grid stopper), so the grid is hard to push past ~0 V. More series R gives harder
  clipping. [VW] also says ECC81-type valves have more grid current than the ECC83. [CH §2.2]: the
  triode "acts like a rectifier", and this is a cause of plate saturation.
- **Blocking / bias shift.** On each positive peak, the rectified grid current charges the coupling
  capacitor. It then discharges slowly through the grid-leak resistor, so the grid DC level moves
  negative. Koren [NK] notes the effect. [DZ] cite it as the cause of "blocking distortion" and say it
  matters most in cascades. Our ngspice burst already shows it (grid at −3.8 V after a 4 V burst,
  recovering in ~100 ms; see `../fullstage/RESEARCH-numerics.md`).
- **Audible effect.** Asymmetric soft-to-hard clipping on the positive grid swing, which adds even
  harmonics. After heavy transients you get level-dependent gating or "farty" recovery as the stage
  is pushed towards cutoff, then bias recovery with the RC time constant. That last part is our
  inference from the mechanism above plus the ngspice burst, not a quoted listening result.

## 2. Models
### Koren diode + resistor [NK]
Grid-to-cathode diode in series with `RGI`. "No grid current flows during normal operation, when
EG < 0". RGI values are "rough estimates based on measurements of similar tubes in old texts".
Our subckt uses IS=1n, RGI=2k for the 12AX7. **Downsides:** a hard exponential kink (bad for Newton),
no sub-0 V initial-velocity current, and a 1 nA reverse-leakage artefact (+1 mV through 1 MΩ).

### Dempwolf & Zölzer [DZ] eq. (10)–(12), fitted to 3 measured 12AX7s
```
Ik = G·( log(1+exp(C·(Va/µ+Vg))) / C )^γ
Ig = Gg·( log(1+exp(Cg·Vg)) / Cg )^ξ + Ig0
Ia = Ik − Ig
```
Ig0 is added "due to stability reasons". Measured range Va 20–300 V, Vg −5…+3 V. Table 1:

| tube | G | µ | γ | C | Gg | ξ | Cg | Ig0 |
|---|---|---|---|---|---|---|---|---|
| RSD-1 | 2.242e-3 | 103.2 | 1.26 | 3.40 | 6.177e-4 | 1.314 | 9.901 | 8.025e-8 |
| RSD-2 | 2.173e-3 | 100.2 | 1.28 | 3.19 | 5.911e-4 | 1.358 | 11.76 | 4.527e-8 |
| EHX-1 | 1.371e-3 | 86.9 | 1.349 | 4.56 | 3.263e-4 | 1.156 | 11.99 | 3.917e-8 |

Currents are in A (my reading; it's consistent with mA-scale plots). Parasitics from the datasheet:
Cak 0.9 pF, Cgk 2.3 pF, Cag 2.4 pF.
Note that Ig0 is a constant offset, so it gives ~80 nA of grid current at deep negative bias. Through a
1 MΩ grid leak that's ~−0.08 V. Watch for this in the operating point.

### Cohen & Hélie [CH] eq. (2)–(3)
A piecewise function: 0 below `Vγ−Kn`, `(Vgk−Vγ)/Rgk` above `Vγ+Kn`, and a quadratic `aV²+bV+c` blend in
between, with `a=1/(4·Kn·Rgk)`, `b=(Kn−Vγ)/(2·Kn·Rgk)`, `c=−a(Vγ−Kn)²−b(Vγ−Kn)`. (The b numerator
is garbled in the PDF text; `Kn−Vγ` is the value that makes the blend C¹, so it's my reconstruction.)
Table 3 (12AX7): Vγ 0.35/0.18/0.33 V, Rgk 1300/1280/1350 Ω, Kn 0.5/0.49/0.55 for new 1/new 2/aged 3.
It's C¹ but not C² at the joins. It also includes Miller capacitance: they found Cgp × gain (~60) with the grid
resistance gives a low-pass at "5 kHz and more", while the other capacitances can be neglected. The
thesis was **not fetched**.

### Not verified this pass (not fetched, so no claims)
Rutt and Leach SPICE models, Dunkel et al., Mačák thesis grid-current section, uTracer/dos4ever
measured grid-current fits, and any pentode-specific g1-current model. **Pentode partition:** [NK] gives
only screen current `IG2 = (EG+EG2/µ)^1.5/KG2` and says g2 data are scarce. I found no fetched source
for g1/g2/plate division at positive g1.

## 3. Data
- 12AX7A (frank.pocnet.net `127/1/12AX7A.pdf`) is an image scan. I didn't confirm positive-grid curves
  without visual inspection; to do.
- [VW] p.~22 reproduces the ECC83 "average grid current characteristics" (fig. 1.22), so a
  positive-grid curve exists there.
- 6AS6 sheets 084_6 and 127_6, and the 5725 sheet 049_5: image scans. Our fit README already records
  **no positive-g1 or grid-current curves**.
- EF86 at frank.pocnet.net: my guessed URL 404'd and I didn't search further.
- The measured 12AX7 datasets usable for fitting are the DZ (3 tubes) and CH (3 tubes) plots and
  parameter tables only. The raw data isn't published in either PDF.

## 4. Numerical implications
- **Smoothness:** DZ is C^∞ (softplus), so Newton sees continuous Jacobians. CH is C¹ with a curvature
  jump at Vγ±Kn. Koren's diode is exponential with a slope of 1/Vt ≈ 39 /V. That's the stiffest case and
  needs SPICE-style junction limiting.
- **Stiffness at onset:** the grid conductance goes from ~0 to ~1/Rgk (≈0.8 mS) within ~1 V. With a
  high source R (68k stopper, 1M leak) the grid node becomes strongly nonlinear, and the per-sample NR
  steps cluster there. Use previous-sample init, damped steps and an iteration log (as already planned).
- **Extra port:** grid current adds Vgk as a second controlling voltage, so the nonlinearity becomes 2-in
  (Vgk, Vak) and 2-out (Ig, Ia). The NR solve goes from 1×1 to 2×2 per iteration. DZ's Ig doesn't
  depend on Va, so the Jacobian is triangular-ish and the cost is small.
- **WDF:** the grid becomes a second nonlinear port and needs grouping at an R-type root (Werner) or a
  D'Angelo-style triode element.
- **Stateful ADAA:** this is a 2-D nonlinearity, so the tables grow. That confirms the existing plan
  of oversampling first.

## 5. Recommendation
1. **12AX7 DK stage:** use **DZ eq. (10)–(12) with RSD-1 parameters** for both Ik and Ig. It's smooth,
   measured, and the same model family as our plate path. Keep CH (new 1) as an alternative switch for
   comparison. In ngspice, run the same DZ equations as a B-source, so the reference and the DK use the
   same physics. Separately, run Koren's subckt to see how much the model choice matters.
2. **6AS6 stage (assumptions are ours, labelled as such):** keep the fitted Koren-pentode Ik plus our
   split term. Add a g1 diode-like current of DZ form `Ig1 = Gg·(softplus(Cg·Vg1)/Cg)^ξ`, and subtract it from Ik
   *before* the plate/screen split. Start from the 12AX7 RSD-1 ξ and Cg (the shape of onset) and scale Gg
   by a stated factor. For example, scale by the ratio of the 6AS6 to 12AX7 cathode perveance from our fits, on
   the assumption that grid current scales with emission. That's a guess to replace with measurement.
   Expose it as a "grid hardness" parameter rather than presenting it as fact.
3. **Validation:** (a) a static Ig–Vgk sweep in ngspice against the Python model (exact match
   expected); (b) the burst test (4 V, 1 kHz, 10–30 ms, ≥300 ms total). Compare grid DC at burst end
   and at 35/50/119 ms, and the recovery shape. (c) A THD-vs-drive check with and without grid current, to
   show its contribution. (d) Later, uTracer measurement of a real 6AS6 at positive g1, if one is ever available.

## Files
- `grid_current/plot_ig.py`: computes the DZ (3 tubes), CH (3 tubes) and Koren (IS=1n, RGI=2k) curves.
  Run it with Python + numpy + matplotlib; it writes `grid_current/ig_vs_vgk.png` (not committed). Values at
  Vgk = 0 / 0.5 / 1 / 2 V: DZ RSD-1 0.019 / 0.25 / 0.62 / 1.54 mA; CH new 1 0.009 / 0.16 / 0.50 / 1.27 mA;
  Koren ≈0 / 0.10 / 0.34 / 0.82 mA (reverse leakage clipped at 0).
