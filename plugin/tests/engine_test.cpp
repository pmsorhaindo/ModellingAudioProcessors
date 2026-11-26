/* engine_test.cpp -- tests of mapvs::Engine, the realtime part both wrappers share (#22, #114).
 *
 *   1. null against the direct core, each path (exact, the existing convention):
 *      12AX7  3 qualities x 44.1 / 48 / 96 kHz, stereo and mono, varying blocks
 *      6AS6   3 qualities (Eco runs Standard) x 44.1 / 48 / 96 kHz, stereo and mono; and at 48 kHz T at 0.6 mA (built
 *             at the 0.5 mA cap), T with the 4k filter, P2 (experimental) with the 7k filter, P1 0.6 mA with 7k
 *   2. bypass, each path: fully bypassed output == input delayed by the reported latency (exact); crossfade finite
 *   3. live switches through the worker, realtime-paced: 12AX7 quality / bias, valve 12AX7 -> 6AS6, 6AS6 mode / bias /
 *      quality / filter, 6AS6 -> 12AX7. Each is adopted, the latency follows, the output stays finite, and process()
 *      neither allocates (operator new counted) nor locks, waits or makes a listed syscall (rt_hooks.cpp)
 *   4. after a live path switch, each path nulls against its own core again (after request_reset); a filter-only
 *      change on the running 6AS6 stage nulls against the core switched at the same block
 *   5. NaN / inf input, each path: output finite
 *   6. NaN / inf and out-of-range parameter values: sanitized, output finite; a host rate the 6AS6 core refuses runs
 *      the 12AX7 path
 *   7. the 12AX7 Bias range ends (0.5, 2.0) build a pack and solve the DC point; the T cap is the core's envelope
 *   8. saved state: VST 3 state decoding (no extension -> the 12AX7 path, the #114 extension, refused values),
 *      normalized -> index conversions (NaN, between entries) and sanitize_plain (the AU's)
 * Prints one line per check; exit status 0 if all pass.
 */
#include "../tests/null_common.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <new>
#include <thread>

// ---- rt_hooks.cpp (linked dylib): locks, waits, allocations, syscalls on the armed thread -------------------------
extern "C" {
void map_rt_arm(bool on);
long map_rt_count();
void map_rt_clear();
const char* map_rt_last();
}

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
static void check(bool ok, const std::string& what, const std::string& detail = "") { check(ok, what.c_str(), detail); }
static std::string fmt(const char* f, double a, double b = 0.0, double c = 0.0) {
    char buf[256];
    std::snprintf(buf, sizeof buf, f, a, b, c);
    return buf;
}

// process() with both counters armed on this thread
static void process_armed(mapvs::Engine& e, const float* const* in, float* const* out, int nch, int n) {
    g_armed = true;
    map_rt_arm(true);
    e.process(in, out, nch, n);
    map_rt_arm(false);
    g_armed = false;
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
        process_armed(e, in, out, nch, int(n));
        pos += n;
        if (pace) std::this_thread::sleep_for(std::chrono::microseconds(300));
    }
}

static void set_all(mapvs::Engine& e, const Settings& s) {
    e.set_valve(s.valve);
    e.set_drive(s.drive);
    e.set_bias(s.bias);
    e.set_output(s.output);
    e.set_quality(s.quality);
    e.set_bias6(s.bias6);
    e.set_mode6(s.mode6);
    e.set_filter6(s.filter6);
}

static bool all_finite(const std::vector<float>& a, const std::vector<float>& b) {
    for (std::size_t i = 0; i < a.size(); ++i)
        if (!std::isfinite(a[i]) || !std::isfinite(b[i])) return false;
    return true;
}

