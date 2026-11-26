/* null_common.h -- shared by the null tests (#22, #114): the test signal, the direct-core references, the comparison.
 *
 * The reference is the path's core called directly with the same settings, set_drive(drive * kVoltsPerFullScale),
 * set_output(output * kFullScalePerVolt), float I/O, one call for the whole signal:
 *   12AX7  dkcore::StereoStage::prepare(fs, quality, bias)
 *   6AS6   one dk6as6::Stage per channel, Stage::prepare(fs, quality, settings) with the settings written out here from
 *          #114's rules, not taken from the engine: Eco runs STANDARD, T at most 0.5 mA, P2 experimental, the filter.
 * The plugin side goes through the host API (AU render / VST 3 process) and the wrapper's engine, in blocks of varying
 * size. Both carry the same latency, so they are compared sample by sample with no alignment.
 */
#ifndef MAP_NULL_COMMON_H
#define MAP_NULL_COMMON_H

#include "dk6as6_core.h"
#include "map_engine.h"
#include "map_params.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

namespace nulltest {

struct Settings {
    double fs = 48000.0;
    int quality = mapvs::kStandard;
    double drive = 2.0, bias = 1.25, output = 1.25;     // plain values
    double seconds = 2.0;
    int valve = mapvs::k12AX7;                          // the pre-#114 nulls' path
    double bias6 = 0.3;                                 // 6AS6 path, mA
    int mode6 = mapvs::kP1, filter6 = mapvs::kFilterOff;
};

// The 6AS6 settings #114 asks for, written out independently of the engine's mapping
inline dk6as6::Settings settings_6as6(const Settings& s) {
    dk6as6::Settings set;
    set.mode = s.mode6 == mapvs::kT ? dk6as6::Mode::T : (s.mode6 == mapvs::kP2 ? dk6as6::Mode::P2 : dk6as6::Mode::P1);
    set.bias_ma = (s.mode6 == mapvs::kT && s.bias6 > 0.5) ? 0.5 : s.bias6;   // T capped at 0.5 mA
    set.lpf = s.filter6 == mapvs::kFilter4k ? dk6as6::Lpf::k4 : (s.filter6 == mapvs::kFilter7k ? dk6as6::Lpf::k7 : dk6as6::Lpf::off);
    set.experimental = s.mode6 == mapvs::kP2;           // P2: experimental only
    return set;
}
inline dkcore::Quality quality_6as6(int q) { return q == mapvs::kHQ ? dk6as6::HQ : dk6as6::STANDARD; }   // no eco

// L: two tones with a slow swell (0 .. 0.9 FS); R: deterministic white noise (LCG), 0.5 FS peak, plus a quiet
// 50 Hz tone. Both channels differ, so a channel swap or mono collapse would not null.
inline void make_signal(const Settings& s, std::vector<float>& L, std::vector<float>& R) {
    const std::size_t n = std::size_t(std::lround(s.seconds * s.fs));
    L.assign(n, 0.0f);
    R.assign(n, 0.0f);
    std::uint32_t lcg = 0x12345678u;
    for (std::size_t i = 0; i < n; ++i) {
        const double t = double(i) / s.fs;
        const double env = 0.5 - 0.5 * std::cos(2.0 * dkcore::PI * t / s.seconds);
        L[i] = float(0.9 * env * (0.7 * std::sin(2.0 * dkcore::PI * 220.0 * t) + 0.3 * std::sin(2.0 * dkcore::PI * 3150.0 * t)));
        lcg = lcg * 1664525u + 1013904223u;
        const double u = double(lcg >> 8) / double(1u << 24) * 2.0 - 1.0;
        R[i] = float(0.45 * u + 0.05 * std::sin(2.0 * dkcore::PI * 50.0 * t));
    }
}

inline bool reference(const Settings& s, const std::vector<float>& L, const std::vector<float>& R,
                      std::vector<float>& oL, std::vector<float>& oR, int& latency) {
    if (s.valve == mapvs::k6AS6) {
        auto ref = std::make_unique<dk6as6::Stage[]>(2);
        const dk6as6::Settings set = settings_6as6(s);
        oL.assign(L.size(), 0.0f);
        oR.assign(R.size(), 0.0f);
        for (int c = 0; c < 2; ++c) {
            if (ref[c].prepare(s.fs, quality_6as6(s.quality), set) != dk6as6::Status::ok) return false;
            ref[c].set_drive(s.drive * mapvs::kVoltsPerFullScale);
            ref[c].set_output(s.output * mapvs::kFullScalePerVolt);
            ref[c].process((c == 0 ? L : R).data(), (c == 0 ? oL : oR).data(), L.size());
        }
        latency = ref[0].latency_samples();
        return true;
    }
    dkcore::StereoStage ref;
    if (ref.prepare(s.fs, mapvs::Engine::core_quality(s.quality), s.bias) != dkcore::BuildStatus::ok) return false;
    ref.set_drive(s.drive * mapvs::kVoltsPerFullScale);
    ref.set_output(s.output * mapvs::kFullScalePerVolt);
    oL.assign(L.size(), 0.0f);
    oR.assign(R.size(), 0.0f);
    ref.process(L.data(), R.data(), oL.data(), oR.data(), L.size());
    latency = ref.latency_samples();
    return true;
}

// block sizes cycled by the hosts: odd, tiny, above and below the engine's 256-sample chunk
inline const std::vector<int>& block_pattern() {
    static const std::vector<int> b{512, 1, 37, 256, 257, 1000, 64, 4096, 3, 128};
    return b;
}

struct Diff { double max_abs = 0.0; std::size_t at = 0; std::size_t nonfinite = 0; double peak_ref = 0.0; };

inline Diff compare(const std::vector<float>& a, const std::vector<float>& b) {
    Diff d;
    for (std::size_t i = 0; i < a.size() && i < b.size(); ++i) {
        if (!std::isfinite(a[i]) || !std::isfinite(b[i])) { ++d.nonfinite; continue; }
        const double e = std::fabs(double(a[i]) - double(b[i]));
        if (e > d.max_abs) { d.max_abs = e; d.at = i; }
        d.peak_ref = std::fmax(d.peak_ref, std::fabs(double(b[i])));
    }
    return d;
}

inline const char* quality_name(int q) { return mapvs::kQualityNames[mapvs::clamp_quality(q)]; }
inline const char* valve_name(int v) { return mapvs::kValveNames[mapvs::clamp_index(v, mapvs::kNumValves)]; }
inline std::string describe(const Settings& s) {
    char buf[256];
    if (s.valve == mapvs::k6AS6)
        std::snprintf(buf, sizeof buf, "%s %s, bias %.6g mA, %s, %.0f Hz, %s", valve_name(s.valve),
                      mapvs::kMode6Names[mapvs::clamp_index(s.mode6, mapvs::kNumModes6)], s.bias6,
                      mapvs::kFilter6Names[mapvs::clamp_index(s.filter6, mapvs::kNumFilters6)], s.fs, quality_name(s.quality));
    else
        std::snprintf(buf, sizeof buf, "%s bias %.6g x Rk, %.0f Hz, %s", valve_name(s.valve), s.bias, s.fs,
                      quality_name(s.quality));
    return buf;
}

inline void parse_args(int argc, char** argv, Settings& s) {
    for (int i = 1; i + 1 < argc; i += 2) {
        const std::string k = argv[i];
        const double v = std::atof(argv[i + 1]);
        if (k == "--fs") s.fs = v;
        else if (k == "--quality") s.quality = int(v);
        else if (k == "--drive") s.drive = v;
        else if (k == "--bias") s.bias = v;
        else if (k == "--output") s.output = v;
        else if (k == "--seconds") s.seconds = v;
        else if (k == "--valve") s.valve = int(v);
        else if (k == "--bias6") s.bias6 = v;
        else if (k == "--mode6") s.mode6 = int(v);
        else if (k == "--filter6") s.filter6 = int(v);
        else { std::fprintf(stderr, "unknown option %s\n", argv[i]); std::exit(2); }
    }
}

}  // namespace nulltest
#endif
