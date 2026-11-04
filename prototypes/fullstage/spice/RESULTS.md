# Reference results (ngspice 41, Koren 12AX7)

## v1 — first run (3 Nov 2026)
- **Operating point:** Va = 202.92 V, Vk = 1.456 V, Ia = 0.971 mA, Vgk = −1.455 V
  (consistent with Blencowe's load-line estimate for this stage).
- **Small signal:** 35.39 dB at 1 kHz (AC analysis, output across Rl).
- **THD vs drive** (1 kHz sine, `.fourier` on v(out), 10 harmonics), **60 ms runs**:

  | peak in | 0.05 V | 0.5 V | 2 V | 8 V |
  |---|---|---|---|---|
  | THD | 0.27 % | 2.72 % | 13.73 % | 57.86 % |

  Caveat: 60 ms is too short — `.fourier` analyses the last cycle while Cin/Ck (τ up to ~33 ms for Rk·Ck,
  much longer for the grid-current-charged Cin) are still settling. Superseded by v2 below.
- **Tone burst** (4 V peak, 1 kHz, 10–30 ms): one-cycle (1 ms) moving average of Vgk falls from
  −1.455 V at rest to −4.11 V at the end of the burst (t = 30 ms): grid current charges Cin and
  shifts the bias ("blocking"), then it recovers after the burst.
