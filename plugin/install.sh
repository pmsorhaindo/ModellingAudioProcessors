#!/bin/bash
# Install the two local test bundles on macOS.
set -euo pipefail

fail() {
    printf 'Install failed: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s [build-dir]\n       %s path/to/Gyre.vst3 path/to/Gyre.component\n' "$0" "$0"
}

[[ "${1:-}" != --help ]] || { usage; exit 0; }
plugin_dir=$(cd "$(dirname "$0")" && pwd)
bundle_file=$(sed -n 's/^#define MAP_BUNDLE_FILE "\(.*\)"$/\1/p' "$plugin_dir/src/map_identity.h")
[[ -n "$bundle_file" ]] || fail 'MAP_BUNDLE_FILE is missing from map_identity.h'
vst3="$bundle_file.vst3"
au="$bundle_file.component"
case $# in
    0|1)
        build=${1:-"$plugin_dir/build"}
        sources=("$build/VST3/Release/$vst3" "$build/AU/$au")
        ;;
    2) sources=("$1" "$2") ;;
    *) usage >&2; exit 1 ;;
esac

[[ "${sources[0]%/}" == *.vst3 && "${sources[1]%/}" == *.component ]] ||
    fail 'expected a .vst3 bundle followed by a .component bundle'
for source in "${sources[@]}"; do
    executable="$source/Contents/MacOS/$bundle_file"
    [[ -f "$source/Contents/Info.plist" && -f "$executable" && -x "$executable" ]] ||
        fail "missing or incomplete bundle: $source"
done

[[ "${HOME:-}" == /* ]] || fail 'HOME must be an absolute directory'
home=$(cd -- "$HOME" && pwd -P)
[[ ! "$home/Library" -ef /Library && ! "$home/Library" -ef /System/Library ]] ||
    fail 'installation into a system Library is not allowed'
plugins="$home/Library/Audio/Plug-Ins"
destinations=("$plugins/VST3/$vst3" "$plugins/Components/$au")
for path in "$home/Library" "$home/Library/Audio" "$plugins" \
    "$plugins/VST3" "$plugins/Components" "${destinations[@]}"; do
    [[ ! -L "$path" && ( ! -e "$path" || -d "$path" ) ]] ||
        fail "expected a directory or an unused path: $path"
done

mkdir -p "$plugins/VST3" "$plugins/Components"
# A single staging directory also serializes two installs into the same Plug-Ins folder.
stage="$plugins/.gyre-install"
mkdir -m 700 "$stage" || fail "could not create staging directory: $stage"
names=("$vst3" "$au")
replacing=0
cleanup() {
    local status=$? i staged previous dest restored=1
    if (( replacing )); then
        for i in 0 1; do
            staged="$stage/${names[i]}"
            previous="$stage/previous/${names[i]}"
            dest=${destinations[i]}
            if [[ ! -e "$staged" ]]; then
                mv -h "$dest" "$staged" || { restored=0; continue; }
            fi
            if [[ -e "$previous" ]]; then
                mv -h "$previous" "$dest" || restored=0
            fi
        done
    fi
    (( restored )) || fail "could not restore an earlier install; check $stage for backups"
    rm -rf "$stage" || exit 1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Prepare both copies before changing either installed version. Sign here so the seal covers
# the complete bundle, including the VST3 SDK's generated moduleinfo.json.
mkdir "$stage/previous"
for i in 0 1; do
    source=${sources[i]}
    [[ "$source" == /* ]] || source="./$source"
    staged="$stage/${names[i]}"
    ditto "$source" "$staged"
    codesign --force --sign - --timestamp=none "$staged"
done
replacing=1
for i in 0 1; do
    dest=${destinations[i]}
    if [[ -d "$dest" ]]; then
        mv -h "$dest" "$stage/previous/${names[i]}"
    fi
    mv -h "$stage/${names[i]}" "$dest"
done
replacing=0
printf 'Installed %s\n' "${destinations[@]}"
