# Thermionic Culture Vulture: front-panel controls (sourced)

Compiled 2026-11-04. Everything below is quoted or paraphrased from the cited page; nothing is inferred unless marked **[our note]**.
No schematic or component values appear in any source.

## Sources
| ID | Document | URL | Copy (not in repo) |
|---|---|---|---|
| M-ORIG | *The Culture Vulture valve distortion unit — Operating Manual*, ©Thermionic Culture Oct 2005 (original 2-ch, 6AS6) | https://www.fullcompass.com/common/files/14250-Manual.pdf (read via fetch; curl gets 403). Mirror: http://www.stanleyhallstudios.co.uk/manuals/docs/thermionic/vulture_manual_iss5.pdf (returned HTTP 500 today) | not downloaded (blocked) |
| M-MP | *Culture Vulture Mastering PLUS Operating Manual*, iss. 4, ©2010-2014 | http://www.thermionicculture.com/images/stories/virtuemart/product/CVMasPlusman4.pdf (now 404 on live site; fetched from Wayback `web.archive.org/web/2020id_/…`) | downloaded via Wayback; not redistributed |
| M-S15 | *Culture Vulture Super 15 Operating Manual*, iss. 2, ©2014 | http://www.thermionicculture.com/images/stories/virtuemart/product/Super15Manual_Iss2.pdf (via Wayback 2022) | downloaded via Wayback; not redistributed |
| SOS | Hugh Robjohns, "Thermionic Culture The Culture Vulture", Sound On Sound, Aug 2003 (reviews serial VU0050) | https://www.soundonsound.com/reviews/thermionic-culture-culture-vulture | – |
| UA | Universal Audio, "Thermionic Culture Vulture Manual" (UAD plug-in, official emulation) | https://help.uaudio.com/hc/en-us/articles/33542576197908-Thermionic-Culture-Vulture-Manual | – |

Not found: a separate manual for the plain "Mastering" (non-Plus) or the "11th Anniversary" edition (M-S15 says Super 15 "retains the features found on our 11th Anniversary edition"). No Thermionic-made plug-in exists; the UA plug-in is the licensed emulation. Other reviews seen but not used: Bonedo (Vulture/Super 15/Ultra test, German), Audiofanzine Super 15 news.

## 1. Original stereo Culture Vulture (EF86 / 6AS6 / 5963)
Valves (M-ORIG §5.1): "Input - 2 x EF86; Distortion - 2 x 6AS6; Output - 5963 (12AU7 / ECC82 near equivalent)". SOS confirms the same per channel (5963 shared? SOS says "each channel employing an EF86 …, a 6AS6 …, and a 5963").

