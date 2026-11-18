# Cut-down 12AX7 stage — validation, aliasing reduction methods, and op-count estimate (#23)

Setup: 48 kHz and 44.1 kHz (Octatrack) base rates (`fs = 96 kHz` / `88.2 kHz` internal at `2×`), Drive = Bias = Output = 1, Dempwolf–Zölzer 12AX7 parameters (`prototypes/fullstage/dk/triode.py: DZ_12AX7_RSD1`), circuit values from `prototypes/fullstage/dk/circuit.py: VALUES` with the three pF valve capacitances (`CCG`, `CGP`, `CCP`) dropped.

> **Reference note:** `ngspice` is not installed in this environment, so no live `ngspice` runs were executed. All ngspice comparisons below are measured against the committed tight-tolerance reference JSONs (`prototypes/fullstage/spice/reference_dz.json` and `prototypes/fullstage/spice/reference_koren.json`). All measured numbers below come directly from commands run in this session:
> - `cd prototypes/cutdown && /tmp/issue23b-venv/bin/python -m cutdown.validate` (writes `prototypes/cutdown/results.json`, `85.8 s` wall)
> - `cd prototypes/cutdown && /tmp/issue23b-venv/bin/python -m pytest -q` (`68 passed in 14.85s`)
> - `/tmp/issue23b-venv/bin/ruff check prototypes/cutdown` (`All checks passed!`)

---

## Architecture choices

