/* map_vst3.cpp -- VST 3 processor, controller and factory for the DK stage (#22).
 *
 * Thin glue over mapvs::Engine (../map_engine.h): parameters in, audio through, latency out. The editor is our own
 * IPlugView around the shared web editor (../ui/map_webui.h, #105). Built directly against the Steinberg VST 3 SDK
 * (MIT, 3.8.x); no VSTGUI.
 */
#include "../map_engine.h"
#include "../map_identity.h"
#include "../map_params.h"
#include "../ui/map_webui.h"

#include "base/source/fstreamer.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/vsttypes.h"
#include "public.sdk/source/common/pluginview.h"
#include "public.sdk/source/main/pluginfactory.h"
#include "public.sdk/source/vst/vstaudioeffect.h"
#include "public.sdk/source/vst/vsteditcontroller.h"
#include "public.sdk/source/vst/vstparameters.h"

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <memory>
#include <mutex>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

namespace mapvs {

static const FUID kProcessorUID(MAP_VST3_PROCESSOR_UID);
static const FUID kControllerUID(MAP_VST3_CONTROLLER_UID);

static double default_norm(ParamID id) {
    switch (id) {
    case kDrive: return to_norm(kDriveRange, kDriveRange.def);
    case kBias: return to_norm(kBiasRange, kBiasRange.def);
    case kOutput: return to_norm(kOutputRange, kOutputRange.def);
    case kQuality: return quality_to_norm(kQualityDefault);
    default: return 0.0;
    }
}

// state: kStateVersion, then kNumParams normalized doubles (map_params.h)
static bool read_state(IBStream* s, double (&norm)[kNumParams]) {
    IBStreamer st(s, kLittleEndian);
    int32 version = 0;
    if (!st.readInt32(version) || version != kStateVersion) return false;
    double v[kNumParams];
    for (double& x : v)
        if (!st.readDouble(x) || !std::isfinite(x)) return false;
    for (int i = 0; i < kNumParams; ++i) norm[i] = clamp01(v[i]);
    return true;
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
        return uint32(Engine::latency_for(engine_.quality_target(), fs));
    }
    uint32 PLUGIN_API getTailSamples() override {
        const double fs = processSetup.sampleRate > 0.0 ? processSetup.sampleRate : 48000.0;
        return uint32(getLatencySamples() + std::lround(kTailSeconds * fs));
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
        default: break;
        }
    }

    Engine engine_;
    std::atomic<double> norm_[kNumParams]{};
};

// ---- editor view (main thread) ------------------------------------------------------------------------------------

class Controller;

// The editor's logical size in points (macOS IPlugView coordinates are logical), and the host-resize limits.
inline constexpr int kEditorW = 760, kEditorH = 400;
inline constexpr double kMinScale = 0.75, kMaxScale = 2.0;

class EditorView : public CPluginView {
public:
    explicit EditorView(Controller* c);
    ~EditorView() override;

    tresult PLUGIN_API isPlatformTypeSupported(FIDString type) override {
        return type && std::strcmp(type, kPlatformTypeNSView) == 0 ? kResultTrue : kResultFalse;
    }
    tresult PLUGIN_API attached(void* parent, FIDString type) override;
    tresult PLUGIN_API removed() override;
    tresult PLUGIN_API canResize() override { return kResultTrue; }
    tresult PLUGIN_API checkSizeConstraint(ViewRect* r) override {
        if (!r) return kResultFalse;
        const int w = std::clamp(int(r->getWidth()), int(kEditorW * kMinScale), int(kEditorW * kMaxScale));
        const int h = std::clamp(int(r->getHeight()), int(kEditorH * kMinScale), int(kEditorH * kMaxScale));
        r->right = r->left + w;
        r->bottom = r->top + h;
        return kResultTrue;
    }
    tresult PLUGIN_API onSize(ViewRect* r) override {
        const tresult res = CPluginView::onSize(r);
        if (r && editor_) editor_->set_size(int(r->getWidth()), int(r->getHeight()));
        return res;
    }
    void param_changed(Steinberg::Vst::ParamID id, ParamValue v) {   // any thread (atomics in the editor)
        if (editor_) editor_->set_param(int(id), v);
    }

private:
    IPtr<Controller> ctl_;
    std::unique_ptr<ui::WebEditor> editor_;
};

// ---- controller --------------------------------------------------------------------------------------------------

class Controller : public EditController {
public:
    static FUnknown* createInstance(void*) { return static_cast<IEditController*>(new Controller); }

