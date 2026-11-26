/* null_au.cpp -- offline render through the installed AU (AudioComponent API) against the direct core (#22).
 *
 * Finds the component by type / subtype / manufacturer (map_identity.h, via CMake), so it tests the bundle that
 * the system registered (install it to ~/Library/Audio/Plug-Ins/Components first). Then:
 *   1. null: parameters set before AudioUnitInitialize, render in varying blocks, compare to the reference
 *   2. live: switch the quality and the bias, then the valve, while rendering; the latency property must follow
 *   3. saved state (#114): ClassInfo round-trips every value of both paths; a ClassInfo without our key (pre-#114)
 *      loads on the 12AX7 path with the 6AS6 values at their defaults; NaN / out-of-range values in a preset are
 *      sanitized
 * Exit status 0 only if the null is exact (max abs diff 0) and the live and state checks pass.
 *
 *   map_null_au [--fs 48000] [--quality 1] [--drive 2] [--bias 1.25] [--output 1.25] [--seconds 2]
 *               [--valve 1] [--bias6 0.3] [--mode6 0] [--filter6 0]
 */
#include "null_common.h"

#include <AudioToolbox/AudioToolbox.h>

#include <chrono>
#include <thread>

using namespace nulltest;

namespace {

OSType fourcc(const char* s) {
    return OSType((std::uint32_t(std::uint8_t(s[0])) << 24) | (std::uint32_t(std::uint8_t(s[1])) << 16) |
                  (std::uint32_t(std::uint8_t(s[2])) << 8) | std::uint32_t(std::uint8_t(s[3])));
}

struct Input {
    const std::vector<float>* ch[2];
    std::size_t pos = 0;
};

OSStatus input_proc(void* ref, AudioUnitRenderActionFlags*, const AudioTimeStamp*, UInt32, UInt32 frames,
                    AudioBufferList* io) {
    auto* in = static_cast<Input*>(ref);
    for (UInt32 c = 0; c < io->mNumberBuffers && c < 2; ++c) {
        const std::vector<float>& src = *in->ch[c];
        if (!io->mBuffers[c].mData) return kAudio_ParamError;
        auto* dst = static_cast<float*>(io->mBuffers[c].mData);
        for (UInt32 i = 0; i < frames; ++i) {
            const std::size_t k = in->pos + i;
            dst[i] = k < src.size() ? src[k] : 0.0f;
        }
    }
    return noErr;
}

#define CHECK(x)                                                                                    \
    do {                                                                                            \
        const OSStatus st_ = (x);                                                                   \
        if (st_ != noErr) { std::fprintf(stderr, "FAIL %s: %d\n", #x, int(st_)); return 1; }        \
    } while (0)

struct Abl2 {                                           // an AudioBufferList with room for 2 buffers
    UInt32 mNumberBuffers;
    AudioBuffer mBuffers[2];
};

double latency_property(AudioUnit au) {
    Float64 lat = -1.0;
    UInt32 size = sizeof lat;
    AudioUnitGetProperty(au, kAudioUnitProperty_Latency, kAudioUnitScope_Global, 0, &lat, &size);
    return lat;
}

int render(AudioUnit au, Input& in, std::vector<float>& oL, std::vector<float>& oR, std::size_t n,
           double sample_time0, bool realtime_pace) {
    std::size_t pos = 0, k = 0;
    while (pos < n) {
        UInt32 frames = UInt32(block_pattern()[k++ % block_pattern().size()]);
        if (pos + frames > n) frames = UInt32(n - pos);
        Abl2 abl{};
        abl.mNumberBuffers = 2;
        for (int c = 0; c < 2; ++c) {
            abl.mBuffers[c].mNumberChannels = 1;
            abl.mBuffers[c].mDataByteSize = frames * sizeof(float);
            abl.mBuffers[c].mData = (c == 0 ? oL.data() : oR.data()) + pos;
        }
        AudioTimeStamp ts{};
        ts.mFlags = kAudioTimeStampSampleTimeValid;
        ts.mSampleTime = sample_time0 + double(pos);
        in.pos = pos;
        AudioUnitRenderActionFlags flags = 0;
        CHECK(AudioUnitRender(au, &flags, &ts, 0, frames, reinterpret_cast<AudioBufferList*>(&abl)));
        pos += frames;
        if (realtime_pace) std::this_thread::sleep_for(std::chrono::microseconds(500));
    }
    return 0;
}

float get_param(AudioUnit au, AudioUnitParameterID id) {
    AudioUnitParameterValue v = -1.0f;
    AudioUnitGetParameter(au, id, kAudioUnitScope_Global, 0, &v);
    return v;
}

// ClassInfo with one parameter's value replaced (the AUBase data blob: scope, element, count, then id / value pairs,
// big endian) and our key removed or kept
CFPropertyListRef edit_state(CFPropertyListRef src, bool drop_key, AudioUnitParameterID id, float value) {
    CFMutableDictionaryRef d = CFDictionaryCreateMutableCopy(kCFAllocatorDefault, 0, static_cast<CFDictionaryRef>(src));
    if (drop_key) {
        CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, mapvs::kAUStateKey, kCFStringEncodingUTF8);
        CFDictionaryRemoveValue(d, key);
        CFRelease(key);
    }
    auto* data = static_cast<CFDataRef>(CFDictionaryGetValue(d, CFSTR(kAUPresetDataKey)));
    if (data) {
        CFMutableDataRef m = CFDataCreateMutableCopy(kCFAllocatorDefault, 0, data);
        UInt8* p = CFDataGetMutableBytePtr(m);
        const CFIndex n = CFDataGetLength(m);
        auto rd = [&](CFIndex at) { std::uint32_t x; std::memcpy(&x, p + at, 4); return CFSwapInt32BigToHost(x); };
        CFIndex at = 0;
        while (at + 12 <= n) {
            const std::uint32_t scope = rd(at), count = rd(at + 8);
            at += 12;
            for (std::uint32_t k = 0; k < count && at + 8 <= n; ++k, at += 8) {
                if (scope == kAudioUnitScope_Global && rd(at) == id) {
                    std::uint32_t bits;
                    std::memcpy(&bits, &value, 4);
                    bits = CFSwapInt32HostToBig(bits);
                    std::memcpy(p + at + 4, &bits, 4);
                }
            }
        }
        CFDictionarySetValue(d, CFSTR(kAUPresetDataKey), m);
        CFRelease(m);
    }
    return d;
}

}  // namespace

int main(int argc, char** argv) {
    Settings s;
    parse_args(argc, argv, s);
    // the AU's parameters are float: the reference uses the values the AU actually receives
    s.drive = double(float(s.drive));
    s.bias = double(float(s.bias));
    s.output = double(float(s.output));
    s.bias6 = double(float(s.bias6));

    AudioComponentDescription desc{fourcc(MAP_TEST_AU_TYPE), fourcc(MAP_TEST_AU_SUBTYPE),
                                   fourcc(MAP_TEST_AU_MANUFACTURER), 0, 0};
    AudioComponent comp = AudioComponentFindNext(nullptr, &desc);
    if (!comp) { std::fprintf(stderr, "FAIL: component %s %s %s not registered\n", MAP_TEST_AU_TYPE,
                              MAP_TEST_AU_SUBTYPE, MAP_TEST_AU_MANUFACTURER); return 1; }
    CFStringRef cfname = nullptr;
    AudioComponentCopyName(comp, &cfname);
    char name[256] = "?";
    if (cfname) { CFStringGetCString(cfname, name, sizeof name, kCFStringEncodingUTF8); CFRelease(cfname); }
    AudioUnit au = nullptr;
    CHECK(AudioComponentInstanceNew(comp, &au));

    AudioStreamBasicDescription fmt{};
    fmt.mSampleRate = s.fs;
    fmt.mFormatID = kAudioFormatLinearPCM;
    fmt.mFormatFlags = kAudioFormatFlagsNativeFloatPacked | kAudioFormatFlagIsNonInterleaved;
    fmt.mBytesPerPacket = 4;
    fmt.mFramesPerPacket = 1;
    fmt.mBytesPerFrame = 4;
    fmt.mChannelsPerFrame = 2;
    fmt.mBitsPerChannel = 32;
    CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Input, 0, &fmt, sizeof fmt));
    CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_StreamFormat, kAudioUnitScope_Output, 0, &fmt, sizeof fmt));
    UInt32 maxf = 4096;
    CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxf,
                               sizeof maxf));
    std::vector<float> L, R;
    make_signal(s, L, R);
    Input in{{&L, &R}, 0};
    AURenderCallbackStruct cb{input_proc, &in};
    CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof cb));
    CHECK(AudioUnitSetParameter(au, mapvs::kDrive, kAudioUnitScope_Global, 0, float(s.drive), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kBias, kAudioUnitScope_Global, 0, float(s.bias), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kOutput, kAudioUnitScope_Global, 0, float(s.output), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kQuality, kAudioUnitScope_Global, 0, float(s.quality), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, float(s.valve), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kBias6, kAudioUnitScope_Global, 0, float(s.bias6), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kMode6, kAudioUnitScope_Global, 0, float(s.mode6), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kFilter6, kAudioUnitScope_Global, 0, float(s.filter6), 0));
    CHECK(AudioUnitInitialize(au));

    // 1. null
    std::vector<float> pL(L.size()), pR(R.size()), rL, rR;
    if (render(au, in, pL, pR, L.size(), 0.0, false)) return 1;
    int ref_latency = 0;
    if (!reference(s, L, R, rL, rR, ref_latency)) { std::fprintf(stderr, "FAIL: reference prepare\n"); return 1; }
    const Diff dl = compare(pL, rL), dr = compare(pR, rR);
    const double lat = latency_property(au);
    std::printf("AU \"%s\" (%s %s %s)\n", name, MAP_TEST_AU_TYPE, MAP_TEST_AU_SUBTYPE, MAP_TEST_AU_MANUFACTURER);
    std::printf("settings: %s, drive %.6g, output %.6g, %zu samples (%.3g s), stereo, float32, blocks cycling "
                "1..4096\n", describe(s).c_str(), s.drive, s.output, L.size(), s.seconds);
    std::printf("latency: AU property %.9g s = %.3f samples; core %d samples\n", lat, lat * s.fs, ref_latency);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              std::lround(lat * s.fs) == ref_latency;

    // 2. live quality and bias change, then the valve, while rendering (realtime-paced so the engine's worker runs)
    const int target_q = s.quality == mapvs::kHQ ? mapvs::kEco : mapvs::kHQ;
    const int other_valve = s.valve == mapvs::k6AS6 ? mapvs::k12AX7 : mapvs::k6AS6;
    const std::size_t nlive = std::size_t(0.25 * s.fs);
    std::vector<float> qL(nlive), qR(nlive);
    double t0 = double(L.size());
    for (int step = 0; step < 2; ++step) {
        if (step == 0) {
            CHECK(AudioUnitSetParameter(au, mapvs::kQuality, kAudioUnitScope_Global, 0, float(target_q), 0));
            CHECK(AudioUnitSetParameter(au, mapvs::kBias, kAudioUnitScope_Global, 0, 1.5f, 0));
        } else {
            CHECK(AudioUnitSetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, float(other_valve), 0));
        }
        if (render(au, in, qL, qR, nlive, t0, true)) return 1;
        t0 += double(nlive);
        const int valve = step == 0 ? s.valve : other_valve;
        // the latency property follows once the switch is in effect (the worker's notification): settle briefly
        double lat2 = latency_property(au);
        const int want = mapvs::Engine::latency_for(valve, target_q, s.fs);
        for (int k = 0; k < 20 && std::lround(lat2 * s.fs) != want; ++k) {
            if (render(au, in, qL, qR, std::size_t(0.02 * s.fs), t0, true)) return 1;
            t0 += 0.02 * s.fs;
            lat2 = latency_property(au);
        }
        std::size_t bad = 0;
        for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
        std::printf("live: quality -> %s, bias -> 1.5%s%s: latency property %.3f samples (want %d), nonfinite %zu\n",
                    quality_name(target_q), step ? ", valve -> " : "", step ? valve_name(valve) : "", lat2 * s.fs, want,
                    bad);
        ok = ok && std::lround(lat2 * s.fs) == want && bad == 0;
    }

    // 3. saved state (#114)
    {
        CFPropertyListRef saved = nullptr;
        UInt32 size = sizeof saved;
        CHECK(AudioUnitGetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &saved, &size));
        float values[mapvs::kNumParams];
        for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id) values[id] = get_param(au, id);
        bool has_key = false;
        {
            CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, mapvs::kAUStateKey, kCFStringEncodingUTF8);
            has_key = CFDictionaryContainsKey(static_cast<CFDictionaryRef>(saved), key);
            CFRelease(key);
        }
        // round trip, after moving every parameter away
        for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id)
            CHECK(AudioUnitSetParameter(au, id, kAudioUnitScope_Global, 0, float(mapvs::sanitize_plain(id, 1e9)), 0));
        CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &saved, sizeof saved));
        bool same = has_key && int(std::lround(values[mapvs::kValve])) == other_valve;
        for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id) same = same && get_param(au, id) == values[id];
        std::printf("ClassInfo round trip after the live path switch (our key present, every value of both paths): %s\n",
                    same ? "yes" : "NO");
        ok = ok && same;

        // pre-#114: no key, and no 6AS6 values (they were never saved); set them away from the defaults first
        CHECK(AudioUnitSetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, float(mapvs::k6AS6), 0));
        CHECK(AudioUnitSetParameter(au, mapvs::kBias6, kAudioUnitScope_Global, 0, 0.2f, 0));
        CHECK(AudioUnitSetParameter(au, mapvs::kMode6, kAudioUnitScope_Global, 0, float(mapvs::kT), 0));
        CFPropertyListRef legacy = edit_state(saved, true, 0xffffffffu, 0.0f);
        // a pre-#114 blob has only IDs 0..4: rename the new IDs to IDs no build defines, which AUBase ignores
        {
            CFMutableDictionaryRef d = const_cast<CFMutableDictionaryRef>(static_cast<CFDictionaryRef>(legacy));
            auto* data = static_cast<CFDataRef>(CFDictionaryGetValue(d, CFSTR(kAUPresetDataKey)));
            CFMutableDataRef m = CFDataCreateMutableCopy(kCFAllocatorDefault, 0, data);
            UInt8* p = CFDataGetMutableBytePtr(m);
            const CFIndex n = CFDataGetLength(m);
            CFIndex at = 0;
            while (at + 12 <= n) {
                std::uint32_t cnt;
                std::memcpy(&cnt, p + at + 8, 4);
                cnt = CFSwapInt32BigToHost(cnt);
                at += 12;
                for (std::uint32_t k = 0; k < cnt && at + 8 <= n; ++k, at += 8) {
                    std::uint32_t id;
                    std::memcpy(&id, p + at, 4);
                    id = CFSwapInt32BigToHost(id);
                    if (id >= std::uint32_t(mapvs::kNumParamsV1)) {
                        const std::uint32_t none = CFSwapInt32HostToBig(1000u + id);
                        std::memcpy(p + at, &none, 4);
                    }
                }
            }
            CFDictionarySetValue(d, CFSTR(kAUPresetDataKey), m);
            CFRelease(m);
        }
        CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &legacy, sizeof legacy));
        bool old = int(std::lround(get_param(au, mapvs::kValve))) == mapvs::k12AX7 &&
                   get_param(au, mapvs::kBias6) == float(mapvs::kBias6Range.def) &&
                   get_param(au, mapvs::kMode6) == float(mapvs::kMode6Default) &&
                   get_param(au, mapvs::kFilter6) == float(mapvs::kFilter6Default);
        for (AudioUnitParameterID id = 0; id < AudioUnitParameterID(mapvs::kNumParamsV1); ++id)
            old = old && get_param(au, id) == values[id];
        std::printf("pre-#114 ClassInfo (no key, IDs 0-4 only): the 12AX7 path, its 5 values, the 6AS6 values at their "
                    "defaults: %s\n", old ? "yes" : "NO");
        ok = ok && old;
        CFRelease(legacy);

        // NaN and out-of-range values in a preset
        CFPropertyListRef bad1 = edit_state(saved, false, mapvs::kDrive, NAN);
        CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &bad1, sizeof bad1));
        const float drive_nan = get_param(au, mapvs::kDrive);
        CFRelease(bad1);
        CFPropertyListRef bad2 = edit_state(saved, false, mapvs::kValve, 9.0f);
        CFPropertyListRef bad3 = edit_state(bad2, false, mapvs::kBias6, -4.0f);
        CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &bad3, sizeof bad3));
        const float valve_hi = get_param(au, mapvs::kValve), bias6_lo = get_param(au, mapvs::kBias6);
        CFRelease(bad2);
        CFRelease(bad3);
        const bool clean = drive_nan == float(mapvs::kDriveRange.def) && valve_hi == float(mapvs::kNumValves - 1) &&
                           bias6_lo == float(mapvs::kBias6Range.min);
        std::printf("preset with NaN Drive, Valve 9, 6AS6 Bias -4: restored as %g, %g, %g (default, last, min): %s\n",
                    double(drive_nan), double(valve_hi), double(bias6_lo), clean ? "yes" : "NO");
        ok = ok && clean;
        std::vector<float> zL(std::size_t(0.05 * s.fs)), zR(zL.size());
        if (render(au, in, zL, zR, zL.size(), t0, true)) return 1;
        std::size_t bad = 0;
        for (std::size_t i = 0; i < zL.size(); ++i) bad += !std::isfinite(zL[i]) || !std::isfinite(zR[i]);
        ok = ok && bad == 0;
        CFRelease(saved);
    }

    AudioUnitUninitialize(au);
    AudioComponentInstanceDispose(au);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
