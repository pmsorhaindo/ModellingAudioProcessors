/* map_au.cpp -- AUv2 effect for the DK stage (#22), built directly against Apple's AudioUnitSDK (Apache-2.0).
 *
 * Thin glue over mapvs::Engine (../map_engine.h). Generic host UI only (no Cocoa view).
 *   Initialize / Cleanup   engine prepare / release (non-RT; the host does not render meanwhile)
 *   Reset                  engine.request_reset(), honoured by the audio thread at its next block (not while
 *                          host-bypassed: see Reset)
 *   render                 parameters (RT-safe reads) -> engine targets -> engine.process
 *   host bypass            kAudioUnitProperty_BypassEffect drives the engine's latency-aligned crossfade instead
 *                          of AUEffectBase's plain copy, so bypass is click-free and keeps the reported latency
 *   latency                kAudioUnitProperty_Latency follows the running quality. Once a quality switch is in
 *                          effect, the engine's worker thread posts the property notification to the main queue
 *                          (AUBase's listener list is not locked, and hosts edit it from the main thread). A shared
 *                          token, cleared in the destructor, keeps a late notification from reaching a disposed AU.
 */
#include "../map_engine.h"
#include "../map_identity.h"
#include "../map_params.h"

#include <AudioUnitSDK/AUEffectBase.h>

#include <cmath>
#include <memory>
#include <mutex>

#include <dispatch/dispatch.h>

namespace mapvs {

class MapValveStageAU : public ausdk::AUEffectBase {
    using Base = ausdk::AUEffectBase;

public:
    explicit MapValveStageAU(AudioComponentInstance ci) : Base(ci, true) {
        CreateElements();
        Globals()->UseIndexedParameters(kNumParams);
        Globals()->SetParameter(kDrive, AudioUnitParameterValue(kDriveRange.def));
        Globals()->SetParameter(kBias, AudioUnitParameterValue(kBiasRange.def));
        Globals()->SetParameter(kOutput, AudioUnitParameterValue(kOutputRange.def));
        Globals()->SetParameter(kQuality, AudioUnitParameterValue(kQualityDefault));
        Globals()->SetParameter(kBypass, 0.0f);
        token_->au = this;
        engine_.set_latency_listener([tok = token_] {        // worker thread (non-RT): hop to the main queue
            auto* hold = new std::shared_ptr<Token>(tok);
            dispatch_async_f(dispatch_get_main_queue(), hold, [](void* p) {
                std::unique_ptr<std::shared_ptr<Token>> t(static_cast<std::shared_ptr<Token>*>(p));
                std::lock_guard<std::mutex> lk((*t)->m);
                if ((*t)->au) (*t)->au->PropertyChanged(kAudioUnitProperty_Latency, kAudioUnitScope_Global, 0);
            });
        });
    }
    ~MapValveStageAU() override {
        engine_.release();                                  // joins the worker: no new notifications
        std::lock_guard<std::mutex> lk(token_->m);
        token_->au = nullptr;                               // queued ones become no-ops
    }

    OSStatus Initialize() override {
        const OSStatus r = Base::Initialize();
        if (r != noErr) return r;
        push_parameters();
        engine_.set_host_bypass(IsBypassEffect());
        return engine_.prepare(GetSampleRate()) ? OSStatus(noErr) : OSStatus(kAudioUnitErr_FailedInitialization);
    }
    void Cleanup() override {
        engine_.release();
        Base::Cleanup();
    }
    OSStatus Reset(AudioUnitScope scope, AudioUnitElement element) override {
        // AUEffectBase also calls Reset when the host turns its bypass off. The stage keeps running on the input
        // while bypassed, so its state is current; resetting it there would restart it from rest under the
        // crossfade (a thump). So reset only while not bypassed.
        if (!IsBypassEffect()) engine_.request_reset();
        return Base::Reset(scope, element);
    }

    UInt32 SupportedNumChannels(const AUChannelInfo** outInfo) override {
        static constexpr AUChannelInfo kConfigs[] = {{1, 1}, {2, 2}};
        if (outInfo) *outInfo = kConfigs;
        return UInt32(sizeof kConfigs / sizeof kConfigs[0]);
    }

    Float64 GetLatency() AUSDK_RTSAFE override {
        const Float64 fs = GetSampleRate();
        return fs > 0.0 ? Float64(engine_.active_latency_samples()) / fs : 0.0;
    }
    Float64 GetTailTime() AUSDK_RTSAFE override { return kTailSeconds; }
    bool SupportsTail() AUSDK_RTSAFE override { return true; }

