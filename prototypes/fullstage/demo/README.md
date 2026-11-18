# Musical renders of the 12AX7 stage

This demo renders musical excerpts through the Python DK model of a **12AX7 common-cathode stage**, inspired by the Culture Vulture. The circuit values are example values, **not Culture Vulture values** ([fullstage README](../README.md)). There are no reference recordings of the original hardware here; these renders are examples of the model and do not establish how closely it sounds like the unit.

## Input levels

The model takes **volts**, not normalized audio. In the batch manifest, each excerpt's largest dry sample is mapped to the selected peak input voltage. Every channel is scaled by the same factor, preserving the stereo balance. This makes the levels comparable across source recordings, whose file peaks vary. A single render defaults to mapping digital full scale (1.0) to the chosen voltage; `--ref peak` uses the batch convention.

| Level | Peak input | Reason |
|---|---:|---|
| clean | 0.5 V | A low-drive reference with measured settled 1 kHz THD of 2.725% in [spice/RESULTS.md](../spice/RESULTS.md) (Koren 12AX7). The DK probe shows only 0.05 dB gain compression at this level. |
| edge | 2 V | Around the onset of strong grid-current and cutoff behavior: the same reference measures 13.73% THD, and the DK probe shows 3.66 µA peak grid current above leakage and a 0.0049 mA minimum plate current. The [DK results](../dk/RESULTS.md) also validate this drive in the aliasing sweep. |
| driven | 8 V | A deliberate high-drive comparison: the reference measures 57.74% THD, and the [DK results](../dk/RESULTS.md) validate the quality settings' aliasing behavior at 8 V. The DK probe shows 14.13 dB gain compression relative to its 0.05 V baseline. |

Those percentages are **settled 1 kHz sine measurements**, not predicted distortion percentages for the music. The model's own Dempwolf–Zölzer valve equations differ from the Koren reference, so the probe figures are a more direct description of its behavior. In that probe, grid current above leakage is 0.023 µA at 1 V, 1.257 µA at 1.5 V, and 3.661 µA at 2 V; minimum plate current is almost zero at 2 V and reaches zero around 2.5 V. Run `python demo/render.py probe --jobs 4` from `prototypes/fullstage` to reproduce them. The 2 V and 8 V values stay within the repo's measured operating and aliasing range; 8 V is intentionally extreme rather than a typical line level.

## Excerpts and outputs

The five excerpt times, credits, licences, and source links are in [CREDITS.md](CREDITS.md) and [manifest.json](manifest.json). The 9.49 s vocal source cannot supply a 10 s excerpt, so 0–9.4 s is used. The other selections are 12 s active passages; the bass file's quiet ending and the full mix's quiet tail are excluded.

For each excerpt, batch mode writes dry, standard clean/edge/driven, eco driven, and hq driven. All processed batch renders use the FIR resampler at 48 kHz. Stereo channels get separate `Stage` instances. The stage's reported latency is compensated in samples. Output is 24-bit WAV with a 320 kbps MP3 copy. A `--plot vocal` batch also writes dry-versus-driven spectrum and sustained-vocal harmonic PNGs; the note interval is 0.5–2.5 s of the vocal excerpt. Plotting another excerpt writes its spectrum and writes harmonics only if that excerpt has a `note` interval in the manifest.

`$LAB_DIR` below is a placeholder for your own working directory (a virtualenv plus the demo source and output folders).

```bash
source "$LAB_DIR/.venv/bin/activate"
pip install pyloudnorm soundfile    # only if missing
cd prototypes/fullstage
python demo/render.py batch \
  --src "$LAB_DIR/demo/src" \
  --out "$LAB_DIR/demo/out" \
  --plot vocal --jobs 8

# One file; --mono sums channels, while the default keeps stereo.
python demo/render.py render input.wav -o /tmp/stage.wav \
  --start 5 --dur 12 --level edge --ref peak --quality standard --filt fir
```

`render.py` loads WAV with soundfile and MP3 with ffmpeg, trims at the source sample rate, resamples to 48 kHz if needed, and applies short boundary fades. It scales the excerpt to volts, processes each channel, removes DC, then applies one gain to match the **dry excerpt's integrated LUFS** using pyloudnorm's ITU-R BS.1770 gated measurement. This controls the large perceived-volume differences that would obscure a level-matched comparison. A four-times-oversampled peak estimate is then checked against −1 dBTP; if necessary, gain is **reduced only**. The peak ceiling can therefore prevent an exact loudness match, which is shown by `limited` and `limit_db` in `metrics.csv`/`metrics.json`. Gain reduction does not clip or change the waveform shape. The dry target is measured after the same peak safety rule, so it represents the written dry file.

Metrics include sample peak dBFS, estimated true peak dBTP, RMS dBFS, integrated LUFS, applied gain, reported latency samples, and processor runtime. The WAVs are the comparison masters; MP3 encoding can change peaks and loudness slightly. For the IIR option, `Stage.latency_samples` is a nominal integer delay; the default FIR has an exact integer sample latency ([DK results](../dk/RESULTS.md)).
