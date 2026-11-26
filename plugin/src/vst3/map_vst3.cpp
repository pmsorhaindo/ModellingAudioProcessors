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

/* Read-only, hidden output parameter: the processor writes the latency of the stage it is running (normalized:
 * samples / kLatencyScale) into the host's output parameter changes whenever that changes, and the controller, which
 * the host hands those changes to on its own thread, asks the host to re-read the latency. So a switch reaches delay
 * compensation however the change arrived (controller edit, processor automation, state) and only once it is in
 * effect. Not part of the saved state. */
static constexpr Steinberg::Vst::ParamID kLatencyParam = 1000;
static constexpr double kLatencyScale = 1024.0;   // > any latency the engine runs (Engine::kRing - kChunk)
static_assert(Engine::kRing - Engine::kChunk < int(kLatencyScale), "latency fits the output parameter");

// state (map_params.h, "saved state"): the pre-#114 head (version 1, 5 values), then the #114 extension if present.
// Refused (false, norm untouched) for another head version, a short stream or a NaN / inf value.
static bool read_state(IBStream* s, double (&norm)[kNumParams]) {
    IBStreamer st(s, kLittleEndian);
    int32 version = 0;
    if (!st.readInt32(version) || version != kStateVersion) return false;
    double base[kNumParamsV1], ext[kNumParamsExt];
    for (double& v : base)
        if (!st.readDouble(v)) return false;
    int32 magic = 0, ext_version = 0;
    if (!st.readInt32(magic) || magic != kStateExtMagic) return decode_state(base, nullptr, norm);   // pre-#114
    if (!st.readInt32(ext_version) || ext_version < 1) return false;
    for (double& v : ext)
        if (!st.readDouble(v)) return false;
    return decode_state(base, ext, norm);
}

static void write_state(IBStream* s, const double (&norm)[kNumParams]) {
    IBStreamer st(s, kLittleEndian);
    st.writeInt32(kStateVersion);
    for (int i = 0; i < kNumParamsV1; ++i) st.writeDouble(norm[i]);
    st.writeInt32(kStateExtMagic);
    st.writeInt32(kStateExtVersion);
    for (int i = kNumParamsV1; i < int(kNumParams); ++i) st.writeDouble(norm[i]);
}

/* A list parameter whose index is the processor's (index_from_norm: rounded to the nearest entry), so the host's
 * display and the DSP agree for any normalized value, also one between entries. StringListParameter's own toPlain
 * would floor n * count instead (at n = 0.3 of three entries: entry 0, where the processor runs entry 1). Quality
 * keeps the rounding the processor has always used, so a pre-#114 session sounds as it did. */
class ListParameter : public StringListParameter {
public:
    ListParameter(const char16* title, Steinberg::Vst::ParamID id, int count)
        : StringListParameter(title, id, nullptr, ParameterInfo::kCanAutomate | ParameterInfo::kIsList), count_(count) {}
    ParamValue toPlain(ParamValue n) const override { return ParamValue(index_from_norm(n, count_)); }

private:
    int count_;
};