    // ---- parameters ------------------------------------------------------------------------------------------
    OSStatus GetParameterInfo(AudioUnitScope scope, AudioUnitParameterID id, AudioUnitParameterInfo& info) override {
        if (scope != kAudioUnitScope_Global) return kAudioUnitErr_InvalidScope;
        info.flags = kAudioUnitParameterFlag_IsWritable | kAudioUnitParameterFlag_IsReadable;
        switch (id) {
        case kDrive: fill(info, CFSTR("Drive"), kAudioUnitParameterUnit_LinearGain, kDriveRange); break;
        case kBias: fill(info, CFSTR("Bias"), kAudioUnitParameterUnit_Ratio, kBiasRange); break;
        case kOutput: fill(info, CFSTR("Output"), kAudioUnitParameterUnit_LinearGain, kOutputRange); break;
        case kQuality:
            fill(info, CFSTR("Quality"), kAudioUnitParameterUnit_Indexed,
                 Range{0.0, double(kNumQualities - 1), double(kQualityDefault)});
            break;
        case kBypass: fill(info, CFSTR("Bypass"), kAudioUnitParameterUnit_Boolean, Range{0.0, 1.0, 0.0}); break;
        default: return kAudioUnitErr_InvalidParameter;
        }
        return noErr;
    }
    OSStatus GetParameterValueStrings(AudioUnitScope scope, AudioUnitParameterID id, CFArrayRef* outStrings) override {
        if (scope != kAudioUnitScope_Global || id != kQuality) return kAudioUnitErr_InvalidProperty;
        if (!outStrings) return noErr;                  // asking whether strings exist
        CFStringRef names[kNumQualities];
        for (int i = 0; i < kNumQualities; ++i)
            names[i] = CFStringCreateWithCString(kCFAllocatorDefault, kQualityNames[i], kCFStringEncodingUTF8);
        *outStrings = CFArrayCreate(kCFAllocatorDefault, reinterpret_cast<const void**>(names), kNumQualities,
                                    &kCFTypeArrayCallBacks);
        for (CFStringRef s : names) CFRelease(s);
        return noErr;
    }
    using Base::SetParameter;
    OSStatus SetParameter(AudioUnitParameterID id, AudioUnitScope scope, AudioUnitElement element,
                          AudioUnitParameterValue value, UInt32 offset) AUSDK_RTSAFE override {
        const OSStatus r = Base::SetParameter(id, scope, element, value, offset);
        if (r == noErr && scope == kAudioUnitScope_Global) push_parameter(id, value);   // atomics only
        return r;
    }
    OSStatus RestoreState(CFPropertyListRef plist) override {
        const OSStatus r = Base::RestoreState(plist);
        if (r == noErr) push_parameters();
        return r;
    }

    // ---- bypass and render -----------------------------------------------------------------------------------
    void SetBypassEffect(bool flag) override {
        Base::SetBypassEffect(flag);
        engine_.set_host_bypass(flag);
    }
    bool ShouldBypassEffect() AUSDK_RTSAFE override { return false; }   // the engine crossfades instead

    OSStatus ProcessBufferLists(AudioUnitRenderActionFlags& flags, const AudioBufferList& inBuffer,
                                AudioBufferList& outBuffer, UInt32 frames) AUSDK_RTSAFE override {
        push_parameters_rt();
        const UInt32 nin = inBuffer.mNumberBuffers, nout = outBuffer.mNumberBuffers;
        const UInt32 nch = nin < nout ? nin : nout;
        if (nch < 1) return noErr;
        const float* in[2] = {nullptr, nullptr};
        float* out[2] = {nullptr, nullptr};
        for (UInt32 c = 0; c < nch && c < 2; ++c) {
            in[c] = static_cast<const float*>(inBuffer.mBuffers[c].mData);    // NOLINT subscript
            out[c] = static_cast<float*>(outBuffer.mBuffers[c].mData);        // NOLINT subscript
            if (!in[c] || !out[c]) return kAudio_ParamError;
        }
        engine_.process(in, out, int(nch < 2 ? nch : 2), int(frames));
        flags &= ~kAudioUnitRenderAction_OutputIsSilence;
        return noErr;
    }

private:
    static void fill(AudioUnitParameterInfo& info, CFStringRef name, AudioUnitParameterUnit unit, const Range& r) {
        FillInParameterName(info, name, false);
        info.unit = unit;
        info.minValue = AudioUnitParameterValue(r.min);
        info.maxValue = AudioUnitParameterValue(r.max);
        info.defaultValue = AudioUnitParameterValue(r.def);
    }
    void push_parameter(AudioUnitParameterID id, AudioUnitParameterValue v) AUSDK_RTSAFE {
        switch (id) {
        case kDrive: engine_.set_drive(double(v)); break;
        case kBias: engine_.set_bias(double(v)); break;
        case kOutput: engine_.set_output(double(v)); break;
        case kQuality: engine_.set_quality(int(std::lround(double(v)))); break;
        case kBypass: engine_.set_bypass(v >= 0.5f); break;
        default: break;
        }
    }
    void push_parameters_rt() AUSDK_RTSAFE {           // render thread: scheduled / ramped values land here
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) push_parameter(id, Globals()->GetParameterRT(id));
    }
    void push_parameters() {
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) push_parameter(id, Globals()->GetParameter(id));
    }

    struct Token {
        std::mutex m;
        MapValveStageAU* au = nullptr;
    };
    std::shared_ptr<Token> token_ = std::make_shared<Token>();
    Engine engine_;
};

}  // namespace mapvs

using MapValveStageAU = mapvs::MapValveStageAU;
AUSDK_COMPONENT_ENTRY(ausdk::AUBaseFactory, MapValveStageAU)
