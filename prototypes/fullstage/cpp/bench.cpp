/* bench.cpp -- per-instance CPU benchmark of cpp/dk_core.h (#20). Method, signal and results: cpp/BENCHMARK.md.
 * Normally built and run by cpp/bench.py, which adds the host description and writes the results file.
 *
 *   bench [--seconds S] [--reps R] [--only A,B,...] [--list]
 *     --seconds  length of the test signal per pass (default 2; rounded down to a multiple of 512 samples)
 *     --reps     timed passes per configuration (default 11), after one untimed warm-up pass of every configuration
 *     --only     run only the configurations whose name contains one of the comma-separated substrings
 *   stdout: one JSON object (meta + one row per configuration); stderr: progress and a short table.
 *
 * A configuration is quality (eco / standard / hq) x math (libm / fast) x input peak (0.5 / 2 / 8 V) x block
 * (64 / 512) x instances (1 = one mono Stage; 2 = two independent mono Stages on one thread, left then right in each
 * block: a stereo-equivalent, not a SIMD stereo path, which does not exist yet, #21). Each pass resets the
 * instances and runs the whole signal through Stage::process() block by block; passes of all configurations are
 * interleaved round-robin so that drift (thermal, background load) spreads over all of them. Output is checked
 * bit-identical across passes (process() is deterministic from reset()).
 *
 * Counters, where they exist (else null in the JSON):
 *   macOS:  proc_pid_rusage(RUSAGE_INFO_V6) ri_cycles / ri_instructions / ri_pcycles: the kernel's per-thread
 *           fixed-counter totals for this (single-threaded) process, and the part counted on P-cores.
 *   Linux:  perf_event_open PERF_COUNT_HW_CPU_CYCLES / INSTRUCTIONS on this thread, user space only; rejected if
 *           the kernel multiplexed them or they read zero (e.g. a VM without a virtual PMU).
 *   both:   CLOCK_THREAD_CPUTIME_ID, to report the fraction of wall time the thread was on a CPU.
 */
#include "dk_core.h"

#include <algorithm>
#include <chrono>
#include <cinttypes>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
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

using namespace dkcore;

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

// ---- configurations -------------------------------------------------------------------------------------------
struct Config {
    std::string name, quality;
    Quality q;
    TriodeMath math;
    double level;
    int block, inst;
    Stage* s[2] = {nullptr, nullptr};
    const std::vector<double>* x[2] = {nullptr, nullptr};
    std::vector<double> y[2];
    std::uint64_t checksum = 0;
    bool deterministic = true;
    double newton_mean = 0.0;
    std::uint64_t nonconverged = 0;
    std::vector<double> ns, cpu_frac, cyc, ins, pfrac, ghz, blocks;   // per pass (blocks: per block, all passes)
    bool cyc_ok = true;
};

static std::uint64_t bits_sum(const std::vector<double>& a, std::uint64_t h) {
    for (double v : a) { std::uint64_t b; std::memcpy(&b, &v, 8); h = (h ^ b) * 1099511628211ull; }
    return h;
}

