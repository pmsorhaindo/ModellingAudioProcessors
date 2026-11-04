#!/usr/bin/env bash
# Datasheet PDFs are not redistributed here; fetch them from Frank's Electron Tube Data Sheets.
set -euo pipefail
cd "$(dirname "$0")"
curl -sfL -o 084_6_6AS6.pdf https://frank.pocnet.net/sheets/084/6/6AS6.pdf   # GE 6AS6 (all digitised data)
curl -sfL -o 127_6_6AS6.pdf https://frank.pocnet.net/sheets/127/6/6AS6.pdf   # Tung-Sol 6AS6 (cross-check, not yet used)
curl -sfL -o 049_5_5725.pdf https://frank.pocnet.net/sheets/049/5/5725.pdf   # RCA 5725 (ratings only)
sha256sum -c SHA256SUMS
