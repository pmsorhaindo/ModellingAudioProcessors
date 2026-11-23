# Third-party notices

This repository's own code is MIT-licensed ([LICENSE](LICENSE)), except the adapted SciPy source listed below, which
keeps SciPy's BSD licence. The plugin wrapper in [plugin/](plugin/) builds
against the two SDKs below, and its editor uses the CHOC headers below. None of them is vendored into this repository. CMake fetches each one at a pinned tag or commit when
the build is configured ([plugin/CMakeLists.txt](plugin/CMakeLists.txt)). Their files keep their own licences. Our
`LICENSE` does not relicense them. Any distributed binary must carry these notices and the licence texts (see
[docs/licensing-and-wrapper-plan.md](docs/licensing-and-wrapper-plan.md), section 3.3). Nothing is distributed yet.

## Steinberg VST 3 SDK

- Version: tag `v3.8.1_build_84` (commit `3cdf9ca5d1f5b1b21e0a86832aa4abe55607bd96`), with only the submodules
  `base`, `cmake`, `pluginterfaces` and `public.sdk`.
- Licence: MIT, `Copyright (c) 2026, Steinberg Media Technologies GmbH`:
  <https://github.com/steinbergmedia/vst3sdk/blob/v3.8.1_build_84/LICENSE.txt>
- Not fetched or built: VSTGUI and the mda example files (BSD-like licences), the documentation and the tutorials.
  The VST 2 API is not part of the SDK licence and is not used.

> This product includes the Steinberg VST 3 SDK, Copyright (c) 2026, Steinberg Media Technologies GmbH, licensed
> under the MIT License (https://github.com/steinbergmedia/vst3sdk/blob/v3.8.1_build_84/LICENSE.txt).

VST is a registered trademark of Steinberg Media Technologies GmbH.

## Apple AudioUnitSDK

- Version: tag `AudioUnitSDK-1.4.0` (commit `bd98b31feff57a15989fcfab4cd86dc63382b1ac`). Built as C++23,
  which its 1.4.0 changelog requires for `std::expected`. The SDK files are unmodified. The tag has no `NOTICE`
  file.
- Licence: Apache License, Version 2.0:
  <https://github.com/apple/AudioUnitSDK/blob/AudioUnitSDK-1.4.0/LICENSE.txt>

> This product includes Apple's AudioUnitSDK, Copyright (C) 2023 Apple Inc., licensed under the Apache License,
> Version 2.0 (https://github.com/apple/AudioUnitSDK/blob/AudioUnitSDK-1.4.0/LICENSE.txt).

## CHOC (Tracktion), the web editor's WebView (#105)

- Version: commit `8af031c2ba4f5723f4b0a5ca14e0b4984af15b0e` (main, 23 Oct 2026), fetched by CMake. Header-only.
  The plugin compiles `choc/gui/choc_WebView.h` and the headers it includes (on macOS: `platform/choc_Platform.h`,
  `choc_Assert.h`, `choc_ObjectiveCHelpers.h`; `text/choc_JSON.h`, `choc_StringUtilities.h`, `choc_UTF8.h`,
  `choc_FloatToString.h`; `containers/choc_Value.h`; `math/choc_MathHelpers.h`). On macOS the WebView is the system's WKWebView (WebKit framework); no browser
  engine is bundled. The Windows-only code in that header (which embeds Microsoft's WebView2 loader, under its own
  licence) is not compiled on macOS.
- Licence: ISC, <https://github.com/Tracktion/choc/blob/8af031c2ba4f5723f4b0a5ca14e0b4984af15b0e/LICENSE.md>,
  reproduced here as it requires ("the above copyright notice and this permission notice appear in all copies"):

```
Copyright (c) 2025 Tracktion Corporation

Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby
granted, provided that the above copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.
```

## SciPy (adapted source: Brent's root finder)

- What: `detail::brentq` in [prototypes/fullstage/cpp/dk6as6_core.h](prototypes/fullstage/cpp/dk6as6_core.h) is a
  C++ translation of SciPy's `brentq` (`scipy/optimize/Zeros/brentq.c`, "Written by Charles Harris"), so that the
  C++ 6AS6 stage solves its cathode resistor exactly as `dk6as6/stage.py` does through `scipy.optimize.brentq` (#104).
  The control flow and expressions are kept; the callback, the status codes and the statistics are adapted.
- Source: <https://github.com/scipy/scipy/blob/v1.18.1/scipy/optimize/Zeros/brentq.c>, SciPy 1.18.1 (the version in
  `prototypes/fullstage/requirements.txt`).
- Licence: BSD 3-Clause, <https://github.com/scipy/scipy/blob/v1.18.1/LICENSE.txt>, reproduced here as its condition 1
  requires:

```
Copyright (c) 2001-2002 Enthought, Inc. 2003, SciPy Developers.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above
   copyright notice, this list of conditions and the following
   disclaimer in the documentation and/or other materials provided
   with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived
   from this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Test tools (not part of the product)

- The Steinberg VST 3 validator is built from the SDK above, under the same MIT licence. It is a test tool and is
  not shipped.
- pluginval (GPLv3, <https://github.com/Tracktion/pluginval>) may be run as a separate program on the built
  plugins. It is not vendored, linked or shipped.
- `auval` ships with macOS.