// stereo and mono null of the engine (prepared with s) against the path's core
static void null_case(mapvs::Engine& e, const Settings& s, const std::string& label) {
    std::vector<float> L, R, pL, pR, rL, rR;
    make_signal(s, L, R);
    int lat = 0;
    if (!reference(s, L, R, rL, rR, lat)) { check(false, "reference prepare " + label); return; }
    set_all(e, s);
    e.set_bypass(false);
    e.prepare(s.fs);
    run_engine(e, L, R, pL, pR, 2);
    const Diff dl = compare(pL, rL), dr = compare(pR, rR);
    check(dl.max_abs == 0.0 && dr.max_abs == 0.0 && dl.nonfinite == 0 && dr.nonfinite == 0 && e.latency_samples() == lat &&
              e.active_latency_samples() == lat && e.active_valve() == s.valve && e.build_status() == 0,
          "null stereo " + label,
          fmt("max abs diff L %.3g R %.3g, latency %.0f", dl.max_abs, dr.max_abs, double(e.latency_samples())) +
              fmt(" (core %.0f), ref peak %.3g", double(lat), dl.peak_ref));
    // mono: the left channel through the mono path equals the reference's left channel fed L on both lanes
    std::vector<float> rLm, rRm;
    reference(s, L, L, rLm, rRm, lat);
    e.prepare(s.fs);
    run_engine(e, L, L, pL, pR, 1);
    const Diff dm = compare(pL, rLm);
    check(dm.max_abs == 0.0 && dm.nonfinite == 0, "null mono   " + label, fmt("max abs diff %.3g", dm.max_abs));
}

// realtime-paced until adopted(e) or ~40 passes; returns whether adopted; out: the last pass's output
template <class Pred>
static bool run_until(mapvs::Engine& e, const std::vector<float>& L, const std::vector<float>& R, std::vector<float>& pL,
                      std::vector<float>& pR, bool& finite, Pred adopted) {
    finite = true;
    for (int tries = 0; tries < 40; ++tries) {
        run_engine(e, L, R, pL, pR, 2, true);
        finite = finite && all_finite(pL, pR);
        if (adopted()) {
            run_engine(e, L, R, pL, pR, 2, true);       // and one more pass on the adopted slot (fade-in done)
            finite = finite && all_finite(pL, pR);
            return true;
        }
    }
    return false;
}

