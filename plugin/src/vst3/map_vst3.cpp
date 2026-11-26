/* map_vst3.cpp -- VST 3 processor, controller and factory for the DK stages (#22; two valve paths, #114).
 *
 * Thin glue over mapvs::Engine (../map_engine.h): parameters in, audio through, latency out. Generic host UI only
 * (no IPlugView). Built directly against the Steinberg VST 3 SDK (MIT, 3.8.x); no VSTGUI.
 */
#include "../map_engine.h"
#include "../map_identity.h"
#include "../map_params.h"

#include "base/source/fstreamer.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vsttypes.h"
#include "public.sdk/source/main/pluginfactory.h"
#include "public.sdk/source/vst/vstaudioeffect.h"
#include "public.sdk/source/vst/vsteditcontroller.h"
#include "public.sdk/source/vst/vstparameters.h"

#include <atomic>
#include <cmath>

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace mapvs {

static const FUID kProcessorUID(MAP_VST3_PROCESSOR_UID);
static const FUID kControllerUID(MAP_VST3_CONTROLLER_UID);

// state: int32 version, then that version's normalized doubles (map_params.h, "saved state"). Refused (false, norm
// untouched) for an unknown version, a short stream or a NaN / inf value.
static bool read_state(IBStream* s, double (&norm)[kNumParams]) {
    IBStreamer st(s, kLittleEndian);
    int32 version = 0;
    if (!st.readInt32(version)) return false;
    const int count = state_value_count(version);
    if (count == 0) return false;
    double v[kNumParams];
    for (int i = 0; i < count; ++i)
        if (!st.readDouble(v[i])) return false;
    return decode_state(version, v, count, norm);
}

static void add_list(ParameterContainer& params, const char16* title, Steinberg::Vst::ParamID id,
                     const char* const* names, int count, int def) {
    auto* p = new StringListParameter(title, id, nullptr, ParameterInfo::kCanAutomate | ParameterInfo::kIsList);
    for (int k = 0; k < count; ++k) {
        String128 s{};
        for (int i = 0; names[k][i] && i < 127; ++i) s[i] = char16(names[k][i]);   // ASCII names
        p->appendString(s);
    }
    p->getInfo().defaultNormalizedValue = index_to_norm(def, count);
    p->setNormalized(index_to_norm(def, count));
    params.addParameter(p);
}

// ---- processor ---------------------------------------------------------------------------------------------------

class Processor : public AudioEffect {
public:
    Processor() {
        setControllerClass(kControllerUID);
        for (int i = 0; i < kNumParams; ++i) apply(ParamID(i), default_norm(ParamID(i)));
    }
    static FUnknown* createInstance(void*) { return static_cast<IAudioProcessor*>(new Processor); }

    tresult PLUGIN_API initialize(FUnknown* context) override {
        const tresult r = AudioEffect::initialize(context);
        if (r != kResultOk) return r;
        addAudioInput(STR16("Input"), SpeakerArr::kStereo);
        addAudioOutput(STR16("Output"), SpeakerArr::kStereo);
        return kResultOk;
    }
    tresult PLUGIN_API terminate() override {
        engine_.release();
        return AudioEffect::terminate();
    }

    // mono -> mono or stereo -> stereo
    tresult PLUGIN_API setBusArrangements(SpeakerArrangement* inputs, int32 numIns, SpeakerArrangement* outputs,
                                          int32 numOuts) override {
        if (numIns == 1 && numOuts == 1 && inputs[0] == outputs[0] &&
            (inputs[0] == SpeakerArr::kMono || inputs[0] == SpeakerArr::kStereo))
            return AudioEffect::setBusArrangements(inputs, numIns, outputs, numOuts);
        return kResultFalse;
    }
    tresult PLUGIN_API canProcessSampleSize(int32 size) override {
        return size == kSample32 ? kResultTrue : kResultFalse;
    }
    uint32 PLUGIN_API getLatencySamples() override {
        // Hosts may query after setupProcessing and before activation/Engine::prepare.
        const double fs = processSetup.sampleRate > 0.0 ? processSetup.sampleRate : 48000.0;
        return uint32(Engine::latency_for(engine_.valve_target(), engine_.quality_target(), fs));
    }
    uint32 PLUGIN_API getTailSamples() override {
        const double fs = processSetup.sampleRate > 0.0 ? processSetup.sampleRate : 48000.0;
        return uint32(getLatencySamples() + std::lround(tail_seconds(engine_.valve_target()) * fs));
    }

    // non-RT, never concurrently with process(): the host's activation is the one place where prepare() runs
    tresult PLUGIN_API setActive(TBool state) override {
        if (state) {
            if (!engine_.prepare(processSetup.sampleRate)) return kResultFalse;
        } else {
            engine_.release();
        }
        return AudioEffect::setActive(state);
    }
    tresult PLUGIN_API setProcessing(TBool state) override {
        if (state) engine_.request_reset();
        return kResultOk;
    }

    tresult PLUGIN_API process(ProcessData& data) override {
        if (IParameterChanges* changes = data.inputParameterChanges) {
            const int32 nq = changes->getParameterCount();
            for (int32 q = 0; q < nq; ++q) {
                IParamValueQueue* queue = changes->getParameterData(q);
                if (!queue) continue;
                const int32 np = queue->getPointCount();
                int32 offset = 0;
                ParamValue v = 0.0;
                // the last point of the block; the engine's 20 ms ramps do the smoothing
                if (np > 0 && queue->getPoint(np - 1, offset, v) == kResultTrue) apply(queue->getParameterId(), v);
            }
        }
        if (data.numSamples <= 0 || data.numInputs < 1 || data.numOutputs < 1) return kResultOk;
        AudioBusBuffers& in = data.inputs[0];
        AudioBusBuffers& out = data.outputs[0];
        const int32 nch = in.numChannels < out.numChannels ? in.numChannels : out.numChannels;
        if (nch < 1 || !in.channelBuffers32 || !out.channelBuffers32) return kResultOk;
        engine_.process(in.channelBuffers32, out.channelBuffers32, int(nch), int(data.numSamples));
        // channels beyond what the engine handles (none with the arrangements above) stay silent
        for (int32 c = nch; c < out.numChannels; ++c)
            for (int32 i = 0; i < data.numSamples; ++i) out.channelBuffers32[c][i] = 0.0f;
        out.silenceFlags = 0;
        return kResultOk;
    }

    tresult PLUGIN_API setState(IBStream* state) override {
        double norm[kNumParams];
        if (!state || !read_state(state, norm)) return kResultFalse;
        for (int i = 0; i < kNumParams; ++i) apply(ParamID(i), norm[i]);
        return kResultOk;
    }
    tresult PLUGIN_API getState(IBStream* state) override {
        if (!state) return kResultFalse;
        IBStreamer st(state, kLittleEndian);
        st.writeInt32(kStateVersion);
        for (auto& n : norm_) st.writeDouble(n.load(std::memory_order_relaxed));
        return kResultOk;
    }

private:
    void apply(Steinberg::Vst::ParamID id, double n) {   // any thread; realtime-safe (atomics only)
        if (id >= kNumParams) return;
        n = clamp01(n);
        norm_[id].store(n, std::memory_order_relaxed);
        switch (id) {
        case kDrive: engine_.set_drive(to_plain(kDriveRange, n)); break;
        case kBias: engine_.set_bias(to_plain(kBiasRange, n)); break;
        case kOutput: engine_.set_output(to_plain(kOutputRange, n)); break;
        case kQuality: engine_.set_quality(quality_from_norm(n)); break;
        case kBypass: engine_.set_bypass(n >= 0.5); break;
        case kValve: engine_.set_valve(index_from_norm(n, kNumValves)); break;
        case kBias6: engine_.set_bias6(to_plain(kBias6Range, n)); break;
        case kMode6: engine_.set_mode6(index_from_norm(n, kNumModes6)); break;
        case kFilter6: engine_.set_filter6(index_from_norm(n, kNumFilters6)); break;
        default: break;
        }
    }

    Engine engine_;
    std::atomic<double> norm_[kNumParams]{};
};

// ---- controller --------------------------------------------------------------------------------------------------

class Controller : public EditController {
public:
    static FUnknown* createInstance(void*) { return static_cast<IEditController*>(new Controller); }

    tresult PLUGIN_API initialize(FUnknown* context) override {
        const tresult r = EditController::initialize(context);
        if (r != kResultOk) return r;
        // ParamID order (map_params.h); the 6AS6 / 12AX7 prefix says which path a control acts on (README)
        parameters.addParameter(new RangeParameter(STR16("Drive"), kDrive, STR16("x"), kDriveRange.min,
                                                   kDriveRange.max, kDriveRange.def, 0, ParameterInfo::kCanAutomate));
        parameters.addParameter(new RangeParameter(STR16("12AX7 Bias"), kBias, STR16("x Rk"), kBiasRange.min,
                                                   kBiasRange.max, kBiasRange.def, 0, ParameterInfo::kCanAutomate));
        parameters.addParameter(new RangeParameter(STR16("Output"), kOutput, STR16("x"), kOutputRange.min,
                                                   kOutputRange.max, kOutputRange.def, 0,
                                                   ParameterInfo::kCanAutomate));
        add_list(parameters, STR16("Quality"), kQuality, kQualityNames, kNumQualities, kQualityDefault);
        parameters.addParameter(STR16("Bypass"), nullptr, 1, 0.0,
                                ParameterInfo::kCanAutomate | ParameterInfo::kIsBypass, kBypass);
        add_list(parameters, STR16("Valve"), kValve, kValveNames, kNumValves, kValveDefault);
        // T caps the built bias at kTBiasCapMa (0.5 mA): above it, T runs 0.5 mA
        parameters.addParameter(new RangeParameter(STR16("6AS6 Bias"), kBias6, STR16("mA"), kBias6Range.min,
                                                   kBias6Range.max, kBias6Range.def, 0, ParameterInfo::kCanAutomate));
        add_list(parameters, STR16("6AS6 Mode"), kMode6, kMode6Names, kNumModes6, kMode6Default);
        add_list(parameters, STR16("6AS6 Filter"), kFilter6, kFilter6Names, kNumFilters6, kFilter6Default);
        return kResultOk;
    }

    tresult PLUGIN_API setComponentState(IBStream* state) override {
        double norm[kNumParams];
        if (!state || !read_state(state, norm)) return kResultFalse;
        for (int i = 0; i < kNumParams; ++i) setParamNormalized(Steinberg::Vst::ParamID(i), norm[i]);
        return kResultOk;
    }

    // A quality or valve change moves the latency: ask the host to re-read it (UI / main thread, not the audio
    // thread). The latency is that of the requested valve and quality (Engine::latency_for).
    tresult PLUGIN_API setParamNormalized(Steinberg::Vst::ParamID tag, ParamValue value) override {
        const bool moves = tag == kQuality || tag == kValve;
        const int before = moves ? latency_index() : 0;
        const tresult r = EditController::setParamNormalized(tag, value);
        if (r == kResultOk && moves && latency_index() != before && componentHandler)
            componentHandler->restartComponent(kLatencyChanged);
        return r;
    }

private:
    // (valve, quality) as the engine maps them to a latency: on the 6AS6 path Eco and Standard are the same
    int latency_index() {
        const int v = index_from_norm(getParamNormalized(kValve), kNumValves);
        const int q = quality_from_norm(getParamNormalized(kQuality));
        return v * kNumQualities + (v == k6AS6 ? quality_6as6(q) : q);
    }
};

}  // namespace mapvs

// ---- factory -----------------------------------------------------------------------------------------------------

BEGIN_FACTORY_DEF(MAP_VENDOR_NAME, MAP_VENDOR_URL, MAP_VENDOR_EMAIL)

DEF_CLASS2(INLINE_UID_FROM_FUID(mapvs::kProcessorUID), PClassInfo::kManyInstances, kVstAudioEffectClass,
           MAP_PRODUCT_NAME, Vst::kDistributable, MAP_VST3_CATEGORY, MAP_VERSION_STRING, kVstVersionString,
           mapvs::Processor::createInstance)

DEF_CLASS2(INLINE_UID_FROM_FUID(mapvs::kControllerUID), PClassInfo::kManyInstances, kVstComponentControllerClass,
           MAP_PRODUCT_NAME " Controller", 0, "", MAP_VERSION_STRING, kVstVersionString,
           mapvs::Controller::createInstance)

END_FACTORY
