# Full common-cathode stage — numerical pitfalls and their known solutions

Compiled 3 Nov 2026 (Europe/Amsterdam) for the DK-then-WDF single-stage build.
Every link below was fetched on that date (HTTP 200 or the page content was read). Where a source is
only known via another paper's reference list it is marked *(cited-by, no link verified)*.

**Correction to our plan text:** the Holters & Zölzer paper *"A generalized method for the derivation of
non-linear state-space models from circuit schematics"* is **EUSIPCO 2015**, not DAFx 2011.
Their DAFx 2011 paper is the wah-wah / nodal-DK-with-variable-parts paper (listed under §g).

## Core method references (read first)

| Source | Why | Link |
|---|---|---|
| D. T. Yeh, PhD thesis, Stanford 2009, *Digital Implementation of Musical Distortion Circuits by Analysis and Simulation* | Origin of the nodal DK method in audio; K-method tables; triode stage examples | https://ccrma.stanford.edu/~dtyeh/papers/DavidYehThesissinglesided.pdf |
| Yeh, Abel, Smith, *Automated Physical Modeling of Nonlinear Audio Circuits for Real-Time Audio Effects — Part I*, IEEE TASLP 18(4) 2010 | Formal DK derivation | https://ccrma.stanford.edu/~dtyeh/papers/yeh10_taslp.pdf |
| Yeh, *… Part II: BJT and Vacuum Tube Examples*, IEEE TASLP 2012 | Triode stage worked in DK; tables vs. iteration | https://ccrma.stanford.edu/~dtyeh/papers/yeh12_taslp.pdf |
| Holters & Zölzer, *A generalized method for the derivation of non-linear state-space models from circuit schematics*, EUSIPCO 2015 | Removes nodal-DK limitations; the method ACME.jl uses | https://www.hsu-hh.de/ant/wp-content/uploads/sites/699/2017/10/Holters-Z%C3%B6lzer-2015-A-Generalized-Method-for-the-Derivation-of-Non-linear-State-space-Models-from-Circuit-Schematics.pdf (also https://www.eurasip.org/Proceedings/Eusipco/Eusipco2015/papers/1570103545.pdf) |
| ACME.jl (HSU, open source, Julia) | Working reference implementation of the above — use as a second oracle next to ngspice | https://github.com/HSU-ANT/ACME.jl |
| J. Mačák, PhD thesis, Brno 2012, *Real-time Digital Simulation of Guitar Amplifiers as Audio Effects* | Triode stages, DK vs WDF vs approximations, real-time tradeoffs | https://ccrma.stanford.edu/~jingjiez/portfolio/gtr-amp-sim/pdfs/Real-time%20Digital%20Simulation%20of%20Guitar%20Amplifiers%20as%20Audio%20Effects.pdf (official VUT handle hdl.handle.net/11012/21132 returned HTTP 500 today) |
| Mačák, DAFx-16 tutorial *Virtual analog modeling – new ways how to achieve old sounds* | Slide-level DK recipe, triode as 2-port nonlinearity with 2×2 Jacobian | https://dafx16.vut.cz/files/dafx16_tutorial_macak.pdf |

---

## a. Newton–Raphson robustness in DK models

**The problem.** Each sample solves `p + K·i(v) − v = 0` (v = nonlinear port voltages, here Vgk and Vak,
i = grid and plate currents). Exponential/power-law currents make plain NR diverge on large steps
(grid swinging positive, cutoff→conduction transitions).

**Techniques**
1. **Initial guess:** previous sample's solution (Yeh thesis; Mačák tutorial). Better: linear
   extrapolation from the last two samples, or a cached solution — Holters & Zölzer's *k-d tree based
   solution cache for the non-linear equation of circuit simulations* (EUSIPCO 2016) *(cited-by Holters
   2019/2020, no link verified)*.