Per channel (mirror-image layout, SOS/UA):
| Control | Range / positions | What sources say |
|---|---|---|
| **Drive** | chicken-head, 1–11 (SOS) | "vary the amount of gain that the input … signals are subjected to" (M-ORIG §2). SOS: can reduce input "all the way down to complete silence". UA: input gain "amplified by the EF86 input stage valve" sent to the 6AS6. |
| **Bias** | 1–11 (SOS) | "The meter reads the current flowing through the 6AS6 valve and the bias control adjusts this current. Current setting for lowest distortion is at 0.25-0.3mA" (M-ORIG §2). Reverse acting: "As the Bias control is advanced, the amount of current … falls" (SOS); UA: clockwise decreases current; UA default knob 6 = 0.45 mA. Near zero current "only allow the peaks … through … a 'gated' effect" (M-ORIG §3). SOS: ~0.6 mA "warm"; <0.1 mA "starves" the valve; meter current "modulated by the audio signal" at high drive. |
| **Distortion** (3-pos rotary) | T / P1 / P2 | "changes the valve from triode 'T' (even harmonics) to pentode 'P1' (odd harmonics) … 'P2' is an unusual version of pentode … more drastic distortion at high bias settings, especially when 'overdrive' is selected … frequency doubling effect" (M-ORIG §2). P2 frequency doubling at 0.15–0.2 mA (M-ORIG §3). UA: P2 "unusual wiring", quieter than T/P1, polarity may invert at extremes. SOS: P2 has a "null point" of horrid distortion around 0.5 mA; P2 quietest; P1 noisier than T. |
| **Overdrive** (toggle) | off/on | "increases gain current by approximately 20dB, and bias" (M-ORIG §2). SOS: "increases both the Drive gain and Bias range". UA: +20 dB to 6AS6 input (UA adds 9 dB clean gain when off — a plug-in deviation). |
| **Filter** (3-pos rotary) | Off / 2 LP settings | **Conflict:** M-ORIG §2 "low pass filter operating at 5 or 9 kHz"; SOS (2003 unit) "Off, 4kHz or 7kHz … second-order, 12dB/octave". UA: 12 dB/oct LP "at the output of the 6AS6 distortion valve circuit"; slight HF roll-off even when Off, except in P2. Likely changed between production runs **[our note]**. |
| **Output level** | 1–11 | "around 8-9 will give the cleanest output for +4dB" (M-ORIG §2); SOS: unity between 7 and 8, doesn't reach silence. UA: feeds the 5963 output valve; >9 overdrives it. |
| **Bypass** (toggle) | | "links the input to output directly" (M-ORIG §2). |
| Meter | mA | 6AS6 current. Shared: mains switch + lamp. Front DI jack overrides rear input. |

Specs (M-ORIG §6): MOL +17 dBV; distortion 0.2%–99.5%; noise >75 dB below MOL; Zin 30 kΩ; Zout 2 kΩ (drive ≥10 kΩ); response ±1.75 dB 50 Hz–15 kHz (SOS quotes ±1.5 dB); max gain clean 35 dB, overdriven >60 dB. Rear "Hi" (+4 dBV) and "Lo" (−20 dB) outputs, unbalanced (M-ORIG §4).

## 2. Culture Vulture Mastering PLUS (5654 / 5725 / 13D5)
Valve complement (M-MP p.9): "Input 2 x 5654 (M8100, EF95, 6AK5); Distortion 2 x 5725 (6AS6); Output - 1 x 13D5 (ECC82, 12AU7)". Intro (p.3): "four types of distortion"; Mullard M8100 input "to achieve very low noise".
| Control | Positions | Quote (M-MP §2, pp.4-5) |
|---|---|---|
| **Drive** switch + pot | Normal / Drive / Overdrive | Normal flat; "Drive" "+10dB and adds some top end"; "Overdrive" "28dB above Normal". Pot sets signal "to the 2nd valve, a 5725". |
| **Output Level** switch + pot | (−10dB) switch, pot ~0..+? | controls signal to output valve; use −10 dB when pushing distortion "at other than P3, which always requires maximum gain". |
| **Bias** + meter | mA | "varies the current through V2 by changing the positive voltage on its cathode" (cathode-voltage bias, **key circuit detail**); best low-distortion 0.25 mA; "all of the valve's current is flowing through" the bias pot. |
| **Function** | T / P1 / P2 / P3 | T, P1 triode/pentode; "P2 … sound quite clean until overload is close … over-compression … dynamics are inverted"; "P3 is a very distorted sort of pentode. Rectifying effects … when lowering the bias". |
| **Filter** | on/off | "low pass filter taking out frequencies above 7 kHz at 12dB/octave". |
| **Bypass** | | hard-wire. |
Hints (p.6): frequency doubling on P2 & P3 "most likely to occur with high bias (around 0.1mA current)" (note: "high bias" = low current here). Specs (p.10, 1 kHz, 0.25 mA, T, 10 kΩ load): THD 0.2%; 40 Hz–17 kHz ±1 dB; MOL +17 dBu @2%; gain 47 dB line / 53 dB DI; noise −88 dB re MOL; max THD 98%; max gain 76 dB (P1, min bias); Zin 20 kΩ line / 50 kΩ DI; Zout 2 kΩ. Balanced outs via transformer (more "rounded", bass distortion at high level), unbalanced direct from output valve (p.7).

