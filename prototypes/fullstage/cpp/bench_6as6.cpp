/* bench_6as6.cpp -- per-instance CPU benchmark of cpp/dk6as6_core.h (#104), with the #20 / #67 method of cpp/bench.cpp
 * (bench_common.h: the same signal, counters, clocks, warm-up, round-robin passes, determinism check, percentiles).
 * Method: cpp/BENCHMARK.md; results: cpp/README-6as6.md. Normally built and run by `cpp/bench.py --core 6as6`.
 *
 *   bench_6as6 [--seconds S] [--reps R] [--only A,B,...] [--list]
 *
 * A configuration is quality (standard / hq) x input peak (0.5 / 2 / 8 V) x block (64 / 512) x instances (x1 one mono
 * Stage; x2 two independent mono Stages on one thread, left then right per block: the stereo cost, as #20's x2) at the
 * default setting (P1, 0.45 mA, the default grid current, filter off, the core's DEFAULT_FIR: k140), plus x1 / block-64
 * rows that change one thing from the default: T 0.5 mA (the top of T's envelope), P1 0.1 and 0.6 mA (the ends of the
 * range), no grid current, the 7k post-filter, and the shipped resampler. #135 adds, at the default setting and every
 * quality / peak / block, the fast valve law (LawMath::fast) as x1 and x2 rows ("_fast"), and the two-lane kernel
 * (cpp/dk6as6_stereo.h, one StereoStage on the x2 signals): s2 (libm), s2_fast_per_lane (fast, scalar lane maths: the
 * bit-exact reference) and v2 (fast, vector lane maths); each stereo row says whether its output is bit-identical to
 * its x2 twin's (matches_x2, same maths).
 * DK6AS6_BENCH_ADAA adds experimental rows from the offline #136 renderer.
 * DK6AS6_BENCH_GRID_ADAA adds experimental rows from the offline #154 grid renderer.
 */
#include "dk6as6_stereo.h"   // includes dk6as6_core.h

#include <chrono>
#include <cinttypes>
#include <cstdlib>
#include <memory>

#include "bench_common.h"   // FS, the signal, the counters, thread_cpu_ns, pct / median / jnum
#ifdef DK6AS6_BENCH_ADAA
#include "../dk6as6/adaa_screen.h"
#endif
#ifdef DK6AS6_BENCH_GRID_ADAA
#include "../dk6as6/grid_adaa_screen.h"
#include <fstream>
#endif

using namespace dk6as6;

// The #148 research run extends the same benchmark without changing the default configurations.
#ifdef DK6AS6_BENCH_EXTENDED_RATES
using BenchCore = StageT<64>;
using BenchStereo = StereoStageT<64>;
#else
using BenchCore = Stage;
using BenchStereo = StereoStage;
#endif

struct BenchQuality {
    std::string name;
    Quality rate;
    int order = 0, nodes = 0;
#ifdef DK6AS6_BENCH_GRID_ADAA
    bool grid_study = false;
#endif
};