    tresult PLUGIN_API initialize(FUnknown* context) override {
        const tresult r = EditController::initialize(context);
        if (r != kResultOk) return r;
        parameters.addParameter(new RangeParameter(STR16("Drive"), kDrive, STR16("x"), kDriveRange.min,
                                                   kDriveRange.max, kDriveRange.def, 0, ParameterInfo::kCanAutomate));
        parameters.addParameter(new RangeParameter(STR16("Bias"), kBias, STR16("x Rk"), kBiasRange.min,
                                                   kBiasRange.max, kBiasRange.def, 0, ParameterInfo::kCanAutomate));
        parameters.addParameter(new RangeParameter(STR16("Output"), kOutput, STR16("x"), kOutputRange.min,
                                                   kOutputRange.max, kOutputRange.def, 0,
                                                   ParameterInfo::kCanAutomate));
        auto* quality = new StringListParameter(STR16("Quality"), kQuality, nullptr,
                                                ParameterInfo::kCanAutomate | ParameterInfo::kIsList);
        for (const char* name : kQualityNames) {
            String128 s{};
            for (int i = 0; name[i] && i < 127; ++i) s[i] = char16(name[i]);   // ASCII names
            quality->appendString(s);
        }
        quality->getInfo().defaultNormalizedValue = quality_to_norm(kQualityDefault);
        quality->setNormalized(quality_to_norm(kQualityDefault));
        parameters.addParameter(quality);
        parameters.addParameter(STR16("Bypass"), nullptr, 1, 0.0,
                                ParameterInfo::kCanAutomate | ParameterInfo::kIsBypass, kBypass);
        return kResultOk;
    }

    tresult PLUGIN_API setComponentState(IBStream* state) override {
        double norm[kNumParams];
        if (!state || !read_state(state, norm)) return kResultFalse;
        for (int i = 0; i < kNumParams; ++i) setParamNormalized(Steinberg::Vst::ParamID(i), norm[i]);
        return kResultOk;
    }

    // A quality change moves the latency: ask the host to re-read it (UI / main thread, not the audio thread).
    tresult PLUGIN_API setParamNormalized(Steinberg::Vst::ParamID tag, ParamValue value) override {
        const int before = tag == kQuality ? quality_from_norm(getParamNormalized(tag)) : 0;
        const tresult r = EditController::setParamNormalized(tag, value);
        if (r == kResultOk && tag == kQuality && quality_from_norm(value) != before && componentHandler)
            componentHandler->restartComponent(kLatencyChanged);
        if (r == kResultOk) {
            std::lock_guard<std::mutex> lk(views_mu_);  // controller side only, never the audio thread
            for (EditorView* v : views_) v->param_changed(tag, getParamNormalized(tag));
        }
        return r;
    }

    IPlugView* PLUGIN_API createView(FIDString name) override {
        if (!name || std::strcmp(name, ViewType::kEditor) != 0) return nullptr;
        return new EditorView(this);
    }

    // the web editor's edits (main thread): one gesture per begin/end, like a host-drawn control
    void ui_begin(int id) {
        if (id >= 0 && id < kNumParams) beginEdit(Steinberg::Vst::ParamID(id));
    }
    void ui_perform(int id, double norm) {
        if (id < 0 || id >= kNumParams) return;
        setParamNormalized(Steinberg::Vst::ParamID(id), norm);
        performEdit(Steinberg::Vst::ParamID(id), getParamNormalized(Steinberg::Vst::ParamID(id)));
    }
    void ui_end(int id) {
        if (id >= 0 && id < kNumParams) endEdit(Steinberg::Vst::ParamID(id));
    }
    double ui_norm(int id) {
        return id >= 0 && id < kNumParams ? getParamNormalized(Steinberg::Vst::ParamID(id)) : 0.0;
    }
    void add_view(EditorView* v) {
        std::lock_guard<std::mutex> lk(views_mu_);
        views_.push_back(v);
    }
    void remove_view(EditorView* v) {
        std::lock_guard<std::mutex> lk(views_mu_);
        views_.erase(std::remove(views_.begin(), views_.end(), v), views_.end());
    }

private:
    std::mutex views_mu_;
    std::vector<EditorView*> views_;
};

EditorView::EditorView(Controller* c) : CPluginView(nullptr), ctl_(c) {
    ViewRect r(0, 0, kEditorW, kEditorH);
    CPluginView::rect = r;
}
EditorView::~EditorView() {
    if (editor_) ctl_->remove_view(this);
    editor_.reset();
}

tresult PLUGIN_API EditorView::removed() {
    if (editor_) ctl_->remove_view(this);
    editor_.reset();                                    // removes the NSView from the host's parent
    return CPluginView::removed();
}

tresult PLUGIN_API EditorView::attached(void* parent, FIDString type) {
    if (!parent || editor_ || isPlatformTypeSupported(type) != kResultTrue) return kResultFalse;
    ui::Callbacks cb;
    Controller* c = ctl_.get();
    cb.begin_edit = [c](int id) { c->ui_begin(id); };
    cb.perform_edit = [c](int id, double n) { c->ui_perform(id, n); };
    cb.end_edit = [c](int id) { c->ui_end(id); };
    cb.get_norm = [c](int id) { return c->ui_norm(id); };
    cb.request_resize = [this](int w, int h) {
        if (!plugFrame) return false;
        ViewRect r(0, 0, w, h);
        checkSizeConstraint(&r);
        return plugFrame->resizeView(this, &r) == kResultTrue;   // the host then calls onSize
    };
    editor_ = std::make_unique<ui::WebEditor>(std::move(cb), int(rect.getWidth()), int(rect.getHeight()), "VST3");
    editor_->attach(parent);
    ctl_->add_view(this);
    return CPluginView::attached(parent, type);
}

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
