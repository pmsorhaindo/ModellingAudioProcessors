# 6AS6 DK stage — the #102 circuit values, the #110 bias control, the #111 T / P1 / P2 modes and the #112 g1 grid current, validated against the same-model ngspice references (21 Nov 2026)

`python -m dk6as6.validate` → `results.json` (standard 8× and hq 16× at 0.1, 0.25, 0.45 and 0.6 mA; plain 4× at
0.45 mA for information); `python -m pytest -q dk6as6/tests spice6as6/tests` covers it (standard at all four currents,
hq at 0.1 and 0.6 mA, for CI time). **Standard (8×) and hq (16×) pass every criterion at all four currents**,
including the starved 0.1 mA, with the 12AX7 stage's criteria (`dk/validate.CRIT`, imported unchanged) against
reference (ii), ngspice running the same valve law. The bias control (#110) is in "Bias control" below; the Distortion switch's T / P1 / P2 wiring (#111) is in
"Modes (#111)" at the end (everything above it is P1, the default, unchanged by #111). History: #26 validated the same method on placeholder values
(the 12AX7 stage's passives, HT 300 V, Ra 100k, Rk 1.5k bypassed by 22 µF, g3 at 0 V); those are replaced here.

> **#104: the C++ port** (`cpp/dk6as6_core.h`, [cpp/README-6as6.md](../cpp/README-6as6.md)) reproduces every result
> file below through `dk6as6.validate` (`cpp/validate_cpp_6as6.py`, all 44 standard / hq settings: every criterion
> reads the same, every supported setting passes), with its own aliasing sweep and guard at 44.1 and 48 kHz.

> **#134: the stage's default resampler is #113's k140** (`stage.FIR = "k140"`, `stage.K140`; `Stage(fir="shipped")`
> keeps the earlier design). The validation records `results*.json` are re-run with it: every supported setting passes
> every criterion at standard and hq, as before. With the shipped FIR, P1 0.6 mA at 2 V and 44.1 kHz was over the
> aliasing gate from 19957 Hz up; with k140 it reads −162.9 dBc there (gate −118.3). The sweeps of the aliasing sections
> below (`aliasing*.json`, `lpf_aliasing.json`, `aliasing_quality.json`, `results_4x.json`, `resampler_study.json`)
> are the shipped-FIR records of #110–#113: the helpers that reproduce them name that design, and they are not re-run.
> The CI guard measures k140 ("Limits, baselines and the CI guard" below). Details:
> [cpp/README-6as6.md](../cpp/README-6as6.md), "Resampler" and "Antialiasing".

> **#112: the default stage now models g1 grid current** (an unvalidated, borrowed Dempwolf–Zölzer law with a
> `grid_hardness` control; "Grid current (#112)" at the end). Every result above "Grid current (#112)" is the stage at
> `grid_hardness=0`, reproduced bit for bit. With grid current P1 is unchanged to rounding, and T is supported to
> 0.5 mA instead of 0.3 mA.
>
> **#131 (part a): bounds on that borrowed law.** DZ Table 1's other two measured 12AX7s (RSD-2, EHX-1), scaled the
> same way, are opt-in bounds (`Stage(grid_shape=...)`, experimental only); the default stays RSD-1, bit for bit.
> "Grid-current bounds (#131)" at the end has how far the T 0.45–0.6 mA results move.
>
> **#113 rework (22 Nov 2026; proposed, Michael's call): inaudible aliasing.** Aliasing on this path is now judged by
> an inaudibility criterion (`dk6as6/inaudible.py`), not the dBc gate (and the CI guard checks it). Standard is 16×
> and hq 32× (k140, #134's default). There is no eco, and T with grid current is supported to 0.35 mA. Details:
> "Inaudible aliasing in every mode and quality (#113 rework)", after the #113 section. Every record in this file was
> made before the rework, at standard 8× / hq 16× and judged against T to 0.5 mA. The aliasing records use the shipped
> FIR, and the validation records `results*.json` use k140 since #134. None is re-run: `stage.record_stage` and
> `QUALITY_RECORDS` reproduce them, and #104's C++ core and its tests still run those factors.

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
| P1 wiring | g3 → cathode (the default mode) | S8 **speculation**; the datasheet's standard pentode connection | low |
| T wiring (#111) | g2 + g3 → plate through Rstrap = 100 Ω, no screen supply | S8 **speculation** plus standard practice (#102) | low |
| P2 wiring (#111) | g3 → ground | **our hypothesis** (#102 E9) | low |
| Rg (g1 grid leak) | 1 MΩ | S9 maximum | medium |
| Cin, Cout | 100 nF | **Inferred** (HP corner of a few Hz) | low |
| Rl | 1 MΩ | the next-stage grid; the #102 Zout figures include a 1 MΩ load | — |
| Inter-electrode capacitances | GE ET-T706 p.1: g1 input 4 pF, g3 input 3.4 pF, output 3 pF, g1–p 0.02 pF, g1–g3 0.15 pF (unchanged values) | S9 | high |
| Overdrive "bias range" shift | not modelled | **Unsourced**; flagged for #110 | — |

Not in the #102 table, kept from the 12AX7 stage (our conventional round values, `dk/circuit.py`): the test bench's
source resistance **Rs 1 kΩ** and the grid stopper **Rgs 68 kΩ**. With Ig1 = 0 neither has a DC effect.

T and P2 are in "Modes (#111)"; live bias and mode changes are #115. The knob law (#110) is below.

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

Operating points at the reference currents (DK; ngspice agrees to ≤ 2.5e-9 relative):

| bias (target Ik) | knob (`bias_knob`) | Rk | Va | Vk = −Vg1k | Vg2k | Vak | Ip | Ig2 | gain at 1 kHz |
|---|---|---|---|---|---|---|---|---|---|
| 0.1 mA (starved; knob end) | 11.00 | 52.768 kΩ | 182.357 V | 5.2768 V | 114.723 V | 177.080 V | 0.06535 mA | 0.03465 mA | 6.986 dB |
| 0.25 mA | 5.89 | 19.129 kΩ | 156.988 V | 4.7824 V | 115.218 V | 152.206 V | 0.15930 mA | 0.09070 mA | 15.223 dB |
| 0.45 mA (default) | 2.61 | 9.8729 kΩ | 126.066 V | 4.4428 V | 115.557 V | 121.623 V | 0.27383 mA | 0.17617 mA | 19.869 dB |
| 0.6 mA (knob end) | 1.00 | 7.1112 kΩ | 105.751 V | 4.2667 V | 115.733 V | 101.484 V | 0.34907 mA | 0.25093 mA | 21.577 dB |

Vg3k = 0 exactly (P1). Ik hits its target to 1e-14 (DK) and 1e-9 (ngspice).

## Bias control (#110)
**What the control is.** The manuals describe one mechanism: the bias pot "varies the current through V2 by changing
the positive voltage on its cathode" and "all of the valve's current is flowing through" it (M-MP / M-S15 §2,
`prototypes/research/culture-vulture-controls.md`; S3 / S6 in #102); the meter reads that current. The #102 research
found that only an **unbypassed** rheostat reproduces the documented 0.2 % THD (its E6), so that is the circuit here:
Rk from cathode to ground, no Ck, Vk = Rk · Ik. The 12AX7-style Rk *scale* is gone (since #118). Since every source
specifies settings by the meter reading, the stage's `bias` parameter is that reading, the target Ik in mA, and Rk is
solved for it (`cathode_resistor`). Rk is fixed per run (`swap_bias` raises; smooth changes are #115).

**Knob law (`stage.knob_bias`, inverse `bias_knob`).** Ik = 0.6 mA · (0.1 / 0.6)^((knob − 1) / 10) on the panel's
1–11 scale. What is sourced and what is ours:

| element | value | basis |
|---|---|---|
| scale | 1–11 | SOS 2003: Drive and Bias "are calibrated from one to 11" |
| direction | reverse acting: advancing the knob lowers Ik | SOS 2003: "As the Bias control is advanced, the amount of current … falls"; UA: clockwise decreases current |
| range | 0.6 mA at 1, 0.1 mA at 11 | **inferred.** The documented range (SOS: ~0.6 mA "warm", < 0.1 mA "starves"; M-ORIG §3) put on the ends of the travel. No source gives the current at either end, and Overdrive's "Bias range" shift (S1/S7) has no number (not modelled) |
| taper | log in Ik (equal knob travel per current ratio) | **our choice**, no source gives a taper. The sources only describe the control by its meter reading, and their named settings sit at roughly equal ratios, crowded at the low end: < 0.1 starved, ~0.1–0.2 frequency doubling (P2), 0.2–0.4 normal, 0.25–0.3 lowest distortion, ~0.5 P2 null, ~0.6 warm. A log taper gives the 0.1–0.3 mA region, where most of those sit, 61 % of the travel; linear in Ik would give it 40 % |
| default | 0.45 mA (`BIAS_MA`, unchanged) | UA plug-in manual, Bias section: default knob 6 = 0.45 mA (https://help.uaudio.com/hc/en-us/articles/33542576197908-Thermionic-Culture-Vulture-Manual) |

**Known disagreement.** The UA plug-in manual (link above) gives the default as knob 6 = 0.45 mA. This law puts 0.45 mA at
knob 2.61 and gives 0.245 mA at knob 6. The obvious alternatives with the same ends miss it too: linear in Ik gives
0.35 mA at knob 6, and linear in Rk (a linear-taper rheostat) 0.167 mA. Matching it would mean shaping a law to that
one point, with end currents that no source gives. (The datum is also in
`prototypes/research/culture-vulture-controls.md`; the PR #120 review re-read the UA page and confirmed it.) So the
knob scale here is ours, not UA's.

**Bias meter (`stage.bias_point(bias_ma)`).** Returns Rk, Vk, Va, Ip, Ig2 and Ik at the DC operating point (the
stage's own solve; tested equal to `Stage.dc`, with Vk = Rk · Ik). Every knob position (`bias_sweep.json` "knobs"; THD
at 3 V peak output, standard 8×; Newton over `validate.py`'s own signals: the 0.05 / 0.5 / 2 / 8 V sines and the 4 V
burst: 0.82 M internal samples at standard and 1.63 M at hq per position):

| knob | Ik | Rk | Vk | Va | Ip | Ig2 | gain 1 kHz | THD at 3 V out | Newton non-conv. std / hq | max iterations std / hq |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 0.6000 mA | 7.11 kΩ | 4.2667 V | 105.75 V | 0.3491 mA | 0.2509 mA | 21.58 dB | 0.4945 % | 0 / 0 | 3 / 3 |
| 2 | 0.5016 mA | 8.73 kΩ | 4.3773 V | 118.77 V | 0.3008 mA | 0.2007 mA | 20.58 dB | 0.3431 % | 0 / 0 | 3 / 3 |
| 3 | 0.4193 mA | 10.70 kΩ | 4.4849 V | 130.55 V | 0.2572 mA | 0.1621 mA | 19.37 dB | 0.2313 % | 0 / 0 | 3 / 3 |
| 4 | 0.3505 mA | 13.09 kΩ | 4.5900 V | 140.96 V | 0.2187 mA | 0.1319 mA | 18.02 dB | 0.1445 % | 0 / 0 | 3 / 3 |
| 5 | 0.2930 mA | 16.02 kΩ | 4.6928 V | 150.02 V | 0.1851 mA | 0.1079 mA | 16.57 dB | 0.0730 % | 0 / 0 | 3 / 3 |
| 6 | 0.2449 mA | 19.57 kΩ | 4.7938 V | 157.82 V | 0.1562 mA | 0.0887 mA | 15.05 dB | 0.0132 % | 0 / 0 | 3 / 3 |
| 7 | 0.2048 mA | 23.90 kΩ | 4.8930 V | 164.47 V | 0.1316 mA | 0.0732 mA | 13.48 dB | 0.0530 % | 0 / 0 | 3 / 3 |
| 8 | 0.1712 mA | 29.16 kΩ | 4.9908 V | 170.13 V | 0.1106 mA | 0.0605 mA | 11.89 dB | 0.1135 % | 0 / 0 | 3 / 3 |
| 9 | 0.1431 mA | 35.55 kΩ | 5.0872 V | 174.92 V | 0.0929 mA | 0.0502 mA | 10.27 dB | 0.1779 % | 0 / 0 | 3 / 3 |
| 10 | 0.1196 mA | 43.32 kΩ | 5.1825 V | 178.96 V | 0.0779 mA | 0.0417 mA | 8.63 dB | 0.2488 % | 0 / 0 | 3 / 3 |
| 11 | 0.1000 mA | 52.77 kΩ | 5.2768 V | 182.36 V | 0.0653 mA | 0.0347 mA | 6.99 dB | 0.3294 % | 0 / 0 | 3 / 3 |

Against the documented range:
- **0.1–0.6 mA is reachable** with the #102 network: Rk 7.11–52.77 kΩ (the research's ~100 kΩ pot class; the solver's
  bracket is 0.5 kΩ–1 MΩ). Vk moves only 4.27–5.28 V over the whole 6:1 current range: with g1 at ground,
  Vg1k = −Vk sits near the valve's cut-off, where Ik is a steep function of Vg1k.
- **Lowest distortion.** At 3 V peak output the THD minimum on a 5 µA grid is **0.0102 % at 0.24 mA** (an H2 null
  between 0.235 and 0.245 mA; H3 −80.2 dBc there). That is just below the documented 0.25–0.3 mA (M-ORIG §2; 0.25 mA in
  M-MP / M-S15). Over 0.25–0.3 mA THD is 0.0185–0.0818 %, rising with current. The #102 comment's 0.24–0.25 mA for P1
  agrees; its HT/Ra choice (inferred) is what places the null.
- The documented spec point (M-MP p.10 / Super 15: 1 kHz, 0.25 mA, Function at **T**, 10 kΩ load, 0.2 % THD) is the
  whole unit's figure in T mode (#111). This stage in P1 at 0.25 mA gives 0.0185 % at 3 V peak out and 0.1468 % at
  2 V in / 11.5 V out (reference table below). Mode, level, load and the other two valves differ, so the two are not
  comparable; this is listed only so neither is read as the other.

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
500 ms; the 4 V burst). References: `reference_dual_{0.1,0.25,0.45,0.6}mA.json` (reference (ii); 0.1 and 0.6 mA
added for #110) and
`reference_r51_0.45mA.json` (the #51 fit, for `compare_fits`). Each records its bias and Rk; the tests check that the
DK stage still solves the same Rk (1e-9). Each is one uninterrupted `run_refs.py` run with conda-forge `ngspice=41`
(osx-64 build, under Rosetta on Apple silicon; `NGSPICE=...`). The valve subckts `models/6as6_{dual,r51}.inc` are
generated from `fit_results.json` with the `fit6as6/emit_spice.py` expressions (17-digit parameters and 2/π),
unchanged from #26 apart from the CG3 placement. `spice6as6/tests/test_netlists_6as6.py` checks every value, the
topology and P1 wiring, the solver settings, that the committed files are the generated ones, and **evaluates the
subckt's B-source expressions in Python against the DK valve law**.

**ngspice at rest at 0.6 mA (slow, not wrong).** The 0.6 mA burst reference took ~2 h against ~1 min at 0.1 mA. The
burst's first 10 ms are exact silence, and at the 0.6 mA operating point ngspice's transient chatters there: 0.1 ms
of silence takes 17 144 accepted and 5 713 rejected timepoints (~27 Newton iterations each) against 208 / 0 at
0.1 and 0.45 mA. The binding test is `vntol` (1e-9 V: with 1e-6 V the same 0.1 ms takes 208 / 0; reltol, abstol and
chgtol × 10 change nothing; TIGHT is no slower). A 0.05 V sine at 0.6 mA (the main analysis) runs normally
(231 / 2), so only the silent stretch before the burst is slow. ngspice accepts only converged timepoints, so the reference is valid at the unchanged #5
settings; it is only slow. We did not find the cause. The one difference we found is that at 0.6 mA
Vak = 101.5 V < 120 V, the only reference with Vak below the fit's `pwr(V(P,K)/120, XA)` normalisation point; the
expressions are smooth there, so that is not an explanation.

There is no reference (i): this repo's sources have no independent 6AS6 SPICE model.

## Results vs criteria (reference (ii), `results.json`)

| criterion (limit) | 0.1 mA std 8× | 0.1 mA hq 16× | 0.25 mA std 8× | 0.25 mA hq 16× | 0.45 mA std 8× (default) | 0.45 mA hq 16× | 0.6 mA std 8× | 0.6 mA hq 16× | 0.45 mA plain 4× (info) |
|---|---|---|---|---|---|---|---|---|---|
| op point, worst rel. of Va, Vk, Ia, Ig2, Ik, Vgk (< 0.001) | 7.6e-10 | 7.6e-10 | 1.2e-9 | 1.2e-9 | 1.4e-9 | 1.4e-9 | 2.5e-9 | 2.5e-9 | 1.4e-9 |
| small signal 20 Hz–20 kHz, stage alone (< 0.1 dB) | 0.000005 dB | 0.000001 dB | 0.00005 dB | 0.00001 dB | 0.00007 dB | 0.00002 dB | 0.00008 dB | 0.00002 dB | 0.00030 dB |
| small signal, whole chain incl. resampler (< 0.1 dB) | 0.00081 dB | 0.00109 dB | 0.00084 dB | 0.00110 dB | 0.00085 dB | 0.00110 dB | 0.00085 dB | 0.00110 dB | 0.00080 dB |
| H2–H5 worst tested dev, ref > −90 dBc (< 0.1 dB) | 0.00214 dB (H4, 2 V; 10 tested) | 0.00213 dB | 0.00069 dB (H4, 2 V; 9 tested) | 0.00068 dB | 0.00297 dB (H4, 2 V; 10 tested) | 0.00298 dB | 0.00350 dB (H4, 2 V; 10 tested) | 0.00351 dB | 0.00292 dB |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.058 % / 0.0065 % | 0.058 % / 0.0065 % | 0.056 % / 0.0063 % | 0.057 % / 0.0063 % | 0.056 % / 0.0062 % | 0.056 % / 0.0062 % | 0.055 % / 0.0062 % | 0.055 % / 0.0062 % | 0.057 % / 0.0062 % |
| Newton: non-converged samples (sines + burst) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Newton iterations mean / max (8 V sine) | 2.48 / 3 | 2.00 / 3 | 2.64 / 3 | 2.00 / 3 | 2.68 / 3 | 2.00 / 3 | 2.68 / 3 | 2.00 / 3 | 2.85 / 4 |
| CPU, pure Python, s per s (informational) | 10.7–13.2 | 19.8–21.9 | 9.7–11.2 | 19.5–19.7 | 9.7–11.4 | 19.3–19.6 | 9.7–11.4 | 19.2–19.4 | 4.8–5.9 |

Resampler latency and cost are unchanged from #26 (standard 72 samples / 1.500 ms, 143 / 143 MACs; hq 73 / 1.521 ms,
247 / 247). The 0.25 and 0.45 mA columns are the #118 results, reproduced exactly by this rerun (everything but CPU
time; the #118 table rounded the 0.25 mA standard shift, 0.0565 %, to 0.057 %). Untested harmonics (reference below
−90 dBc, the numerical floor): from −90 to −120 dBc they agree to ≤ 0.039 dB (H5 at 2 V, 0.6 mA, −102.4 dBc); below
that the reference is at its floor (worst 14.8 dB on H5 at 0.1 mA / 0.5 V, −175.2 dBc; 0.77 dB on H4 at 0.6 mA /
0.05 V, −164.0 dBc). At 0.25 mA and 0.05 V no harmonic is above the floor (H2 −95.9 dBc; DK within 0.0025 dB), so
that test case is skipped with that reason rather than passing vacuously; at 0.1 mA the stage has less feedback
(H2 −78.5 dBc at 0.05 V) and every amplitude is tested.

**Newton across the range.** Zero non-converged samples, at most 3 iterations, on these signals at all four
reference currents (table) and at every knob position 1–11, standard and hq ("Bias control" above). Silence stays a
fixed point at 0.1, 0.45 and 0.6 mA, standard and hq (`test_silence_has_no_startup_thump`, < 1 µV at the output over
the first 100 ms).

**Burst recovery τ is not tested** (`validate.py`). The old criterion measured the 22 µF bypass cap recharging
(τ 8.9 ms on the placeholder). With no Ck and no grid current there is no bias-shift memory: the cycle-mean Vgk
moves by −0.213 V (0.1 mA) / −0.235 V (0.25 mA) / −0.257 V (0.45 mA) / −0.271 V (0.6 mA) during the burst and returns with its end, leaving a coupling-cap
tail of ~0.18 mV (0.07 % of the shift, decaying with τ ≈ 0.1 s). ngspice's trace does not take that tail to 1/e within
the 120 ms window (`recovery_tau_s` = NaN in every reference), so the ratio is undefined; the DK trace crosses 1/e
at 66–67 ms only because its value at the 30.5 ms reference sample is 0.25 mV against ngspice's 0.18 mV (that sample
straddles the burst's end). The shift and the whole trajectory, which are well defined, agree to 0.057 % and
0.0063 %. The τ check comes back automatically if a reference defines one.
*Superseded by #112 (#124): recovery is now compared as the decay constant of that tail, fitted on full-window
cycle-mean samples over 31–118 ms, and agrees to ≤ 0.0053 % at every P1 current ("Grid current (#112)", "Burst
recovery measure").*

Harmonics of the 1 kHz sines (reference; DK equal to the digits shown), input drive in V peak:

| bias | drive | out H1 | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD % |
|---|---|---|---|---|---|---|---|
| 0.1 mA | 0.05 | 0.1118 V | −78.53 | −125.70 | −154.16 | −156.08 | 0.0118 |
| 0.1 mA | 0.5 | 1.117 V | −58.50 | −86.11 | −118.87 | −175.17 | 0.1190 |
| 0.1 mA | 2 | 4.460 V | −45.94 | −61.61 | −82.22 | −101.79 | 0.5117 |
| 0.1 mA | 8 | 16.54 V | −23.94 | −29.02 | −36.78 | −45.52 | 7.435 |
| 0.25 mA | 0.05 | 0.288 V | −95.88 | −121.06 | −155.26 | −157.20 | 0.0016 |
| 0.25 mA | 0.5 | 2.884 V | −76.22 | −81.26 | −116.01 | −149.39 | 0.0177 |
| 0.25 mA | 2 | 11.49 V | −72.71 | −56.80 | −79.20 | −98.02 | 0.1468 |
| 0.25 mA | 8 | 40.72 V | −24.75 | −25.29 | −34.70 | −45.73 | 8.170 |
| 0.45 mA | 0.05 | 0.492 V | −67.01 | −115.36 | −159.66 | −161.85 | 0.0446 |
| 0.45 mA | 0.5 | 4.922 V | −47.03 | −75.42 | −111.35 | −140.72 | 0.4456 |
| 0.45 mA | 2 | 19.54 V | −35.27 | −51.09 | −74.50 | −97.68 | 1.746 |
| 0.45 mA | 8 | 64.40 V | −41.16 | −21.45 | −31.83 | −49.41 | 8.903 |
| 0.6 mA | 0.05 | 0.5996 V | −60.10 | −112.89 | −164.03 | −177.22 | 0.0989 |
| 0.6 mA | 0.5 | 5.992 V | −40.12 | −72.90 | −104.14 | −137.46 | 0.9869 |
| 0.6 mA | 2 | 23.74 V | −28.35 | −48.65 | −67.73 | −102.41 | 3.841 |
| 0.6 mA | 8 | 74.99 V | −27.54 | −19.62 | −28.54 | −56.03 | 11.88 |

## TIGHT convergence of the references (`run_refs.py --convergence dual` → `spice6as6/convergence_dual_*mA.json`)
The dual references rerun with `run_refs.TIGHT` (reltol, abstol, vntol, chgtol / 10; time step and max step / 2)
and compared with the committed JSONs; ngspice 41, as the references. This is the 6AS6 record #102 asked for (the
12AX7 study's v3 result, `spice/RESULTS.md`, does not carry over).

| change under TIGHT | 0.1 mA | 0.25 mA | 0.45 mA | 0.6 mA |
|---|---|---|---|---|
| op point (Va, Vk, Ia, Ig2), relative | 0 | 0 (identical to the printed digits) | 0 | 0 |
| AC 1 Hz–100 kHz, max | 0 dB | 0 dB | 0 dB | 0 dB |
| H2–H5 with reference above −90 dBc, max | 1.1e-3 dB (H4, 2 V, −82.2 dBc) | 5.5e-4 dB (H4, 2 V, −79.2 dBc) | 7.6e-4 dB (H4, 2 V, −74.5 dBc) | 2.5e-4 dB (H4, 2 V, −67.7 dBc) |
| H2–H5 at −90 to −120 dBc, max | 0.044 dB (H4, 0.5 V, −118.9 dBc) | 0.0083 dB (H5, 2 V, −98.0 dBc) | 0.080 dB (H4, 0.5 V, −111.4 dBc) | 0.069 dB (H4, 0.5 V, −104.1 dBc) |
| burst shift / trajectory, relative | 1.6e-6 / 2.0e-7 | 1.3e-6 / 1.3e-7 | 1.2e-6 / 1.7e-7 | 9.4e-7 / 1.4e-7 |

Below −120 dBc the references are at their numerical floor (up to 5.5 dB: H5 at 0.05 V, 0.1 mA, −156.1 dBc), which is why
CRIT tests only above −90 dBc. So above −90 dBc the references are converged to ≤ 1.1e-3 dB (0.1 mA, H4 at 2 V; the
others < 1e-3 dB, as for the 12AX7 stage), 1 % of the 0.1 dB criterion, and the DK stage's worst tested deviation
(0.0035 dB) is a method difference, not reference error.

## Sanity check against the #102 research (`python -m dk6as6.bias_sweep` → `bias_sweep.json`)
The finer 0.2–0.3 mA rows are all in `bias_sweep.json`; three are shown. The research comment's numbers come from an independent static solve of the same valve law (box lab). The DK stage
(standard 8×, full circuit with coupling and valve capacitances), 1 kHz at **3 V peak output**:

| bias mA | Rk | Va | gain 1 kHz | drive for 3 V out | H2 dBc | H3 dBc | THD % |
|---|---|---|---|---|---|---|---|
| 0.1 | 52.77k | 182.36 V | 6.99 dB | 1.344 V | -49.7 | -68.8 | 0.3294 |
| 0.15 | 33.75k | 173.73 V | 10.69 dB | 0.876 V | -56.0 | -74.6 | 0.1604 |
| 0.2 | 24.53k | 165.27 V | 13.27 dB | 0.651 V | -64.5 | -78.3 | 0.0608 |
| 0.225 | 21.52k | 161.11 V | 14.31 dB | 0.578 V | -73.9 | -79.5 | 0.0227 |
| 0.235 | 20.50k | 159.45 V | 14.69 dB | 0.553 V | -84.7 | -80.0 | 0.0116 |
| 0.24 | 20.02k | 158.63 V | 14.87 dB | 0.542 V | -90.5 | -80.2 | 0.0102 |
| 0.245 | 19.57k | 157.81 V | 15.05 dB | 0.531 V | -80.7 | -80.4 | 0.0133 |
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
| P1 THD minimum around 0.24–0.25 mA | minimum at 0.24 mA (0.0102 %) on a 5 µA grid over 0.2–0.3 mA (`bias_sweep.json`; the #118 table's 12-point grid put it at 0.25 mA, 0.0185 %) |
| THD 0.02–0.2 % at 3 V for 0.25–0.4 mA | 0.0185–0.2065 % |
| small-signal gain +11 to +17 dB at 0.15–0.3 mA | +10.69 to +16.76 dB |
| aliasing, 8× / 48 kHz / 8 V, worst −102 to −110 dBc (P1 0.12–0.45 mA) | −100.96 dBc (0.45 mA), −107.18 dBc (0.25 mA), −112.50 dBc (0.1 mA); −98.44 dBc at 0.6 mA, outside the research's range |
| aliasing at 2 V ≤ −130 dBc | −130.37 dBc (0.45 mA), −140.31 (0.1 mA), −147.99 dBc (0.25 mA); −123.59 dBc at 0.6 mA, outside the research's range |

The 0.45 mA aliasing figure is 1.2 dB above the research's −102.2 dBc for the same point (its filter table, "Off").
The DK stage includes the coupling and valve capacitances and the resampler, which the research's static law does
not, so the two are not expected to agree to 0.1 dB.

## Aliasing (`python -m dk6as6.aliasing` → `aliasing.json`; `dk6as6/tests/test_dk6as6_aliasing.py`)
`dk/aliasing.py`'s metric on the 6AS6 stage, standard 8×, FIR, 48 kHz, full sweep (1037–19037 Hz, 1 s windows,
1.25 s settle; the settle is longer than the 12AX7's, see `dk6as6/aliasing.py`). Worst total, every one at 19037 Hz:

| bias | 2 V | 8 V |
|---|---|---|
| 0.1 mA | −140.31 dBc (resampler −140.3, internal −217.8) | −112.50 dBc (internal −118.3, resampler −113.8) |
| 0.25 mA | −147.99 dBc (resampler −148.0, internal −223.2) | −107.18 dBc (internal −108.5, resampler −113.1) |
| 0.45 mA | −130.37 dBc (resampler −130.4, internal −210.6) | −100.96 dBc (internal −101.3, resampler −112.5) |
| 0.6 mA | −123.59 dBc (resampler −123.6, internal −205.3) | −98.44 dBc (internal −98.7, resampler −111.5) |

The worst point of the range is 0.6 mA (knob 1) at 8 V, −98.44 dBc: the least cathode feedback and the most
distortion (11.9 % THD). At 2 V every current is at the resampler's floor; internal (stage) aliasing is ≤ −205 dBc.
The 0.25 and 0.45 mA rows reproduce the #118 sweep exactly (all 76 records).

Limits (`aliasing.LIMIT_DBC`) are these plus `dk.aliasing.MARGIN_DB` (3 dB), rounded up to 0.1 dB, as for the 12AX7
stage. The test runs 19037 Hz in the 0.1 s CI window (within 0.11 dB of the full sweep) at 0.25 mA / 8 V,
0.45 mA / 2 V and 8 V, and 0.6 mA / 8 V (−98.45 dBc in the CI window). 4×, hq, 44.1 kHz, the post-filter on and the
resampler's stopband are in "Aliasing at every quality … (#113)" below (IIR is not offered, so not swept).

## Aliasing at every quality, the resampler's stopband, and the eco / quality decision (#113, 21 Nov 2026)
On the settled circuit (#102 values, #110 bias, #111 modes, #103 post-filter) and, since #112 (PR #128), the production
default stage with g1 grid current; revised after both PR #129 reviews. Files: `python -m dk6as6.aliasing_quality` →
`aliasing_quality.json` (the sweeps, the band-edge scan, the settle check, the model check), `python -m
dk6as6.validate_4x` → `results_4x.json`, `python -m dk6as6.resampler_study` → `resampler_study.json` (with
`resampler_cost.cpp`), `python -m dk6as6.aliasing_guard` (the CI guard; `aliasing_baseline.json`,
`aliasing_baseline_44100.json`). Tests: `dk6as6/tests/test_dk6as6_aliasing_quality.py` and
`test_dk6as6_aliasing_guard.py` (they read the committed results; the measurements take hours).
**Reproduce:** `python -m dk6as6.aliasing_quality reproduce [--workers N]` runs every measurement behind
`aliasing_quality.json` (`aliasing_quality.REPRODUCE`, 32 runs printed as they start; resumable, with separate JSONLs per
method and a resume key that includes the model, settle and window) and collects; `test_reproduce_plans_exactly…` checks
that it plans exactly the committed points.

### Which model (since #112)
The default `Stage` now has g1 grid current (`pentode.HARDNESS`), and T is supported to 0.5 mA with it (0.3 mA without).
Every record carries its `grid_hardness`:
- the cases supported without grid current (P1 0.1–0.6 mA, T 0.1–0.3 mA) were swept on the no-grid stage; the default
  stage reads the same at all 48 compared points (every factor and rate, P1 0.6 mA and T 0.3 mA, 2 and 8 V, 19037 /
  19937 Hz) to ≤ 4.4e-4 dB (largest at 4×, T 0.3 mA, 8 V), so these sweeps stand for the default stage;
- T 0.35 / 0.45 / 0.5 mA, supported only with grid current, are swept on the default stage (full grid, every factor
  and rate); at 8× / 48 kHz they reproduce #112's own sweeps (`aliasing_grid_T.json`) to ≤ 3.2e-6 dB at 86 points;
- the guard measures the default stage (`aliasing_guard.GRID_HARDNESS`, recorded in the baselines).

### Decision (the numbers are below)
1. **Eco: Gyre does not offer an eco setting on the 6AS6 path (recommendation).** Plain 4× is accurate for the stage
   (every `dk/validate.CRIT` criterion at all seven references, worst small-signal error 0.00091 dB), and all its maxima
   are inside the 12AX7 eco budget (by ≥ 12.9 dB, band-edge scan included). But it raises the worst 8 V total over standard by 37.5 / 34.8 dB
   for P1 0.6 mA and 19.6 / 16.6 dB for T 0.5 mA with grid current (48 / 44.1 kHz); with grid current T 0.5 mA at 4× is
   **above the 12AX7 standard budget at both drives** (8 V −43.9 / −43.3 dBc against −48.6 / −46.8; 2 V −105.2 /
   −100.8 against −118.3); the post-filter at 4× misses its own discretisation tolerance (0.265 dB / 1.19° against
   `test_lpf.py`'s 0.15 dB / 0.35°); and what it buys is about half the stage's CPU (the 12AX7 C++ core: 0.53–0.56×).
2. **Standard stays 8× with no pre-warp.** 0.00085 dB worst small-signal error (`results.json`), and even 4× leaves only
   0.00091 dB, so a pre-warp has nothing to correct; no 6AS6 pre-warp was derived and the 12AX7's `PREWARP_HZ` is not
   used. Worst totals: 8 V −63.5 / −59.9 dBc (T 0.5 mA, grid current; 14.9 / 13.1 dB inside the gate), 2 V −123.6 /
   −119.2 dBc on the grid (P1 0.6 mA) and −120.5 / −117.1 dBc in the band-edge scan (44.1 kHz: 1.2 dB **above** the
   −118.3 dBc gate at 19987 Hz).
3. **hq stays 16×.** For the no-grid cases it reads the 64× ground truth to ≤ 0.014 dB at all 14 ground-truth points:
   the resampler's floor (8 V worst −111.5 / −105.4 dBc on the grid, −108.0 / −104.2 in the scan; internal share ≤
   −148.4 dBc). With grid current, T 0.5 mA at 8 V is still internal folding at 16× (−84.9 / −80.5 dBc, 21.9 / 17.5 dB
   inside the hq gate) and **23.5–30.4 dB above its 64× ground truth** (−104.7 to −116.5 dBc): 16× does not remove the
   grid-current law's folding (Michael's call, below). T 0.35 mA with grid current at 16× is the resampler's floor
   instead (−118.9 / −113.4 dBc, internal 28.7 / 25.0 dB below the resampler share).
4. **FIR resampler: k140 is the preferred candidate among the designs tested, for the conditions measured
   (recommendation, not implemented here).** Kaiser β = 14.47; first stage 123 taps at 48 kHz, 219 at 44.1 kHz; later
   stages 47 taps; ≥ 140.4 dB one-pass stopband against the shipped 89.3–94.2 dB.
   - The 108 supported-mode checkpoints (P1 0.1 / 0.6 mA, T 0.3 mA; 2 and 8 V; 16037 / 19037 / 19937 Hz; every factor,
     both rates; no-grid cases): never more than 0.005 dB above the shipped FIR; every 2 V point 20.7–56.5 dB lower;
     at 8 V 0–0.3 dB at 4×, 0–11.2 dB (P1) and 36.1–54.3 dB (T) at 8×, 35.9–56.1 dB at 16×.
   - With the shipped FIR, P1 0.6 mA (supported) at 2 V and 44.1 kHz reads **−117.1 dBc at 19987 Hz, above the
     −118.3 dBc gate at every factor** (measured); with k140 that point reads −163.7 dBc. Nothing here bounds the tones
     between the measured ones ("The band edge").
   - With grid current, the 8 V maxima of T 0.45–0.5 mA are internal folding at every factor (no resampler changes
     those), and so are T 0.35 mA's at 4× and 8×; T 0.35 mA at 16× (8 V) and every 2 V maximum at 8× and 16× are the
     resampler's share (at the 2 V maxima the internal share is ≥ 33.2 dB under the total; at other tones it comes
     within 5.1 dB, T 0.5 mA, 8×, 44.1 kHz, 19837 Hz, −150.6 dBc). k140 was not measured on the grid-current cases,
     so **its effect there remains open**.
   - P2 (current #55 fit, experimental): with k140 its full 2 V grid is inside the gate at both rates (−129.5 / −131.2
     dBc, from −89.0 / −91.7); its two measured 8 V points read −59.6 to −63.8 dBc at 8× and −106.2 to −116.6 dBc at 16×.
     Whether aliasing still keeps P2 experimental must be re-checked at both drives on the #123 refit.
   - Choice: the lowest-latency derived candidate with which every measured supported point and every measured P2 2 V
     point meets the gate with at least `dk.aliasing.MARGIN_DB` (3 dB), at both rates (written after the first k120 /
     k140 subset results, before the full P2 sweeps and the every-factor checkpoints). k120 fails it (P2 0.45 mA 2 V
     −109.9 dBc at 48 kHz); the review's k130 spot checks read −119.67 / −120.29 dBc (reviewer's measurement), so k130
     would fail it too. Implementing it changes every committed 6AS6 result and belongs with the C++ port (follow-up);
     the baselines and the guard are for the **shipped** resampler.

### Method
- `dk/aliasing.py`'s metric (coherent periodic-Hann 1 s window, total non-harmonic energy 20 Hz–20 kHz in dBc) after
  `dk6as6.aliasing.SETTLE_S` (1.25 s). The **grid** is the #6 tones (1037…19037 Hz) and a band-edge tone, 19937 Hz:
  the interpolator's image of a tone f is at fs − f, which nears the FIR's stopband edge as f nears 20 kHz. A
  **band-edge scan** (19237, 19437, 19637, 19831, 19837, 19987 Hz) covers the guard's cases (P1 0.6 mA, T 0.5 mA with
  grid current, T 0.3 mA at 8 V) at every factor, rate and drive.
- **Every case, setting, rate and drive is on the full 20-tone grid** (4×, 8×, 16×); 64× is the ground truth at 18
  points. P1 8× no-grid is `lpf_aliasing.json` (#119, the same function: 304 records reused; `verify-reuse` re-measures
  six bit for bit at both rates).
- The internal / resampler / other split is an attribution diagnostic (each bin credited to the lowest folding order,
  before the spectrum is looked at; the first review counted ~96 % of the band's bins given a harmonic origin at
  48 kHz, 16037 Hz, 1 s); the measured total is the evidence. "other" reads ≤ −184.1 dBc for the no-grid cases and ≤ −178.2 dBc for
  grid-current T; three of the
  deepest floors re-measured after 2.5 s instead of 1.25 s move by < 0.1 dB (`settle_check`).
- Each point is one stage run, decimated directly ("off") and through each post-filter position (`lpf_aliasing.measure`,
  which takes `quality`, `mode`, FIR designs and `grid_hardness`); "abs" is relative to the **unfiltered** fundamental.
- Gate (`aliasing.QUALITY_GATE_DBC`, PR #121's rule): the shipped 12AX7 stage's committed budget for the same setting,
  FIR and rate; 4× against the 12AX7's eco and, for the decision, its standard. An engineering budget measured on the
  #6 grid, not an audibility threshold.

### Plain 4× against the references (`results_4x.json`)
Every case passes every criterion (op, small-signal stage and chain, harmonics, burst, Newton):

| case | small-signal stage / chain, dB (CRIT 0.1) | worst tested harmonic, dB (CRIT 0.1) |
|---|---|---|
| P1 0.1 mA | 0.00002 / 0.00065 | 0.00219 |
| P1 0.25 mA | 0.00023 / 0.00076 | 0.00074 |
| P1 0.45 mA | 0.00030 / 0.00080 | 0.00292 |
| P1 0.6 mA | 0.00032 / 0.00082 | 0.00345 |
| T 0.1 mA | 0.00040 / 0.00041 | 0.00047 |
| T 0.25 mA | 0.00022 / 0.00076 | 0.00042 |
| T 0.35 mA | 0.00049 / 0.00091 | 0.00187 |

(T 0.1 / 0.25 / 0.35 mA bracket T's supported 0.1–0.3 mA, as for standard and hq.) The post-filter is a separate
question: its TPT SVF is pre-warped at `lpf.PREWARP_HZ` (15.7 kHz), the minimax for 8× rates. Against the analog
prototype, 20 Hz–20 kHz, its worst error is 0.0456–0.0638 dB at 8×, 0.0113–0.0158 dB at 16× and **0.188–0.265 dB /
0.89–1.19°** at 4× (both positions, both rates), above `test_lpf.py`'s 0.15 dB / 0.35°. `lpf.prewarp_minimax` over the
4× rates returns 15 696 Hz (−30.1 dB complex error), the same frequency, so re-choosing the pre-warp cannot fix it.

### Aliasing per setting and rate (`aliasing_quality.json`)
**48 kHz, 2 V**: worst over the 20-tone grid (the #6 tones and 19937 Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered fundamental]; a tone count is shown only where the grid is not complete
| case | 4x | standard | hq |
|---|---|---|---|
| P1 0.1 mA | -140.3 (19037) [-153.8 / -147.9] | -140.3 (19037) [-154.0 / -148.0] | -140.3 (19037) [-154.0 / -148.0] |
| P1 0.25 mA | -147.9 (19037) [-153.0 / -153.0] | -148.0 (19037) [-153.0 / -153.0] | -148.0 (19037) [-153.0 / -153.0] |
| P1 0.45 mA | -130.4 (19037) [-143.9 / -138.0] | -130.4 (19037) [-144.0 / -138.1] | -130.4 (19037) [-144.0 / -138.1] |
| P1 0.6 mA | -123.6 (19037) [-137.2 / -131.2] | -123.6 (19037) [-137.3 / -131.3] | -123.6 (19037) [-137.3 / -131.3] |
| T 0.1 mA | -140.7 (19037) [-154.3 / -148.3] | -140.7 (19037) [-154.4 / -148.5] | -140.7 (19037) [-154.5 / -148.5] |
| T 0.25 mA | -136.5 (19037) [-150.1 / -144.2] | -136.5 (19037) [-150.3 / -144.3] | -136.6 (19037) [-150.3 / -144.3] |
| T 0.3 mA | -135.0 (19037) [-148.6 / -142.7] | -135.0 (19037) [-148.7 / -142.8] | -135.0 (19037) [-148.8 / -142.8] |
| T 0.35 mA (grid current) | -133.3 (19037) [-146.9 / -141.0] | -133.3 (19037) [-147.1 / -141.1] | -133.4 (19037) [-147.1 / -141.1] |
| T 0.45 mA (grid current) | -129.3 (19037) [-142.9 / -137.0] | -129.3 (19037) [-143.0 / -137.1] | -129.3 (19037) [-143.1 / -137.1] |
| T 0.5 mA (grid current) | -105.2 (19937) [-112.3 / -111.5] | -126.8 (19037) [-140.5 / -134.6] | -126.8 (19037) [-140.6 / -134.6] |

**48 kHz, 8 V**: worst over the 20-tone grid (the #6 tones and 19937 Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered fundamental]; a tone count is shown only where the grid is not complete
| case | 4x | standard | hq |
|---|---|---|---|
| P1 0.1 mA | -60.6 (19937) [-74.7 / -69.5] | -109.0 (19937) [-117.1 / -113.2] | -113.8 (19037) [-123.7 / -121.3] |
| P1 0.25 mA | -61.0 (19937) [-76.7 / -70.4] | -103.0 (19937) [-110.6 / -106.8] | -113.1 (19037) [-120.9 / -120.5] |
| P1 0.45 mA | -61.9 (19937) [-71.4 / -68.5] | -99.4 (19937) [-106.8 / -103.2] | -112.5 (19037) [-117.6 / -117.6] |
| P1 0.6 mA | -59.5 (18037) [-66.2 / -65.2] | -97.0 (19937) [-103.8 / -100.7] | -111.5 (19037) [-115.9 / -115.9] |
| T 0.1 mA | -103.4 (19937) [-116.6 / -112.6] | -125.3 (19037) [-138.7 / -132.9] | -125.3 (19037) [-138.8 / -133.0] |
| T 0.25 mA | -96.2 (19937) [-106.7 / -105.2] | -121.1 (19037) [-134.6 / -128.8] | -121.1 (19037) [-134.6 / -128.8] |
| T 0.3 mA | -95.0 (19937) [-104.5 / -103.7] | -119.6 (19037) [-133.2 / -127.3] | -119.6 (19037) [-133.2 / -127.4] |
| T 0.35 mA (grid current) | -60.9 (19937) [-66.3 / -65.5] | -94.2 (19937) [-101.2 / -98.8] | -118.9 (19037) [-126.6 / -125.2] |
| T 0.45 mA (grid current) | -47.1 (19937) [-51.2 / -50.4] | -64.7 (19937) [-71.7 / -69.4] | -89.9 (19937) [-103.4 / -97.5] |
| T 0.5 mA (grid current) | -43.9 (19937) [-49.2 / -48.4] | -63.5 (19937) [-71.4 / -67.6] | -84.9 (19037) [-93.6 / -90.3] |

**44.1 kHz, 2 V**: worst over the 20-tone grid (the #6 tones and 19937 Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered fundamental]; a tone count is shown only where the grid is not complete
| case | 4x | standard | hq |
|---|---|---|---|
| P1 0.1 mA | -135.9 (19937) [-141.5 / -138.5] | -136.0 (19937) [-141.7 / -138.6] | -136.0 (19937) [-141.7 / -138.7] |
| P1 0.25 mA | -143.2 (19937) [-156.9 / -154.6] | -143.7 (19937) [-156.9 / -155.2] | -143.7 (19937) [-156.9 / -155.3] |
| P1 0.45 mA | -126.0 (19937) [-131.6 / -128.6] | -126.0 (19937) [-131.7 / -128.7] | -126.0 (19937) [-131.7 / -128.7] |
| P1 0.6 mA | -119.2 (19937) [-124.6 / -121.7] | -119.2 (19937) [-124.7 / -121.7] | -119.2 (19937) [-124.8 / -121.8] |
| T 0.1 mA | -136.4 (19937) [-141.7 / -138.8] | -136.5 (19937) [-141.9 / -138.9] | -136.5 (19937) [-142.0 / -138.9] |
| T 0.25 mA | -132.2 (19937) [-137.5 / -134.6] | -132.2 (19937) [-137.7 / -134.7] | -132.2 (19937) [-137.7 / -134.7] |
| T 0.3 mA | -130.7 (19937) [-136.0 / -133.0] | -130.7 (19937) [-136.1 / -133.1] | -130.7 (19937) [-136.2 / -133.1] |
| T 0.35 mA (grid current) | -129.0 (19937) [-134.3 / -131.4] | -129.0 (19937) [-134.4 / -131.4] | -129.0 (19937) [-134.5 / -131.5] |
| T 0.45 mA (grid current) | -124.9 (19937) [-130.3 / -127.3] | -124.9 (19937) [-130.4 / -127.4] | -124.9 (19937) [-130.4 / -127.4] |
| T 0.5 mA (grid current) | -100.8 (19937) [-109.5 / -107.0] | -122.5 (19937) [-127.9 / -124.9] | -122.5 (19937) [-128.0 / -124.9] |

**44.1 kHz, 8 V**: worst over the 20-tone grid (the #6 tones and 19937 Hz), dBc, filter off (tone); [4k / 7k on: worst dB re the unfiltered fundamental]; a tone count is shown only where the grid is not complete
| case | 4x | standard | hq |
|---|---|---|---|
| P1 0.1 mA | -61.3 (18037) [-71.3 / -67.7] | -99.1 (19937) [-111.0 / -106.4] | -108.4 (19937) [-116.2 / -112.8] |
| P1 0.25 mA | -60.1 (19937) [-67.3 / -65.1] | -93.1 (19937) [-107.0 / -101.7] | -107.8 (19937) [-116.5 / -113.0] |
| P1 0.45 mA | -56.9 (19937) [-65.0 / -62.6] | -91.8 (19937) [-107.6 / -101.6] | -107.0 (19937) [-119.9 / -115.2] |
| P1 0.6 mA | -56.0 (19937) [-62.9 / -60.7] | -90.8 (19037) [-103.9 / -98.2] | -105.4 (19937) [-119.1 / -114.6] |
| T 0.1 mA | -94.7 (19937) [-108.6 / -104.9] | -120.6 (19937) [-126.8 / -123.7] | -120.7 (19937) [-126.9 / -123.7] |
| T 0.25 mA | -92.0 (19937) [-106.3 / -102.5] | -116.4 (19937) [-122.5 / -119.4] | -116.5 (19937) [-122.5 / -119.4] |
| T 0.3 mA | -90.8 (19937) [-105.4 / -101.4] | -115.0 (19937) [-121.0 / -117.9] | -115.0 (19937) [-121.0 / -117.9] |
| T 0.35 mA (grid current) | -58.5 (19937) [-65.8 / -63.6] | -94.0 (18037) [-104.7 / -100.6] | -113.4 (19937) [-121.3 / -118.0] |
| T 0.45 mA (grid current) | -46.3 (19037) [-52.0 / -50.1] | -64.9 (18037) [-75.8 / -71.4] | -87.5 (19937) [-94.4 / -93.0] |
| T 0.5 mA (grid current) | -43.3 (19037) [-48.0 / -46.0] | -59.9 (19037) [-69.5 / -65.2] | -80.5 (19937) [-87.7 / -87.2] |

Reading the tables:
- **2 V.** For the no-grid cases every factor is on the resampler's floor (internal ≤ −197.0 dBc at 8× and 16×; at 4×
  at least 10 dB under the total above −160 dBc). With grid current T 0.5 mA's maximum at 4× is internal folding (−105.2 /
  −100.8 dBc); at 8× and 16× every grid-current maximum is the resampler's share (T 0.5 mA −126.8 / −122.5 dBc,
  internal ≥ 33.2 dB under it), though at other tones the internal share comes within 5.1 dB of the total.
- **8 V.** 4× is internal folding everywhere; 8× is internal folding for P1 and grid-current T; 16× is the resampler's
  floor for the no-grid cases and for T 0.35 mA with grid current (−118.9 / −113.4 dBc), and internal folding for
  T 0.45 / 0.5 mA with grid current (−89.9 / −84.9 dBc at 48 kHz).
- **The post-filter only lowers the absolute alias energy**, at every point above the analysis floor (0.006–29 dB).

### The band edge (`aliasing_quality.json` band_edge_scan)
Total dBc with the shipped FIR (19037 and 19937 Hz are grid tones):

| shipped FIR | 19037 | 19237 | 19437 | 19637 | 19831 | 19837 | 19937 | 19987 |
|---|---|---|---|---|---|---|---|---|
| P1 0.6 mA, 48 kHz, 4x, 2 V | -123.6 | -136.8 | -121.5 | -120.5 | -129.9 | -130.7 | -131.7 | -125.2 |
| P1 0.6 mA, 48 kHz, standard, 2 V | -123.6 | -136.8 | -121.5 | -120.5 | -129.9 | -130.7 | -131.7 | -125.2 |
| P1 0.6 mA, 48 kHz, hq, 2 V | -123.6 | -136.8 | -121.5 | -120.5 | -129.9 | -130.7 | -131.7 | -125.2 |
| P1 0.6 mA, 48 kHz, 4x, 8 V | -61.1 | -60.6 | -62.8 | -62.4 | -61.9 | -61.8 | -61.5 | -61.4 |
| P1 0.6 mA, 48 kHz, standard, 8 V | -98.4 | -96.7 | -98.4 | -98.5 | -97.9 | -97.8 | -97.0 | -96.5 |
| P1 0.6 mA, 48 kHz, hq, 8 V | -111.5 | -122.3 | -109.4 | -108.0 | -118.7 | -119.6 | -117.2 | -112.0 |
| P1 0.6 mA, 44.1 kHz, 4x, 2 V | -126.3 | -121.7 | -131.3 | -119.7 | -140.9 | -142.0 | -119.2 | -117.1 |
| P1 0.6 mA, 44.1 kHz, standard, 2 V | -126.3 | -121.7 | -131.4 | -119.8 | -142.7 | -144.7 | -119.2 | -117.1 |
| P1 0.6 mA, 44.1 kHz, hq, 2 V | -126.3 | -121.7 | -131.4 | -119.8 | -142.8 | -144.7 | -119.2 | -117.1 |
| P1 0.6 mA, 44.1 kHz, 4x, 8 V | -59.7 | -59.2 | -58.7 | -55.1 | -56.0 | -56.0 | -56.0 | -56.0 |
| P1 0.6 mA, 44.1 kHz, standard, 8 V | -90.8 | -91.9 | -93.3 | -93.2 | -92.1 | -92.0 | -91.2 | -90.9 |
| P1 0.6 mA, 44.1 kHz, hq, 8 V | -109.6 | -107.0 | -108.6 | -105.2 | -109.2 | -109.2 | -105.4 | -104.2 |
| T 0.5 mA (grid current), 48 kHz, 4x, 2 V | -109.9 | -104.9 | -105.2 | -105.1 | -105.2 | -105.2 | -105.2 | -105.2 |
| T 0.5 mA (grid current), 48 kHz, standard, 2 V | -126.8 | -140.1 | -124.8 | -123.7 | -133.1 | -134.0 | -135.0 | -128.4 |
| T 0.5 mA (grid current), 48 kHz, hq, 2 V | -126.8 | -140.2 | -124.8 | -123.7 | -133.1 | -134.0 | -135.0 | -128.4 |
| T 0.5 mA (grid current), 48 kHz, 4x, 8 V | -47.6 | -44.2 | -44.4 | -44.2 | -44.0 | -44.0 | -43.9 | -43.9 |
| T 0.5 mA (grid current), 48 kHz, standard, 8 V | -68.9 | -64.2 | -64.5 | -64.1 | -63.7 | -63.7 | -63.5 | -63.4 |
| T 0.5 mA (grid current), 48 kHz, hq, 8 V | -84.9 | -83.8 | -84.0 | -84.4 | -86.1 | -86.1 | -86.2 | -86.2 |
| T 0.5 mA (grid current), 44.1 kHz, 4x, 2 V | -105.2 | -105.2 | -105.2 | -100.4 | -100.8 | -100.8 | -100.8 | -100.8 |
| T 0.5 mA (grid current), 44.1 kHz, standard, 2 V | -129.6 | -124.9 | -134.7 | -123.0 | -147.6 | -150.6 | -122.5 | -120.4 |
| T 0.5 mA (grid current), 44.1 kHz, hq, 2 V | -129.6 | -124.9 | -134.7 | -123.0 | -148.3 | -152.2 | -122.5 | -120.4 |
| T 0.5 mA (grid current), 44.1 kHz, 4x, 8 V | -43.3 | -43.2 | -43.2 | -42.5 | -43.7 | -43.7 | -43.7 | -43.7 |
| T 0.5 mA (grid current), 44.1 kHz, standard, 8 V | -59.9 | -59.6 | -59.4 | -60.6 | -60.4 | -60.4 | -60.2 | -60.1 |
| T 0.5 mA (grid current), 44.1 kHz, hq, 8 V | -85.5 | -82.2 | -81.7 | -81.2 | -80.8 | -80.7 | -80.5 | -80.4 |

Between 19 and 20 kHz the floor moves by up to 27.6 dB from tone to tone, so the grid and the scan are samples, not a
maximum over the band. At 44.1 kHz, P1 0.6 mA, 2 V, standard, the resampler share follows the interpolator's image
rejection A(fs − f) with a fitted coefficient K = share + A(fs − f) (`resampler_study.band_edge_estimate`):

| tone, Hz | 15037 | 16037 | 17037 | 18037 | 19037 | 19237 | 19437 | 19637 | 19831 | 19837 | 19937 | 19987 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| K = share + A(fs − f), dB | -21.3 | -20.0 | -23.5 | -23.9 | -21.5 | -21.9 | -23.7 | -22.8 | 7.8 | -23.3 | -22.3 | -22.2 |

K stays within −23.9 to −20.0 dB except at 19831 Hz, where A has a notch and another path takes over (K = +7.8 dB;
the re-review's probe, reproduced). So max K − min A (−114.8 dBc for the shipped FIR, −160.4 dBc for k140) is an
**empirical estimate**, not a bound; no conclusion here rests on it. The guard covers the scan's measured tones.

### 64× ground truth
| point | 4× | 8× | 16× | 64× | 16× − 64×, dB | 64× internal |
|---|---|---|---|---|---|---|
| P1 0.6 mA, 48 kHz, 2 V, 16037 | -125.57 | -125.57 | -125.56 | -125.56 | -0.00 | -197.1 |
| P1 0.6 mA, 48 kHz, 2 V, 19037 | -123.60 | -123.59 | -123.59 | -123.59 | -0.00 | -205.3 |
| P1 0.6 mA, 48 kHz, 8 V, 16037 | -68.86 | -107.15 | -114.76 | -114.76 | 0.00 | -184.3 |
| P1 0.6 mA, 48 kHz, 8 V, 19037 | -61.12 | -98.44 | -111.50 | -111.50 | 0.00 | -199.4 |
| P1 0.6 mA, 48 kHz, 8 V, 19937 | -61.55 | -97.01 | -117.17 | -117.18 | 0.01 | -184.3 |
| P1 0.6 mA, 44.1 kHz, 8 V, 19037 | -59.74 | -90.83 | -109.62 | -109.63 | 0.00 | -189.1 |
| P1 0.6 mA, 44.1 kHz, 8 V, 19937 | -55.99 | -91.24 | -105.40 | -105.40 | 0.00 | -188.7 |
| T 0.3 mA, 48 kHz, 2 V, 16037 | -137.02 | -137.04 | -137.04 | -137.04 | 0.00 | -215.4 |
| T 0.3 mA, 48 kHz, 2 V, 19037 | -135.00 | -135.02 | -135.02 | -135.02 | 0.00 | -219.2 |
| T 0.3 mA, 48 kHz, 8 V, 16037 | -110.75 | -122.00 | -122.01 | -122.01 | 0.00 | -202.9 |
| T 0.3 mA, 48 kHz, 8 V, 19037 | -102.99 | -119.64 | -119.65 | -119.65 | 0.00 | -207.5 |
| T 0.3 mA, 48 kHz, 8 V, 19937 | -94.99 | -127.15 | -127.18 | -127.18 | 0.01 | -202.8 |
| T 0.3 mA, 44.1 kHz, 8 V, 19037 | -102.87 | -121.46 | -121.51 | -121.52 | 0.01 | -210.0 |
| T 0.3 mA, 44.1 kHz, 8 V, 19937 | -90.76 | -114.99 | -115.01 | -115.02 | 0.01 | -204.8 |
| T 0.5 mA (grid current), 48 kHz, 8 V, 19037 | -47.58 | -68.91 | -84.87 | -110.91 | 26.03 | -180.6 |
| T 0.5 mA (grid current), 48 kHz, 8 V, 19937 | -43.92 | -63.49 | -86.16 | -116.54 | 30.38 | -174.2 |
| T 0.5 mA (grid current), 44.1 kHz, 8 V, 19037 | -43.30 | -59.90 | -85.46 | -108.99 | 23.53 | -171.4 |
| T 0.5 mA (grid current), 44.1 kHz, 8 V, 19937 | -43.69 | -60.22 | -80.49 | -104.65 | 24.16 | -166.6 |

### The floor: the FIR resampler's stopband (`resampler_study.json`)
The shipped half-bands (Kaiser β = 9) are limited by β, not length: at 127 taps a larger β reaches about 157 dB. The
candidates are derived (`resampler_study.design_for`): for a target stopband A, β = 0.1102 (A − 8.7) (Kaiser's
formula, Oppenheim & Schafer, *Discrete-Time Signal Processing*, 3rd ed., Sec. 7.5, eq. 7.75, p. 542), and each stage
gets the shortest 4m + 3 length at least A dB down over its folding band with ≤ 0.005 dB passband ripple to 20 kHz.

| candidate | rate | first stage taps / beta | later taps / beta | one-pass stopband 4x / 8x / 16x, dB | chain ripple, dB | latency 4x / 8x / 16x, base samples | C++ MACs per sample 4x / 8x / 16x |
|---|---|---|---|---|---|---|---|
| shipped | 48 kHz | 127 / 9 | 23 / 9 | 92.8 / 94.2 / 94.2 | <= 1.1e-03 | 69 / 72 / 73 | 349 / 537 / 913 |
| shipped | 44.1 kHz | 215 / 9 | 23 / 9 | 89.3 / 90.1 / 90.1 | <= 1.1e-03 | 113 / 116 / 117 | 525 / 713 / 1089 |
| k120 | 48 kHz | 99 / 12.27 | 35 / 12.27 | 120.0 / 120.5 / 120.5 | <= 3.7e-05 | 58 / 62 / 64 | 341 / 625 / 1193 |
| k120 | 44.1 kHz | 179 / 12.27 | 35 / 12.27 | 120.0 / 120.5 / 120.5 | <= 3.1e-05 | 98 / 102 / 104 | 501 / 785 / 1353 |
| k140 | 48 kHz | 123 / 14.47 | 47 / 14.47 | 140.6 / 140.6 / 140.6 | <= 3.2e-06 | 73 / 79 / 82 | 437 / 817 / 1577 |
| k140 | 44.1 kHz | 219 / 14.47 | 47 / 14.47 | 140.4 / 140.4 / 140.4 | <= 2.9e-06 | 121 / 127 / 130 | 629 / 1009 / 1769 |
| k150 | 48 kHz | 131 / 15.57 | 51 / 15.57 | 150.4 / 150.4 / 150.4 | <= 1.1e-06 | 78 / 84 / 87 | 469 / 881 / 1705 |
| k150 | 44.1 kHz | 235 / 15.57 | 51 / 15.57 | 150.1 / 150.2 / 150.2 | <= 1.0e-06 | 130 / 136 / 139 | 677 / 1089 / 1913 |

Aliasing at the study's points, standard 8×, 1 s windows (dBc, filter off; the resampler share in brackets):

| point | rate | shipped | k120 | k140 | k150 |
|---|---|---|---|---|---|
| P2 0.1 mA 2 V 19037 | 48 kHz | -97.2 [-97.2] | -121.6 [-121.6] | -147.5 [-147.5] | -167.6 [-170.1] |
| P2 0.1 mA 2 V 16037 | 48 kHz | -99.1 [-99.1] | -118.1 [-118.1] | -137.7 [-137.7] | -158.0 [-158.2] |
| P2 0.25 mA 2 V 19037 | 48 kHz | -91.2 [-91.2] | -115.6 [-115.6] | -141.5 [-141.5] | -161.7 [-164.0] |
| P2 0.25 mA 2 V 16037 | 48 kHz | -93.1 [-93.1] | -112.1 [-112.1] | -131.7 [-131.7] | -152.0 [-152.2] |
| P2 0.45 mA 2 V 19037 | 48 kHz | -89.0 [-89.0] | -113.3 [-113.3] | -139.2 [-139.3] | -159.7 [-161.7] |
| P2 0.45 mA 2 V 16037 | 48 kHz | -90.9 [-90.9] | -109.9 [-109.9] | -129.5 [-129.5] | -149.9 [-150.0] |
| P2 0.6 mA 2 V 19037 | 48 kHz | -89.5 [-89.5] | -113.9 [-113.9] | -139.8 [-139.8] | -160.4 [-162.1] |
| P2 0.6 mA 2 V 16037 | 48 kHz | -91.5 [-91.5] | -110.5 [-110.5] | -130.1 [-130.1] | -150.5 [-150.6] |
| P2 0.25 mA 8 V 19037 | 48 kHz | -61.6 [-66.7] | -63.1 [-85.8] | -63.1 [-106.0] | -63.1 [-132.0] |
| P2 0.45 mA 8 V 19037 | 48 kHz | -63.5 [-75.0] | -63.8 [-94.1] | -63.8 [-114.4] | -63.8 [-140.5] |
| P1 0.6 mA 2 V 19037 | 48 kHz | -123.6 [-123.6] | -147.9 [-147.9] | -173.7 [-173.8] | -193.4 [-195.8] |
| P1 0.45 mA 2 V 19037 | 48 kHz | -130.4 [-130.4] | -154.6 [-154.6] | -180.3 [-180.4] | -200.0 [-201.9] |
| T 0.3 mA 2 V 19037 | 48 kHz | -135.0 [-135.0] | -159.3 [-159.3] | -185.2 [-185.2] | -206.5 [-207.3] |
| T 0.3 mA 8 V 19037 | 48 kHz | -119.6 [-119.6] | -143.7 [-143.7] | -168.6 [-168.6] | -186.3 [-190.0] |
| P2 0.1 mA 2 V 19037 | 44.1 kHz | -99.9 [-99.9] | -125.6 [-125.6] | -139.4 [-139.4] | -152.7 [-152.7] |
| P2 0.1 mA 2 V 16037 | 44.1 kHz | -109.7 [-109.7] | -145.2 [-145.3] | -146.1 [-146.1] | -158.0 [-158.2] |
| P2 0.25 mA 2 V 19037 | 44.1 kHz | -93.9 [-93.9] | -119.6 [-119.6] | -133.4 [-133.4] | -146.7 [-146.7] |
| P2 0.25 mA 2 V 16037 | 44.1 kHz | -103.7 [-103.7] | -139.2 [-139.2] | -140.1 [-140.1] | -152.0 [-152.2] |
| P2 0.45 mA 2 V 19037 | 44.1 kHz | -91.7 [-91.7] | -117.4 [-117.4] | -131.2 [-131.2] | -144.5 [-144.5] |
| P2 0.45 mA 2 V 16037 | 44.1 kHz | -101.4 [-101.4] | -136.8 [-136.8] | -137.8 [-137.8] | -149.8 [-150.0] |
| P2 0.6 mA 2 V 19037 | 44.1 kHz | -92.2 [-92.2] | -117.9 [-117.9] | -131.7 [-131.7] | -145.0 [-145.1] |
| P2 0.6 mA 2 V 16037 | 44.1 kHz | -102.0 [-102.0] | -137.2 [-137.2] | -138.4 [-138.4] | -150.3 [-150.5] |
| P2 0.25 mA 8 V 19037 | 44.1 kHz | -55.6 [-57.5] | -60.2 [-87.1] | -60.2 [-109.7] | -60.2 [-115.3] |
| P2 0.45 mA 8 V 19037 | 44.1 kHz | -58.7 [-65.9] | -59.6 [-95.5] | -59.6 [-118.0] | -59.6 [-123.7] |
| P1 0.6 mA 2 V 19037 | 44.1 kHz | -126.3 [-126.3] | -152.0 [-152.0] | -165.8 [-165.8] | -179.0 [-179.1] |
| P1 0.45 mA 2 V 19037 | 44.1 kHz | -133.1 [-133.1] | -158.8 [-158.8] | -172.5 [-172.5] | -185.8 [-185.9] |
| T 0.3 mA 2 V 19037 | 44.1 kHz | -137.8 [-137.8] | -163.5 [-163.5] | -177.2 [-177.2] | -190.6 [-190.6] |
| T 0.3 mA 8 V 19037 | 44.1 kHz | -121.5 [-121.5] | -147.6 [-147.7] | -161.5 [-161.9] | -170.8 [-174.9] |

Supported-mode k140 checkpoints at every factor (no-grid cases; dBc, filter off; shipped → k140):

| rate | setting | case, drive | 16037 Hz: shipped → k140 | 19037 Hz: shipped → k140 | 19937 Hz: shipped → k140 |
|---|---|---|---|---|---|
| 48 kHz | 4x | P1 0.1 mA, 2 V | -142.5 → -181.2 | -140.3 → -187.1 | -148.1 → -170.4 |
| 48 kHz | 4x | P1 0.1 mA, 8 V | -79.3 → -79.3 | -73.2 → -73.2 | -60.6 → -60.6 |
| 48 kHz | 4x | P1 0.6 mA, 2 V | -125.6 → -164.2 | -123.6 → -172.6 | -131.7 → -163.8 |
| 48 kHz | 4x | P1 0.6 mA, 8 V | -68.9 → -68.9 | -61.1 → -61.1 | -61.5 → -61.5 |
| 48 kHz | 4x | T 0.3 mA, 2 V | -137.0 → -175.6 | -135.0 → -185.1 | -143.1 → -178.6 |
| 48 kHz | 4x | T 0.3 mA, 8 V | -110.7 → -111.1 | -103.0 → -103.1 | -95.0 → -95.0 |
| 48 kHz | standard | P1 0.1 mA, 2 V | -142.5 → -181.2 | -140.3 → -190.0 | -148.1 → -183.9 |
| 48 kHz | standard | P1 0.1 mA, 8 V | -116.4 → -127.6 | -112.5 → -118.3 | -109.0 → -109.3 |
| 48 kHz | standard | P1 0.6 mA, 2 V | -125.6 → -164.2 | -123.6 → -173.7 | -131.7 → -167.2 |
| 48 kHz | standard | P1 0.6 mA, 8 V | -107.1 → -108.0 | -98.4 → -98.7 | -97.0 → -97.1 |
| 48 kHz | standard | T 0.3 mA, 2 V | -137.0 → -175.7 | -135.0 → -185.2 | -143.1 → -178.6 |
| 48 kHz | standard | T 0.3 mA, 8 V | -122.0 → -160.8 | -119.6 → -168.6 | -127.2 → -163.2 |
| 48 kHz | hq | P1 0.1 mA, 2 V | -142.5 → -181.2 | -140.3 → -190.0 | -148.1 → -183.9 |
| 48 kHz | hq | P1 0.1 mA, 8 V | -116.8 → -155.9 | -113.8 → -160.6 | -120.3 → -157.3 |
| 48 kHz | hq | P1 0.6 mA, 2 V | -125.6 → -164.2 | -123.6 → -173.7 | -131.7 → -167.2 |
| 48 kHz | hq | P1 0.6 mA, 8 V | -114.8 → -155.2 | -111.5 → -155.4 | -117.2 → -153.0 |
| 48 kHz | hq | T 0.3 mA, 2 V | -137.0 → -175.7 | -135.0 → -185.2 | -143.1 → -178.6 |
| 48 kHz | hq | T 0.3 mA, 8 V | -122.0 → -160.8 | -119.6 → -168.7 | -127.2 → -163.2 |
| 44.1 kHz | 4x | P1 0.1 mA, 2 V | -153.0 → -188.2 | -143.1 → -174.6 | -135.9 → -156.6 |
| 44.1 kHz | 4x | P1 0.1 mA, 8 V | -72.8 → -72.7 | -63.6 → -63.6 | -70.0 → -70.0 |
| 44.1 kHz | 4x | P1 0.6 mA, 2 V | -136.1 → -172.4 | -126.3 → -165.2 | -119.2 → -146.9 |
| 44.1 kHz | 4x | P1 0.6 mA, 8 V | -62.5 → -62.4 | -59.7 → -59.7 | -56.0 → -56.0 |
| 44.1 kHz | 4x | T 0.3 mA, 2 V | -147.5 → -183.9 | -137.8 → -177.2 | -130.7 → -180.6 |
| 44.1 kHz | 4x | T 0.3 mA, 8 V | -117.6 → -117.8 | -102.9 → -102.9 | -90.8 → -90.8 |
| 44.1 kHz | standard | P1 0.1 mA, 2 V | -153.0 → -189.3 | -143.1 → -182.5 | -136.0 → -192.4 |
| 44.1 kHz | standard | P1 0.1 mA, 8 V | -121.3 → -123.7 | -110.4 → -113.4 | -99.1 → -99.7 |
| 44.1 kHz | standard | P1 0.6 mA, 2 V | -136.1 → -172.4 | -126.3 → -165.8 | -119.2 → -175.6 |
| 44.1 kHz | standard | P1 0.6 mA, 8 V | -104.9 → -105.1 | -90.8 → -90.9 | -91.2 → -91.4 |
| 44.1 kHz | standard | T 0.3 mA, 2 V | -147.6 → -183.9 | -137.8 → -177.2 | -130.7 → -187.1 |
| 44.1 kHz | standard | T 0.3 mA, 8 V | -131.8 → -168.7 | -121.5 → -161.5 | -115.0 → -169.3 |
| 44.1 kHz | hq | P1 0.1 mA, 2 V | -153.0 → -189.3 | -143.1 → -182.5 | -136.0 → -192.4 |
| 44.1 kHz | hq | P1 0.1 mA, 8 V | -125.0 → -163.3 | -113.5 → -156.1 | -108.4 → -162.6 |
| 44.1 kHz | hq | P1 0.6 mA, 2 V | -136.1 → -172.4 | -126.3 → -165.8 | -119.2 → -175.6 |
| 44.1 kHz | hq | P1 0.6 mA, 8 V | -119.0 → -161.1 | -109.6 → -151.2 | -105.4 → -148.1 |
| 44.1 kHz | hq | T 0.3 mA, 2 V | -147.6 → -183.9 | -137.8 → -177.2 | -130.7 → -187.1 |
| 44.1 kHz | hq | T 0.3 mA, 8 V | -131.9 → -168.7 | -121.5 → -161.9 | -115.0 → -171.1 |

- **What limits P2 at 2 V is the interpolator, not the decimator.** At P2 0.45 mA, 2 V, 19037 Hz, 48 kHz: k140 up with
  the shipped decimator reads −133.7 dBc; the shipped interpolator with the k140 decimator reads −89.0, the shipped
  value. P2's strong second-order term mixes the tone with the interpolator's residual image at fs − f straight into
  the audio band, which is why higher oversampling (whose first stage is the same) does not help.
- **P2 with k140** (current fit): full 2 V grid (19 tones × 4 currents, the #6 tones) worst −129.5 dBc (0.45 mA,
  16037 Hz) at 48 kHz and −131.2 dBc (0.45 mA, 19037 Hz) at 44.1 kHz. At 8 V only 0.25 / 0.45 mA at 19037 Hz were
  measured: −59.6 to −63.8 dBc at 8× whatever the FIR (internal), and at hq −66.9 / −75.0 (48 kHz) and −57.7 / −65.9
  (44.1 kHz) with the shipped FIR (why PR #121 found hq no fix), −106.2 / −114.3 and −109.3 / −116.6 with k140.
- **Cost.** Latency and multiply-adds are in the design table (MACs counted as `cpp/dk_core.h`'s `FirHalfBand`
  implements them, zero taps included). Measured with `resampler_cost.cpp` (the core's `FirHalfBand`, mono double,
  `-O3`, candidates in alternating order, 11 rounds of 0.5 s per candidate, rate and factor on the shared M1; best of
  the rounds quoted because the medians are 1.0–2.2× the minima with other jobs running): shipped → k140, ns per base
  sample, 48 kHz 95.5 → 115.7 (4×), 162.2 → 205.6 (8×), 281.2 → 471.6 (16×); 44.1 kHz 132.6 → 155.0, 195.0 → 259.6,
  311.7 → 477.5: 1.17–1.68×, in line with the 1.20–1.73× multiply-add ratio. For scale, the whole 12AX7 C++ core at
  standard costs 2.55–2.97 µs per sample on the same M1 (`cpp/bench_apple_m1.json`); the 6AS6 core (#104) is not ported
  yet. Most of k140's extra work is in the later stages (47 taps at 2–8× the base rate); per-stage later lengths would
  recover part of it.

### Limits, baselines and the CI guard (`dk6as6/aliasing_guard.py`, CI jobs `aliasing (6as6, <rate> Hz)`)
> **#134:** the guard now measures k140, the stage's default, with the design in every key (`P1/0.6mA/standard/k140/2V`).
> Its keys are those below. Its limits and tones come from the k140 grid at those keys (`aliasing_guard_k140.json`,
> 29 tones including 19957 / 19977 / 19997 Hz, measured on the C++ core, which reads the same as this stage to 1.1e-5
> dB). Its baselines equal the C++ core's CI-window readings to ≤ 0.01 dB. Each limit also counts its tones'
> CI-window readings, because with k140 at 2 V the 0.1 s window's floor (−149.6 dBc) lies above the 1 s worst
> (−162.9 dBc). The table below is #113's shipped-FIR record.

The guard measures the production default stage (grid current). Per rate the keys are each setting × drive's worst
supported case, and at standard / 8 V also the other mode's worst, so each mode keeps a key. Each key's limit is the
worst total over every tone measured there (the grid and the scan) + 3 dB, rounded up to 0.1 dB; its CI tones are the
highest (at most three, `MAX_TONES`) within 3 dB of that worst, which bounds CI time where a key is flat across the top
of the band (grid-current T). The per-tone ratchet is the #98 format (`format_version` 2, `values[key].tones[tone] =
{total_dbc, f_hz}`, 1.5 dB tolerance), in the 0.1 s CI window after the 1.25 s settle: 19 points at 48 kHz and 21 at
44.1 kHz, margins 2.97–5.60 dB. 4× is guarded although it is not a shipped setting. The limits are regression ceilings,
not a listening threshold; between the measured tones the guard measures nothing.

| rate | key | limit dBc | tone Hz: CI-window baseline dBc (margin dB) |
|---|---|---|---|
| 48 kHz | T/0.5mA/4x/fir/2V (grid current) | -101.8 | 19637: -105.15 (3.35); 19437: -105.15 (3.35); 19237: -104.87 (3.07) |
| 48 kHz | T/0.5mA/4x/fir/8V (grid current) | -40.8 | 19987: -43.88 (3.08); 19937: -43.92 (3.12); 19837: -44.02 (3.22) |
| 48 kHz | P1/0.6mA/standard/fir/2V | -117.4 | 19637: -120.53 (3.13); 19437: -121.46 (4.06) |
| 48 kHz | T/0.5mA/standard/fir/8V (grid current) | -60.3 | 19987: -63.39 (3.09); 19937: -63.48 (3.18); 19837: -63.66 (3.36) |
| 48 kHz | P1/0.6mA/standard/fir/8V | -93.5 | 19987: -96.49 (2.99); 19937: -96.98 (3.48); 19237: -97.65 (4.15) |
| 48 kHz | P1/0.6mA/hq/fir/2V | -117.4 | 19637: -120.53 (3.13); 19437: -121.46 (4.06) |
| 48 kHz | T/0.5mA/hq/fir/8V (grid current) | -80.7 | 19637: -84.37 (3.67); 19437: -84.04 (3.34); 19237: -83.79 (3.09) |
| 44.1 kHz | T/0.5mA/4x/fir/2V (grid current) | -97.3 | 19987: -100.75 (3.45); 19937: -100.77 (3.47); 19637: -100.45 (3.15) |
| 44.1 kHz | T/0.5mA/4x/fir/8V (grid current) | -39.4 | 19637: -42.67 (3.27); 19437: -43.18 (3.78); 19237: -43.22 (3.82) |
| 44.1 kHz | P1/0.6mA/standard/fir/2V | -114.1 | 19987: -117.07 (2.97); 19937: -119.00 (4.90); 19637: -119.70 (5.60) |
| 44.1 kHz | T/0.5mA/standard/fir/8V (grid current) | -56.4 | 19437: -59.41 (3.01); 19237: -59.65 (3.25); 19037: -59.89 (3.49) |
| 44.1 kHz | P1/0.6mA/standard/fir/8V | -87.8 | 19987: -90.85 (3.05); 19937: -91.22 (3.42); 19037: -90.84 (3.04) |
| 44.1 kHz | P1/0.6mA/hq/fir/2V | -114.1 | 19987: -117.07 (2.97); 19937: -119.00 (4.90); 19637: -119.69 (5.59) |
| 44.1 kHz | T/0.5mA/hq/fir/8V (grid current) | -77.3 | 19987: -80.36 (3.06); 19937: -80.48 (3.18); 19837: -80.73 (3.43) |

### Merge gate and CI headroom (#127)
- The workflow cannot block a merge (`main` has no branch protection or rulesets). The `ci gate` job runs
  `.github/scripts/ci_gate.py`: it reads every job of the run from the API (the `needs` context aggregates matrix legs,
  and a leg cancelled before a runner can leave that aggregate green, PR #129 re-review) and fails unless every expected
  job, each matrix leg by name, is present and passed (macOS may be skipped where it does not run), with no unlisted job.
  Its self-test covers eleven cases. **Making `ci gate` a required check on `main` is a repository setting for
  Michael.**
- #127 landed with #128 (the 6AS6 tests in four shards on Linux and macOS); this PR is rebased on it.
  On run 44914992226 (head b88779b): Linux fullstage 3:51, 6as6 stage 5:22, envelope 5:53, validation 3:53, rest 5:21; macOS fullstage 3:48, 6as6 stage 3:21, envelope 7:14, validation 4:14, rest 5:56, so at most 7:14 of 20:00 (36 %, within #127's 60 %); the 6AS6 guard jobs 9:18 (48 kHz) and 9:47 (44.1 kHz), 49 %.

### A/B samples (not committed; `~/Developer/MAP-wt-113-samples`)
A 3 s exponential chirp 4–20 kHz from silence, 48 kHz, every file in a pair at one gain (the reference at −1 dBFS),
with difference files (and +40 dB copies). P1 0.6 mA at 8 V: 4× − 16× is −73.4 dBFS below 18 kHz against a −4.4 dBFS
signal (−69 dB), 8× − 16× −95.3 dBFS (−91 dB). P2 0.45 mA at 2 V, shipped − k140 FIR: −96.7 dBFS below 18 kHz
(−85 dB re the signal); its −47.7 dBFS above 20 kHz is the two filters' different transition bands, not aliasing.
No listening test was run, and none is claimed; audibility stays open for production validation.

### For Michael (choices to revisit)
1. **No eco on the 6AS6 path.** With grid current, T 0.5 mA at 4× is above the 12AX7 standard budget at both drives.
2. **k140 soon.** The shipped FIR leaves a supported setting (P1 0.6 mA, 2 V, 44.1 kHz) above the gate at 19987 Hz.
3. **Grid-current T at 8 V.** T 0.45–0.5 mA is internal folding at every factor (8×: −64.7 / −63.5 dBc; 16×: −89.9 /
   −84.9 at 48 kHz) and 16× stays 23.5–30.4 dB above the 64× ground truth. All within the gates, but if aliasing should
   be lower there it needs more oversampling or another antialiasing method for the grid-current law (#104's ADAA
   question); the grid law itself is unvalidated (#112).
4. **k140 vs k150**: k150 lowers P2's 2 V floor another ~20 dB at 48 kHz and T's 8 V floor at 8× (−168.6 → −186.3 dBc),
   for +5 samples (8×, 48 kHz) and 8 % more multiply-adds.
5. **The plugin's Quality menu** (Eco / Standard / HQ for the 12AX7) for a 6AS6 Gyre without eco: #104 / #115.
6. **The 12AX7 path** has the same resampler and grid blind spot (limits −118.2 / −118.3 dBc on the #6 grid).

### Follow-ups (evidence above)
- Implement k140 for the 6AS6 path (Python stage and #104's C++), regenerate the 6AS6 results and baselines, and measure
  it on the grid-current T cases.
- Grid-current T folding at 16×: more oversampling or ADAA for the grid law (with #104), once the grid law is validated.
- Measure the 12AX7 stage at the band edge and with a k140-class resampler.
- A 4×-accurate post-filter, if eco is ever wanted.

## Inaudible aliasing in every mode and quality (#113 rework, 22 Nov 2026)
Michael reopened #113 (21 Nov 2026). For this path he replaced the dBc gate with an inaudibility criterion that must
hold in every mode and quality, with grid current, at 2 and 8 V and at 44.1 and 48 kHz. He also asked for an eco quality
and for the k140 FIR. PR #129 stays (no revert); everything here is measured on top of it.
- Criterion: `dk6as6/inaudible.py`.
- Assessment: `python -m dk6as6.inaudible_sweep` → `inaudible.json`.
- CI guard: `python -m dk6as6.aliasing_guard`, with baselines `aliasing_baseline_inaudible_<fs>.json`.
- Tests: `test_dk6as6_inaudible.py`, `test_dk6as6_aliasing_guard.py`.

### Proposal for Michael (a decision is needed; the numbers are below)
This PR implements the proposal so that CI enforces the criterion on what is offered. Every item is a two-way door in
the code (`stage.QUALITY`, `stage.SUPPORTED_MA`).
1. **Standard 8× → 16×, hq 16× → 32×, both with k140.**
   - 8× fails at 8 V in P1 at every bias (−7.9 to −21.3 dB) and in T 0.35 mA (−22.5 / −20.3 dB at 48 / 44.1 kHz).
   - 16× passes every supported case at both rates and drives, in every post-filter position, for inputs below 20 kHz:
     by at least +9.8 dB (T 0.35 mA, 8 V, 44.1 kHz, filter 4k; +25.7 dB with the filter off).
   - 32× passes them by at least +18.5 dB (filter 4k; +34.3 dB off).
   - CPU (C++ core) relative to today's 8×: 16× costs 1.65–1.96×, 32× 3.12–3.62×.
2. **T with grid current supported to 0.35 mA (from 0.5 mA).** This caps a supported range.
   - At 16× and 8 V (48 / 44.1 kHz): T 0.375 mA +3.7 / −6.7 dB, 0.4 mA −12.4 / −15.3, 0.45 mA −23.3 / −25.2,
     0.5 mA −28.6 / −29.5. All of it is internal folding from the grid-current onset.
   - Higher factors would hold more of the range. 32× holds T to 0.45 mA (+13.0 / +7.1 dB) and T 0.5 mA at 48 kHz only (+3.2 dB; −3.6 dB at 44.1 kHz, where it needs 64×).
     But 32× as standard costs about two cores per mono channel in today's C++ core (CPU, below).
   - ADAA (#136) may hold the range more cheaply. It is not built yet.
3. **No eco.** Without ADAA (#136), no setting cheaper than 16× meets the criterion at 8 V.
   - 8× misses by up to 22.5 dB (T 0.35 mA) and 21.3 dB (P1 0.6 mA); 4× by up to 60.3 dB (P1 0.6 mA, 44.1 kHz). At 4× and 8 V every supported case fails, even T 0.1 mA (−2.2 / −14.9 dB).
   - Eco therefore waits for #136. Its numbers are posted there.
4. **hq at 32×:** about two cores per mono channel on this M1 (measured under load, below). Michael's call: keep hq at
   32× (for example render-only), leave it at 16× (then equal to standard), or drop it.
5. **P2 stays experimental.** At 8 V and 16× it fails at 0.25 mA (−2.5 / −9.7 dB) and at 0.45 mA at 44.1 kHz (−1.9).
   - The failing products are the resampler share at the band edge: a 19997 Hz tone's interpolator image mixes onto
     |2f − fs|. A higher factor does not change that: 32× and 64× read the same as 16× to 0.1 dB (0.25 mA: −2.4 / −9.6 dB).
6. **Inputs above 20 kHz fail at every factor (1088 of the 1248 points measured), and this PR does not guarantee them
   (#147).** A tone between
   20 kHz and Nyquist keeps an interpolator image at fs − f, inside the k140 half-band's transition band, which the
   stage mixes onto |2f − fs|. At 8 V and 16×, filter off: 48 kHz, 23299 Hz → 1402 Hz at −29.7 dBFS (−83.3 dB);
   44.1 kHz, 21799 Hz → 502 Hz at −27.3 dBFS (−81.5 dB). The 4k / 7k positions read worse (down to −104.6 dB), and
   32× reads the same. The 12AX7 path has the same mechanism. The guarantee, the guard and the tables above cover
   inputs below 20 kHz; fixing the rest needs an input band-limit or a first interpolator stage that stops from
   fs / 2 (#147, with latency and CPU to measure). Michael's call whether that blocks this rework.
7. **C++ (#104 / PR #133).** Its core has `MAX_OS` = 16 with standard 8× and hq 16×. If this is accepted, it needs 32×,
   and the plugin's Quality menu changes (#115).

### The criterion (`dk6as6/inaudible.py`)
Every alias product must sit below −100 dBFS **and** at least 10 dB under the absolute threshold of hearing at its own
frequency. Per product:
- margin = limit − level;
- limit(f) = min(−100 dBFS, T_q(f) − 10 dB − 105 dB).

A point passes when every product's margin is ≥ 0.
- **Threshold curve:** Terhardt's approximation of the threshold in quiet,
  T_q(f) = 3.64 (f/kHz)^−0.8 − 6.5 e^(−0.6 (f/kHz − 3.3)²) + 10^−3 (f/kHz)^4 dB SPL.
  - Sources: E. Terhardt, "Calculating virtual pitch", *Hearing Research* 1 (1979) 155–182; in the form of T. Painter
    and A. Spanias, "Perceptual coding of digital audio", *Proc. IEEE* 88(4) (2000) 451–515, eq. 1.
  - Its minimum is about −5 dB SPL near 3.3 kHz, so the limit is lowest there, about −120 dBFS.
  - The threshold term binds only between about 170 Hz and 10.97 kHz. Outside that the −100 dBFS floor binds, so the
    curve's steep top end, which is lenient (66 dB SPL at 16 kHz), decides no product.
- **SPL calibration (an assumption):** a full-scale sine (0 dBFS, AES17) reproduces at 105 dB SPL.
  - This is the SMPTE RP 200 cinema monitoring reference (pink noise at −20 dBFS RMS = 85 dB SPL, C-weighted, per
    channel), taken for a sine. It is a loud listening level.
  - A quieter calibration only loosens the threshold term. Every record's `spl_headroom_db` says how much louder the
    calibration could be before that term fails. At the worst supported points it equals the margin (the table below).
- **Full scale:** the output's own sample peak in the window, i.e. the level at which Output puts the processed signal at
  full scale, the loudest that does not clip.
  - It does not use the provisional volts mapping (#22, 1/64 FS per V at Output 1). At 8 V the stage peaks at
    75–90 V, which that mapping would put 1.4–3 dB over full scale.
- **Products:** a coherent tone on a DFT bin, 20 Hz–20 kHz. Every bin except the harmonics' own is read as a product,
  from the unwindowed DFT, 20 log10(2 |R| / n / peak): exact for each coherent line whatever its neighbours (metric 3).
  - History: metric 1 masked ±3 bins around every harmonic and could hide a folded line next to one (review round 2).
    Metric 2 read the other bins through a Hann window, whose estimate subtracts half of each neighbour, so in-phase
    neighbouring lines cancelled part of a line (review round 3: a cluster of seven folded lines passed both CI checks
    over its limit). Every record carries `margin_hann_db`, metric 2's reading, for comparison.
  - Cost: energy that is not a line (the start-up residue of the 1.25 s settle, mostly near 20 Hz) leaks further, about
    10 dB above the windowed reading. It is −109 to −131 dBFS re the peak in the 0.1 s CI window and −130 to −146 dBFS
    in 1 s at the guard keys, which still pass; where alias lines are the worst products the two readings agree to
    0.4 dB.
  - A harmonic's own bin is unmeasurable. Every record carries `first_hidden_order`, the lowest order whose fold could
    land exactly on one: at least order 2090 on the 1 s grid, far above any order that reaches a measurable level.
  - `test_dk6as6_inaudible.py` checks it on synthetic lines of known level, including both review cases: a −95 dBFS
    line one bin from the fundamental (round 2; it fails by 5.0 dB), and the in-phase cluster (round 3; its centre at
    1.5 dB over the limit fails the criterion, and 3 dB under its baseline margin fails the ratchet).
  - Energy that is not a line is read per bin at the window's resolution. In the 0.1 s CI window, lines less than
    10 Hz apart share a bin and read as their sum.
- The dBc totals of the earlier sections stay as supporting evidence. They are no longer the gate for this path.

### Method
- **Grid:** 29 tones: the #6 tones (1037 … 19037 Hz), #113's band-edge scan and #104's 19957 / 19977 / 19997 Hz.
  2 and 8 V; 44.1 and 48 kHz; a 1 s window after `SETTLE_S` (1.25 s).
  - Always the production default stage: g1 grid current (`pentode.HARDNESS`), k140.
  - **Post-filter (#103):** off everywhere. 7k and 4k as well at 16× and 32× for the supported cases (plus T 0.45 /
    0.6 mA). The filter lowers the absolute alias energy, but it lowers the output peak (0 dBFS here) more at high
    tones, so it costs margin (PR #146 review round 2).
  - **Inputs above 20 kHz** (`ABOVE_20K_HZ`: 20299–23899 Hz at 48 kHz, 20299–21799 Hz at 44.1 kHz, below Nyquist) at
    16× and 32× for those cases, every position. They are out of the guaranteed scope; see "What fails, and why".
- **Cases:**
  - P1: 0.1 / 0.25 / 0.45 / 0.6 mA.
  - T: 0.1 / 0.25 / 0.3 / 0.35 / 0.375 / 0.4 / 0.45 / 0.5 / 0.55 / 0.6 mA, which covers #132's range.
  - P2 (experimental): 0.1 / 0.25 / 0.45 / 0.6 mA.
  - Factors: 8×, 16×, 32× everywhere; 64× for T 0.375–0.6 mA and P2 at 8 V; 4× for the supported cases.
- **Engines.** The pure-Python stage costs about 75 µs per internal sample on this shared M1, so a 32× point takes about
  5 min. The full grid (7830 points) is out of reach with it.
  - So the grid ran on the C++ 6AS6 core (#104, `cpp/dk6as6_core.h`) through its `test_core_6as6 cli` harness,
    built with Apple clang 17 at −O2. It was built from PR #133's head fc5d690, whose core files are identical to main's
    since #133 merged (c8d882a).
  - Two lines were changed so that it runs 32× and 64×: `MAX_OS` 16 → 64, and `ResamplerT::later_[3]` → `later_[5]`.
  - The patched build is not committed.
  - The Python stage re-measures every guard key's deciding tone and every failing point that a conclusion rests on
    (`records_python`, `parity`): 33 points: every guard key's deciding tone (21), and the twelve failing points the proposal and the reviews cite. Those include the round-2 20299 Hz / 4k case and a 22999 Hz input. |Python − C++| ≤ 2e-4 dB in margin on all 32 paired points, with the same worst product. The 22999 Hz point is Python only.
- **CPU:** thread CPU time per base-rate sample of one mono C++ Stage (`dk6as6/inaudible_bench.cpp`).
  - 256-sample blocks, k140, a 1 + 7 + 13 kHz sum at 2 / 8 V peak.
  - Best of 7 rounds of 1 s per factor, the factors interleaved in each round.
  - Measured on the same M1, which other jobs shared: the load average was 4 at the start and about 90 during the run.
    An earlier run under heavier load (75–190) gives the same ratios to within 0.2 (`inaudible_bench.json` has both).
    The absolute numbers carry that load, so the ratios to 8× are the portable figure.
- **Accuracy:** `dk6as6.validate.run` (every `dk/validate.CRIT` criterion against the same-model ngspice references) at
  every reference setting of both models (44).
  - It ran on the same C++ core with PR #133's `CppStage`, which #133 holds to Python within its parity limits
    (`cpp/validate_cpp_6as6.LIMITS`: AC ≤ 1e-6 dB, harmonics ≤ 3e-4 dB), at 8×, 16×, 32× and 64× with k140.
  - The live CI tests (`test_dk6as6_stage.py`, `test_dk6as6_validation.py`) run the Python stage at the new standard
    and hq.

### The smallest margin per case (C++ screen, every grid tone; dB, negative = fails)
**2 V**: the smallest margin over the 29 grid tones, dB (negative = fails)

| case | 48 kHz 4x | 48 kHz 8x | 48 kHz 16x | 48 kHz 32x | 48 kHz 64x | 44.1 kHz 4x | 44.1 kHz 8x | 44.1 kHz 16x | 44.1 kHz 32x | 44.1 kHz 64x |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 0.1 mA | +62.7 | +62.7 | +62.7 | +62.7 | — | +55.7 | +62.0 | +62.0 | +62.0 | — |
| P1 0.25 mA | +65.9 | +70.7 | +70.7 | +70.7 | — | +53.1 | +70.7 | +70.7 | +70.7 | — |
| P1 0.45 mA | +59.1 | +59.1 | +59.1 | +59.1 | — | +50.3 | +52.0 | +52.0 | +52.0 | — |
| P1 0.6 mA | +50.4 | +50.4 | +50.4 | +50.4 | — | +45.1 | +45.1 | +45.1 | +45.1 | — |
| T 0.1 mA | +64.6 | +64.6 | +64.6 | +64.6 | — | +62.2 | +62.2 | +62.2 | +62.2 | — |
| T 0.25 mA | +65.4 | +65.4 | +65.4 | +65.4 | — | +58.0 | +58.0 | +58.0 | +58.0 | — |
| T 0.3 mA | +64.0 | +64.0 | +64.0 | +64.0 | — | +56.4 | +56.4 | +56.4 | +56.4 | — |
| T 0.35 mA | +62.3 | +62.3 | +62.3 | +62.3 | — | +54.8 | +54.8 | +54.8 | +54.8 | — |
| T 0.375 mA (unsupported) | — | +61.4 | +61.4 | +61.4 | +61.4 | — | +53.9 | +53.9 | +53.9 | +53.9 |
| T 0.4 mA (unsupported) | — | +60.5 | +60.5 | +60.5 | — | — | +52.9 | +52.9 | +52.9 | — |
| T 0.45 mA (unsupported) | +58.3 | +58.3 | +58.3 | +58.3 | — | +50.7 | +50.7 | +50.7 | +50.7 | — |
| T 0.5 mA (unsupported) | — | +55.3 | +55.9 | +55.9 | — | — | +45.2 | +48.3 | +48.3 | — |
| T 0.55 mA (unsupported) | — | -17.4 | +50.2 | +51.0 | — | — | -24.5 | +40.9 | +53.9 | — |
| T 0.6 mA (unsupported) | -56.1 | -29.2 | +18.2 | +47.3 | — | -64.8 | -36.2 | +12.8 | +43.3 | — |
| P2 0.1 mA (unsupported) | — | +24.1 | +24.1 | +24.1 | — | — | +18.6 | +18.6 | +18.6 | — |
| P2 0.25 mA (unsupported) | — | +19.6 | +19.6 | +19.6 | — | — | +12.6 | +12.6 | +12.6 | — |
| P2 0.45 mA (unsupported) | — | +18.0 | +18.0 | +18.0 | — | — | +10.4 | +10.4 | +10.4 | — |
| P2 0.6 mA (unsupported) | — | +18.5 | +18.5 | +18.5 | — | — | +10.9 | +10.9 | +10.9 | — |

**8 V**: the smallest margin over the 29 grid tones, dB (negative = fails)

| case | 48 kHz 4x | 48 kHz 8x | 48 kHz 16x | 48 kHz 32x | 48 kHz 64x | 44.1 kHz 4x | 44.1 kHz 8x | 44.1 kHz 16x | 44.1 kHz 32x | 44.1 kHz 64x |
|---|---|---|---|---|---|---|---|---|---|---|
| P1 0.1 mA | -45.7 | -7.9 | +44.7 | +44.7 | — | -52.9 | -11.8 | +37.0 | +37.0 | — |
| P1 0.25 mA | -43.6 | -14.8 | +44.8 | +44.8 | — | -56.4 | -15.7 | +37.5 | +37.5 | — |
| P1 0.45 mA | -47.1 | -18.7 | +41.7 | +41.7 | — | -58.1 | -12.7 | +40.8 | +42.9 | — |
| P1 0.6 mA | -49.4 | -21.1 | +34.3 | +34.3 | — | -60.3 | -21.3 | +34.3 | +34.3 | — |
| T 0.1 mA | -2.2 | +54.8 | +54.8 | +54.8 | — | -14.9 | +47.2 | +47.2 | +47.2 | — |
| T 0.25 mA | -12.0 | +50.5 | +50.5 | +50.5 | — | -16.0 | +42.9 | +42.9 | +42.9 | — |
| T 0.3 mA | -14.3 | +49.0 | +49.0 | +49.0 | — | -17.0 | +41.4 | +41.4 | +41.4 | — |
| T 0.35 mA | -51.9 | -22.5 | +34.3 | +49.5 | — | -57.3 | -20.3 | +25.7 | +42.0 | — |
| T 0.375 mA (unsupported) | — | -40.9 | +3.7 | +45.5 | +45.5 | — | -35.2 | -6.7 | +47.4 | +47.4 |
| T 0.4 mA (unsupported) | — | -45.1 | -12.4 | +35.7 | +44.2 | — | -50.2 | -15.3 | +27.6 | +47.1 |
| T 0.45 mA (unsupported) | -66.9 | -51.9 | -23.3 | +13.0 | +36.6 | -71.2 | -44.5 | -25.2 | +7.1 | +36.6 |
| T 0.5 mA (unsupported) | — | -53.7 | -28.6 | +3.2 | +29.9 | — | -57.4 | -29.5 | -3.6 | +29.9 |
| T 0.55 mA (unsupported) | — | -55.5 | -32.7 | -5.3 | +25.5 | — | -59.0 | -32.5 | -11.4 | +25.5 |
| T 0.6 mA (unsupported) | -69.2 | -60.8 | -30.5 | -12.1 | +22.3 | -76.6 | -57.3 | -36.6 | -13.4 | +22.3 |
| P2 0.1 mA (unsupported) | — | -44.5 | +1.6 | +1.6 | +1.6 | — | -45.6 | +1.5 | +1.5 | +1.5 |
| P2 0.25 mA (unsupported) | — | -62.6 | -14.7 | -14.7 | -14.7 | — | -64.4 | -15.2 | -15.2 | -15.2 |
| P2 0.45 mA (unsupported) | — | -55.2 | -4.2 | -4.2 | -4.2 | — | -50.8 | -4.3 | -4.3 | -4.3 |
| P2 0.6 mA (unsupported) | — | -52.7 | +0.6 | +0.6 | +0.6 | — | -53.2 | +0.6 | +0.6 | +0.6 |

The lowest measured factor at which every grid tone passes (— : none measured passes):

| case | 48 kHz, 2 V | 48 kHz, 8 V | 44.1 kHz, 2 V | 44.1 kHz, 8 V |
|---|---|---|---|---|
| P1 0.1 mA | 4× | 16× | 4× | 16× |
| P1 0.25 mA | 4× | 16× | 4× | 16× |
| P1 0.45 mA | 4× | 16× | 4× | 16× |
| P1 0.6 mA | 4× | 16× | 4× | 16× |
| T 0.1 mA | 4× | 8× | 4× | 8× |
| T 0.25 mA | 4× | 8× | 4× | 8× |
| T 0.3 mA | 4× | 8× | 4× | 8× |
| T 0.35 mA | 4× | 16× | 4× | 16× |
| T 0.375 mA | 8× | 16× | 8× | 32× |
| T 0.4 mA | 8× | 32× | 8× | 32× |
| T 0.45 mA | 4× | 32× | 4× | 32× |
| T 0.5 mA | 8× | 32× | 8× | 64× |
| T 0.55 mA | 16× | 64× | 16× | 64× |
| T 0.6 mA | 16× | 64× | 16× | 64× |
| P2 0.1 mA | 8× | 16× | 8× | 16× |
| P2 0.25 mA | 8× | — | 8× | — |
| P2 0.45 mA | 8× | — | 8× | — |
| P2 0.6 mA | 8× | 16× | 8× | 16× |

### The worst supported point per quality
| rate | factor | drive | worst supported point | margin dB | product Hz | level dBFS | limit dBFS | source | SPL headroom dB |
|---|---|---|---|---|---|---|---|---|---|
| 48 kHz | 8x | 2 V | P1 0.6 mA, 3037 Hz, filter off | +50.4 | 3038 | -170.0 | -119.7 | resampler | +50.4 |
| 48 kHz | 8x | 8 V | T 0.35 mA, 19037 Hz, filter off | -22.5 | 3260 | -97.5 | -120.0 | internal | -22.5 |
| 48 kHz | 16x | 2 V | P1 0.6 mA, 19997 Hz, filter 4k | +30.1 | 20 | -130.1 | -100.0 | other | +40.5 |
| 48 kHz | 16x | 8 V | T 0.35 mA, 19637 Hz, filter 4k | +13.0 | 2157 | -128.9 | -116.0 | internal | +13.0 |
| 48 kHz | 32x | 2 V | P1 0.6 mA, 19997 Hz, filter 4k | +30.1 | 20 | -130.1 | -100.0 | other | +40.5 |
| 48 kHz | 32x | 8 V | P1 0.1 mA, 19997 Hz, filter 4k | +24.0 | 20 | -124.0 | -100.0 | other | +32.5 |
| 44.1 kHz | 8x | 2 V | P1 0.6 mA, 19997 Hz, filter off | +45.1 | 4106 | -163.0 | -117.9 | resampler | +45.1 |
| 44.1 kHz | 8x | 8 V | P1 0.6 mA, 19437 Hz, filter off | -21.3 | 2934 | -98.1 | -119.4 | internal | -21.3 |
| 44.1 kHz | 16x | 2 V | P1 0.6 mA, 19997 Hz, filter 4k | +26.4 | 4106 | -144.4 | -117.9 | resampler | +26.4 |
| 44.1 kHz | 16x | 8 V | T 0.35 mA, 19997 Hz, filter 4k | +9.8 | 5705 | -123.1 | -113.2 | internal | +9.8 |
| 44.1 kHz | 32x | 2 V | P1 0.6 mA, 19997 Hz, filter 4k | +26.5 | 4106 | -144.4 | -117.9 | resampler | +26.5 |
| 44.1 kHz | 32x | 8 V | P1 0.1 mA, 19997 Hz, filter 4k | +18.5 | 4106 | -136.4 | -117.9 | resampler | +18.5 |

### Post-filter positions (in scope; the worst supported case per rate, factor, drive and position, dB)
| rate | factor | drive | filter off | filter 4k | filter 7k |
|---|---|---|---|---|---|
| 48 kHz | 16x | 2 V | +50.4 (P1 0.6 mA, 3037 Hz) | +30.1 (P1 0.6 mA, 19997 Hz) | +38.2 (P1 0.6 mA, 19997 Hz) |
| 48 kHz | 16x | 8 V | +34.3 (P1 0.6 mA, 3037 Hz) | +13.0 (T 0.35 mA, 19637 Hz) | +19.8 (T 0.35 mA, 19637 Hz) |
| 48 kHz | 32x | 2 V | +50.4 (P1 0.6 mA, 3037 Hz) | +30.1 (P1 0.6 mA, 19997 Hz) | +38.2 (P1 0.6 mA, 19997 Hz) |
| 48 kHz | 32x | 8 V | +34.3 (P1 0.6 mA, 3037 Hz) | +24.0 (P1 0.1 mA, 19997 Hz) | +30.1 (P1 0.6 mA, 18037 Hz) |
| 44.1 kHz | 16x | 2 V | +45.1 (P1 0.6 mA, 19997 Hz) | +26.4 (P1 0.6 mA, 19997 Hz) | +31.7 (P1 0.6 mA, 19997 Hz) |
| 44.1 kHz | 16x | 8 V | +25.7 (T 0.35 mA, 19997 Hz) | +9.8 (T 0.35 mA, 19997 Hz) | +13.9 (T 0.35 mA, 19997 Hz) |
| 44.1 kHz | 32x | 2 V | +45.1 (P1 0.6 mA, 19997 Hz) | +26.5 (P1 0.6 mA, 19997 Hz) | +31.7 (P1 0.6 mA, 19997 Hz) |
| 44.1 kHz | 32x | 8 V | +34.3 (P1 0.6 mA, 3037 Hz) | +18.5 (P1 0.1 mA, 19997 Hz) | +23.7 (P1 0.1 mA, 19997 Hz) |

The filter lowers the output peak (0 dBFS here) more than the low-frequency alias products at high input tones, so
4k costs up to about 16 dB of margin against off; every supported in-scope point still passes at 16× and 32×.

### Inputs above 20 kHz (out of scope, reported; #147)
| rate | factor | drive | filter off | filter 4k | filter 7k |
|---|---|---|---|---|---|
| 48 kHz | 16x | 2 V | -79.3 (P1 0.6 mA, 23299 Hz → 1402 Hz, -33.7 dBFS) | -102.1 (P1 0.6 mA, 23299 Hz → 1402 Hz, -10.9 dBFS) | -96.6 (P1 0.6 mA, 23299 Hz → 1402 Hz, -16.4 dBFS) |
| 48 kHz | 16x | 8 V | -83.3 (P1 0.1 mA, 23299 Hz → 1402 Hz, -29.7 dBFS) | -104.6 (T 0.35 mA, 23299 Hz → 1402 Hz, -8.3 dBFS) | -100.0 (P1 0.1 mA, 23299 Hz → 1402 Hz, -13.0 dBFS) |
| 48 kHz | 32x | 2 V | -79.3 (P1 0.6 mA, 23299 Hz → 1402 Hz, -33.7 dBFS) | -102.0 (P1 0.6 mA, 23299 Hz → 1402 Hz, -10.9 dBFS) | -96.6 (P1 0.6 mA, 23299 Hz → 1402 Hz, -16.4 dBFS) |
| 48 kHz | 32x | 8 V | -83.3 (P1 0.1 mA, 23299 Hz → 1402 Hz, -29.7 dBFS) | -104.6 (T 0.35 mA, 23299 Hz → 1402 Hz, -8.3 dBFS) | -100.0 (P1 0.1 mA, 23299 Hz → 1402 Hz, -13.0 dBFS) |
| 44.1 kHz | 16x | 2 V | -77.5 (P1 0.6 mA, 21799 Hz → 502 Hz, -31.2 dBFS) | -99.7 (P1 0.6 mA, 21799 Hz → 502 Hz, -9.0 dBFS) | -94.8 (P1 0.6 mA, 21799 Hz → 502 Hz, -13.9 dBFS) |
| 44.1 kHz | 16x | 8 V | -81.5 (P1 0.6 mA, 21799 Hz → 502 Hz, -27.3 dBFS) | -103.1 (P1 0.6 mA, 21799 Hz → 502 Hz, -5.6 dBFS) | -97.7 (T 0.35 mA, 21799 Hz → 502 Hz, -11.1 dBFS) |
| 44.1 kHz | 32x | 2 V | -77.5 (P1 0.6 mA, 21799 Hz → 502 Hz, -31.2 dBFS) | -99.7 (P1 0.6 mA, 21799 Hz → 502 Hz, -9.0 dBFS) | -94.8 (P1 0.6 mA, 21799 Hz → 502 Hz, -13.9 dBFS) |
| 44.1 kHz | 32x | 8 V | -81.5 (P1 0.6 mA, 21799 Hz → 502 Hz, -27.3 dBFS) | -103.1 (P1 0.6 mA, 21799 Hz → 502 Hz, -5.7 dBFS) | -97.7 (T 0.35 mA, 21799 Hz → 502 Hz, -11.1 dBFS) |

Every worst supported point is either the k140 floor (resampler) or internal folding at least 9.8 dB under its limit.
SPL headroom equals the margin there, so these points pass for any calibration up to 105 + margin dB SPL.

### What fails, and why
- **P1 at 8×, 8 V:** internal folding at every bias. The worst is 0.6 mA (the least cathode feedback): −21.1 / −21.3 dB at 48 / 44.1 kHz. The products near 3–4 kHz sit at about −97 to −98 dBFS, over the −100 dBFS floor as well.
  16× passes all of it by ≥ +37.0 dB (on the k140 floor at 48 kHz; at 44.1 kHz P1 0.45 / 0.6 mA are still internal
  folding, +40.8 / +37.0 dB).
- **T above 0.35 mA at 8 V:** the g1 grid-current onset (the borrowed Dempwolf–Zölzer law, unvalidated, #112 / #131)
  folds high-order products into the band, and the worst lands near 2–6 kHz, where the limit is −113 to −120 dBFS.
  - The onset sharpens with bias, so the factor needed rises fast: 0.375 mA needs 32× at 44.1 kHz; 0.4–0.45 mA 32×;
    0.5 mA 32× at 48 kHz and 64× at 44.1 kHz; 0.55–0.6 mA 64× (8 V margins +25.5 / +25.5 and +22.3 / +22.3 dB at 48 / 44.1 kHz). T 0.55–0.6 mA also fails at 2 V and 8× (−17.4 to −36.2 dB).
  - At 2 V every T bias passes at 16× (≥ +12.8 dB, T 0.6 mA, 44.1 kHz).
- **P2:** at 8 V the worst products are the resampler share at the band edge (a 19997 Hz tone, onto |2f − fs| = 8006 / 4106 Hz). They read the same at 16×, 32× and 64× (0.25 mA: −2.5 / −2.4 / −2.4 dB at 48 kHz, −9.7 / −9.6 / −9.6 dB at 44.1 kHz), so only a resampler with more stopband would move them. At 2 V, P2 passes at 8× and above.
- **Inputs above 20 kHz:** 1088 of the 1248 points fail. Every point fails from 21.5 kHz up (48 kHz) and from
  20.9 kHz up (44.1 kHz); only near 20.3 kHz do some pass. It is the interpolator's image, not folding, so 32× reads
  the same as 16× (item 6 above and the table "Inputs above 20 kHz").

### CPU (C++ core, one mono channel; µs per base-rate sample, ratio to 8×, % of real time at that rate)
| rate | case | 4× | 8× | 16× | 32× | 64× |
|---|---|---|---|---|---|---|
| 48 kHz | P1 0.45 mA 2 V | 6.0 (0.59×, 29 %) | 10.0 (1.00×, 48 %) | 19.7 (1.96×, 94 %) | 34.5 (3.44×, 166 %) | 59.7 (5.95×, 287 %) |
| 48 kHz | P1 0.45 mA 8 V | 7.4 (0.56×, 36 %) | 13.2 (1.00×, 63 %) | 21.7 (1.65×, 104 %) | 43.4 (3.29×, 209 %) | 67.9 (5.15×, 326 %) |
| 48 kHz | T 0.5 mA 8 V | 7.9 (0.62×, 38 %) | 12.8 (1.00×, 61 %) | 22.9 (1.79×, 110 %) | 42.0 (3.28×, 202 %) | 69.1 (5.40×, 332 %) |
| 44.1 kHz | P1 0.45 mA 2 V | 5.5 (0.48×, 24 %) | 11.4 (1.00×, 50 %) | 22.1 (1.93×, 97 %) | 41.4 (3.62×, 182 %) | 68.2 (5.97×, 301 %) |
| 44.1 kHz | P1 0.45 mA 8 V | 7.6 (0.56×, 34 %) | 13.6 (1.00×, 60 %) | 23.0 (1.70×, 102 %) | 44.9 (3.31×, 198 %) | 72.5 (5.35×, 320 %) |
| 44.1 kHz | T 0.5 mA 8 V | 8.2 (0.61×, 36 %) | 13.4 (1.00×, 59 %) | 22.7 (1.70×, 100 %) | 41.7 (3.12×, 184 %) | 70.7 (5.28×, 312 %) |

On an unloaded machine all of these are lower; the ratios carry over. For scale, #136 (from PR #133) gives standard 8×
on the CI x86-64 runner at 32–39 % of one core, mono. Scaled by the ratios above, that estimates 16× at about 53–76 %
and 32× at about 100–141 % of one core per mono channel there. This is an estimate, not a measurement.

### Accuracy at the new factors (`dk/validate.CRIT` unchanged)
| factor | supported settings passing (all reference settings run) | worst small-signal chain, dB | worst H2–H5, dB | worst op, relative | Newton unconverged |
|---|---|---|---|---|---|
| 8× | 12 / 12 (22 run) | 1.2e-04 | 0.0034 | 2.8e-09 | 0 |
| 16× | 12 / 12 (22 run) | 2.9e-05 | 0.0034 | 2.8e-09 | 0 |
| 32× | 12 / 12 (22 run) | 7.1e-06 | 0.0034 | 2.8e-09 | 0 |
| 64× | 12 / 12 (22 run) | 2.5e-06 | 0.0034 | 2.8e-09 | 0 |

### Eco and ADAA (#136)
This PR implements no ADAA and invents none: #136 owns it. From the tables above, at 8 V and the worst supported
case, an eco setting would have to close these gaps:

| factor | P1 (worst, 0.6 mA) | T 0.35 mA | T 0.1–0.3 mA (worst) | CPU relative to 16× |
|---|---|---|---|---|
| 8× | −21.1 / −21.3 dB | −22.5 / −20.3 dB | passes (≥ +41.4 dB) | 0.51–0.61 |
| 4× | −49.4 / −60.3 dB | −51.9 / −57.3 dB | −14.3 / −17.0 dB | 0.24–0.38 |

- An eco at 8× needs an ADAA that removes at least about 23 dB of the internal folding at 8 V, for P1 and T 0.35 mA,
  at both rates. At 4× it needs about 61 dB.
- #136 records an ideal-ADAA ceiling of up to about 47 dB at 8 V (PR #133, on the dBc metric). Whether a real stateful
  ADAA reaches that is #136's measurement.
- The criterion, the sweep (`inaudible_sweep.measure` takes any stage) and the guard here take an ADAA quality
  unchanged.

### #132 (T 0.3–0.6 mA)
The T rows of the tables above are #132's acceptance measurement under this criterion: every bias from 0.3 to 0.6 mA,
2 and 8 V, 44.1 and 48 kHz, at 8×, 16×, 32× and (8 V) 64×.
- At 2 V every T bias passes at 16×.
- At 8 V the supported range at 16× ends at 0.35 mA (item 2 above).
- #132 stays open for the decision on the range.

### The CI guard (`dk6as6/aliasing_guard.py`, jobs `aliasing (6as6, <quality>, <rate> Hz)`)
- **Points:** per rate and offered quality, `aliasing_guard.derive()` takes from `inaudible.json`, for each drive:
  - each mode's lowest-margin supported case;
  - each mode's top supported bias;
  - the lowest-margin tone(s) at each, on distinct 0.1 s bins (2 at standard, 1 at hq, for CI time).
  - `test_dk6as6_aliasing_guard.py` re-derives them.
- **Checks:** each point runs the Python stage in the 0.1 s CI window after 1.25 s.
  - It must pass the criterion, with no tolerance.
  - Its margin must not fall more than 1.5 dB below its committed baseline.
- Every key sits at the 4k position, which is the worst for every supported case in scope.
- In the 0.1 s window, the 4k filter's lower output peak and the unwindowed reading lift the start-up residue near
  20 Hz (to −109 dBFS re that peak). At the 2 V keys and the P1 8 V keys this residue is the worst product: they read
  up to about 15 dB under their 1 s margin, the lowest CI margin being +9.2 dB (P1 0.1 mA, 8 V). That is conservative,
  but at those keys the ratchet watches the residue, not the alias products.
- The tightest alias keys (T 0.35 mA at 8 V, +9.5 to +14.5 dB) read the same alias products as the 1 s grid, to within
  0.4 dB (table).

| rate | quality | key | tone Hz | CI-window margin dB | worst product Hz | 1 s grid margin dB |
|---|---|---|---|---|---|---|
| 48 kHz | standard | P1 0.1 mA, 8 V, filter 4k | 19997 | +9.19 | 20 | +23.94 |
| 48 kHz | standard | P1 0.1 mA, 8 V, filter 4k | 19977 | +9.20 | 20 | +23.96 |
| 48 kHz | standard | P1 0.6 mA, 2 V, filter 4k | 19997 | +15.09 | 20 | +30.06 |
| 48 kHz | standard | P1 0.6 mA, 2 V, filter 4k | 19977 | +15.09 | 20 | +30.08 |
| 48 kHz | standard | P1 0.6 mA, 8 V, filter 4k | 19637 | +10.31 | 20 | +24.76 |
| 48 kHz | standard | P1 0.6 mA, 8 V, filter 4k | 19997 | +10.03 | 20 | +25.11 |
| 48 kHz | standard | T 0.35 mA, 2 V, filter 4k | 19997 | +30.62 | 20 | +45.95 |
| 48 kHz | standard | T 0.35 mA, 2 V, filter 4k | 19977 | +30.63 | 20 | +45.96 |
| 48 kHz | standard | T 0.35 mA, 8 V, filter 4k | 19637 | +13.25 | 2040 | +12.95 |
| 48 kHz | standard | T 0.35 mA, 8 V, filter 4k | 19237 | +14.43 | 1600 | +14.76 |
| 48 kHz | hq | P1 0.1 mA, 8 V, filter 4k | 19997 | +9.20 | 20 | +23.95 |
| 48 kHz | hq | P1 0.6 mA, 2 V, filter 4k | 19997 | +15.10 | 20 | +30.07 |
| 48 kHz | hq | P1 0.6 mA, 8 V, filter 4k | 19997 | +10.04 | 20 | +25.12 |
| 48 kHz | hq | T 0.3 mA, 8 V, filter 4k | 19997 | +16.94 | 20 | +32.15 |
| 48 kHz | hq | T 0.35 mA, 2 V, filter 4k | 19997 | +30.63 | 20 | +45.96 |
| 48 kHz | hq | T 0.35 mA, 8 V, filter 4k | 19837 | +30.40 | 20 | +32.47 |
| 44.1 kHz | standard | P1 0.6 mA, 2 V, filter 4k | 19997 | +15.07 | 20 | +26.45 |
| 44.1 kHz | standard | P1 0.6 mA, 2 V, filter 4k | 19987 | +15.07 | 20 | +27.31 |
| 44.1 kHz | standard | P1 0.6 mA, 8 V, filter 4k | 19637 | +10.30 | 20 | +17.95 |
| 44.1 kHz | standard | T 0.35 mA, 2 V, filter 4k | 19997 | +30.64 | 20 | +36.25 |
| 44.1 kHz | standard | T 0.35 mA, 2 V, filter 4k | 19987 | +30.65 | 20 | +37.12 |
| 44.1 kHz | standard | T 0.35 mA, 8 V, filter 4k | 19997 | +9.53 | 5600 | +9.82 |
| 44.1 kHz | standard | T 0.35 mA, 8 V, filter 4k | 19987 | +10.46 | 5950 | +10.73 |
| 44.1 kHz | hq | P1 0.1 mA, 8 V, filter 4k | 19997 | +9.20 | 20 | +18.46 |
| 44.1 kHz | hq | P1 0.6 mA, 2 V, filter 4k | 19997 | +15.08 | 20 | +26.47 |
| 44.1 kHz | hq | P1 0.6 mA, 8 V, filter 4k | 19997 | +10.03 | 20 | +25.12 |
| 44.1 kHz | hq | T 0.3 mA, 8 V, filter 4k | 19997 | +16.94 | 20 | +22.87 |
| 44.1 kHz | hq | T 0.35 mA, 2 V, filter 4k | 19997 | +30.66 | 20 | +36.27 |
| 44.1 kHz | hq | T 0.35 mA, 8 V, filter 4k | 19997 | +23.53 | 4100 | +23.55 |

- **Tests:** `test_dk6as6_aliasing_guard.py` runs no stage. It checks:
  - every offered quality passes on the whole supported envelope (every case, drive, rate and tone of the screen, and
    every Python point);
  - Python reads the screen to `PARITY_DB` (0.01 dB);
  - the keys and tones are derived as above;
  - the baselines cover the keys and pass;
  - the comparison fails on a criterion failure, a ratchet regression, a missing point and a Newton failure.

### k140 on the Python stage, and the records made before this rework
- `stage.FIR = "k140"` is the default (#134, PR #133; this rework started from the same table and API).
- Every record made before this rework is at standard 8× / hq 16×, judged against T to 0.5 mA. The aliasing,
  envelope, grid and bounds records (`aliasing*.json`, `aliasing_quality.json`, `resampler_study.json`,
  `envelope*.json`, `grid*.json`, `grid_bounds.json`, `modes.json`, `bias_sweep.json`) use the shipped FIR. The
  validation records `results*.json` use k140, re-run in #134.
  - `stage.record_stage` (with `QUALITY_RECORDS` and the helpers' own `FIR`) builds that stage for the helpers that
    reproduce those records.
  - `stage.SUPPORTED_MA_RECORDS` / `supported_records` give the envelope they were judged against.
  - None of them is re-run here.
- #104's C++ core keeps those factors and that envelope until it follows this rework. Its harnesses
  (`cpp/validate_cpp_6as6.py`, `cpp/aliasing_6as6.py`) read `QUALITY_RECORDS` and `supported_records`.
- The live envelope tests (`test_dk6as6_envelope.py`, `envelope.excursion(..., offered=True)`) and the live validation
  tests re-check the offered stage, at 16× / 32× with k140, at the ends of the new envelope.
- #113's dBc guard, as #134 left it, is frozen as `aliasing_guard_dbc.py` with its baselines and
  `aliasing_guard_k140.json`, because `cpp/aliasing_6as6.py guardgrid` is built on its keys.

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

## Modes (#111)
The Distortion switch's T / P1 / P2, wired as `circuit.MODES` (one table for the DK netlist and the ngspice netlists).
The v1 mode set is the original unit's three positions, the issue's starting point; the Mastering Plus P3 and the
Super 15 PK / SQ positions are out of scope (no sourced reason to include one was found). The mode is fixed per run
(`Stage(mode=)`; smooth switching is #115). Rk is solved for the target current in each mode, so `bias` stays the
meter reading.

### Supported envelope (`stage.SUPPORTED_MA`; PR #121 review)
> **Since #112 this section describes the stage without grid current (`grid_hardness=0`,
> `stage.SUPPORTED_MA_NO_GRID`, `envelope.json`).** The default stage models g1 grid current, and with it T is
> supported to 0.5 mA, limited by the aliasing gate rather than the law ("Grid current (#112)", at the end).

`Stage` accepts a mode / bias only inside the supported envelope and raises `UnsupportedSettingError` outside it;
`experimental=True` runs any setting with an `ExperimentalWarning` (`Stage.supported` records which). A setting is
supported only where all three hold:
1. **in the law** (`python -m dk6as6.envelope` → `envelope.json`): for host signals bounded by 8 V peak (validate.py's
   largest drive), g1 never goes positive (the valve law has no grid current and no positive-g1 data; that is #112)
   and the plate never goes below the cathode. Both are read on the **interpolated internal-rate** voltages the solver
   sees, not the host signal: the interpolator's onsets overshoot (an 8 V 18 kHz sine from silence reaches 8.63 V,
   PR #121 re-review). The stimulus class: tones at 20, 50, 100, 200, 500 Hz and 1, 2, 5, 10, 15, 18, 20 kHz, each from
   silence at sine phase (a slope onset) and at cosine phase (a full 8 V step onset), gated off at a peak (a step
   offset), at standard 8× and hq 16×. The worst case everywhere is the step onset at 20–200 Hz;
2. **validated:** passing same-model ngspice references (`status() == "passed"`, standard and hq) at or bracketing
   each end of the range (`test_committed_validation_every_supported_setting_passes`);
3. **aliasing:** within `aliasing.QUALITY_GATE_DBC` at every supported current (below).

Worst excursion over the whole stimulus class (both qualities):

| mode, bias | max Vg1k | where | min Vak | in the law |
|---|---|---|---|---|
| P1 0.1 mA | −4.347 V | hq, 100 Hz step onset | 148.2 V | yes |
| P1 0.6 mA | −3.170 V | hq, 20 Hz step onset | 30.2 V | yes |
| T 0.1 mA | −6.131 V | hq, 100 Hz step onset | 138.5 V | yes |
| T 0.25 mA | −2.064 V | hq, 100 Hz step onset | 63.6 V | yes |
| T 0.3 mA | −0.855 V | hq, 100 Hz step onset | 35.1 V | yes |
| T 0.35 mA | **+0.298 V** | 100 Hz step onset (standard and hq); also positive on the 18 kHz slope onset (+0.080 V) | 3.8 V | **no** |

| mode | supported | why it ends there |
|---|---|---|
| P1 | 0.1–0.6 mA (the documented range) | holds all three over the whole range, with ≥ 3.17 V of g1 margin |
| T | **0.1–0.3 mA** | at 0.35 mA an 8 V step onset takes g1 to +0.30 V; the top end (0.3 mA, 0.85 V of margin) lies between the passing 0.25 and 0.35 mA references (no reference at 0.3 mA: one would be another multi-hour run, #125) |
| P2 | **none: experimental only** | our wiring hypothesis on the fit's weakest g3 region (with the #51 fit its doubling vanishes; refit is #123), its 2 V aliasing fails the gate at every current, and its 0.6 mA burst τ fails CRIT |

**Runtime guard.** The derivation covers its stimulus class only; larger inputs, or adversarial inter-sample peaks,
are not covered. So `Stage.process_internal` also counts every internal sample with Vg1k > 0 or Vak < 0
(`Stage.out_of_law`, `out_of_law_block`) and raises `OutOfLawWarning` for a block that has any, in every mode and
whether supported or not. It is a counter and a comparison per sample (realtime-safe, no change to the numerics;
P1's results are unchanged). Tested on the re-review's reproduction and on T just above its envelope.

Within T's envelope the DC point still extrapolates the fit in Vg2: Vg2k = 165 V at 0.1 mA and > 140 V below
~0.2 mA (the fit covers 60–140 V), and Vg3k = Vg2k is far above the fitted +12 V everywhere (where the share
saturates; "Where each mode sits in the fit"). That is disclosed, as P1's Vg1k = −5.28 V at 0.1 mA (just below the
fitted −5 V) has been since #110, not hidden behind the envelope. Panel consequence: in T the Bias knob's 0.3–0.6 mA
travel (knob 1 to 4.87) is not available; how the panel should handle that is #115 / Michael's call.

### What the sources say, and the wirings considered
**No source states how T, P1 or P2 is wired.** What is documented is each mode's character:
- T: "triode 'T' (even harmonics)" (M-ORIG §2); "The 'T' position provides a triode configuration, which generates
  mainly even-order harmonics" (SOS 2003). The whole-unit spec point is in T (M-MP p.10 / M-S15: 1 kHz, 0.25 mA,
  Function at T, 10 kΩ load, 0.2 % THD).
- P1: "pentode 'P1' (odd harmonics)" (M-ORIG §2); "a pentode configuration which creates odd-order harmonics" (SOS).
  "The P1 mode was also distinctly noisier than the triode mode" (SOS).
- P2: "an unusual version of pentode … more drastic distortion at high bias settings, especially when 'overdrive'
  is selected … frequency doubling effect" (M-ORIG §2), frequency doubling at 0.15–0.2 mA (M-ORIG §3);
  "far more drastic distortion effects, especially when used with high bias settings" and "Strangely, the P2 mode
  was also the quietest of all" (SOS); UA: P2 "unusual wiring", quieter than T / P1, polarity may invert at
  extremes, and no slight HF roll-off with the filter off (all as recorded in
  `prototypes/research/culture-vulture-controls.md`; the UA page returned HTTP 403 on 20 Nov 2026, so it was not
  re-read). The Mastering Plus manual puts frequency doubling on P2 / P3 "around 0.1mA" (M-MP p.6). "High bias" is
  low current on this reverse-acting control.

The only wiring-level statements found are forum speculation (GroupDIY, read 20 Nov 2026; S8 in #102):
- thread 42421, tardishead, 23 Dec 2010: "So in triode mode the screen grid and suppressor grid would be tied to
  the plate." and "P1 the suppessor to cathode and the screen through a resistor to B+" — "I would imagine", "Not
  sure about the Pentode 2 mode".
- thread 27906, Sorr, 30 May 2008, not identified as this unit ("I have forgotten the effect of the second pentode
  mode"): "The second pentode mode could alter the screen volts (probably lower)." and "It could also disconnect
  the screen bypass cap?"
- thread 42421, 0dbfs, 23 Dec 2010, from a photo: "Looks like the bias pot is dual ganged with the meter in series."

| mode | wiring | basis | label | confidence | status |
|---|---|---|---|---|---|
| T | **g2 + g3 strapped to the plate through 100 Ω (`Rstrap`), no screen supply** | tardishead (speculation); the 100 Ω stopper is #102's standard-practice value | inferred (speculation + practice) | low | **v1** |
| T | g2 → plate, g3 → cathode | the other common triode strapping [our note] | inferred | low | not implemented: in this model it differs only in how Ik splits between plate and screen, which are tied (below) |
| P1 | **g3 → cathode, g2 at a stiff 120 V** | tardishead (g3 → cathode); 120 V is the GE / Raytheon typical screen voltage (S9) | inferred | low | **v1, the default, unchanged since #118** |
| P1 | g3 → cathode, g2 through a dropping resistor to B+ | tardishead (speculation) | inferred | low | not implemented: no source gives the resistor or a bypass |
| P2 | **g3 → ground (so Vg3k = −Vk), g2 at the stiff 120 V** | #102 E9: of the hypotheses tried, the only one showing all three documented traits | **our hypothesis** | low | **v1** |
| P2 | lower screen volts, or the screen bypass removed | Sorr (speculation, not this unit) | hypothesis | very low | #102 E9 tried a 1 MΩ unbypassed and a 470k bypassed screen: neither shows the traits |
| P2 | g3 → g1 (signal on g3) | research note [our hypothesis] | hypothesis | very low | #102 E9: fails the traits |
| P2 | g3 → plate | — | hypothesis | — | #102 E9: no DC solution |

CG3 placement (a modelling choice, not a value): the 3.4 pF g3 input capacitance goes g3 → k in T and P2, as in
`fit6as6/results/6AS6_fit.inc`; only P1, where g3 is the cathode, keeps #118's g3 → g2. No mode shorts a valve
capacitance (tested). The ngspice subckt therefore comes in two variants, `models/6as6_dual.inc` (P1, unchanged)
and `models/6as6_dual_cg3k.inc` (T, P2), generated from the same expressions.

### Operating points and small signal (`python -m dk6as6.modes` → `modes.json`; DK, standard 8×)
Rk is solved for the target cathode current in each mode, so `bias` stays the meter reading (Ik = Ip + Ig2). In T
the whole of Ik flows through Ra (the screen is on the plate side), so Va falls by Ra = 270 V per mA against ~150 V
per mA in P1 (only Ip flows in Ra there).

| mode | bias | Rk | Va | Vk | Vg2k | Vg3k | Ip | Ig2 | gain 1 kHz | polarity | 20 kHz re 1 kHz |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P1 | 0.1 mA | 52.768 kΩ | 182.36 V | 5.277 V | 114.72 V | 0.000 V | 0.0653 mA | 0.0347 mA | 6.99 dB | inverting | -0.000 dB |
| P1 | 0.25 mA | 19.129 kΩ | 156.99 V | 4.782 V | 115.22 V | 0.000 V | 0.1593 mA | 0.0907 mA | 15.22 dB | inverting | -0.003 dB |
| P1 | 0.45 mA | 9.873 kΩ | 126.07 V | 4.443 V | 115.56 V | 0.000 V | 0.2738 mA | 0.1762 mA | 19.87 dB | inverting | -0.004 dB |
| P1 | 0.6 mA | 7.111 kΩ | 105.75 V | 4.267 V | 115.73 V | 0.000 V | 0.3491 mA | 0.2509 mA | 21.58 dB | inverting | -0.004 dB |
| T | 0.1 mA | 80.168 kΩ | 173.00 V | 8.017 V | 164.98 V | 164.980 V | 0.0707 mA | 0.0293 mA | 6.18 dB | inverting | +0.005 dB |
| T | 0.25 mA | 21.499 kΩ | 132.50 V | 5.375 V | 127.12 V | 127.118 V | 0.1766 mA | 0.0734 mA | 15.49 dB | inverting | -0.003 dB |
| T | 0.45 mA | 5.895 kΩ | 78.50 V | 2.653 V | 75.83 V | 75.834 V | 0.3173 mA | 0.1327 mA | 22.22 dB | inverting | -0.010 dB |
| T | 0.6 mA | 1.581 kΩ | 38.00 V | 0.949 V | 37.03 V | 37.033 V | 0.4211 mA | 0.1789 mA | 26.32 dB | inverting | -0.019 dB |
| P2 | 0.1 mA | 52.581 kΩ | 189.87 V | 5.258 V | 114.74 V | -5.258 V | 0.0375 mA | 0.0625 mA | -7.36 dB | non-inverting | -0.003 dB |
| P2 | 0.25 mA | 19.057 kΩ | 174.05 V | 4.764 V | 115.24 V | -4.764 V | 0.0961 mA | 0.1539 mA | -4.78 dB | non-inverting | -0.005 dB |
| P2 | 0.45 mA | 9.831 kΩ | 155.12 V | 4.424 V | 115.58 V | -4.424 V | 0.1662 mA | 0.2838 mA | -1.09 dB | non-inverting | -0.006 dB |
| P2 | 0.6 mA | 7.079 kΩ | 142.73 V | 4.247 V | 115.75 V | -4.247 V | 0.2121 mA | 0.3879 mA | 1.92 dB | non-inverting | -0.006 dB |

- **T reaches 0.1–0.6 mA** with Rk 80.2–1.58 kΩ (#102: "T 80k down to 1.6k"; tested). **P2** needs nearly P1's Rk
  (52.6–7.08 kΩ), since its screen takes the current its plate loses.
- **P2 is the quiet mode**, as documented (UA, SOS): its small-signal gain is 14–20 dB below P1's at every current
  (−7.4 to +1.9 dB against +7.0 to +21.6 dB). #102 E9 gave −4 to −6 dB at 0.15–0.3 mA; the DK stage gives −6.24 to
  −3.98 dB there.
- **P2 is non-inverting** where P1 and T invert: with g3 on ground, Vg3k = −Vk, so a rise on g1 raises Vk and cuts
  the plate's share faster than it raises the cathode current. UA records that P2's polarity "may invert at
  extremes"; here it is inverted relative to P1 / T at small signal at every current. This is a property of the hypothesis, not a
  measurement.
- **HF roll-off.** The UA manual records a slight HF roll-off with the filter off except in P2. The stage alone
  rolls off by at most 0.019 dB at 20 kHz in every mode (T at 0.6 mA), so if the unit's roll-off is real it comes
  from somewhere this stage does not model (the filter's "off" position, the output stage or #103's network), not
  from the mode wiring.

### Harmonic profile (1 kHz, 0.5 / 2 / 8 V peak in; `modes.json` has 0.05–8 V at ten currents)
"even − odd" is the energy of H2 + H4 over H3 + H5.

| mode | bias | 0.5 V: H2 / H3 dBc, THD | 2 V | 8 V | even − odd at 0.5 / 2 / 8 V |
|---|---|---|---|---|---|
| T | 0.1 mA | -58.4 / -96.8, 0.121 % | -46.2 / -72.6, 0.489 % | -32.4 / -45.8, 2.46 % | +38.4 / +26.4 / +13.4 dB |
| T | 0.25 mA | -53.9 / -94.3, 0.203 % | -41.7 / -70.0, 0.821 % | -27.8 / -42.5, 4.16 % | +40.4 / +28.3 / +14.7 dB |
| T | 0.45 mA | -46.6 / -96.4, 0.47 % | -34.4 / -71.5, 1.92 % | -14.5 / -23.7, 20.2 % | +49.8 / +37.1 / +9.3 dB |
| T | 0.6 mA | -38.2 / -68.2, 1.23 % | -18.2 / -22.7, 16.4 % | -8.1 / -26.2, 40.3 % | +30.0 / +4.0 / +16.6 dB |
| P1 | 0.1 mA | -58.5 / -86.1, 0.119 % | -45.9 / -61.6, 0.512 % | -23.9 / -29.0, 7.43 % | +27.6 / +15.7 / +5.2 dB |
| P1 | 0.25 mA | -76.2 / -81.3, 0.0177 % | -72.7 / -56.8, 0.147 % | -24.8 / -25.3, 8.17 % | +5.0 / -15.0 / +0.9 dB |
| P1 | 0.45 mA | -47.0 / -75.4, 0.446 % | -35.3 / -51.1, 1.75 % | -41.2 / -21.4, 8.9 % | +28.4 / +15.8 / -9.9 dB |
| P1 | 0.6 mA | -40.1 / -72.9, 0.987 % | -28.4 / -48.6, 3.84 % | -27.5 / -19.6, 11.9 % | +32.8 / +20.3 / -5.4 dB |
| P2 | 0.1 mA | -13.7 / -59.1, 20.5 % | -1.8 / -33.5, 81.3 % | 17.8 / 2.9, 829 % | +45.4 / +31.7 / +14.8 dB |
| P2 | 0.25 mA | -8.5 / -52.7, 37.8 % | 4.1 / -28.1, 161 % | 36.9 / 13.1, 7.45e+03 % | +44.2 / +32.3 / +22.9 dB |
| P2 | 0.45 mA | -7.7 / -45.6, 41 % | 6.3 / -20.5, 207 % | 25.4 / -18.5, 1.98e+03 % | +37.9 / +26.8 / +27.8 dB |
| P2 | 0.6 mA | -9.0 / -44.0, 35.5 % | 5.7 / -18.2, 194 % | 21.3 / -15.7, 1.23e+03 % | +35.0 / +23.9 / +26.1 dB |

- **T is even-dominant at every current and drive** (even − odd +4.0 to +57 dB over the whole grid), as documented
  ("even harmonics", M-ORIG; "mainly even-order harmonics", SOS). Tested (`test_mode_harmonic_character`).
- **P1 is not reliably "odd"**, as #102 already found: H3 leads only near the H2 null (0.25 mA at 2 V: −15 dB) and
  at 8 V from 0.3 mA up. Not asserted in the tests.
- **P2 doubles the frequency.** H2 ≥ H1 from 1.5 V at 0.2–0.6 mA, from 2 V at 0.15 mA and from 3 V at 0.1 mA; above
  2–6 V the fundamental *falls* as the drive rises (the "inverted dynamics" of M-MP's P2 / M-S15's SQ3):

  | P2 bias | H2 ≥ H1 from (drive grid 0.05–8 V) | H1 peaks at | H1 there |
  |---|---|---|---|
  | 0.1 mA | 3 V | 4 V | 1.241 V |
  | 0.15 mA | 2 V | 4 V | 1.362 V |
  | 0.2 mA | 1.5 V | 4 V | 1.362 V |
  | 0.25 mA | 1.5 V | 4 V | 1.293 V |
  | 0.3 mA | 1.5 V | 3 V | 1.267 V |
  | 0.35 mA | 1.5 V | 3 V | 1.264 V |
  | 0.4 mA | 1.5 V | 3 V | 1.273 V |
  | 0.45 mA | 1.5 V | 2 V | 1.313 V |
  | 0.5 mA | 1.5 V | 2 V | 1.437 V |
  | 0.6 mA | 1.5 V | 6 V | 2.009 V |

  Against the sources: frequency doubling appears, but **not specifically at the documented currents** (0.15–0.2 mA,
  M-ORIG §3; "around 0.1mA", M-MP p.6): the model doubles at every current, and needs *more* drive at 0.1 mA than at
  0.45 mA. Likewise the documented "more drastic distortion at high bias settings" (low current) is the reverse
  here: P2's THD at 0.5 V is 20.5 % at 0.1 mA and 41 % at 0.45 mA. No unit has been measured, so neither is a match
  or a mismatch to the inspiring unit, only to its manuals' wording.
- **P2 depends on the fit's g3 toe.** With the #51 parameters (`refit_51`, the same data with KR = 0, XA = 1) the same
  P2 circuit gives +4.6 to +5.8 dB more small-signal gain and **no doubling at 2 V** (H2 −4.4 / −2.2 / −0.4 dBc at
  0.15 / 0.25 / 0.45 mA, against +0.8 / +4.1 / +6.3 dBc with `dual_control`; scratch run, not committed). The
  mechanism (g1 and g3 acting on the plate current in opposite directions through Vk) is structural; its size is
  not settled by the fit.

### Where each mode sits in the fit (`modes.json` "swing" / "outside_fit"; fit ranges from `fit6as6/README.md`)
The #55 fit's data cover Ec2 60–140 V, Ec1 −5…0 V, Ec3 about −9…+12 V (to −14 V only at Ec2 = 120 V, Ec1 = −2 V),
Eb 20–435 V, with no positive-g1 data and no grid current (Ig1 = Ig3 = 0 here).

| | P1 | T | P2 |
|---|---|---|---|
| Vg3k | 0 (the fitted Ec3 = 0 families) | = Vg2k: +37…+165 V at rest, +17…+186 V on the 1 kHz sines while the stage stays physical (below): far above +12 V | = −Vk: −5.26 (0.1 mA) to −4.25 V (0.6 mA) at rest; −7.0…−2.6 V at 2 V, −12.4…−0.0 V at 8 V |
| what the fit does there | fitted | the share saturates at FMAX (z ≫ 0) and the KR term is off, so Ik = Ik0(Vg1k, Vg2k): Koren's law in g1 and g2 | the g3 cut-off toe, the fit's weakest region: toe points at Ec3 −4.7…−8 V read 1.5–3.1× high (#25; up to 2.4× for #51, not re-measured for #55); gm(g3) bias +3 % at −6…0 V, +74 % below −6 V; Ik drop past cut-off one fitted fraction (KR = 0.27) |
| Vg2k | 108–120 V (EC2 − Vk) | **> 140 V below ~0.2 mA** (165 V at 0.1 mA), **< 60 V above ~0.52 mA** (37 V at 0.6 mA), at rest | 108–120 V |
| Vg1k | −5.28 V at rest at 0.1 mA, i.e. just below the lowest fitted Ec1 at ≤ 0.15 mA (as since #110) | −8.0 V at rest at 0.1 mA; **positive** at 0.4 mA / 8 V, 0.45 mA / 8 V, 0.5 mA / 4 V, 0.6 mA / 2 V (below) | as P1 |
| Ic3 needed? | no: g3 is the cathode | not to first order: a real g3 current would flow into the same strapped node, and only the plate / screen split behind the 100 Ω Rstrap would change (Rstrap · Ig2 ≤ 18 mV at rest) | **no**: Vg3k = −Vk ≤ 0 always (Vk = Rk · Ik ≥ 0), so g3 never collects current |
| g3 refit needed? | no | no | **probably**, before P2's numbers are relied on (previous section) |

**T at high current and drive leaves the valve law entirely.** With Rk down to 1.6 kΩ there is little cathode
feedback, so g1 goes positive, and with g2 strapped to the plate the Koren term (floored at Vg2 = 1 mV) still
conducts as Vg2k reaches 0: E1 → Vg1 for a positive g1, so the screen keeps drawing current with the plate at or below
the cathode and the plate is driven far negative. Lowest Vak and highest Vg1k over the 1 kHz sines:

| T, bias | 0.5 V in | 2 V | 4 V | 8 V |
|---|---|---|---|---|
| 0.25 mA | Vg1k ≤ -5.18 V, Vak ≥ 124 V | Vg1k ≤ -4.60 V, Vak ≥ 114 V | Vg1k ≤ -3.85 V, Vak ≥ 100 V | Vg1k ≤ -2.44 V, Vak ≥ 72 V |
| 0.35 mA | Vg1k ≤ -3.69 V, Vak ≥ 97 V | Vg1k ≤ -2.93 V, Vak ≥ 82 V | Vg1k ≤ -1.96 V, Vak ≥ 61 V | Vg1k ≤ -0.16 V, Vak ≥ 17 V |
| 0.4 mA | Vg1k ≤ -2.99 V, Vak ≥ 83 V | Vg1k ≤ -2.15 V, Vak ≥ 65 V | Vg1k ≤ -1.07 V, Vak ≥ 40 V | **Vg1k ≤ +0.57 V, Vak ≥ -24 V** |
| 0.45 mA | Vg1k ≤ -2.33 V, Vak ≥ 69 V | Vg1k ≤ -1.40 V, Vak ≥ 48 V | Vg1k ≤ -0.21 V, Vak ≥ 18 V | **Vg1k ≤ +0.70 V, Vak ≥ -100 V** |
| 0.5 mA | Vg1k ≤ -1.70 V, Vak ≥ 55 V | Vg1k ≤ -0.67 V, Vak ≥ 30 V | **Vg1k ≤ +0.54 V, Vak ≥ -12 V** | **Vg1k ≤ +0.88 V, Vak ≥ -213 V** |
| 0.6 mA | Vg1k ≤ -0.53 V, Vak ≥ 26 V | **Vg1k ≤ +0.57 V, Vak ≥ -29 V** | **Vg1k ≤ +0.95 V, Vak ≥ -259 V** | **Vg1k ≤ +1.56 V, Vak ≥ -746 V** |

A real valve would draw grid current there (clamping g1 near 0 V through Rgs and the coupling network), and no
electrode below the cathode collects electrons; this model has neither. **These T settings (≥ 0.4 mA at 8 V,
≥ 0.5 mA at 4 V, 0.6 mA from 2 V) are not physical**, whatever the DK and ngspice agreement below says (both run
the same law). On these 1 kHz steady-state sines T stays at Vg1k ≤ −0.16 V and Vak ≥ 17 V up to 0.35 mA at every drive
up to 8 V; with tone onsets from silence the limit is 0.3 mA ("Supported envelope"). Fixing it needs
either a g1 grid-current model for the 6AS6 (no sheet gives one) or a positive-screen-voltage condition on the
cathode current: both change the #55 valve law, so they are left to Michael (below), not invented here.


### Validation against the same-model ngspice references (`dk/validate.CRIT` unchanged)
References `spice6as6/reference_dual_{T,P2}_{0.1,0.25,0.45,0.6}mA.json` and `reference_dual_T_0.35mA.json` (added for
the top of T's envelope; 2.5 h, of which ~2 h is the burst's silent start, as #120's 0.6 mA): ngspice 41, the conda-forge osx-64 build of
#118 / #120 (under Rosetta), `spice/run_refs.SOLVER` unchanged, the netlists generated from `circuit.VALUES` and
`circuit.MODES` (`stage_6as6_{T,P2}.cir` are the committed 0.45 mA copies). Each took 1.3–2.4 h, all eight run
side by side (`run_refs.py dual/T dual/P2`); none needed the solver settings touched. `python -m dk6as6.validate`
→ `results_T.json`, `results_P2.json` (P1's `results.json` is unchanged, CPU time apart; it and `aliasing.json` were
regenerated by this branch and compared).

**T**

| criterion | 0.1 mA std 8× | 0.1 mA hq 16× | 0.25 mA std 8× | 0.25 mA hq 16× | 0.35 mA std 8× | 0.35 mA hq 16× | 0.45 mA std 8× | 0.45 mA hq 16× | 0.6 mA std 8× | 0.6 mA hq 16× |
|---|---|---|---|---|---|---|---|---|---|---|
| op point, worst rel. (< 0.001) | 1.8e-09 | 1.8e-09 | 1.1e-09 | 1.1e-09 | 2.8e-09 | 2.8e-09 | 1.5e-09 | 1.5e-09 | 7.9e-09 | 7.9e-09 |
| small signal, stage (< 0.1 dB) | 0.00010 dB | 0.00002 dB | 0.00005 dB | 0.00001 dB | 0.00012 dB | 0.00003 dB | 0.00019 dB | 0.00005 dB | 0.00035 dB | 0.00009 dB |
| small signal, whole chain (< 0.1 dB) | 0.00076 dB | 0.00108 dB | 0.00084 dB | 0.00110 dB | 0.00088 dB | 0.00111 dB | 0.00092 dB | 0.00112 dB | 0.00101 dB | 0.00114 dB |
| H2–H5 worst tested, ref > −90 dBc (< 0.1 dB) | 0.00048 dB (H2, 0.05 V; 8 tested) | 0.00050 dB (H5, 8 V; 8 tested) | 0.00037 dB (H4, 8 V; 8 tested) | 0.00036 dB (H4, 8 V; 8 tested) | 0.00180 dB (H5, 8 V; 9 tested) | 0.00178 dB (H5, 8 V; 9 tested) | 0.00390 dB (H4, 2 V; 5 tested; 8 V not compared: exp cap) | 0.00391 dB (H4, 2 V; 5 tested; 8 V not compared: exp cap) | 0.00016 dB (H2, 0.05 V; 3 tested; 2 V, 8 V not compared: exp cap) | 0.00016 dB (H2, 0.05 V; 3 tested; 2 V, 8 V not compared: exp cap) |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.067 % / 0.0075 % | 0.067 % / 0.0075 % | 0.069 % / 0.0076 % | 0.068 % / 0.0076 % | 0.069 % / 0.0077 % | 0.069 % / 0.0077 % | 0.070 % / 0.0077 % | 0.069 % / 0.0077 % | not compared: exp cap (1.25 % / 15.84 %) | not compared: exp cap (1.25 % / 15.84 %) |
| Newton non-converged (sines + burst) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 4 it.) | 0 (max 4 it.) | 0 (max 6 it.) | 0 (max 5 it.) |
| status (validate.status) | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | inconclusive, unsupported | inconclusive, unsupported | inconclusive, unsupported | inconclusive, unsupported |

**P2**

| criterion | 0.1 mA std 8× | 0.1 mA hq 16× | 0.25 mA std 8× | 0.25 mA hq 16× | 0.45 mA std 8× | 0.45 mA hq 16× | 0.6 mA std 8× | 0.6 mA hq 16× |
|---|---|---|---|---|---|---|---|---|
| op point, worst rel. (< 0.001) | 1.7e-09 | 1.7e-09 | 2.7e-09 | 2.7e-09 | 2.4e-09 | 2.4e-09 | 2.6e-09 | 2.6e-09 |
| small signal, stage (< 0.1 dB) | 0.00006 dB | 0.00001 dB | 0.00009 dB | 0.00002 dB | 0.00011 dB | 0.00003 dB | 0.00011 dB | 0.00003 dB |
| small signal, whole chain (< 0.1 dB) | 0.00084 dB | 0.00110 dB | 0.00087 dB | 0.00111 dB | 0.00087 dB | 0.00111 dB | 0.00087 dB | 0.00111 dB |
| H2–H5 worst tested, ref > −90 dBc (< 0.1 dB) | 0.02467 dB (H4, 0.5 V; 12 tested) | 0.02468 dB (H4, 0.5 V; 12 tested) | 0.03942 dB (H4, 0.5 V; 12 tested) | 0.03944 dB (H4, 0.5 V; 12 tested) | 0.04881 dB (H4, 0.5 V; 13 tested) | 0.04882 dB (H4, 0.5 V; 13 tested) | 0.06713 dB (H4, 0.5 V; 13 tested) | 0.06714 dB (H4, 0.5 V; 13 tested) |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.059 % / 0.0066 % | 0.059 % / 0.0066 % | 0.058 % / 0.0064 % | 0.058 % / 0.0064 % | 0.057 % / 0.0063 % | 0.057 % / 0.0064 % | 0.057 % / 0.0063 %; τ 53 % (**> 1 %**; tail fit 0.03 %) | 0.056 % / 0.0063 %; τ 53 % (**> 1 %**; tail fit 0.03 %) |
| Newton non-converged (sines + burst) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) | 0 (max 3 it.) |
| status (validate.status) | **passed**, unsupported | **passed**, unsupported | **passed**, unsupported | **passed**, unsupported | **passed**, unsupported | **passed**, unsupported | failed, unsupported | failed, unsupported |

- **Statuses** (`validate.status`): a case is `passed` only if every criterion passed, `failed` if any failed, and
  `inconclusive` if a criterion could not be compared (it reads `None`). A non-pass is never counted as a pass, and
  `python -m dk6as6.validate` fails if any *supported* setting is not `passed`. Unsupported settings are run as
  experimental and recorded with `"supported": false`.
  - *Inconclusive: ngspice's exp() cap* (T 0.45 and 0.6 mA, outside T's envelope). ngspice 41 clamps a B-source
    `exp()` argument at 227.9559 (checked: `ln(1+exp(300))` evaluates to 227.9559), so the subckt's Koren term
    `ln(1 + exp(KP (1/MU + Vg1k/max(Vg2k, 1 mV))))` stops being the DK law (an exact softplus) once Vg1k / Vg2k passes
    ~1.5: a positive g1 over a screen near 0 V. `validate.exp_capped` detects it on exactly the unphysical cases
    (0.45 mA / 8 V, 0.6 mA / 2 and 8 V, the 0.6 mA burst), never inside any supported envelope (tested). There the two
    laws disagree (up to 13.8 dB in H1; burst shift 1.25 %, trajectory 15.8 % at 0.6 mA), so those criteria are not
    compared and the cases are `inconclusive`. Emitting a stable softplus in the subckt and regenerating would make the
    reference agree, but only about a stage that is outside the valve law anyway (it would not make these settings
    supported); it is left for the C++ port's reference work.
  - *Failed: P2's recovery τ at 0.6 mA* (CRIT's 1 %: DK 41.7 ms against ngspice 87.8 ms). This is the only finite τ
    among the P1 and P2 references, and the metric, not the stage, is what differs: it starts from the 30.5 ms sample,
    whose cycle mean still straddles the burst's end (ngspice +0.110 mV, DK +0.187 mV above rest), and takes 1/e of
    that. From 31 ms on the two tails agree to ≤ 0.28 % at every current, and a log-linear fit over 31–118 ms gives the
    same decay constant to ≤ 0.03 % (`tail_tau_fit_rel`; 87.61 ms ngspice, 87.58 ms DK). The criterion and the shared
    metric (`spice/run_refs.burst_metrics`, also the 12AX7 stage's and P1's) are left as they are, the case is
    `failed`, and CI carries it: `test_p2_0p6mA_burst_recovery_tau_crit` is a strict xfail (it turns CI red if it
    starts passing) beside `test_p2_0p6mA_burst_shift_trajectory_and_tail`, which passes. P2 is unsupported anyway.
    *Resolved by #112 (#124): with the full-window tail τ as the recovery criterion P2 0.6 mA passes (0.031 %); the
    xfail is now a real assertion ("Grid current (#112)", "Burst recovery measure").*
- **P2's H4 at 0.5 V** is the largest tested deviation of any mode (0.025–0.067 dB, rising with current; P1 and T
  ≤ 0.004 dB), within the 0.1 dB criterion: the analysis window, not the method or the reference (TIGHT, below).
- **Newton:** zero non-converged samples on the 0.05 / 0.5 / 2 / 8 V sines and the 4 V burst in every mode at every
  current, standard and hq; at most 3 iterations in P1, P2 and T up to 0.35 mA, 4 in T at 0.45 mA, 6 at 0.6 mA (the
  unphysical cases). Same contract as `dk6as6/solver.py` / #7; nothing in the solver changed.
- **Silence:** `test_silence_has_no_startup_thump` (< 1 µV over 100 ms) passes in all three modes at 0.1, 0.45 and
  0.6 mA, standard and hq.
- **CI** runs P1 as before, T at standard at 0.25 mA (its highest reference inside the envelope), the P2 0.6 mA burst,
  the envelope's onset checks, and checks the
  committed `results_*.json` statuses; `python -m dk6as6.validate` runs every mode at every bias, standard and hq.

### TIGHT convergence of the new references (`spice6as6/convergence_dual_{T,P2}_*mA.json`)
Partial; completing it is #125. Started for T and P2 at 0.25 and 0.45 mA only (`run_refs.py --convergence dual/T:0.25 ...`, `run_refs.TIGHT`, ngspice
41), to bound the run time; 0.1 and 0.6 mA were not run.

| change under TIGHT | T 0.25 mA | T 0.45 mA | P2 0.25 mA | P2 0.45 mA |
|---|---|---|---|---|
| op point, AC | **not run** (below) | 0 / 0 dB | 0 / 0 dB | 0 / 1e-8 dB |
| H2–H5 with reference above −90 dBc, max | — | 8.2e-3 dB (H4, 2 V, −80.0 dBc) | 3.5e-3 dB (H5, 2 V, −68.5 dBc) | 3.2e-3 dB (H3, 0.05 V, −85.6 dBc) |
| H4 at 0.5 V (P2's largest DK deviation) | — | — | 1.3e-3 dB | 8.0e-4 dB |
| burst shift / trajectory | — | 1.4e-7 / 1.0e-7 | **not run** (below) | **not run** |

- **T 0.25 mA:** ngspice's TIGHT transient aborts at its first step ("Timestep too small", node in1, at 2.5 ns), the
  first-step failure #118 met with other settings; the reference itself runs at the unchanged #5 settings. Nothing
  was loosened to make TIGHT run.
- **P2:** both TIGHT runs finished their main analyses, then were terminated abnormally (not by ngspice) 3.5 h in,
  during the burst. Their op / AC / sine outputs were analysed with `run_refs`'s own functions (`load_wrdata`,
  `harmonics`) into the convergence JSONs, which say so in a `note` and have `burst: null`. Rerunning would take
  another 4+ h and was not done.
- So above −90 dBc these references move by ≤ 8.2e-3 dB (T), ≤ 3.5e-3 dB (P2), under a tenth of the 0.1 dB criterion,
  but more than P1's ≤ 1.1e-3 dB.
- **P2's H4 at 0.5 V is not reference error** (TIGHT moves it by ≤ 1.3e-3 dB against a DK deviation of 0.025–0.067
  dB), and not the DK time step (standard and hq agree to 1e-5 dB). It is the analysis window: P2 rectifies strongly
  (H2 −8.5 dBc at 0.5 V), so the 0.1 s coupling caps are still settling, and the DK's input starts through the
  interpolator's ramp where ngspice's starts abruptly. The same reference reads H3 0.04 dB and H5 5.7 dB apart over
  0.3–0.4 s and 0.4–0.5 s, and the DK's H4 moves by 0.037 dB from a 0.5 s to a 1 s sine (scratch runs). A setup
  limit for strongly rectifying modes, within the criterion; not changed here.

### Aliasing (`python -m dk6as6.aliasing` → `aliasing_{T,P2}.json`; 8× FIR, 48 kHz, full sweep, 1 s windows)
Worst total over the sweep (1037–19037 Hz):

| mode | 0.1 mA, 2 V | 0.1 mA, 8 V | 0.25 mA, 2 V | 0.25 mA, 8 V | 0.45 mA, 2 V | 0.45 mA, 8 V | 0.6 mA, 2 V | 0.6 mA, 8 V |
|---|---|---|---|---|---|---|---|---|
| P1 | -140.31 | -112.50 | -147.99 | -107.18 | -130.37 | -100.96 | -123.59 | -98.44 |
| T | -140.73 | -125.25 | -136.55 | -121.08 | -129.32 | -56.71 (18037 Hz) | -55.38 | -54.77 (18037 Hz) |
| P2 | -97.25 | -81.21 (16037 Hz) | -91.24 | -61.55 | -89.01 | -63.50 | -89.53 | -65.77 |

(dBc; worst tone 19037 Hz unless shown. P1 is #120's sweep, reproduced byte for byte. T swept since the review at
0.3 mA, the top of its envelope: −135.02 dBc at 2 V, −119.64 dBc at 8 V; and at 0.35 mA: −133.35 / −117.98 dBc. CI checks
the regression baselines at T 0.3 mA / 8 V and P2 0.25 mA / 8 V, and the gate on the committed sweeps.)

- **Two separate things** (PR #121 review). `aliasing.LIMIT_DBC` (the full sweep's worst + 3 dB, as for P1) are
  regression baselines: chosen from the measurements, they show reproducibility, not quality. The quality gate is
  `aliasing.QUALITY_GATE_DBC`, the shipped 12AX7 stage's committed budget for the same setting, filter and drive
  (`dk/aliasing.LIMIT_DBC["standard", "fir"]`: −118.3 dBc at 2 V, −48.6 dBc at 8 V, #6 / #96), which no 6AS6 result
  chose. It is an engineering budget, not an audibility threshold: the repo has no calibrated listening criterion
  and #96 explicitly declined to invent a dBc cutoff for "inaudible", so neither does this. Every supported setting
  must meet it (`test_every_supported_setting_meets_the_quality_gate`).
- **P1 and T (to 0.3 mA) meet the gate** with room: T ≤ −135.0 dBc at 2 V and ≤ −119.6 dBc at 8 V (all at the
  resampler floor; internal ≤ −188 dBc), better than P1 at the same currents. T's unphysical cases are at −55 to
  −57 dBc; they are outside the envelope, and their baselines are a regression ratchet only.
- **P2 fails the gate at 2 V at every current** (−89.0 to −97.3 dBc against −118.3), which is one reason it is
  experimental only (`test_p2_fails_the_quality_gate`). At 2 V it is all resampler (internal ≤ −171 dBc); at 8 V
  (−61.5 to −81.2 dBc) internal and resampler are both large. hq (16×) removes the internal part (0.25 mA / 8 V:
  internal −63.1 → −127.1 dBc) but not the resampler part (−66.7 → −66.9 dBc; 0.45 mA / 2 V −89.0 dBc at both;
  scratch run), so higher oversampling is not a demonstrated fix.
- **Supplementary, output-relative** (`python -m dk6as6.aliasing --output-relative` →
  `aliasing_output_relative.json`): the dBc denominator is the fundamental, which P2's doubling suppresses at 1 kHz,
  so the same alias total was also taken relative to the stage's whole in-band output (fundamental + harmonics,
  20 Hz–20 kHz) at every worst tone. At those tones (16–19 kHz) every harmonic is above 20 kHz, so the in-band output
  *is* the fundamental and the two figures are equal in every mode (harmonics +0.00 dB re H1). **P2's poor figures
  are therefore not a denominator effect**: its 19 kHz alias energy is −61.5 to −97 dB below everything the stage puts
  in the audio band.

### For Michael
1. **T is supported only to 0.3 mA without grid current.** Above it an 8 V step onset takes g1 positive, and from 0.4 mA a steady 8 V drive
   also drives the plate below the cathode (−55 dBc aliasing). Making 0.3–0.6 mA usable in T needs a positive-g1 /
   grid-current treatment (#112) or a screen-voltage condition in the #55 law (no data for either). The Bias knob in
   T (knob 1 to 4.87) is a panel question (#115). The runtime guard flags any excursion in the meantime.
   *#112 adopted the borrowed g1 grid-current model (unvalidated, labelled); with it, the default, T is supported
   to 0.5 mA: "Grid current (#112)" below.*
2. **P2 is experimental only:** our hypothesis, the fit's weakest region (the #51 parameters remove its doubling at
   2 V; refit #123), its doubling not confined to the documented 0.15–0.2 mA and its distortion growing with current
   against the manuals, and its 2 V aliasing above the gate (the resampler's stopband; not fixed by hq). (Its 0.6 mA
   burst τ failure was the metric: resolved by #112, #124.)
3. **The burst τ metric** started on a sample that straddles the burst's end, and read the padded trace end: #124,
   resolved for the 6AS6 stage by #112.
4. **TIGHT** is partial: #125 (T 0.45 mA complete; P2 0.25 / 0.45 mA main analyses only; T 0.25 mA fails at
   ngspice's first TIGHT step; 0.1 / 0.3 / 0.35 / 0.6 mA not run).
5. **The Rosetta ngspice hang** is #126; every call now has a wall-clock timeout with kill and a bounded reap.
   **macOS fullstage headroom** is #127.
6. **T wiring alternative** (g3 → cathode instead of the plate) and **P1 with a screen resistor** were not built;
   neither has a sourced value.
7. **#119's LPF** (`dk6as6/lpf.py`, `RESULTS-lpf.md`) forwards `mode=` to this Stage, but its measured evidence is P1
   only; T / P2 through the filter are not measured.

## Grid current (#112)
**Decision: the stage models g1 grid current by default, with an explicitly unvalidated law and a "grid hardness"
control** (`Stage(grid_hardness=)`, `pentode.HARDNESS` = 1; 0 is the #111 stage bit for bit: the same 2-port
circuit and solver, `results.json` reproduced exactly). Everything above this section is grid hardness 0 unless it
says otherwise. Basis: Michael asked for accuracy first, and for #112 to lean toward modelling grid current. A real
6AS6 draws g1 current as g1 nears the cathode. Ig1 = 0 is a known error wherever g1 gets there, and in T above
0.35 mA it made the stage unphysical (g1 to +1.56 V, the plate to −746 V; "Modes", above).

### The law (`dk6as6/pentode.py`: `grid()`, `grid_current()`, `currents_grid()`)
- `Ig1 = Gg · (softplus(Cg·Vg1k)/Cg)^ξ`: Dempwolf & Zölzer, *A physically-motivated triode model for circuit
  simulations*, DAFx-11, eq. (11) (https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf; the equation and Table 1
  were re-read from the paper for #112). It is C∞ (softplus), so Newton sees continuous Jacobians and needs no
  junction limiting.
- **ξ = 1.314, Cg = 9.901 /V: DZ's 12AX7 RSD-1 values** (Table 1; `dk/triode.DZ_12AX7_RSD1`, imported), i.e. the
  12AX7's shape of onset. **Borrowed, not 6AS6 data.**
- **Gg = hardness × 1.2808 × 6.177e-4 = 7.912e-4 A/V^ξ** at hardness 1. **1.2808 = `pentode.PERVEANCE_RATIO`, the
  ratio of the two fitted cathode coefficients:** the 6AS6 fit's 2/KG1 = 2.872e-3 A/V^1.49 (#55 `dual_control`, where
  Ik0 = (2/KG1)·E1^EX) over RSD-1's G = 2.242e-3 A/V^1.26. They are compared at 1 V of effective control voltage,
  where the different exponents drop out. **Assumption (ours): grid current scales with cathode emission at the
  12AX7's grid geometry.** It is a guess to replace with a measurement. Hardness is the control for that guess, not a
  fitted value.
- **No 6AS6 grid-current data exists to check it.** No reviewed 6AS6 / 5725 sheet has positive-g1 curves
  (`research/6as6-datasheets.md`). Mullard M8196 pp. D1–D3, re-read for #112, give only reverse grid current
  (≤ 0.1 µA at Vg1 = −2 V, Rg1 = 100 kΩ; ≤ 1 µA at Vh = 7.5 V, Vg1 = −10 V) and no forward spot value.
- **DZ's Ig0 (8.025e-8 A, "added due to stability reasons") is left out.** It is a constant, not a current law, and
  through the 1.07 MΩ grid circuit it would move the grid DC by −86 mV at cut-off.
- **Where it enters: out of the fit's cathode current, before the plate/screen split**, as recommended in
  `research/grid-current.md`: Ip = (Ik − Ig1)·f, Ig2 = (Ik − Ig1)·(1 − f), with Ik and the share f the #55 law's.
  The cathode current is unchanged (Ik = Ip + Ig2 + Ig1, tested), and Ig1 depends on Vg1k only, as DZ measured for
  the 12AX7 ("only small dependency on the anode voltage").
- Values (Vg1k → Ig1): −1 V 87 pA, −0.5 V 58 nA, −0.16 V 4.3 µA, 0 V 24.0 µA, +0.5 V 0.32 mA, +1 V 0.79 mA, +2 V
  1.97 mA. With the 68k stopper, 24 µA at Vg1k = 0 is already a 1.6 V drop, so the grid clamps within ~0.1–0.2 V
  of the cathode.
- **The law's limit, kept visible rather than patched:** where the screen is near the cathode and g1 slightly
  negative, the Koren-form Ik falls below DZ's sub-zero grid tail, so Ik − Ig1 < 0 (negative plate and screen
  current). DZ state the same kind of limit for their triode ("V_g > 0 and small anode voltages, say V_a < 20 V, the
  results are obviously not correct"). A smooth clamp would be a new, invented law, so there is none. Instead the
  supported envelope requires Ip, Ig2 ≥ 0 (next section).
- **Tests** (`dk6as6/tests/test_pentode.py`): `currents_grid` against `reference_grid` (model6as6.dual_ip_ig2 for Ik
  and f, DZ eq. (11) in numpy) to 1e-12. Ip and Ig2 are differences, so, as `dk/tests/test_triode.py`'s Ia = Ik − Ig,
  their error is taken relative to the sum of magnitudes. Also tested: the Jacobian against central differences;
  cathode-current conservation; and hardness 0 equal to `currents()` exactly. Ig3 stays 0: no v1 mode needs it (#111).

### DK and ngspice
- **DK:** a third valve current port g → k (`circuit.nli(grid=True)`), so K is 4×3, and a 3×3 Newton step
  (`solver.solve_grid`; Cramer, J's g1 row is (dIg1/dVg1k, 0, 0, 0)). TOL_V / TOL_F / MAX_IT / LS_MAX and the
  non-convergence contract are unchanged. With hardness 0 the stage builds the #111 2-port circuit and calls the #111
  `solve()`: P1 / T / P2 outputs are bit-identical (checked against `results.json`'s 2 V and 8 V harmonics).
- **ngspice:** model `dualg` (`spice6as6/run_refs.py`) is a new subckt, `models/6as6_dualg{,_cg3k}.inc`, with
  `Bg1 G1 K` (softplus written overflow-free as max(x,0)+ln(1+exp(−|x|))) and Bp / Bg2 carrying (Ik − Ig1)·share.
  **The `dual` subckts and every #111 reference are unchanged** (regenerated byte-identical; tested). The operating
  point also writes ig1 = (v(gl) − v(g))/Rgs. `test_grid_subckt_is_the_dk_valve_law_with_grid_current` evaluates the
  three B-sources in Python against `currents_grid`.
- **Static Ig–Vgk sweep** (`python -m dk6as6.grid static` → `grid.json` "static"; ngspice `.dc` of the dualg subckt,
  Vg1k −3…+2 V in 50 mV, at (Vg2k, Vg3k, Vak) = (120, 0, 150) V and (50, 50, 50) V): DK and ngspice agree on Ig1, Ip
  and Ig2 to within 2.5 % of ngspice's own Newton tolerance on source currents (reltol·|I| + abstol). In plain
  numbers: Ig1 to 1.0e-8 relative where it is above 1e-15 A, and Ip / Ig2 to ≤ 1.5e-8 of Ik.

### What it does to the envelope (`python -m dk6as6.grid envelope` → `grid.json` "envelope"; DK, standard 8×)
**P1: nothing measurable.** Its unbypassed Rk (7–53 kΩ) keeps g1 at or below −3.28 V on every signal up to 8 V peak,
1 kHz or any onset to 19 kHz, so Ig1 ≤ 1.2e-23 A. With grid current on, P1's outputs differ from the #111 stage only
by rounding (the CI cases below, and P1's references).

**T: the grid current is what keeps T physical above 0.35 mA.** Here are the highest Vg1k, the lowest Vak and the THD
over the last 100 cycles of a 0.5 s, 1 kHz sine, with grid current (compare "Modes", "T at high current and drive"
above, without it):

| T, bias | 2 V | 4 V | 6 V | 8 V |
|---|---|---|---|---|
| 0.1 mA | −7.57 V, 159 V, 0.5 % | −7.14 V, 154 V, 1.0 % | −6.74 V, 148 V, 1.6 % | −6.34 V, 142 V, 2.5 % |
| 0.25 mA | −4.60 V, 114 V, 0.8 % | −3.85 V, 100 V, 1.7 % | −3.13 V, 86 V, 2.8 % | −2.44 V, 72 V, 4.2 % |
| 0.35 mA | −2.93 V, 82 V, 1.2 % | −1.96 V, 61 V, 2.5 % | −1.04 V, 40 V, 4.0 % | −0.25 V, 19 V, 6.0 % |
| 0.4 mA | −2.15 V, 65 V, 1.5 % | −1.07 V, 40 V, 3.1 % | −0.22 V, 18 V, 4.8 % | −0.08 V, 15 V, 7.3 % |
| 0.45 mA | −1.40 V, 48 V, 1.9 % | −0.29 V, 20 V, 3.8 % | −0.10 V, 15 V, 5.4 % | −0.04 V, 14 V, 10.0 % |
| 0.5 mA | −0.67 V, 30 V, 2.6 % | −0.13 V, 16 V, 3.9 % | −0.05 V, 14 V, 6.8 % | −0.01 V, 13 V, 13.3 % |
| 0.6 mA | −0.15 V, 16 V, 4.1 % | −0.06 V, 14 V, 6.4 % | −0.02 V, 13 V, 10.2 % | +0.02 V, 12 V, 19.4 % |

(All 10 currents 0.1–0.6 mA are in `grid.json`; up to 0.3 mA Ig1 < 3e-12 A and nothing changes.) Without grid current
the same 0.6 mA / 8 V point reached g1 = +1.56 V, the plate −746 V and 40.4 % THD. With it, g1 is clamped at
≤ +0.02 V, the plate stays ≥ 12 V above the cathode and Ip + Ig2 ≥ 0.88 µA. Newton: 0 non-converged, at most 4
iterations.

**Tone onsets** (PR #121's review case: an 8 V zero-phase sine from silence, 30 ms, at 1037 / 5037 / 10037 / 15037 /
18037 / 19037 Hz, standard and hq, at every supported current of P1 and T): **every one stays in the law** (plate above
the cathode, Ip + Ig2 > 0). The review's T 0.35 mA / 18 kHz case, which took g1 to +0.0797 V without grid current,
now peaks at −0.129 V (standard and hq). The worst onset of all, T 0.6 mA at 18 kHz, takes g1 to +0.20 V, the plate
down to 6.8 V and Ip + Ig2 down to 0.95 µA. Newton converges on every sample, but **T's onsets at 0.35–0.6 mA take up
to 15 iterations at standard (up to 11 at hq)**, against at most 6 in P1 and on any 1 kHz signal: the grid current
switching on within a few samples at 15–19 kHz. That cost is a real-time budget item for the C++ port (#104), not a
correctness issue (MAX_IT = 50).

**Fidelity caveat for T above ~0.35 mA at high drive.** The plate swings down to 7–20 V while g1 is near or just above
0 V. That is below the #55 fit's lowest plate voltage (Eb 20 V), it uses the Koren law's extrapolation to Ec1 ≥ 0
(the fit has Ec1 ≤ 0), and it is the corner where DZ say their own law is "obviously not correct" for a triode
(Vg > 0, Va < 20 V). It is in the law as defined here (plate above the cathode, Ip, Ig2 ≥ 0), as T 0.35 mA without grid
current was (Vak ≥ 16.8 V) when #111 supported it, but **it is model extrapolation, not data**.

**#121's stimulus class with grid current** (`python -m dk6as6.envelope --grid` → `envelope_grid.json`: 8 V peak tones
at 20 Hz–20 kHz from silence, sine-phase slope onsets and cosine-phase step onsets, step offsets, standard and hq, on
the interpolated internal-rate voltages; in the law = plate above the cathode, Ip + Ig2 ≥ 0, every sample converged):
**every T current 0.1–0.6 mA is in the law, and so are P1's ends**. The runtime guard counts no sample in any of the
528 runs.

| T, bias | worst Vg1k (stimulus) | min Vak | min Ip + Ig2 | without grid current (`envelope.json`) |
|---|---|---|---|---|
| 0.3 mA | −0.855 V (100 Hz step, hq) | 35.1 V | 37 µA | −0.85 V: in the law |
| 0.35 mA | **−0.078 V** (20 / 100 Hz step) | 14.5 V | 36 µA | **+0.298 V: out** |
| 0.4 mA | +0.051 V (20 Hz step) | 11.1 V | 17 µA | out |
| 0.45 mA | +0.114 V | 9.5 V | 5.3 µA | out |
| 0.5 mA | +0.156 V | 8.4 V | 1.6 µA | out |
| 0.55 mA | +0.187 V | 7.6 V | 0.56 µA | out |
| 0.6 mA | +0.210 V | 6.8 V | 0.22 µA | out |

So with grid current the law no longer limits T: the step onset that ended #121's envelope at 0.3 mA (+0.30 V at
0.35 mA) is clamped to −0.08 V. **T's top with grid current, 0.5 mA, is set by the aliasing gate** (below). Ip + Ig2
shrinks toward zero with current (0.22 µA at 0.6 mA). That margin depends on the borrowed grid law, so the guard
stays on.

**The runtime guard (`Stage.out_of_law`, `OutOfLawWarning`, #121) changes with the law in force.** Without grid
current (hardness 0) it is #121's, unchanged: g1 positive or the plate below the cathode. With grid current a positive
g1 is what the grid law models, so it is no longer an excursion. The guard counts the grid law's own limits instead:
the plate below the cathode, or Ip + Ig2 < 0 (Ik < Ig1, the grid current exceeding the cathode current that feeds
it). Tested both ways: `test_t_leaves_the_law_just_above_its_envelope` without grid current;
`test_grid_current_keeps_that_onset_in_the_law` and `test_supported_envelope_stays_in_the_law`, at both hardnesses.

**Where T's envelope ends: steady state across the band** (`python -m dk6as6.grid hf` → `grid.json` "hf"; T at
0.35–0.6 mA every 50 µA, 4 / 6 / 8 V peak, tones 1037–19037 Hz every 2 kHz, 0.4 s from silence). The 1 kHz and onset
checks above are not enough. **Every T setting 0.35–0.6 mA at 4 / 6 / 8 V and every tone stays in the law, with no
unconverged sample, no Newton restart and at most 15 iterations.** At 0.6 mA / 8 V the plate stays ≥ 6.9 V above the
cathode and Ip + Ig2 ≥ 0.47 µA.

*History (PR #128 review).* The first version of `currents_grid` rebuilt the plate share as Ip / Ik. Where Ik
underflows in cut-off (a screen strapped near the cathode, g1 below it), that division gave a NaN or discontinuous
Jacobian. Newton visited that region on single samples of steady 6–8 V, 13–19 kHz tones at T 0.5–0.6 mA, hit MAX_IT,
and needed a restart (55–57 iterations). With the share and its gradient taken straight from the fit
(`pentode.cathode_share`) those stalls are gone: 0 restarts over the 180 runs of this scan and the 212 envelope runs
above. The restart stays, as a safety net with a fixed contract. **`Stage.process_internal`, grid path only, via
`solver.solve_grid_restarts`:** if the extrapolated warm start does not converge, restart from the previous sample's
currents, then from the operating point, summing the iterations. If none converges, keep the candidate with the
smallest finite residual over all three starts and count the sample as not converged. A singular, non-finite or
overflowing step ends an attempt instead of raising (`dk6as6/tests/test_dk6as6_grid.py`: a live rescued-sample
regression against an independent solve, and the review's failure case, which checks that selection). Worst case per
sample is 3 × MAX_IT iterations; none of the measured signals needs a second start. The C++ port (#104) should budget
from the measured ≤ 15 (≤ 18 with the EHX-1 bound shape: "Grid-current bounds (#131)" below).

### Validation against the same-model ngspice references (`dk/validate.CRIT` unchanged; `results_grid[_T].json`)
References `spice6as6/reference_dualg_{0.1,0.25,0.45}mA.json` (P1) and `reference_dualg_T_{0.1,0.25,0.35,0.45,0.5,0.6}mA.json`
(T): ngspice 41 (the conda-forge osx-64 build under Rosetta, as #118 / #120 / #121), `spice/run_refs.SOLVER` unchanged,
the netlists generated as before with the dualg subckt. **Run with `run_refs.py --split 6`**: 'main' as six ngspice
calls (.op/.ac, one per sine, the burst), the same analyses (`test_split_main_is_the_same_analyses`). This was needed
to keep each call bounded. The machine was shared with other sessions' jobs at load averages of 100–400, so each
ngspice got ~15–22 % of a core, and a sine took 50–62 min of wall clock against #111's 15–35. Two earlier attempts
(one ngspice call per 'main', then split under a 1 h cap) were stopped by hand after ~25 min, once the CPU rate showed
they could not finish under 1 h. The runs that made the references used a 3 h `NGSPICE_TIMEOUT_S` cap per call; the
longest call took 61.5 min. **P1 0.6 mA has no dualg reference** (below). `python -m dk6as6.validate --grid` → `results_grid.json`
(P1) and `results_grid_T.json`.

| criterion (limit) | P1 0.1 | P1 0.25 | P1 0.45 | T 0.1 | T 0.25 | T 0.35 | T 0.45 | T 0.5 (T's top) | T 0.6 (unsupported) |
|---|---|---|---|---|---|---|---|---|---|
| op point, worst rel. (< 0.001) | 7.6e-10 | 1.2e-9 | 1.4e-9 | 1.8e-9 | 1.1e-9 | 2.8e-9 | 1.5e-9 | 3.2e-9 | 6.8e-9 |
| small signal, stage / chain (< 0.1 dB) | 0.00000 / 0.00081 | 0.00005 / 0.00084 | 0.00007 / 0.00085 | 0.00010 / 0.00076 | 0.00005 / 0.00084 | 0.00012 / 0.00088 | 0.00019 / 0.00092 | 0.00023 / 0.00094 | 0.00035 / 0.00101 |
| H2–H5 worst tested (< 0.1 dB) | 0.00214 (H4, 2 V) | 0.00069 (H4, 2 V) | 0.00297 (H4, 2 V) | 0.00050 (H5, 8 V) | 0.00040 (H4, 8 V) | 0.00055 (H5, 8 V) | 0.00382 (H4, 2 V) | 0.00134 (H4, 2 V; 10 tested) | 0.00275 (H2, 2 V; 13 tested) |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.058 % / 0.0065 % | 0.056 % / 0.0063 % | 0.056 % / 0.0062 % | 0.067 % / 0.0075 % | 0.069 % / 0.0076 % | 0.069 % / 0.0077 % | 0.069 % / 0.0076 % | 0.055 % / 0.0061 % | 0.036 % / 0.0039 % |
| recovery: tail τ (< 1 %; #124) | 0.0053 % | 0.0051 % | 0.0003 % | 0.0039 % | 0.0015 % | 0.0007 % | 0.0002 % (107.1 ms) | 0.0000 % (105.8 ms) | 0.0000 % (102.5 ms) |
| Newton non-converged / max it. | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 4 | 0 / 4 | 0 / 4 |
| status | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed**, unsupported (aliasing) |

(Standard 8×; **hq 16× passes every criterion at every one of these currents too**, with the same figures to ±0.00003 dB.) Above 0.35 mA the T comparison is complete for the first time. Without grid current T 0.45 / 0.6 mA
were "inconclusive": ngspice's exp() cap made the reference a different law there. With grid current g1 never gets
far enough positive for the cap (no case is `spice_exp_capped`), and every harmonic and the burst are compared.

**P1 0.6 mA, the top of P1's envelope, is validated by equivalence rather than by a dualg reference.** On every
validate.py signal P1 at 0.6 mA draws Ig1 ≤ 1.2e-23 A (g1 ≤ −3.28 V). That is nine orders below ngspice's abstol (1e-14
A), so the dualg circuit there is the dual one, and the dual 0.6 mA reference passes (`results.json`).
`test_p1_grid_current_is_negligible` checks the DK side: grid on and off agree to 1e-12 relative at 0.6 mA / 8 V
(measured 5e-15). A dualg 0.6 mA run was tried: its op/AC and sine pieces finished, but its burst (#120's silent-start chatter) was
killed at the 3 h cap, so it is not committed.

### Burst recovery measure (#124, resolved for the 6AS6 stage; PR #128 review)
The shared `spice/run_refs.burst_metrics` crossing τ (time from the 30.5 ms sample to 1/e of its deviation) misread
two ways. Its start sample's cycle mean straddles the burst's end (P2 0.6 mA: 52.5 % off, #124). It also searches the
cycle mean up to the end of the 120 ms trace, where `np.convolve(..., "same")` pads with zeros. Wherever the true 1/e
crossing lies beyond the trace it returns the padded edge: **89.0 ms in every grid-current T case, the 0.002 %
"agreement" this PR first reported**. The same DK burst at T 0.5 mA captured for 250 ms crosses at 106.0 ms (105.87 ms
from 31 ms on full-window samples).

**The measure now (`dk6as6/validate.py`, `burst_compare`, documented there).** Both quantities use only the stored
0.5 ms trajectory: the cycle mean at 0–118.5 ms, every sample a full one-cycle window inside the trace. Both start at
31 ms, the first sample whose window has left the burst:
- **tail τ, the criterion** (CRIT's `burst_tau_rel`, 1 %, unchanged): the decay constant of a log-linear fit of
  |Vgk − rest| over 31–118 ms. It is defined in every case: the ~0.07 % coupling-cap tail without grid current, grid
  blocking with it.
- **cross τ, reported:** 1/e of the 31 ms deviation on the same samples, "unobserved" if it lies past the trace.
- **the padded crossing,** kept in every record as `padded_tau_s` for information only.

**Every 6AS6 burst comparison was re-scored** from the committed DK records and references (`python -m dk6as6.validate
--rescore`, no new simulation). Tail τ, DK against ngspice:
- P1: 0.0003–0.0053 %.
- T without grid current, 0.1–0.45 mA: ≤ 0.0039 % (0.6 mA is not compared: ngspice's exp() cap).
- P2: 0.0039–0.031 %.
- With grid current, P1 ≤ 0.0053 % and T ≤ 0.0039 %.

Every cross τ is unobserved in the 120 ms trace (every tail decays with τ ≈ 87–114 ms), except P2 0.6 mA's DK one
(87.5 ms). **The only status that changed: P2 0.6 mA, failed → passed** (both qualities; still unsupported). Its strict
xfail is now a real assertion (`test_p2_0p6mA_burst_passes_crit_with_the_full_window_recovery_measure`).
`test_burst_recovery_measure_is_not_the_padded_trace_end` runs that T 0.5 mA burst for 250 ms. It checks that the
tail τ of the 120 ms run matches the crossing actually observed in the long run (105.84 vs 105.87 ms, within 1 %),
and that the padded 89 ms does not. The 12AX7 stage's validation (`dk/validate.py`) still uses the shared crossing.
Its bypass-cap recovery (τ ≈ 9 ms) crosses 1/e far inside the trace, so it does not read the padding, but it starts
on the same straddling sample: left to #124.

### What the grid current changes (DK standard, ngspice agreeing to the digits shown; `python -m dk6as6.grid tables`)
**4 V, 1 kHz burst (10–30 ms): grid DC (cycle-mean Vgk), V.** "No grid" is the #111 stage. Raw shift and trajectory,
and the tail τ above (DK and ngspice equal to the digits shown).

| T, bias | | rest | shift (min − rest) | at 30 / 35 / 50 / 118.5 ms | tail τ |
|---|---|---|---|---|---|
| 0.35 mA | no grid | −3.9416 | −0.682 | −4.624 / −3.940 / −3.941 / −3.941 | 111.9 ms |
| 0.35 mA | grid | −3.9416 | −0.682 | −4.624 / −3.940 / −3.941 / −3.941 | 111.9 ms |
| 0.45 mA | no grid | −2.6527 | −0.843 | −3.496 / −2.651 / −2.651 / −2.652 | 109.1 ms |
| 0.45 mA | grid | −2.6527 | **−0.855** | −3.508 / **−2.662 / −2.661 / −2.657** | 107.1 ms |
| 0.5 mA | grid (no no-grid reference) | −2.0543 | −1.184 | −3.238 / −2.292 / −2.260 / −2.162 | 105.8 ms |
| 0.6 mA | no grid (ngspice; DK not comparable: exp cap) | −0.9488 | −1.102 | −2.051 / −0.950 / −0.950 / −0.949 | 106.0 ms |
| 0.6 mA | grid | −0.9488 | **−2.189** | −3.138 / **−1.969 / −1.830 / −1.401** | 102.5 ms |

**This is the blocking the issue asked for.** Without grid current the grid DC only follows the unbypassed cathode
during the burst and returns with its end (to < 2 mV at 35 ms). With it, at 0.45 mA the burst's positive peaks charge
Cin through the grid, leaving the grid 9–10 mV low at 35–50 ms and 4 mV low at 118.5 ms. At 0.6 mA it is 1.02 V low at
35 ms and still 0.45 V low at 118.5 ms, recovering with the Cin / grid-leak time constant (tail fit ~0.1 s; Cin 100 nF
into Rg 1 MΩ). The shift itself doubles (−2.19 V against −1.10 V). In P1 (every current) and T up to 0.35 mA the 4 V
burst draws no grid current (Ig1 < 1e-11 A), so the burst is the #111 one to the printed digits (P1 0.1 / 0.25 /
0.45 mA shifts −0.213 / −0.235 / −0.257 V, as above).

**THD vs drive (1 kHz, validate.py's amplitudes), % — with grid current / without:**

| bias | 0.05 V | 0.5 V | 2 V | 8 V |
|---|---|---|---|---|
| P1 0.1 / 0.25 / 0.45 mA | identical (0.0118 / 0.0016 / 0.0446) | identical (0.119 / 0.0177 / 0.446) | identical (0.512 / 0.147 / 1.746) | identical (7.435 / 8.170 / 8.903) |
| T 0.1 / 0.25 mA | identical | identical | identical | identical (2.463 / 4.162) |
| T 0.35 mA | 0.0296 / 0.0296 | 0.297 / 0.297 | 1.201 / 1.201 | **5.991 / 6.103** (H3 −40.8 vs −43.6, H5 −52.5 vs −63.8 dBc) |
| T 0.45 mA | 0.0469 / 0.0469 | 0.470 / 0.470 | 1.916 / 1.916 | **10.03 / 20.24** (no-grid DK; ngspice 12.33, exp-capped: neither is physical) |
| T 0.5 mA (grid only) | 0.0618 | 0.619 | 2.554 | 13.25 |
| T 0.6 mA | **0.1202 / 0.1221** | **1.098 / 1.226** (H4 −68.0 vs −97.6 dBc) | **4.085 / 16.40** (ngspice 7.86, capped) | **19.36 / 40.35** (ngspice 29.46, capped) |

("Identical": to ≤ 5e-7 dB on any harmonic, the largest on harmonics at the −150 dBc floor.) The grid current matters
where g1 gets within ~1 V of the cathode: T at ≥ 0.35 mA. There it **halves the 8 V distortion of the no-grid law at
0.45–0.6 mA** (which drove g1 to +0.7…+1.6 V and the plate below the cathode). At T 0.6 mA, where g1 rests at −0.95 V,
the grid current's exponential onset also **adds H4/H5 at moderate drive** (0.5 V: H4 −68.0 dBc against −97.6, H5
−74.8 against −117.6). That is a prediction of the borrowed DZ shape, the part of this law least supported by 6AS6 data.

**T's "mainly even harmonics" at 8 V.** With grid current T at 8 V clips on the grid from 0.45 mA: g1 is clamped near
0 V and the plate runs down to ~10 V. That brings H3 within 2 dB of H2 (0.45 mA: H2 −22.6, H3 −24.5 dBc; 0.5 mA:
−20.4 / −21.6). Without grid current those settings were not physical. Up to 2 V everywhere, and at 8 V up to 0.35 mA,
T stays even-dominant by over 10 dB (`test_mode_harmonic_character` asserts that). The manuals' "T: mainly even
harmonics" is not level-specific, so this is reported as a model prediction (`test_t_grid_clipping_at_8v_is_not_even_dominant`
pins it), not counted against T's envelope.

### Aliasing with grid current (`python -m dk6as6.aliasing --grid` → `aliasing_grid[_T].json`; 8× FIR, 48 kHz)
`dk/aliasing.py`'s metric, the #121 quality gate unchanged (`aliasing.QUALITY_GATE_DBC`, the shipped 12AX7 stage's
budget: −118.3 dBc at 2 V, −48.6 dBc at 8 V). Full 19-tone sweeps at T 0.45, 0.5 and 0.6 mA. At the other currents
only the five top tones (15037–19037 Hz) were swept: every full sweep of this stage so far has had its worst tone
there. Worst total, dBc:

| | 2 V | 8 V | gate |
|---|---|---|---|
| P1 0.1 / 0.25 / 0.45 / 0.6 mA | −140.31 / −147.99 / −130.37 / −123.59 | −112.50 / −107.18 / −100.96 / −98.44 | pass; equal to the no-grid sweep to the 0.01 dB printed |
| T 0.1 mA | −140.73 | −125.25 | pass (= no grid) |
| T 0.25 mA | −136.55 | −121.08 | pass (= no grid) |
| T 0.35 mA | −133.35 | **−95.03** (internal; no grid −117.98) | pass |
| T 0.45 mA (full) | −129.32 | **−66.14** (internal) | pass |
| **T 0.5 mA (full; T's top)** | −126.84 | **−63.92** at 17037 Hz (internal) | **pass** |
| T 0.55 mA | **−109.66** (internal) | −60.33 | **fails at 2 V** |
| T 0.6 mA (full) | **−87.49** (internal) | −57.26 | **fails at 2 V** |

(Without grid current T 0.45 / 0.6 mA read −56.7 / −54.8 dBc at 8 V, but that stage was not physical.) **The grid
current costs aliasing, inside the stage, where it switches on.** Its exponential onset (softplus with Cg = 9.9 /V)
makes high harmonics that 8× oversampling folds back. At 8 V, T 0.35–0.5 mA rises from the resampler floor (−118 to
−121 dBc) to −64…−95 dBc. That is within the gate's −48.6 dBc, the 12AX7 stage's own budget (that stage also models
grid current), but it is the largest aliasing of any supported 6AS6 setting. At 2 V it stays at the resampler floor to
0.5 mA. From 0.55 mA g1 rests within ~1.3 V of the cathode (−0.95 V at 0.6 mA), so a 2 V signal already reaches the
onset and the internal aliasing passes the gate (−109.7 / −87.5 dBc). **That, not the law, ends T's envelope at
0.5 mA** (`test_t_above_its_grid_envelope_fails_the_quality_gate`). hq (16×) would lower the internal part; it is not
measured here, and the gate is defined at standard. The repo has no audibility criterion (#96), and this does not
invent one. Regression baselines: `aliasing.LIMIT_DBC_GRID` (worst + 3 dB). CI re-measures P1 0.45 / 0.6 mA at 8 V and
T 0.5 mA at 2 and 8 V (`CI_CASES_GRID`). Every grid sweep record carries Newton's unconverged count: zero everywhere.

## Grid-current bounds (#131, part a; every grid-current number here is estimated, unvalidated)
**Question:** #112's grid law borrows its shape (ξ, Cg) and, by a perveance scaling, its strength (Gg) from one
measured 12AX7, Dempwolf & Zölzer's RSD-1. The 6AS6 positive-g1 measurement that would replace it is parked (#131: no
tube available). How far do the #112 / #113 results move if the borrowed shape is instead one of the other two 12AX7s
the same paper measured?

**Basis.** Dempwolf & Zölzer, *A physically-motivated triode model for circuit simulations*, DAFx-11
(https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf), **Table 1, "Individually fitted parameters for 12AX7
triodes", columns RSD-1, RSD-2 and EHX-1, rows G, Gg, ξ, Cg** (re-read from the PDF for #131; all eight rows of each
column are in `dk/triode.py`, `DZ_12AX7_RSD1/RSD2/EHX1`, and `test_table1_columns_are_the_papers` pins them). Each
column is scaled the way #112 scaled RSD-1: **Gg = hardness × (2/KG1) / G × Gg** of that column, with ξ and Cg as
printed (`pentode.grid(shape=...)`, `test_bounds_are_scaled_as_the_nominal`).

| shape (Table 1 column) | G, A/V^γ | Gg, A/V^ξ | ξ | Cg, /V | ratio (2/KG1)/G | 6AS6 Gg (hardness 1) |
|---|---|---|---|---|---|---|
| **RSD-1 (nominal, the #112 default)** | 2.242e-3 | 6.177e-4 | 1.314 | 9.901 | 1.2808 | 7.912e-4 |
| RSD-2 (bound) | 2.173e-3 | 5.911e-4 | 1.358 | 11.76 | 1.3215 | 7.811e-4 |
| EHX-1 (bound) | 1.371e-3 | 3.263e-4 | 1.156 | 11.99 | 2.0945 | 6.834e-4 |

**How the bounds are selected (and what does not change).** `pentode.GRID_SHAPES` / `GRID_BOUNDS`,
`Stage(grid_shape="RSD-2" | "EHX-1")` (also `operating_point`, `cathode_resistor`, `bias_point`, and the measurement
functions of `grid.py`, `envelope.py`, `aliasing.py`); ngspice models `dualg_rsd2` / `dualg_ehx1` (`run_refs.py`,
their own subckts `models/6as6_dualg_{rsd2,ehx1}_cg3k.inc`, T only); `validate.py --bounds`. **A bound has no supported
envelope**: `Stage(grid_shape=bound)` raises `UnsupportedSettingError` unless `experimental=True`. **The nominal path is
main's bit for bit** (`dk6as6/tests/test_dk6as6_grid_bounds.py`): `pentode.GRID` and every committed record's grid dict
are #112's exactly (no new key), the nominal subckts and netlists regenerate byte-identical, and the default stage and
an explicit `grid_shape="RSD-1"` give the same samples bit for bit, and reproduce main's committed T 0.5 mA onset
(`grid.json`) and burst trajectory and Rk (`results_grid_T.json`) exactly on macOS arm64, where those records were made
(on CI's Linux, whose numpy differs in the last ulps, the onset to 1e-12 relative and the burst trajectory to
1e-8 V, against Newton's 1e-6 V tolerance).

**Ig1 (A) at Vg1k: these are not two-sided bounds, and the curves cross** (corrected after the PR #139 review).
RSD-1 has the softest onset (Cg 9.9 /V against ~12), so far below the cathode both bounds draw less current (RSD-2
1/27 of RSD-1 at −1 V). **RSD-2 stays below RSD-1 up to +1.34 V.** **EHX-1 crosses above RSD-1 at −66.5 mV and back
below at +383 mV**, so in that window it draws *more* (+1.5 % at −50 mV, +5.4 % at 0 V). At +0.5 V all three agree
within 5 %, and at +2 V EHX-1 is 23 % lower. Which shape "starts conducting first" therefore depends on the current
taken as the onset, so the comparison is tied to a stated threshold (Vg1k at which Ig1 reaches it, mV; bisection on
`pentode.grid_current`):

| onset threshold | RSD-1 | RSD-2 | EHX-1 |
|---|---|---|---|
| 1 µA | −278.3 | −203.8 | −262.0 |
| 10 µA | −85.9 | −42.3 | −84.3 |
| 20 µA (illustrative, ~1.4 V across the 68k stopper) | −19.2 | +15.9 | **−22.9** |

So EHX-1 reaches 20 µA 3.8 mV before RSD-1, and at 1 µA or 10 µA it is later. The 0.2–0.3 V earlier onset that the
6Ж2П datasheet hints at (#131 write-up) is far outside this spread. It is **131b's** opt-in sensitivity test, not
covered here.

| Vg1k | RSD-1 | RSD-2 | EHX-1 |
|---|---|---|---|
| −1.5 V | 1.3e-13 | 1.09e-15 | 3.62e-14 |
| −1 V | 8.71e-11 | 3.19e-12 | 3.70e-11 |
| −0.5 V | 5.79e-8 | 9.34e-9 | 3.78e-8 |
| −0.16 V | 4.28e-6 | 1.94e-6 | 3.89e-6 |
| −0.05 V | 1.466e-5 | 9.06e-6 | 1.488e-5 |
| 0 V | 2.40e-5 | 1.67e-5 | 2.53e-5 |
| +0.5 V | 3.19e-4 | 3.05e-4 | 3.07e-4 |
| +1 V | 7.91e-4 | 7.81e-4 | 6.83e-4 |
| +2 V | 1.97e-3 | 2.00e-3 | 1.52e-3 |

At rest the shapes move nothing. T's operating points (Rk, Vg1k, Vak) agree to 4 digits at 0.35–0.55 mA. At 0.6 mA,
where g1 rests at −0.949 V, Ig1 at rest is 0.17 nA (RSD-1), 7.2 pA (RSD-2) and 75 pA (EHX-1), and Rk differs by
≤ 1.7e-4 (`grid_bounds.json` "law").

**Files.** `python -m dk6as6.grid_bounds [law] [envelope] [aliasing]` → `grid_bounds.json` (bounds only; the nominal
numbers are the committed #112 / #113 records, which the same functions reproduce); `python spice6as6/run_refs.py
--split 6 dualg_rsd2/T dualg_ehx1/T` → `spice6as6/reference_dualg_{rsd2,ehx1}_T_{0.45,0.5,0.6}mA.json` (ngspice 41,
`spice/run_refs.SOLVER` unchanged); `python -m dk6as6.validate --bounds` → `results_grid_{rsd2,ehx1}_T.json` (re-run with #134's k140 resampler, like the
nominal `results_grid_T.json`; `grid.py` / `envelope.py` name the shipped FIR their records were made with, and
`grid_bounds.py` keeps #113's shipped-FIR guard keys, tones and limits, `GUARD_T_KEYS`, with those keys' nominal
baseline values in `grid_bounds_nominal_guard.json`, so the guard table below still reproduces);
`python -m dk6as6.grid_bounds tables` prints every table below. The machine was shared (load 90–180), so no timing is
reported.

### Method check: DK against each bound's own ngspice reference (`dk/validate.CRIT` unchanged)
**All 12 cases pass every criterion** (T 0.45 / 0.5 / 0.6 mA × RSD-2 / EHX-1 × standard / hq). These settings are
experimental, so the results are recorded, not counted as supported passes. Standard 8×:

| criterion (limit) | RSD-2 0.45 | RSD-2 0.5 | RSD-2 0.6 | EHX-1 0.45 | EHX-1 0.5 | EHX-1 0.6 |
|---|---|---|---|---|---|---|
| op point, worst rel. (< 0.001) | 1.5e-9 | 3.2e-9 | 6.6e-9 | 1.5e-9 | 3.2e-9 | 7.9e-9 |
| small signal, stage / chain (< 0.1 dB) | 0.00019 / 0.00019 | 0.00023 / 0.00023 | 0.00035 / 0.00035 | 0.00019 / 0.00019 | 0.00023 / 0.00023 | 0.00035 / 0.00035 |
| H2–H5 worst tested (< 0.1 dB) | 0.00369 (H4, 2 V; 9 tested) | 0.00088 (H4, 2 V; 10) | 0.00702 (H2, 2 V; 13) | 0.00374 (H4, 2 V; 9) | 0.00107 (H4, 2 V; 10) | 0.00290 (H2, 2 V; 13) |
| burst shift / trajectory (< 0.5 % / 0.5 %) | 0.070 % / 0.0077 % | 0.056 % / 0.0062 % | 0.036 % / 0.0039 % | 0.069 % / 0.0077 % | 0.055 % / 0.0061 % | 0.036 % / 0.0039 % |
| recovery: tail τ (< 1 %) | 0.0002 % | 0.0000 % | 0.0000 % | 0.0000 % | 0.0000 % | 0.0000 % |
| Newton non-converged / max it. | 0 / 4 | 0 / 4 | 0 / 4 | 0 / 4 | 0 / 4 | 0 / 4 |

So the DK solver handles the sharper bound onsets (Cg ~12 /V) as accurately as the nominal one. In the result tables
below, DK and ngspice agree to the digits shown.

### How far T 0.45–0.6 mA moves (DK standard 8×, 48 kHz)
**4 V, 1 kHz burst: grid DC (cycle-mean Vgk), V** (`results_grid[_rsd2|_ehx1]_T.json`):

| T, bias | shape | rest | shift (min − rest) | at 30 / 35 / 50 / 118.5 ms | tail τ |
|---|---|---|---|---|---|
| 0.45 mA | RSD-1 | −2.6527 | −0.854 | −3.507 / −2.662 / −2.661 / −2.657 | 107.1 ms |
| 0.45 mA | RSD-2 | −2.6527 | −0.848 | −3.500 / −2.655 / −2.655 / −2.654 | 106.3 ms |
| 0.45 mA | EHX-1 | −2.6527 | −0.853 | −3.505 / −2.660 / −2.659 / −2.656 | 107.0 ms |
| 0.5 mA | RSD-1 | −2.0543 | −1.183 | −3.238 / −2.292 / −2.260 / −2.162 | 105.8 ms |
| 0.5 mA | RSD-2 | −2.0543 | −1.162 | −3.217 / −2.272 / −2.243 / −2.153 | 105.9 ms |
| 0.5 mA | EHX-1 | −2.0543 | −1.182 | −3.236 / −2.290 / −2.259 / −2.161 | 105.8 ms |
| 0.6 mA | RSD-1 | −0.9488 | −2.188 | −3.137 / −1.969 / −1.830 / −1.401 | 102.5 ms |
| 0.6 mA | RSD-2 | −0.9488 | −2.165 | −3.114 / −1.948 / −1.811 / −1.391 | 102.6 ms |
| 0.6 mA | EHX-1 | −0.9488 | −2.189 | −3.138 / −1.970 / −1.831 / −1.401 | 102.5 ms |

**The peak shift and the tail τ move by ≤ 2 %; the residual blocking after the burst does not** (corrected after the
PR #139 review). The shift moves by ≤ 23 mV and ≤ 1.8 % (RSD-2; 0.5 mA: 21 mV, 1.8 %; 0.6 mA: 23 mV, 1.1 %) and the tail τ by ≤ 0.8 ms
(0.7 %). The residual blocking (Vgk − rest after the burst), mV:

| T, bias | at 35 ms: RSD-1 / RSD-2 / EHX-1 | at 118.5 ms: RSD-1 / RSD-2 / EHX-1 |
|---|---|---|
| 0.45 mA | −8.98 / −2.75 / −7.32 | −4.12 / −1.25 / −3.35 |
| 0.5 mA | −237.6 / −218.0 / −235.9 | −108.0 / −99.1 / −107.2 |
| 0.6 mA | −1020.3 / −998.8 / −1021.3 | −451.9 / −442.5 / −452.4 |

At 35 ms it moves by up to 21.5 mV (0.6 mA, RSD-2), which is 2.1 % of the blocking there, 8.25 % at 0.5 mA (19.6 mV),
and 69 % at 0.45 mA, where the blocking itself is only 9 mV (6.2 mV). EHX-1 stays within 2 mV of RSD-1 in every column of both tables.
RSD-2 draws the least current below +1.34 V, so it blocks least.

**THD vs drive, 1 kHz, %** (H2 / H3 / H4 dBc where they move):

| T, bias | shape | 0.05 V | 0.5 V | 2 V | 8 V |
|---|---|---|---|---|---|
| 0.45 mA | RSD-1 / RSD-2 / EHX-1 | 0.04691 (all) | 0.4697 (all) | 1.916 (all) | 10.03 / 9.992 / 10.05 |
| 0.5 mA | RSD-1 / RSD-2 / EHX-1 | 0.06179 (all) | 0.6192 (all) | 2.554 / 2.558 / 2.556 | 13.25 / 13.20 / 13.28 |
| 0.6 mA | RSD-1 | 0.1202 | 1.098 (H4 −68.0, H5 −74.8) | 4.086 | 19.36 |
| 0.6 mA | RSD-2 | 0.1220 | 1.206 (H4 −83.6, H5 −87.5) | 4.078 | 19.33 |
| 0.6 mA | EHX-1 | 0.1212 | 1.142 (H4 −71.3, H5 −77.4) | 4.139 | 19.39 |

THD moves by ≤ 0.05 percentage points at 8 V and ≤ 0.004 at 2 V up to 0.5 mA, and by ≤ 0.11 at 0.6 mA (0.5 V). Some
low-level harmonics move more: at 0.5 mA, 2 V, H5 is −110.4 / −94.8 / −103.0 dBc.

**1 kHz tones, last 100 cycles of 0.5 s** (max Vg1k, min Vak, THD; `grid_bounds.json` "envelope" tones; RSD-1 from
`grid.json`, which has no 0.55 mA tone):

| T, bias | drive | RSD-1 | RSD-2 | EHX-1 |
|---|---|---|---|---|
| 0.45 mA | 4 V | −0.29 V, 20.2 V, 3.81 % | −0.25 V, 19.1 V, 3.94 % | −0.28 V, 20.0 V, 3.84 % |
| 0.45 mA | 8 V | −0.04 V, 13.5 V, 10.03 % | +0.00 V, 12.5 V, 9.99 % | −0.04 V, 13.6 V, 10.05 % |
| 0.5 mA | 4 V | −0.13 V, 16.0 V, 3.95 % | −0.09 V, 14.6 V, 4.02 % | −0.13 V, 15.8 V, 3.98 % |
| 0.5 mA | 8 V | −0.01 V, 12.9 V, 13.25 % | +0.02 V, 12.0 V, 13.20 % | −0.01 V, 13.1 V, 13.28 % |
| 0.55 mA | 8 V | — | +0.04 V, 11.6 V, 16.41 % | +0.00 V, 12.7 V, 16.48 % |
| 0.6 mA | 2 V | −0.15 V, 16.4 V, 4.09 % | −0.10 V, 15.0 V, 4.08 % | −0.15 V, 16.2 V, 4.14 % |
| 0.6 mA | 8 V | +0.02 V, 12.3 V, 19.36 % | +0.05 V, 11.4 V, 19.33 % | +0.01 V, 12.5 V, 19.39 % |

(Up to 6 V at 0.35–0.4 mA and up to 2 V at 0.45–0.5 mA the three are equal to the digits shown; all 24 points per
shape are in the file.)

**In the law (#121's stimulus class with grid current, `envelope.excursion`, 288 runs per shape, standard and hq):
every T current 0.35–0.6 mA stays in the law with both bounds**, with no guard count and no unconverged sample. Worst
max Vg1k, min Vak, min Ip + Ig2:

| T, bias | RSD-1 (`envelope_grid.json`) | RSD-2 | EHX-1 |
|---|---|---|---|
| 0.35 mA | −0.078 V, 14.5 V, 36 µA | −0.043 V, 13.5 V, 36 µA | −0.077 V, 14.5 V, 36 µA |
| 0.45 mA | +0.114 V, 9.5 V, 5.3 µA | +0.137 V, 8.8 V, 5.6 µA | +0.103 V, 9.8 V, 5.3 µA |
| 0.5 mA | +0.156 V, 8.4 V, 1.6 µA | +0.176 V, 7.7 V, 1.7 µA | +0.144 V, 8.7 V, 1.6 µA |
| 0.55 mA | +0.187 V, 7.6 V, 0.56 µA | +0.207 V, 7.0 V, 0.58 µA | +0.175 V, 7.9 V, 0.56 µA |
| 0.6 mA | +0.210 V, 6.8 V, 0.22 µA | +0.229 V, 6.2 V, 0.23 µA | +0.198 V, 7.1 V, 0.22 µA |

**Onsets and the steady-state band scan** (`grid._onset` at T 0.35–0.6 mA × 6 tones × standard / hq; `grid._hf` at
0.35–0.6 mA × 4 / 6 / 8 V × 10 tones): every run is in the law and converges on every sample with no restart. **Newton
takes up to 18 iterations with EHX-1** (standard; at 0.5–0.6 mA, 10–19 kHz), against ≤ 15 for RSD-1 and ≤ 14 for
RSD-2. EHX-1 has the largest Cg with the smallest ξ, so its onset is the sharpest switch in a few samples. **Scope:**
the 1 kHz tones, onsets and band scan record iterations and restarts. The stimulus-class runs (above) and the aliasing
runs (below) record only unconverged samples (none) and the guard count (none, stimulus class), so 18 is the largest
count *observed* in the recorded runs, not a bound over every run here.

**Aliasing** (`aliasing.measure`, 1 s window after 1.25 s, 8× FIR, 48 kHz; the five top tones, and the full 19-tone
sweep at T 0.5 mA; RSD-1 from `aliasing_grid_T.json`). Worst total, dBc (gate `aliasing.QUALITY_GATE_DBC`):

| T, bias, drive | gate | RSD-1 | RSD-2 | EHX-1 |
|---|---|---|---|---|
| 0.45 mA, 2 V | −118.3 | −129.32 (resampler) | −129.32 | −129.32 |
| 0.45 mA, 8 V | −48.6 | −66.14 (internal) | −63.92 | −65.12 |
| **0.5 mA, 2 V (full)** | −118.3 | **−126.84** (resampler) | **−126.78** | **−126.81** |
| **0.5 mA, 8 V (full)** | −48.6 | **−63.92** at 17037 Hz (internal) | **−62.47** at 17037 Hz | **−63.22** at 17037 Hz |
| 0.55 mA, 2 V | −118.3 | **−109.66 (fails)** | **−96.92 (fails)** | **−104.44 (fails)** |
| 0.55 mA, 8 V | −48.6 | −60.33 | −58.99 | −59.70 |
| 0.6 mA, 2 V | −118.3 | **−87.49 (fails)** | **−84.25 (fails)** | **−85.86 (fails)** |
| 0.6 mA, 8 V | −48.6 | −57.26 | −56.31 | −56.81 |

**The CI guard's T keys** (`aliasing_guard.CI`, its 0.1 s CI window and tones, both rates; worst tone, dBc; RSD-1 is
the committed baseline):

| rate, key | limit | RSD-1 | RSD-2 (worst Δ vs baseline) | EHX-1 (worst Δ) |
|---|---|---|---|---|
| 48 kHz, T/0.5mA/4x/2V | −101.8 | −104.87 | −119.32 (−14.2 dB) | −108.68 (−3.8) |
| 48 kHz, T/0.5mA/4x/8V | −40.8 | −43.88 | −43.31 (+0.6) | −43.59 (+0.3) |
| 48 kHz, T/0.5mA/standard/8V | −60.3 | −63.39 | **−60.90 (+2.5)** | −62.26 (+1.1) |
| 48 kHz, T/0.5mA/hq/8V | −80.7 | −83.79 | −82.11 (**+1.7**) | −82.92 (+0.9) |
| 44.1 kHz, T/0.5mA/4x/2V | −97.3 | −100.45 | −115.20 (−14.5) | −104.40 (−3.9) |
| 44.1 kHz, T/0.5mA/4x/8V | −39.4 | −42.67 | −42.37 (+0.5) | −42.48 (+0.3) |
| 44.1 kHz, T/0.5mA/standard/8V | −56.4 | −59.41 | −58.12 (+1.3) | −58.79 (+0.6) |
| 44.1 kHz, T/0.5mA/hq/8V | −77.3 | −80.36 | −78.61 (**+1.7**) | −79.53 (+0.8) |

Every bound point is inside the guard's absolute limits. With RSD-2 the 48 kHz standard 8 V key keeps only 0.6 dB of
its 3 dB margin, and three keys read more than the ratchet's 1.5 dB above the nominal baseline. The guard measures the
nominal stage only (`aliasing_guard.GRID_HARDNESS`, shape RSD-1), so none of this changes CI. It does mean that
**changing the default shape would need new baselines**, and with RSD-2 the standard 8 V limit too.

### What this means for the #112 / #113 claims
- **T's envelope end (0.5 mA), at the measured points:** at standard 8×, 48 kHz, T 0.5 mA passes the quality gate with
  every shape at both drives (2 V by ≥ 8.4 dB, 8 V by ≥ 13.8 dB), and T 0.55 mA fails it at 2 V with every shape. The
  0.5 mA end set by the 2 V gate therefore does not move between these shapes. The margin at 0.55 mA does move:
  −96.9 to −109.7 dBc against the −118.3 dBc gate. The law limits (plate above the cathode, Ip + Ig2 ≥ 0, converged)
  hold with each shape at every T current measured, 0.35–0.6 mA. **Not re-measured with the bounds:** T below 0.35 mA
  (law) and below 0.45 mA (aliasing), 44.1 kHz except the guard's keys, and 4× / 16× except the guard's T 0.5 mA keys.
  So this is not a re-derivation of the whole 0.1–0.5 mA envelope per shape.
- **Blocking and recovery** (#112's "this is the blocking the issue asked for"): the peak shift and the ~0.1 s tail τ
  move by ≤ 2 % across the bounds. The residual blocking after the burst moves by up to 21.5 mV at 35 ms (8.25 % at
  0.5 mA), so it is shape-dependent in size. That blocking happens, and how it recovers, is common to the whole
  borrowed law family. It is still not a 6AS6 measurement.
- **THD and "halves the 8 V distortion" at 0.45–0.6 mA:** 8 V THD moves by ≤ 0.05 points, so that claim stands. **The
  H4 / H5 that the grid onset adds at T 0.6 mA, 0.5 V does not stand as a number:** H4 is −68.0 / −83.6 / −71.3 dBc
  (15.6 dB spread) and H5 −74.8 / −87.5 / −77.4. #112 already called it "a prediction of the borrowed DZ shape, the
  part of this law least supported by 6AS6 data". The bounds show it is the most shape-sensitive result here, at an
  unsupported setting.
- **8× aliasing with grid current (#113):** at the measured 8 V points (8×, 48 kHz, T 0.45–0.6 mA) the bounds only
  make the internal folding worse, by up to 2.2 dB (T 0.45 mA, RSD-2: −63.92 against −66.14 dBc) and 1.45 dB at T's
  top (0.5 mA, RSD-2: −62.47 dBc, still 13.9 dB inside the gate). The 2 V maxima at 0.45–0.5 mA do not move (they are
  the resampler's). #113's "internal folding at every factor" at 8 V is confirmed for the bounds only where it was
  measured: T 0.5 mA at 4×, 8× and 16× on the guard's tones at both rates (0.1 s CI window; internal ≥ 20 dB above the
  resampler share at every one), and T 0.45–0.6 mA at 8×. #113's figures are the nominal's and stay as stated, with up
  to +2.2 dB of shape uncertainty at the measured 8 V points.
- **Newton budget for the C++ port (#104):** "budget from the measured ≤ 15" becomes **≤ 18, the largest count observed**
  with the bounds (EHX-1; tones, onsets and band scan, the runs that record iterations). It stays far below MAX_IT = 50,
  and none of those runs needed a restart.
- **What the bounds cannot say.** All three are 12AX7s, and all three are scaled by the same perveance assumption. A
  real 6AS6 could differ in ways Table 1 does not span: the onset voltage, the grid / plate current division, or a
  different Gg scaling. The columns' onsets differ by only a few to ~75 mV, depending on the threshold (EHX-1 is
  3.8 mV *earlier* than RSD-1 at 20 µA, later at 1 µA and 10 µA). A 6AS6 that conducts 0.2–0.3 V earlier (the 6Ж2П
  hint) lies well outside that spread. That is #131b. T-mode aliasing with these bounds is #132. The positive-g1
  measurement stays parked in #131. **Every grid-current result stays labelled "estimated, unvalidated."**
