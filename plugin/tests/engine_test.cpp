/* engine_test.cpp -- tests of mapvs::Engine, the realtime part both wrappers share (#22).
 *
 *   1. null against the direct core: 3 qualities x 44.1 / 48 / 96 kHz, stereo and mono, varying blocks (exact)
 *   2. no allocation inside process(), including a quality switch and a bias swap (operator new counted)
 *   3. bypass: fully bypassed output == input delayed by the reported latency (exact); crossfade output finite
 *   4. live quality switch and bias swap through the worker thread: adopted, latency follows, output finite
 *   5. NaN / inf input: output finite
 *   6. the Bias range ends (0.5, 2.0) build a pack and solve the DC point
 * Prints one line per check; exit status 0 if all pass.
 */
#include "../tests/null_common.h"

#include <atomic>
#include <chrono>
#include <new>
#include <thread>

// ---- allocation counter (this thread only, while armed) ----------------------------------------------------------
static thread_local bool g_armed = false;
static std::atomic<long> g_allocs{0};
void* operator new(std::size_t n) {
    if (g_armed) g_allocs.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t n) { return operator new(n); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

using namespace nulltest;

static int g_fail = 0;
static void check(bool ok, const char* what, const std::string& detail = "") {
    std::printf("%s  %s%s%s\n", ok ? "ok  " : "FAIL", what, detail.empty() ? "" : ": ", detail.c_str());
    if (!ok) ++g_fail;
}
static std::string fmt(const char* f, double a, double b = 0.0, double c = 0.0) {
    char buf[256];
    std::snprintf(buf, sizeof buf, f, a, b, c);
    return buf;
}

static void run_engine(mapvs::Engine& e, const std::vector<float>& L, const std::vector<float>& R,
                       std::vector<float>& oL, std::vector<float>& oR, int nch, bool pace = false) {
    oL.assign(L.size(), 0.0f);
    oR.assign(R.size(), 0.0f);
    std::size_t pos = 0, k = 0;
    while (pos < L.size()) {
        std::size_t n = std::size_t(block_pattern()[k++ % block_pattern().size()]);
        if (pos + n > L.size()) n = L.size() - pos;
        const float* in[2] = {L.data() + pos, R.data() + pos};
        float* out[2] = {oL.data() + pos, oR.data() + pos};
        g_armed = true;
        e.process(in, out, nch, int(n));
        g_armed = false;
        pos += n;
        if (pace) std::this_thread::sleep_for(std::chrono::microseconds(300));
    }
}

static void set_all(mapvs::Engine& e, const Settings& s) {
    e.set_drive(s.drive);
    e.set_bias(s.bias);
    e.set_output(s.output);
    e.set_quality(s.quality);
}

int main() {
    auto engine = std::make_unique<mapvs::Engine>();
    mapvs::Engine& e = *engine;

    // 1. null
    for (double fs : {44100.0, 48000.0, 96000.0}) {
        for (int q = 0; q < mapvs::kNumQualities; ++q) {
            Settings s;
            s.fs = fs;
            s.quality = q;
            s.seconds = 0.5;
            s.drive = 3.0;
            s.bias = 1.5;
            s.output = 0.75;
            std::vector<float> L, R, pL, pR, rL, rR;
            make_signal(s, L, R);
            int lat = 0;
            reference(s, L, R, rL, rR, lat);
            set_all(e, s);
            e.set_bypass(false);
            e.prepare(fs);
            run_engine(e, L, R, pL, pR, 2);
            const Diff dl = compare(pL, rL), dr = compare(pR, rR);
            check(dl.max_abs == 0.0 && dr.max_abs == 0.0 && e.latency_samples() == lat,
                  ("null stereo " + std::string(quality_name(q)) + fmt(" %.0f Hz", fs)).c_str(),
                  fmt("max abs diff L %.3g R %.3g, latency %.0f", dl.max_abs, dr.max_abs, double(e.latency_samples())));
            // mono: the left channel through the mono path equals the reference's left channel fed L on both lanes
            std::vector<float> rLm, rRm;
            reference(s, L, L, rLm, rRm, lat);
            e.prepare(fs);
            run_engine(e, L, L, pL, pR, 1);
            const Diff dm = compare(pL, rLm);
            check(dm.max_abs == 0.0, ("null mono   " + std::string(quality_name(q)) + fmt(" %.0f Hz", fs)).c_str(),
                  fmt("max abs diff %.3g", dm.max_abs));
        }
    }

    // 2.-4. bypass, live switch, allocation
    for (double fs : {44100.0, 48000.0}) {
        Settings s;
        s.fs = fs;
        s.seconds = 0.4;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(true);
        e.prepare(s.fs);
        g_allocs = 0;
        run_engine(e, L, R, pL, pR, 2);
        const int lat = e.latency_samples();
        double worst = 0.0;
        for (std::size_t i = 0; i < L.size(); ++i) {
            const double want = i >= std::size_t(lat) ? double(L[i - std::size_t(lat)]) : 0.0;
            worst = std::fmax(worst, std::fabs(double(pL[i]) - want));
        }
        check(worst == 0.0, "bypassed output == input delayed by the latency", fmt("max abs diff %.3g", worst));

        // bypass off mid-signal: crossfade, finite
        e.set_bypass(false);
        run_engine(e, L, R, pL, pR, 2);
        bool finite = true;
        for (float v : pL) finite = finite && std::isfinite(v);
        check(finite, "bypass crossfade output finite");

        // live quality switch + bias swap; realtime-paced so the worker gets to run
        e.set_quality(mapvs::kHQ);
        e.set_bias(2.0);
        bool adopted = false;
        for (int tries = 0; tries < 40 && !adopted; ++tries) {
            run_engine(e, L, R, pL, pR, 2, true);
            adopted = e.active_quality() == mapvs::kHQ && e.applied_bias() == 2.0;
        }
        finite = true;
        for (float v : pL) finite = finite && std::isfinite(v);
        check(adopted && e.active_latency_samples() == mapvs::Engine::latency_for(mapvs::kHQ, s.fs)
              && e.latency_samples() == e.active_latency_samples() && e.bias_status() == 0,
              "live quality switch to HQ and bias swap to 2.0 adopted",
              fmt("active quality %.0f, active latency %.0f, bias status %.0f", e.active_quality(),
                  e.active_latency_samples(), e.bias_status()));
        check(finite, "output finite through the switch");
        e.set_quality(mapvs::kEco);
        e.set_bias(0.5);
        adopted = false;
        for (int tries = 0; tries < 40 && !adopted; ++tries) {
            run_engine(e, L, R, pL, pR, 2, true);
            adopted = e.active_quality() == mapvs::kEco && e.applied_bias() == 0.5;
        }
        check(adopted && e.active_latency_samples() == mapvs::Engine::latency_for(mapvs::kEco, s.fs)
              && e.latency_samples() == e.active_latency_samples(),
              "live switch back to Eco and bias 0.5 adopted");
        check(g_allocs.load() == 0, "no allocation in process() (bypass, crossfade, switches, swaps)",
              fmt("%.0f allocations", double(g_allocs.load())));
    }

    // 5. non-finite input
    {
        Settings s;
        s.seconds = 0.1;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        for (std::size_t i = 0; i < L.size(); i += 97) { L[i] = NAN; R[i] = INFINITY; }
        set_all(e, s);
        e.set_bypass(false);
        e.prepare(s.fs);
        run_engine(e, L, R, pL, pR, 2);
        bool finite = true;
        for (std::size_t i = 0; i < L.size(); ++i) finite = finite && std::isfinite(pL[i]) && std::isfinite(pR[i]);
        check(finite, "NaN / inf input -> finite output");
    }

    // 6. the Bias range ends
    for (double b : {mapvs::kBiasRange.min, mapvs::kBiasRange.max}) {
        dkcore::StereoStage st;
        const auto r = st.prepare(48000.0, dkcore::STANDARD, b);
        check(r == dkcore::BuildStatus::ok, fmt("bias %.2f builds and solves the DC point", b).c_str());
    }

    e.release();
    std::printf("%s (%d failed)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
