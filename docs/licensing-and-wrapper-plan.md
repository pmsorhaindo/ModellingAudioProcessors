# Licensing and plugin wrapper plan (#22)

Research fetched 18 Nov 2026. Every licence term, date, price and version below was read that day from the official
URL cited next to it. Licences, EULAs and prices change: read them again before any distribution.

**This is not legal advice.** It is an engineering summary of published terms. A lawyer should review it before
anything is distributed (see [Decisions still for Michael](#decisions-still-for-michael)).

## 1. Purpose and scope

Issue #22 asks for the DK core to be wrapped as an Audio Unit and VST® 3 plugin, and for the SDK/framework licences
and their obligations to be written down before any distribution. This document is that licensing note, plus a build
plan.

- Write-up only. This PR adds no plugin code, wrapper code, build files, SDK checkouts or binaries.
- Nothing is signed, notarised, published or released. None of that happens without Michael's later go.
- #22 stays open: its done-when also needs the plugin to build and load locally.

Already decided by Michael (18 Nov 2026), not reopened here:

- Wrapper: a thin wrapper of our own, written directly against the Steinberg VST 3 SDK and Apple's AudioUnitSDK.
- The repository is MIT-licensed. This PR adds the MIT `LICENSE` file, which main did not have.

## 2. Repository licence

Main (86c8cf4) has no `LICENSE` file, and GitHub reports no licence for the repository. This PR adds `LICENSE` at the
root with the standard MIT text and the proposed line `Copyright (c) 2026 Michael Sorhaindo`. Michael can correct
the legal name on that line (decision 1 below).

The root `LICENSE` covers this project's own code only. Third-party code keeps its own licence:

- When an SDK is vendored, fetched or linked later, its files stay under their own licence (MIT for the VST 3 SDK,
  Apache-2.0 for AudioUnitSDK, BSD-like for VSTGUI and mda). They are not relicensed as MIT by our `LICENSE`.
- Their licence and copyright notices stay in their files, and a copy of each licence ships next to the SDK code and
  with any binary (section 3.3).
- Existing rules in the README still apply: no code, impulse responses or presets from commercial plugins, and
  third-party valve models and datasheets are fetched by scripts, not redistributed.

## 3. Baseline: Steinberg VST 3 SDK + Apple AudioUnitSDK

Conclusion: the baseline is compatible with an MIT repository. Both SDKs are permissive. Our own code can be MIT; the
SDK files keep their own licences; distribution needs the notices listed below. No fees, no signed agreements, and no
obligation to publish our source.

### 3.1 VST 3 SDK (MIT since 3.8)

Sources:
[VST 3 License](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/VST3+License.html),
[VST 3 Licensing index](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/Index.html),
[FAQ: Licensing](https://steinbergmedia.github.io/vst3_dev_portal/pages/FAQ/Licensing.html),
[Version 3.8.0](https://steinbergmedia.github.io/vst3_dev_portal/pages/Versions/Version+3.8.0.html),
[Version 3.8.1](https://steinbergmedia.github.io/vst3_dev_portal/pages/Versions/Version+3.8.1.html),
[LICENSE.txt on master](https://github.com/steinbergmedia/vst3sdk/blob/master/LICENSE.txt),
[LICENSE.txt at v3.8.1_build_84](https://github.com/steinbergmedia/vst3sdk/blob/v3.8.1_build_84/LICENSE.txt).

- **Licence and version.** "Since version 3.8, VST 3 is licensed under MIT license." Version 3.8.0 (2025/10/20)
  introduced it: "VST 3 is now open source, released under the MIT license." The portal also says "Licensing under
  GPLv3 and the Steinberg proprietary license is no longer available." The newest tag on GitHub on 18 Nov 2026 was
  `v3.8.1_build_84` (version 3.8.1, dated 2026/08/11 on the portal). Use 3.8.0 or later; earlier SDKs carry the old
  licences.
- **Obligations.** The MIT condition: the copyright notice and permission notice must be included in all copies or
  substantial portions. The FAQ says the only obligation is to "include the original copyright notice and the MIT
  license text in all copies or substantial portions of the code", and that you "only need to provide the license
  and copyright notice when you share or distribute the software."
- **What the FAQ allows.** Source disclosure is not required ("The MIT License does not require you to disclose your
  source code"). Sharing source that includes or references the SDK on GitHub is allowed. Distributing or selling in
  binary form only, as freeware, shareware or paid, is allowed. Modifying the SDK is allowed. No fees, memberships or
  signed documents are needed.
- **Not everything in the SDK is MIT.** The licensing index says: "Mostly all files are under MIT license, except
  VSTGUI and mda files which are under BSD-like license." A thin wrapper with a generic host view does not need VSTGUI;
  leave it out of the build unless a later UI decision requires it, and then add its licence to the third-party
  notice. Do not use the mda example code.
- **VST 2 is not covered.** "VST 2 API source code is NOT part of this VST 3 license!" The FAQ says the VST 2 files
  (e.g. `aeffect.h`, `aeffectx.h`) must not be redistributed, and VST 2 binaries may only be distributed by those who
  signed the VST 2 licence before October 2018. This project builds VST 3 only.
- **Copyright year difference.** The MIT text on the portal's licence page says `Copyright (c) 2025, Steinberg Media
  Technologies GmbH`. `LICENSE.txt` on GitHub (master and `v3.8.1_build_84`, byte-identical) says `Copyright (c)
  2026, Steinberg Media Technologies GmbH`. Copy the `LICENSE.txt` from the exact SDK tag we build against; do not
  hand-edit it.

**Trademark and logo.** Source:
[Steinberg VST usage guidelines](https://steinbergmedia.github.io/vst3_dev_portal/pages/VST+3+Licensing/Usage+guidelines.html).

The licence page says trademark use "(e.g. "VST" name or logo) is optional under MIT license, but if used, must comply
with Steinberg's official trademark rules", and "Non-compliance with logo guidelines does not affect your MIT rights
(but may result in trademark violations)." If the name or logo is used, the guidelines ask for, among other things:

- The VST Compatible Logo (from `VST_SDK/VST3_SDK/vst3_doc/artwork` in the SDK) on packaging, on every web page that
  references or promotes the product (a footer or imprint alone is not enough), in documentation, in marketing, and in
  the About box or equivalent; a text attribution where space does not allow the logo. Minimum print sizes are given
  (20 x 18,7 mm and 15 x 10 mm).
- The ® symbol on the first use of "VST" in any product-related material, and this sentence in all product credits
  and documentation: "VST is a registered trademark of Steinberg Media Technologies GmbH."
- Compatibility claims only if the product is compatible, was built with the SDK, and the logo is near the claim.
- In a product name, "VST" only in plain text as a compatibility reference (their example: "MyCoolEngine for VST"),
  with the logo in immediate visual proximity, and not stylised or merged into the product brand.
- "VST" in a company name or company logo is prohibited.
- No modified terms or variants such as "VSTi" or other abbreviations, blends or derivatives.

Section 1 of the guidelines ties referring to the VST trademark (not only showing the logo) to the logo requirements.
Whether a plain factual format name such as "VST 3 plug-in" on a download page needs the logo under that wording is a
question for the lawyer.

Recommended practice until Michael decides otherwise (decision 2):

- Do not use the VST Compatible Logo, and do not put "VST" in the product name, company name, domain or social handles.
- In this repository's engineering docs, use "VST 3" only factually (the SDK and plugin format), with ® on the first
  use and the attribution sentence above. This document does so (first use in section 1, sentence in the footer).
- Before any public product page, packaging or About box mentions VST, decide whether to follow the full guidelines.

### 3.2 Apple AudioUnitSDK (Apache License 2.0)

Sources: [AudioUnitSDK repository and readme](https://github.com/apple/AudioUnitSDK/),
[LICENSE.txt](https://github.com/apple/AudioUnitSDK/blob/main/LICENSE.txt),
[release AudioUnitSDK-1.4.0](https://github.com/apple/AudioUnitSDK/releases/tag/AudioUnitSDK-1.4.0),
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0).

- **Licence.** Apache License, Version 2.0 (`LICENSE.txt` starts "Copyright (C) 2023 Apple Inc. All rights
  reserved."; GitHub identifies it as Apache-2.0).
- **Obligations when redistributing (section 4).** (a) give recipients a copy of the licence; (b) modified files must
  carry prominent notices that they were changed; (c) keep all copyright, patent, trademark and attribution notices in
  the source form; (d) if the work includes a `NOTICE` file, reproduce its attribution notices. On 18 Nov 2026 the
  repository root had no `NOTICE` file; check again in the tag we vendor.
- **Trademarks (section 6).** The licence does not grant use of Apple's names or marks, "except as required for
  reasonable and customary use in describing the origin of the Work and reproducing the content of the NOTICE file."
  Saying factually that the plugin is an Audio Unit built with Apple's AudioUnitSDK fits that; do not use Apple marks
  in the product name or branding.
- **Version and build constraints (to verify when building, not pinned here).** The latest release on 18 Nov 2026 was
  `AudioUnitSDK-1.4.0`, published 2025-12-05. The readme gives "macOS 11.0 / iOS 14.0 or later" as supported
  deployment targets. The 1.4.0 changelog says "C++ language version to `C++23` for `std::expected`", while the DK
  core and its tests build as C++17 today. The AU target (or a separate AudioUnitSDK library target) will need C++23
  on the Xcode toolchain we use; confirm that when the first build is written.
- **Compatibility with MIT.** Our code stays MIT. The AudioUnitSDK files stay Apache-2.0, unmodified where possible.
  Combining them in one binary is fine as long as the Apache-2.0 conditions are met for the SDK part.

### 3.3 Third-party notice (standard approach)

- Root `LICENSE`: MIT, for this project's own code.
- When an SDK is added (vendored, submodule or fetched by the build), put its own licence file next to it, unchanged,
  and add a short `THIRD_PARTY_NOTICES` file (or equivalent section) listing each one.
- Any distributed binary or installer carries the same notices (for example a licences file in the package and an
  entry in the About box or manual).

Draft text for that notice, to be checked against the exact SDK tags used:

> This product includes the Steinberg VST 3 SDK, Copyright (c) 2026, Steinberg Media Technologies GmbH, licensed under
> the MIT License (https://github.com/steinbergmedia/vst3sdk/blob/master/LICENSE.txt).
>
> This product includes Apple's AudioUnitSDK, Copyright (C) 2023 Apple Inc., licensed under the Apache License,
> Version 2.0 (https://github.com/apple/AudioUnitSDK/blob/main/LICENSE.txt).

Validation tools are not part of the product. pluginval is GPLv3
([README](https://github.com/Tracktion/pluginval)); running it as a separate program on our binaries does not put our
code under the GPL, but do not vendor, link or ship it. `auval` ships with macOS.

## 4. Alternatives compared with the baseline

Short answer: **nothing beats the baseline** for this job (a thin wrap of an already-written C++ core, AU + VST3,
MIT repository). The alternatives either add licence families and dependencies (iPlug2), add a copyleft or paid
licence (JUCE), or do not cover the required formats (CLAP).

| option | licence | formats | fit for #22 |
|---|---|---|---|
| Baseline: VST 3 SDK + AudioUnitSDK | MIT + Apache-2.0 | VST3, AUv2 | best licence fit; we write the host glue |
| iPlug2 | zlib-like, plus bundled libraries' own licences | CLAP, VST2, VST3, AUv2, AUv3, AAX, WAM | permissive, faster GUI path, more dependencies |
| JUCE 9 | AGPLv3 or commercial JUCE 9 EULA | AU, VST3 and more | does not fit an MIT, possibly closed binary without fees or AGPL |
| CLAP (extra) | MIT | CLAP only | compatible later add-on; not a replacement |

### 4.1 iPlug2

Sources: [LICENSE.txt](https://github.com/iPlug2/iPlug2/blob/master/LICENSE.txt),
[README](https://github.com/iPlug2/iPlug2), [iplug2.github.io](https://iplug2.github.io/).

- Licence: a zlib-style licence ("Permission is granted to anyone to use this software for any purpose, including
  commercial applications…"; do not misrepresent origin; mark altered sources; keep the notice in source
  distributions). Bundled libraries keep their own licences: Cockos WDL (zlib-like), NanoVG and NanoSVG (zlib),
  MetalNanoVG, RTAudio and RTMidi (MIT), optional Skia (BSD). The iPlug2 WebView library is MIT. The licence file adds:
  "It is up to you to comply with the licenses of every source file you use".
- Formats: the README lists CLAP, VST2, VST3, AUv2, AUv3, AAX (Native) and Web Audio Module, plus standalone apps.
  VST3 and AU builds still use the Steinberg and Apple SDKs underneath, so their notices are needed as well.
- Against the baseline: still permissive and compatible with an MIT repo, but it adds another licence family and a
  larger dependency set to track. It wins only if Michael later wants a custom GUI and extra formats sooner than
  we can write host glue. Not for v1.

### 4.2 JUCE

Sources: [LICENSE.md](https://github.com/juce-framework/JUCE/blob/master/LICENSE.md),
[JUCE 9 EULA](https://juce.com/legal/juce-9-licence/), [Get JUCE and licensing FAQ](https://juce.com/get-juce/),
[AGPLv3](https://www.gnu.org/licenses/agpl-3.0.en.html). (juce.com returned 403 to curl from this Mac; both pages were
read with a web fetch the same day.)

- Licence: "The JUCE Framework modules are dual-licensed under the AGPLv3 and the commercial JUCE licence." Anyone not
  using AGPLv3 agrees to the JUCE 9 EULA. The EULA changelog says "17th June 2026: JUCE 9 EULA Published." Examples
  are ISC. Third-party dependencies are inventoried in `JUCE.spdx.json` in the repository.
- JUCE 9 licence types (EULA section 1.2, as published):

  | | Starter | Indie | Pro | Educational |
  |---|---|---|---|---|
  | Annual revenue or funding limit | up to $20,000 | up to $300,000 | no limit | no limit |
  | Minimum subscription commitment | N/A | 1 month | 12 months | N/A |
  | Monthly subscription per user | N/A | $40 | $175 | N/A |
  | Perpetual price per user | Free | $800 | $3500 | Free |

- How the limit is counted (section 1.2.1, over the previous 12 months). For an individual (or an entity without a
  separate legal identity), it is "the total revenue or funding generated by that individual or entity's use of the
  Framework from all sources, including donations, sponsorship, advertising, and any other indirect revenue". For a
  company, it is "the total revenue or funding received by the entity and all its Affiliates … from all sources,
  whether it be received in connection with the entity's use of the Framework or not, without offsets of any kind."
  Section 1.14: exceeding the limit means buying the matching licence type "or immediately cease developing and
  Distributing Products." Educational licences "may not be used for any commercial, professional, promotional, or any
  other for-profit activity."
- FAQ: without AGPLv3 "you will require a JUCE licence", kept "for at least the duration over which you are
  distributing closed-source binaries" containing JUCE. The FAQ says it "does not replace or supersede" the EULA.
- Against the baseline: AGPLv3 would put the plugin under copyleft terms with source-offer obligations, which does
  not match distributing an MIT-licensed plugin in closed or permissive form. The commercial EULA adds revenue/funding
  caps and, above $20,000, fees. JUCE does not beat the baseline on licensing. It only wins if Michael wants JUCE's
  tooling and UI and accepts either a commercial licence or the AGPL.

### 4.3 CLAP as an optional extra

Sources: [LICENSE](https://github.com/free-audio/clap/blob/main/LICENSE) (MIT, "Copyright (c) 2021 Alexandre BIQUE"),
[An Open License](https://cleveraudio.org/1-feature-overview/_open_licnese/).

- MIT; the CLAP site says "the only requirement using the CLAP places on you is to make the CLAP license available
  somehow". Compatible with an MIT repo, same notice obligation as the VST 3 SDK.
- It does not replace AU or VST3, which #22 requires. It can be added in a later version with no licence clash; host
  support is narrower than for AU and VST3. Recommendation: later, not v1, unless Michael says otherwise (decision 6).

## 5. macOS signing and notarisation (later distribution step; not done in this work)

Sources: [What's included](https://developer.apple.com/programs/whats-included/),
[Program enrollment](https://developer.apple.com/help/account/membership/program-enrollment/),
[Notarizing macOS software before distribution](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
[Developer ID](https://developer.apple.com/developer-id/), [macOS distribution](https://developer.apple.com/macos/distribution/).

- Cost: "The Apple Developer Program is 99 USD per membership year, or in local currency where available."
- Not needed to build and test locally: "You can install apps on your personal device with Xcode. You'll only need to
  enroll if you'd like to distribute apps…". Building and loading the plugin on this Mac needs no membership.
- Distribution outside the Mac App Store: sign with a Developer ID certificate so Gatekeeper can verify it, and
  notarise. Apple: "Beginning in macOS 10.15, all software built after June 1, 2019, and distributed with Developer ID
  must be notarized."
- Plug-ins specifically: "In macOS 10.15 and later, apps can load quarantined plug-ins — those downloaded from the
  internet or transferred with AirDrop — only if the plug-in is notarized." Otherwise the user must approve it by hand
  (Apple's page still names System Preferences > Security & Privacy > General).
- Requirements for notarisation: Developer ID certificate; Hardened Runtime enabled; a secure timestamp on the
  signature; no `com.apple.security.get-task-allow` entitlement set to true. Upload with `notarytool` ("Starting
  November 1, 2023, the Apple notary service no longer accepts uploads from `altool` or Xcode 13 or earlier"), then
  staple the ticket with `stapler`.

Record only. Do not enroll, sign or notarise until Michael says go (decision 4).

## 6. Minimal build plan (plan only, no code in this PR)

1. **Build system.** CMake at the repo root (or under a new `plugin/` directory): one static library target for the
   DK core, and our own VST3 and AUv2 targets against the two SDKs. Steinberg's 3.8.1 changelog says it removed the
   AUWrapper target from the SDK's main `CMakeLists.txt` and fixed `auwrapper.mm` for "building AUWrapper with the new
   AUSDK". The baseline is our own targets against both SDKs, not a commitment to Steinberg's AUWrapper.
2. **SDK pinning.** Fetch the VST 3 SDK at a tagged 3.8.x release and AudioUnitSDK at a tagged release, recorded in
   the build files. Keep each licence file with it (section 3.3). Leave VSTGUI out.
3. **Core.** Wrap `dkcore::StereoStage` (`prototypes/fullstage/cpp/dk_core.h`): `prepare`, `process` (float or
   double), `latency_samples()` reported to the host, `set_drive`, `set_output`, `request_bias` off the audio thread,
   and the quality setting. Parameters: Drive, Bias, Output, quality (#22).
4. **Targets.** Universal macOS binary (`arm64;x86_64`). Deployment target no lower than AudioUnitSDK's macOS 11.0;
   C++23 for the AudioUnitSDK sources (section 3.2).
5. **UI.** The host's generic parameter view is enough for a first loadable AU and VST3.
6. **Validation.** `auval` for the AU and pluginval for both formats, on this Mac. Loading in a host locally counts;
   nothing is distributed.

Open issues the wrapper will meet:

- v1 scope and working name (#75).
- Parameter smoothing for drive, output and bias (#73).
- Host sample rates other than 48 kHz are untested (#74).
- The mapping between host full scale and the stage's volts, input and output, is still undefined (recorded on #22,
  14 Nov 2026).

## 7. Naming rule

No brand or device names in the product name; "inspired by" only (#75, #22). Following the recommendation in 3.1, no
"VST" in the product name either.

## Decisions still for Michael

1. **Legal name on the copyright line.** This PR adds `LICENSE` with the proposed line
   `Copyright (c) 2026 Michael Sorhaindo`. Correct it if the legal name or rights holder differs.
2. **VST trademark and logo.** Recommended default: no logo and no "VST" in product names; factual use with the
   attribution in docs only. Following Steinberg's full guidelines is the alternative.
3. **Product name and v1 scope** (#75).
4. **Apple Developer Program, signing, notarising, publishing:** whether and when. Blocked until he says go.
5. **Repository visibility.** The repository is private today. Decide whether it will be public; MIT allows either.
6. **CLAP** in a later version, or not.
7. **Legal review.** Confirm that a lawyer reviews the licences, the trademark question in 3.1 and the third-party
   notice before any distribution.

---

VST is a registered trademark of Steinberg Media Technologies GmbH. Apple and Steinberg product names are used here
only to describe the SDKs and plugin formats.