class BenchStage {
public:
    Status prepare(double fs, const BenchQuality& q, const Settings& s, [[maybe_unused]] const std::string& variant) {
#ifdef DK6AS6_BENCH_GRID_ADAA
        grid_adaa_ = q.grid_study && q.order != 0;
        if (grid_adaa_) {
            const char* dir = std::getenv("DK6AS6_GRID_ADAA_TABLES");
            if (!dir) return Status::bad_argument;
            const std::string file = std::string(dir) + "/" + variant + "_x" + std::to_string(q.rate.os) + ".bin";
            std::ifstream table(file, std::ios::binary);
            table_.resize(grid_research::ROWS * grid_research::COLS * 2);
            if (!table.read(reinterpret_cast<char*>(table_.data()), table_.size() * sizeof(double)))
                return Status::bad_argument;
            return grid_screen_.prepare(fs, q.rate.os, s, q.order, table_.data(),
                                        table_.data() + grid_research::ROWS * grid_research::COLS);
        }
#endif
#ifdef DK6AS6_BENCH_ADAA
        adaa_ = q.nodes != 0;
        if (adaa_) return screen_.prepare(fs, q.rate, s, q.order, q.nodes);
#endif
        return stage_.prepare(fs, q.rate, s);
    }
    // #135: this BenchStage runs both channels on one StereoStage instead
    Status prepare_stereo(double fs, const BenchQuality& q, const Settings& s, LaneMath lm) {
        stereo_ = true;
        stereo_stage_.reset(new BenchStereo);   // only stereo rows hold one: a mono row keeps #104's layout
        stereo_stage_->set_lane_math(lm);
        return stereo_stage_->prepare(fs, q.rate, s);
    }
    void process_stereo(const double* l, const double* r, double* yl, double* yr, std::size_t n) {
        stereo_stage_->process(l, r, yl, yr, n);
    }
    bool stereo() const { return stereo_; }
    const Stats& stats(int ch) const { return stereo_ ? stereo_stage_->stats(ch) : stats(); }
    std::uint64_t kernel_iterations() const { return stereo_stage_->kernel_iterations(); }
    void reset() {
        if (stereo_) { stereo_stage_->reset(); return; }
#ifdef DK6AS6_BENCH_GRID_ADAA
        if (grid_adaa_) { grid_screen_.reset(); return; }
#endif
#ifdef DK6AS6_BENCH_ADAA
        if (adaa_) { screen_.reset(); return; }
#endif
        stage_.reset();
    }
    void process(const double* x, double* y, std::size_t n) {
#ifdef DK6AS6_BENCH_GRID_ADAA
        if (grid_adaa_) { grid_screen_.process(x, y, n); return; }
#endif
#ifdef DK6AS6_BENCH_ADAA
        if (adaa_) { screen_.process(x, y, n); return; }
#endif
        stage_.process(x, y, n);
    }
    const Stats& stats() const {
#ifdef DK6AS6_BENCH_GRID_ADAA
        if (grid_adaa_) return grid_screen_.stats();
#endif
#ifdef DK6AS6_BENCH_ADAA
        if (adaa_) return screen_.stats();
#endif
        return stage_.stats();
    }
private:
    BenchCore stage_;
    std::unique_ptr<BenchStereo> stereo_stage_;
    bool stereo_ = false;
#ifdef DK6AS6_BENCH_ADAA
    research::Screen screen_;
    bool adaa_ = false;
#endif
#ifdef DK6AS6_BENCH_GRID_ADAA
    grid_research::Screen grid_screen_;
    std::vector<double> table_;
    bool grid_adaa_ = false;
#endif
};

struct Config {
    std::string name, variant, path = "mono";   // path: mono, stereo_simd, stereo_simd_per_lane, stereo_simd_vecmath
    BenchQuality q;
    Settings set;
    double level;
    int block, inst;
    BenchStage* s[2] = {nullptr, nullptr};
    const std::vector<double>* x[2] = {nullptr, nullptr};
    std::vector<double> y[2];
    std::uint64_t checksum = 0, nonconverged = 0, restarted = 0;
    bool deterministic = true;
    double newton_mean = 0.0, newton_mean_r = 0.0, newton_mean_max = 0.0;
    int newton_max = 0;
    int matches_x2 = -1;                        // stereo rows: output bit-identical to the x2 twin's (-1: no twin run)
    bool stereo() const { return path != "mono"; }
    const Stats& st(int k) const { return stereo() ? s[0]->stats(k) : s[k]->stats(); }
    std::vector<double> ns, cpu_frac, cyc, ins, pfrac, ghz, blocks;
    bool cyc_ok = true;
};

static std::uint64_t bits_sum(const std::vector<double>& a, std::uint64_t h) {
    for (double v : a) { std::uint64_t b; std::memcpy(&b, &v, 8); h = (h ^ b) * 1099511628211ull; }
    return h;
}

