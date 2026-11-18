# Cut-down 12AX7 stage — validation, aliasing, and op-count estimate (#23)

Setup: 48 kHz base rate (`fs = 96 kHz` internal at `2×`), Drive = Bias = Output = 1, Dempwolf–Zölzer 12AX7 parameters (`prototypes/fullstage/dk/triode.py: DZ_12AX7_RSD1`), circuit values from `prototypes/fullstage/dk/circuit.py: VALUES` with the three pF valve capacitances (`CCG`, `CGP`, `CCP`) dropped.

> **Reference note:** `ngspice` is not installed in this environment, so no live `ngspice` runs were executed. All ngspice comparisons below are measured against the committed tight-tolerance reference JSONs (`prototypes/fullstage/spice/reference_dz.json` and `prototypes/fullstage/spice/reference_koren.json`). All numbers below come directly from:
> - `cd prototypes/cutdown && /tmp/issue23-venv/bin/python -m cutdown.validate` (writes `prototypes/cutdown/results.json`)
> - `cd prototypes/cutdown && /tmp/issue23-venv/bin/python -m pytest -q` (`24 passed in 4.32s`)
> - The diagnostic checks quoted in each section below.

---

## Architecture choices

1. **3-state circuit (`cutdown/circuit.py`):** Dropping `CCG = 2.3 pF`, `CGP = 2.4 pF`, and `CCP = 0.9 pF` leaves only `Cin = 22 nF`, `Ck = 22 µF`, and `Cout = 22 nF` (3 states). With those capacitors disconnected from direct inter-node coupling outside the nonlinear ports, `A` (3×3) is diagonal, `E0[0] = 0`, and the state-space matrices are sparse (`A`: 3 nonzeros, `B0`: 1, `C`: 4, `D[0]`: 1, `F[0]`: 1, `G`: 4, `H0`: 1).
2. **2-D lookup table (`cutdown/table.py`):** Stores pre-solved `(Ig, Ia)` on a `128 × 32` grid over `(p0, p1)` = `128 × 32 × 2 = 8,192` words (8 K words, matching `64 × 64 × 2 = 8,192` words and well within the DSP56300's 32 K Y-private memory).
   - Domain relative to `p_dc = (-1.5120 V, 289.8914 V)`: `p0` spans `p_dc[0] + [-15, +10] = [-16.512, +8.488] V` (`Δp0 = 0.1969 V`), `p1` spans `p_dc[1] ± 20 = [269.891, 309.891] V` (`Δp1 = 1.2903 V`).
   - Interpolation (`cubic0_lin1`): Catmull-Rom cubic along `p0` (the strongly nonlinear grid/cutoff axis) and linear along `p1` (the nearly linear plate axis), reading 8 grid points per port instead of 16 for full bicubic. An exact DC-rest offset `(dig_off, dia_off) = (-6.13e-14 A, -1.37e-8 A)` is subtracted so silence gives `Δi_nl = (0, 0)` exactly.
   - Measured LUT error against Newton over `[-13.5, 6.0] V × [285, 296] V` (200 × 25 grid): `max |ΔIa| = 1.508e-6 A`, `max |ΔIg| = 2.757e-8 A` (vs `1.718e-5 A` and `2.878e-7 A` for `64 × 64` bilinear).
3. **2× IIR half-band resampler (`dk/resample.py`):** Reuses `Resampler(48000, 2, "iir")`, a 6-coefficient polyphase allpass HIIR design (`N = 6` first-order allpass sections per way = 12 MACs/base sample total, ≥ 96.4 dB stopband rejection, `< 0.0001 dB` passband ripple, 3 base-rate samples nominal latency = 0.0625 ms). Reusing `dk/resample.py` at `os = 2, filt = "iir"` avoids inventing a custom filter while already keeping the resampler cost to 6 + 6 MACs/base sample and keeping filter leakage well below the 2× stage's own folding.

---

## 1. Error vs ngspice references and full DK model

Full DK comparisons use `standard/fir` (`8×` 6-state NR, from committed `prototypes/fullstage/dk/results.json`) and `eco/iir` (`4×` + 17.3 kHz small-cap pre-warp, 6-state NR, run inside `cutdown.validate`). Ablations show `3-state 2× IIR Newton` (isolating circuit reduction + 2× IIR from LUT error) and `64 × 64 bilinear` (showing piecewise-linear kink harmonics).

### Operating point, small-signal AC (20 Hz–20 kHz), and 4 V tone burst vs `reference_dz.json`

| metric | full DK `standard/fir` (8×) | full DK `eco/iir` (4×) | 3-state 2× IIR Newton | **cutdown `128×32 cubic0_lin1`** | ablation `64×64 bilinear` | full DK `CRIT` |
|---|---|---|---|---|---|---|
| OP worst rel err (`va, vk, ia, vgk`) | 2.15e-9 | 2.15e-9 | 2.15e-9 | **2.15e-9** | 2.15e-9 | < 1e-3 (pass) |
| AC max dev, stage (20 Hz–20 kHz) | 0.0518 dB | 0.0537 dB | 4.7786 dB | **4.7775 dB** | 4.6128 dB | — |
| AC max dev, chain (20 Hz–20 kHz) | 0.0520 dB | 0.0537 dB | 4.7786 dB | **4.7775 dB** | 4.6128 dB | < 0.1 dB (**FAIL**) |
| AC chain dev at 20 Hz | 0.0000 dB | +0.0012 dB | +0.0443 dB | **+0.0433 dB** | -0.1039 dB | — |
| AC chain dev at 100 Hz | 0.0000 dB | +0.0016 dB | +0.0598 dB | **+0.0587 dB** | -0.1051 dB | — |
| AC chain dev at 1 kHz | 0.0000 dB | +0.0027 dB | +0.0817 dB | **+0.0805 dB** | -0.0841 dB | — |
| AC chain dev at 10 kHz | -0.0067 dB | +0.0526 dB | +1.7949 dB | **+1.7938 dB** | +1.6291 dB | — |
| AC chain dev at 20 kHz | -0.0520 dB | -0.0537 dB | +4.7786 dB | **+4.7775 dB** | +4.6128 dB | — |
| Burst shift `min_rel` | 0.031 % | 0.008 % | 0.634 % | **0.634 %** | 0.792 % | < 0.5 % (**FAIL**) |
| Burst recovery `tau_rel` | 0.041 % | 0.082 % | 0.327 % | **0.327 %** | 0.163 % | < 1.0 % (pass) |
| Burst trajectory `traj_rel` | 0.003 % | 0.011 % | 0.215 % | **0.215 %** | 0.287 % | < 0.5 % (pass) |
| Burst `t_min` diff | 0.0000 ms | +0.0024 ms | -0.0020 ms | **-0.0020 ms** | -0.0020 ms | — |

- **Why AC fails the 0.1 dB full-DK `CRIT`:** Dropping `CCG`, `CGP`, and `CCP` removes the ~16 kHz Miller low-pass pole, leaving the 3-state stage flat up to 20 kHz (`+36.27 dB`) whereas `reference_dz.json` rolls off to `+34.48 dB` at 10 kHz and `+31.50 dB` at 20 kHz. Against the **3-state continuous-time analog response** (`cutdown.linear.analog_response`), `cutdown`'s whole chain matches within **0.00112 dB** across 20 Hz–20 kHz.
- **End-to-end multitone check** (`test_cutdown_e2e.py`, 1 mV tones 20 Hz–20 kHz through `CutdownStage.process()`): max gain deviation from the linearised 3-state LUT chain is **0.00143 dB** and max phase deviation is **0.0077°**.
- **Burst details (`cutdown`):** `rest_vgk = -1.5178 V`, `min_vgk = -4.3780 V`, `t_min = 29.998 ms`, `recovery_tau_s = 12.708 ms` (vs `reference_dz.json`: `rest = -1.5178 V`, `min = -4.3599 V`, `t_min = 30.000 ms`, `tau = 12.750 ms`). `min_rel` is 0.634% (over full DK's 0.5% `CRIT`, identical to 3-state Newton because `CCG/CGP/CCP` are absent).

### Harmonics (1 kHz sine at 0.05, 0.5, 2, 8 V peak) vs `reference_dz.json`

Values are `deviation from reference_dz.json (dB)` for H1–H5, and THD (%). Full DK `CRIT` tests `|dev| < 0.1 dB` for harmonics with reference > −90 dBc (all H2–H5 at 0.5/2/8 V except H5 at 0.5 V at −97.87 dBc; at 0.05 V only H2 at −55.43 dBc is above −90 dBc).

| level | metric | ref `dz` | `standard/fir` (8×) | `eco/iir` (4×) | 3-state 2× NR | **cutdown `128×32 c0l1`** | ablation `64×64 bilin` |
|---|---|---|---|---|---|---|---|
| **0.05 V** | H1 dev (dB) | (3.226 V) | +0.0000 | +0.0027 | +0.0816 | **+0.0799** | -0.0836 |
| | H2 dev (dB) | (-55.43 dBc) | -0.0001 | +0.0086 | +0.2245 | **+1.2248** | -59.1417 |
| | THD (%) | 0.1693 % | 0.1693 % | 0.1695 % | 0.1738 % | **0.1950 %** | 0.0002 % |
| **0.5 V** | H1 dev (dB) | (32.082 V) | +0.0000 | +0.0027 | +0.0804 | **+0.0803** | +0.0503 |
| | H2 dev (dB) | (-35.05 dBc) | -0.0000 | +0.0087 | +0.2278 | **+0.2365** | +0.6389 |
| | H3 dev (dB) | (-55.86 dBc) | -0.0001 | +0.0123 | +0.3171 | **+0.3700** | +6.1588 |
| | H4 dev (dB) | (-76.75 dBc) | -0.0002 | +0.0098 | +0.2820 | **+1.1458** | +12.5166 |
| | THD (%) | 1.7756 % | 1.7756 % | 1.7774 % | 1.8229 % | **1.8249 %** | 1.9426 % |
| **2 V** | H1 dev (dB) | (99.472 V) | +0.0000 | +0.0009 | +0.0251 | **+0.0253** | -0.0504 |
| | H2 dev (dB) | (-16.85 dBc) | -0.0000 | +0.0075 | +0.2117 | **+0.2119** | +0.2586 |
| | H3 dev (dB) | (-21.69 dBc) | -0.0001 | +0.0085 | +0.2133 | **+0.2087** | +0.0772 |
| | H4 dev (dB) | (-34.82 dBc) | -0.0000 | +0.0086 | +0.3956 | **+0.3699** | +0.4300 |
| | H5 dev (dB) | (-43.03 dBc) | -0.0008 | +0.0167 | +0.2915 | **+0.1864** | -1.9164 |
| | THD (%) | 16.7002 % | 16.7001 % | 16.7151 % | 17.1167 % | **17.1132 %** | 17.1094 % |
| **8 V** | H1 dev (dB) | (101.392 V) | -0.0000 | +0.0000 | +0.0029 | **+0.0028** | -0.0009 |
| | H2 dev (dB) | (-5.29 dBc) | -0.0001 | +0.0019 | +0.0518 | **+0.0520** | +0.0457 |
| | H3 dev (dB) | (-21.46 dBc) | -0.0002 | +0.0105 | +0.2672 | **+0.2692** | +0.2595 |
| | H4 dev (dB) | (-15.67 dBc) | -0.0005 | +0.0036 | +0.1120 | **+0.1111** | +0.0690 |
| | H5 dev (dB) | (-15.49 dBc) | -0.0007 | +0.0103 | +0.2969 | **+0.2965** | +0.2329 |
| | THD (%) | 60.6926 % | 60.6912 % | 60.7166 % | 61.3780 % | **61.3782 %** | 61.2528 % |

- **Does `cutdown` pass full DK's `CRIT` (`< 0.1 dB`)?** **No.** Dropping the pF Miller capacitances alone moves H2–H5 by `+0.05` to `+0.40 dB` (visible in `3-state 2× NR`), and the `128×32 cubic0_lin1` LUT adds `+1.22 dB` on H2 at 0.05 V and `+1.15 dB` on H4 at 0.5 V (Catmull-Rom's $C^1$ second-derivative jump across grid boundaries). At 2 V and 8 V, `cutdown` tracks `3-state 2× NR` within `0.11 dB` on H2–H5 and `0.0035 %` on THD.
- **Why `64×64 bilinear` is worse:** At 0.05 V, the entire swing stays inside a single bilinear cell (`THD = 0.0002%`, H2 off by `-59.1 dB`); at 0.5 V, crossing bilinear kinks inflates H3 by `+6.2 dB` and H4 by `+12.5 dB`.

### Comparison vs `reference_koren.json` (includes DZ vs Koren valve-model mismatch)

| metric | full DK `standard/fir` vs `koren` | **cutdown vs `koren`** |
|---|---|---|
| OP worst rel err (`vgk`) | 4.30 % | **4.30 %** |
| AC max dev, chain (20 Hz–20 kHz) | 0.8080 dB (@ 380 Hz) | **5.3141 dB (@ 20 kHz)** |
| AC chain dev at 1 kHz / 20 kHz | +0.8066 / +0.4847 dB | **+0.8871 / +5.3141 dB** |
| Sine THD (0.05 / 0.5 / 2 / 8 V; ref 0.269 / 2.725 / 13.733 / 57.749 %) | 0.169 / 1.776 / 16.700 / 60.691 % | **0.195 / 1.825 / 17.113 / 61.378 %** |
| Burst `min_rel` / `tau_rel` / `traj_rel` | 6.88 % / 4.21 % / 3.15 % | **7.59 % / 4.56 % / 3.38 %** |

---

## 2. Aliasing (offset-tone sweep vs full DK model)

Measured using `prototypes/fullstage/dk/aliasing.py` (`SWEEP_HZ = 1037…19037 Hz`, `0.25 s` settle, `1.0 s` analysis window, periodic Hann, `±3` bin masks, total non-harmonic energy 20 Hz–20 kHz in dBc). Full DK columns come from committed `prototypes/fullstage/dk/results.json` and `dk/aliasing_64x.json`.

**2 V peak** (worst cutdown tone: **19037 Hz at −32.9 dBc**, vs `eco/iir` −75.7 dBc at 16037 Hz, `standard/iir` −114.0 dBc at 13037 Hz):

| tone (Hz) | **cutdown 2× IIR** | hq FIR | hq IIR | standard FIR | standard IIR | eco FIR | eco IIR | 64× FIR | 64× IIR |
|---|---|---|---|---|---|---|---|---|---|
| 1037 | -98.5 | -173.9 | -173.7 | -173.9 | -173.8 | -173.9 | -173.8 | -173.9 | -173.7 |
| 2037 | -80.9 | -167.7 | -155.5 | -167.8 | -156.2 | -168.0 | -155.6 | -167.7 | -154.8 |
| 3037 | -75.2 | -166.7 | -140.3 | -166.9 | -140.5 | -166.8 | -141.5 | -166.6 | -140.3 |
| 4037 | -73.9 | -155.2 | -136.8 | -155.3 | -136.2 | -140.5 | -136.7 | -155.2 | -137.7 |
| 5037 | -74.8 | -145.2 | -132.1 | -145.3 | -133.2 | -123.0 | -122.6 | -145.1 | -131.9 |
| 6037 | -64.6 | -147.0 | -144.2 | -147.0 | -146.7 | -104.7 | -104.7 | -146.9 | -143.3 |
| 7037 | -60.8 | -147.0 | -127.7 | -144.0 | -126.5 | -98.3 | -98.3 | -146.9 | -129.0 |
| 8037 | -54.1 | -140.1 | -122.9 | -134.6 | -122.7 | -90.0 | -90.0 | -140.1 | -122.4 |
| 9037 | -52.1 | -142.1 | -121.5 | -133.7 | -120.8 | -90.7 | -90.7 | -142.1 | -122.0 |
| 10037 | -50.0 | -134.4 | -117.1 | -130.8 | -116.1 | -94.0 | -94.0 | -134.3 | -117.8 |
| 11037 | -50.2 | -128.4 | -129.7 | -126.9 | -126.3 | -83.5 | -83.5 | -128.4 | -131.2 |
| 12037 | -51.7 | -136.7 | -119.0 | -132.6 | -120.5 | -81.7 | -81.7 | -136.6 | -118.5 |
| 13037 | -40.2 | -130.8 | -115.8 | -129.2 | -114.0 | -77.0 | -77.0 | -130.7 | -116.4 |
| 14037 | -40.1 | -125.6 | -115.2 | -125.2 | -115.6 | -78.3 | -78.3 | -125.6 | -115.1 |
| 15037 | -40.3 | -139.3 | -128.1 | -136.3 | -130.0 | -76.1 | -76.1 | -139.2 | -128.0 |
| 16037 | -38.3 | -124.9 | -115.3 | -125.0 | -116.7 | -75.7 | -75.7 | -124.8 | -115.0 |
| 17037 | -38.4 | -128.9 | -118.6 | -129.3 | -116.9 | -77.7 | -77.7 | -128.8 | -119.3 |
| 18037 | -38.3 | -123.0 | -119.8 | -123.2 | -121.2 | -79.2 | -79.2 | -123.0 | -119.6 |
| 19037 | **-32.9** | -121.2 | -126.4 | -121.4 | -122.9 | -85.3 | -85.3 | -121.2 | -126.8 |

**8 V peak** (worst cutdown tone: **19037 Hz at −12.1 dBc**, vs `eco/iir` −32.6 dBc, `standard/iir` −51.7 dBc):

| tone (Hz) | **cutdown 2× IIR** | hq FIR | hq IIR | standard FIR | standard IIR | eco FIR | eco IIR | 64× FIR | 64× IIR |
|---|---|---|---|---|---|---|---|---|---|
| 1037 | -83.5 | -152.0 | -131.5 | -152.2 | -131.5 | -121.1 | -120.8 | -152.0 | -131.6 |
| 2037 | -59.4 | -135.3 | -116.3 | -104.1 | -103.9 | -79.1 | -79.1 | -136.1 | -116.3 |
| 3037 | -51.3 | -110.5 | -108.8 | -85.2 | -85.2 | -66.7 | -66.7 | -138.4 | -113.8 |
| 4037 | -41.3 | -97.3 | -97.1 | -77.0 | -77.0 | -59.8 | -59.8 | -128.5 | -111.2 |
| 5037 | -40.1 | -89.9 | -89.9 | -71.9 | -71.9 | -56.8 | -56.8 | -128.0 | -112.3 |
| 6037 | -42.6 | -84.8 | -84.8 | -68.7 | -68.7 | -53.5 | -53.5 | -123.3 | -121.4 |
| 7037 | -31.3 | -82.2 | -82.1 | -65.2 | -65.2 | -51.7 | -51.7 | -123.3 | -107.3 |
| 8037 | -30.0 | -79.0 | -79.0 | -63.3 | -63.3 | -46.2 | -46.2 | -119.1 | -101.2 |
| 9037 | -27.5 | -76.3 | -76.2 | -62.3 | -62.3 | -47.6 | -47.6 | -120.3 | -101.8 |
| 10037 | -21.7 | -74.9 | -74.9 | -59.8 | -59.8 | -44.0 | -44.0 | -117.8 | -102.5 |
| 11037 | -20.5 | -73.0 | -73.0 | -58.1 | -58.1 | -44.0 | -44.0 | -112.4 | -113.3 |
| 12037 | -20.5 | -72.6 | -72.6 | -58.1 | -58.1 | -37.5 | -37.5 | -113.3 | -105.7 |
| 13037 | -19.7 | -71.0 | -71.0 | -55.3 | -55.3 | -41.6 | -41.6 | -110.1 | -100.9 |
| 14037 | -19.7 | -69.7 | -69.7 | -55.1 | -55.1 | -37.1 | -37.1 | -107.7 | -101.0 |
| 15037 | -22.5 | -68.2 | -68.2 | -54.9 | -54.9 | -36.9 | -36.9 | -106.9 | -105.9 |
| 16037 | -14.4 | -67.2 | -67.2 | -53.3 | -53.3 | -35.7 | -35.7 | -104.1 | -99.5 |
| 17037 | -14.7 | -66.2 | -66.2 | -52.0 | -52.0 | -40.4 | -40.4 | -103.9 | -99.5 |
| 18037 | -14.7 | -66.7 | -66.8 | -52.2 | -52.2 | -34.8 | -34.8 | -102.8 | -102.3 |
| 19037 | **-12.1** | -66.0 | -66.0 | -51.7 | -51.7 | -32.6 | -32.6 | -101.0 | -100.2 |

- **Source split at the worst tone (19037 Hz):**
  - 2 V: `total = -32.93 dBc`, `internal = -32.93 dBc`, `resampler = -108.80 dBc`, largest component `−36.20 dBc` at `19852 Hz` (H4 = 76148 Hz folding across 96 kHz).
  - 8 V: `total = -12.11 dBc`, `internal = -12.11 dBc`, `resampler = -97.40 dBc`, largest component `−16.95 dBc` at `815 Hz` (H5 = 95185 Hz folding across 96 kHz).
- As expected in #23, 2× oversampling (`96 kHz` internal rate, plus the missing HF Miller roll-off) has ~20.5 dB more aliasing at 8 V / 19037 Hz than 4× `eco`, and internal folding dominates resampler leakage by > 75 dB.

---

## 3. Paper op-count ESTIMATE vs ~300 cycles/sample mono

> **ESTIMATE ONLY:** The numbers below are a paper count of arithmetic and memory operations in the runtime path of `CutdownStage` (`cutdown/stage.py`, `cutdown/table.py`, `dk/resample.py` at `os = 2, filt = "iir"`). They are **not** a measured DSP56300 cycle count and **not** a DSP56300 instruction schedule; measured cycle counts on the 24-bit fixed-point DSP56300 belong to issue #24.

Target from #11 / #23: **~300 cycles/sample mono** (within the **~411 cycles/sample** Octatrack slot budget).

### Operation count per base-rate sample (48 kHz mono; 2 internal 96 kHz samples)

| runtime stage | rate | multiplies | adds / subs | table / coeff / state mem ops | notes |
|---|---|---|---|---|---|
| **2× IIR half-band up** (`N = 6` 1st-order allpasses) | 1× → 2× | 6 | 12 | 6 coeff + 24 state R/W | 1 mul + 2 adds per 1-mul allpass section |
| **2× IIR half-band down** (`N = 6` allpasses + `0.5(y0+y1)`) | 2× → 1× | 7 | 13 | 6 coeff + 25 state R/W | includes `0.5 * (y0 + y1)` and 1-sample odd delay |
| **3-state `p = G x + H0 s + p_dc`** (sparse: `G` has 4 nz, `H0` has 1 nz) | 2× | 2 × 5 = 10 | 2 × 5 = 10 | 2 × 10 | dense 3-state would be 2 × 8 muls |
| **2-D LUT `128×32 cubic0_lin1`** (`Ig, Ia` ports, 8 corners/port) | 2× | 2 × 25 = 50 | 2 × 33 = 66 | 2 × 16 table loads | per call: norm (2m, 4a) + CR weights (7m, 7a) + 2 ports × (4 `T0+fw*(T1-T0)` + 4-tap dot = 8m, 11a); `n1 = 32` is shift |
| **3-state output `y = D0 x + F0 di`** (sparse: `D0` 1 nz, `F0` 1 nz, `E0=0`) | 2× | 2 × 2 = 4 | 2 × 2 = 4 | 2 × 4 | includes `ia - ia0` sub |
| **3-state update `x = A x + B0 s + C di`** (sparse: `A` diag 3 nz, `B0` 1 nz, `C` 4 nz) | 2× | 2 × 8 = 16 | 2 × 6 = 12 | 2 × 12 | includes `ig - ig0` sub |
| **Total ESTIMATE (`128×32 cubic0_lin1`)** | **1× base** | **93 muls** | **117 adds/subs** | **~123 mem ops** | **210 arithmetic ops** (+ index clamps ~230–280 ops) |
| *Ablation (`64×64 bilinear`, 4 corners/port)* | *1× base* | *59 muls* | *83 adds/subs* | *~107 mem ops* | *per call: norm (2m, 4a) + 2 ports × 3 lerps (3m, 6a) = 8m, 16a; total 142 arithmetic ops* |

- **Memory footprint:** `128 × 32 × 2 ports = 8,192` table words (25% of the DSP56300's 32 K Y-private memory), plus 3 state variables, 12 IIR allpass states + 12 coefficients, and 15 sparse state-space coefficients.
- **Assessment vs target (ESTIMATE):** Even without parallel MAC+move overlap, the `128×32 cubic0_lin1` path totals **210 arithmetic ops** (~230–280 ops including grid-index clamping and base-rate drive/output scaling), sitting below the **~300 cycles/sample** estimate and well inside the **~411 cycles/sample** Octatrack slot budget. Whether fixed-point scaling, saturation, and 24-bit precision preserve this margin on hardware is the scope of #24.
