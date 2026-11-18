# Cut-down 12AX7 stage — validation, aliasing reduction methods, and op-count estimate (#23)

Setup: 48 kHz base rate (`fs = 96 kHz` internal at `2×`), Drive = Bias = Output = 1, Dempwolf–Zölzer 12AX7 parameters (`prototypes/fullstage/dk/triode.py: DZ_12AX7_RSD1`), circuit values from `prototypes/fullstage/dk/circuit.py: VALUES` with the three pF valve capacitances (`CCG`, `CGP`, `CCP`) dropped.

> **Reference note:** `ngspice` is not installed in this environment, so no live `ngspice` runs were executed. All ngspice comparisons below are measured against the committed tight-tolerance reference JSONs (`prototypes/fullstage/spice/reference_dz.json` and `prototypes/fullstage/spice/reference_koren.json`). All measured numbers below come directly from commands run in this session:
> - `cd prototypes/cutdown && /tmp/issue23b-venv/bin/python -m cutdown.validate` (writes `prototypes/cutdown/results.json`, `74.1 s` wall)
> - `cd prototypes/cutdown && /tmp/issue23b-venv/bin/python -m pytest -q` (`51 passed in 14.12s`)
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
- **2-D formulation on `(p0, p1) -> (Ig, Ia)`:** In `CutdownStage`, `H0 = [0.9988, 0.0]` so the input `vin` drives `p0` directly (`max |Δp0| = 9.33 V/sample` at 8 V / 19 kHz), whereas `p1` depends only on the slow `Ck`/`Cout` states (`max |Δp1| = 0.007 V/sample` at 19 kHz vs `Δp1 = 1.29 V` grid spacing). We integrate the `cubic0_lin1` interpolant analytically along `p0` at the segment midpoint $p_{1m} = \frac{1}{2}(p_1[n] + p_1[n-1])$:
  $$\bar{i}_{\text{ADAA1}}[n] = \frac{F_1(u[n], p_{1m}) - F_1(u[n-1], p_{1m})}{u[n] - u[n-1]}, \qquad u = (p_0 - p_{0,\min}) / \Delta p_0$$
  where $F_1(u, p_1) = s_1[i, j] + \sum_{k=0}^3 W_k(f_u)\,T_{k,j} - u\,i_{\text{off}}$ uses the exact quartic antiderivatives $W_k(t) = \int_0^t w_k(\tau)\,d\tau$ of Catmull-Rom (`s1` is `(128, 32, 2)` cumulative cell integrals = 8,192 extra words, so `16,384` words total = 50% of the DSP56300 32 K Y-private memory). Because `F1` is affine in `fw` on `[j, j+1]`, caching the previous `j`-row slices evaluates `F1(u[n-1], p1m)` in 2 linear interpolations whenever `j == j_prev` (avoiding a full second lookup). Checked against 16-point Gauss-Legendre quadrature of `table.lookup(p0(t), p1(t))` over 200 random 2-D segments (`|Δp1| ≤ 0.05 V`): max error `3.50e-8 A` (`Ig`), `1.61e-6 A` (`Ia`).
- **Fallback & DC-rest:** When $|u[n] - u[n-1]| < \text{ADAA\_TOL\_1} = 10^{-6}$ cells ($\approx 0.2\,\mu\text{V}$), evaluates `table.lookup(0.5*(p0[n] + p0[n-1]), p1m)`. Because both `F1` and `table.lookup` subtract the exact same `(dig_off, dia_off)`, silence produces `Δi = (0, 0)` (`< 1e-9 V` output) and overdrive transients decay smoothly back to `0 V` without offset jumps.
- **Why `adaa=2` (Method B2) is ruled out:** Second-order ADAA (`adaa=2`, Bilbao et al. 2017) requires `s2` (`24,576` total words = 75% of 32 K Y-private memory), `324` arithmetic ops + `4` divisions/base sample (optimistically cached; `~590` uncached) pushing past the `~300` target, `1/Δu^2` division which is ill-conditioned in 24-bit fixed point on the DSP56300, adds `-6.32 dB` linearised roll-off at 20 kHz (`(1 + z^-1 + z^-2)/3`, severely over-darkening `A+B2` to `~ -6 dB`), and buys only `0.09 dB` over `B1` at 8 V / 19037 Hz (`-34.16` vs `-34.07 dBc`). It is retained as an explicit ablation switch (`adaa=2`) and **not** recommended as the default.

