# 6AS6 DK stage — the #102 circuit values, validated against the same-model ngspice references (19 Nov 2026)

`python -m dk6as6.validate` → `results.json` (standard 8× and hq 16× at 0.25 and 0.45 mA; plain 4× at 0.45 mA for
information); `python -m pytest -q dk6as6/tests spice6as6/tests` covers it. **Standard (8×) and hq (16×) pass every
criterion at both currents**, with the 12AX7 stage's criteria (`dk/validate.CRIT`, imported unchanged) against
reference (ii), ngspice running the same valve law. History: #26 validated the same method on placeholder values
(the 12AX7 stage's passives, HT 300 V, Ra 100k, Rk 1.5k bypassed by 22 µF, g3 at 0 V); those are replaced here.

## Circuit values (#102)
Copied from the #102 research pass
(https://github.com/pmsorhaindo/ModellingAudioProcessors/issues/102#issuecomment-7021775895, box lab
`lab/ohm/gyre-102-103`), with its source / inference labels. **No public schematic of the inspiring unit exists, so
every passive value here is inferred**; the topology claims rest on the manuals. Sources: S1 operating manual iss. 5
(2003), S2 Oct 2005 revision, S3 "Notes on principles and operation" (2010), S4 Mastering PLUS manual iss. 4, S5 Super 15
manual iss. 2 (2014), S6 UA plug-in manual, S7 SOS review (2003), S8 GroupDIY threads, S9 6AS6 / 5725 datasheets
(URLs in the issue comment).

| Item | Value in `circuit.VALUES` / `stage.py` | Source / inference (from #102) | Confidence |
|---|---|---|---|
| HT | 200 V | **Inferred.** S9 plate ratings: the plate reaches HT at cut-off, so ≤ 200 V keeps the 5725 inside its rating | low |
| Ra | 270 kΩ (HT/Ra ≈ 0.74 mA) | **Inferred** (E2/E3/E7): the THD minimum lands on the documented 0.25–0.3 mA (S2/S5) only when HT/Ra ≈ 0.6–0.95 mA | low–medium |
| Bias network | unbypassed rheostat cathode → ground, solved for a target cathode current over 0.1–0.6 mA, default 0.45 mA (S6's default) | Topology from S3/S6; "unbypassed" **inferred** from S5's PK modes and E4/E6 | medium (topology), low (values) |
| Ck | **removed** | Inferred (E4, E6) | medium |
| EC2 | 120 V, stiff | S9 typical operation | medium |
| P1 wiring | g3 → cathode (the default and only mode here) | S8 **speculation**; the datasheet's standard pentode connection | low |
| Rg (g1 grid leak) | 1 MΩ | S9 maximum | medium |
| Cin, Cout | 100 nF | **Inferred** (HP corner of a few Hz) | low |
| Rl | 1 MΩ | the next-stage grid; the #102 Zout figures include a 1 MΩ load | — |
| Inter-electrode capacitances | GE ET-T706 p.1: g1 input 4 pF, g3 input 3.4 pF, output 3 pF, g1–p 0.02 pF, g1–g3 0.15 pF (unchanged values) | S9 | high |
| Overdrive "bias range" shift | not modelled | **Unsourced**; flagged for #110 | — |

Not in the #102 table, kept from the 12AX7 stage (our conventional round values, `dk/circuit.py`): the test bench's
source resistance **Rs 1 kΩ** and the grid stopper **Rgs 68 kΩ**. With Ig1 = 0 neither has a DC effect.

Not implemented here: T (g2 + g3 strapped to the plate through 100 Ω) and P2 (g3 → ground hypothesis) are #111;
the knob law and live bias changes are #110 / #115.

**Placement of the g3 input capacitance (a modelling choice, not a value).** The datasheet's 3.4 pF is g3 to all
other electrodes (with the external shield). `fit6as6/results/6AS6_fit.inc` and #26 put it g3 → k. With g3 on the
cathode that is a short, and the cathode is then left with no capacitance to AC ground; ngspice 41's transient at the
#5 tolerances then fails at its first step ("timestep too small", at 5 ns; it runs with any capacitance from k to AC
ground, and with reltol 1e-3). Here it goes g3 → g2, the screen, which is at AC ground like the heater and the shield
the same 3.4 pF also faces. The DK stage uses the same placement. It sits at the cathode, whose small-signal impedance
(Rk ∥ 1/(dIk/dVk), from the valve Jacobian) is 4.8 kΩ at 0.1 mA to 0.9 kΩ at 0.6 mA, so with 3.4 pF its pole is at
9.8–52 MHz.

**Revisit (from the #102 comment, for Michael).** (1) HT/Ra: 250 V / 390k fits the THD-minimum positions slightly
better but exceeds the plate rating at cut-off; 200 V / 300–330k moves the P1 minimum to 0.20–0.21 mA. (2) Pure
rheostat vs a Thevenin (positive-voltage) cathode: S3's wording allows a divider; a stiff Thevenin cathode behaves
like the held cathode (high THD). (3) "P1 = odd harmonics" holds only in a narrow current band with these values.
(4) Overdrive bias shift: no source gives a number. (5) The P2 hypothesis relies on the fit's weak g3 cut-off region.

## What is simulated
`dk6as6/circuit.py`: the values above; screen g2 an ideal source at 120 V; g3 on the cathode (P1); no screen resistor.
**Valve: fit6as6 `dual_control` (#55)**, `model6as6.dual_ip_ig2` with `fit_results.json["dual_control"]`
(KR = 0.2737, XA = 0.5743), read from the fit's own JSON. Ig1 = Ig3 = 0 (no sheet gives grid current); cathode
current = Ip + Ig2. `dk6as6/pentode.py` is a scalar transcription with the analytic Jacobian; the tests check it
against `model6as6` to 1e-12 relative and the Jacobian against central differences.

Operating points (DK; ngspice agrees to ≤ 1.4e-9 relative):

| bias (target Ik) | Rk | Va | Vk = −Vg1k | Vg2k | Vak | Ip | Ig2 | gain at 1 kHz |
|---|---|---|---|---|---|---|---|---|
| 0.25 mA | 19.129 kΩ | 156.988 V | 4.7824 V | 115.218 V | 152.206 V | 0.15930 mA | 0.09070 mA | 15.223 dB |
| 0.45 mA (default) | 9.8729 kΩ | 126.066 V | 4.4428 V | 115.557 V | 121.623 V | 0.27383 mA | 0.17617 mA | 19.869 dB |

Vg3k = 0 exactly (P1). Ik hits its target to 1e-14 (DK) and 1e-9 (ngspice).

## Method (what differs from the 12AX7 stage)
- **Ports.** The valve injects two currents (Ip a→k, Ig2 g2→k) but depends on four voltages (Vg1k, Vg2k, Vg3k, Vak),
  so the DK matrices use separate current and controlling-voltage incidences: K is 4×2 (`circuit.py`).
- **Newton on the currents** (`dk6as6/solver.py`): R(i) = i − i(p − K i), a 2×2 step with dR/di = I + J K. This is
  the same iteration as `dk/solver.py`'s voltage form restricted to v = p − K i, so TOL_V / TOL_F / MAX_IT /
  LS_MAX are the 12AX7 stage's, unchanged, and mean the same thing. Line search as in `dk/solver.py`, same #7
  non-convergence contract. No pnjlim (no grid-current exponential).
- **State as deviation from the operating point** (`stage.py` reset): with the absolute trapezoidal history state, the
  per-sample rounding of large DC entries is a constant error that (I − A)⁻¹ ≈ fs·τ amplifies (#26: 1.2 µV after
  100 ms of silence at hq, from the placeholder's 22 µF bypass cap). Running x − x_dc and i − i_dc makes silence an
  exact fixed point. With the #102 values (7 states: Cin, Cout and the five valve capacitances; no Ck): 1.3e-10 V
  (standard) and 1.7e-10 V (hq) at the output over the first 100 ms of silence.
- **Bias = target cathode current** (`stage.py`): `cathode_resistor()` runs Brent's method on log10 Rk over
  0.5 kΩ–1 MΩ, each step the stage's own DC solve (`operating_point()`, source-stepped). Ik falls monotonically with Rk
  (tested). Converged to ~1e-12 relative in Ik; ~0.01 s per bias. Rk is then fixed for the run (`swap_bias` raises).
- Reused unchanged from `dk/`: the resampler (`dk/resample.py`), Stage's process / latency / iteration_stats
  (subclass), the companion model, CRIT, the measurement code (`analysis.py`, `spice/run_refs.burst_metrics`).

## Reference (ii): `spice6as6/`
`spice6as6/run_refs.py` generates every netlist from `dk6as6/circuit.VALUES`, `circuit.PINS` (P1 wiring) and
`circuit.VALVE_CAPS`, the solved Rk (17 digits) and `spice/run_refs.SOLVER` (the 12AX7's tight #5 settings), with the
same analyses as #26 (.op; .ac 1 Hz–100 kHz + 20 Hz / 20 kHz edges; 1 kHz sines at 0.05 / 0.5 / 2 / 8 V peak for
500 ms; the 4 V burst). References: `reference_dual_0.25mA.json`, `reference_dual_0.45mA.json` (reference (ii)) and
`reference_r51_0.45mA.json` (the #51 fit, for `compare_fits`). Each records its bias and Rk; the tests check that the
DK stage still solves the same Rk (1e-9). Each is one uninterrupted `run_refs.py` run with conda-forge `ngspice=41`
(osx-64 build, under Rosetta on Apple silicon; `NGSPICE=...`). The valve subckts `models/6as6_{dual,r51}.inc` are
generated from `fit_results.json` with the `fit6as6/emit_spice.py` expressions (17-digit parameters and 2/π),
unchanged from #26 apart from the CG3 placement. `spice6as6/tests/test_netlists_6as6.py` checks every value, the
topology and P1 wiring, the solver settings, that the committed files are the generated ones, and **evaluates the
subckt's B-source expressions in Python against the DK valve law**.
There is no reference (i): this repo's sources have no independent 6AS6 SPICE model.

## Results vs criteria (reference (ii), `results.json`)

| criterion (limit) | 0.25 mA standard 8× | 0.25 mA hq 16× | 0.45 mA standard 8× (default) | 0.45 mA hq 16× | 0.45 mA plain 4× (info) |
|---|---|---|---|---|---|
| op point, worst rel. of Va, Vk, Ia, Ig2, Ik, Vgk (< 0.001) | 1.2e-9 | 1.2e-9 | 1.4e-9 | 1.4e-9 | 1.4e-9 |
| small signal 20 Hz–20 kHz, stage alone (< 0.1 dB) | 0.00005 dB | 0.00001 dB | 0.00007 dB | 0.00002 dB | 0.00030 dB |
| small signal, whole chain incl. resampler (< 0.1 dB) | 0.00084 dB at 17.4 kHz | 0.00110 dB | 0.00085 dB at 17.4 kHz | 0.00110 dB | 0.00080 dB |
| H2–H5 worst tested dev, ref > −90 dBc (< 0.1 dB) | 0.00069 dB (H4, 2 V; 9 tested) | 0.00068 dB | 0.00297 dB (H4, 2 V; 10 tested) | 0.00298 dB | 0.00292 dB |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.057 % / 0.0063 % | 0.057 % / 0.0063 % | 0.056 % / 0.0062 % | 0.056 % / 0.0062 % | 0.057 % / 0.0062 % |
| Newton: non-converged samples | 0 | 0 | 0 | 0 | 0 |
| Newton iterations mean / max (8 V sine) | 2.64 / 3 | 2.00 / 3 | 2.68 / 3 | 2.00 / 3 | 2.85 / 4 |
| CPU, pure Python, s per s (informational) | 10.9–12.7 | 19.9–23.5 | 11.5–13.6 | 21.6–23.3 | 5.7–7.8 |

Resampler latency and cost are unchanged from #26 (standard 72 samples / 1.500 ms, 143 / 143 MACs; hq 73 / 1.521 ms,
247 / 247). Untested harmonics (reference below −90 dBc, the numerical floor) still agree to ≤ 0.63 dB (H4 at
0.05 V, −159.7 dBc). At 0.25 mA and 0.05 V no harmonic is above the floor (H2 −95.9 dBc; DK within 0.0025 dB), so
that test case is skipped with that reason rather than passing vacuously.

**Burst recovery τ is not tested** (`validate.py`). The old criterion measured the 22 µF bypass cap recharging
(τ 8.9 ms on the placeholder). With no Ck and no grid current there is no bias-shift memory: the cycle-mean Vgk
moves by −0.235 V (0.25 mA) / −0.257 V (0.45 mA) during the burst and returns with its end, leaving a coupling-cap
tail of ~0.18 mV (0.07 % of the shift, decaying with τ ≈ 0.1 s). ngspice's trace does not take that tail to 1/e within
the 120 ms window (`recovery_tau_s` = NaN in both references), so the ratio is undefined; the DK trace crosses 1/e
at 66–67 ms only because its value at the 30.5 ms reference sample is 0.25 mV against ngspice's 0.18 mV (that sample
straddles the burst's end). The shift and the whole trajectory, which are well defined, agree to 0.057 % and
0.0063 %. The τ check comes back automatically if a reference defines one.

Harmonics of the 1 kHz sines (reference; DK equal to the digits shown), input drive in V peak:

| bias | drive | out H1 | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD % |
|---|---|---|---|---|---|---|---|
| 0.25 mA | 0.05 | 0.288 V | −95.88 | −121.06 | −155.26 | −157.20 | 0.0016 |
| 0.25 mA | 0.5 | 2.884 V | −76.22 | −81.26 | −116.01 | −149.39 | 0.0177 |
| 0.25 mA | 2 | 11.49 V | −72.71 | −56.80 | −79.20 | −98.02 | 0.1468 |
| 0.25 mA | 8 | 40.72 V | −24.75 | −25.29 | −34.70 | −45.73 | 8.170 |
| 0.45 mA | 0.05 | 0.492 V | −67.01 | −115.36 | −159.66 | −161.85 | 0.0446 |
| 0.45 mA | 0.5 | 4.922 V | −47.03 | −75.42 | −111.35 | −140.72 | 0.4456 |
| 0.45 mA | 2 | 19.54 V | −35.27 | −51.09 | −74.50 | −97.68 | 1.746 |
| 0.45 mA | 8 | 64.40 V | −41.16 | −21.45 | −31.83 | −49.41 | 8.903 |

## TIGHT convergence of the references (`run_refs.py --convergence dual` → `spice6as6/convergence_dual_*mA.json`)
Both dual references rerun with `run_refs.TIGHT` (reltol, abstol, vntol, chgtol / 10; time step and max step / 2)
and compared with the committed JSONs; ngspice 41, as the references. This is the 6AS6 record #102 asked for (the
12AX7 study's v3 result, `spice/RESULTS.md`, does not carry over).

| change under TIGHT | 0.25 mA | 0.45 mA |
|---|---|---|
| op point (Va, Vk, Ia, Ig2), relative | 0 (identical to the printed digits) | 0 |
| AC 1 Hz–100 kHz, max | 0 dB | 0 dB |
| H2–H5 with reference above −90 dBc, max | 6.9e-5 dB (H3, 2 V) | 7.6e-4 dB (H4, 2 V, −74.5 dBc) |
| H2–H5 at −90 to −120 dBc, max | 0.0083 dB (H5, 2 V, −98.0 dBc) | 0.080 dB (H4, 0.5 V, −111.4 dBc) |
| burst shift / trajectory, relative | 1.3e-6 / 1.3e-7 | 1.2e-6 / 1.7e-7 |

Below −120 dBc the references are at their numerical floor (up to 4.8 dB at H4 0.05 V, −159.7 dBc), which is why
CRIT tests only above −90 dBc. So above −90 dBc the references are converged to < 1e-3 dB, as for the 12AX7 stage,
and the DK stage's worst tested deviation (0.003 dB) is a method difference, not reference error.

## Sanity check against the #102 research (`python -m dk6as6.bias_sweep` → `bias_sweep.json`)
The research comment's numbers come from an independent static solve of the same valve law (box lab). The DK stage
(standard 8×, full circuit with coupling and valve capacitances), 1 kHz at **3 V peak output**:

| bias mA | Rk | Va | gain 1 kHz | drive for 3 V out | H2 dBc | H3 dBc | THD % |
|---|---|---|---|---|---|---|---|
| 0.1 | 52.77k | 182.36 V | 6.99 dB | 1.344 V | -49.7 | -68.8 | 0.3294 |
| 0.15 | 33.75k | 173.73 V | 10.69 dB | 0.876 V | -56.0 | -74.6 | 0.1604 |
| 0.2 | 24.53k | 165.27 V | 13.27 dB | 0.651 V | -64.5 | -78.3 | 0.0608 |
| 0.225 | 21.52k | 161.11 V | 14.31 dB | 0.578 V | -73.9 | -79.5 | 0.0227 |
| 0.25 | 19.13k | 156.99 V | 15.22 dB | 0.520 V | -75.9 | -80.6 | 0.0185 |
| 0.275 | 17.20k | 152.92 V | 16.03 dB | 0.474 V | -66.1 | -81.4 | 0.0501 |
| 0.3 | 15.60k | 148.90 V | 16.76 dB | 0.436 V | -61.8 | -82.1 | 0.0818 |
| 0.35 | 13.12k | 141.04 V | 18.01 dB | 0.377 V | -56.8 | -83.0 | 0.1439 |
| 0.4 | 11.28k | 133.42 V | 19.03 dB | 0.336 V | -53.7 | -83.6 | 0.2065 |
| 0.45 | 9.87k | 126.07 V | 19.87 dB | 0.305 V | -51.3 | -84.0 | 0.2717 |
| 0.5 | 8.76k | 118.99 V | 20.56 dB | 0.281 V | -49.4 | -84.3 | 0.3408 |
| 0.6 | 7.11k | 105.75 V | 21.58 dB | 0.250 V | -46.1 | -84.9 | 0.4945 |

| research claim (#102) | DK |
|---|---|
| P1 Rk 52.8k at 0.1 mA, 19.1k at 0.25, 15.6k at 0.3, 7.1k at 0.6 | 52.77k, 19.13k, 15.60k, 7.11k (tested to 0.1k) |
| P1 THD minimum around 0.24–0.25 mA | minimum at 0.25 mA (0.0185 %; H2 null between 0.225 and 0.275 mA) |
| THD 0.02–0.2 % at 3 V for 0.25–0.4 mA | 0.0185–0.2065 % |
| small-signal gain +11 to +17 dB at 0.15–0.3 mA | +10.69 to +16.76 dB |
| aliasing, 8× / 48 kHz / 8 V, worst −102 to −110 dBc (P1 0.12–0.45 mA) | −100.96 dBc (0.45 mA) to −107.18 dBc (0.25 mA), below |
| aliasing at 2 V ≤ −130 dBc | −130.37 dBc (0.45 mA), −147.99 dBc (0.25 mA) |

The 0.45 mA aliasing figure is 1.2 dB above the research's −102.2 dBc for the same point (its filter table, "Off").
The DK stage includes the coupling and valve capacitances and the resampler, which the research's static law does
not, so the two are not expected to agree to 0.1 dB.

## Aliasing (`python -m dk6as6.aliasing` → `aliasing.json`; `dk6as6/tests/test_dk6as6_aliasing.py`)
`dk/aliasing.py`'s metric on the 6AS6 stage, standard 8×, FIR, 48 kHz, full sweep (1037–19037 Hz, 1 s windows,
1.25 s settle; the settle is longer than the 12AX7's, see `dk6as6/aliasing.py`). Worst total, every one at 19037 Hz:

| bias | 2 V | 8 V |
|---|---|---|
| 0.25 mA | −147.99 dBc (resampler −148.0, internal −223.2) | −107.18 dBc (internal −108.5, resampler −113.1) |
| 0.45 mA | −130.37 dBc (resampler −130.4, internal −210.6) | −100.96 dBc (internal −101.3, resampler −112.5) |

Limits (`aliasing.LIMIT_DBC`) are these plus `dk.aliasing.MARGIN_DB` (3 dB), rounded up to 0.1 dB, as for the 12AX7
stage. The test runs 19037 Hz in the 0.1 s CI window (within 0.11 dB of the full sweep) at 0.25 mA / 8 V and
0.45 mA / 2 V and 8 V. hq, IIR and 44.1 kHz are not swept for this stage yet.

## #51 vs #55 valve fit: harmonic profile (informational; `python -m dk6as6.compare_fits` → `fit_comparison.json`)
Same circuit, method, quality (standard 8×) and target current (0.45 mA, so Rk is solved per fit: 9.8729 kΩ for #55,
9.8727 kΩ for #51); only the valve parameters change. **Each column passes every criterion against its
own same-model ngspice reference.**

| drive (V peak) | fit | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD % |
|---|---|---|---|---|---|---|
| 0.05 | #51 refit_51 | -69.67 | -117.54 | -157.64 | -160.19 | 0.03284 |
| 0.05 | #55 dual_control | -67.01 | -115.36 | -159.04 | -161.73 | 0.04462 |
| 0.5 | #51 refit_51 | -49.70 | -77.63 | -111.21 | -140.80 | 0.3276 |
| 0.5 | #55 dual_control | -47.03 | -75.42 | -111.32 | -140.87 | 0.4456 |
| 2 | #51 refit_51 | -38.08 | -53.18 | -74.48 | -95.29 | 1.266 |
| 2 | #55 dual_control | -35.27 | -51.09 | -74.50 | -97.70 | 1.746 |
| 8 | #51 refit_51 | -30.86 | -22.39 | -32.31 | -47.70 | 8.493 |
| 8 | #55 dual_control | -41.16 | -21.45 | -31.83 | -49.42 | 8.903 |

Operating point #51 / #55: Va 126.575 / 126.066 V, Vk 4.4427 / 4.4428 V, Ip 0.27194 / 0.27383 mA, Ig2 0.17806 /
0.17617 mA; 1 kHz gain 19.911 / 19.869 dB. The fits differ most in H2 (2.6–2.8 dB at 0.05–2 V, 10.3 dB at 8 V),
which depends on the g3 / plate share; with g3 on the cathode both run at Vg3k = 0, not in the fit's weak g3 cut-off
region. Neither fit has been checked against a measured 6AS6 stage; this table compares the fits, it does not rank
them.
