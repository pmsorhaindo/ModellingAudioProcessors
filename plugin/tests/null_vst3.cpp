/* null_vst3.cpp -- offline render through the built VST 3 bundle (SDK hosting classes) against the direct core (#22).
 *
 * Loads the .vst3 module, instantiates processor + controller with the SDK's PlugProvider, sets the state before
 * activation (so prepare() sees the settings), renders in varying blocks through IAudioProcessor::process, and
 * compares to the path's core (null_common.h). Then, through process() parameter changes only (the controller is not
 * told), a live quality / bias change and then a valve switch: getLatencySamples() stays the rate's constant
 * throughout (the engine pads every stage to it, so delay compensation never goes stale and nothing needs a restart);
 * getTailSamples() follows the running valve, which shows the adoption; and after a reset the new path nulls exactly
 * against its own core. Then NaN host values (processor queue and controller) are
 * ignored and the saved state still reloads; the controller's list displays agree with the processor's index for
 * values between entries. Then the saved state (#114): getState after the switch carries every value of both paths and
 * is readable by the pre-#114 reader (version 1, five values); a pre-#114 state loads on the 12AX7 path with the 6AS6
 * values at their defaults, in the processor and the controller; another head version, a short extension and a NaN
 * value are refused and change nothing.
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

#include <algorithm>
#include <chrono>
#include <utility>
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

// a state stream: head version, the first n_base values, then (if ext_version > 0) the #114 extension with n_ext values
IPtr<MemoryStream> make_state(int32 version, const double* v, int n_base, int32 ext_version = 0, int n_ext = 0,
                              int32 magic = mapvs::kStateExtMagic) {
    IPtr<MemoryStream> st = owned(new MemoryStream);
    IBStreamer w(st, kLittleEndian);
    w.writeInt32(version);
    for (int i = 0; i < n_base; ++i) w.writeDouble(v[i]);
    if (ext_version > 0) {
        w.writeInt32(magic);
        w.writeInt32(ext_version);
        for (int i = 0; i < n_ext; ++i) w.writeDouble(v[n_base + i]);
    }
    st->seek(0, IBStream::kIBSeekSet, nullptr);
    return st;
}

// getState, decoded as this build lays it out: head version, 5 values, magic, extension version, 4 values
bool get_state(IComponent* comp, double (&v)[mapvs::kNumParams]) {
    IPtr<MemoryStream> st = owned(new MemoryStream);
    if (comp->getState(st) != kResultOk) return false;
    st->seek(0, IBStream::kIBSeekSet, nullptr);
    IBStreamer r(st, kLittleEndian);
    int32 version = 0, magic = 0, ext = 0;
    if (!r.readInt32(version) || version != mapvs::kStateVersion) return false;
    for (int i = 0; i < mapvs::kNumParamsV1; ++i)
        if (!r.readDouble(v[i])) return false;
    if (!r.readInt32(magic) || magic != mapvs::kStateExtMagic || !r.readInt32(ext) || ext != mapvs::kStateExtVersion) return false;
    for (int i = mapvs::kNumParamsV1; i < mapvs::kNumParams; ++i)
        if (!r.readDouble(v[i])) return false;
    double extra = 0.0;
    return !r.readDouble(extra);                        // nothing after
}

// the pre-#114 processor's read_state, verbatim in effect: version must be 1, then 5 doubles; the rest is not read
bool pre114_reads(IComponent* comp, double (&v)[mapvs::kNumParamsV1]) {
    IPtr<MemoryStream> st = owned(new MemoryStream);
    if (comp->getState(st) != kResultOk) return false;
    st->seek(0, IBStream::kIBSeekSet, nullptr);
    IBStreamer r(st, kLittleEndian);
    int32 version = 0;
    if (!r.readInt32(version) || version != 1) return false;
    for (double& x : v)
        if (!r.readDouble(x) || !std::isfinite(x)) return false;
    return true;
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
        IPtr<MemoryStream> st = make_state(mapvs::kStateVersion, norm, mapvs::kNumParamsV1, mapvs::kStateExtVersion,
                                           mapvs::kNumParamsExt);
        if (comp->setState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setState\n"); return 1; }
        st->seek(0, IBStream::kIBSeekSet, nullptr);
        if (ctrl->setComponentState(st) != kResultOk) { std::fprintf(stderr, "FAIL: setComponentState\n"); return 1; }
    }
    ProcessSetup setup{kOffline, kSample32, kMaxBlock, s.fs};
    // Query several setups before activation. The engine has not prepared any of these rates yet.
    for (double fs : {48000.0, 44100.0, 96000.0, s.fs}) {
        setup.sampleRate = fs;
        if (proc->setupProcessing(setup) != kResultOk) { std::fprintf(stderr, "FAIL: setupProcessing\n"); return 1; }
        const int want = mapvs::Engine::reported_latency_for(fs);
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
    const uint32 lat = proc->getLatencySamples();
    const int reported = mapvs::Engine::reported_latency_for(s.fs), pad = reported - ref_latency;
    const Diff dl = compare(pL, delayed(rL, pad)), dr = compare(pR, delayed(rR, pad));
    std::printf("VST 3 \"%s\" (%s)\n", name.c_str(), path.c_str());
    std::printf("settings: %s, drive %.17g, output %.17g, %zu samples (%.3g s), stereo, float32, blocks cycling "
                "1..4096\n", describe(s).c_str(), s.drive, s.output, L.size(), s.seconds);
    std::printf("latency: getLatencySamples %u = the rate's constant %d = core %d + pad %d samples\n", unsigned(lat),
                reported, ref_latency, pad);
    std::printf("null L: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dl.max_abs, dl.at, dl.peak_ref,
                dl.nonfinite);
    std::printf("null R: max abs diff %.3g (at %zu, ref peak %.4g), nonfinite %zu\n", dr.max_abs, dr.at, dr.peak_ref,
                dr.nonfinite);
    bool ok = dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 &&
              int(lat) == reported;

    // 2. live, through process() parameter changes only, realtime-paced so the worker runs
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
        const int valve = step == 0 ? s.valve : other_valve;
        // the tail follows the running valve (0.2 s 12AX7, 0.8 s 6AS6): it shows when the processor has adopted it
        const int tail_want = reported + int(std::lround(mapvs::tail_seconds(valve) * s.fs));
        const int before = int(proc->getLatencySamples());
        bool constant = true;
        if (render(proc, data, L, R, qL, qR, nlive, &changes, true)) return 1;
        for (int k = 0; k < 40 && int(proc->getTailSamples()) != tail_want; ++k) {   // worker build + 10 ms fades
            if (render(proc, data, L, R, qL, qR, std::size_t(0.02 * s.fs), nullptr, true)) return 1;
            constant = constant && int(proc->getLatencySamples()) == reported;
        }
        const int lat2 = int(proc->getLatencySamples());
        std::size_t bad = 0;
        for (std::size_t i = 0; i < nlive; ++i) bad += !std::isfinite(qL[i]) || !std::isfinite(qR[i]);
        const bool adopted = int(proc->getTailSamples()) == tail_want;
        std::printf("live (processor queue only): quality -> %s, bias -> 1.5%s%s: getLatencySamples %d -> %d (the rate's "
                    "constant %d throughout: %s), running valve %s, nonfinite %zu\n",
                    quality_name(target_q), step ? ", valve -> " : "", step ? valve_name(valve) : "", before, lat2,
                    reported, constant ? "yes" : "NO", adopted ? "adopted" : "NOT adopted", bad);
        ok = ok && before == reported && lat2 == reported && constant && adopted && bad == 0;
    }
    {   // the switched-to path nulls exactly against its own core (setProcessing resets the running stage)
        Settings t = s;
        t.valve = other_valve;
        t.quality = target_q;
        t.bias = 1.5;
        proc->setProcessing(false);
        proc->setProcessing(true);
        std::vector<float> tL(L.size()), tR(R.size()), xL, xR;
        if (render(proc, data, L, R, tL, tR, L.size(), nullptr, false)) return 1;
        int lat3 = 0;
        if (!reference(t, L, R, xL, xR, lat3)) { std::fprintf(stderr, "FAIL: reference prepare\n"); return 1; }
        const Diff a = compare(tL, delayed(xL, reported - lat3)), b = compare(tR, delayed(xR, reported - lat3));
        std::printf("null after the live switch to %s: max abs diff L %.3g R %.3g, latency %u (core %d + pad %d)\n",
                    describe(t).c_str(), a.max_abs, b.max_abs, unsigned(proc->getLatencySamples()), lat3, reported - lat3);
        ok = ok && a.max_abs == 0.0 && b.max_abs == 0.0 && a.nonfinite == 0 && int(proc->getLatencySamples()) == reported;
    }

    // 3. NaN host values are ignored: processor queue (Valve, 6AS6 Bias, Mode, Drive) and controller
    {
        double v0[mapvs::kNumParams], v1[mapvs::kNumParams];
        bool nan_ok = get_state(comp, v0);
        ParameterChanges changes;
        int32 idx = 0;
        for (ParamID id : {ParamID(mapvs::kValve), ParamID(mapvs::kBias6), ParamID(mapvs::kMode6), ParamID(mapvs::kDrive)})
            if (auto* q = changes.addParameterData(id, idx)) q->addPoint(0, NAN, idx);
        std::vector<float> zL(1024), zR(1024);
        if (render(proc, data, L, R, zL, zR, zL.size(), &changes, false)) return 1;
        nan_ok = nan_ok && get_state(comp, v1);
        for (int i = 0; nan_ok && i < mapvs::kNumParams; ++i) nan_ok = v1[i] == v0[i];
        IPtr<MemoryStream> st = owned(new MemoryStream);
        nan_ok = nan_ok && comp->getState(st) == kResultOk;
        st->seek(0, IBStream::kIBSeekSet, nullptr);
        nan_ok = nan_ok && comp->setState(st) == kResultOk;
        const ParamValue c0 = ctrl->getParamNormalized(mapvs::kMode6);
        nan_ok = nan_ok && ctrl->setParamNormalized(mapvs::kMode6, NAN) != kResultOk && ctrl->getParamNormalized(mapvs::kMode6) == c0;
        std::printf("NaN Valve / Bias / Mode / Drive in the processor queue, NaN Mode to the controller: ignored, the "
                    "saved state is unchanged and reloads: %s\n", nan_ok ? "yes" : "NO");
        ok = ok && nan_ok;
    }

    // 4. the controller's list values are the processor's index for any normalized value
    {
        bool agree = true;
        struct ListInfo { ParamID id; int count; const char* const* names; } lists[] = {
            {mapvs::kQuality, mapvs::kNumQualities, mapvs::kQualityNames}, {mapvs::kValve, mapvs::kNumValves, mapvs::kValveNames},
            {mapvs::kMode6, mapvs::kNumModes6, mapvs::kMode6Names}, {mapvs::kFilter6, mapvs::kNumFilters6, mapvs::kFilter6Names}};
        for (const auto& l : lists)
            for (int k = 0; k <= 100; ++k) {
                const double n = k / 100.0;
                const int want = mapvs::index_from_norm(n, l.count);
                String128 str{};
                ctrl->getParamStringByValue(l.id, n, str);
                std::string a;
                for (int i = 0; str[i]; ++i) a += char(str[i]);
                agree = agree && int(ctrl->normalizedParamToPlain(l.id, n)) == want && a == l.names[want];
            }
        std::printf("controller list display / plain value == the processor's index at 101 points per list (0.30 of 3 -> "
                    "entry 1): %s\n", agree ? "yes" : "NO");
        ok = ok && agree;
    }

    // 5. saved state (#114)
    {
        double v[mapvs::kNumParams];
        bool same = get_state(comp, v);
        for (int i = 0; same && i < mapvs::kNumParams; ++i) same = v[i] == live_norm[i];
        double old5[mapvs::kNumParamsV1];
        const bool old_reads = pre114_reads(comp, old5) && std::equal(old5, old5 + mapvs::kNumParamsV1, live_norm);
        std::printf("state after the live path switch: every value of both paths as set: %s; the pre-#114 reader takes "
                    "its 5 values from it: %s\n", same ? "yes" : "NO", old_reads ? "yes" : "NO");
        ok = ok && same && old_reads;

        // a pre-#114 state: version 1, five values and nothing else
        const double v1[mapvs::kNumParamsV1] = {nd, nb, no, nq, 0.0};
        IPtr<MemoryStream> st1 = make_state(mapvs::kStateVersion, v1, mapvs::kNumParamsV1);
        bool legacy = comp->setState(st1) == kResultOk;
        st1->seek(0, IBStream::kIBSeekSet, nullptr);
        legacy = legacy && ctrl->setComponentState(st1) == kResultOk && get_state(comp, v);
        for (int i = 0; legacy && i < mapvs::kNumParamsV1; ++i) legacy = v[i] == v1[i];
        legacy = legacy && mapvs::index_from_norm(v[mapvs::kValve], mapvs::kNumValves) == mapvs::k12AX7 &&
                 v[mapvs::kBias6] == mapvs::default_norm(mapvs::kBias6) && v[mapvs::kMode6] == mapvs::default_norm(mapvs::kMode6) &&
                 v[mapvs::kFilter6] == mapvs::default_norm(mapvs::kFilter6) &&
                 mapvs::index_from_norm(ctrl->getParamNormalized(mapvs::kValve), mapvs::kNumValves) == mapvs::k12AX7 &&
                 ctrl->getParamNormalized(mapvs::kBias6) == mapvs::default_norm(mapvs::kBias6);
        std::printf("pre-#114 state (version 1, 5 values): loads on the 12AX7 path, 6AS6 values at their defaults "
                    "(processor and controller): %s\n", legacy ? "yes" : "NO");
        ok = ok && legacy;

        // refused, state unchanged: another head version (also the 9-value version 2 of an earlier draft), a short
        // extension, a NaN value
        double before[mapvs::kNumParams];
        get_state(comp, before);
        double nan_norm[mapvs::kNumParams];
        for (int i = 0; i < mapvs::kNumParams; ++i) nan_norm[i] = norm[i];
        nan_norm[mapvs::kValve] = NAN;
        IPtr<MemoryStream> st2 = make_state(2, norm, mapvs::kNumParams);
        IPtr<MemoryStream> sts = make_state(mapvs::kStateVersion, norm, mapvs::kNumParamsV1, mapvs::kStateExtVersion, 2);
        IPtr<MemoryStream> stn = make_state(mapvs::kStateVersion, nan_norm, mapvs::kNumParamsV1, mapvs::kStateExtVersion,
                                            mapvs::kNumParamsExt);
        bool refused = comp->setState(st2) != kResultOk && comp->setState(sts) != kResultOk && comp->setState(stn) != kResultOk;
        refused = refused && get_state(comp, v) && std::equal(v, v + mapvs::kNumParams, before);
        std::printf("head version 2 / short extension / NaN value: refused, state unchanged: %s\n", refused ? "yes" : "NO");
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
