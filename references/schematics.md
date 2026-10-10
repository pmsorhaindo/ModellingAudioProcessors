# Culture Vulture: schematics, manuals and circuit information

**Bottom line: we found no public schematic for any revision.** Every source below that describes the circuit is either a manual, a review, or forum speculation. A French forum post (Audiofanzine, 2009–2010) says a schematic once appeared on the Prodigy-Pro DIY forum and was quickly taken down. We could not find it, and nothing here relies on it.

When this was checked (2026-10-10), thermionicculture.com served an empty directory index. Thermionic Culture's own pages and PDFs below are therefore linked through Wayback Machine snapshots or third-party mirrors.

## Valve complement by revision (as the sources state it)

| Revision / document | Input | Distortion | Output | Source | Confidence |
| --- | --- | --- | --- | --- | --- |
| Original Culture Vulture (SOS review, Aug 2003; serial VU0050) | EF86 | 6AS6 | 5963 (ECC82/12AU7 equivalent) | SOS review | High: reviewer inspected a unit |
| Original Culture Vulture manual (© Oct 2005) | 2 × EF86 | 2 × 6AS6 | 5963 (12AU7 / ECC82 near equivalent) | Manufacturer manual | High |
| Culture Vulture product page (archived 2026) | not stated | 6AS6 type | not stated | Manufacturer page | High for the distortion valve; says the unit "has changed little since its introduction in 1998" |
| Mastering Plus manual (© May 2010; the fuse section calls it the "Anniversary Edition") | 2 × M8100 (CV4010, 5591, EF95) | 2 × 5725 (CV4011, M8196, 6AS6) | 1 × 5963 (CV4003, ECC82, 12AU7) | Manufacturer manual | High |
| Mastering Plus manual, issue 4 (© 2010–2014) | 2 × 5654 (M8100, EF95, 6AK5) | 2 × 5725 (6AS6) | 1 × 13D5 (ECC82, 12AU7) | Manufacturer manual | High that the manual says this. The 13D5 output type appears only here and is not cross-checked. |
| Super 15 manual, issue 2 (© 2014; product introduced 2013) | 2 × 5654 (M8100, 5591, EF95) | 2 × 5725 (M8196, 6AS6) | 1 × 5963 (ECC82, 12AU7) | Manufacturer manual | High |
| CV20A 20th Anniversary | not verified | not verified | not verified | Product page only says "All Mullard/Phillips factory valves" | Low: the CV20 manual URL is dead and was not archived |
| UAD plugin (UA, 2014) | EF86 | 6AS6 | 5963 | UA manual | High for what UA modelled (the original unit) |

The valve equivalents listed in brackets are the manufacturer's, "nearest first". The 5725 is the ruggedised version of the 6AS6.

## Manufacturer documents

- **The Culture Vulture: valve distortion unit, operating manual.** Thermionic Culture Ltd, © October 2005. Mirror (velvetsound.net): <https://www.velvetsound.net/userfiles/image/produkty/134/prilohy/CultureVultureValveDistortionUnitManual9.pdf>
  - The best primary source for the original unit. It gives the valve complement, the T/P1/P2 modes, an Overdrive switch of about +20 dB, the low-pass filter ("5 or 9 kHz"), and a bias of 0.25–0.3 mA for lowest distortion.
  - Specs: 30 kΩ input, 2 kΩ output, +17 dBV MOL, 35 dB clean gain, more than 60 dB overdriven, ±1.75 dB from 50 Hz to 15 kHz.
- **The Culture Vulture Mastering PLUS: operating manual.** Thermionic Culture Ltd, © May 2010. Mirror (Thomann): <https://images.thomann.de/pics/atg/atgdata/document/manual/cvmp.pdf>
  - Function switch: T is described as "all even harmonics", P1 as odd, P2 as a "new discovery" (clean until near overload, then over-compression with dynamics inverted), and P3 as the old P2.
  - Drive switch: +10 dB at "Drive", +28 dB at "Overdrive".
  - Filter: 7 kHz at 12 dB/octave.
  - Bias: "varies the current through V2 by changing the positive voltage on its cathode".
  - Includes measured specs.
- **The Culture Vulture Mastering PLUS: operating manual (issue 4).** Thermionic Culture Ltd, © 2010–2014. Archived: <https://web.archive.org/web/20260413133531/http://thermionicculture.com/images/stories/virtuemart/product/CVMasPlusman4.pdf>
  - A later issue that lists different valve types (5654 input, 13D5 output).
- **The Culture Vulture Super 15: operating manual (issue 2).** Thermionic Culture Ltd, © 2014. Archived: <https://web.archive.org/web/20260511001403/http://www.thermionicculture.com/images/stories/virtuemart/product/Super15Manual_Iss2.pdf>
  - A 10-position function switch (T, P and SQ "squash" settings) and a Presence switch peaking at 3 kHz. This is the most feature-rich revision, and a later target.
- **The Culture Vulture product page.** Thermionic Culture Ltd, archived 2026. <https://web.archive.org/web/20260607050655/https://www.thermionicculture.com/index.php/products/the-culture-vulture-1-17-192012-03-20-11-02-02-detail>
  - Describes the bias as current through the "6AS6 type" valve, with lowest distortion at 0.25–0.3 mA, and a 12 dB/octave low-pass at "6 or 9 kHz". It says P2 can give "an extra octave at higher bias settings".
- **The Culture Vulture Super 15 product page.** Thermionic Culture Ltd, archived 2026. <https://web.archive.org/web/20260511001403/http://www.thermionicculture.com/index.php/products/cvsuper15-detail>
  - Input valves are "M8100/5654 of Mullard origin". The line inputs are balanced and the outputs unbalanced.
