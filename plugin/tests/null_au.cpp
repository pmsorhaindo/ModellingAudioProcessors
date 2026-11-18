/* null_au.cpp -- offline render through the installed AU (AudioComponent API) against the direct core (#22).
 *
 * Finds the component by type / subtype / manufacturer (map_identity.h, via CMake), so it tests the bundle that
 * the system registered (install it to ~/Library/Audio/Plug-Ins/Components first). Then:
 *   1. null: parameters set before AudioUnitInitialize, render in varying blocks, compare to the reference
 *   2. live: switch the quality to HQ and the bias while rendering; the latency property must follow
 * Exit status 0 only if the null is exact (max abs diff 0) and the live checks pass.
 *
 *   map_null_au [--fs 48000] [--quality 1] [--drive 2] [--bias 1.25] [--output 1.25] [--seconds 2]
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

}  // namespace

int main(int argc, char** argv) {
    Settings s;
    parse_args(argc, argv, s);
    // the AU's parameters are float: the reference uses the values the AU actually receives
    s.drive = double(float(s.drive));
    s.bias = double(float(s.bias));
    s.output = double(float(s.output));

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
    CHECK(AudioUnitInitialize(au));

    // 1. null
    std::vector<float> pL(L.size()), pR(R.size()), rL, rR;
    if (render(au, in, pL, pR, L.size(), 0.0, false)) return 1;
    int ref_latency = 0;
    if (!reference(s, L, R, rL, rR, ref_latency)) { std::fprintf(stderr, "FAIL: reference prepare\n"); return 1; }
    const Diff dl = compare(pL, rL), dr = compare(pR, rR);
    const double lat = latency_property(au);
    std::printf("AU \"%s\" (%s %s %s)\n", name, MAP_TEST_AU_TYPE, MAP_TEST_AU_SUBTYPE, MAP_TEST_AU_MANUFACTURER);
    std::printf("settings: fs %.0f Hz, quality %s, drive %.6g, bias %.6g, output %.6g, %zu samples (%.3g s), "
                "stereo, float32, blocks cycling 1..4096\n",
                s.fs, quality_name(s.quality), s.drive, s.bias, s.output, L.size(), s.seconds);
    std::printf("latency: AU property %.9g s = %.3f samples; core %d samples\n", lat, lat * s.fs, ref_latency);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              std::lround(lat * s.fs) == ref_latency;

    // 2. live quality and bias change while rendering (realtime-paced so the engine's worker runs)
    const int target_q = s.quality == mapvs::kHQ ? mapvs::kEco : mapvs::kHQ;
    CHECK(AudioUnitSetParameter(au, mapvs::kQuality, kAudioUnitScope_Global, 0, float(target_q), 0));
    CHECK(AudioUnitSetParameter(au, mapvs::kBias, kAudioUnitScope_Global, 0, 1.5f, 0));
    const std::size_t nlive = std::size_t(0.25 * s.fs);
    std::vector<float> qL(nlive), qR(nlive);
    if (render(au, in, qL, qR, nlive, double(L.size()), true)) return 1;
    const double lat2 = latency_property(au);
    const int want = mapvs::Engine::latency_for(target_q);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
    std::printf("live: quality -> %s, bias -> 1.5: latency property %.3f samples (want %d), nonfinite %zu\n",
                quality_name(target_q), lat2 * s.fs, want, bad);
    ok = ok && std::lround(lat2 * s.fs) == want && bad == 0;

    AudioUnitUninitialize(au);
    AudioComponentInstanceDispose(au);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
