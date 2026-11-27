/* switch_cpu.cpp -- callback CPU of the engine on the 6AS6 path around live Mode switches (#115, PR #161 review).
 * A measurement tool, not a pass / fail test: timings depend on the host and its load, so run_checks.sh does not run
 * it. Prints, per configuration, the thread CPU time and wall time of each process() call (median, p95, max) and how
 * many exceed the host period, before switching and during the 30 ms of each switch.
 *
 *   map_switch_cpu [--rt] [--quality 1|2] [--block 64]
 *     --rt   run the calling thread under a Mach time-constraint policy for the period, as an audio I/O thread runs
 *            (macOS; a thread that overruns its constraint is demoted, so expect worse numbers there)
 *
 * The signal and switches follow the review's measurement: 48 kHz, stereo (L 220 Hz 0.9 FS, R 330 Hz 0.63 FS), Drive
 * 1 and 8, 30 alternating P1 <-> T switches at 0.45 mA, each followed by 10 idle blocks; with and without a bias ramp
 * (0.45 <-> 0.3 mA) started with each switch. */
#include "map_engine.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <memory>
#include <vector>
#if defined(__APPLE__)
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/thread_policy.h>
#include <pthread.h>
#endif

static double ms_cpu() { timespec t; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return double(t.tv_sec) * 1e3 + double(t.tv_nsec) * 1e-6; }
static double ms_wall() { timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return double(t.tv_sec) * 1e3 + double(t.tv_nsec) * 1e-6; }

static void time_constraint(double period_ms) {
#if defined(__APPLE__)
    mach_timebase_info_data_t tb;
    mach_timebase_info(&tb);
    auto abs = [&](double ms) { return std::uint32_t(ms * 1e6 * double(tb.denom) / double(tb.numer)); };
    thread_time_constraint_policy_data_t p{abs(period_ms), abs(0.75 * period_ms), abs(period_ms), 1};
    thread_policy_set(pthread_mach_thread_np(pthread_self()), THREAD_TIME_CONSTRAINT_POLICY, thread_policy_t(&p),
                      THREAD_TIME_CONSTRAINT_POLICY_COUNT);
#else
    (void)period_ms;
#endif
}

struct Stats { double median, p95, max; long over; std::size_t n; };
static Stats stats(std::vector<double> v, double period) {
    std::sort(v.begin(), v.end());
    const long over = long(std::count_if(v.begin(), v.end(), [&](double x) { return x > period; }));
    return {v[v.size() / 2], v[v.size() * 95 / 100], v.back(), over, v.size()};
}

int main(int argc, char** argv) {
    bool rt = false;
    int quality = mapvs::kStandard, block = 64;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--rt")) rt = true;
        else if (!std::strcmp(argv[i], "--quality") && i + 1 < argc) quality = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--block") && i + 1 < argc) block = std::max(1, std::atoi(argv[++i]));
    }
    const double fs = 48000.0, period = 1e3 * block / fs;
    if (rt) time_constraint(period);
    const int switch_blocks = int(std::ceil(0.030 * fs / block));
    std::printf("map_switch_cpu: 48 kHz, block %d (period %.3f ms), quality %d, stereo, %s\n", block, period, quality,
                rt ? "time-constraint thread" : "default thread");
    for (double drive : {1.0, 8.0})
        for (bool ramp : {false, true}) {
            auto e = std::make_unique<mapvs::Engine>();
            e->set_valve(mapvs::k6AS6);
            e->set_quality(quality);
            e->set_drive(drive);
            e->set_mode6(mapvs::kP1);
            e->set_bias6(0.45);
            e->prepare(fs);
            const std::size_t nb = std::size_t(block);
            std::vector<float> L(nb), R(nb), oL(nb), oR(nb);
            long n = 0;
            std::vector<double> bc, bw, sc, sw;
            auto run = [&](std::vector<double>* c, std::vector<double>* w) {
                for (int i = 0; i < block; ++i, ++n) {
                    L[std::size_t(i)] = float(0.9 * std::sin(2 * M_PI * 220.0 * double(n) / fs));
                    R[std::size_t(i)] = float(0.63 * std::sin(2 * M_PI * 330.0 * double(n) / fs));
                }
                const float* in[2] = {L.data(), R.data()};
                float* out[2] = {oL.data(), oR.data()};
                const double c0 = ms_cpu(), w0 = ms_wall();
                e->process(in, out, 2, block);
                const double c1 = ms_cpu(), w1 = ms_wall();
                if (c) c->push_back(c1 - c0);
                if (w) w->push_back(w1 - w0);
            };
            for (int k = 0; k < 400; ++k) run(nullptr, nullptr);
            for (int k = 0; k < 150; ++k) run(&bc, &bw);
            for (int s = 0; s < 30; ++s) {
                e->set_mode6(s % 2 ? mapvs::kP1 : mapvs::kT);
                if (ramp) e->set_bias6(s % 2 ? 0.45 : 0.3);
                for (int k = 0; k < switch_blocks; ++k) run(&sc, &sw);
                for (int k = 0; k < 10; ++k) run(nullptr, nullptr);
            }
            const Stats a = stats(bc, period), b = stats(sc, period), aw = stats(bw, period), bw2 = stats(sw, period);
            std::printf("Drive %.0f, bias ramp %s: CPU ms median / p95 / max: before %.3f / %.3f / %.3f (%.0f %% of the "
                        "period; over %ld/%zu), switching %.3f / %.3f / %.3f (%.0f %%; over %ld/%zu); wall over: before "
                        "%ld/%zu, switching %ld/%zu\n",
                        drive, ramp ? "on" : "off", a.median, a.p95, a.max, 100.0 * a.median / period, a.over, a.n, b.median,
                        b.p95, b.max, 100.0 * b.median / period, b.over, b.n, aw.over, aw.n, bw2.over, bw2.n);
            e->release();
        }
    return 0;
}