---

## Comparison of methods (`cutdown.validate`, 48 kHz base / 2× IIR)

All metrics below are measured directly from `cutdown/results.json` against `reference_dz.json` and the full 38-point `dk/aliasing.py` sweep (full DK `CRIT`: AC `< 0.1 dB`, harmonics `< 0.1 dB` above `-90 dBc`, burst `< 0.5% / 1.0% / 0.5%`; op-count target `~300` mono ops/base sample).

| metric | **baseline** (`no A, adaa=0`) | **A only** (`miller_lpf`) | **B1** (`adaa=1`) | **B2** (`adaa=2`, ruled out) | **A + B1** (**recommended**) | full DK `CRIT` / target |
|---|---|---|---|---|---|---|
| **Aliasing 2 V worst** (tone) | -32.93 dBc (19037 Hz) | -46.42 dBc (16037 Hz) | -48.48 dBc (19037 Hz) | -55.16 dBc (19037 Hz) | **-61.42 dBc** (16037 Hz) | (full DK `eco/iir`: -75.7) |
| **Aliasing 8 V worst** (tone) | -12.11 dBc (19037 Hz) | -14.47 dBc (19037 Hz) | -29.36 dBc (16037 Hz) | -34.16 dBc (19037 Hz) | **-33.17 dBc** (19037 Hz) | (full DK `eco/iir`: -32.6) |
| **AC max dev** (20 Hz–20 kHz) | 4.778 dB (20 kHz) | **0.059 dB** (363 Hz, **PASS**) | 2.767 dB (20 kHz) | 1.142 dB (20 kHz) | 1.992 dB (20 kHz) | `< 0.1 dB` |
| AC dev @ 20 Hz | +0.043 dB | +0.043 dB | +0.045 dB | +0.046 dB | +0.045 dB | — |
| AC dev @ 1 kHz | +0.081 dB | +0.059 dB | +0.078 dB | +0.071 dB | +0.056 dB | — |
| AC dev @ 10 kHz | +1.794 dB | +0.028 dB | +1.321 dB | +0.508 dB | -0.444 dB | — |
| AC dev @ 20 kHz | +4.778 dB | +0.018 dB | +2.767 dB | -1.142 dB | -1.992 dB | — |
| **Burst `min_rel`** | 0.634 % (FAIL) | **0.346 % (PASS)** | 0.674 % (FAIL) | 0.680 % (FAIL) | **0.386 % (PASS)** | `< 0.5 %` |
| **Burst `tau_rel`** | 0.327 % (PASS) | 0.490 % (PASS) | 0.327 % (PASS) | 0.327 % (PASS) | 0.490 % (PASS) | `< 1.0 %` |
| **Burst `traj_rel`** | 0.215 % (PASS) | 0.114 % (PASS) | 0.229 % (PASS) | 0.228 % (PASS) | 0.127 % (PASS) | `< 0.5 %` |
| **0.05 V** H1 / H2 dev (dB) | +0.080 / +1.225 | +0.058 / +1.205 | +0.077 / +1.220 | +0.071 / +1.213 | +0.055 / +1.200 | H2 `< 0.1 dB` |
| **0.05 V** THD (ref 0.1693 %) | 0.1950 % | 0.1945 % | 0.1949 % | 0.1947 % | 0.1944 % | — |
| **0.5 V** H2 / H3 / H4 dev (dB) | +0.237 / +0.370 / +1.146 | +0.213 / +0.327 / +1.107 | +0.232 / +0.355 / +1.122 | +0.224 / +0.335 / +1.090 | +0.208 / +0.312 / +1.080 | `< 0.1 dB` |
| **0.5 V** THD (ref 1.7756 %) | 1.8249 % | 1.8199 % | 1.8239 % | 1.8221 % | 1.8189 % | — |
| **2 V** H2..H5 max \|dev\| (dB) | 0.370 dB (H4) | 0.275 dB (H5) | 0.328 dB (H4) | 0.271 dB (H4) | 0.232 dB (H5) | `< 0.1 dB` |
| **2 V** THD (ref 16.7002 %) | 17.1132 % | 17.0168 % | 17.1015 % | 17.0760 % | 17.0051 % | — |
| **8 V** H2..H5 max \|dev\| (dB) | 0.297 dB (H5) | 0.281 dB (H5) | 0.315 dB (H3) | 0.352 dB (H3) | 0.265 dB (H3) | `< 0.1 dB` |
| **8 V** THD (ref 60.6926 %) | 61.3782 % | 61.3226 % | 61.3368 % | 61.2883 % | 61.2823 % | — |
| **Added paper ops** (ESTIMATE) | 0 | +10 (6m + 4a) | +48 (28m + 20a) + 2 div | +114 (60m + 54a) + 4 div | **+58 (34m + 24a) + 2 div** | — |
| **Total paper ops** (ESTIMATE) | 210 (93m + 117a) | 220 (99m + 121a) | 258 (121m + 137a) + 2 div | 324 (153m + 171a) + 4 div | **268 (127m + 141a) + 2 div** | `~300` (slot 411) |
| **Table memory** (words) | 8,192 | 8,192 | 16,384 | 24,576 | **16,384** | ≤ 32 K Y-priv |
| **Latency** (base samples / ms) | 3 (0.0625 ms) | 3 + 0.373 frac (0.0703 ms) | 3 + 0.250 frac (0.0677 ms) | 3 + 0.500 frac (0.0729 ms) | **3 + 0.623 frac (0.0755 ms)** | (IIR resampler: 3) |

