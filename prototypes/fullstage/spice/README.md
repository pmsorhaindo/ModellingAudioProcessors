# ngspice reference: one 12AX7 common-cathode stage

This is the offline "ground truth" the DK (and later WDF) models are tested against. It proves our
maths matches the *circuit*; it is only as true to a real valve as the Koren model is.

**Component values are example values, not Culture Vulture values.** HT 300 V, Ra 100k, Rk 1.5k and
Rg 1M are from Merlin Blencowe, *Designing Valve Preamps for Guitar and Bass*, ch. 1
(https://www.valvewizard.co.uk/Common_Gain_Stage.pdf). Cin 22n, Ck 22u, Cout 22n, Rl 1M, Rs 1k and the
68k grid stopper are round values chosen by us. See the netlist header.

## Setup
```bash
# ngspice 41 from conda-forge, in a local micromamba env (what we used)
micromamba create -y -p ../.ngenv -c conda-forge ngspice=41
models/fetch_models.sh          # Koren 12AX7 model, hash-checked
mkdir -p out
../.ngenv/bin/ngspice -b stage_12ax7.cir       > out/log.txt        # op, AC, sine transients + .fourier
../.ngenv/bin/ngspice -b stage_12ax7_burst.cir > out/log_burst.txt  # 4 V, 1 kHz burst, 10-30 ms
```
`.spiceinit` sets `ngbehavior=psa` (PSpice compatibility, needed for the Koren B-source syntax).
`out/` is git-ignored (the transient dumps are several MB each).

Long, settled sine runs (v2, the test reference; needs the lab Python env):
```bash
../.ngenv/bin/ngspice -b stage_12ax7_sine_long.cir > out/log_long.txt   # ~10 s
python analyse_sine_long.py                                             # -> reference_sine.json
```

Test references (`reference_dz.json`, `reference_koren.json`; needs the lab Python env and the Koren model):
```bash
NGSPICE=../.ngenv/bin/ngspice python run_refs.py      # default NGSPICE is ../.ngenv/bin/ngspice; ~1 min/model
```
Solver settings are the `SOLVER` constant in `run_refs.py` (reltol 1e-6, abstol 1e-14, vntol 1e-9,
chgtol 1e-16, 0.5 µs step, trapezoidal; #5). Each JSON records them and the ngspice version under `"generator"`.
`stage_12ax7*.cir` above are the original hand-run netlists and still use the old, looser options.

Results: see `RESULTS.md`.
