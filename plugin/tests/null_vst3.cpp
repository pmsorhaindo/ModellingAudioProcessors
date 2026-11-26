/* null_vst3.cpp -- offline render through the built VST 3 bundle (SDK hosting classes) against the direct core (#22).
 *
 * Loads the .vst3 module, instantiates processor + controller with the SDK's PlugProvider, sets the state before
 * activation (so prepare() sees the settings), renders in varying blocks through IAudioProcessor::process, and
 * compares to the path's core (null_common.h). Then a live quality / bias / valve change through process() parameter
 * changes: the processor's reported latency must follow. Then the saved state (#114): getState after the live path
 * switch carries every value of both paths; a pre-#114 (version 1) state loads on the 12AX7 path with the 6AS6
 * values at their defaults, in the processor and the controller; an unknown version is refused and changes nothing.
 * Exit status 0 only if the null is exact (max abs diff 0) and the live and state checks pass.
 *
 *   map_null_vst3 [path.vst3] [--fs 48000] [--quality 1] [--drive 2] [--bias 1.25] [--output 1.25] [--seconds 2]
 *                 [--valve 1] [--bias6 0.3] [--mode6 0] [--filter6 0]
 */
#include "null_common.h"

#include "base/source/fstreamer.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/hosting/processdata.h"

#include <chrono>
#include <thread>

using namespace Steinberg;
using namespace Steinberg::Vst;
using namespace nulltest;

namespace {

constexpr int32 kMaxBlock = 4096;

int render(IAudioProcessor* proc, HostProcessData& data, const std::vector<float>& L, const std::vector<float>& R,
           std::vector<float>& oL, std::vector<float>& oR, std::size_t n, ParameterChanges* changes,
           bool realtime_pace) {
    std::size_t pos = 0, k = 0;
    while (pos < n) {
        int32 frames = int32(block_pattern()[k++ % block_pattern().size()]);
        if (pos + std::size_t(frames) > n) frames = int32(n - pos);
        data.numSamples = frames;
        for (int c = 0; c < 2; ++c) {
            const std::vector<float>& src = c == 0 ? L : R;
            float* dst = data.inputs[0].channelBuffers32[c];
            for (int32 i = 0; i < frames; ++i) dst[i] = pos + std::size_t(i) < src.size() ? src[pos + std::size_t(i)] : 0.0f;
        }
        data.inputParameterChanges = (changes && pos == 0) ? changes : nullptr;
        if (proc->process(data) != kResultOk) { std::fprintf(stderr, "FAIL: process\n"); return 1; }
        for (int c = 0; c < 2; ++c) {
            const float* src = data.outputs[0].channelBuffers32[c];
            float* dst = (c == 0 ? oL.data() : oR.data()) + pos;
            for (int32 i = 0; i < frames; ++i) dst[i] = src[i];
        }
        pos += std::size_t(frames);
        if (realtime_pace) std::this_thread::sleep_for(std::chrono::microseconds(500));
    }
    data.inputParameterChanges = nullptr;
    return 0;
}

bool read_state(IComponent* comp, int32& version, std::vector<double>& v) {
    IPtr<MemoryStream> st = owned(new MemoryStream);
    if (comp->getState(st) != kResultOk) return false;
    st->seek(0, IBStream::kIBSeekSet, nullptr);
    IBStreamer r(st, kLittleEndian);
    if (!r.readInt32(version)) return false;
    v.clear();
    double x = 0.0;
    while (r.readDouble(x)) v.push_back(x);
    return true;
}

IPtr<MemoryStream> make_state(int32 version, const double* v, int n) {
    IPtr<MemoryStream> st = owned(new MemoryStream);
    IBStreamer w(st, kLittleEndian);
    w.writeInt32(version);
    for (int i = 0; i < n; ++i) w.writeDouble(v[i]);
    st->seek(0, IBStream::kIBSeekSet, nullptr);
    return st;
}

}  // namespace