### Recommendation and remaining misses

- **Recommended method for aliasing reduction at 2×:** **Method A + B1 (`miller_lpf=True, adaa=1`)**.
  - **Why:** Method A alone only improves 8 V / 19037 Hz aliasing by `+2.4 dB` (`-14.47 dBc`), because an 8 V input attenuated by 4.8 dB still hard-clips the triode. Adding 1st-order ADAA (`B1`) drops 8 V worst-case aliasing by **`+21.1 dB` (`-33.17 dBc`, matching 4× `eco/iir`'s `-32.65 dBc` at half the oversampling rate)** and 2 V worst-case aliasing by **`+28.5 dB` (`-61.42 dBc`)**. As a side effect, Method A also fixes the burst `min_rel` miss (`0.634% -> 0.386%`, passing all three burst `CRIT`).
  - **Op-count & DSP56300 caveat (ESTIMATE):** `268` arithmetic ops/base sample (`127 muls + 141 adds`) + `2` divisions + grid clamps and `16,384` Y-memory words (50% of 32 K). On DSP56300 (issue #24), each 24-bit `DIV` takes ~24–30 instructions (~50–60 cycles for 2 divides), which together with clamps and stalls sits right around the `~300` target (well under the `~411` slot ceiling). If `DIV` + clamps push `A+B1` over the target on hardware, **Method A alone (`220` ops, `0` divides, `8,192` words)** is the low-cost fallback that passes AC (`0.059 dB`) and burst (`0.346%`) while cutting 2 V aliasing by `+13.5 dB`.
- **Remaining misses vs full DK `CRIT` (not hidden):**
  1. **`A+B1` AC at 20 kHz (`-1.992 dB`):** Method A restores the exact `-4.78 dB` Miller roll-off (`0.059 dB` max dev alone), and ADAA1's small-signal trapezoidal averaging $\frac{1+z^{-1}}{2}$ at 96 kHz adds `-2.01 dB` at 20 kHz. (Only `Method A alone` meets the `< 0.1 dB` AC `CRIT`.)
  2. **Harmonics (`< 0.1 dB` above `-90 dBc`):** None of the 3-state LUT variants pass the `0.1 dB` full-DK harmonic gate (`+1.20 dB` on 0.05 V H2 and `+1.08 dB` on 0.5 V H4 from Catmull-Rom's $C^1$ curvature discontinuity; `0.15–0.31 dB` at 2–8 V from dropping the pF caps).
  3. **Aliasing vs `eco/iir` at 2 V:** `A+B1` (`-61.42 dBc`) is still `14.3 dB` behind 4× `eco/iir` (`-75.74 dBc`) at 2 V, though slightly better at 8 V (`-33.17` vs `-32.65 dBc`).

---

## Full 19-tone aliasing sweep (`dk/aliasing.py`, 2 V and 8 V)

Measured with `dk/aliasing.py` (`SWEEP_HZ = 1037…19037 Hz`, `0.25 s` settle, `1.0 s` periodic-Hann window, `±3` bin masks). Full DK columns (`eco/iir`, `standard/iir`, `64× IIR`) come from committed `prototypes/fullstage/dk/results.json`, `cutdown/results.json`, and `dk/aliasing_64x.json`.

**2 V peak** (bold = each column's worst tone):

| tone (Hz) | **baseline** | **A only** | **B1** | **B2** | **A + B1** | `eco IIR` (4×) | `standard IIR` (8×) | `64× IIR` |
|---|---|---|---|---|---|---|---|---|
| 1037 | -98.5 | -98.6 | -117.2 | -132.4 | -117.5 | -173.8 | -173.8 | -173.7 |
| 2037 | -80.9 | -81.0 | -97.2 | -111.9 | -97.3 | -155.6 | -156.2 | -154.8 |
| 3037 | -75.2 | -75.3 | -92.1 | -108.0 | -92.3 | -141.5 | -140.5 | -140.3 |
| 4037 | -73.9 | -73.4 | -92.0 | -109.2 | -91.8 | -136.7 | -136.2 | -137.7 |
| 5037 | -74.8 | -72.9 | -96.6 | -102.7 | -90.0 | -122.6 | -133.2 | -131.9 |
| 6037 | -64.6 | -69.4 | -88.0 | -110.9 | -89.9 | -104.7 | -146.7 | -143.3 |
| 7037 | -60.8 | -63.9 | -74.3 | -96.7 | -79.7 | -98.3 | -126.5 | -129.0 |
| 8037 | -54.1 | -59.1 | -70.5 | -84.6 | -80.1 | -90.0 | -122.7 | -122.4 |
| 9037 | -52.1 | -58.5 | -77.4 | -82.1 | -86.5 | -90.7 | -120.8 | -122.0 |
| 10037 | -50.0 | -59.7 | -71.5 | -90.2 | -74.0 | -94.0 | -116.1 | -117.8 |
| 11037 | -50.2 | -55.6 | -62.9 | -76.8 | -68.6 | -83.5 | -126.3 | -131.2 |
| 12037 | -51.7 | -56.1 | -60.8 | -69.5 | -73.7 | -81.7 | -120.5 | -118.5 |
| 13037 | -40.2 | -48.4 | -55.7 | -65.1 | -74.1 | -77.0 | **-114.0** | -116.4 |
| 14037 | -40.1 | -49.4 | -58.6 | -64.1 | -70.4 | -78.3 | -115.6 | -115.1 |
| 15037 | -40.3 | -50.6 | -62.8 | -67.4 | -64.8 | -76.1 | -130.0 | -128.0 |
| 16037 | -38.3 | **-46.4** | -57.7 | -66.1 | **-61.4** | **-75.7** | -116.7 | **-115.0** |
| 17037 | -38.4 | -48.8 | -56.4 | -67.2 | -63.4 | -77.7 | -116.9 | -119.3 |
| 18037 | -38.3 | -51.1 | -52.9 | -60.5 | -65.8 | -79.2 | -121.2 | -119.6 |
| 19037 | **-32.9** | -53.5 | **-48.5** | **-55.2** | -67.3 | -85.3 | -122.9 | -126.8 |

**8 V peak** (bold = each column's worst tone):

| tone (Hz) | **baseline** | **A only** | **B1** | **B2** | **A + B1** | `eco IIR` (4×) | `standard IIR` (8×) | `64× IIR` |
|---|---|---|---|---|---|---|---|---|
| 1037 | -83.5 | -83.6 | -99.3 | -113.7 | -99.4 | -120.8 | -131.5 | -131.6 |
| 2037 | -59.4 | -59.8 | -76.5 | -92.7 | -76.9 | -79.1 | -103.9 | -116.3 |
| 3037 | -51.3 | -51.8 | -70.0 | -90.0 | -71.0 | -66.7 | -85.2 | -113.8 |
| 4037 | -41.3 | -41.8 | -58.9 | -77.1 | -59.0 | -59.8 | -77.0 | -111.2 |
| 5037 | -40.1 | -39.7 | -58.7 | -74.9 | -58.4 | -56.8 | -71.9 | -112.3 |
| 6037 | -42.6 | -41.8 | -63.7 | -72.2 | -60.3 | -53.5 | -68.7 | -121.4 |
| 7037 | -31.3 | -35.2 | -46.6 | -70.0 | -50.7 | -51.7 | -65.2 | -107.3 |
| 8037 | -30.0 | -33.5 | -54.9 | -61.9 | -57.1 | -46.2 | -63.3 | -101.2 |
| 9037 | -27.5 | -33.3 | -44.2 | -69.8 | -50.8 | -47.6 | -62.3 | -101.8 |
| 10037 | -21.7 | -25.8 | -38.2 | -54.7 | -42.4 | -44.0 | -59.8 | -102.5 |
| 11037 | -20.5 | -22.8 | -40.7 | -47.8 | -41.0 | -44.0 | -58.1 | -113.3 |
| 12037 | -20.5 | -23.4 | -47.2 | -49.0 | -50.3 | -37.5 | -58.1 | -105.7 |
| 13037 | -19.7 | -23.9 | -34.4 | -62.8 | -42.7 | -41.6 | -55.3 | -100.9 |
| 14037 | -19.7 | -24.5 | -32.7 | -47.0 | -39.0 | -37.1 | -55.1 | -101.0 |
| 15037 | -22.5 | -26.0 | -33.3 | -40.9 | -37.4 | -36.9 | -54.9 | -105.9 |
| 16037 | -14.4 | -18.7 | **-29.4** | -35.2 | -33.6 | -35.7 | -53.3 | **-99.5** |
| 17037 | -14.7 | -20.0 | -37.4 | -36.4 | -38.2 | -40.4 | -52.0 | -99.5 |
| 18037 | -14.7 | -20.6 | -40.7 | -36.7 | -37.9 | -34.8 | -52.2 | -102.3 |
| 19037 | **-12.1** | **-14.5** | -34.1 | **-34.2** | **-33.2** | **-32.6** | **-51.7** | -100.2 |

---

## Baseline details: ablations, Koren comparison, and op-count breakdown

### 1. Operating point, AC, and burst vs `reference_dz.json` (baseline + ablations)

| metric | full DK `standard/fir` (8×) | full DK `eco/iir` (4×) | 3-state 2× IIR Newton | **baseline `128×32 cubic0_lin1`** | ablation `64×64 bilinear` | full DK `CRIT` |
|---|---|---|---|---|---|---|
| OP worst rel err (`va, vk, ia, vgk`) | 2.15e-9 | 2.15e-9 | 2.15e-9 | **2.15e-9** | 2.15e-9 | `< 1e-3` (pass) |
| AC max dev, chain (20 Hz–20 kHz) | 0.0520 dB | 0.0537 dB | 4.7786 dB | **4.7775 dB** | 4.6128 dB | `< 0.1 dB` (FAIL) |
| Burst `min_rel` / `tau_rel` / `traj_rel` | 0.031 / 0.041 / 0.003 % | 0.008 / 0.082 / 0.011 % | 0.634 / 0.327 / 0.215 % | **0.634 / 0.327 / 0.215 %** | 0.792 / 0.163 / 0.287 % | `< 0.5 / 1.0 / 0.5 %` |

### 2. Baseline vs `reference_koren.json` (includes DZ vs Koren valve-model mismatch)

| metric | full DK `standard/fir` vs `koren` | **baseline vs `koren`** | **A+B1 vs `koren`** |
|---|---|---|---|
| OP worst rel err (`vgk`) | 4.30 % | 4.30 % | 4.30 % |
| AC max dev, chain (20 Hz–20 kHz) | 0.8080 dB (@ 380 Hz) | 5.3141 dB (@ 20 kHz) | 1.4555 dB (@ 20 kHz) |
| Sine THD (0.05 / 0.5 / 2 / 8 V; ref 0.269 / 2.725 / 13.733 / 57.749 %) | 0.169 / 1.776 / 16.700 / 60.691 % | 0.195 / 1.825 / 17.113 / 61.378 % | 0.194 / 1.819 / 17.005 / 61.282 % |
| Burst `min_rel` / `tau_rel` / `traj_rel` | 6.88 / 4.21 / 3.15 % | 7.59 / 4.56 / 3.38 % | 7.33 / 4.72 / 3.28 % |

### 3. Paper op-count ESTIMATE breakdown per base-rate sample (48 kHz mono; 2× internal)

> **ESTIMATE ONLY:** Paper count of arithmetic and memory operations in the runtime path of `CutdownStage` (`cutdown/stage.py`, `cutdown/table.py`, `dk/resample.py` at `os = 2, filt = "iir"`). **Not** a measured DSP56300 cycle count or schedule (measured 56300 counts belong to issue #24).

| runtime block | rate | muls | adds / subs | divs | table / state mem notes |
|---|---|---|---|---|---|
| **2× IIR half-band up + down** (`N = 6` allpasses each) | 1×↔2× | 13 | 25 | 0 | 12 coeffs, 12 allpass states + 1 odd delay |
| **3-state `p`, `y`, `x` updates** (`G, H0, D0, F0, A, B0, C`) | 2× | 30 | 26 | 0 | 15 sparse coeffs, 3 capacitor states |
| **Baseline LUT `128×32 cubic0_lin1`** | 2× | 50 | 66 | 0 | 8,192 table words (`tbl_ig, tbl_ia`) |
| **+ Method A (1-pole Miller LPF)** | 2× | +6 | +4 | 0 | `b0, b1, p` + `lp_x, lp_y` (0 extra table words) |
| **+ Method B1 (ADAA1 w/ slice cache)** | 2× | +28 | +20 | +2 | `s1` adds 8,192 words (`16,384` total); 4 cached slice floats |
| **+ Method B2 (ADAA2, ruled out)** | 2× | +60 | +54 | +4 | `s1, s2` add 16,384 words (`24,576` total); optimistically cached |
| **Total — Baseline** | **1× base** | **93** | **117** | **0** | **210 arith ops** (+ clamps ~230–280), **8,192 words** |
| **Total — Method A only** | **1× base** | **99** | **121** | **0** | **220 arith ops** (+ clamps ~240–290), **8,192 words** |
| **Total — Method B1 only** | **1× base** | **121** | **137** | **2** | **258 arith ops + 2 divs** (+ clamps), **16,384 words** |
| **Total — Method B2 (ruled out)** | **1× base** | **153** | **171** | **4** | **324 arith ops + 4 divs** (+ clamps), **24,576 words** |
| **Total — Method A + B1 (recommended)** | **1× base** | **127** | **141** | **2** | **268 arith ops + 2 divs** (+ clamps), **16,384 words** |