int main() {
    auto engine = std::make_unique<mapvs::Engine>();
    mapvs::Engine& e = *engine;
    using mapvs::Engine;

    // 0. the tripwire sees what it should (else a zero count below would prove nothing)
    {
        std::mutex m;
        map_rt_clear();
        map_rt_arm(true);
        m.lock();
        m.unlock();
        void* volatile p = std::malloc(16);
        std::free(p);
        map_rt_arm(false);
        const long n = map_rt_count();
        check(n >= 3, "rt hooks count a std::mutex lock, malloc and free on the armed thread", fmt("%.0f calls", double(n)));
        map_rt_clear();
    }

    // 1. null, 12AX7
    for (double fs : {44100.0, 48000.0, 96000.0}) {
        for (int q = 0; q < mapvs::kNumQualities; ++q) {
            Settings s;
            s.valve = mapvs::k12AX7;
            s.fs = fs;
            s.quality = q;
            s.seconds = 0.5;
            s.drive = 3.0;
            s.bias = 1.5;
            s.output = 0.75;
            null_case(e, s, describe(s));
        }
    }
    // 1. null, 6AS6
    for (double fs : {44100.0, 48000.0, 96000.0}) {
        for (int q = 0; q < mapvs::kNumQualities; ++q) {
            Settings s;
            s.valve = mapvs::k6AS6;
            s.fs = fs;
            s.quality = q;
            s.seconds = 0.25;
            s.drive = 3.0;
            s.output = 0.75;
            s.bias6 = 0.3;
            null_case(e, s, describe(s));
            check(e.active_quality() == (q == mapvs::kHQ ? mapvs::kHQ : mapvs::kStandard) && !e.active_experimental(),
                  "  6AS6 runs " + std::string(quality_name(e.active_quality())) + " for " + quality_name(q) + ", supported");
        }
    }
    {
        struct Case { int mode; double bias; int filter; double want_bias; bool experimental; };
        const Case cases[] = {
            {mapvs::kT, 0.6, mapvs::kFilterOff, 0.5, false},   // the T cap: 0.6 mA requested, 0.5 mA built
            {mapvs::kT, 0.25, mapvs::kFilter4k, 0.25, false},
            {mapvs::kP2, 0.45, mapvs::kFilter7k, 0.45, true},  // P2: experimental only
            {mapvs::kP1, 0.6, mapvs::kFilter7k, 0.6, false},
        };
        for (const Case& c : cases) {
            Settings s;
            s.valve = mapvs::k6AS6;
            s.seconds = 0.25;
            s.drive = 4.0;
            s.mode6 = c.mode;
            s.bias6 = c.bias;
            s.filter6 = c.filter;
            null_case(e, s, describe(s));
            check(e.active_bias6() == c.want_bias && e.active_mode6() == c.mode && e.active_experimental() == c.experimental,
                  "  built at " + fmt("%.2f mA", c.want_bias) + (c.experimental ? ", experimental" : ", supported"),
                  fmt("active bias %.3g mA, mode %.0f, experimental %.0f", e.active_bias6(), double(e.active_mode6()),
                      double(e.active_experimental())));
        }
    }

    // 2. bypass, each path
    for (int valve : {mapvs::k12AX7, mapvs::k6AS6}) {
        Settings s;
        s.valve = valve;
        s.seconds = 0.2;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(true);
        e.prepare(s.fs);
        g_allocs = 0;
        map_rt_clear();
        run_engine(e, L, R, pL, pR, 2);
        const int lat = e.latency_samples();
        double worst = 0.0;
        for (std::size_t i = 0; i < L.size(); ++i) {
            const double want = i >= std::size_t(lat) ? double(L[i - std::size_t(lat)]) : 0.0;
            worst = std::fmax(worst, std::fabs(double(pL[i]) - want));
        }
        check(worst == 0.0, std::string(valve_name(valve)) + ": bypassed output == input delayed by the latency",
              fmt("max abs diff %.3g, latency %.0f", worst, double(lat)));
        e.set_bypass(false);
        run_engine(e, L, R, pL, pR, 2);
        check(all_finite(pL, pR), std::string(valve_name(valve)) + ": bypass crossfade output finite");
        check(g_allocs.load() == 0 && map_rt_count() == 0, std::string(valve_name(valve)) + ": bypass: no allocation, lock or syscall",
              fmt("%.0f allocations, %.0f hooked calls ", double(g_allocs.load()), double(map_rt_count())) + map_rt_last());
    }

    // 3. live switches, 4. null after a switch
    for (double fs : {44100.0, 48000.0}) {
        Settings s;
        s.fs = fs;
        s.seconds = 0.3;
        s.valve = mapvs::k12AX7;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(false);
        e.prepare(s.fs);
        g_allocs = 0;
        map_rt_clear();
        const std::string at = fmt(" (%.0f Hz)", fs);
        bool finite = true;

        e.set_quality(mapvs::kHQ);
        e.set_bias(2.0);
        bool ok = run_until(e, L, R, pL, pR, finite, [&] { return e.active_quality() == mapvs::kHQ && e.applied_bias() == 2.0; });
        check(ok && finite && e.active_latency_samples() == Engine::latency_for(mapvs::k12AX7, mapvs::kHQ, fs) &&
                  e.latency_samples() == e.active_latency_samples() && e.build_status() == 0,
              "live 12AX7: quality -> HQ, bias -> 2.0 adopted, latency follows, finite" + at);

        e.set_valve(mapvs::k6AS6);
        e.set_mode6(mapvs::kP1);
        e.set_bias6(0.45);
        e.set_quality(mapvs::kStandard);
        ok = run_until(e, L, R, pL, pR, finite, [&] { return e.active_valve() == mapvs::k6AS6; });
        check(ok && finite && e.active_quality() == mapvs::kStandard &&
                  e.active_latency_samples() == Engine::latency_for(mapvs::k6AS6, mapvs::kStandard, fs) &&
                  e.latency_samples() == e.active_latency_samples() && e.build_status() == 0,
              "live valve 12AX7 -> 6AS6 adopted, latency follows, finite" + at,
              fmt("latency %.0f -> %.0f", double(Engine::latency_for(mapvs::k12AX7, mapvs::kHQ, fs)),
                  double(e.active_latency_samples())));

        e.set_mode6(mapvs::kT);
        e.set_bias6(0.6);
        ok = run_until(e, L, R, pL, pR, finite, [&] { return e.active_mode6() == mapvs::kT; });
        check(ok && finite && e.active_bias6() == mapvs::kTBiasCapMa && !e.active_experimental(),
              "live 6AS6 mode -> T, bias -> 0.6 mA: built at the 0.5 mA cap, supported, finite" + at,
              fmt("active bias %.3g mA", e.active_bias6()));

        e.set_mode6(mapvs::kP2);
        e.set_quality(mapvs::kHQ);
        e.set_filter6(mapvs::kFilter4k);
        ok = run_until(e, L, R, pL, pR, finite, [&] { return e.active_mode6() == mapvs::kP2 && e.active_quality() == mapvs::kHQ; });
        check(ok && finite && e.active_experimental() && e.active_bias6() == 0.6 &&
                  e.active_latency_samples() == Engine::latency_for(mapvs::k6AS6, mapvs::kHQ, fs),
              "live 6AS6 mode -> P2 (experimental, 0.6 mA: the cap is T's), quality -> HQ, filter -> 4k, finite" + at);

        // Eco on the 6AS6 path is Standard: a change between them builds nothing (same config)
        e.set_quality(mapvs::kEco);
        ok = run_until(e, L, R, pL, pR, finite, [&] { return e.active_quality() == mapvs::kStandard; });
        check(ok && finite && e.latency_samples() == Engine::latency_for(mapvs::k6AS6, mapvs::kStandard, fs),
              "live 6AS6 quality -> Eco runs Standard" + at);

        e.set_valve(mapvs::k12AX7);
        e.set_bias(0.5);
        ok = run_until(e, L, R, pL, pR, finite,
                       [&] { return e.active_valve() == mapvs::k12AX7 && e.active_quality() == mapvs::kEco && e.applied_bias() == 0.5; });
        check(ok && finite && e.active_latency_samples() == Engine::latency_for(mapvs::k12AX7, mapvs::kEco, fs) &&
                  e.latency_samples() == e.active_latency_samples(),
              "live valve 6AS6 -> 12AX7 (Eco, bias 0.5) adopted, latency follows, finite" + at);

        check(g_allocs.load() == 0, "no allocation in process() through every switch" + at,
              fmt("%.0f allocations", double(g_allocs.load())));
        check(map_rt_count() == 0, "no lock, wait or listed syscall in process() through every switch" + at,
              fmt("%.0f hooked calls ", double(map_rt_count())) + map_rt_last());

        // 4. each path nulls against its own core after a live switch: reset the adopted slot, then compare
        for (int valve : {mapvs::k6AS6, mapvs::k12AX7}) {
            Settings t = s;
            t.valve = valve;
            t.quality = mapvs::kStandard;
            t.bias = 1.25;
            t.mode6 = mapvs::kT;
            t.bias6 = 0.35;
            t.filter6 = mapvs::kFilter7k;
            Settings first = t;                         // every other value first, then the valve: the worker builds
            first.valve = e.valve_target();             // the new path with them (12AX7: prepared at bias 1.25)
            set_all(e, first);
            e.set_valve(valve);
            ok = run_until(e, L, R, pL, pR, finite, [&] {
                return e.active_valve() == valve && e.active_quality() == mapvs::kStandard &&
                       (valve == mapvs::k12AX7 ? e.applied_bias() == 1.25 : e.active_bias6() == 0.35);
            });
            e.request_reset();
            std::vector<float> rL, rR;
            int lat = 0;
            reference(t, L, R, rL, rR, lat);
            run_engine(e, L, R, pL, pR, 2);
            const Diff dl = compare(pL, rL), dr = compare(pR, rR);
            check(ok && dl.max_abs == 0.0 && dr.max_abs == 0.0 && e.active_latency_samples() == lat,
                  "null after a live switch to " + describe(t), fmt("max abs diff L %.3g R %.3g", dl.max_abs, dr.max_abs));
        }
    }

    // 4b. a filter-only change on the running 6AS6 stage (no rebuild): fixed 256-sample blocks so the engine's chunks
    // and the reference's blocks coincide; the reference core gets set_lpf before the same block
    for (int to : {mapvs::kFilter7k, mapvs::kFilter4k}) {
        Settings s;
        s.valve = mapvs::k6AS6;
        s.seconds = 0.25;
        s.drive = 4.0;
        s.filter6 = mapvs::kFilterOff;
        std::vector<float> L, R;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(false);
        e.prepare(s.fs);
        auto ref = std::make_unique<dk6as6::Stage[]>(2);
        const dk6as6::Settings set = settings_6as6(s);
        for (int c = 0; c < 2; ++c) {
            ref[c].prepare(s.fs, quality_6as6(s.quality), set);
            ref[c].set_drive(s.drive * mapvs::kVoltsPerFullScale);
            ref[c].set_output(s.output * mapvs::kFullScalePerVolt);
        }
        const std::size_t blk = std::size_t(Engine::kChunk), at = 40 * blk;   // change at 0.21 s, > 15 ms before the end
        std::vector<float> pL(L.size()), pR(R.size()), rL(L.size()), rR(R.size()), uL(L.size()), uR(R.size());
        Settings off = s;
        int lat = 0;
        reference(off, L, R, uL, uR, lat);                // unchanged filter, for "the change took effect"
        g_allocs = 0;
        map_rt_clear();
        for (std::size_t pos = 0; pos < L.size(); pos += blk) {
            const std::size_t n = std::min(blk, L.size() - pos);
            if (pos == at) {
                e.set_filter6(to);
                for (int c = 0; c < 2; ++c) ref[c].set_lpf(to == mapvs::kFilter7k ? dk6as6::Lpf::k7 : dk6as6::Lpf::k4);
            }
            const float* in[2] = {L.data() + pos, R.data() + pos};
            float* out[2] = {pL.data() + pos, pR.data() + pos};
            process_armed(e, in, out, 2, int(n));
            ref[0].process(L.data() + pos, rL.data() + pos, n);
            ref[1].process(R.data() + pos, rR.data() + pos, n);
        }
        const Diff dl = compare(pL, rL), dr = compare(pR, rR);
        double moved = 0.0;
        for (std::size_t i = at + std::size_t(0.015 * s.fs) + std::size_t(lat); i < L.size(); ++i)
            moved = std::fmax(moved, std::fabs(double(pL[i]) - double(uL[i])));
        check(dl.max_abs == 0.0 && dr.max_abs == 0.0 && moved > 1e-3 && e.active_valve() == mapvs::k6AS6 &&
                  g_allocs.load() == 0 && map_rt_count() == 0,
              std::string("live 6AS6 filter-only change Off -> ") + mapvs::kFilter6Names[to] +
                  " on the running stage: exact null vs the core switched at the same block, no allocation / lock",
              fmt("max abs diff L %.3g R %.3g; differs from the unfiltered output by up to %.3g after the 15 ms fade",
                  dl.max_abs, dr.max_abs, moved));
    }

    // 5. non-finite input, each path
    for (int valve : {mapvs::k12AX7, mapvs::k6AS6}) {
        Settings s;
        s.valve = valve;
        s.seconds = 0.1;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        for (std::size_t i = 0; i < L.size(); i += 97) { L[i] = NAN; R[i] = INFINITY; }
        set_all(e, s);
        e.set_bypass(false);
        e.prepare(s.fs);
        run_engine(e, L, R, pL, pR, 2);
        check(all_finite(pL, pR), std::string(valve_name(valve)) + ": NaN / inf input -> finite output");
    }

    // 6. NaN / inf and out-of-range parameter values
    {
        Settings s;
        s.valve = mapvs::k6AS6;
        s.seconds = 0.1;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(false);
        e.prepare(s.fs);
        e.set_drive(NAN);                               // ignored: Drive stays 2
        e.set_output(INFINITY);                         // clamped to 4
        e.set_bias(-1e300);                             // clamped to 0.5
        e.set_bias6(NAN);                               // ignored: stays 0.3 mA
        e.set_valve(7);                                 // clamped to the last valve (12AX7)
        e.set_valve(-3);                                // clamped to the first (6AS6)
        e.set_mode6(99);                                // clamped to P2
        e.set_mode6(-1);                                // clamped to P1
        e.set_filter6(42);
        e.set_quality(-5);
        const Engine::Config c = e.target_config();
        check(c.valve == mapvs::k6AS6 && c.mode6 == mapvs::kP1 && c.bias6 == 0.3 && c.quality == mapvs::kStandard,
              "NaN / inf and out-of-range parameter values: the targets stay valid",
              fmt("valve %.0f, mode %.0f, bias %.3g mA", double(c.valve), double(c.mode6), c.bias6));
        bool finite = true;
        run_until(e, L, R, pL, pR, finite, [] { return true; });
        check(finite, "  output finite");
    }

    // 6b. a host rate the 6AS6 core refuses (the Steinberg validator's 1234.5678 Hz): the 12AX7 path runs instead
    {
        Settings s;
        s.valve = mapvs::k6AS6;
        s.fs = 1234.5678;
        s.seconds = 0.5;
        std::vector<float> L, R, pL, pR;
        make_signal(s, L, R);
        set_all(e, s);
        e.set_bypass(false);
        const bool ok = e.prepare(s.fs);
        bool finite = true;
        run_until(e, L, R, pL, pR, finite, [] { return true; });
        check(ok && e.active_valve() == mapvs::k12AX7 && e.build_status() != 0 && e.build_status_valve() == mapvs::k6AS6 &&
                  finite && e.active_latency_samples() == Engine::latency_for(mapvs::k12AX7, s.quality, s.fs),
              "6AS6 at 1234.5678 Hz (the core refuses the rate): prepare succeeds on the 12AX7 path, finite",
              fmt("6AS6 status %.0f", double(e.build_status())));
    }

    // 7. the 12AX7 Bias range ends; the T cap is the core's supported envelope
    for (double b : {mapvs::kBiasRange.min, mapvs::kBiasRange.max}) {
        dkcore::StereoStage st;
        const auto r = st.prepare(48000.0, dkcore::STANDARD, b);
        check(r == dkcore::BuildStatus::ok, fmt("12AX7 bias %.2f builds and solves the DC point", b));
    }
    {
        double lo = 0.0, hi = 0.0;
        const bool known = dk6as6::envelope(dk6as6::Mode::T, dk6as6::HARDNESS, lo, hi);
        dk6as6::Settings t;
        t.mode = dk6as6::Mode::T;
        t.bias_ma = 0.6;
        auto st = std::make_unique<dk6as6::Stage>();
        const auto r = st->prepare(48000.0, dk6as6::STANDARD, t);
        check(known && hi == mapvs::kTBiasCapMa && r == dk6as6::Status::unsupported_setting &&
                  mapvs::bias6_effective(mapvs::kT, 0.6) == 0.5 && mapvs::bias6_effective(mapvs::kP1, 0.6) == 0.6,
              "T cap 0.5 mA = the core's T envelope (the core refuses T 0.6 mA unless experimental)",
              fmt("envelope %.2f-%.2f mA", lo, hi));
        check(!dk6as6::supported(dk6as6::Mode::P2, 0.45) && dk6as6::supported(dk6as6::Mode::P1, 0.6),
              "P2 is outside the supported envelope (experimental only); P1 covers the whole Bias range");
    }

    // 8. saved state
    {
        using namespace mapvs;
        double norm[kNumParams];
        for (double& v : norm) v = -1.0;
        const double v1[kNumParamsV1] = {0.25, 0.75, 0.5, 0.0, 1.0};
        bool ok = decode_state(v1, nullptr, norm);
        bool same = true;
        for (int i = 0; i < kNumParamsV1; ++i) same = same && norm[i] == v1[i];
        check(ok && same && index_from_norm(norm[kValve], kNumValves) == k12AX7 &&
                  norm[kBias6] == default_norm(kBias6) && norm[kMode6] == default_norm(kMode6) &&
                  norm[kFilter6] == default_norm(kFilter6),
              "state without the #114 extension (pre-#114): its 5 values, the 12AX7 path, 6AS6 values at their defaults");

        double ext[kNumParamsExt];
        for (int i = 0; i < kNumParamsExt; ++i) ext[i] = double(i + 1) / 10.0;
        ext[kValve - kNumParamsV1] = index_to_norm(k6AS6, kNumValves);
        ok = decode_state(v1, ext, norm);
        same = true;
        for (int i = 0; i < kNumParamsV1; ++i) same = same && norm[i] == v1[i];
        for (int i = 0; i < kNumParamsExt; ++i) same = same && norm[kNumParamsV1 + i] == ext[i];
        check(ok && same, "state with the #114 extension: every value of both paths round-trips");

        double before[kNumParams];
        for (int i = 0; i < kNumParams; ++i) before[i] = norm[i];
        double bad_ext[kNumParamsExt];
        for (double& v : bad_ext) v = 0.5;
        bad_ext[kBias6 - kNumParamsV1] = NAN;
        const bool r_nan = decode_state(v1, bad_ext, norm);
        bad_ext[kBias6 - kNumParamsV1] = INFINITY;
        const bool r_inf = decode_state(v1, bad_ext, norm);
        double bad_base[kNumParamsV1] = {0.5, 0.5, NAN, 0.5, 0.0};
        const bool r_base = decode_state(bad_base, nullptr, norm);
        same = true;
        for (int i = 0; i < kNumParams; ++i) same = same && norm[i] == before[i];
        check(!r_nan && !r_inf && !r_base && same,
              "state refused, nothing changed: a NaN or inf value in the head or the extension (stream framing: null_vst3)");

        double wide[kNumParamsV1], wide_ext[kNumParamsExt];
        for (int i = 0; i < kNumParamsV1; ++i) wide[i] = i % 2 ? 7.0 : -7.0;
        for (int i = 0; i < kNumParamsExt; ++i) wide_ext[i] = (kNumParamsV1 + i) % 2 ? 7.0 : -7.0;
        ok = decode_state(wide, wide_ext, norm);
        same = true;
        for (int i = 0; i < kNumParams; ++i) same = same && norm[i] == (i % 2 ? 1.0 : 0.0);
        check(ok && same, "state values outside 0..1 are clamped");

        // conversions are defined for NaN (no NaN reaches an integer cast), and lists round to the nearest entry
        check(clamp01(NAN) == 0.0 && index_from_norm(NAN, kNumModes6) == 0 && to_plain(kBias6Range, NAN) == kBias6Range.min &&
                  index_from_norm(0.30, kNumModes6) == kT && index_from_norm(0.24, kNumModes6) == kP1 &&
                  index_from_norm(0.76, kNumFilters6) == kFilter7k && quality_from_norm(0.30) == kStandard,
              "normalized -> index: NaN -> the first entry; between entries, the nearest (0.30 of 3 -> entry 1)");

        // AU: any restored float lands on a valid plain value
        same = true;
        for (std::uint32_t id = 0; id < kNumParams; ++id) {
            same = same && sanitize_plain(id, NAN) == default_plain(id) && sanitize_plain(id, default_plain(id)) == default_plain(id);
            const double hi = sanitize_plain(id, 1e30), lo = sanitize_plain(id, -1e30);
            same = same && std::isfinite(hi) && std::isfinite(lo) && lo <= hi;
        }
        check(same && sanitize_plain(kValve, 0.6) == k12AX7 && sanitize_plain(kMode6, 1.4) == kT &&
                  sanitize_plain(kBias6, 0.05) == 0.1 && sanitize_plain(kBias6, INFINITY) == 0.6 &&
                  sanitize_plain(kDrive, 9.0) == 8.0 && sanitize_plain(kBypass, 0.7) == 1.0,
              "AU values: NaN -> the default, out of range -> clamped, list values rounded");
    }

    e.release();
    std::printf("%s (%d failed)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