1. **3-state circuit (`cutdown/circuit.py`):** Dropping `CCG = 2.3 pF`, `CGP = 2.4 pF`, and `CCP = 0.9 pF` leaves only `Cin = 22 nF`, `Ck = 22 µF`, and `Cout = 22 nF` (3 states). With those capacitors disconnected from direct inter-node coupling outside the nonlinear ports, `A` (3×3) is diagonal, `E0[0] = 0`, and the state-space matrices are sparse (`A`: 3 nonzeros, `B0`: 1, `C`: 4, `D[0]`: 1, `F[0]`: 1, `G`: 4, `H0`: 1).
2. **2-D lookup table (`cutdown/table.py`):** Stores pre-solved `(Ig, Ia)` on a `128 × 32` grid over `(p0, p1)` = `128 × 32 × 2 = 8,192` words (8 K words, matching `64 × 64 × 2 = 8,192` words and well within the DSP56300's 32 K Y-private memory).
   - Domain relative to `p_dc = (-1.5120 V, 289.8914 V)`: `p0` spans `p_dc[0] + [-15, +10] = [-16.512, +8.488] V` (`Δp0 = 0.1969 V`), `p1` spans `p_dc[1] ± 20 = [269.891, 309.891] V` (`Δp1 = 1.2903 V`).
   - Interpolation (`cubic0_lin1`): Catmull-Rom cubic along `p0` (the strongly nonlinear grid/cutoff axis) and linear along `p1` (the nearly linear plate axis), reading 8 grid points per port instead of 16 for full bicubic. An exact DC-rest offset `(dig_off, dia_off) = (-6.13e-14 A, -1.37e-8 A)` is subtracted so silence gives `Δi_nl = (0, 0)` exactly.
3. **2× IIR half-band resampler (`dk/resample.py`):** Reuses `Resampler(48000, 2, "iir")`, a 6-coefficient polyphase allpass HIIR design (`N = 6` first-order allpass sections per way = 12 MACs/base sample total, ≥ 96.4 dB stopband rejection, `< 0.0001 dB` passband ripple, 3 base-rate samples nominal latency = 0.0625 ms at 48 kHz).

---

## Aliasing-reduction & Miller-pole methods (#23)

### Method A: one-pole Miller low-pass (`miller_lpf=True`)

- **Physical derivation (no invented component values):** From `dk.circuit.VALUES` (`CCG = 2.3 pF`, `CGP = 2.4 pF`, `CCP = 0.9 pF`, `Rs = 1 kΩ`, `Rg = 1 MΩ`, `Rgs = 68 kΩ`, `Ra = 100 kΩ`, `Rl = 1 MΩ`) and the DC operating-point Jacobian `J` (`gm = 1.9474 mA/V`, `gds = 1.887e-5 S`):
  - Grid Thévenin resistance (HF, `Cin` shorted): $R_{g,\text{th}} = R_{gs} + (R_s \parallel R_g) = 68.999\text{ k}\Omega$
  - Plate effective resistance (HF, `Ck, Cout` shorted): $R_{\text{plate}} = (1/g_{ds}) \parallel R_a \parallel R_l = 33.479\text{ k}\Omega$
  - Open-circuit time-constant (OCTC) sum of the three dropped pF caps:
    $$\tau_{\text{OCTC}} = C_{CG} R_{g,\text{th}} + C_{GP} (R_{g,\text{th}} + R_{\text{plate}} + g_m R_{g,\text{th}} R_{\text{plate}}) + C_{CP} R_{\text{plate}} = 11.231\ \mu\text{s} \implies f_c = \mathbf{14{,}171.2\text{ Hz}}$$
- **Placement and discretisation:** Placed **before** the 3-state nonlinear stage at internal rate $f_s = 96\text{ kHz}$ (modeling the grid-side Miller capacitance attenuating high frequencies before they drive the nonlinear triode; a post-nonlinearity LPF cannot remove harmonics that have already folded below 20 kHz and actually worsens `dBc` at 19 kHz by attenuating the fundamental). Implemented as a first-order IIR:
  $$H_A(z) = \frac{b_0 + b_1 z^{-1}}{1 - p z^{-1}}, \quad p = e^{-2\pi f_c / f_s} = 0.395541, \quad b_0 = (1-p)(1-\alpha),\ b_1 = (1-p)\alpha$$
  where $\alpha = 0.096604$ is solved in closed form from $f_c$ and $f_{\text{ref}} = 20\text{ kHz}$ so $|H_A(20\text{ kHz})| = 1/\sqrt{1 + (20\text{ kHz}/f_c)^2}$ (avoiding the `-0.48 dB` 20 kHz droop of bilinear or the `+0.23 dB` excess of all-pole). DC gain is $(b_0 + b_1)/(1 - p) = 1$ exactly (`0 V` stays `0 V`).
- **Latency / group delay:** Adds **0 integer base-rate samples** (`latency_samples = 3`). Group delay of $H_A(z)$ is `0.7455` internal samples = **0.3727 base-rate samples (`0.00777 ms`) at 1 kHz**, `0.1895` base samples at 10 kHz, and `-0.0101` base samples at 20 kHz (replacing the physical RC delay $\tau_{\text{OCTC}} = 11.23\,\mu\text{s} = 0.539$ base samples present in the 6-state reference).

### Method B: 2-D LUT antiderivative antialiasing (`adaa=1`, and `adaa=2` evaluated & ruled out)

- **Citations (already documented in `prototypes/fullstage/research/ALIASING.md` and `RESEARCH-numerics.md`):** Parker, Zavalishin, Le Bivic (DAFx-16) for 1st-order ADAA; Bilbao, Esqueda, Parker, Välimäki (IEEE SPL 24(7), 2017) for 2nd-order ADAA; Holters (DAFx-19 / Appl. Sci. 10(1):20, 2020) and Albertini, Bernardini, Sarti (DAFx-20 / JAES 69(7/8), 2021) for stateful / multiport ADAA.
- **2-D formulation & 24-bit fixed-point conditioning (`cutdown/table.py: adaa1_dev`):** In `CutdownStage`, `H0 = [0.9988, 0.0]` so the input `vin` drives `p0` directly (`max |Δp0| = 9.33 V/sample` at 8 V / 19 kHz), whereas `p1` depends only on the slow `Ck`/`Cout` states (`max |Δp1| = 0.007 V/sample` at 19 kHz vs `Δp1 = 1.29 V` grid spacing). We integrate `cubic0_lin1` analytically along `p0` at $p_{1m} = \frac{1}{2}(p_1[n] + p_1[n-1])$:
  $$\bar{i}_{\text{ADAA1}}[n] = \frac{F_1(u[n], p_{1m}) - F_1(u[n-1], p_{1m})}{u[n] - u[n-1]}, \qquad u = (p_0 - p_{0,\min}) / \Delta p_0$$
  Naive cumulative integrals of total `(Ig, Ia)` (`s1_ia` up to `0.119 A·cell`) suffer severe cancellation in 24-bit fixed point / `float32` (`-44.8 dB` SNR at `1 mV`, `-16.1 dB` at `10 mV` when `F1` is quantized to 24 bits). To eliminate that cancellation:
  1. **Deviation-form tables (`dtbl`, `ds1`):** `(i_dc + d_off)` is folded into `dtbl` at build time and `ds1` is anchored to `0` at the rest cell `i_dc_cell`.
  2. **Division-free same-cell pairs (`i == i_prev`):** Evaluates the exact divided-difference weights $D_k(f_u, f_{u,\text{prev}}) = (W_k(f_u) - W_k(f_{u,\text{prev}})) / (f_u - f_{u,\text{prev}})$ directly on `dtbl` — zero `ds1` reads, zero divisions, and no `|du| < tol` fallback needed.
  3. **Boundary-split cross-cell pairs (`i != i_prev`):** Splits across the cell boundaries as $\big[(1-f_a) D_k(1, f_a) \cdot T_{i_a} + (\text{ds1}[i_b] - \text{ds1}[i_a+1]) + W_k(f_b) \cdot T_{i_b}\big] / |du|$ with a single shared `1/|du|` reciprocal (using `ADAA_TOL_1 = 1e-6` only on rare tiny boundary crossings).
  In `float64`, `adaa1_dev` matches `f1_slices` to `< 1e-14 A` (`Ig`) / `< 1e-13 A` (`Ia`). Quantizing `dtbl` and `ds1` to 24-bit fractional fixed point (`2^ceil(log2(max|arr|)) * 2^-23`) gives end-to-end `CutdownStage.process()` SNRs of **`128.7 dB` (`1 mV`), `142.6 dB` (`10 mV`), `144.8 dB` (`50 mV`), `145.7 dB` (`0.5 V`), `141.4 dB` (`2 V`)**.
- **Memory & `swap_bias` open item (#24, #73):** `s1`/`ds1` adds 8,192 words (`16,384` words total = 50% of the DSP56300's 32 K Y-private memory) for one fixed bias. In Python, `swap_bias(bias)` rebuilds 4,096 Newton solves + `ds1`; on the DSP56300, audio-thread bias smoothing (#73/#97) cannot rebuild tables per knob move and storing multiple 16 K bias tables collides with 32 K Y-memory, so #24 will need either a fixed bias, `Method A` (`8,192` words), or an `Rk`-parametrised table.
- **Why `adaa=2` (Method B2) is ruled out:** Second-order ADAA (`adaa=2`, Bilbao et al. 2017) requires `s2` (`24,576` total words = 75% of 32 K Y-private memory), `324` arithmetic ops + `4` divisions/base sample, `1/Δu^2` division which is ill-conditioned in 24-bit fixed point on the DSP56300, adds `-6.32 dB` linearised roll-off at 20 kHz (`(1 + z^-1 + z^-2)/3`, severely over-darkening `A+B2` to `~ -6 dB`), and buys only `0.09 dB` over `B1` at 8 V / 19037 Hz (`-34.16` vs `-34.07 dBc`). It is retained as preset `"method_b2"` and **not** recommended.

### Treble droop compensation (`treble_comp=True`, valid only with `adaa=1`)

- **Origin of the A + B1 droop:** At `96 kHz` (`88.2 kHz`) internal rate, first-order ADAA acts small-signal as $H_{\text{ADAA1}}(z) = \frac{1 + z^{-1}}{2}$, whose magnitude $\cos(\pi f / f_s)$ is `-2.0107 dB` (`-2.420 dB`) at `20 kHz` (combined with Method A's `+0.0181 dB`, giving the measured `-1.9921 dB` at 48 kHz and `-2.4019 dB` at 44.1 kHz). Passing `treble_comp=True` with `adaa != 1` raises `ValueError`.
- **Design & placement selection (measured, not assumed):**
  - *Pre-nonlinearity boost (ruled out):* Placing a compensating shelf before the stage drives the triode harder at HF and degrades 2 V aliasing by up to `+15.3 dB` (`-67.28 -> -51.95 dBc` at `19037 Hz`) and 8 V aliasing by `+2.1 dB` (`-33.62 -> -31.50 dBc` at `16037 Hz`).
  - *3-tap symmetric FIR (ruled out):* Costs `4` ops/base sample at `48 kHz` (`0.48 dB` max error vs ADAA1) or `8` ops/base sample at `96 kHz` (`0.13 dB` best error) — neither meets the `< 0.1 dB` AC gate.
  - *Internal-rate 1st-order IIR (ruled out):* Exact inverse of $\frac{1+z^{-1}}{2}$ puts an unstable pole at $z = -1$, and running at `2×` doubles the op count.
  - *Selected: base-rate post-decimation 1st-order IIR high shelf (`FirstOrderSection` on `"out"`):*
    $$H_C(z_{\text{base}}) = \frac{b_0 + b_1 z_{\text{base}}^{-1}}{1 - p z_{\text{base}}^{-1}}, \qquad |H_C(\omega)|^2 = \frac{1 + N x}{1 + D x}, \quad x = 1 - \cos\!\left(\frac{2\pi f}{f_{\text{base}}}\right)$$
    Unit DC gain ($(b_0 + b_1)/(1 - p) = 1$) is structural. At construction time, `(f1, f2)` in `[20 Hz, min(20 kHz, 0.49*fs_base)]` are solved for minimax Chebyshev ripple of $|H_C(f)| \cos(\pi f / f_s)$, then plugged into the closed-form 2-point shelf ($T_k = 1/\cos^2(\pi f_k/f_s)$, $s_k = (T_k-1)/x_k$):
    $$D = \frac{s_1 - s_2}{T_2 - T_1},\quad N = s_2 + T_2 D,\quad p = \frac{D + 1 - \sqrt{2D + 1}}{D},\quad b_0 = \frac{1 - p}{2}\left(1 + \sqrt{1 + 2N}\right),\quad b_1 = 1 - p - b_0$$
    - **48 kHz / 2×:** `(f1, f2) = (14,353.0, 19,367.1) Hz` -> `b0 = 1.109763`, `b1 = 0.182034`, `p = -0.291797` (comp error `0.0297 dB`, chain AC max dev **`0.0607 dB` — PASS `< 0.1 dB`**).
    - **44.1 kHz / 2×:** `(f1, f2) = (14,581.0, 19,383.4) Hz` -> `b0 = 1.111567`, `b1 = 0.223817`, `p = -0.335383` (comp error `0.0620 dB`, chain AC max dev **`0.0690 dB` — PASS `< 0.1 dB`**).
    - *(Note for #24 fixed point: `b0 > 1` requires `b0/2` + `ASL` or scaled form, plus `+2 dB` HF headroom.)*
- **Cost & latency:** Adds **`+5` ops/base sample** (`3` muls + `2` adds, `0` divs, `0` table words). Adds `0` integer latency samples; analytical group delay at `1 kHz` is `-0.0849` base samples at `48 kHz` (`3 + 0.538 frac` = `0.0737 ms`) and `-0.0835` base samples at `44.1 kHz` (`4 + 0.499 frac` = `0.1020 ms`, since `Resampler(44100, 2, "iir")` uses 8 allpasses/way with `latency_samples = 4`).
- **Aliasing impact vs `A + B1`:** Post-decimation linear compensation does **not** undo Method A + B1's aliasing reduction on worst-case tones at 48 kHz: 2 V worst improves by **`0.58 dB`** (`-61.42 -> -62.00 dBc` at `16037 Hz`, because the `16037 Hz` fundamental gets boosted by `+1.27 dB` while its dominant `222 Hz` alias is unboosted), and 8 V worst improves by **`0.02 dB`** (`-33.17 -> -33.20 dBc` at `19037 Hz`). For lower test tones (`1–11 kHz`) whose aliases fall in the `10–20 kHz` boost band, total aliasing lifts by `+0.36` to `+1.49 dB` (e.g. `1037 Hz` goes `-117.5 -> -116.3 dBc` at 2 V and `-99.4 -> -97.9 dBc` at 8 V).

---

## Comparison of methods (`cutdown.validate`, 48 kHz base / 2× IIR)

All metrics below are measured directly from `cutdown/results.json` against `reference_dz.json` and the full 38-point `dk/aliasing.py` sweep (full DK `CRIT`: AC `< 0.1 dB`, harmonics `< 0.1 dB` above `-90 dBc`, burst `< 0.5% / 1.0% / 0.5%`; op-count target `~300` mono ops/base sample).

| metric | **baseline** (`no A, adaa=0`) | **A only** (`miller_lpf`) | **B1** (`adaa=1`) | **B2** (`adaa=2`, **over budget**) | **A + B1** (`no comp`) | **A + B1 + comp** (**default**) | full DK `CRIT` / target |
|---|---|---|---|---|---|---|---|
| **Aliasing 2 V worst** (tone) | -32.93 dBc (19037 Hz) | -46.42 dBc (16037 Hz) | -48.48 dBc (19037 Hz) | -55.16 dBc (19037 Hz) | -61.42 dBc (16037 Hz) | **-62.00 dBc** (16037 Hz) | (full DK `eco/iir`: -75.7) |
| **Aliasing 8 V worst** (tone) | -12.11 dBc (19037 Hz) | -14.47 dBc (19037 Hz) | -29.36 dBc (16037 Hz) | -34.16 dBc (19037 Hz) | -33.17 dBc (19037 Hz) | **-33.20 dBc** (19037 Hz) | (full DK `eco/iir`: -32.6) |
| **AC max dev** (20 Hz–20 kHz) | 4.777 dB (20 kHz) | **0.059 dB** (363 Hz, **PASS**) | 2.767 dB (20 kHz) | 1.142 dB (20 kHz) | 1.992 dB (20 kHz) | **0.061 dB** (302 Hz, **PASS**) | `< 0.1 dB` |
| AC dev @ 20 Hz | +0.043 dB | +0.043 dB | +0.045 dB | +0.046 dB | +0.045 dB | +0.045 dB | — |
| AC dev @ 1 kHz | +0.081 dB | +0.059 dB | +0.077 dB | +0.071 dB | +0.056 dB | +0.060 dB | — |
| AC dev @ 10 kHz | +1.794 dB | +0.028 dB | +1.321 dB | +0.508 dB | -0.444 dB | -0.000 dB | — |
| AC dev @ 20 kHz | +4.777 dB | +0.018 dB | +2.767 dB | -1.142 dB | -1.992 dB | **-0.011 dB** | — |
| **Burst `min_rel`** | 0.634 % (FAIL) | **0.346 % (PASS)** | 0.674 % (FAIL) | 0.680 % (FAIL) | **0.386 % (PASS)** | **0.386 % (PASS)** | `< 0.5 %` |
| **Burst `tau_rel`** | 0.327 % (PASS) | 0.490 % (PASS) | 0.327 % (PASS) | 0.327 % (PASS) | 0.490 % (PASS) | 0.490 % (PASS) | `< 1.0 %` |
| **Burst `traj_rel`** | 0.215 % (PASS) | 0.114 % (PASS) | 0.229 % (PASS) | 0.228 % (PASS) | 0.127 % (PASS) | 0.127 % (PASS) | `< 0.5 %` |
| **0.05 V** H1 / H2 dev (dB) | +0.080 / +1.225 | +0.058 / +1.204 | +0.077 / +1.220 | +0.071 / +1.213 | +0.055 / +1.200 | +0.059 / +1.212 | H2 `< 0.1 dB` |
| **0.05 V** THD (ref 0.1693 %) | 0.1950 % | 0.1945 % | 0.1949 % | 0.1947 % | 0.1944 % | 0.1947 % | — |
| **0.5 V** H2 / H3 / H4 dev (dB) | +0.237 / +0.370 / +1.146 | +0.213 / +0.327 / +1.106 | +0.232 / +0.355 / +1.122 | +0.224 / +0.335 / +1.090 | +0.208 / +0.312 / +1.080 | +0.220 / +0.345 / +1.141 | `< 0.1 dB` |
| **0.5 V** THD (ref 1.7756 %) | 1.8249 % | 1.8199 % | 1.8239 % | 1.8221 % | 1.8189 % | 1.8215 % | — |
| **2 V** H2..H5 max \|dev\| (dB) | 0.370 dB (H4) | 0.275 dB (H5) | 0.328 dB (H4) | 0.271 dB (H4) | 0.232 dB (H5) | 0.331 dB (H5) | `< 0.1 dB` |
| **2 V** THD (ref 16.7002 %) | 17.1132 % | 17.0168 % | 17.1015 % | 17.0760 % | 17.0051 % | 17.0409 % | — |
| **8 V** H2..H5 max \|dev\| (dB) | 0.296 dB (H5) | 0.281 dB (H5) | 0.315 dB (H3) | 0.352 dB (H3) | 0.265 dB (H3) | 0.348 dB (H5) | `< 0.1 dB` |
| **8 V** THD (ref 60.6926 %) | 61.3782 % | 61.3226 % | 61.3368 % | 61.2883 % | 61.2823 % | 61.4935 % | — |
| **Paper ops, common / same-cell** (ESTIMATE) | 210 (`93m+117a`) | 220 (`99m+121a`) | 236 (`109m+127a`), 0d | — | 246 (`115m+131a`), 0d | **251 (`118m+133a`), 0d** | `~300` (slot 411) |
| **Paper ops, worst / cross-cell** (ESTIMATE) | 210 (`93m+117a`) | 220 (`99m+121a`) | 382 (`177m+205a`)+2d | 324 (`153m+171a`)+4d | 392 (`183m+209a`)+2d | **397 (`186m+211a`)+2d** | `~300` (slot 411) |
| **Table memory** (words) | 8,192 | 8,192 | 16,384 | 24,576 | 16,384 | **16,384** | ≤ 32 K Y-priv |
| **Latency** (base samples / ms) | 3 (0.0625 ms) | 3 + 0.373 frac (0.0703 ms) | 3 + 0.250 frac (0.0677 ms) | 3 + 0.500 frac (0.0729 ms) | 3 + 0.623 frac (0.0755 ms) | **3 + 0.538 frac (0.0737 ms)** | (IIR resampler: 3) |

### Octatrack base rate (`44.1 kHz` / `2× IIR`, `fs = 88.2 kHz`)

`Resampler(44100, 2, "iir")` requires `8` allpass sections per way (`+12` arith ops/base sample vs `48 kHz`) and nominal latency `4` base samples (`0.0907 ms`). Measured in `results.json["variants_44k"]` and `results.json["aliasing_44k"]`:

| metric (`44.1 kHz`) | **baseline** | **A only** (`method_a`) | **A + B1** (no comp) | **A + B1 + comp** (default) | `CRIT` / target |
|---|---|---|---|---|---|
| **Aliasing 2 V worst** (tone) | — | -43.74 dBc (14037 Hz) | — | **-60.46 dBc** (15037 Hz) | — |
| **Aliasing 8 V worst** (tone) | — | -14.28 dBc (18037 Hz) | — | **-31.19 dBc** (14037 Hz) | — |
| **AC max dev** (20 Hz–20 kHz) | 4.777 dB (20 kHz) | **0.059 dB** (363 Hz, **PASS**) | 2.402 dB (20 kHz) | **0.069 dB** (17378 Hz, **PASS**) | `< 0.1 dB` |
| AC dev @ 10 kHz / 20 kHz | +1.794 / +4.777 dB | +0.024 / +0.018 dB | -0.538 / -2.402 dB | -0.037 / -0.044 dB | — |
| **Burst `min / tau / traj`** | 0.723 / 0.316 / 0.221 % | **0.450 / 0.494 / 0.117 % (PASS)** | 0.489 / 0.494 / 0.130 % | **0.489 / 0.494 / 0.130 % (PASS)** | `< 0.5 / 1.0 / 0.5 %` |
| **Paper ops, same / cross-cell** (ESTIMATE) | 222 (`97m+125a`) | **232 (`103m+129a`), 0d** | 258 / 404 + 2d | **263 / 409 + 2d** | (+12 vs 48 kHz) |
| **Latency** (base samples / ms) | 4 (0.0907 ms) | 4 + 0.333 frac (0.0983 ms) | 4 + 0.583 frac (0.1039 ms) | **4 + 0.499 frac (0.1020 ms)** | (IIR resampler: 4) |

### Recommendation, DSP56300 budget caveat, and remaining misses

- **Prototype default (`method_ab1c`):** `A + B1 + treble_comp` is kept as the `CutdownStage` default so the full aliasing (`-62.0 / -33.2 dBc` @ 48k, `-60.5 / -31.2 dBc` @ 44.1k), AC (`0.061 dB` @ 48k, `0.069 dB` @ 44.1k), burst, and 24-bit conditioning (`128.7 dB` SNR at 1 mV) improvements are active out of the box.
- **DSP56300 port caveat (#24) & fallback (`method_a`):** Applying #24's measured `1.4–1.6×` stall factor:
  - `method_ab1c` same-cell (`251` ops @ 48k, `0` divs) estimates `~351–402` cycles, **but** a hard real-time slot must cover the **worst-case cross-cell path** (`397` ops @ 48k / `409` @ 44.1k + `2` shared `1/du` `DIV`/`NORM` shifts -> `~556–635` cycles + `DIV` cycles, **exceeding** the `411`-cycle slot at 48 kHz; stereo doubles it). It also uses `16,384` table words (50% of 32 K Y-memory) for a single bias setting (`swap_bias` cannot rebuild on-DSP for #73-style bias smoothing).
  - **Method A alone (`method_a`: `220` ops @ 48k / `232` @ 44.1k, `0` divs, `8,192` words = 25% of Y)** sits at `~308–352` cycles with `1.4–1.6×` stalls, passes AC (`0.059 dB`) and burst (`0.346%` @ 48k, `0.450%` @ 44.1k) at both rates, and is the safer guaranteed-in-budget default for the #24 port unless #24 confirms worst-case `ab1c` fits.
- **Remaining misses vs full DK `CRIT` (not hidden):**
  1. **Harmonics (`< 0.1 dB` above `-90 dBc`):** None of the 3-state LUT variants pass the `0.1 dB` full-DK harmonic gate (`+1.21 dB` on 0.05 V H2 and `+1.14 dB` on 0.5 V H4 from Catmull-Rom's $C^1$ curvature discontinuity; `0.14–0.35 dB` at 2–8 V from dropping the pF caps). Why `treble_comp` increases `2–8 V` harmonic error slightly (`+0.012 / +0.032 / +0.060 / +0.098 dB` on `H2..H5` at 2 V): at `1 kHz` fundamental, ADAA1's `cos(pi k f0/96k)/cos(pi f0/96k)` droops `H2..H5` (`2–5 kHz`) by `-0.014 / -0.037 / -0.071 / -0.112 dB`, which previously masked part of the 3-state circuit's excess. Removing that droop exposes the underlying 3-state error, which matches the **3-state 2× IIR Newton ablation** (`results.json`: `+0.212 / +0.213 / +0.396 / +0.292 dB` on `H2..H5` at 2 V; `+0.052 / +0.267 / +0.112 / +0.297 dB` at 8 V), proving the `2–8 V` harmonic mismatch is due to dropping the pF caps rather than the LUT or comp.
  2. **Aliasing vs `eco/iir` at 2 V:** `A+B1+comp` (`-62.00 dBc` @ 48k) is `13.7 dB` behind 4× `eco/iir` (`-75.74 dBc`) at 2 V, though `0.55 dB` better at 8 V (`-33.20` vs `-32.65 dBc`).

---

## Full 19-tone aliasing sweep (`dk/aliasing.py`, 2 V and 8 V)

Measured with `dk/aliasing.py` (`SWEEP_HZ = 1037…19037 Hz`, `0.25 s` settle, `1.0 s` periodic-Hann window, `±3` bin masks). Full DK columns (`eco/iir`, `standard/iir`, `64× IIR`) come from committed `prototypes/fullstage/dk/results.json`, `cutdown/results.json`, and `dk/aliasing_64x.json`.

**2 V peak** (bold = each column's worst tone):

| tone (Hz) | **baseline** | **A only** | **B1** | **B2** | **A + B1** | **A + B1 + comp** | `eco IIR` (4×) | `standard IIR` (8×) | `64× IIR` |
|---|---|---|---|---|---|---|---|---|---|
| 1037 | -98.5 | -98.6 | -117.2 | -132.4 | -117.5 | -116.3 | -173.8 | -173.8 | -173.7 |
| 2037 | -80.9 | -81.0 | -97.2 | -111.9 | -97.3 | -95.9 | -155.6 | -156.2 | -154.8 |
| 3037 | -75.2 | -75.3 | -92.1 | -108.0 | -92.3 | -91.1 | -141.5 | -140.5 | -140.3 |
| 4037 | -73.9 | -73.4 | -92.0 | -109.2 | -91.8 | -91.1 | -136.7 | -136.2 | -137.7 |
| 5037 | -74.8 | -72.9 | -96.6 | -102.7 | -90.0 | -89.4 | -122.6 | -133.2 | -131.9 |
| 6037 | -64.6 | -69.4 | -88.0 | -110.9 | -89.9 | -89.2 | -104.7 | -146.7 | -143.3 |
| 7037 | -60.8 | -63.9 | -74.3 | -96.7 | -79.6 | -78.2 | -98.3 | -126.5 | -129.0 |
| 8037 | -54.1 | -59.1 | -70.5 | -84.6 | -80.1 | -79.2 | -90.0 | -122.7 | -122.4 |
| 9037 | -52.1 | -58.5 | -77.4 | -82.1 | -86.5 | -86.6 | -90.7 | -120.8 | -122.0 |
| 10037 | -50.0 | -59.7 | -71.5 | -90.2 | -74.0 | -73.6 | -94.0 | -116.1 | -117.8 |
| 11037 | -50.2 | -55.6 | -62.9 | -76.8 | -68.6 | -67.8 | -83.5 | -126.3 | -131.2 |
| 12037 | -51.7 | -56.1 | -60.8 | -69.5 | -73.7 | -73.7 | -81.7 | -120.5 | -118.5 |
| 13037 | -40.2 | -48.4 | -55.7 | -65.1 | -74.1 | -73.4 | -77.0 | **-114.0** | -116.4 |
| 14037 | -40.1 | -49.4 | -58.6 | -64.1 | -70.4 | -70.9 | -78.3 | -115.6 | -115.1 |
| 15037 | -40.3 | -50.6 | -62.8 | -67.4 | -64.8 | -65.7 | -76.1 | -130.0 | -128.0 |
| 16037 | -38.3 | **-46.4** | -57.7 | -66.1 | **-61.4** | **-62.0** | **-75.7** | -116.7 | **-115.0** |
| 17037 | -38.4 | -48.8 | -56.4 | -67.2 | -63.4 | -64.5 | -77.7 | -116.9 | -119.3 |
| 18037 | -38.3 | -51.2 | -52.9 | -60.5 | -65.8 | -67.1 | -79.2 | -121.2 | -119.6 |
| 19037 | **-32.9** | -53.5 | **-48.5** | **-55.2** | -67.3 | -68.3 | -85.3 | -122.9 | -126.8 |

**8 V peak** (bold = each column's worst tone):

| tone (Hz) | **baseline** | **A only** | **B1** | **B2** | **A + B1** | **A + B1 + comp** | `eco IIR` (4×) | `standard IIR` (8×) | `64× IIR` |
|---|---|---|---|---|---|---|---|---|---|
| 1037 | -83.5 | -83.6 | -99.3 | -113.7 | -99.4 | -97.9 | -120.8 | -131.5 | -131.6 |
| 2037 | -59.4 | -59.8 | -76.5 | -92.7 | -76.9 | -75.7 | -79.1 | -103.9 | -116.3 |
| 3037 | -51.3 | -51.8 | -70.0 | -90.0 | -71.0 | -70.2 | -66.7 | -85.2 | -113.8 |
| 4037 | -41.3 | -41.8 | -58.9 | -77.0 | -59.0 | -58.0 | -59.8 | -77.0 | -111.2 |
| 5037 | -40.1 | -39.7 | -58.7 | -74.9 | -58.4 | -57.6 | -56.8 | -71.9 | -112.3 |
| 6037 | -42.6 | -41.8 | -63.7 | -72.2 | -60.3 | -59.3 | -53.5 | -68.7 | -121.4 |
| 7037 | -31.3 | -35.2 | -46.6 | -70.0 | -50.7 | -49.2 | -51.7 | -65.2 | -107.3 |
| 8037 | -30.0 | -33.5 | -54.9 | -61.9 | -57.1 | -56.2 | -46.2 | -63.3 | -101.2 |
| 9037 | -27.5 | -33.3 | -44.2 | -69.8 | -50.8 | -50.4 | -47.6 | -62.3 | -101.8 |
| 10037 | -21.7 | -25.8 | -38.2 | -54.7 | -42.4 | -41.8 | -44.0 | -59.8 | -102.5 |
| 11037 | -20.5 | -22.8 | -40.7 | -47.8 | -41.0 | -40.0 | -44.0 | -58.1 | -113.3 |
| 12037 | -20.5 | -23.4 | -47.2 | -49.0 | -50.3 | -50.6 | -37.5 | -58.1 | -105.7 |
| 13037 | -19.7 | -23.9 | -34.4 | -62.8 | -42.7 | -42.7 | -41.6 | -55.3 | -100.9 |
| 14037 | -19.7 | -24.5 | -32.7 | -47.0 | -39.0 | -39.5 | -37.1 | -55.1 | -101.0 |
| 15037 | -22.5 | -26.1 | -33.3 | -40.9 | -37.4 | -38.2 | -36.9 | -54.9 | -105.9 |
| 16037 | -14.4 | -18.7 | **-29.4** | -35.2 | -33.6 | -33.8 | -35.7 | -53.3 | **-99.5** |
| 17037 | -14.7 | -20.0 | -37.4 | -36.4 | -38.2 | -39.4 | -40.4 | -52.0 | -99.5 |
| 18037 | -14.7 | -20.6 | -40.7 | -36.7 | -37.9 | -38.9 | -34.8 | -52.2 | -102.3 |
| 19037 | **-12.1** | **-14.5** | -34.1 | **-34.2** | **-33.2** | **-33.2** | **-32.6** | **-51.7** | -100.2 |

---

## Baseline details: ablations, Koren comparison, and op-count breakdown

### 1. Operating point, AC, and burst vs `reference_dz.json` (baseline + ablations)

| metric | full DK `standard/fir` (8×) | full DK `eco/iir` (4×) | 3-state 2× IIR Newton | **baseline `128×32 cubic0_lin1`** | ablation `64×64 bilinear` | full DK `CRIT` |
|---|---|---|---|---|---|---|
| OP worst rel err (`va, vk, ia, vgk`) | 2.15e-9 | 2.15e-9 | 2.15e-9 | **2.15e-9** | 2.15e-9 | `< 1e-3` (pass) |
| AC max dev, chain (20 Hz–20 kHz) | 0.0520 dB | 0.0537 dB | 4.7786 dB | **4.7775 dB** | 4.6128 dB | `< 0.1 dB` (FAIL) |
| Burst `min_rel` / `tau_rel` / `traj_rel` | 0.031 / 0.041 / 0.003 % | 0.008 / 0.082 / 0.011 % | 0.634 / 0.327 / 0.215 % | **0.634 / 0.327 / 0.215 %** | 0.792 / 0.163 / 0.287 % | `< 0.5 / 1.0 / 0.5 %` |

### 2. Baseline vs `reference_koren.json` (includes DZ vs Koren valve-model mismatch)

| metric | full DK `standard/fir` vs `koren` | **baseline vs `koren`** | **A+B1 vs `koren`** | **A+B1+comp vs `koren`** |
|---|---|---|---|---|
| OP worst rel err (`vgk`) | 4.30 % | 4.30 % | 4.30 % | 4.30 % |
| AC max dev, chain (20 Hz–20 kHz) | 0.8080 dB (@ 380 Hz) | 5.3141 dB (@ 20 kHz) | 1.4555 dB (@ 20 kHz) | 0.8687 dB (@ 347 Hz) |
| Sine THD (0.05 / 0.5 / 2 / 8 V; ref 0.269 / 2.725 / 13.733 / 57.749 %) | 0.169 / 1.776 / 16.700 / 60.691 % | 0.195 / 1.825 / 17.113 / 61.378 % | 0.194 / 1.819 / 17.005 / 61.282 % | 0.195 / 1.821 / 17.041 / 61.494 % |
| Burst `min_rel` / `tau_rel` / `traj_rel` | 6.88 / 4.21 / 3.15 % | 7.59 / 4.56 / 3.38 % | 7.33 / 4.72 / 3.28 % | 7.33 / 4.72 / 3.28 % |

### 3. Paper op-count ESTIMATE breakdown per base-rate sample (48 kHz and 44.1 kHz mono; 2× internal)

> **ESTIMATE ONLY:** Hand count of arithmetic and memory operations in the runtime path of `CutdownStage` (`cutdown/stage.py`, `cutdown/table.py`, `dk/resample.py` at `os = 2, filt = "iir"`). **Not** a measured DSP56300 cycle count (issue #24 applies octabam's `1.4–1.6×` stall factor and measures hardware cycles).

| runtime block | rate | muls | adds / subs | divs | table / state mem notes |
|---|---|---|---|---|---|
| **2× IIR half-band up + down** (48 kHz: `N = 6`/way) | 1×↔2× | 13 | 25 | 0 | 12 coeffs, 12 allpass states + 1 odd delay (`44.1 kHz`: `N = 8` -> `17m + 33a`) |
| **3-state `p`, `y`, `x` updates** (`G, H0, D0, F0, A, B0, C`) | 2× | 30 | 26 | 0 | 15 sparse coeffs, 3 capacitor states |
| **Baseline LUT `128×32 cubic0_lin1`** | 2× | 50 | 66 | 0 | 8,192 table words (`dtbl_ig, dtbl_ia`) |
| **+ Method A (1-pole Miller LPF)** | 2× | +6 | +4 | 0 | `b0, b1, p` + `x_prev, y_prev` (0 extra table words) |
| **+ Method B1 same-cell** (`i == i_prev`, common case) | 2× | +16 | +10 | 0 | `ds1` adds 8,192 words (`16,384` total); 0 `ds1` reads, 0 divs |
| **+ Method B1 cross-cell** (`i != i_prev`, worst case) | 2× | +84 | +88 | +2 | `16,384` words; splits partial cells + shared `1/du` (+ `NORM` shift) |
| **+ Method B2 (ADAA2, over budget)** | 2× | +60 | +54 | +4 | `s1, s2` add 16,384 words (`24,576` total); optimistically cached |
| **+ Treble comp (1-pole/1-zero post shelf)** | 1× | +3 | +2 | 0 | `b0, b1, p` (`b0 > 1` needs scaling) + `zi` (0 extra table words) |
| **Total — Baseline** (`48k` / `44.1k`) | **1× base** | **93 / 97** | **117 / 125** | **0** | **210 / 222 arith ops**, **8,192 words** |
| **Total — Method A only** (`48k` / `44.1k`) | **1× base** | **99 / 103** | **121 / 129** | **0** | **220 / 232 arith ops**, **8,192 words** |
| **Total — Method B1 only** (`48k` same / cross) | **1× base** | **109 / 177** | **127 / 205** | **0 / 2** | **236 (0d) / 382 (+2d) ops**, **16,384 words** |
| **Total — Method B2 (over budget)** (`48k` / `44.1k`) | **1× base** | **153 / 157** | **171 / 179** | **4** | **324 / 336 arith ops + 4 divs**, **24,576 words** |
| **Total — Method A + B1 (no comp)** (`48k` same / cross) | **1× base** | **115 / 183** | **131 / 209** | **0 / 2** | **246 (0d) / 392 (+2d) ops**, **16,384 words** |
| **Total — Method A + B1 + comp (default, `48k` same / cross)** | **1× base** | **118 / 186** | **133 / 211** | **0 / 2** | **251 (0d) / 397 (+2d) ops** (`44.1k`: **263 / 409**), **16,384 words** |
