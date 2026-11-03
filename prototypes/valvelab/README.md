# Valve stage lab (Ohm)

A small Python/NumPy lab for a saturator *inspired by* the Thermionic Culture Vulture.
It covers static valve models, a single common-cathode stage solved properly, harmonic analysis
against drive and bias, and an antialiasing comparison (naive, oversampling, ADAA) with a scalar C cost benchmark.

**Not** a model of the real unit. No schematic is public. Stage component values are illustrative
textbook values, and the 6AS6 and 5963 are represented by labelled stand-ins (see below).

## Run

```bash
cd prototypes/valvelab
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # .venv already exists on the box
.venv/bin/python scripts/run_all.py      # ~25 s: experiments -> results/, C bench, index.html
.venv/bin/python -m pytest -q tests      # 23 checks
```

The output is `index.html`, a single self-contained page: inline base64 plots, no external assets, a JS demo,
and `<meta name="robots" content="noindex">`. Raw numbers are in `results/results.json`.

## Layout

| path | what |
|---|---|
| `valvelab/params.py` | every parameter set, verbatim, with its source, plus the stand-in notes |
| `valvelab/models.py` | Koren triode (eq. 4) + Koren grid-current network, Koren pentode (eqs. 5, 3), Dempwolf-Zoelzer triode (DAFx-11 eqs. 10-12) |
| `valvelab/stage.py` | common-cathode stage: DC point (cathode bias), bias-by-plate-voltage, memoryless dynamic transfer with grid current via Rs; vectorised safeguarded Newton-Raphson |
| `valvelab/analysis.py` | exact harmonic analysis of a memoryless map (H1..H20, THD, even/odd balance) |
| `valvelab/antialias.py` | tabulated nonlinearity with exact antiderivative, naive / oversampled / ADAA1, coherent alias metric |
| `valvelab/report.py` | builds `index.html` |
| `scripts/run_all.py` | runs everything |
| `bench/adaa_bench.c` | scalar C cost of naive / ADAA1 / 2-4-8x polyphase OS / ADAA1+2x (cross-checked against Python) |
| `tests/` | model monotonicity, DC KCL sanity, solver vs small-signal gain, harmonic known answer, ADAA vs naive at low frequency, aliasing reduction, C-vs-Python |
| `sources/` | SPICE libraries the parameters came from: not redistributed here, see `sources/README.md` for URLs, SHA256 and fetch commands |

## Parameter sources

- 12AX7 and 12AU7, Koren triode: N. Koren, *Improved vacuum tube models for SPICE simulations* (Table 1 and Tube.lib), https://www.normankoren.com/Audio/Tubemodspice_article.html
- 6AN8P, Koren pentode: Koren's Tube.lib in https://www.normankoren.com/Audio/Tubemods.zip
- EF86, Koren pentode format: LTspice wiki `nk_pentode.sub` ("Philips data sheet", 2008), https://ltwiki.org/files/LTspiceIV/lib/sub/nk_pentode.sub. Its KG2 = 4500 is a library-wide default, so the screen is held at a fixed voltage.
- 12AX7, Dempwolf-Zoelzer: DAFx-11 paper, Table 1, column RSD-1, https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf

**Stand-ins:** no published fits were found for the **6AS6** (stand-in: EF86, and its suppressor-grid control is not modelled)
or the **5963** (stand-in: 12AU7; the RCA data sheet gives mu = 21, as for the 12AU7 family).

## Limits

- The model is memoryless: no blocking distortion from coupling-cap charging, no Miller capacitance, no supply sag. Next step: a DK or WDF stateful stage.
- The bias knob here is "quiescent plate voltage as a fraction of B+". The real unit's bias circuit is unknown.
- CPU numbers are from one scalar core on the shared box. Treat them as relative.

> In this repo the PNG figures are not committed; `scripts/run_all.py` regenerates `results/figs/`.