// one pass: reset, then the whole signal block by block; timed passes append their measurements to c
static void pass(Config& c, std::size_t n, std::vector<std::chrono::steady_clock::time_point>& tp, bool timed) {
    for (int k = 0; k < c.inst; ++k) c.s[k]->reset();
    const std::size_t nb = n / std::size_t(c.block);
    const double cpu0 = thread_cpu_ns();
    const Cnt k0 = counters_read();
#if defined(__linux__)
    const std::uint64_t e0 = g_ena, r0 = g_run;
#endif
    tp[0] = std::chrono::steady_clock::now();
    for (std::size_t b = 0; b < nb; ++b) {
        const std::size_t o = b * std::size_t(c.block);
        for (int k = 0; k < c.inst; ++k) c.s[k]->process(c.x[k]->data() + o, c.y[k].data() + o, std::size_t(c.block));
        tp[b + 1] = std::chrono::steady_clock::now();
    }
    const Cnt k1 = counters_read();
    const double cpu1 = thread_cpu_ns();
    std::uint64_t h = 1469598103934665603ull;
    for (int k = 0; k < c.inst; ++k) h = bits_sum(c.y[k], h);
    if (!timed) {
        c.checksum = h;
        c.newton_mean = double(c.s[0]->stats().iterations) / double(c.s[0]->stats().samples);
        for (int k = 0; k < c.inst; ++k) c.nonconverged += c.s[k]->stats().nonconverged;
        return;
    }
    if (h != c.checksum) c.deterministic = false;
    const double wall = std::chrono::duration<double, std::nano>(tp[nb] - tp[0]).count();
    c.ns.push_back(wall / double(n));
    c.cpu_frac.push_back((cpu1 - cpu0) / wall);
    const double period_ns = 1e9 * double(c.block) / FS;
    for (std::size_t b = 0; b < nb; ++b)
        c.blocks.push_back(100.0 * std::chrono::duration<double, std::nano>(tp[b + 1] - tp[b]).count() / period_ns);
    bool ok = k0.ok && k1.ok && k1.cycles > k0.cycles;
#if defined(__linux__)
    ok = ok && (g_ena - e0) == (g_run - r0);   // not multiplexed during the pass
#endif
    if (!ok) { c.cyc_ok = false; return; }
    c.cyc.push_back(double(k1.cycles - k0.cycles) / double(n));
    c.ins.push_back(double(k1.instr - k0.instr) / double(n));
    c.pfrac.push_back(double(k1.pcycles - k0.pcycles) / double(k1.cycles - k0.cycles));
    c.ghz.push_back(double(k1.cycles - k0.cycles) / (cpu1 - cpu0));   // cycles per on-CPU ns: a clock sanity check
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

int main(int argc, char** argv) {
    double seconds = 2.0;
    int reps = 11;
    std::vector<std::string> only;
    bool list = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--seconds" && i + 1 < argc) seconds = std::atof(argv[++i]);
        else if (a == "--reps" && i + 1 < argc) reps = std::atoi(argv[++i]);
        else if (a == "--only" && i + 1 < argc) {
            std::string s = argv[++i];
            for (std::size_t p; (p = s.find(',')) != std::string::npos; s.erase(0, p + 1)) only.push_back(s.substr(0, p));
            only.push_back(s);
        } else if (a == "--list") list = true;
        else { std::fprintf(stderr, "usage: bench [--seconds S] [--reps R] [--only A,B,...] [--list]\n"); return 2; }
    }
    const std::size_t n = std::size_t(seconds * FS) / 512 * 512;
    if (n == 0 || reps < 1) { std::fprintf(stderr, "need --seconds >= %g and --reps >= 1\n", 512 / FS); return 2; }

    const std::pair<const char*, Quality> qs[] = {{"eco", ECO}, {"standard", STANDARD}, {"hq", HQ}};
    const double levels[] = {0.5, 2.0, 8.0};
    std::vector<Config> cfg;
    for (const auto& q : qs)
        for (TriodeMath m : {TriodeMath::libm, TriodeMath::fast})
            for (double lv : levels)
                for (int blk : {64, 512})
                    for (int inst : {1, 2}) {
                        char nm[64];
                        std::snprintf(nm, sizeof nm, "%s_%s_%gV_b%d_x%d", q.first, m == TriodeMath::fast ? "fast" : "libm",
                                      lv, blk, inst);
                        bool keep = only.empty();
                        for (const auto& o : only) keep = keep || std::string(nm).find(o) != std::string::npos;
                        if (!keep) continue;
                        Config c;
                        c.name = nm; c.quality = q.first; c.q = q.second; c.math = m; c.level = lv; c.block = blk; c.inst = inst;
                        cfg.push_back(std::move(c));
                    }
    if (list) { for (const auto& c : cfg) std::printf("%s\n", c.name.c_str()); return 0; }
    if (cfg.empty()) { std::fprintf(stderr, "no configuration matches --only\n"); return 2; }

#if defined(__APPLE__)
    // The QoS class a host's audio thread does not use, but the strongest hint available without a realtime
    // workgroup: it makes the scheduler prefer P-cores. ri_pcycles reports where the passes actually ran.
    const bool qos = pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) == 0;
    const char* sched = qos ? "QOS_CLASS_USER_INTERACTIVE (no pinning API on macOS)" : "default (setting QoS failed)";
#elif defined(__linux__)
    cpu_set_t cs;
    CPU_ZERO(&cs);
    const int ncpu = sched_getaffinity(0, sizeof cs, &cs) == 0 ? CPU_COUNT(&cs) : -1;
    char schedbuf[96];
    std::snprintf(schedbuf, sizeof schedbuf, "default policy; affinity mask has %d CPU(s) (pin with taskset)", ncpu);
    const char* sched = schedbuf;
#else
    const char* sched = "default";
