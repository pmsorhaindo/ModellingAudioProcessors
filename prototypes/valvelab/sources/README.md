# Third-party SPICE model sources

These files are **not redistributed** here: their headers carry no licence or redistribution grant
(Koren's header says only "All the usual legal disclaimers apply"). The code does not read them at
runtime; `valvelab/params.py` copies the parameters with citations. Fetch them to verify.

| local file | original source | SHA256 |
|---|---|---|
| `koren_Tube.lib` | `Tube.lib` inside https://www.normankoren.com/Audio/Tubemods.zip (Norman Koren) | `581821af37b2669ea340b93116de46c9c4edf4c713990eea270b197c62885844` |
| `ltwiki_Tubes_Koren.lib` | https://ltwiki.org/files/LTspiceIV/lib/sub/Tubes_Koren.lib (LTspice wiki, Koren models, community-modified) | `510f4c10247ba46358ff5a6b3f15189bec5cf207507ccc70922184385a66c360` |
| `ltwiki_nk_pentode.sub` | https://ltwiki.org/files/LTspiceIV/lib/sub/nk_pentode.sub (LTspice wiki, Koren pentode models) | `30dab0642311a44aecf57b1808913f74d9a62c35429bee059f900d69dd009932` |

All three URLs and hashes verified on 3 Nov 2026.

## Fetch

```bash
cd prototypes/valvelab/sources
curl -sLO https://www.normankoren.com/Audio/Tubemods.zip && unzip -p Tubemods.zip Tube.lib > koren_Tube.lib && rm Tubemods.zip
curl -sL -o ltwiki_Tubes_Koren.lib https://ltwiki.org/files/LTspiceIV/lib/sub/Tubes_Koren.lib
curl -sL -o ltwiki_nk_pentode.sub https://ltwiki.org/files/LTspiceIV/lib/sub/nk_pentode.sub
sha256sum -c SHA256SUMS
```
