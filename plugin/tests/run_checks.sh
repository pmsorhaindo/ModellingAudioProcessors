#!/bin/bash
# run_checks.sh -- the plugin checks (#22), after `cmake --build plugin/build`.
#
#   plugin/tests/run_checks.sh [build-dir]            engine and smoothing tests, VST 3 validator (normal +
#                                                     extensive), VST 3 null, VST 3 web editor test
#   plugin/tests/run_checks.sh [build-dir] --au       also: install the AU into ~/Library/Audio/Plug-Ins/Components
#                                                     (the user's own folder), auval, AU null (arm64, and x86_64
#                                                     under Rosetta when it is installed), AU web editor test
#
# Exits non-zero at the first failure. No signing identity is used (only ad-hoc signatures: the linker's, and the
# VST 3 SDK's post-build one on the .vst3), and nothing is published.
set -euo pipefail
BUILD="${1:-$(cd "$(dirname "$0")/.." && pwd)/build}"
AU=0
[[ "${2:-}" == "--au" || "${1:-}" == "--au" ]] && AU=1
[[ "${1:-}" == "--au" ]] && BUILD="$(cd "$(dirname "$0")/.." && pwd)/build"
BIN="$BUILD/bin/Release"
# bundle file name and AU codes come from map_identity.h, so they cannot drift from the build
IDENTITY="$(cd "$(dirname "$0")/.." && pwd)/src/map_identity.h"
ident() {
    local v
    v="$(sed -n "s/^#define $1 \"\(.*\)\"$/\1/p" "$IDENTITY")"
    [[ -n "$v" ]] || { echo "FAIL: $1 not found in $IDENTITY" >&2; exit 1; }
    echo "$v"
}
BUNDLE_FILE="$(ident MAP_BUNDLE_FILE)"
AU_TYPE="$(ident MAP_AU_TYPE)"
AU_SUBTYPE="$(ident MAP_AU_SUBTYPE)"
AU_MANUFACTURER="$(ident MAP_AU_MANUFACTURER)"
VST3="$BUILD/VST3/Release/$BUNDLE_FILE.vst3"
COMPONENT="$BUILD/AU/$BUNDLE_FILE.component"

echo "== engine test"
"$BIN/map_engine_test"

echo "== smoothing test (#73: Drive / Output / Bias moves, block sizes, drive ramp vs aliasing)"
"$BIN/map_smoothing_test"

echo "== VST 3 validator"
"$BIN/validator" "$VST3" | tail -n 3
echo "== VST 3 validator, extensive (-e)"
"$BIN/validator" -e "$VST3" | tail -n 3

echo "== VST 3 null (48 kHz standard; 44.1 kHz eco; 96 kHz hq)"
"$BIN/map_null_vst3" "$VST3"
"$BIN/map_null_vst3" "$VST3" --fs 44100 --quality 0 --drive 4 --bias 1.0 --output 0.5
"$BIN/map_null_vst3" "$VST3" --fs 96000 --quality 2 --drive 1 --bias 1.5 --output 1

UI_ARGS=("$VST3")
[[ $AU == 1 ]] && UI_ARGS+=(--au)

if [[ $AU == 1 ]]; then
    DEST="$HOME/Library/Audio/Plug-Ins/Components/$BUNDLE_FILE.component"
    echo "== install AU -> $DEST"
    # the pre-#116 temporary identity (aufx vls1 MapT), so it does not stay registered beside the renamed one
    rm -rf "$HOME/Library/Audio/Plug-Ins/Components/MAPValveStage-temporary.component"
    rm -rf "$DEST"
    ditto "$COMPONENT" "$DEST"
    killall -9 AudioComponentRegistrar 2>/dev/null || true
    sleep 1
    echo "== auval"
    auval -v "$AU_TYPE" "$AU_SUBTYPE" "$AU_MANUFACTURER" | tail -n 4
    auval -v "$AU_TYPE" "$AU_SUBTYPE" "$AU_MANUFACTURER" | grep -q "AU VALIDATION SUCCEEDED"
    echo "== AU null (48 kHz standard; 44.1 kHz eco; 96 kHz hq)"
    "$BIN/map_null_au"
    "$BIN/map_null_au" --fs 44100 --quality 0 --drive 4 --bias 1.0 --output 0.5
    "$BIN/map_null_au" --fs 96000 --quality 2 --drive 1 --bias 1.5 --output 1
    if arch -x86_64 /usr/bin/true 2>/dev/null; then
        echo "== AU null, x86_64 slice (Rosetta)"
        arch -x86_64 "$BIN/map_null_au"
    fi
fi
# #105: both editors in a test window (needs a window server, i.e. a logged-in GUI session; MAP_SKIP_UI_TEST=1 skips)
if [[ "${MAP_SKIP_UI_TEST:-0}" == 1 ]]; then
    echo "== web editor test: skipped (MAP_SKIP_UI_TEST=1)"
else
    echo "== web editor test (VST 3$([[ $AU == 1 ]] && echo " + AU"): page, parameter bridge, key focus, resize, open / close)"
    # the full per-check listing is long; show the summary lines (pipefail keeps the test's exit status)
    "$BIN/map_ui_test" "${UI_ARGS[@]}" | grep -E "FAIL|PASS|info|^==|-> mode"
fi
echo "== all plugin checks passed"
