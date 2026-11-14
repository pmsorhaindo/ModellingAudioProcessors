# References: background for a valve saturator inspired by the Thermionic Culture Vulture

Background reading for an AU/VST3 valve saturator in C/C++, *inspired by* the Thermionic Culture Vulture, a two-channel all-valve distortion unit. The plugin is not an emulation of that unit and will not carry its name.

| File | What's in it |
| --- | --- |
| [schematics.md](schematics.md) | Manufacturer manuals and product pages, reviews, the UAD plugin docs, forum threads, and the state of public schematics |
| [tube-modelling-papers.md](tube-modelling-papers.md) | Triode and pentode device models, plus circuit-level valve simulation (WDF, DK method, state-space) |
| [virtual-analog-techniques.md](virtual-analog-techniques.md) | General virtual-analog methods: ADAA, oversampling, ZDF/TPT filters, WDF tooling |
| [datasheets.md](datasheets.md) | Datasheets for the valves the unit uses, including the substitutes the manuals list |

## What we know so far

- **There is no public schematic.** We found no official or reliable traced schematic for any revision (see `schematics.md`). The topology has to be pieced together from the manuals, reviews, and datasheets, then confirmed against measurements of real hardware.
- **Signal chain** (per channel, from the manuals): a pentode input stage, then the distortion valve (6AS6 / 5725), then a shared twin triode (5963) for output. Different revisions use different input valves; see the revision table in `schematics.md`.
- **Distortion stage:** the 6AS6 / 5725 is a dual-control pentode. The Function switch rewires it as a triode (T) or as a pentode (P1, P2, and later P3 and SQ settings). The Bias control changes the cathode current, which the front-panel meter shows in mA.
- **Suggested modelling path:**
  1. Fit a Koren-style or Dempwolf-style device model to the 6AS6 datasheet curves.
  2. Simulate the guessed stage in ngspice.
  3. Build a WDF or DK-method model, using chowdsp_wdf for prototyping.
  4. Control aliasing with oversampling and/or ADAA.
  5. Validate against measurements of real hardware.

## How to use these files

- Every entry gives the title, author or source, year, and a URL that was fetched and checked on 2026-10-10, plus a note on why it matters for this project.
- **Access:** entries marked *paywalled* need a subscription. *Open* means the full text is free to read. *Archived* means the original page is gone and the link points to a Wayback Machine snapshot.
- **Copyright:** this folder only links to sources. Don't commit PDFs, scans, or photos from them.
- **Unverified claims:** forum guesses about the circuit are speculation and are labelled that way. Don't treat a component value as fact unless it comes from a primary source or a measurement.
- **Adding an entry:** fetch the link first and confirm it is what it claims to be. If you can't, put it under "Unverified".