static void add_list(ParameterContainer& params, const char16* title, Steinberg::Vst::ParamID id,
                     const char* const* names, int count, int def) {
    auto* p = new ListParameter(title, id, count);
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
    // Active: the latency of the stage the engine is running (what the output is delayed by), including a fallback
    // path. Before activation (hosts query after setupProcessing): the latency the requested valve and quality will
    // have at the set-up rate.
    uint32 PLUGIN_API getLatencySamples() override {
        if (engine_.prepared()) return uint32(engine_.active_latency_samples());
        const double fs = processSetup.sampleRate > 0.0 ? processSetup.sampleRate : 48000.0;
        return uint32(Engine::latency_for(engine_.valve_target(), engine_.quality_target(), fs));
    }
    uint32 PLUGIN_API getTailSamples() override {
        const double fs = processSetup.sampleRate > 0.0 ? processSetup.sampleRate : 48000.0;
        const int valve = engine_.prepared() ? engine_.active_valve() : engine_.valve_target();
        return uint32(getLatencySamples() + std::lround(tail_seconds(valve) * fs));
    }

    // non-RT, never concurrently with process(): the host's activation is the one place where prepare() runs
    tresult PLUGIN_API setActive(TBool state) override {
        if (state) {
            if (!engine_.prepare(processSetup.sampleRate)) return kResultFalse;
            reported_latency_ = engine_.active_latency_samples();   // what getLatencySamples() now returns
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
        report_latency(data);
        if (data.numSamples <= 0 || data.numInputs < 1 || data.numOutputs < 1) return kResultOk;
        AudioBusBuffers& in = data.inputs[0];
        AudioBusBuffers& out = data.outputs[0];
        const int32 nch = in.numChannels < out.numChannels ? in.numChannels : out.numChannels;
        if (nch < 1 || !in.channelBuffers32 || !out.channelBuffers32) return kResultOk;
        engine_.process(in.channelBuffers32, out.channelBuffers32, int(nch), int(data.numSamples));
        report_latency(data);                           // a switch adopted in this block
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
        double norm[kNumParams];
        for (int i = 0; i < kNumParams; ++i) norm[i] = norm_[i].load(std::memory_order_relaxed);
        write_state(state, norm);
        return kResultOk;
    }

private:
    // audio thread: tell the controller (through the host's output parameter changes, preallocated by the host) when
    // the running latency differs from the one last reported; retried next block if the host gave no container
    void report_latency(ProcessData& data) {
        const int lat = engine_.active_latency_samples();
        if (lat == reported_latency_ || !data.outputParameterChanges) return;
        int32 index = 0;
        if (IParamValueQueue* q = data.outputParameterChanges->addParameterData(kLatencyParam, index)) {
            int32 point = 0;
            if (q->addPoint(0, double(lat) / kLatencyScale, point) == kResultTrue) reported_latency_ = lat;
        }
    }

    // any thread; realtime-safe (atomics only). A NaN / inf value from a host is ignored: it is neither applied nor
    // stored, so it cannot reach the saved state or an integer conversion.
    void apply(Steinberg::Vst::ParamID id, double n) {
        if (id >= kNumParams || !std::isfinite(n)) return;
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
    int reported_latency_ = -1;                         // audio thread, or setActive() (never concurrently)
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
        parameters.addParameter(STR16("Latency"), STR16("samples"), 0, 0.0,
                                ParameterInfo::kIsReadOnly | ParameterInfo::kIsHidden, kLatencyParam);
        return kResultOk;
    }

    tresult PLUGIN_API setComponentState(IBStream* state) override {
        double norm[kNumParams];
        if (!state || !read_state(state, norm)) return kResultFalse;
        for (int i = 0; i < kNumParams; ++i) setParamNormalized(Steinberg::Vst::ParamID(i), norm[i]);
        return kResultOk;
    }

    // Ask the host to re-read the latency (UI / main thread, not the audio thread):
    //   - when the processor reports that the running latency changed (kLatencyParam: the switch is in effect);
    //   - when a Quality / Valve edit will move it, so a host that reactivates on kLatencyChanged prepares the new
    //     stage at once (a host that only re-reads then sees the running latency, and is told again on adoption).
    // A NaN / inf value is refused.
    tresult PLUGIN_API setParamNormalized(Steinberg::Vst::ParamID tag, ParamValue value) override {
        if (!std::isfinite(value)) return kResultFalse;
        const bool moves = tag == kQuality || tag == kValve;
        const int before = moves ? latency_index() : 0;
        const ParamValue lat_before = tag == kLatencyParam ? getParamNormalized(tag) : 0.0;
        const tresult r = EditController::setParamNormalized(tag, value);
        if (r != kResultOk || !componentHandler) return r;
        if ((moves && latency_index() != before) || (tag == kLatencyParam && getParamNormalized(tag) != lat_before))
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