int main(int argc, char** argv) {
    std::string path = MAP_TEST_VST3_PATH;
    int first = 1;
    if (argc > 1 && argv[1][0] != '-') { path = argv[1]; first = 2; }
    Settings s;
    parse_args(argc - (first - 1), argv + (first - 1), s);
    // the processor receives normalized values: the reference uses what it maps them back to
    const double nd = mapvs::to_norm(mapvs::kDriveRange, s.drive), nb = mapvs::to_norm(mapvs::kBiasRange, s.bias),
                 no = mapvs::to_norm(mapvs::kOutputRange, s.output), nq = mapvs::quality_to_norm(s.quality),
                 nv = mapvs::index_to_norm(s.valve, mapvs::kNumValves), nb6 = mapvs::to_norm(mapvs::kBias6Range, s.bias6),
                 nm6 = mapvs::index_to_norm(s.mode6, mapvs::kNumModes6),
                 nf6 = mapvs::index_to_norm(s.filter6, mapvs::kNumFilters6);
    s.drive = mapvs::to_plain(mapvs::kDriveRange, nd);
    s.bias = mapvs::to_plain(mapvs::kBiasRange, nb);
    s.output = mapvs::to_plain(mapvs::kOutputRange, no);
    s.bias6 = mapvs::to_plain(mapvs::kBias6Range, nb6);

    std::string err;
    auto module = VST3::Hosting::Module::create(path, err);
    if (!module) { std::fprintf(stderr, "FAIL: load %s: %s\n", path.c_str(), err.c_str()); return 1; }
    HostApplication host;
    PluginContextFactory::instance().setPluginContext(static_cast<IHostApplication*>(&host));
    auto factory = module->getFactory();
    IPtr<PlugProvider> provider;
    std::string name;
    for (auto& ci : factory.classInfos())
        if (ci.category() == kVstAudioEffectClass) { provider = owned(new PlugProvider(factory, ci, true)); name = ci.name(); break; }
    if (!provider) { std::fprintf(stderr, "FAIL: no audio effect class\n"); return 1; }
    IComponent* comp = provider->getComponent();
    IEditController* ctrl = provider->getController();
    FUnknownPtr<IAudioProcessor> proc(comp);
    if (!comp || !ctrl || !proc) { std::fprintf(stderr, "FAIL: component / controller / processor\n"); return 1; }

    SpeakerArrangement stereo = SpeakerArr::kStereo;
    if (proc->setBusArrangements(&stereo, 1, &stereo, 1) != kResultTrue) { std::fprintf(stderr, "FAIL: stereo\n"); return 1; }
    comp->activateBus(kAudio, kInput, 0, true);
    comp->activateBus(kAudio, kOutput, 0, true);

    // state before activation: version, then the normalized values (map_params.h kStateVersion layout)
    const double norm[mapvs::kNumParams] = {nd, nb, no, nq, 0.0, nv, nb6, nm6, nf6};
    {
        IPtr<MemoryStream> st = make_state(mapvs::kStateVersion, norm, mapvs::kNumParams);
        if (comp->setState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setState\n"); return 1; }
        st->seek(0, IBStream::kIBSeekSet, nullptr);
        if (ctrl->setComponentState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setComponentState\n"); return 1; }
    }
    ProcessSetup setup{kOffline, kSample32, kMaxBlock, s.fs};
    // Query several setups before activation. The engine has not prepared any of these rates yet.
    for (double fs : {48000.0, 44100.0, 96000.0, s.fs}) {
        setup.sampleRate = fs;
        if (proc->setupProcessing(setup) != kResultOk) { std::fprintf(stderr, "FAIL: setupProcessing\n"); return 1; }
        const int want = mapvs::Engine::latency_for(s.valve, s.quality, fs);
        const int tail = want + int(std::lround(mapvs::tail_seconds(s.valve) * fs));
        if (int(proc->getLatencySamples()) != want || int(proc->getTailSamples()) != tail) {
            std::fprintf(stderr, "FAIL: pre-activation latency/tail at %.0f Hz\n", fs); return 1;
        }
    }
    if (comp->setActive(true) != kResultOk) { std::fprintf(stderr, "FAIL: setActive\n"); return 1; }
    proc->setProcessing(true);
    HostProcessData data;
    data.prepare(*comp, kMaxBlock, kSample32);

    // 1. null
    std::vector<float> L, R, pL, pR, rL, rR;
    make_signal(s, L, R);
    pL.assign(L.size(), 0.0f);
    pR.assign(R.size(), 0.0f);
    if (render(proc, data, L, R, pL, pR, L.size(), nullptr, false)) return 1;
    int ref_latency = 0;
    if (!reference(s, L, R, rL, rR, ref_latency)) { std::fprintf(stderr, "FAIL: reference prepare\n"); return 1; }
    const Diff dl = compare(pL, rL), dr = compare(pR, rR);
    const uint32 lat = proc->getLatencySamples();
    std::printf("VST 3 \"%s\" (%s)\n", name.c_str(), path.c_str());
    std::printf("settings: %s, drive %.17g, output %.17g, %zu samples (%.3g s), stereo, float32, blocks cycling "
                "1..4096\n", describe(s).c_str(), s.drive, s.output, L.size(), s.seconds);
    std::printf("latency: getLatencySamples %u; core %d samples\n", unsigned(lat), ref_latency);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              int(lat) == ref_latency;

    // 2. live: quality, bias and then the valve through process() parameter changes, realtime-paced so the worker runs
    const int target_q = s.quality == mapvs::kHQ ? mapvs::kEco : mapvs::kHQ;
    const int other_valve = s.valve == mapvs::k6AS6 ? mapvs::k12AX7 : mapvs::k6AS6;
    const std::size_t nlive = std::size_t(0.25 * s.fs);
    std::vector<float> qL(nlive), qR(nlive);
    double live_norm[mapvs::kNumParams];
    for (int i = 0; i < mapvs::kNumParams; ++i) live_norm[i] = norm[i];
    for (int step = 0; step < 2; ++step) {
        ParameterChanges changes;
        int32 idx = 0;
        if (step == 0) {
            live_norm[mapvs::kQuality] = mapvs::quality_to_norm(target_q);
            live_norm[mapvs::kBias] = mapvs::to_norm(mapvs::kBiasRange, 1.5);
            if (auto* q = changes.addParameterData(mapvs::kQuality, idx)) q->addPoint(0, live_norm[mapvs::kQuality], idx);
            if (auto* q = changes.addParameterData(mapvs::kBias, idx)) q->addPoint(0, live_norm[mapvs::kBias], idx);
        } else {
            live_norm[mapvs::kValve] = mapvs::index_to_norm(other_valve, mapvs::kNumValves);
            if (auto* q = changes.addParameterData(mapvs::kValve, idx)) q->addPoint(0, live_norm[mapvs::kValve], idx);
        }
        if (render(proc, data, L, R, qL, qR, nlive, &changes, true)) return 1;
        const int valve = step == 0 ? s.valve : other_valve;
        const uint32 lat2 = proc->getLatencySamples();
        const int want = mapvs::Engine::latency_for(valve, target_q, s.fs);
        std::size_t bad = 0;
        for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
        std::printf("live: quality -> %s, bias -> 1.5%s%s: getLatencySamples %u (want %d), nonfinite %zu\n",
                    quality_name(target_q), step ? ", valve -> " : "", step ? valve_name(valve) : "", unsigned(lat2), want,
                    bad);
        ok = ok && int(lat2) == want && bad == 0;
    }

    // 3. saved state (#114)
    {
        int32 version = 0;
        std::vector<double> v;
        bool same = read_state(comp, version, v) && version == mapvs::kStateVersion && int(v.size()) == mapvs::kNumParams;
        for (int i = 0; same && i < mapvs::kNumParams; ++i) same = v[i] == live_norm[i];
        std::printf("state after the live path switch: version %d, %zu values, every value of both paths as set: %s\n",
                    int(version), v.size(), same ? "yes" : "NO");
        ok = ok && same;

        // a pre-#114 state: version 1, five values
        const double v1[mapvs::kNumParamsV1] = {nd, nb, no, nq, 0.0};
        IPtr<MemoryStream> st1 = make_state(mapvs::kStateVersionV1, v1, mapvs::kNumParamsV1);
        bool legacy = comp->setState(st1) == kResultOk;
        st1->seek(0, IBStream::kIBSeekSet, nullptr);
        legacy = legacy && ctrl->setComponentState(st1) == kResultOk;
        legacy = legacy && read_state(comp, version, v) && version == mapvs::kStateVersion && int(v.size()) == mapvs::kNumParams;
        for (int i = 0; legacy && i < mapvs::kNumParamsV1; ++i) legacy = v[i] == v1[i];
        legacy = legacy && mapvs::index_from_norm(v[mapvs::kValve], mapvs::kNumValves) == mapvs::k12AX7 &&
                 v[mapvs::kBias6] == mapvs::default_norm(mapvs::kBias6) && v[mapvs::kMode6] == mapvs::default_norm(mapvs::kMode6) &&
                 v[mapvs::kFilter6] == mapvs::default_norm(mapvs::kFilter6) &&
                 mapvs::index_from_norm(ctrl->getParamNormalized(mapvs::kValve), mapvs::kNumValves) == mapvs::k12AX7 &&
                 ctrl->getParamNormalized(mapvs::kBias6) == mapvs::default_norm(mapvs::kBias6);
        std::printf("pre-#114 state (version 1): loads on the 12AX7 path, 6AS6 values at their defaults (processor and "
                    "controller): %s\n", legacy ? "yes" : "NO");
        ok = ok && legacy;

        // refused: an unknown version, and a NaN value; the state stays what it was
        std::vector<double> before = v;
        IPtr<MemoryStream> st3 = make_state(3, norm, mapvs::kNumParams);
        double nan_norm[mapvs::kNumParams];
        for (int i = 0; i < mapvs::kNumParams; ++i) nan_norm[i] = norm[i];
        nan_norm[mapvs::kValve] = NAN;
        IPtr<MemoryStream> stn = make_state(mapvs::kStateVersion, nan_norm, mapvs::kNumParams);
        bool refused = comp->setState(st3) != kResultOk && comp->setState(stn) != kResultOk;
        refused = refused && read_state(comp, version, v) && v == before;
        std::printf("unknown version / NaN value: refused, state unchanged: %s\n", refused ? "yes" : "NO");
        ok = ok && refused;
    }

    proc->setProcessing(false);
    comp->setActive(false);
    data.unprepare();
    provider = nullptr;
    PluginContextFactory::instance().setPluginContext(nullptr);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