## 3. Culture Vulture Super 15 (2013; 5654 / 5725 / 5963)
Valves (M-S15 §5.1): "Input: 2 x 5654 (M8100, 5591, EF95); Distortion: 2 x 5725 (M8196, 6AS6); Output: 1 x 5963 (ECC82, 12AU7)".
| Control | Positions | Quote (M-S15 §2) |
|---|---|---|
| **Drive** switch + pot | pot to 15; Normal / +10dB / Overdrive | as M-MP; +10 "adds some top end"; Overdrive 28 dB above Normal. |
| **Output Level** + −10 dB switch | | |
| **Bias** + meter | | identical text to M-MP (positive voltage on cathode; 0.25 mA cleanest). |
| **Function** (10-pos) | T, P1, PK1–PK5, SQ1, SQ2, SQ3 | "All 'PK' positions use the pentode configuration. 'PK1' to 'PK5' have a sharp filter across the bias control … mid lift"; "SQ1 is a very distorted sort of pentode and SQ2 is a more extreme version (equivalent to P2 in the standard Culture Vulture)"; SQ3 = clean until overload then over-compression, inverted dynamics (= M-MP "P2" text). §3: "reducing the V2 current will increase the lift, whereas no bias, or max current, will nullify the effect" — i.e. PK = frequency-selective cathode degeneration **[our note: implies a tuned network across the cathode/bias pot]**. Frequency doubling on SQ settings at ~0.1 mA. |
| **Presence** (3-pos toggle) | up +3 dB / centre out / down +6 dB | only active on +10 dB drive; "to the mid range, peaking at 3kHz". |
| **Filter**, **Bypass** | | filter referenced in hints, no frequency given in Iss2 text; bypass disabled when DI used. |
Specs (§6): THD 0.2% at +4 dB; response Normal 40 Hz–18 kHz ±1.5 dB, Overdrive 50 Hz–11 kHz ±1.5 dB; MOL +17 dBu @2% / +25 dBu @20%; DI gain >65 dB; noise −85 dB; Zin 20 k line (transformer-balanced XLR) / 50 k DI; Zout line 1 kΩ, Lo 4 kΩ. Page 12 has a plotted frequency-response chart (Flat, LPF, Pres Mid/Hi, PK1–PK5) — digitisable for filter targets.

## 4. UA plug-in (official emulation of the original)
Controls per channel: Drive (1–11), Bias (1–11, reversed, default 6 = 0.45 mA), Distortion Type T/P1/P2, Overdrive (+20 dB; +9 dB clean when off — UA deviation), Filter (12 dB/oct LP after 6AS6 stage, 3-pos; frequencies not stated), Output Level (1–11 into 5963; default max), Bypass; global Control Link, Mix (0–100%, phase-accurate), Power, meters. Mix and Link are UA additions.

## Circuit facts usable for modelling (all sourced)
- Bias is set by varying the **positive cathode voltage** of the distortion pentode; the bias pot carries the full valve current (M-MP/M-S15 §2.3).
- Operating currents are tiny: 0.1–0.6 mA metered (vs. 6AS6 datasheet typical 3.6–5.2 mA at 120 V) → the valve runs near cut-off; starved-cathode gating (M-ORIG §3, SOS).
- Overdrive ≈ +20 dB (original) / +28 dB (later models); original Overdrive also shifts bias (M-ORIG, SOS).
- LP filter 12 dB/oct; 4/7 kHz (SOS 2003), 5/9 kHz (M-ORIG 2005), 7 kHz (M-MP); located after the distortion stage (UA).
- P2/SQ-type modes: frequency doubling (full-wave-rectifier-like), inverted dynamics, quieter — consistent with signal on g3 or the screen **[our hypothesis, unsourced]**.
- Not stated anywhere: supply voltages, plate/screen resistors, how T/P1/P2 are wired, filter topology.
