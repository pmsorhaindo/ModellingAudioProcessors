/* map_params.h -- parameters shared by the AU and VST 3 wrappers, and the provisional volts mapping (#22).
 *
 * v1 scope (#75 option A): Drive, Bias, Output, Quality, plus a wrapper-level Bypass. Nothing else.
 */
#ifndef MAP_PARAMS_H
#define MAP_PARAMS_H

#include <cstdint>

namespace mapvs {

/* ---- host full scale <-> stage volts: PROVISIONAL ---------------------------------------------------------------
 * The DK stage works in volts; the mapping to digital full scale is still undefined (#22, 14 Nov 2026). This is
 * the one place where it is set. Change these two constants only, here.
 *   input:  host sample 1.0 (0 dBFS) at Drive 1 = 1.0 V at the stage input. This is the convention render.py uses
 *           for a single render ("ref fs": digital full scale maps to the chosen peak voltage).
 *   output: 1/64 of host full scale per volt at the stage output. Chosen so that, at Drive = Bias = Output = 1,
 *           small signals come out at roughly the level they went in: the stage's small-signal gain measured
 *           through the C++ core for this PR (1 kHz, 0.05 V peak, standard, 48 kHz) is 64.5x (36.2 dB).
 * Both are engineering placeholders, not a calibration to any hardware. */
inline constexpr double kVoltsPerFullScale = 1.0;
inline constexpr double kFullScalePerVolt = 1.0 / 64.0;

enum ParamID : std::uint32_t { kDrive = 0, kBias = 1, kOutput = 2, kQuality = 3, kBypass = 4, kNumParams = 5 };

struct Range { double min, max, def; };
/* Drive: linear gain on the input (x). 8x at 0 dBFS is 8 V, the "driven" demo level (demo/README.md).
 * Bias: Rk scale (1.0 = 1.5 kOhm, dk/stage.py). The core's vectors cover 1.0 and 1.5; 0.5 and 2.0 build a pack and
 * solve the DC point (checked by the engine test), but are not otherwise validated.
 * Output: linear gain after the stage (x). */
inline constexpr Range kDriveRange{0.0, 8.0, 1.0};
inline constexpr Range kBiasRange{0.5, 2.0, 1.0};
inline constexpr Range kOutputRange{0.0, 4.0, 1.0};

enum Quality : int { kEco = 0, kStandard = 1, kHQ = 2, kNumQualities = 3 };
inline constexpr int kQualityDefault = kStandard;
inline constexpr const char* kQualityNames[kNumQualities] = {"Eco (4x)", "Standard (8x)", "HQ (16x)"};

/* Reported tail beyond the latency: 0.2 s, about 6 time constants of Ck * Rk = 22 uF * 1.5 kOhm = 33 ms (the
 * slowest RC of the circuit values, dk/circuit.py). A provisional choice, not a measured decay. */
inline constexpr double kTailSeconds = 0.2;

inline double clamp01(double n) { return n < 0.0 ? 0.0 : (n > 1.0 ? 1.0 : n); }
inline double to_plain(const Range& r, double norm) { return r.min + clamp01(norm) * (r.max - r.min); }
inline double to_norm(const Range& r, double plain) { return clamp01((plain - r.min) / (r.max - r.min)); }
inline int clamp_quality(int q) { return q < 0 ? 0 : (q >= kNumQualities ? kNumQualities - 1 : q); }
inline int quality_from_norm(double norm) { return clamp_quality(int(clamp01(norm) * (kNumQualities - 1) + 0.5)); }
inline double quality_to_norm(int q) { return double(clamp_quality(q)) / double(kNumQualities - 1); }

/* VST 3 state layout (processor getState / setState, controller setComponentState): int32 kStateVersion, then the
 * kNumParams normalized values as doubles, in ParamID order, little endian (IBStreamer). */
inline constexpr std::int32_t kStateVersion = 1;

}  // namespace mapvs
#endif
