# 6AS6 DK stage — validation against the same-model ngspice reference (6 Nov 2026, #26)

`python -m dk6as6.validate` → `results.json` ("standard", "hq", and "plain_4x" for information); `python -m pytest -q
dk6as6/tests spice6as6/tests` covers it. **Standard (8×) and hq (16×) pass every criterion**, with the 12AX7 stage's
criteria (`dk/validate.CRIT`, imported unchanged) against reference (ii), ngspice running the same valve law.

## What is simulated
Common-cathode 6AS6 stage, `dk6as6/circuit.py`:
- **Passives: the 12AX7 stage's, unchanged** (`dk/circuit.VALUES`: HT 300 V, Rs 1k, Cin 22n, Rg 1M, Rgs 68k,
  Ra 100k, Rk 1.5k, Ck 22u, Cout 22n, Rl 1M). These are **example values, NOT Culture Vulture values**; sources in
  `dk/circuit.py` (Blencowe, *Designing Valve Preamps for Guitar and Bass*, ch.1, for HT / Ra / Rk / Rg; the rest
  our conventional round values). Same topology.
- **Screen g2 and suppressor g3: ideal sources to ground** at the GE / Raytheon typical conditions used throughout
  fit6as6, **Ec2 = 120 V, Ec3 = 0 V** (GE ET-T706, https://frank.pocnet.net/sheets/084/6/6AS6.pdf; Raytheon
  5725/6AS6W, https://frank.pocnet.net/sheets/138/5/5725.pdf). No screen resistor, no T/P1/P2 switching: the
  Culture Vulture schematic is unknown (`references/schematics.md`).
- **Inter-electrode capacitances: the GE sheet's**, as in `fit6as6/results/6AS6_fit.inc`: Cg1–K 4p, Cg3–K 3.4p,
  Cpk 3p, Cg1–P 0.02p, Cg1–G3 0.15p, in both the DK model and the ngspice subckt.
- **Valve: fit6as6 `dual_control` (#55)**, `model6as6.dual_ip_ig2` with `fit_results.json["dual_control"]`
  (KR = 0.2737, XA = 0.5743), read from the fit's own JSON. Ig1 = Ig3 = 0 (no sheet gives grid current); cathode
  current = Ip + Ig2. `dk6as6/pentode.py` is a scalar transcription with the analytic Jacobian; the tests check it
  against `model6as6` to 1e-12 relative (measured 1.2e-15) and the Jacobian against central differences.

Operating point (DK = ngspice to 3e-9): Va = 185.067 V, Vk = 3.3363 V (so Vg1k = Vg3k = −3.336 V,
Vg2k = 116.66 V, Vak = 181.73 V), Ip = 1.1493 mA, Ig2 = 1.0749 mA. Small-signal gain at 1 kHz 38.887 dB.

## Method (what differs from the 12AX7 stage)
- **Ports.** The valve injects two currents (Ip a→k, Ig2 g2→k) but depends on four voltages (Vg1k, Vg2k, Vg3k, Vak),
  so the DK matrices use separate current and controlling-voltage incidences: K is 4×2 (`circuit.py`).
- **Newton on the currents** (`dk6as6/solver.py`): R(i) = i − i(p − K i), a 2×2 step with dR/di = I + J K. This is
  the same iteration as `dk/solver.py`'s voltage form restricted to v = p − K i, so TOL_V / TOL_F / MAX_IT /
  LS_MAX are the 12AX7 stage's, unchanged, and mean the same thing. Line search as in `dk/solver.py`, same #7
  non-convergence contract. No pnjlim (no grid-current exponential).
- **State as deviation from the operating point** (`stage.py` reset): with the absolute trapezoidal history state, the
  per-sample rounding of the large DC entries (Ck's ≈ 113 A at hq) is a constant error that (I − A)⁻¹ ≈ fs·τ
  amplifies. On silence that gave 1.2 µV at the output after 100 ms at hq (0.14 µV at standard), over the 1 µV
  thump test. Running x − x_dc and i − i_dc makes silence an exact fixed point: 1.3e-10 V at hq, 2.7e-11 V at standard.
  Harmonics changed by ~1e-11 dB.
- Reused unchanged from `dk/`: the resampler (`dk/resample.py`), Stage's process / latency / iteration_stats
  (subclass), the companion model, CRIT, the measurement code (`analysis.py`, `spice/run_refs.burst_metrics`).

## Reference (ii): `spice6as6/`
`spice6as6/run_refs.py` generates every netlist from `dk6as6/circuit.VALUES` and `spice/run_refs.SOLVER` (the
12AX7's tight #5 settings), with the same analyses (.op; .ac 1 Hz–100 kHz + 20 Hz / 20 kHz edges; 1 kHz sines at 0.05 /
0.5 / 2 / 8 V peak for 500 ms; the 4 V burst). The valve subckts `models/6as6_{dual,r51}.inc` are generated from
`fit_results.json` with the `fit6as6/emit_spice.py` expressions, with parameters and 2/π written to 17 digits (the
committed `6AS6_fit.inc` has 6, and 2/π as 0.63661977, 3.7e-9 short). The internal ik0 / ik nodes are inlined.
`spice6as6/tests/test_netlists_6as6.py` checks every value, the topology and the solver settings, and **evaluates
the subckt's B-source expressions in Python against the DK valve law** (1e-12 relative, apart from a documented
ln(1+exp(x)) round-off in deep cut-off). It also checks the committed files are the generated ones.
There is no reference (i): this repo's sources have no independent 6AS6 SPICE model.

**Provenance caveat (reference_dual.json).** The first `run_refs.py dual` run was interrupted after the 0.05 / 0.5 /
2 V sines. The 8 V sine and the burst were then run from generator-produced netlists, one analysis each (same text
apart from the single amplitude in place of the `foreach`). Op and AC were rerun by `run_refs.py` itself. The JSON
was written by `run_refs.analyse()`, and its `"generator"` block was then filled in to what `run()` records. Checked:
`analyse("dual", write=False)` on that wrdata reproduces the committed JSON bit-for-bit (op, ac, sine, burst), and
the rerun op / AC equal the first run's. `reference_r51.json` is one uninterrupted `run_refs.py r51`. The TIGHT
convergence rerun (`run_refs.py --convergence`) has **not** been run for the 6AS6 references yet. The 12AX7 study
(spice/RESULTS.md v3) found these settings converged to < 1e-3 dB above −90 dBc.

## Results vs criteria (reference (ii), `results.json`)

| criterion (limit) | standard 8× (default) | hq 16× | plain 4× (information only) |
|---|---|---|---|
| op point, worst rel. of Va, Vk, Ia, Ig2, Vgk (< 0.001) | 3.1e-9 (Ig2) | 3.1e-9 | 3.1e-9 |
| small signal 20 Hz–20 kHz, stage alone (< 0.1 dB) | 0.00032 dB at 20 kHz | 0.00008 dB at 20 kHz | 0.00134 dB at 20 kHz |
| small signal, whole chain incl. resampler (< 0.1 dB) | 0.00099 dB at 17.4 kHz | 0.00114 dB at 17.4 kHz | 0.00151 dB at 19.1 kHz |
| H2–H5 worst tested dev, ref > −90 dBc (< 0.1 dB; 14 tested) | 0.00061 dB (H5, 0.5 V) | 0.00059 dB (H5, 0.5 V) | 0.00133 dB (H2, 0.5 V) |
| burst shift / τ / trajectory (< 0.5 % / 1 % / 0.5 %) | 0.033 % / 0.059 % / 0.0035 % | 0.033 % / 0.030 % / 0.0036 % | 0.032 % / 0.088 % / 0.0047 % |
| Newton: non-converged samples | 0 | 0 | 0 |
| Newton iterations mean / max (8 V sine) | 1.90 / 3 | 1.71 / 3 | 2.07 / 4 |
| resampler latency, MACs up / down | 72 samples (1.500 ms), 143 / 143 | 73 (1.521 ms), 247 / 247 | 69 (1.438 ms), 91 / 91 |
| CPU, pure Python (informational) | 9.9–10.5 s/s | 18.8–20.4 s/s | 4.9–6.0 s/s |

Untested (reference below −90 dBc): H4 and H5 at 0.05 V (−127.2 / −148.5 dBc; DK within 0.0003 / 0.0027 dB anyway).
THD, reference vs DK, at 0.05 / 0.5 / 2 / 8 V: 0.0702 / 1.481 / 20.519 / 58.156 % in both.
Burst (Vgk cycle mean): rest −3.3363 V, minimum −6.0363 V (ngspice) vs −6.0354 V (DK standard), recovery
τ 8.914 ms vs 8.919 ms. With no grid current the shift is the cathode bypass cap charging (Vk rises), not grid blocking.

**Why plain 4× is so close here.** The 12AX7 stage fails plain 4× by 0.21 dB at 20 kHz. That is trapezoidal warping of the
Miller-multiplied Cgp (2.4 pF × gain). The 6AS6's Cg1–P is 0.02 pF, so the HF roll-off sits far above the band and the warping
error at 20 kHz is 0.0013 dB. Plain 4× passes every criterion above, but **it is not offered as a setting**: its
aliasing (`dk/aliasing.py`, the 12AX7 stage's #6 criterion) has not been assessed for this stage.
Aliasing is not part of this first 6AS6 pass for any setting.

## #51 vs #55 valve fit: harmonic profile (informational; `python -m dk6as6.compare_fits` → `fit_comparison.json`)
Same circuit, method and quality (standard 8×); only the valve parameters change. #51 = `fit_results["refit_51"]`
(no KR / XA, i.e. KR = 0, XA = 1); #55 = `fit_results["dual_control"]` (KR = 0.2737, XA = 0.5743). **Each column is
validated against its own same-model ngspice reference and passes every criterion.** #51: op 2.5e-9, chain
small-signal 0.00099 dB, H2–H5 0.0015 dB, burst 0.032 % / 0.034 % / 0.0034 %. #55: as above. So the differences
below come from the fits, not the method.

| drive (V peak) | fit | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD % |
|---|---|---|---|---|---|---|
| 0.05 | #51 refit_51 | −73.47 | −77.71 | −126.06 | −151.89 | 0.02488 |
| 0.05 | #55 dual_control | −63.29 | −76.15 | −127.16 | −148.52 | 0.07025 |
| 0.5 | #51 refit_51 | −42.58 | −38.07 | −72.54 | −72.48 | 1.454 |
| 0.5 | #55 dual_control | −53.54 | −36.68 | −75.47 | −69.74 | 1.481 |
| 2 | #51 refit_51 | −14.37 | −21.04 | −28.08 | −39.06 | 21.50 |
| 2 | #55 dual_control | −15.29 | −19.79 | −27.68 | −37.24 | 20.52 |
| 8 | #51 refit_51 | −5.20 | −20.16 | −16.59 | −16.12 | 60.46 |
| 8 | #55 dual_control | −5.59 | −23.91 | −15.51 | −16.46 | 58.16 |

Operating point #51 / #55: Va 188.57 / 185.07 V, Vk 3.3466 / 3.3363 V, Ip 1.1143 / 1.1493 mA, Ig2 1.1167 / 1.0749 mA;
1 kHz gain 38.618 / 38.887 dB. The fits differ most at low drive, where H2 depends on the curvature of the g3 / plate
share at Vg3k ≈ −3.3 V (the KR and XA terms of #55). At 0.05 V, H2 is 10.2 dB higher with #55 (THD 2.8×). At 0.5 V
it is 11.0 dB lower, while THD is within 2 %. At 2 V and 8 V the harmonics differ by 0.3–3.7 dB and THD by
at most 4.5 %. Neither fit has been checked against a measured 6AS6 stage; this table compares the fits, it does not rank them.
