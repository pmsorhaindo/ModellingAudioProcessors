# Octatrack slot budget vs the 12AX7 DK stage — 4 Nov 2026 (Ohm)

Status: estimate + box benchmark. Nothing committed. Bench sources: `bench/` (gen.py, bench.c; the bd.txt/bf.txt outputs are reproduced in `bench/README.md`).

## 1. modwerk (pmsorhaindo/modwerk, private, read-only via GitHub connector)
- Found after Michael's correction (my first search for "modwork" found nothing; the GitHub user search and code search were both empty for that spelling).
- modwerk = web app + SDK; its native side is vendored **octabam** (`sdk/octabam`, upstream github.com/sambanks/octabam, pinned in `sdk/UPSTREAM.json`). Effects are **DSP56300 assembly** (`engine.asm`) + Python manifest. There is no C/float toolchain for the DSP.
- modwerk itself carries no slot-cost catalog (`sdk/octabam/tools/remix/slot_costs.json` absent); the measured figures below are upstream octabam's `docs/contributing/COSTS.md` (25–27 Oct 2026) and README, the same codebase modwerk builds on.

### Hardware/firmware facts (octabam docs, measured on units)
- Audio DSP: **NXP/Freescale DSP56721**, two DSP56300 cores @ **200 MHz** (NXP datasheet DSP56720EC: 200 MIPS/core). 24-bit fixed-point fractional, 56-bit accumulators, single-cycle MAC, **no FPU, no hardware exp/log; DIV is one bit per instruction**.
- Sample rate **44.1 kHz**; frame = **16 samples** (362.8 µs) — octabam DSP.md / PLACEMENT.md.
- **4,535 cycles / sample / core**; each core serves 4 tracks; 4 FX1 + 4 FX2 slots per core.
- Per-instance state block 0x100 words (`x:0x20a + 0x100·k`); program memory: modules share a ~2,724-word donor region per core; Y private 32 K words/core; shared 64 K window. DSP cannot reach the ColdFire's DRAM directly.
- Measured base load 22.4 % (core 0) / 14.9 % (core 1); limit chosen 95 % of a frame.
- **Slot budget ("free everywhere", safe in any layout): ≤ 9.07 % of a frame on core 0 ≈ 411 cycles/sample** (10.0 % ≈ 453 on core 1). Existing effects: LOFI2 13.6 %, BBD 11.3 %, DISTORX 9.45 % (all over budget → remix fails `check`); CHARACTER 8.9 %, FILTER 5.1 %, stock FILTER 192 static cycles.
- Absolute ceiling if ONE instance were the only custom FX on its core: (95 − 22.4) % ≈ **3,290 cycles/sample**, but then every other slot on that core must be ~free — not a shippable module under octabam's bound.
- Measured/static stall factor: real cost ≈ **1.4–1.6 × `make cycles` static count**.

## 2. Our kernel: operation count per internal sample (mono)
From circuit.py / solver.py / stage.py: 6 states (3 coupling/bypass C + 3 inter-electrode pF), 2 NL ports, inputs (vin, HT const).
- Linear: p = G x + H u (12+2 MAC), y = D x + E u + F i (9), x' = A x + B u + C i (36+6+12) → **~80 MAC**.
- NR iteration: 2×2 Jacobian build (~12 mul), 2×2 solve (1 divide + ~8 mul), pnjlim (sometimes a log), line search ≥1 model eval. Measured iterations: 8× mean 2.0–3.5, 4× 2.6–3.7 (C bench below; Python max 6 at 8×, 8 at 4×).
- Dempwolf–Zölzer eval (`currents`): 2 softplus (exp+log1p each), 2 sigmoid (1 exp each), **4 pow** → 8 transcendental + ~25 flops. Per sample ≈ (1 + iters) evals → ~30 transcendentals at 3 iterations.
- FIR: 64·OS−1 taps; polyphase up = L MAC/base sample, decimate-by-OS = L MAC/base sample → 2L: 2× 254, 4× 510, **8× 1,022**, 16× 2,046 MAC/base sample. (The Python prototype filters at full rate before decimating — 8× wasteful; a C core should be polyphase.)

## 3. Octatrack estimate (DSP56300, fixed point, 1.5× stall factor)
Assumed per-eval cost with polynomial/table exp & log2 on DSP56300: ~150 cycles; NR iteration ~350 cycles (eval + div ~30 + Jacobian/solve ~50 + overhead); linear ~100.
Per internal sample ≈ 100 + 3×350 ≈ 1,150 static → **~1,700 measured**. Per base sample, mono:

| option | stage | FIR | ≈ cycles/base sample (×1.5) | vs 411 slot | vs 3,290 ceiling |
|---|---|---|---|---|---|
| 1× (no OS) | 1,150 | 0 | ~1,700 | **no** (4×) | fits alone |
| 2× | 2,300 | 254 | ~3,800 | **no** | no |
| A: 4× + prewarp | 4,600 | 510 | ~7,700 | **no** (19×) | no |
| C: 8× | 9,200 | 1,022 | ~15,000 | **no** (37×) | no (3.4× a whole core) |
| 16× | 18,400 | 2,046 | ~31,000 | **no** | no |
Octatrack tracks are stereo → ×2 if both channels are processed. Unknowns: real DSP56300 cycle count of a fixed-point D–Z model (never written), whether 24-bit fractional NR converges at HT = 300 V scaling (needs per-port scaling and 56-bit accumulators; float tolerances 1e-9 V are not reachable — our float C run already had to relax to 1e-4 V).
**Verdict: none of the options fits a slot as implemented — not even 1×. The NR + transcendental model is the blocker, not the oversampling.**

