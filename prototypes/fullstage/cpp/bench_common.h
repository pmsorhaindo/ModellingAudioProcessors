/* bench_common.h -- the #20 benchmark method shared by cpp/bench.cpp (12AX7 core) and cpp/bench_6as6.cpp (6AS6 core,
 * #104): the test signal, the cycle / instruction counters, the thread CPU clock and the percentiles. Method and
 * caveats: cpp/BENCHMARK.md. Include once, in the benchmark's translation unit. */
#ifndef DKCORE_BENCH_COMMON_H
#define DKCORE_BENCH_COMMON_H

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>

#if defined(__APPLE__)
#include <libproc.h>
#include <pthread.h>
#include <sys/resource.h>
#include <unistd.h>
#elif defined(__linux__)
#include <cerrno>
#include <linux/perf_event.h>
#include <sched.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

static constexpr double FS = 48000.0;

// ---- the test signal ------------------------------------------------------------------------------------------
// A 2 s guitar-like DI phrase, repeated: Karplus-Strong plucks (an LCG noise burst in a one-period delay line with
// the two-point average in the loop). Low E sustained, single notes, a six-string E minor strum (8 ms between
// strings), a lead A4 over it, and a release to near silence. Notes are damped at their end, as by a palm or a
// lifted finger. Deterministic: the same samples on every machine. The whole signal is scaled to the given peak.
struct Note { double t, f, dur, amp; };
static const Note PHRASE[] = {
    {0.000, 82.41, 0.48, 1.0},  {0.250, 123.47, 0.23, 0.6}, {0.500, 164.81, 0.23, 0.7}, {0.750, 146.83, 0.23, 0.8},
    {1.000, 82.41, 0.75, 0.5},  {1.008, 123.47, 0.74, 0.5}, {1.016, 164.81, 0.73, 0.5}, {1.024, 196.00, 0.72, 0.5},
    {1.032, 246.94, 0.71, 0.5}, {1.040, 329.63, 0.70, 0.5}, {1.500, 440.00, 0.40, 0.8},
};
static constexpr double PHRASE_S = 2.0, RHO_RING = 0.996, RHO_DAMP = 0.6, DAMP_S = 0.15;   // rho per period

static std::vector<double> make_signal(std::size_t n, double peak, std::uint32_t seed) {
    std::vector<double> x(n, 0.0);
    std::uint32_t r = seed;
    auto uni = [&r] { r = r * 1664525u + 1013904223u; return double(r >> 8) / 8388608.0 - 1.0; };
    std::vector<double> d;
    for (int rep = 0; rep * PHRASE_S * FS < double(n); ++rep)
        for (const Note& nt : PHRASE) {
            const std::size_t p = std::size_t(std::lround(FS / nt.f));
            d.assign(p, 0.0);
            for (double& v : d) v = nt.amp * uni();
            for (std::size_t i = p - 1; i > 0; --i) d[i] = 0.5 * (d[i] + d[i - 1]);   // soften the pick
            const std::size_t t0 = std::size_t((rep * PHRASE_S + nt.t) * FS), t1 = t0 + std::size_t(nt.dur * FS);
            const std::size_t t2 = std::min(n, t1 + std::size_t(DAMP_S * FS));
            // rho is the loss per pass through the loop (once per period), applied per sample as rho^(1/p)
            const double ring = std::pow(RHO_RING, 1.0 / double(p)), damp = std::pow(RHO_DAMP, 1.0 / double(p));
            for (std::size_t i = t0, k = 0; i < t2; ++i, k = (k + 1) % p) {
                x[i] += d[k];
                d[k] = (i < t1 ? ring : damp) * 0.5 * (d[k] + d[(k + 1) % p]);
            }
        }
    double m = 0.0;
    for (double v : x) m = std::max(m, std::fabs(v));
    for (double& v : x) v *= peak / m;
    return x;
}

// ---- counters -------------------------------------------------------------------------------------------------
struct Cnt { std::uint64_t cycles = 0, instr = 0, pcycles = 0; bool ok = false; };

#if defined(__APPLE__)
static const char* COUNTER_SOURCE = "proc_pid_rusage RUSAGE_INFO_V6 ri_cycles / ri_instructions / ri_pcycles";
static bool counters_init() { return true; }
static Cnt counters_read() {
    rusage_info_v6 u{};
    Cnt c;
    if (proc_pid_rusage(getpid(), RUSAGE_INFO_V6, reinterpret_cast<rusage_info_t*>(&u)) != 0) return c;
    c.cycles = u.ri_cycles; c.instr = u.ri_instructions; c.pcycles = u.ri_pcycles; c.ok = true;
    return c;
}
#elif defined(__linux__)
static const char* COUNTER_SOURCE = "perf_event_open cycles / instructions, this thread, user space";
static int g_fd = -1, g_nr = 0;
static std::uint64_t g_ena = 0, g_run = 0;
static std::string g_counter_error;
static int perf_open(std::uint64_t config, int group) {
    perf_event_attr a;
    std::memset(&a, 0, sizeof a);
    a.size = sizeof a;
    a.type = PERF_TYPE_HARDWARE;
    a.config = config;
    a.exclude_kernel = 1;
    a.exclude_hv = 1;
    a.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
    return int(syscall(SYS_perf_event_open, &a, 0, -1, group, 0));
}
static bool counters_init() {
    g_fd = perf_open(PERF_COUNT_HW_CPU_CYCLES, -1);
    if (g_fd < 0) { g_counter_error = std::strerror(errno); return false; }
    g_nr = perf_open(PERF_COUNT_HW_INSTRUCTIONS, g_fd) >= 0 ? 2 : 1;
    return true;
}
static Cnt counters_read() {
    struct { std::uint64_t nr, ena, run, v[2]; } r{};
    Cnt c;
    if (g_fd < 0 || read(g_fd, &r, sizeof r) < ssize_t(3 * sizeof(std::uint64_t) + std::size_t(g_nr) * 8)) return c;
    c.cycles = r.v[0]; c.instr = g_nr > 1 ? r.v[1] : 0;
    g_ena = r.ena; g_run = r.run;
    c.ok = true;
    return c;
}
#else
static const char* COUNTER_SOURCE = "none";
static bool counters_init() { return false; }
static Cnt counters_read() { return {}; }
#endif

static double thread_cpu_ns() {
    timespec t{};
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t);
    return double(t.tv_sec) * 1e9 + double(t.tv_nsec);
}

static double pct(std::vector<double> v, double p) {          // nearest rank
    std::sort(v.begin(), v.end());
    std::size_t i = std::size_t(std::ceil(p / 100.0 * double(v.size())));
    return v[std::min(v.size() - 1, i ? i - 1 : 0)];
}
static double median(const std::vector<double>& v) { return pct(v, 50.0); }
static void jnum(const char* k, double v, const char* fmt = "%.4g") {
    std::printf("\"%s\": ", k);
    if (std::isfinite(v)) std::printf(fmt, v); else std::printf("null");
}

#endif
