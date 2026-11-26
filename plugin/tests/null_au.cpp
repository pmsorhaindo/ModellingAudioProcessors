/* null_au.cpp -- offline render through the installed AU (AudioComponent API) against the direct core (#22).
 *
 * Finds the component by type / subtype / manufacturer (map_identity.h, via CMake), so it tests the bundle that
 * the system registered (install it to ~/Library/Audio/Plug-Ins/Components first). Then:
 *   1. null: parameters set before AudioUnitInitialize, render in varying blocks, compare to the reference
 *   2. live: switch the quality and the bias, then the valve, while rendering; the latency property stays the rate's
 *      constant (the engine pads every stage to it), the tail-time property follows the running valve once adopted
 *   2b. AudioUnitScheduleParameters: 100 Valve events (more than AUBase's own reserve of 24) and a ramp, then 1100
 *      events (more than the wrapper's reserve): no allocation, lock or listed syscall in the call or in the render
 *      after it (rt_hooks.cpp), the last value wins; and two Drive points in one render (1 at frame 0, 8 at frame 128)
 *      take effect at their offsets: the output nulls exactly against an Engine given the same two slices
 *   3. saved state (#114): ClassInfo round-trips every value of both paths; a rejected ClassInfo, and one whose decoding
 *      throws inside AUBase (a bad scope in the data), change nothing and leave render publishing parameters; a
 *      ClassInfo without our key (pre-#114) loads on the 12AX7 path with the 6AS6 values at their defaults, and a
 *      PresentPreset listener already sees the 12AX7 Valve; NaN / out-of-range values in a preset are sanitized
 * Exit status 0 only if the null is exact (max abs diff 0) and the live and state checks pass.
 *
 *   map_null_au [--fs 48000] [--quality 1] [--drive 2] [--bias 1.25] [--output 1.25] [--seconds 2]
 *               [--valve 1] [--bias6 0.3] [--mode6 0] [--filter6 0]
 */
#include "null_common.h"

#include <AudioToolbox/AudioToolbox.h>

#include <chrono>
#include <thread>

extern "C" {                                            // rt_hooks.cpp (linked dylib)
void map_rt_arm(bool on);
long map_rt_count();
void map_rt_clear();
const char* map_rt_last();
}

using namespace nulltest;

namespace {

float g_valve_at_preset = -1.0f;                        // Valve when kAudioUnitProperty_PresentPreset was announced
void on_property(void*, AudioUnit au, AudioUnitPropertyID id, AudioUnitScope, AudioUnitElement) {
    if (id == kAudioUnitProperty_PresentPreset) {
        AudioUnitParameterValue v = -1.0f;
        AudioUnitGetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, &v);
        g_valve_at_preset = v;
    }
}
double tail_property(AudioUnit au) {
    Float64 t = -1.0;
    UInt32 size = sizeof t;
    AudioUnitGetProperty(au, kAudioUnitProperty_TailTime, kAudioUnitScope_Global, 0, &t, &size);
    return t;
}


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

