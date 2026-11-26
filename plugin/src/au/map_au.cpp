/* map_au.cpp -- AUv2 effect for the DK stages (#22; two valve paths, #114), built directly against Apple's
 * AudioUnitSDK (Apache-2.0).
 *
 * Thin glue over mapvs::Engine (../map_engine.h). Generic host UI only (no Cocoa view).
 *   Initialize / Cleanup   engine prepare / release (non-RT; the host does not render meanwhile)
 *   Reset                  engine.request_reset(), honoured by the audio thread at its next block (not while
 *                          host-bypassed: see Reset)
 *   render                 the parameter store (RT-safe reads, sanitized) -> engine targets -> engine.process. Render
 *                          is the only place that publishes parameters to the engine (Initialize too, before
 *                          prepare), once per slice, so no older snapshot can overwrite a newer one.
 *   host bypass            kAudioUnitProperty_BypassEffect drives the engine's latency-aligned crossfade instead
 *                          of AUEffectBase's plain copy, so bypass is click-free and keeps the reported latency
 *   latency                kAudioUnitProperty_Latency: the engine's constant for the sample rate (every valve and
 *                          quality is padded to it), so no switch changes it and nothing needs a notification
 *   saved state            AUBase's ClassInfo plus our kAUStateKey (map_params.h). RestoreState starts every parameter
 *                          from its default, so a preset defines all of them; a preset without the key is from before
 *                          #114 and loads on the 12AX7 path; every value is sanitized (NaN, out of range). Render
 *                          never takes parameter values from a restore in progress (restore_seq_, a seqlock), and a
 *                          restore that fails or throws puts the previous values back and ends the seqlock (RAII).
 *   scheduled parameters   AUBase's own slicing: immediate events take effect at their buffer offsets (the render is
 *                          split there and the store updated per slice). Its event list is a std::vector: Initialize
 *                          reserves kMaxScheduledEvents, and an immediate event beyond that capacity in one render
 *                          cycle is not appended, so the render thread never grows it. Such an event is applied at
 *                          once (as AUBase applies every immediate event when it is scheduled) and, after the render's
 *                          slices, again if no queued event of the same parameter has a later offset: the last value
 *                          requested for each parameter is always the one kept. Only its timing is lost (it takes effect
 *                          from the slice after it is applied, at the latest from the next render). Ramped events are dropped: the pinned SDK sets nothing for them anyway
 *                          (AUElement::SetScheduledEvent ignores ramps), but it would still split slices at them and
 *                          log a warning through os_log on the render thread for every slice a ramp covers.
 */
#include "../map_engine.h"
#include "../map_identity.h"
#include "../map_params.h"

#include <AudioUnitSDK/AUEffectBase.h>

#include <atomic>
#include <cmath>
#include <cstddef>

namespace mapvs {

class GyreAU : public ausdk::AUEffectBase {
    using Base = ausdk::AUEffectBase;

public:
    explicit GyreAU(AudioComponentInstance ci) : Base(ci, true) {
        CreateElements();
        Globals()->UseIndexedParameters(kNumParams);
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id)
            Globals()->SetParameter(id, AudioUnitParameterValue(default_plain(id)));
    }
    ~GyreAU() override { engine_.release(); }

    static constexpr std::size_t kMaxScheduledEvents = 1024;   // per render cycle; beyond it: applied at once

