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

## v2 — settled sine runs (4 Nov 2026) — **test reference data**
`stage_12ax7_sine_long.cir`: 500 ms per drive level (data saved from 300 ms), then
`analyse_sine_long.py` resamples v(out) to 1 MHz and takes a single-bin DFT of H1–H5 over the last
100 cycles (400–500 ms). Settling check: the same measurement on 300–400 ms. Output in
`reference_sine.json`.

| peak in | H1 (V peak) | H2 dBc | H3 dBc | H4 dBc | H5 dBc | THD (H2–H10) |
|---|---|---|---|---|---|---|
| 0.05 V | 2.939 | −51.42 | −97.05 | (−149.5) | (−189.9) | 0.269 % |
| 0.5 V | 29.23 | −31.30 | −56.90 | −88.83 | (−110.3) | 2.725 % |
| 2 V | 101.96 | −17.71 | −27.89 | −39.03 | −41.71 | 13.73 % |
| 8 V | 109.03 | −5.67 | −24.76 | −15.38 | −16.28 | 57.74 % |

- **Settled:** between the two windows H1 changes by < 0.0001 dB and every harmonic above −100 dBc by
  < 0.02 dB. Values in parentheses (below −100 dBc) are at the simulator's numerical floor
  (reltol 1e-4) and drift between windows by up to 47 dB, so they are **not** used as test references.
- **Compared with v1 (60 ms):** THD 0.269/2.725/13.73/57.74 % vs 0.269/2.725/13.73/57.86 %. The
  60 ms numbers were already close; only the 8 V case moved (0.12 points). ngspice's own `.fourier`
  on the last cycle of the long runs gives 0.2686/2.7251/13.732/57.742 %, matching our DFT.

## v3 — `run_refs.py` references at tight solver tolerances (4 Nov 2026, #5) — **test reference data**
`reference_dz.json` (the DK test reference, same Dempwolf–Zölzer equations) and `reference_koren.json` are
now generated with

| setting | before | now |
|---|---|---|
| reltol | 1e-4 | **1e-6** |
| abstol | 1e-12 A | **1e-14 A** |
| vntol | 1e-7 V | **1e-9 V** |
| chgtol | 1e-14 C (default) | **1e-16 C** |
| tran step / max step | 2 µs / 2 µs | **0.5 µs / 0.5 µs** |
| method | trap (default) | trap |

ngspice-41 (conda-forge), `ngbehavior=psa`. The settings live in `SOLVER` in `run_refs.py` and are
recorded with the ngspice version and analysis parameters under `"generator"` in each JSON.
`NGSPICE=/path/to/ngspice python spice/run_refs.py` (default `../.ngenv/bin/ngspice`), ~1 min per model
on the lab box, ~350 MB of `out/`. Reruns are bit-identical.

**Why.** The DK model was already matching the old reference better than the old reference was converged:
H5 at 0.5 V (−97.7 dBc) differed from DK by 0.137 dB, and a tight ngspice run moved the reference, not the
model. 0.1 dB harmonic limits need a reference good to ~0.01 dB.

**What changed (dz, new − old):** H2–H4 at every level and H5 at 2 V / 8 V by ≤ 0.016 dB; H5 at 0.5 V
(−97.87 dBc) by +0.142 dB; the below-floor H5 at 0.05 V (−178.9 dBc) by 0.45 dB. Operating point 1e-9
relative, AC response 1e-7 dB, burst shift 8e-6, τ 0, trajectory 2e-6 of the shift.

**Convergence check** (dz; each row is a full rerun compared with the committed settings):

| variant | op point | AC | H2–H5 above −90 dBc | H5 at 0.5 V (−97.87 dBc) | burst shift / τ / traj |
|---|---|---|---|---|---|
| reltol 1e-7, abstol 1e-15, vntol 1e-10, chgtol 1e-17 | 0 | 0 | ≤ 2e-4 dB | 0.0055 dB | 3e-7 / 0 / 9e-8 |
| step 0.25 µs | 0 | 0 | ≤ 7e-4 dB | 0.0068 dB | 1e-7 / 0 / 2e-8 |
| both | 0 | 0 | ≤ 1e-4 dB | 0.0055 dB | 3e-7 / 0 / 7e-8 |

So everything the tests use (harmonics above −90 dBc) is converged to < 1e-3 dB. Below about −95 dBc the
reference is still only good to ~0.01 dB, and below about −130 dBc it is noise (H5 at 0.05 V moves by 0.2–0.4 dB).
τ is quantised to the 1 µs analysis grid (8e-5 relative).