// one render of `frames` (<= 4096) from input position `pos` into oL / oR at `pos`
OSStatus render_once(AudioUnit au, Input& in, std::vector<float>& oL, std::vector<float>& oR, std::size_t pos,
                     UInt32 frames, double sample_time) {
    Abl2 abl{};
    abl.mNumberBuffers = 2;
    for (int c = 0; c < 2; ++c) {
        abl.mBuffers[c].mNumberChannels = 1;
        abl.mBuffers[c].mDataByteSize = frames * sizeof(float);
        abl.mBuffers[c].mData = (c == 0 ? oL.data() : oR.data()) + pos;
    }
    AudioTimeStamp ts{};
    ts.mFlags = kAudioTimeStampSampleTimeValid;
    ts.mSampleTime = sample_time;
    in.pos = pos;
    AudioUnitRenderActionFlags flags = 0;
    return AudioUnitRender(au, &flags, &ts, 0, frames, reinterpret_cast<AudioBufferList*>(&abl));
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
    CHECK(AudioUnitAddPropertyListener(au, kAudioUnitProperty_PresentPreset, on_property, nullptr));

    // 1. null
    std::vector<float> pL(L.size()), pR(R.size()), rL, rR;
    if (render(au, in, pL, pR, L.size(), 0.0, false)) return 1;
    int ref_latency = 0;
    if (!reference(s, L, R, rL, rR, ref_latency)) { std::fprintf(stderr, "FAIL: reference prepare\n"); return 1; }
    const int reported = mapvs::Engine::reported_latency_for(s.fs), pad = reported - ref_latency;
    const Diff dl = compare(pL, delayed(rL, pad)), dr = compare(pR, delayed(rR, pad));
    const double lat = latency_property(au);
    std::printf("AU \"%s\" (%s %s %s)\n", name, MAP_TEST_AU_TYPE, MAP_TEST_AU_SUBTYPE, MAP_TEST_AU_MANUFACTURER);
    std::printf("settings: %s, drive %.6g, output %.6g, %zu samples (%.3g s), stereo, float32, blocks cycling "
                "1..4096\n", describe(s).c_str(), s.drive, s.output, L.size(), s.seconds);
    std::printf("latency: AU property %.9g s = %.3f samples = the rate's constant %d = core %d + pad %d\n", lat, lat * s.fs,
                reported, ref_latency, pad);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              std::lround(lat * s.fs) == reported;

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
        // the tail time follows the running valve (0.2 s / 0.8 s) once the switch is adopted; the latency never moves
        bool constant = std::lround(latency_property(au) * s.fs) == reported;
        for (int k = 0; k < 40 && tail_property(au) != mapvs::tail_seconds(valve); ++k) {
            if (render(au, in, qL, qR, std::size_t(0.02 * s.fs), t0, true)) return 1;
            t0 += 0.02 * s.fs;
            constant = constant && std::lround(latency_property(au) * s.fs) == reported;
        }
        const bool adopted = tail_property(au) == mapvs::tail_seconds(valve);
        std::size_t bad = 0;
        for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
        std::printf("live: quality -> %s, bias -> 1.5%s%s: latency property %.3f samples (the rate's constant %d "
                    "throughout: %s), running valve %s, nonfinite %zu\n",
                    quality_name(target_q), step ? ", valve -> " : "", step ? valve_name(valve) : "",
                    latency_property(au) * s.fs, reported, constant ? "yes" : "NO", adopted ? "adopted" : "NOT adopted", bad);
        ok = ok && constant && adopted && bad == 0;
    }

    // 2b. scheduled parameters: more events than AUBase's own reserve (24), then more than the wrapper's (1024); Valve
    // alternating and ending on the other valve (where step 1 left it), plus a Drive ramp (dropped: AUBase sets nothing for ramps)
    for (std::size_t nev : {std::size_t(100), std::size_t(1100)}) {
        std::vector<AudioUnitParameterEvent> ev(nev + 1);
        for (std::size_t k = 0; k < nev; ++k) {
            ev[k] = AudioUnitParameterEvent{};
            ev[k].scope = kAudioUnitScope_Global;
            ev[k].element = 0;
            ev[k].parameter = mapvs::kValve;
            ev[k].eventType = kParameterEvent_Immediate;
            ev[k].eventValues.immediate.bufferOffset = UInt32(k % 512);
            ev[k].eventValues.immediate.value = float(k % 2 == 0 ? s.valve : other_valve);
        }
        AudioUnitParameterEvent& ramp = ev[nev];
        ramp = AudioUnitParameterEvent{};
        ramp.scope = kAudioUnitScope_Global;
        ramp.parameter = mapvs::kDrive;
        ramp.eventType = kParameterEvent_Ramped;
        ramp.eventValues.ramp.startBufferOffset = 0;
        ramp.eventValues.ramp.durationInFrames = 256;
        ramp.eventValues.ramp.startValue = 0.0f;
        ramp.eventValues.ramp.endValue = 7.0f;
        std::vector<float> zL(512), zR(512);
        // render once unarmed (first-call setup in AudioToolbox), then the scheduled cycle armed
        if (render(au, in, zL, zR, zL.size(), t0, false)) return 1;
        t0 += double(zL.size());
        map_rt_clear();
        map_rt_arm(true);
        const OSStatus st = AudioUnitScheduleParameters(au, ev.data(), UInt32(ev.size()));
        const long in_call = map_rt_count();
        Abl2 abl{};
        abl.mNumberBuffers = 2;
        for (int c = 0; c < 2; ++c) {
            abl.mBuffers[c].mNumberChannels = 1;
            abl.mBuffers[c].mDataByteSize = 512 * sizeof(float);
            abl.mBuffers[c].mData = (c == 0 ? zL.data() : zR.data());
        }
        AudioTimeStamp ts{};
        ts.mFlags = kAudioTimeStampSampleTimeValid;
        ts.mSampleTime = t0;
        in.pos = 0;
        AudioUnitRenderActionFlags flags = 0;
        const OSStatus rr = AudioUnitRender(au, &flags, &ts, 0, 512, reinterpret_cast<AudioBufferList*>(&abl));
        map_rt_arm(false);
        t0 += 512.0;
        const long total = map_rt_count();
        const bool last = std::lround(get_param(au, mapvs::kValve)) == other_valve && get_param(au, mapvs::kDrive) == float(s.drive);
        std::printf("AudioUnitScheduleParameters, %zu Valve events + a Drive ramp: status %d / render %d, hooked calls in "
                    "the call %ld, in call + render %ld %s; last Valve kept, the ramp set nothing (Drive %g): %s\n", nev,
                    int(st), int(rr), in_call, total, map_rt_last(), double(get_param(au, mapvs::kDrive)), last ? "yes" : "NO");
        ok = ok && st == noErr && rr == noErr && total == 0 && last;
    }

    // 2c. immediate events take effect at their buffer offsets (AUBase's slices): Drive 1 at frame 0, 8 at frame 128
    {
        CHECK(AudioUnitSetParameter(au, mapvs::kDrive, kAudioUnitScope_Global, 0, 1.0f, 0));
        AudioUnitUninitialize(au);
        CHECK(AudioUnitInitialize(au));                 // a fresh engine at the current parameters
        mapvs::Engine ref;                              // the same, by hand
        for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id) {
            const double v = mapvs::sanitize_plain(id, double(get_param(au, id)));
            switch (id) {
            case mapvs::kDrive: ref.set_drive(v); break;
            case mapvs::kBias: ref.set_bias(v); break;
            case mapvs::kOutput: ref.set_output(v); break;
            case mapvs::kQuality: ref.set_quality(int(v)); break;
            case mapvs::kBypass: ref.set_bypass(v >= 0.5); break;
            case mapvs::kValve: ref.set_valve(int(v)); break;
            case mapvs::kBias6: ref.set_bias6(v); break;
            case mapvs::kMode6: ref.set_mode6(int(v)); break;
            case mapvs::kFilter6: ref.set_filter6(int(v)); break;
            default: break;
            }
        }
        if (!ref.prepare(s.fs)) { std::fprintf(stderr, "FAIL: reference engine\n"); return 1; }
        AudioUnitParameterEvent two[2] = {};
        for (int k = 0; k < 2; ++k) {
            two[k].scope = kAudioUnitScope_Global;
            two[k].parameter = mapvs::kDrive;
            two[k].eventType = kParameterEvent_Immediate;
            two[k].eventValues.immediate.bufferOffset = k ? 128u : 0u;
            two[k].eventValues.immediate.value = k ? 8.0f : 1.0f;
        }
        std::vector<float> aL(1024), aR(1024), eL(1024), eR(1024);
        CHECK(AudioUnitScheduleParameters(au, two, 2));
        CHECK(render_once(au, in, aL, aR, 0, 512, 0.0));
        CHECK(render_once(au, in, aL, aR, 512, 512, 512.0));   // no events: Drive stays 8
        const float* ri[2] = {L.data(), R.data()};
        float* ro[2] = {eL.data(), eR.data()};
        ref.set_drive(1.0);
        ref.process(ri, ro, 2, 128);
        ref.set_drive(8.0);
        const float* ri2[2] = {L.data() + 128, R.data() + 128};
        float* ro2[2] = {eL.data() + 128, eR.data() + 128};
        ref.process(ri2, ro2, 2, 384);
        const float* ri3[2] = {L.data() + 512, R.data() + 512};
        float* ro3[2] = {eL.data() + 512, eR.data() + 512};
        ref.process(ri3, ro3, 2, 512);
        ref.release();
        const Diff a = compare(aL, eL), b = compare(aR, eR);
        std::printf("scheduled Drive 1 @ frame 0, 8 @ frame 128: AU output vs an Engine run 0-128 at 1, then 8: max abs diff "
                    "L %.3g R %.3g\n", a.max_abs, b.max_abs);
        ok = ok && a.max_abs == 0.0 && b.max_abs == 0.0 && a.nonfinite == 0 && a.peak_ref > 0.0;
        CHECK(AudioUnitSetParameter(au, mapvs::kDrive, kAudioUnitScope_Global, 0, float(s.drive), 0));
        t0 = 1024.0;
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

        // a ClassInfo AUBase refuses (another subtype): an error, every value as it was, the running path unchanged.
        // (That render never takes a restore's intermediate values is the restore_seq_ seqlock in map_au.cpp: a race,
        // not reproducible on demand here.)
        {
            CFMutableDictionaryRef wrong = CFDictionaryCreateMutableCopy(kCFAllocatorDefault, 0, static_cast<CFDictionaryRef>(saved));
            const SInt32 bad_subtype = 0x7a7a7a7a;
            CFNumberRef n = CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt32Type, &bad_subtype);
            CFDictionarySetValue(wrong, CFSTR(kAUPresetSubtypeKey), n);
            CFRelease(n);
            CFPropertyListRef wp = wrong;
            const double tail_before = tail_property(au);
            const OSStatus rs = AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &wp, sizeof wp);
            CFRelease(wrong);
            std::vector<float> zL(std::size_t(0.05 * s.fs)), zR(zL.size());
            if (render(au, in, zL, zR, zL.size(), t0, true)) return 1;
            t0 += double(zL.size());
            bool kept = rs != noErr && tail_property(au) == tail_before;
            for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id) kept = kept && get_param(au, id) == values[id];
            std::printf("rejected ClassInfo (other subtype): error %d, every value and the running %s path kept: %s\n",
                        int(rs), valve_name(other_valve), kept ? "yes" : "NO");
            ok = ok && kept;
        }
        // a ClassInfo whose data names scope 99: AUBase::GetScope throws while decoding it (the dispatcher turns that
        // into an error). Every value comes back, and render still publishes: a Valve change afterwards is adopted.
        {
            CFMutableDictionaryRef bad = CFDictionaryCreateMutableCopy(kCFAllocatorDefault, 0, static_cast<CFDictionaryRef>(saved));
            const UInt8 blob[16] = {0, 0, 0, 99, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};   // scope 99, element 0, 0 params
            CFDataRef d = CFDataCreate(kCFAllocatorDefault, blob, sizeof blob);
            CFDictionarySetValue(bad, CFSTR(kAUPresetDataKey), d);
            CFRelease(d);
            CFPropertyListRef bp = bad;
            const OSStatus rs = AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &bp, sizeof bp);
            CFRelease(bad);
            bool kept = rs != noErr;
            for (AudioUnitParameterID id = 0; id < mapvs::kNumParams; ++id) kept = kept && get_param(au, id) == values[id];
            CHECK(AudioUnitSetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, float(s.valve), 0));
            std::vector<float> zL(std::size_t(0.02 * s.fs)), zR(zL.size());
            for (int k = 0; k < 40 && tail_property(au) != mapvs::tail_seconds(s.valve); ++k) {
                if (render(au, in, zL, zR, zL.size(), t0, true)) return 1;
                t0 += double(zL.size());
            }
            const bool publishes = tail_property(au) == mapvs::tail_seconds(s.valve);
            CHECK(AudioUnitSetParameter(au, mapvs::kValve, kAudioUnitScope_Global, 0, float(other_valve), 0));
            for (int k = 0; k < 40 && tail_property(au) != mapvs::tail_seconds(other_valve); ++k) {
                if (render(au, in, zL, zR, zL.size(), t0, true)) return 1;
                t0 += double(zL.size());
            }
            std::printf("ClassInfo whose decoding throws (scope 99): error %d, every value kept, render publishes again "
                        "(a Valve change is adopted): %s\n", int(rs), kept && publishes ? "yes" : "NO");
            ok = ok && kept && publishes;
        }

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
        g_valve_at_preset = -1.0f;
        CHECK(AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &legacy, sizeof legacy));
        bool old = int(std::lround(get_param(au, mapvs::kValve))) == mapvs::k12AX7 &&
                   g_valve_at_preset == float(mapvs::k12AX7) &&
                   get_param(au, mapvs::kBias6) == float(mapvs::kBias6Range.def) &&
                   get_param(au, mapvs::kMode6) == float(mapvs::kMode6Default) &&
                   get_param(au, mapvs::kFilter6) == float(mapvs::kFilter6Default);
        for (AudioUnitParameterID id = 0; id < AudioUnitParameterID(mapvs::kNumParamsV1); ++id)
            old = old && get_param(au, id) == values[id];
        std::printf("pre-#114 ClassInfo (no key, IDs 0-4 only): the 12AX7 path (also when PresentPreset is announced: "
                    "Valve %g), its 5 values, the 6AS6 values at their defaults: %s\n", double(g_valve_at_preset),
                    old ? "yes" : "NO");
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