    OSStatus Initialize() override {
        const OSStatus r = Base::Initialize();
        if (r != noErr) return r;
        GetParamEventList().reserve(kMaxScheduledEvents);      // non-RT; AUBase's own reserve(24) follows, a no-op
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
        return fs > 0.0 ? Float64(Engine::reported_latency_for(fs)) / fs : 0.0;
    }
    // the running valve's tail once prepared (it follows a switch once adopted), else the requested one's
    Float64 GetTailTime() AUSDK_RTSAFE override {
        return tail_seconds(engine_.prepared() ? engine_.active_valve() : engine_.valve_target());
    }
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
    // ---- scheduled parameters: AUBase's, within the reserved capacity (see the header comment) -----------------
    OSStatus ScheduleParameter(const AudioUnitParameterEvent* events, UInt32 count) AUSDK_RTSAFE override {
        if (!events && count > 0) return kAudio_ParamError;
        auto& list = GetParamEventList();
        if (list.empty()) sched_.clear();               // AUBase ended the last cycle (also one whose render failed)
        for (UInt32 i = 0; i < count; ++i) {
            const AudioUnitParameterEvent& e = events[i];   // NOLINT subscript
            if (e.eventType != kParameterEvent_Immediate) continue;   // a ramp: see the header comment
            const UInt32 off = e.eventValues.immediate.bufferOffset;   // NOLINT union
            const bool ours = e.scope == kAudioUnitScope_Global && e.element == 0 && e.parameter < kNumParams;
            if (list.size() < list.capacity()) {
                Base::ScheduleParameter(&e, 1);             // push_back within capacity: no allocation
                if (ours) sched_.queued(e.parameter, off);
            } else {
                // past the reserve: applied at once (as AUBase does for every immediate event when it is scheduled),
                // and remembered, so that after the slices it wins over any earlier-offset queued event (end_render)
                SetParameter(e.parameter, e.scope, e.element, e.eventValues.immediate.value, 0);   // NOLINT union
                if (ours) sched_.overflowed(e.parameter, off, e.eventValues.immediate.value);   // NOLINT union
            }
        }
        return noErr;
    }
    // AUBase's render (its slices set the queued events per slice), then the overflow rule of the header comment
    OSStatus Render(AudioUnitRenderActionFlags& flags, const AudioTimeStamp& ts, UInt32 frames) AUSDK_RTSAFE override {
        const OSStatus r = Base::Render(flags, ts, frames);
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) {
            AudioUnitParameterValue v;
            if (sched_.final_overflow(id, v)) Globals()->SetParameter(id, v);
        }
        sched_.clear();                                 // AUBase clears its event list after this render too
        return r;
    }

    // Our key next to AUBase's: marks a state as #114's layout (map_params.h, "saved state").
    OSStatus SaveState(CFPropertyListRef* outData) override {
        const OSStatus r = Base::SaveState(outData);
        if (r != noErr || !outData || !*outData || CFGetTypeID(*outData) != CFDictionaryGetTypeID()) return r;
        auto* dict = static_cast<CFMutableDictionaryRef>(const_cast<void*>(*outData));   // AUBase made it mutable
        const SInt32 version = kAUStateVersion;
        CFNumberRef num = CFNumberCreate(kCFAllocatorDefault, kCFNumberSInt32Type, &version);
        CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, kAUStateKey, kCFStringEncodingUTF8);
        CFDictionarySetValue(dict, key, num);
        CFRelease(key);
        CFRelease(num);
        return r;
    }
    // Every parameter starts from its default, so the restored state does not depend on what was set before; a state
    // without our key is from before #114 (the 12AX7 plugin) and loads on the 12AX7 path (set before AUBase restores,
    // so its PresentPreset notification already sees the final Valve). Restored values are sanitized (a preset can
    // hold NaN or any float). If AUBase refuses the state, or throws while decoding it (AUBase::GetScope on a bad
    // scope), the previous values come back. The parameter store is shared with render, so the restore is bracketed
    // by restore_seq_ (odd while it runs; RestoreScope ends it on every exit): render publishes nothing meanwhile, and
    // publishes the final values at its next slice. RestoreState itself does not push to the engine.
    OSStatus RestoreState(CFPropertyListRef plist) override {
        bool ours = false;
        if (plist && CFGetTypeID(plist) == CFDictionaryGetTypeID()) {
            CFStringRef key = CFStringCreateWithCString(kCFAllocatorDefault, kAUStateKey, kCFStringEncodingUTF8);
            ours = CFDictionaryContainsKey(static_cast<CFDictionaryRef>(plist), key);
            CFRelease(key);
        }
        RestoreScope scope(*this);
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id)
            Globals()->SetParameter(id, AudioUnitParameterValue(default_plain(id)));
        if (!ours) Globals()->SetParameter(kValve, AudioUnitParameterValue(kValveLegacy));
        const OSStatus r = Base::RestoreState(plist);   // may throw: ~RestoreScope rolls back
        if (r != noErr) return r;                         // ~RestoreScope rolls back
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id)
            Globals()->SetParameter(id, AudioUnitParameterValue(sanitize_plain(id, double(Globals()->GetParameter(id)))));
        scope.commit();
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
    // render thread: a consistent snapshot of the parameter store, or nothing (seqlock reader: a restore was running
    // or ran meanwhile; the engine keeps its targets until the next slice publishes the restored values)
    void push_parameters_rt() AUSDK_RTSAFE {
        const unsigned seq = restore_seq_.load(std::memory_order_acquire);
        if (seq & 1u) return;
        AudioUnitParameterValue v[kNumParams];
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) v[id] = Globals()->GetParameterRT(id);
        std::atomic_thread_fence(std::memory_order_acquire);
        if (restore_seq_.load(std::memory_order_relaxed) != seq) return;
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) push_parameter(id, v[id]);
    }
    void push_parameters() {
        for (AudioUnitParameterID id = 0; id < kNumParams; ++id) push_parameter(id, Globals()->GetParameter(id));
    }

    // RestoreState's bracket: odd on entry; on every exit (return or exception) even again, and unless committed the
    // parameter store as it was on entry
    class RestoreScope {
    public:
        explicit RestoreScope(GyreAU& au) : au_(au) {
            for (AudioUnitParameterID id = 0; id < kNumParams; ++id) before_[id] = au_.Globals()->GetParameter(id);
            au_.restore_seq_.fetch_add(1, std::memory_order_relaxed);
            std::atomic_thread_fence(std::memory_order_release);
        }
        ~RestoreScope() {
            if (!committed_)
                for (AudioUnitParameterID id = 0; id < kNumParams; ++id) au_.Globals()->SetParameter(id, before_[id]);
            au_.restore_seq_.fetch_add(1, std::memory_order_release);
        }
        void commit() { committed_ = true; }
        RestoreScope(const RestoreScope&) = delete;
        RestoreScope& operator=(const RestoreScope&) = delete;

    private:
        GyreAU& au_;
        AudioUnitParameterValue before_[kNumParams];
        bool committed_ = false;
    };
    std::atomic<unsigned> restore_seq_{0};             // odd while RestoreState runs (main thread)

    // Render-thread bookkeeping of one render cycle's scheduled events (ScheduleParameter and Render run on the render
    // thread, as AUBase's own event list assumes): per parameter, the latest queued offset, and the latest overflowed
    // event (largest offset; at an equal offset the later call). Fixed size, no allocation.
    struct ScheduleBook {
        bool any_queued[kNumParams] = {}, any_over[kNumParams] = {};
        UInt32 queued_max[kNumParams] = {}, over_off[kNumParams] = {};
        AudioUnitParameterValue over_val[kNumParams] = {};
        void queued(AudioUnitParameterID id, UInt32 off) {
            if (!any_queued[id] || off > queued_max[id]) queued_max[id] = off;
            any_queued[id] = true;
        }
        void overflowed(AudioUnitParameterID id, UInt32 off, AudioUnitParameterValue v) {
            if (any_over[id] && off < over_off[id]) return;    // an earlier offset than the one kept: superseded
            over_off[id] = off;
            over_val[id] = v;
            any_over[id] = true;
        }
        // the overflowed value is the parameter's last one in this cycle: no queued event of it comes later (an
        // overflowed event was scheduled after every queued one, so at an equal offset it is the later)
        bool final_overflow(AudioUnitParameterID id, AudioUnitParameterValue& v) const {
            if (!any_over[id] || (any_queued[id] && queued_max[id] > over_off[id])) return false;
            v = over_val[id];
            return true;
        }
        void clear() {
            for (AudioUnitParameterID id = 0; id < kNumParams; ++id) any_queued[id] = any_over[id] = false;
        }
    };
    ScheduleBook sched_;
    Engine engine_;
};

}  // namespace mapvs

using GyreAU = mapvs::GyreAU;
AUSDK_COMPONENT_ENTRY(ausdk::AUBaseFactory, GyreAU)
