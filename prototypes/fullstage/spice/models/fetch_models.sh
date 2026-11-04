#!/usr/bin/env bash
# Fetch third-party valve models (not redistributed in this repo; no licence grant in their headers).
set -euo pipefail
cd "$(dirname "$0")"
curl -sLO https://www.normankoren.com/Audio/Tubemods.zip
unzip -p Tubemods.zip Tube.lib | tr -d '\r' > koren_Tube.lib && rm Tubemods.zip
# The "12AX7 NEW MODEL" subcircuit, verbatim (CR stripped):
awk '/^\.SUBCKT 12AX7 /{f=1} f{print} f&&/^\.ENDS/{exit}' koren_Tube.lib > 12ax7_koren.lib
sha256sum -c SHA256SUMS