#endif
    const bool have_counters = counters_init();

    // signals: one per level (and a second, independently plucked one for the right channel), shared by configs
    std::vector<double> sig[3][2];
    for (int l = 0; l < 3; ++l)
        for (int k = 0; k < 2; ++k) sig[l][k] = make_signal(n, levels[l], 12345u + 7919u * std::uint32_t(k));
    double rms = 0.0;
    for (double v : sig[1][0]) rms += v * v;
    rms = std::sqrt(rms / double(n));

    for (auto& c : cfg) {
        const int l = int(std::find(levels, levels + 3, c.level) - levels);
        for (int k = 0; k < c.inst; ++k) {
            c.s[k] = new Stage;
            if (c.s[k]->prepare(FS, c.q, 1.0, c.math) != BuildStatus::ok) { std::fprintf(stderr, "prepare failed\n"); return 3; }
            c.x[k] = &sig[l][k];
            c.y[k].assign(n, 0.0);
        }
    }
    std::vector<std::chrono::steady_clock::time_point> tp(n / 64 + 1);

    // warm-up: one untimed pass of every configuration (caches, branch predictors, clocks; the reference checksum)
    for (auto& c : cfg) pass(c, n, tp, false);
    for (int r = 0; r < reps; ++r) {
        for (auto& c : cfg) pass(c, n, tp, true);
        std::fprintf(stderr, "\rpass %d / %d", r + 1, reps);
    }
    std::fprintf(stderr, "\n");

    bool all_det = true;
    std::printf("{\"meta\": {\"fs\": %g, \"samples_per_pass\": %zu, \"seconds_per_pass\": %.6g, \"reps\": %d, "
                "\"warmup_passes\": 1, \"order\": \"round-robin over configurations, per pass\", ",
                FS, n, double(n) / FS, reps);
    std::printf("\"signal\": \"Karplus-Strong guitar phrase (bench.cpp make_signal), %g s period, scaled to the peak\", "
                "\"signal_crest_db\": %.2f, ", PHRASE_S, 20.0 * std::log10(1.0 / (rms / 2.0)));
    std::printf("\"timer\": \"std::chrono::steady_clock, one read per block boundary\", \"sched\": \"%s\", ", sched);
#if defined(__linux__)
    std::printf("\"counters\": \"%s\", \"counter_error\": \"%s\", ", have_counters ? COUNTER_SOURCE : "unavailable",
                have_counters ? "" : g_counter_error.c_str());
#else
    std::printf("\"counters\": \"%s\", ", have_counters ? COUNTER_SOURCE : "unavailable");
#endif
    std::printf("\"compiler\": \"%s\", \"sizeof_stage\": %zu}, \"results\": [", __VERSION__, sizeof(Stage));
    for (std::size_t i = 0; i < cfg.size(); ++i) {
        Config& c = cfg[i];
        all_det = all_det && c.deterministic;
        const double med = median(c.ns);
        const bool cy = have_counters && c.cyc_ok && !c.cyc.empty();
        const double nan = std::nan("");
        std::printf("%s\n{\"name\": \"%s\", \"quality\": \"%s\", \"os\": %d, \"math\": \"%s\", \"level_v\": %g, "
                    "\"block\": %d, \"instances\": %d, ", i ? "," : "", c.name.c_str(), c.quality.c_str(), c.q.os,
                    c.math == TriodeMath::fast ? "fast" : "libm", c.level, c.block, c.inst);
        jnum("ns_per_sample", med, "%.1f"); std::printf(", ");
        jnum("ns_min", pct(c.ns, 0.0), "%.1f"); std::printf(", ");
        jnum("ns_max", pct(c.ns, 100.0), "%.1f"); std::printf(", ");
        jnum("pct_core", 100.0 * med * FS / 1e9, "%.3f"); std::printf(", ");
        jnum("block_pct_p50", pct(c.blocks, 50.0), "%.3f"); std::printf(", ");
        jnum("block_pct_p99", pct(c.blocks, 99.0), "%.3f"); std::printf(", ");
        jnum("block_pct_max", pct(c.blocks, 100.0), "%.3f"); std::printf(", ");
        jnum("cpu_on_fraction", median(c.cpu_frac), "%.4f"); std::printf(", ");
        jnum("cycles_per_sample", cy ? median(c.cyc) : nan, "%.0f"); std::printf(", ");
        jnum("instr_per_sample", cy && median(c.ins) > 0 ? median(c.ins) : nan, "%.0f"); std::printf(", ");
#if defined(__APPLE__)
        jnum("p_core_fraction", cy ? median(c.pfrac) : nan, "%.4f"); std::printf(", ");
        jnum("p_core_fraction_min", cy ? pct(c.pfrac, 0.0) : nan, "%.4f"); std::printf(", ");
#endif
        jnum("ghz_on_cpu", cy ? median(c.ghz) : nan, "%.3f"); std::printf(", ");
        jnum("newton_mean", c.newton_mean, "%.4f");
        std::printf(", \"nonconverged\": %" PRIu64 ", \"deterministic\": %s}", c.nonconverged, c.deterministic ? "true" : "false");
        std::fprintf(stderr, "%-26s %8.1f ns/sample  %6.2f %% core  p99 block %6.2f %%  cycles %s\n", c.name.c_str(), med,
                     100.0 * med * FS / 1e9, pct(c.blocks, 99.0), cy ? std::to_string(long(median(c.cyc))).c_str() : "-");
    }
    std::printf("]}\n");
    for (auto& c : cfg) for (int k = 0; k < c.inst; ++k) delete c.s[k];
    return all_det ? 0 : 1;
}