### What could fit (design change, accuracy to be re-proven vs ngspice)
- **Table-driven DK (no Newton)**: precompute v_nl(p0,p1) (+ i_nl) on a 2-D grid, bilinear lookup (~25 cycles). Linear 80 MAC → ~110 static/internal sample. Memory: 64×64×2 ports = 8 K words (fits Y-private 32 K, shared by all instances on a core; NOT the 256-word state block).
- **Reduced order at 44.1/88.2 kHz**: drop/merge the three pF capacitances (they only shape >15 kHz) → 3 states, ~35 MAC.
- **2× with a short IIR/allpass halfband pair** (~2×20 cycles) instead of FIR.
- Estimate: 2× · (35 + 25 + 20 overhead) + 40 ≈ 200 static → **~300 measured mono → marginal fit (≤411)**; stereo ~600 → **no** unless run mono or on a core with fewer dear effects. 1× table + reduced order ≈ 120–150 measured mono → yes, but aliasing is worse than anything we've measured. This is a separate "OT-lite" variant, not the 8× reference.

## 4. Laptop / plugin: measured C benchmark
Machine: this box, Intel Xeon (VM, 8 vCPU, clock not exposed), gcc -O2, straight port of solver.py (pnjlim + line search, libm exp/log1p/pow), polyphase FIR, **mono**, 1 kHz sine, 48 kHz base, 1 s × best of 5. Double: TOL 1e-9 V (as Python). Float: TOL 1e-4 V (1e-9 unreachable in float → hits the 50-iteration cap).

| | 0.5 V in | 4 V in | iters (4 V) | FIR share |
|---|---|---|---|---|
| double 2× | 1.42 µs (6.8 %) | 1.53 µs (7.3 %) | 4.07 | 0.13 µs |
| double 4× (≈ option A; prewarp has zero runtime cost) | 2.61 µs (12.5 %) | 2.69 µs (12.9 %) | 3.71 | 0.27 µs |
| **double 8× (option C)** | 4.77 µs (22.9 %) | 5.10 µs (24.5 %) | 3.52 | 0.53 µs |
| double 16× | 9.42 µs (45 %) | 9.94 µs (47.7 %) | 3.31 | 1.05 µs |
| float 8× | 3.35 µs (16.1 %) | 4.32 µs (20.7 %) | 2.64 | 0.53 µs |
(% = share of one core per mono channel at 48 kHz.) Cost scales ~linearly with OS; the NR + libm transcendentals are ~85–90 % of the time, FIR ~10 %. ≈ 550–640 ns per internal sample (double) ≈ ~1,500 cycles at an assumed ~2.5–3 GHz.

**Per-instance laptop estimate (stereo = 2× mono):** 8× ≈ 45–50 % of one box core; 4× ≈ 25 %. A recent Apple-silicon/modern x86 laptop core is plausibly 1.5–2× faster per thread than this VM (assumption, not measured) → ~25–30 % per stereo 8× instance at 48 kHz, ~2× that at 96 kHz session rates. As-is that is too heavy for many instances in an Ableton set.

## 5. Cost reducers that keep accuracy (ranked by expected payoff)
1. **Cut transcendentals per eval 8 → 4 (exact):** sigmoid from the softplus's exp(−|z|) (shared); sk^(γ−1) = sk^γ / sk (one pow per port). Expected ~1.6–1.8×.
2. **Fast exp/log2 approximations** with stated bound (e.g. minimax, rel. err ≤ 1e-7 float / 1e-12 double) and pow = exp2(γ·log2): ~3–5× on the model eval; verify against the ngspice gate.
3. **NR trimming:** better predictor (extrapolate from last two samples) → ~1–2 iterations; skip line search when the full step reduces ||F|| (already mostly) and use float residual tolerance scaled per port (1e-6 V on Vgk, 1e-4 V on Vak) — float double-checks vs tests. Fixed iteration count (e.g. 3) gives deterministic CPU; must prove the 12 tests still pass.
4. **Tabulated nonlinearity (DK K-method lookup / 2-D table of i(v) or of the solution v(p))**: removes NR entirely; biggest win (≈5–10×) and the only Octatrack route; accuracy depends on grid + interpolation (cubic Hermite), must be re-gated. Rebuild table when Bias (Rk) changes (K changes) — off the audio thread.
5. **Halfband cascade instead of one long FIR:** 8× = three 2× halfbands (half the taps zero, later stages short) → FIR ~3–4× cheaper; or polyphase IIR allpass halfbands (very cheap, not linear-phase — acceptable for an insert). Saves most of the ~10 % FIR share.
6. **SIMD stereo / dual-instance:** run L and R in two SIMD lanes of the same kernel (identical matrices) → stereo for ~1.1–1.3× mono.
7. **Reduced-order state** only where justified: keep the pF caps at 8× (they set the 20 kHz match), consider merging at 2×/OT rates.
8. Polyphase decimation in the prototype itself (Python only; C already does it).
Aliasing note: the cheaper per-internal-sample kernel is what makes 8× (or higher) affordable; ADAA remains impractical for the 2-port stateful case (see RESEARCH-numerics).

## Unknowns / not done
- No DSP56300 implementation or cycle count exists for any part of our kernel; Octatrack numbers are paper estimates × the octabam-measured 1.5 stall factor.
- Laptop clock unknown (VM); no ARM/Apple measurement; no -O3/-ffast-math/SIMD runs yet.
- Prewarp (option A) assumed runtime-free (it only changes the Gc multipliers at build time) — confirmed by reading warps.py.
