/* map_au.cpp -- AUv2 effect for the DK stages (#22; two valve paths, #114), built directly against Apple's
 * AudioUnitSDK (Apache-2.0).
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
 *   saved state            AUBase's ClassInfo plus our kAUStateKey (map_params.h). RestoreState starts every parameter
 *                          from its default, so a preset defines all of them; a preset without the key is from before
 *                          #114 and loads on the 12AX7 path; every value is sanitized (NaN, out of range).
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

class GyreAU : public ausdk::AUEffectBase {
    using Base = ausdk::AUEffectBase;

public:
    explicit GyreAU(AudioComponentInstance ci) : Base(ci, true) {
        CreateElements();
        Globals()->UseIndexedParameters(kNumParams);
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id)
            Globals()->SetParameter(id, AudioUnitParameterValue(default_plain(id)));
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
    ~GyreAU() override {
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
    Float64 GetTailTime() AUSDK_RTSAFE override { return tail_seconds(engine_.valve_target()); }
    bool SupportsTail() AUSDK_RTSAFE override { return true; }

    // ---- parameters ------------------------------------------------------------------------------------------
    OSStatus GetParameterInfo(AudioUnitScope scope, AudioUnitParameterID id, AudioUnitParameterInfo& info) override {
        if (scope != kAudioUnitScope_Global) return kAudioUnitErr_InvalidScope;
        info.flags = kAudioUnitParameterFlag_IsWritable | kAudioUnitParameterFlag_IsReadable;
        switch (id) {
        case kDrive: fill(info, CFSTR("Drive"), kAudioUnitParameterUnit_LinearGain, kDriveRange); break;
        case kBias: fill(info, CFSTR("12AX7 Bias"), kAudioUnitParameterUnit_Ratio, kBiasRange); break;
        case kOutput: fill(info, CFSTR("Output"), kAudioUnitParameterUnit_LinearGain, kOutputRange); break;
        case kQuality: fill_list(info, CFSTR("Quality"), kNumQualities, kQualityDefault); break;
        case kBypass: fill(info, CFSTR("Bypass"), kAudioUnitParameterUnit_Boolean, Range{0.0, 1.0, 0.0}); break;
        case kValve: fill_list(info, CFSTR("Valve"), kNumValves, kValveDefault); break;
        // T caps the built bias at kTBiasCapMa (0.5 mA): above it, T runs 0.5 mA
        case kBias6: fill(info, CFSTR("6AS6 Bias (mA)"), kAudioUnitParameterUnit_Generic, kBias6Range); break;
        case kMode6: fill_list(info, CFSTR("6AS6 Mode"), kNumModes6, kMode6Default); break;
        case kFilter6: fill_list(info, CFSTR("6AS6 Filter"), kNumFilters6, kFilter6Default); break;
        default: return kAudioUnitErr_InvalidParameter;
        }
        return noErr;
    }
    OSStatus GetParameterValueStrings(AudioUnitScope scope, AudioUnitParameterID id, CFArrayRef* outStrings) override {
        if (scope != kAudioUnitScope_Global) return kAudioUnitErr_InvalidProperty;
        const char* const* list = nullptr;
        int count = 0;
        switch (id) {
        case kQuality: list = kQualityNames; count = kNumQualities; break;
        case kValve: list = kValveNames; count = kNumValves; break;
        case kMode6: list = kMode6Names; count = kNumModes6; break;
        case kFilter6: list = kFilter6Names; count = kNumFilters6; break;
        default: return kAudioUnitErr_InvalidProperty;
        }
        if (!outStrings) return noErr;                  // asking whether strings exist
        constexpr int kMaxNames = 3;
        static_assert(int(kNumQualities) <= kMaxNames && int(kNumValves) <= kMaxNames && int(kNumModes6) <= kMaxNames &&
                      int(kNumFilters6) <= kMaxNames, "every list fits");
        CFStringRef names[kMaxNames];
        for (int i = 0; i < count; ++i)
            names[i] = CFStringCreateWithCString(kCFAllocatorDefault, list[i], kCFStringEncodingUTF8);
        *outStrings = CFArrayCreate(kCFAllocatorDefault, reinterpret_cast<const void**>(names), count,
                                    &kCFTypeArrayCallBacks);
        for (int i = 0; i < count; ++i) CFRelease(names[i]);
        return noErr;
    }
    using Base::SetParameter;
    OSStatus SetParameter(AudioUnitParameterID id, AudioUnitScope scope, AudioUnitElement element,
                          AudioUnitParameterValue value, UInt32 offset) AUSDK_RTSAFE override {
        const OSStatus r = Base::SetParameter(id, scope, element, value, offset);
        if (r == noErr && scope == kAudioUnitScope_Global) push_parameter(id, value);   // atomics only
        return r;
    }
    // Our key next to AUBase's: marks a state as #114's layout (map_params.h, "saved state").
    OSStatus SaveState(CFPropertyListRef* outData) override {
        const OSStatus r = Base::SaveState(outData);
        if (r != noErr || !outData || !*outData || CFGetTypeID(*outData) != CFDictionaryGetTypeID()) return r;
        auto* dict = static_cast<CFMutableDictionaryRef>(const_cast<void*>(*outData));   // AUBase made it mutable
        const SInt32 version = kStateVersion;
        CFNumberRef num = CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt32Type, &version);
        CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, kAUStateKey, kCFStringEncodingUTF8);
        CFDictionarySetValue(dict, key, num);
        CFRelease(key);
        CFRelease(num);
        return r;
    }
    // Every parameter starts from its default, so the restored state does not depend on what was set before; a state
    // without our key is from before #114 (the 12AX7 plugin) and loads on the 12AX7 path. If AUBase refuses the
    // state, the previous values come back. Restored values are sanitized (a preset can hold NaN or any float).
    OSStatus RestoreState(CFPropertyListRef plist) override {
        AudioUnitParameterValue before[kNumParams];
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) {
            before[id] = Globals()->GetParameter(id);
            Globals()->SetParameter(id, AudioUnitParameterValue(default_plain(id)));
        }
        const OSStatus r = Base::RestoreState(plist);
        if (r != noErr) {
            for (AudioUnitParameterID id = 0; id < kNumParams; ++id) Globals()->SetParameter(id, before[id]);
            return r;
        }
        bool ours = false;
        if (plist && CFGetTypeID(plist) == CFDictionaryGetTypeID()) {
            CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, kAUStateKey, kCFStringEncodingUTF8);
            ours = CFDictionaryContainsKey(static_cast<CFDictionaryRef>(plist), key);
            CFRelease(key);
        }
        if (!ours) Globals()->SetParameter(kValve, AudioUnitParameterValue(kValveLegacy));
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id)
            Globals()->SetParameter(id, AudioUnitParameterValue(sanitize_plain(id, double(Globals()->GetParameter(id)))));
        push_parameters();
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
    static void fill_list(AudioUnitParameterInfo& info, CFStringRef name, int count, int def) {
        fill(info, name, kAudioUnitParameterUnit_Indexed, Range{0.0, double(count - 1), double(def)});
    }
    // atomics only; any float (NaN, out of range) lands on a valid value (sanitize_plain)
    void push_parameter(AudioUnitParameterID id, AudioUnitParameterValue raw) AUSDK_RTSAFE {
        if (id >= kNumParams) return;
        const double v = sanitize_plain(id, double(raw));
        switch (id) {
        case kDrive: engine_.set_drive(v); break;
        case kBias: engine_.set_bias(v); break;
        case kOutput: engine_.set_output(v); break;
        case kQuality: engine_.set_quality(int(v)); break;
        case kBypass: engine_.set_bypass(v >= 0.5); break;
        case kValve: engine_.set_valve(int(v)); break;
        case kBias6: engine_.set_bias6(v); break;
        case kMode6: engine_.set_mode6(int(v)); break;
        case kFilter6: engine_.set_filter6(int(v)); break;
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
        GyreAU* au = nullptr;
    };
    std::shared_ptr<Token> token_ = std::make_shared<Token>();
    Engine engine_;
};

}  // namespace mapvs

using GyreAU = mapvs::GyreAU;
AUSDK_COMPONENT_ENTRY(ausdk::AUBaseFactory, GyreAU)