static void pass(Config& c, std::size_t n, std::vector<std::chrono::steady_clock::time_point>& tp, bool timed) {
    for (int k = 0; k < (c.stereo() ? 1 : c.inst); ++k) c.s[k]->reset();
    const std::size_t nb = n / std::size_t(c.block);
    const double cpu0 = thread_cpu_ns();
    const Cnt k0 = counters_read();
#if defined(__linux__)
    const std::uint64_t e0 = g_ena, r0 = g_run;
#endif
    tp[0] = std::chrono::steady_clock::now();
    for (std::size_t b = 0; b < nb; ++b) {
        const std::size_t o = b * std::size_t(c.block);
        if (c.stereo())
            c.s[0]->process_stereo(c.x[0]->data() + o, c.x[1]->data() + o, c.y[0].data() + o, c.y[1].data() + o,
                                   std::size_t(c.block));
        else
            for (int k = 0; k < c.inst; ++k) c.s[k]->process(c.x[k]->data() + o, c.y[k].data() + o, std::size_t(c.block));
        tp[b + 1] = std::chrono::steady_clock::now();
    }
    const Cnt k1 = counters_read();
    const double cpu1 = thread_cpu_ns();
    std::uint64_t h = 1469598103934665603ull;
    for (int k = 0; k < c.inst; ++k) h = bits_sum(c.y[k], h);
    if (!timed) {
        c.checksum = h;
        const Stats& l = c.st(0);
        c.newton_mean = double(l.iterations) / double(l.samples);
        c.newton_max = l.max_iterations;
        if (c.inst == 2) c.newton_mean_r = double(c.st(1).iterations) / double(c.st(1).samples);
        if (c.stereo()) c.newton_mean_max = double(c.s[0]->kernel_iterations()) / double(l.samples);
        for (int k = 0; k < c.inst; ++k) {
            c.nonconverged += c.st(k).nonconverged;
            c.restarted += c.st(k).restarted;
            c.newton_max = std::max(c.newton_max, c.st(k).max_iterations);
        }
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
    ok = ok && (g_ena - e0) == (g_run - r0);
#endif
    if (!ok) { c.cyc_ok = false; return; }
    c.cyc.push_back(double(k1.cycles - k0.cycles) / double(n));
    c.ins.push_back(double(k1.instr - k0.instr) / double(n));
    c.pfrac.push_back(double(k1.pcycles - k0.pcycles) / double(k1.cycles - k0.cycles));
    c.ghz.push_back(double(k1.cycles - k0.cycles) / (cpu1 - cpu0));
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
        else { std::fprintf(stderr, "usage: bench_6as6 [--seconds S] [--reps R] [--only A,B,...] [--list]\n"); return 2; }
    }
    const std::size_t n = std::size_t(seconds * FS) / 512 * 512;
    if (n == 0 || reps < 1) { std::fprintf(stderr, "need --seconds >= %g and --reps >= 1\n", 512 / FS); return 2; }

    std::vector<BenchQuality> qs = {{"standard", STANDARD}, {"hq", HQ}};
#ifdef DK6AS6_BENCH_EXTENDED_RATES
    qs.push_back({"research32", {32, 0.0}});
    qs.push_back({"research64", {64, 0.0}});
#endif
#ifdef DK6AS6_BENCH_ADAA
    qs.push_back({"os2", {2, 0.0}});
    qs.push_back({"os4", {4, 0.0}});
    for (int nodes : {2, 4, 8})
        for (int order : {1, 2})
            for (int os : {2, 4, 8})
                qs.push_back({"a" + std::to_string(order) + "x" + std::to_string(os) + "q" + std::to_string(nodes),
                              {os, 0.0}, order, nodes});
#endif
#ifdef DK6AS6_BENCH_GRID_ADAA
#ifndef DK6AS6_BENCH_ADAA
    qs.push_back({"os4", {4, 0.0}, 0, 0, true});
#endif
    for (int os : {4, 8, 16})
        for (int order : {1, 2})
            qs.push_back({"a" + std::to_string(order) + "x" + std::to_string(os), {os, 0.0}, order, 0, true});
#endif
    const double levels[] = {0.5, 2.0, 8.0};
    // (name, settings): the default, and one change from it per variant
    std::vector<std::pair<std::string, Settings>> variants;
    variants.push_back({"P1_0.45mA", Settings{}});
    { Settings s; s.mode = Mode::T; s.bias_ma = 0.5; variants.push_back({"T_0.5mA", s}); }
    { Settings s; s.bias_ma = 0.1; variants.push_back({"P1_0.1mA", s}); }
    { Settings s; s.bias_ma = 0.6; variants.push_back({"P1_0.6mA", s}); }
    { Settings s; s.grid_hardness = 0.0; variants.push_back({"P1_0.45mA_nogrid", s}); }
    { Settings s; s.lpf = Lpf::k7; variants.push_back({"P1_0.45mA_lpf7k", s}); }
    { Settings s; s.fir = dkcore::SHIPPED_FIR; variants.push_back({"P1_0.45mA_shipped", s}); }
    std::vector<Config> cfg;
    for (const auto& v : variants)
        for (const auto& q : qs)
            for (double lv : levels)
                for (int blk : {64, 512})
                    for (int inst : {1, 2}) {
                        if (v.first != "P1_0.45mA" && (blk != 64 || inst != 1)) continue;
#ifdef DK6AS6_BENCH_GRID_ADAA
                        if (q.grid_study && (lv != 8 || blk != 64 || inst != 1 ||
                            (v.first != "P1_0.45mA" && v.first != "T_0.5mA"))) continue;
#endif
                        char nm[96];
                        std::snprintf(nm, sizeof nm, "%s_%s_%gV_b%d_x%d", q.name.c_str(), v.first.c_str(), lv, blk, inst);
                        bool keep = only.empty();
                        for (const auto& o : only) keep = keep || std::string(nm).find(o) != std::string::npos;
                        if (!keep) continue;
                        Config c;
                        c.name = nm; c.variant = v.first; c.q = q; c.set = v.second;
                        c.level = lv; c.block = blk; c.inst = inst;
                        cfg.push_back(std::move(c));
                    }
    // #135: the default setting with the fast law (x1, x2) and on the two-lane kernel (s2, s2 fast per-lane, v2)
    for (const auto& q : qs)
        for (double lv : levels)
            for (int blk : {64, 512}) {
                struct Extra { const char* tag; int inst; LawMath math; const char* path; };
                const Extra ex[5] = {{"x1_fast", 1, LawMath::fast, "mono"}, {"x2_fast", 2, LawMath::fast, "mono"},
                                     {"s2", 2, LawMath::libm, "stereo_simd"},
                                     {"s2_fast_per_lane", 2, LawMath::fast, "stereo_simd_per_lane"},
                                     {"v2", 2, LawMath::fast, "stereo_simd_vecmath"}};
                for (const Extra& e : ex) {
                    char nm[112];
                    std::snprintf(nm, sizeof nm, "%s_P1_0.45mA_%gV_b%d_%s", q.name.c_str(), lv, blk, e.tag);
                    bool keep = only.empty();
                    for (const auto& o : only) keep = keep || std::string(nm).find(o) != std::string::npos;
                    if (!keep) continue;
                    Config c;
                    c.name = nm; c.variant = "P1_0.45mA"; c.q = q; c.set = Settings{}; c.set.math = e.math;
                    c.path = e.path; c.level = lv; c.block = blk; c.inst = e.inst;
                    cfg.push_back(std::move(c));
                }
            }
    if (list) { for (const auto& c : cfg) std::printf("%s\n", c.name.c_str()); return 0; }
    if (cfg.empty()) { std::fprintf(stderr, "no configuration matches --only\n"); return 2; }

#if defined(__APPLE__)
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

    std::vector<double> sig[3][2];
    for (int l = 0; l < 3; ++l)
        for (int k = 0; k < 2; ++k) sig[l][k] = make_signal(n, levels[l], 12345u + 7919u * std::uint32_t(k));
    for (auto& c : cfg) {
        const int l = int(std::find(levels, levels + 3, c.level) - levels);
        for (int k = 0; k < c.inst; ++k) {
            if (c.stereo() && k == 1) { c.x[1] = &sig[l][1]; c.y[1].assign(n, 0.0); continue; }
            c.s[k] = new BenchStage;
            const Status st = c.stereo() ? c.s[k]->prepare_stereo(FS, c.q, c.set, c.path == "stereo_simd_vecmath"
                                                                   ? LaneMath::vector : LaneMath::per_lane)
                                         : c.s[k]->prepare(FS, c.q, c.set, c.variant);
            if (st != Status::ok) { std::fprintf(stderr, "%s: prepare failed (%s)\n", c.name.c_str(), to_string(st)); return 3; }
            c.x[k] = &sig[l][k];
            c.y[k].assign(n, 0.0);
        }
    }
    std::vector<std::chrono::steady_clock::time_point> tp(n / 64 + 1);
    for (auto& c : cfg) pass(c, n, tp, false);
    for (auto& c : cfg) {                        // #135: a stereo row against its x2 twin (same maths), bit for bit
        if (!c.stereo()) continue;
        for (const auto& t : cfg)
            if (!t.stereo() && t.inst == 2 && t.q.name == c.q.name && t.variant == c.variant && t.level == c.level &&
                t.block == c.block && t.set.math == c.set.math && t.set.fir.later_taps == c.set.fir.later_taps)
                c.matches_x2 = t.checksum == c.checksum;
    }
    for (int r = 0; r < reps; ++r) {
        for (auto& c : cfg) pass(c, n, tp, true);
        std::fprintf(stderr, "\rpass %d / %d", r + 1, reps);
    }
    std::fprintf(stderr, "\n");

    bool all_det = true;
    std::printf("{\"meta\": {\"core\": \"cpp/dk6as6_core.h\", \"fs\": %g, \"samples_per_pass\": %zu, \"seconds_per_pass\": "
                "%.6g, \"reps\": %d, \"warmup_passes\": 1, \"order\": \"round-robin over configurations, per pass\", ",
                FS, n, double(n) / FS, reps);
    std::printf("\"signal\": \"Karplus-Strong guitar phrase (bench_common.h make_signal), %g s period, scaled to the "
                "peak\", ", PHRASE_S);
    std::printf("\"timer\": \"std::chrono::steady_clock, one read per block boundary\", \"sched\": \"%s\", ", sched);
#if defined(__linux__)
    std::printf("\"counters\": \"%s\", \"counter_error\": \"%s\", ", have_counters ? COUNTER_SOURCE : "unavailable",
                have_counters ? "" : g_counter_error.c_str());
#else
    std::printf("\"counters\": \"%s\", ", have_counters ? COUNTER_SOURCE : "unavailable");
#endif
    std::printf("\"compiler\": \"%s\", \"sizeof_stage\": %zu, \"fma\": %s}, \"results\": [", __VERSION__,
                sizeof(BenchCore), dkcore::simd::FMA ? "true" : "false");
    for (std::size_t i = 0; i < cfg.size(); ++i) {
        Config& c = cfg[i];
        all_det = all_det && c.deterministic;
        const double med = median(c.ns);
        const bool cy = have_counters && c.cyc_ok && !c.cyc.empty();
        const double nan = std::nan("");
        std::printf("%s\n{\"name\": \"%s\", \"quality\": \"%s\", \"os\": %d, \"variant\": \"%s\", \"mode\": \"%s\", "
                    "\"bias_ma\": %g, \"grid_hardness\": %g, \"lpf\": %d, \"fir\": \"%s\", \"level_v\": %g, \"block\": %d, "
                    "\"instances\": %d, \"math\": \"%s\", \"path\": \"%s\", ", i ? "," : "", c.name.c_str(), c.q.name.c_str(), c.q.rate.os, c.variant.c_str(),
                    to_string(c.set.mode), c.set.bias_ma, c.set.grid_hardness, int(c.set.lpf),
                    c.set.fir.later_taps == dkcore::K140_FIR.later_taps ? "k140" : "shipped", c.level, c.block, c.inst,
                    to_string(c.set.math), c.path.c_str());
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
        jnum("newton_mean", c.newton_mean, "%.4f"); std::printf(", ");
        jnum("newton_mean_r", c.inst == 2 ? c.newton_mean_r : nan, "%.4f"); std::printf(", ");
        jnum("newton_mean_max", c.stereo() ? c.newton_mean_max : nan, "%.4f"); std::printf(", ");
        if (c.stereo()) std::printf("\"matches_x2\": %s, ", c.matches_x2 < 0 ? "null" : c.matches_x2 ? "true" : "false");
        std::printf("\"newton_max\": %d, \"nonconverged\": %" PRIu64 ", \"restarted\": %" PRIu64 ", \"deterministic\": %s}",
                    c.newton_max, c.nonconverged, c.restarted, c.deterministic ? "true" : "false");
        std::fprintf(stderr, "%-36s %8.1f ns/sample  %6.2f %% core  p99 block %6.2f %%  cycles %s\n", c.name.c_str(), med,
                     100.0 * med * FS / 1e9, pct(c.blocks, 99.0), cy ? std::to_string(long(median(c.cyc))).c_str() : "-");
    }
    std::printf("]}\n");
    for (auto& c : cfg) for (int k = 0; k < c.inst; ++k) delete c.s[k];   // (a stereo row's s[1] is null)
    return all_det ? 0 : 1;
}