- **The Culture Vulture CV20A product page.** Thermionic Culture Ltd, archived 2026. <https://web.archive.org/web/20260607054343/http://www.thermionicculture.com/index.php/products/the-culture-vulture-1-17-192012-03-20-11-02-02-7-detail>
  - The 20th Anniversary model. It has Sowter-transformer-balanced line I/O, and P2 (over-compression) and P3 ("the original P2").

**The filter corner frequencies differ between sources:** 4/7 kHz (SOS 2003), 5/9 kHz (2005 manual), 7 kHz (Mastering Plus), and 6/9 kHz (product page). Measure the target unit rather than picking one.

## Reviews

- **Thermionic Culture The Culture Vulture.** Hugh Robjohns, *Sound On Sound*, August 2003. <https://www.soundonsound.com/reviews/thermionic-culture-culture-vulture>
  - Confirms EF86 → 6AS6 → 5963 per channel, with point-to-point tag-strip construction and unbalanced I/O.
  - The meter falls as Bias is advanced. About 0.2–0.4 mA is "normal", about 0.6 mA gives "warm", and below 0.1 mA the valve is "starved" and gates.
  - P2 has a "null point" of harsh distortion near 0.5 mA.
  - The filter is a second-order design with 4 kHz and 7 kHz settings.
  - Overall the most useful qualitative description of the controls.
- **Thermionic Culture: The Culture Vulture.** Larry Crane, *Tape Op* issue #45, Jan/Feb 2005. <https://backup.tapeop.com/reviews/gear/45/the-culture-vulture/index.html>
  - Qualitative user report: it drops to about 0.2% distortion in normal mode, and a starved bias makes the sound collapse.

## Universal Audio plugin (an existing emulation of the original unit)

- **Thermionic Culture Vulture manual.** Universal Audio, page updated 2025. <https://help.uaudio.com/hc/en-us/articles/33542576197908-Thermionic-Culture-Vulture-Manual>
  - Plugin behaviour to compare against:
    - Drive feeds the EF86 into the 6AS6. Bias sets the 6AS6 cathode current, and the knob is reversed, with the default giving 0.45 mA.
    - P2 is quieter and can invert polarity.
    - Overdrive adds +20 dB.
    - The 12 dB/octave filter sits after the distortion stage. There is a slight high-frequency roll-off even with the filter off, except in P2.
    - The output stage (5963) adds its own colouration above about 9 on the output knob.
  - The plugin also adds +9 dB of clean gain when Overdrive is off. That is a deliberate change from the hardware.
- **UA press release: Thermionic Culture Vulture plug-in.** Universal Audio, 8 July 2014. <https://www.uaudio.com/blogs/press/thermionic-culture-vulture>
  - Says the plugin models all three topologies and is UA's "first ever pentode tube circuit model".
- **Thermionic Culture Vulture product page.** Universal Audio. <https://www.uaudio.com/products/thermionic-culture-vulture>
  - A commercial reference for A/B listening. Don't copy code, presets, or impulse responses from it.

## Forum threads and DIY projects (speculative, not schematics)

- **"6AS6 as Distortion Tube".** GroupDIY, 2008. <https://groupdiy.com/threads/6as6-as-distortion-tube.27906/>
  - Discussion of how the 6AS6 might be used: suppressor grid (g3) as a second control grid, lowered screen voltage, cathode-pot bias. **Speculation, not a trace.**
- **"Tube Warmer Similar To Culture Vulture".** GroupDIY, 2010–2011. <https://groupdiy.com/threads/tube-warmer-similar-to-culture-vulture.42421/>
  - Notes from internal photos: a dual-ganged bias pot with the meter in series, and a mains transformer by Sowter.
  - Guesses at T/P1/P2 wiring (screen and suppressor grids tied to plate for T; suppressor to cathode for P1).
  - **Speculation.** One guess in the thread that the preamp valve is a 12AX7 is wrong according to the manuals.
- **"thermionik CULture VULTURE schematic needed".** GroupDIY, 2007. <https://groupdiy.com/threads/thermionik-culture-vulture-schematic-needed.25506/>
  - Repair request. No schematic was ever posted.
- **"en DIY ?"** Audiofanzine forum (French), 2009–2014. <https://fr.audiofanzine.com/effet-studio-divers/thermionic-culture/the-culture-vulture/forums/t.372532,en-diy.html>
  - Reports that a schematic was briefly posted on Prodigy-Pro and removed. Unverified hearsay.
- **"6As6 tube injector".** Kaada Wrongroom blog, 2009. <http://wrongroom.blogspot.com/2009/04/6as6-tube-injector.html>
  - A builder's clone of "a more famous tube distortion": two 6AS6, 10k:10k transformers, E88CC output. The schematic is explicitly not shared.
  - A commenter describes experiments with g3 held at +6 V and with the screen grid (g2) decoupled or not.
- **Rude Tube Distortion Effect.** DIYRE wiki, 2012. <https://www.diyrecordingequipment.com/projects/rude-tube-distortion-effect/>
  - A different, open DIY line-level valve distortion based on the REDD.47. It has drive and bias controls, triode/pentode/peak-filter modes, and a switchable low-pass.
  - Not a Culture Vulture clone, but a useful topology to compare with in SPICE.

## Not found

- No official, leaked, or traced schematic for the original, Mastering, Mastering Plus, Super 15, CV20A, Solo Vulture, or Ultra Vulture.
- No public service manual or service notes beyond the valve-swap procedure in the manuals.
- The "Culture Vulture issue 5" manual mirrored at stanleyhallstudios.co.uk turned up in search but did not load, so it is not linked.
- The CV20 manual (`CV20_Manual_Iss2.pdf`) on thermionicculture.com is dead and has no archive snapshot. A search snippet showed M8100 / 6AS6 / 5963, but this is **unverified**.