2. **Step limiting / damping:** SPICE's junction-voltage limiting (`pnjlim`, Nagel 1975) clamps the
   update of an exponential device's control voltage logarithmically above a critical voltage.
   Nagel, *SPICE2: A Computer Program to Simulate Semiconductor Circuits*, ERL-M520:
   https://www2.eecs.berkeley.edu/Pubs/TechRpts/1975/9602.html . For us: limit ΔVgk per iteration
   (e.g. a few × the grid-diode thermal-ish scale) and damp Δv by a backtracking line search on ‖F‖.
3. **Iteration cap + convergence test:** stop on `‖Δv‖∞ < tol_v` **and** `‖F‖∞ < tol_i`; hard cap
   (e.g. 8–16) with a counter. Log histogram of iterations per sample and the max; that is the CPU budget.
4. **Precomputed solutions (K-method / tables):** the delay-free loop can be removed and the
   nonlinear map `p ↦ i` tabulated (Borin, De Poli, Rocchesso, *Elimination of delay-free loops in
   discrete-time models of nonlinear acoustic systems*, IEEE TSAP 2000:
   https://www.di.univr.it/documenti/ArticoloRivista/allegato/allegato663783.pdf). Yeh uses this for
   real-time (thesis + Part II). Tradeoffs: zero iteration jitter, but a **2-D table** for a triode
   (two port voltages), memory and interpolation error, and a **rebuild whenever K changes**
   (sample rate, any knob that touches a component in the loop). The KVR thread "I started modelling
   circuits" (p.2) discusses precomputing with Newton homotopy + multidimensional interpolation:
   https://www.kvraudio.com/forum/viewtopic.php?start=15&t=457907 ; see also "Realtime Spice?":
   https://www.kvraudio.com/forum/viewtopic.php?t=494602 .
   **Plan:** NR with limiting in the prototype (it is the ground truth), tables as an optimisation
   measured against it.

## b. DC operating point initialisation (no startup thump)

- Solve the DC circuit first (capacitors open, inductors short) with the same NR solver (plus source
  stepping/gmin if needed — the standard SPICE homotopies, Nagel 1975), then set each capacitor state
  to its DC voltage/charge so the first sample is already at equilibrium.
- In DK form: steady state satisfies `x = A x + B u + C i(v)`, solve `(I−A) x = B u + C i(v)` jointly
  with the nonlinear equation (Yeh thesis discusses DC/initial conditions; Holters & Zölzer 2015 use the
  same state-space form).
- Our ngspice `.op` (below) is the reference these numbers must match.
- Same routine is needed after sample-rate change (states are rate-independent if stored as capacitor
  voltages, rate-dependent if stored as trapezoidal "canonical" states — convert, don't re-solve blindly).
- Matching the DC solve is not enough in floating point (#70). With the absolute state, the discretised map's own
  rest point is (I−A)⁻¹(B u + C i), and (I−A)⁻¹ ~ fs·τ amplifies the ~1e-13 rounding of the built matrices. The
  stage started at the DC point and drifted to a rest point 1.6 µV away at hq. Running the state as the deviation
  from the DC point (x = x0 + Δx, i = i0 + Δi, p = p_dc + G Δx + H vin with p_dc = v0 + K i0) makes the DC point the
  exact rest point (dk/stage.py `Stage._rest`, cpp/dk_core.h).

## c. Denormals

- **x86 (SSE/AVX):** set FTZ and DAZ in MXCSR with `_MM_SET_FLUSH_ZERO_MODE(_MM_FLUSH_ZERO_ON)`
  (xmmintrin.h) and `_MM_SET_DENORMALS_ZERO_MODE(_MM_DENORMALS_ZERO_ON)` (pmmintrin.h). Intel:
  https://www.intel.com/content/www/us/en/docs/dpcpp-cpp-compiler/developer-guide-reference/2026-0/set-the-ftz-and-daz-flags.html
  (notes: thread-local mode, not IEEE-conformant, FTZ only affects SSE/AVX not x87).
- **AArch64 / Apple Silicon:** FPCR.FZ is bit 24 (flushes single/double denormal inputs and outputs
  when FPCR.AH=0). Arm ARM register page:
  https://support.arm.com/documentation/ddi0601/2026-09/AArch64-Registers/FPCR--Floating-point-Control-Register
  Set via `__builtin_arm_rsr64/wsr64("fpcr")` or inline `mrs/msr`.
- **In a plugin:** set on entry to `process()` and restore on exit (host owns the thread). JUCE does
  this with `ScopedNoDenormals` (RAII, x86 FTZ/DAZ + ARM FZ):
  https://docs.juce.com/master/classjuce_1_1ScopedNoDenormals.html . We implement our own tiny RAII
  guard in the core so it stays framework-free.
- Belt and braces for the capacitor states: they decay exponentially in silence; a ±1e-20 DC/noise
  offset or explicit `if (fabs(x) < 1e-15) x = 0` on the states is cheap insurance where FTZ is not
  guaranteed (e.g. in a test harness on another thread).

## d. Overflow-safe Koren + grid current

- Koren triode: `E1 = (Vak/Kp)·log(1+exp(Kp·(1/µ + Vgk/sqrt(Kvb+Vak²))))`, `Ip = (E1^Ex/Kg1)·(1+sgn E1)`.
  Source: Koren, *Improved vacuum tube models for SPICE simulations*:
  http://www.normankoren.com/Audio/Tubemodspice_article.html (page explicitly uses that
  log(1+exp(x))→x for x≫1, →0 for x≪1). With Kp=600 (12AX7) the exp argument reaches hundreds → overflow.
- **Stable softplus:** `softplus(x) = max(x,0) + log1p(exp(-|x|))`; derivative `= 1/(1+exp(-x))`
  (logistic), evaluated as `x>=0 ? 1/(1+e^{-x}) : e^{x}/(1+e^{x})`. Clamp E1 ≥ 0 before `pow`, and
  write the Jacobian of `E1^Ex` with a guard at E1→0 (Ex>1 so derivative → 0, fine).
- **Grid current options:**
  - Koren SPICE subckt (the one in our `sources/`): diode (IS=1n) + RGI=2k from grid to cathode.
    Note: the 1 nA reverse saturation current shows up in ngspice as +1 mV on the grid through 1 MΩ
    (see op point below) — a model artefact, not physics.
  - Dempwolf & Zölzer, *A physically-motivated triode model for circuit simulations*, DAFx-11:
    smooth, continuously differentiable `Ik = G·(log(1+exp(C(Va/µ+Vg)))/C)^γ`,
    `Ig = Gg·(log(1+exp(Cg·Vg))/Cg)^ξ + Ig0`, `Ia = Ik − Ig` (fitted to measured 12AX7s; same softplus
    trick applies). https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf — **recommended for DK**
    because it is smooth everywhere (helps NR) and was validated in a state-space common-cathode stage.
  - Cohen & Hélie, *Measures and parameter estimation of triodes, for the real-time simulation of a
    multi-stage guitar preamplifier*, AES 129th Conv. 2010 (paper 8219): Koren-based plate current +
    grid rectification + parasitic C, measured tables vs fitted Koren.
    https://hal.science/hal-00631747/document
  - Koren himself warns grid current charges the coupling cap and drives the stage toward cutoff
    over many cycles — which is exactly the blocking our burst test shows.

## e. Antialiasing for stateful nonlinear systems

- **Oversampling** (2–8×) around the whole solver with polyphase IIR/FIR resamplers is the baseline
  that always works; cost scales linearly (plus NR iterations don't drop much).
- **ADAA (memoryless):** Parker, Zavalishin, Le Bivic, DAFx-16:
  https://dafx.de/paper-archive/2016/dafxpapers/20-DAFx-16_paper_41-PN.pdf (code:
  https://github.com/julian-parker/DAFX-AntiAliasing).
- **ADAA for stateful systems — verified:** M. Holters, *Antiderivative Antialiasing for Stateful
  Systems*, DAFx-19 (Birmingham): https://www.dafx.de/paper-archive/2019/DAFx2019_paper_4.pdf ;
  extended journal version Appl. Sci. 10(1):20, 2020 (doi 10.3390/app10010020):
  https://www.mdpi.com/2076-3417/10/1/20 . Key facts from the paper:
  applies ADAA to the *solution of the delay-free loop*, compensates the extra half-sample delay by
  designing the system for fs/… (coefficients computed for a reduced rate), needs ~2× oversampling
  anyway, and **only works when the nonlinearity input is scalar or very low-dimensional**.
  The journal version explicitly says vector-input nonlinearities need lookup tables whose
  dimensionality roughly doubles. **Implication for us:** a triode with grid current is a 2-input
  nonlinearity (Vgk, Vak) → stateful ADAA means 3–4-D tables; probably not worth it. Plan: oversampling
  first; try stateful ADAA only on a reduced variant (e.g. no grid current, or a dimension-reduced p).
- Also listed in Holters' references (not verified separately): Muller & Hélie, *Trajectory
  anti-aliasing on guaranteed-passive simulation of nonlinear physical systems*, DAFx-17 *(cited-by)*.

## f. WDF for triode stages

- Classic WDF allows **one** nonlinearity at the tree root; the triode has 3 terminals (2 ports) plus
  grid current → historically handled by approximation:
  - Karjalainen & Pakarinen, ICASSP 2006 and Pakarinen & Karjalainen, *Enhanced wave digital triode
    model…*, IEEE TASLP 2010 — use a one-sample delay / iterative coupling between grid and plate ports
    (paywalled; see `references/tube-modelling-papers.md`).
  - D'Angelo, Pakarinen, Välimäki, *New Family of Wave-Digital Triode Models*, IEEE TASLP 21(2) 2013 —
    removes that artificial delay; free Aalto copy:
    https://aaltodoc.aalto.fi/bitstreams/c94fca6e-463e-4f8e-a25d-9dce4aa4e8c8/download ;
    companion page https://research.spa.aalto.fi/publications/papers/ieee-taslp-2012-tube/
- **Modern route (R-type adaptors, all nonlinearities grouped at the root):**
  - Werner, Smith, Abel, *Wave Digital Filter Adaptors for Arbitrary Topologies and Multiport Linear
    Elements*, DAFx-15: https://dafx.de/paper-archive/2015/DAFx-15_submission_53.pdf
  - Werner, Nangia, Smith, Abel, *Resolving Wave Digital Filters with Multiple/Multiport
    Nonlinearities*, DAFx-15 (MNA-derived scattering + K-method-style joint solve):
    https://www.dafx.de/paper-archive/2015/DAFx-15_submission_54.pdf
  - Olsen, Werner, Smith, *Resolving Grouped Nonlinearities in Wave Digital Filters Using Iterative
    Techniques*, DAFx-16 (NR on the grouped root): https://dafx.de/paper-archive/2016/dafxpapers/39-DAFx-16_paper_14-PN.pdf
  - K. J. Werner, PhD thesis, Stanford 2016: https://stacks.stanford.edu/file/druid:jy057cz8322/KurtJamesWernerDissertation-augmented.pdf
  - Dunkel, Rest, Werner, Olsen, Smith, *The Fender Bassman 5F6-A Family of Preamplifier Circuits —
    A Wave Digital Filter Case Study*, DAFx-16 *(cited-by Holters 2020, no link verified)* — closest
    prior art to our exact stage.
- **Comparison plan:** same netlist, same Dempwolf/Koren equations, same NR tolerances; DK vs
  (i) D'Angelo-style triode WDF and (ii) R-type root with grouped [grid, plate] nonlinearity. Compare
  against ngspice: error vs drive, iterations/sample, CPU, behaviour under knob modulation.

## g. Sample-rate change / parameter rebuilds off the audio thread

- Holters & Zölzer, *Physical modelling of a wah-wah effect pedal as a case study for application of the
  nodal DK method to circuits with variable parts*, DAFx-11: low-rank (Woodbury) updates instead of full
  re-inversion when a pot changes — directly relevant to Drive/Bias knobs.
  https://dafx.de/paper-archive/2011/Papers/21_e.pdf
- Real-time rules (no locks, no allocation, no unbounded work in the callback): Ross Bencina,
  *Real-time audio programming 101: time waits for nothing*:
  http://www.rossbencina.com/code/real-time-audio-programming-101-time-waits-for-nothing
- Swapping a rebuilt matrix set: Timur Doumler, *Using locks in real-time audio processing, safely*:
  https://timur.audio/using-locks-in-real-time-audio-processing-safely ; farbot `RealtimeObject`
  (non-realtime-mutatable, lock-free on the audio side): https://github.com/hogliux/farbot
- **Pattern for us:** `prepare(fs)` happens off the audio thread in plugin hosts anyway (AU/VST3 call
  it while not processing) → rebuild there synchronously. For live knob changes: background thread
  builds a new immutable `StageCoeffs` (matrices + optional tables), publishes via an atomic pointer
  swap / farbot RealtimeObject; audio thread picks it up at block start; old object is reclaimed on the
  non-RT side (never `delete` on the audio thread). Crossfade or state-mapping on swap to avoid clicks.

---

## Reference simulation (ngspice)

- ngspice **41** (conda-forge `ngspice-exe`, installed via micromamba into
  `fullstage/.ngenv/` because Debian apt mirrors returned HTTP 500 today).
  Run (originally): `cd spice && ../.ngenv/bin/ngspice -b stage_12ax7.cir` (`.spiceinit` sets `ngbehavior=psa`
  so Koren's PSpice `PWRS()` parses).
- Netlist `spice/stage_12ax7.cir` (+ `stage_12ax7_burst.cir`; since #4 all netlists are generated by
  `spice/run_refs.py` from `dk/circuit.VALUES`, see `spice/README.md`); valve subckt extracted verbatim into
  `spice/12ax7_koren_local.lib` from `sources/koren_Tube.lib` (local only, do not commit).
- **Example values (not Culture Vulture):** HT 300 V, Ra 100k, Rk 1.5k, Rg 1M from Blencowe ch.1
  (https://www.valvewizard.co.uk/Common_Gain_Stage.pdf, §1.8–1.10). Cin 22n, Ck 22µ, Cout 22n,
  Rl 1M, Rs 1k, grid stopper 68k are Ohm's chosen conventional values (no source claimed).

### Results (`spice/out/`)
| quantity | value |
|---|---|
| Va | 202.92 V |
| Vk | 1.456 V |
| Ia | 0.971 mA |
| Vgk | −1.455 V |
| V(grid) | +1.07 mV (Koren grid-diode IS=1 nA leakage through 1 MΩ — artefact) |
| Midband gain (1 kHz, AC) | 35.4 dB (≈59×); 29.9 dB @10 Hz, 33.8 dB @10 kHz, 24.5 dB @50 kHz |
| THD @1 kHz, input 0.05 / 0.5 / 2 / 8 V pk | 0.27 % / 2.7 % / 13.7 % / 57.9 % (ngspice `fourier`, 10 harmonics) |
| Tone burst 4 V pk, 1 kHz, 10–30 ms | grid DC shifted to −3.8 V at burst end (blocking), −1.15 V at 35 ms, −0.58 V at 50 ms, −0.03 V at 119 ms; cathode sagged 1.456 → 1.16 V and recovering |

Cross-check: Blencowe's load-line estimate for the same values is ≈1.05 mA at Vgk −1.5 V (§1.10);
Koren model gives 0.97 mA at −1.46 V — consistent.

**Caveat:** sine runs are 60 ms and `fourier` uses the last period; the Ck·Rk (33 ms) and Cin·Rg
(22 ms) time constants have not fully settled by then, especially at 8 V where grid blocking builds
up. Re-run with ≥300 ms before using these THD figures as test tolerances.
