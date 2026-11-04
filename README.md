# ModellingAudioProcessors
Research and C/C++ AU/VST3 implementations of analog audio processor models, starting with a Thermionic Culture Vulture-inspired saturator.

## Structure
| path | what |
|---|---|
| `references/` | schematics, datasheets and modelling papers (links; free author copies where found) |
| `prototypes/valvelab/` | static valve-stage lab: Koren/Dempwolf models, bias vs harmonics, ADAA vs oversampling, C benchmark |
| `prototypes/fit6as6/` | our 6AS6 model fitted to the GE datasheet (dual-control g3 extension is our own empirical model), verified in ngspice |
| `prototypes/research/` | research notes: grid-current models, Culture Vulture panel controls, 6AS6/5725 datasheet survey |
| `prototypes/fullstage/` | one 12AX7 common-cathode stage with its reactive parts: ngspice references, nodal DK prototype, (next) WDF |

## Status (4 Nov 2026)
- DK prototype of one 12AX7 stage passes all first-pass criteria against the same-model ngspice reference at 8x oversampling (default since 4 Nov): operating point, small-signal response (0.05 dB worst, at 20 kHz), H2-H5 (worst 0.65 dB) and tone-burst bias recovery. At 4x the small-signal check missed by 0.21 dB at 20 kHz (trapezoidal warping); 8x costs ~2x CPU - see `prototypes/fullstage/dk/RESULTS.md`.
- Research notes (`prototypes/research/`): [grid current](prototypes/research/grid-current.md) (Dempwolf-Zolzer chosen for the DK stage; no 6AS6 positive-grid data exists), [Culture Vulture controls](prototypes/research/culture-vulture-controls.md) (Bias works by changing the 6AS6 cathode voltage), [6AS6 datasheets](prototypes/research/6as6-datasheets.md) (the unit biases the 6AS6 at ~0.1-0.6 mA, near cut-off, so the fit needs a low-current refit from the Raytheon 1960 sheet).
- Next: WDF version for comparison, the 6AS6 low-current refit, then a 6AS6 stage.

Circuit values in the prototypes are example values, not Culture Vulture values. Third-party valve models and datasheets are fetched by scripts, not redistributed.
