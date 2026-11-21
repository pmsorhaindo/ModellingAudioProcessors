# 6AS6 DK stage — the #102 circuit values, the #110 bias control, the #111 T / P1 / P2 modes and the #112 g1 grid current, validated against the same-model ngspice references (21 Nov 2026)

`python -m dk6as6.validate` → `results.json` (standard 8× and hq 16× at 0.1, 0.25, 0.45 and 0.6 mA; plain 4× at
0.45 mA for information); `python -m pytest -q dk6as6/tests spice6as6/tests` covers it (standard at all four currents,
hq at 0.1 and 0.6 mA, for CI time). **Standard (8×) and hq (16×) pass every criterion at all four currents**,
including the starved 0.1 mA, with the 12AX7 stage's criteria (`dk/validate.CRIT`, imported unchanged) against
reference (ii), ngspice running the same valve law. The bias control (#110) is in "Bias control" below; the Distortion switch's T / P1 / P2 wiring (#111) is in
"Modes (#111)" at the end (everything above it is P1, the default, unchanged by #111). History: #26 validated the same method on placeholder values
(the 12AX7 stage's passives, HT 300 V, Ra 100k, Rk 1.5k bypassed by 22 µF, g3 at 0 V); those are replaced here.

> **#112: the default stage now models g1 grid current** (an unvalidated, borrowed Dempwolf–Zölzer law with a
> `grid_hardness` control; "Grid current (#112)" at the end). Every result above "Grid current (#112)" is the stage at
> `grid_hardness=0`, reproduced bit for bit. With grid current P1 is unchanged to rounding, and T is supported to
> 0.5 mA instead of 0.3 mA.

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
0.45 mA / 2 V and 8 V, and 0.6 mA / 8 V (−98.45 dBc in the CI window). hq, IIR and 44.1 kHz are not swept for this stage yet.

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
   against the manuals, its 2 V aliasing above the gate (the resampler's stopband; not fixed by hq), and its 0.6 mA
   burst τ failure.
3. **The burst τ metric** starts on a sample that straddles the burst's end: #124.
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
checks above are not enough. Before the fix below, T at 0.5–0.6 mA lost Newton on single samples of a *steady* 6–8 V
tone at 13–19 kHz (0.5 mA / 8 V at 13 and 19 kHz, 0.55 mA / 8 V at 17 kHz, 0.6 mA / 6 V at 19 kHz and 8 V at 15–19 kHz):
1–12 samples per 0.4 s hit MAX_IT and returned an iterate with the plate at ~0 V and Ip + Ig2 slightly negative. **That
was Newton, not the circuit.** At the failing sample of T 0.6 mA / 8 V / 15 kHz the same equations have a root with
every current positive (plate 11.7 V), which Newton finds in 6–7 iterations from zero current or from the previous
sample's currents. Only the linear-extrapolation warm start led it into the Ik < Ig1 region, where it stalled.
**Fix (`Stage.process_internal`, grid path only):** if a sample does not converge, restart from the previous sample's
currents, then from the operating point; the iterations are summed. A sample that converges first time is unchanged,
so every 1 kHz result and the hardness-0 stage are bit-identical. With it, **every T setting 0.35–0.6 mA at 4 / 6 /
8 V and every tone stays in the law with no unconverged sample**. At 0.6 mA / 8 V the plate stays ≥ 6.9 V above the
cathode and Ip + Ig2 ≥ 0.47 µA. **Cost:** a rescued sample takes 55–57 iterations (the failed 50, then 5–7). This
happens on a few samples per 0.4 s, only at T ≥ 0.5 mA with a steady 6–8 V tone above 13 kHz. It is a worst-case
real-time item for the C++ port (#104), not an accuracy one.

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
| recovery τ (< 1 %, where the reference has one) | none | none | none | none | none | none | **0.0018 %** (89.0 ms) | **0.0002 %** (89.0 ms) | **0.0019 %** (89.0 ms) |
| Newton non-converged / max it. | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 3 | 0 / 4 | 0 / 4 | 0 / 4 |
| status | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed** | **passed**, unsupported (aliasing) |

(Standard 8×; **hq 16× passes every criterion at every one of these currents too**, with the same figures to ±0.00003 dB
and τ to 0.0004 %.) Above 0.35 mA the T comparison is complete for the first time. Without grid current T 0.45 / 0.6 mA
were "inconclusive": ngspice's exp() cap made the reference a different law there. With grid current g1 never gets
far enough positive for the cap (no case is `spice_exp_capped`), and every harmonic and the burst are compared.

**P1 0.6 mA, the top of P1's envelope, is validated by equivalence rather than by a dualg reference.** On every
validate.py signal P1 at 0.6 mA draws Ig1 ≤ 1.2e-23 A (g1 ≤ −3.28 V). That is nine orders below ngspice's abstol (1e-14
A), so the dualg circuit there is the dual one, and the dual 0.6 mA reference passes (`results.json`).
`test_p1_grid_current_is_negligible` checks the DK side: grid on and off agree to 1e-12 relative at 0.6 mA / 8 V
(measured 5e-15). A dualg run would repeat #120's ~2 CPU-hour burst, ngspice chattering through that operating
point's silent start.

### What the grid current changes (`grid.json` "tables"; DK standard, ngspice agreeing to the digits shown)
**4 V, 1 kHz burst (10–30 ms): grid DC (cycle-mean Vgk), V.** "No grid" is the #111 stage. **Raw shift and
trajectory are given beside CRIT's τ (#124).** CRIT's τ starts on the 30.5 ms sample, which straddles the burst's end,
so it is shown but not relied on. The tail fit is a log-linear fit over 31–118 ms.

| T, bias | | rest | shift (min − rest) | at 30 / 35 / 50 / 119 ms | CRIT τ (ngspice) | tail-fit τ |
|---|---|---|---|---|---|---|
| 0.35 mA | no grid | −3.9416 | −0.682 | −4.624 / −3.940 / −3.941 / −3.941 | none | 111.9 ms |
| 0.35 mA | grid | −3.9416 | −0.682 | −4.624 / −3.940 / −3.941 / −3.941 | none | 111.9 ms |
| 0.45 mA | no grid | −2.6527 | −0.843 | −3.496 / −2.651 / −2.651 / −2.652 | none | 109.1 ms |
| 0.45 mA | grid | −2.6527 | **−0.855** | −3.508 / **−2.662 / −2.661 / −2.657** | 89.0 ms | 107.1 ms |
| 0.5 mA | grid (no no-grid reference) | −2.0543 | −1.184 | −3.238 / −2.292 / −2.260 / −2.162 | 89.0 ms | 105.8 ms |
| 0.6 mA | no grid (ngspice; DK not comparable: exp cap) | −0.9488 | −1.102 | −2.051 / −0.950 / −0.950 / −0.949 | 89.0 ms | 106.0 ms |
| 0.6 mA | grid | −0.9488 | **−2.189** | −3.138 / **−1.969 / −1.830 / −1.401** | 89.0 ms | 102.5 ms |

**This is the blocking the issue asked for.** Without grid current the grid DC only follows the unbypassed cathode
during the burst and returns with its end (to < 2 mV at 35 ms). With it, at 0.45 mA the burst's positive peaks charge
Cin through the grid, leaving the grid 9–10 mV low at 35–50 ms and 4 mV low at 119 ms. At 0.6 mA it is 1.02 V low at
35 ms and still 0.45 V low at 119 ms, recovering with the Cin / grid-leak time constant (tail fit ~0.1 s; Cin 100 nF
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
