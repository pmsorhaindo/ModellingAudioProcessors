# ModellingAudioProcessors
Research and C/C++ AU/VST3 implementations of analog audio processor models, starting with a Thermionic Culture Vulture-inspired saturator.

## Structure
| path | what |
|---|---|
| `references/` | schematics, datasheets and modelling papers (links; free author copies where found) |
| `prototypes/valvelab/` | static valve-stage lab: Koren/Dempwolf models, bias vs harmonics, ADAA vs oversampling, C benchmark |
| `prototypes/fit6as6/` | our 6AS6 model fitted to the GE datasheet (dual-control g3 extension is our own empirical model), verified in ngspice |
| `prototypes/fullstage/` | one 12AX7 common-cathode stage with its reactive parts: ngspice references, nodal DK prototype, (next) WDF |

## Status (4 Nov 2026)
- DK prototype of one 12AX7 stage: operating point, harmonics and tone-burst bias recovery match the same-model ngspice reference; small-signal response is 0.21 dB off at 20 kHz at 4x oversampling (trapezoidal warping) - see `prototypes/fullstage/dk/RESULTS.md`.
- Next: fix or decide on the HF criterion, WDF version for comparison, then a 6AS6 stage.

Circuit values in the prototypes are example values, not Culture Vulture values. Third-party valve models and datasheets are fetched by scripts, not redistributed.
