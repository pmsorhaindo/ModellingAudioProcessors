# Third-party notices

This repository's own code is MIT-licensed ([LICENSE](LICENSE)). The plugin wrapper in [plugin/](plugin/) builds
against the two SDKs below. Neither is vendored into this repository. CMake fetches each one at a pinned tag when the
build is configured ([plugin/CMakeLists.txt](plugin/CMakeLists.txt)). Their files keep their own licences. Our
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

## Test tools (not part of the product)

- The Steinberg VST 3 validator is built from the SDK above, under the same MIT licence. It is a test tool and is
  not shipped.
- pluginval (GPLv3, <https://github.com/Tracktion/pluginval>) may be run as a separate program on the built
  plugins. It is not vendored, linked or shipped.
- `auval` ships with macOS.
