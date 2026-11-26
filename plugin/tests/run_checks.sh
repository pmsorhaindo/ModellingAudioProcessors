#!/bin/bash
# run_checks.sh -- the plugin checks (#22), after `cmake --build plugin/build`.
#
#   plugin/tests/run_checks.sh [build-dir]            engine and smoothing tests, VST 3 validator (normal +
#                                                     extensive), VST 3 null
#   plugin/tests/run_checks.sh [build-dir] --au       also: install the AU into ~/Library/Audio/Plug-Ins/Components
#                                                     (the user's own folder), auval, AU null (arm64, and x86_64
#                                                     under Rosetta when it is installed)
#   PLUGINVAL=/path/to/pluginval                      also: validate each requested format at strictness 10
#
# Exits non-zero at the first failure. No signing identity is used (only ad-hoc signatures: the linker's, and the
# VST 3 SDK's post-build one on the .vst3), and nothing is published.
set -euo pipefail
BUILD="${1:-$(cd "$(dirname "$0")/.." && pwd)/build}"
AU=0
[[ "${2:-}" == "--au" || "${1:-}" == "--au" ]] && AU=1
[[ "${1:-}" == "--au" ]] && BUILD="$(cd "$(dirname "$0")/.." && pwd)/build"
BUILD="$(cd "$BUILD" && pwd)"
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

echo "== engine test (both valve paths: nulls, bypass, live switches, no allocation / lock / syscall, saved state)"
"$BIN/map_engine_test"

echo "== smoothing test (#73: Drive / Output / Bias moves, block sizes, drive ramp vs aliasing)"
"$BIN/map_smoothing_test"

echo "== VST 3 validator"
"$BIN/validator" "$VST3" | tail -n 3
echo "== VST 3 validator, extensive (-e)"
"$BIN/validator" -e "$VST3" | tail -n 3

echo "== VST 3 null, 12AX7 path (48 kHz standard; 44.1 kHz eco; 96 kHz hq)"
"$BIN/map_null_vst3" "$VST3"
"$BIN/map_null_vst3" "$VST3" --fs 44100 --quality 0 --drive 4 --bias 1.0 --output 0.5
"$BIN/map_null_vst3" "$VST3" --fs 96000 --quality 2 --drive 1 --bias 1.5 --output 1
echo "== VST 3 null, 6AS6 path (48 kHz standard P1; 44.1 kHz eco = standard, T at the 0.5 mA cap, 7k; 48 kHz hq P2 4k)"
"$BIN/map_null_vst3" "$VST3" --valve 0 --seconds 1
"$BIN/map_null_vst3" "$VST3" --valve 0 --seconds 0.5 --fs 44100 --quality 0 --drive 4 --bias6 0.6 --mode6 1 --filter6 2
"$BIN/map_null_vst3" "$VST3" --valve 0 --seconds 0.5 --quality 2 --drive 1 --bias6 0.45 --mode6 2 --filter6 1

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
    auval_status=0
    auval_log=$(auval -v "$AU_TYPE" "$AU_SUBTYPE" "$AU_MANUFACTURER") || auval_status=$?
    printf '%s\n' "$auval_log"
    if [[ $auval_status != 0 || "$auval_log" != *"AU VALIDATION SUCCEEDED"* ]]; then
        echo "FAIL: auval did not validate $BUNDLE_FILE" >&2
        exit 1
    fi
    echo "== AU null, 12AX7 path (48 kHz standard; 44.1 kHz eco; 96 kHz hq)"
    "$BIN/map_null_au"
    "$BIN/map_null_au" --fs 44100 --quality 0 --drive 4 --bias 1.0 --output 0.5
    "$BIN/map_null_au" --fs 96000 --quality 2 --drive 1 --bias 1.5 --output 1
    echo "== AU null, 6AS6 path (as the VST 3 ones)"
    "$BIN/map_null_au" --valve 0 --seconds 1
    "$BIN/map_null_au" --valve 0 --seconds 0.5 --fs 44100 --quality 0 --drive 4 --bias6 0.6 --mode6 1 --filter6 2
    "$BIN/map_null_au" --valve 0 --seconds 0.5 --quality 2 --drive 1 --bias6 0.45 --mode6 2 --filter6 1
    if arch -x86_64 /usr/bin/true 2>/dev/null; then
        echo "== AU null, x86_64 slice (Rosetta)"
        arch -x86_64 "$BIN/map_null_au"
    fi
fi
if [[ -n "${PLUGINVAL:-}" ]]; then
    "$PLUGINVAL" --version
    echo "== pluginval VST 3, strictness 10"
    "$PLUGINVAL" --strictness-level 10 --validate "$VST3"
    if [[ $AU == 1 ]]; then
        echo "== pluginval AU, strictness 10"
        "$PLUGINVAL" --strictness-level 10 --validate "$COMPONENT"
    fi
fi
echo "== all plugin checks passed"
