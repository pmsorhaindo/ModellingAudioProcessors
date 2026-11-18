/* null_vst3.cpp -- offline render through the built VST 3 bundle (SDK hosting classes) against the direct core (#22).
 *
 * Loads the .vst3 module, instantiates processor + controller with the SDK's PlugProvider, sets the state before
 * activation (so prepare() sees the settings), renders in varying blocks through IAudioProcessor::process, and
 * compares to the reference. Then a live quality / bias change through process() parameter changes: the
 * processor's reported latency must follow.
 * Exit status 0 only if the null is exact (max abs diff 0) and the live checks pass.
 *
 *   map_null_vst3 [path.vst3] [--fs 48000] [--quality 1] [--drive 2] [--bias 1.25] [--output 1.25] [--seconds 2]
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

}  // namespace

int main(int argc, char** argv) {
    std::string path = MAP_TEST_VST3_PATH;
    int first = 1;
    if (argc > 1 && argv[1][0] != '-') { path = argv[1]; first = 2; }
    Settings s;
    parse_args(argc - (first - 1), argv + (first - 1), s);
    // the processor receives normalized values: the reference uses what it maps them back to
    const double nd = mapvs::to_norm(mapvs::kDriveRange, s.drive), nb = mapvs::to_norm(mapvs::kBiasRange, s.bias),
                 no = mapvs::to_norm(mapvs::kOutputRange, s.output), nq = mapvs::quality_to_norm(s.quality);
    s.drive = mapvs::to_plain(mapvs::kDriveRange, nd);
    s.bias = mapvs::to_plain(mapvs::kBiasRange, nb);
    s.output = mapvs::to_plain(mapvs::kOutputRange, no);

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
    {
        IPtr<MemoryStream> st = owned(new MemoryStream);
        IBStreamer w(st, kLittleEndian);
        w.writeInt32(mapvs::kStateVersion);
        const double norm[mapvs::kNumParams] = {nd, nb, no, nq, 0.0};
        for (double v : norm) w.writeDouble(v);
        st->seek(0, IBStream::kIBSeekSet, nullptr);
        if (comp->setState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setState\n"); return 1; }
        st->seek(0, IBStream::kIBSeekSet, nullptr);
        if (ctrl->setComponentState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setComponentState\n"); return 1; }
    }
    ProcessSetup setup{kOffline, kSample32, kMaxBlock, s.fs};
    if (proc->setupProcessing(setup) != kResultOk) { std::fprintf(stderr, "FAIL: setupProcessing\n"); return 1; }
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
    std::printf("settings: fs %.0f Hz, quality %s, drive %.17g, bias %.17g, output %.17g, %zu samples (%.3g s), "
                "stereo, float32, blocks cycling 1..4096\n",
                s.fs, quality_name(s.quality), s.drive, s.bias, s.output, L.size(), s.seconds);
    std::printf("latency: getLatencySamples %u; core %d samples\n", unsigned(lat), ref_latency);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              int(lat) == ref_latency;

    // 2. live: quality and bias through process() parameter changes, realtime-paced so the worker runs
    const int target_q = s.quality == mapvs::kHQ ? mapvs::kEco : mapvs::kHQ;
    ParameterChanges changes;
    int32 idx = 0;
    if (auto* q = changes.addParameterData(mapvs::kQuality, idx)) q->addPoint(0, mapvs::quality_to_norm(target_q), idx);
    if (auto* q = changes.addParameterData(mapvs::kBias, idx)) q->addPoint(0, mapvs::to_norm(mapvs::kBiasRange, 1.5), idx);
    const std::size_t nlive = std::size_t(0.25 * s.fs);
    std::vector<float> qL(nlive), qR(nlive), zero(nlive, 0.0f);
    if (render(proc, data, L, R, qL, qR, nlive, &changes, true)) return 1;
    const uint32 lat2 = proc->getLatencySamples();
    const int want = mapvs::Engine::latency_for(target_q);
    std::size_t bad = 0;
    for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
    std::printf("live: quality -> %s, bias -> 1.5: getLatencySamples %u (want %d), nonfinite %zu\n",
                quality_name(target_q), unsigned(lat2), want, bad);
    ok = ok && int(lat2) == want && bad == 0;

    proc->setProcessing(false);
    comp->setActive(false);
    data.unprepare();
    provider = nullptr;
    PluginContextFactory::instance().setPluginContext(nullptr);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 1;
}
