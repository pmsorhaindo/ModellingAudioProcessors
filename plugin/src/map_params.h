/* map_params.h -- parameters shared by the AU and VST 3 wrappers, the provisional volts mapping (#22) and the saved
 * state layout.
 *
 * Two valve paths in one plugin (#114): the 6AS6 DK stage (Gyre's v1 distortion stage, dk6as6_core.h) and the 12AX7 DK
 * stage (#75 option A, dk_core.h), chosen by the Valve parameter. The design (shared vs per-path state, parameter map,
 * saved state when the path changes) is in plugin/README.md, "Valve paths".
 *
 * Parameter IDs are what hosts store in sessions and automation: the first five are the pre-#114 IDs, unchanged, and
 * the new ones are appended. Never renumber them.
 */
#ifndef MAP_PARAMS_H
#define MAP_PARAMS_H

#include <cmath>
#include <cstdint>

namespace mapvs {

/* ---- host full scale <-> stage volts: PROVISIONAL ---------------------------------------------------------------
 * The DK stages work in volts; the mapping to digital full scale is still undefined (#22, 14 Nov 2026). This is
 * the one place where it is set, and both valve paths use it unchanged (#114 does not invent a new one).
 *   input:  host sample 1.0 (0 dBFS) at Drive 1 = 1.0 V at the stage input. This is the convention render.py uses
 *           for a single render ("ref fs": digital full scale maps to the chosen peak voltage).
 *   output: 1/64 of host full scale per volt at the stage output. Chosen so that, at Drive = Bias = Output = 1,
 *           small signals through the 12AX7 stage come out at roughly the level they went in: that stage's
 *           small-signal gain measured through the C++ core for this PR (1 kHz, 0.05 V peak, standard, 48 kHz) is
 *           64.5x (36.2 dB). The 6AS6 stage has a different gain, so the two paths differ in level (README).
 * Both are engineering placeholders, not a calibration to any hardware. */
inline constexpr double kVoltsPerFullScale = 1.0;
inline constexpr double kFullScalePerVolt = 1.0 / 64.0;

enum ParamID : std::uint32_t {
    kDrive = 0,      // shared
    kBias = 1,       // 12AX7 path: Rk scale
    kOutput = 2,     // shared
    kQuality = 3,    // shared
    kBypass = 4,     // shared
    kValve = 5,      // #114: the path
    kBias6 = 6,      // 6AS6 path: target DC cathode current, mA (#110)
    kMode6 = 7,      // 6AS6 path: P1 / T / P2 (experimental) (#111)
    kFilter6 = 8,    // 6AS6 path: post-distortion low-pass Off / 4k / 7k (#103)
    kNumParams = 9
};
inline constexpr int kNumParamsV1 = 5;               // the pre-#114 parameter set (Drive .. Bypass)

struct Range { double min, max, def; };
/* Drive: linear gain on the input (x). 8x at 0 dBFS is 8 V, the "driven" demo level (demo/README.md).
 * Bias (12AX7): Rk scale (1.0 = 1.5 kOhm, dk/stage.py). The core's vectors cover 1.0 and 1.5; 0.5 and 2.0 build a
 * pack and solve the DC point (checked by the engine test), but are not otherwise validated.
 * Output: linear gain after the stage (x).
 * Bias (6AS6): target DC cathode current in mA, dk6as6_core.h BIAS_RANGE_MA (0.1-0.6 mA, #110) and BIAS_MA (0.45);
 * the engine checks these equal the core's. Mode T caps it at kTBiasCapMa (below). */
inline constexpr Range kDriveRange{0.0, 8.0, 1.0};
inline constexpr Range kBiasRange{0.5, 2.0, 1.0};
inline constexpr Range kOutputRange{0.0, 4.0, 1.0};
inline constexpr Range kBias6Range{0.1, 0.6, 0.45};
/* T mode's supported bias ends at 0.5 mA with the default grid current (dk6as6_core.h envelope(), PR #128), and
 * Michael's v0.1 slice (#114, 22 Nov 2026) caps it there: in T the plugin never builds a bias above this. */
inline constexpr double kTBiasCapMa = 0.5;

enum Quality : int { kEco = 0, kStandard = 1, kHQ = 2, kNumQualities = 3 };
/* The shipping default, with kValveDefault below: 6AS6 at Standard (8x). Michael's decision on #143 (27 Nov 2026),
 * knowingly trading the #113 inaudibility criterion for CPU. At the default setting Standard misses it at 8 V by
 * 18.7 / 12.7 dB (48 / 44.1 kHz). HQ meets it but its stereo does not fit one M1 core (113-123 % estimated, against
 * 58-70 % for Standard; measured on a loaded Mac). Record: prototypes/fullstage/cpp/m1_timing/README.md. */
inline constexpr int kQualityDefault = kStandard;
// The 6AS6 stage has no eco (dk6as6_core.h: "eco is not a 6AS6 setting"): on that path Eco runs Standard 8x.
inline constexpr const char* kQualityNames[kNumQualities] = {"Eco (4x; 6AS6 8x)", "Standard (8x)", "HQ (16x)"};

/* Valve: list order is the parameter's value order (index 0 = 6AS6). A new instance starts on the 6AS6 path, Gyre's
 * v1 stage. A saved state from before #114 has no Valve value and was the 12AX7 plugin, so it loads on the 12AX7
 * path (kValveLegacy). */
enum Valve : int { k6AS6 = 0, k12AX7 = 1, kNumValves = 2 };
inline constexpr int kValveDefault = k6AS6;
inline constexpr int kValveLegacy = k12AX7;
inline constexpr const char* kValveNames[kNumValves] = {"6AS6 DK", "12AX7 DK"};

// 6AS6 mode, dk6as6::Mode order. P2 is OUR hypothesis and never supported (README-6as6.md): experimental only.
enum Mode6 : int { kP1 = 0, kT = 1, kP2 = 2, kNumModes6 = 3 };
inline constexpr int kMode6Default = kP1;
inline constexpr const char* kMode6Names[kNumModes6] = {"P1", "T", "P2 (experimental)"};

// 6AS6 post-filter, dk6as6::Lpf order (off, 4k, 7k); the core's default is off
enum Filter6 : int { kFilterOff = 0, kFilter4k = 1, kFilter7k = 2, kNumFilters6 = 3 };
inline constexpr int kFilter6Default = kFilterOff;
inline constexpr const char* kFilter6Names[kNumFilters6] = {"Off", "4k", "7k"};

/* Reported tail beyond the latency, per path; provisional choices, not measured decays.
 *   12AX7: 0.2 s, about 6 time constants of Ck * Rk = 22 uF * 1.5 kOhm = 33 ms (the slowest RC of dk/circuit.py).
 *   6AS6: 0.8 s, about 6 time constants of its slowest coupling RC, Cout * (Ra + Rl) = 100 nF * 1.27 MOhm = 0.127 s
 *         (dk6as6/circuit.py VALUES, all inferred; Cin * (Rs + Rg) is 0.1 s). */
inline constexpr double kTailSeconds12AX7 = 0.2;
inline constexpr double kTailSeconds6AS6 = 0.8;
inline constexpr double kTailSeconds = kTailSeconds12AX7;   // the pre-#114 name
inline double tail_seconds(int valve) { return valve == k12AX7 ? kTailSeconds12AX7 : kTailSeconds6AS6; }

// 0..1; NaN goes to 0, so every conversion below is defined (no NaN reaches an integer cast). Callers that must not
// act on a NaN host value (the VST 3 processor and controller) refuse it before converting.
inline double clamp01(double n) { return n > 0.0 ? (n < 1.0 ? n : 1.0) : 0.0; }
inline double to_plain(const Range& r, double norm) { return r.min + clamp01(norm) * (r.max - r.min); }
inline double to_norm(const Range& r, double plain) { return clamp01((plain - r.min) / (r.max - r.min)); }
// a plain value inside the range; NaN goes to the default (inf clamps to the nearer end)
inline double clamp_range(const Range& r, double v) {
    if (std::isnan(v)) return r.def;
    return v < r.min ? r.min : (v > r.max ? r.max : v);
}

// list parameters (Quality, Valve, Mode, Filter): index <-> normalized, the VST 3 StringListParameter convention
inline int clamp_index(int i, int count) { return i < 0 ? 0 : (i >= count ? count - 1 : i); }
inline int index_from_norm(double norm, int count) { return clamp_index(int(clamp01(norm) * (count - 1) + 0.5), count); }
inline double index_to_norm(int i, int count) { return double(clamp_index(i, count)) / double(count - 1); }
// a plain list value (the AU's float) to an index: rounded, NaN to the default
inline int index_from_plain(double v, int count, int def) {
    if (std::isnan(v)) return def;
    if (v <= 0.0) return 0;
    if (v >= double(count - 1)) return count - 1;
    return clamp_index(int(std::lround(v)), count);
}
inline int clamp_quality(int q) { return clamp_index(q, kNumQualities); }
inline int quality_from_norm(double norm) { return index_from_norm(norm, kNumQualities); }
inline double quality_to_norm(int q) { return index_to_norm(q, kNumQualities); }

/* The 6AS6 bias the plugin builds: inside kBias6Range (NaN: the default), and in T at most kTBiasCapMa. P2 is
 * experimental at any bias of the range; P1 is supported over the whole range. */
inline double bias6_effective(int mode, double ma) {
    const double b = clamp_range(kBias6Range, ma);
    return mode == kT && b > kTBiasCapMa ? kTBiasCapMa : b;
}
// 6AS6 quality: no eco there (Eco runs Standard)
inline int quality_6as6(int q) { return clamp_quality(q) == kHQ ? kHQ : kStandard; }

/* Plain (host) values: the AU's parameter values, and what the VST 3 normalized values map to. sanitize_plain() puts
 * any value, including NaN / inf and out-of-range values from a saved preset or a host, on a valid one. */
inline double default_plain(std::uint32_t id) {
    switch (id) {
    case kDrive: return kDriveRange.def;
    case kBias: return kBiasRange.def;
    case kOutput: return kOutputRange.def;
    case kQuality: return double(kQualityDefault);
    case kBypass: return 0.0;
    case kValve: return double(kValveDefault);
    case kBias6: return kBias6Range.def;
    case kMode6: return double(kMode6Default);
    case kFilter6: return double(kFilter6Default);
    default: return 0.0;
    }
}
inline double sanitize_plain(std::uint32_t id, double v) {
    switch (id) {
    case kDrive: return clamp_range(kDriveRange, v);
    case kBias: return clamp_range(kBiasRange, v);
    case kOutput: return clamp_range(kOutputRange, v);
    case kQuality: return double(index_from_plain(v, kNumQualities, kQualityDefault));
    case kBypass: return std::isnan(v) ? 0.0 : (v >= 0.5 ? 1.0 : 0.0);
    case kValve: return double(index_from_plain(v, kNumValves, kValveDefault));
    case kBias6: return clamp_range(kBias6Range, v);
    case kMode6: return double(index_from_plain(v, kNumModes6, kMode6Default));
    case kFilter6: return double(index_from_plain(v, kNumFilters6, kFilter6Default));
    default: return 0.0;
    }
}

// VST 3 normalized values
inline double default_norm(std::uint32_t id) {
    switch (id) {
    case kDrive: return to_norm(kDriveRange, kDriveRange.def);
    case kBias: return to_norm(kBiasRange, kBiasRange.def);
    case kOutput: return to_norm(kOutputRange, kOutputRange.def);
    case kQuality: return quality_to_norm(kQualityDefault);
    case kBypass: return 0.0;
    case kValve: return index_to_norm(kValveDefault, kNumValves);
    case kBias6: return to_norm(kBias6Range, kBias6Range.def);
    case kMode6: return index_to_norm(kMode6Default, kNumModes6);
    case kFilter6: return index_to_norm(kFilter6Default, kNumFilters6);
    default: return 0.0;
    }
}

/* ---- saved state -------------------------------------------------------------------------------------------------
 * VST 3 (processor getState / setState, controller setComponentState), little endian (IBStreamer):
 *   int32 kStateVersion (1), then the 5 shared / 12AX7 normalized doubles (Drive, Bias, Output, Quality, Bypass):
 *     exactly the pre-#114 layout, which a pre-#114 build reads (it checks the version, reads 5 doubles and ignores
 *     what follows), so rolling back keeps those values;
 *   then the #114 extension: int32 kStateExtMagic, int32 kStateExtVersion, and the kNumParamsExt doubles Valve, 6AS6
 *     Bias, Mode, Filter.
 * A state that ends after the 5 values, or continues with anything but the magic, is from before #114 (the 12AX7
 * plugin): it loads on the 12AX7 path with the 6AS6 values at their defaults. An extension with a later version is
 * read for the values this build knows. Refused, and nothing changes: another head version, a stream short of the
 * 5 values or of the extension's values, an extension version below 1, any NaN / inf value. A finite value outside 0..1
 * is clamped.
 * AU: AUBase's ClassInfo dictionary (parameter ID -> value), plus the key kAUStateKey = kAUStateVersion, written by
 * our SaveState (a pre-#114 build ignores both the key and the IDs it does not have). A dictionary without the key is
 * from before #114: it loads on the 12AX7 path. Parameters a preset does not carry take their defaults, and every
 * restored value goes through sanitize_plain(). */
inline constexpr std::int32_t kStateVersion = 1;
inline constexpr std::int32_t kStateExtMagic = 0x47595232;   // "GYR2"
inline constexpr std::int32_t kStateExtVersion = 1;
inline constexpr int kNumParamsExt = int(kNumParams) - kNumParamsV1;
inline constexpr std::int32_t kAUStateVersion = 2;
inline constexpr const char* kAUStateKey = "gyre-state-version";

/* Decode the values of a VST 3 state (already read from the stream): base = the 5 head values, ext = the extension's
 * kNumParamsExt values, or null if the state had no extension (pre-#114). On success every one of the kNumParams
 * normalized values is set; on failure (a NaN / inf value) norm is untouched. */
inline bool decode_state(const double* base, const double* ext, double (&norm)[kNumParams]) {
    if (!base) return false;
    for (int i = 0; i < kNumParamsV1; ++i)
        if (!std::isfinite(base[i])) return false;
    for (int i = 0; ext && i < kNumParamsExt; ++i)
        if (!std::isfinite(ext[i])) return false;
    for (int i = 0; i < kNumParamsV1; ++i) norm[i] = clamp01(base[i]);
    for (int i = 0; i < kNumParamsExt; ++i)
        norm[kNumParamsV1 + i] = ext ? clamp01(ext[i]) : default_norm(std::uint32_t(kNumParamsV1 + i));
    if (!ext) norm[kValve] = index_to_norm(kValveLegacy, kNumValves);
    return true;
}

}  // namespace mapvs
#endif
