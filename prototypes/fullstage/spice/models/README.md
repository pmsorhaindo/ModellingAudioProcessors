# Third-party valve models (fetched, not redistributed)

The model files carry no licence or redistribution grant, so they are not in the repo
(`*.lib` is git-ignored here). `fetch_models.sh` downloads them and checks the hashes.

| file | source | SHA256 |
|---|---|---|
| `koren_Tube.lib` | `Tube.lib` inside https://www.normankoren.com/Audio/Tubemods.zip (Norman Koren), CR characters stripped | `8150e087298ea9ba59c4d1841aa2512f5e96c91ce85aa62679c5aecedf93ccb7` |
| `12ax7_koren.lib` | the `.SUBCKT 12AX7 ... NEW MODEL` block of the above, verbatim | `d75a8776d4547a325245f3f5c6721c1b7fb790b10a04ca2029aa37ba0bb54842` |

The raw `Tube.lib` (with CRs) matches the SHA256 in `prototypes/valvelab/sources/README.md`.
