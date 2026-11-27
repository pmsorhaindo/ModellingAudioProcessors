/* test_live_6as6.cpp -- live Bias, Mode and Filter on the 6AS6 stage (#115): dk6as6::BiasLine, StageT's line hooks
 * and LiveStageT (cpp/dk6as6_core.h), measured as #73 measured the 12AX7 bias (cpp/RESULTS-smoothing.md). Method and
 * numbers: cpp/RESULTS-smoothing-6as6.md.
 *
 *   test_live_6as6            every check, standard 8x at 48 kHz; one JSON line of metrics; exit 1 on any failure
 *   test_live_6as6 --hq       the same moves and switches at hq 16x too (slower; for the results file)
 *   test_live_6as6 --options  also the option sweep of the switch timing (warm-up and fade lengths), printed
 *   test_live_6as6 --options-only   only that sweep (standard)
 *
 * The checks:
 *   1. the line: its matrices against build_pack_at_rk, the table's Rk and exact()'s against Brent (cathode_resistor),
 *      the recentred operating point against build_pack's;
 *   2. at rest after a move: the stage against a stage prepared at the target;
 *   3. fast full-range bias moves (P1 0.1 <-> 0.6, T 0.1 <-> 0.5, P2 0.1 <-> 0.6 mA, 20 ms) in silence and under a
 *      1 V and an 8 V 220 Hz sine: worst step against a continuous reference (an exact pack at every internal sample),
 *      against the unsmoothed swap (one exact pack adopted at the block boundary);
 *   4. mode switches (all six ordered pairs): worst step against the ideal crossfade of the two modes each run
 *      continuously from the start ("always running"; asserted in silence and at 1 V, reported at 8 V where the new
 *      mode's own settling dominates), and against the crossfade to the new mode started at the switch (what the switch
 *      itself adds; asserted everywhere), beside the hard switch and the engine's pre-#115 5 ms fade-out / fade-in;
 *   5. filter switches: worst step against the ideal crossfade at the internal rate, beside the hard switch;
 *   6. block-size invariance (16 / 64 / 512, bit for bit) with bias, mode and filter all moving;
 *   7. NaN / inf audio and out-of-range targets during a switch and a ramp;
 *   8. no allocation in process(), throughout; informal timings.
 * Worst step = max_n |r[n] - r[n-1]|, r = y - y_ref, at the base rate, in volts at the stage output.
 * Build: c++ -std=c++17 -O2 -Wall -Wextra -pedantic -pthread -o test_live_6as6 cpp/test_live_6as6.cpp */
#include "dk6as6_core.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "alloc_hooks.h"   // allocation counting: g_count, CountAllocs, ALLOC_HOOKS
#include "live_ref_6as6.h"  // the continuous references (shared with plugin/tests/smoothing_test.cpp)

using namespace dk6as6;
using liveref::Vec;
using liveref::settings_of;

/* Limits: the #73 limits of cpp/test_core.cpp (LIMIT_STEP_SILENCE, LIMIT_STEP_SINE, MIN_STEP_REDUCTION), unchanged,
 * as issue #115 asks ("start from the 12AX7 figures"). At the plugin's 1/64 FS per volt: -107 dBFS and -70 dBFS. */
static const double LIMIT_STEP_SILENCE = 3e-4;   // V
static const double LIMIT_STEP_SINE = 2e-2;      // V
static const double MIN_STEP_REDUCTION = 200.0;
// the 6AS6 core's own tolerances (cpp/test_core_6as6.cpp): matrices / op point, and a stage on a C++-built pack
static const double TOL_COEF = 1e-10;
static const double TOL_BUILT_ABS = 1e-5;        // V
// exact()'s Rk against Brent's, relative: both stop on the same DC solver's tolerances (TOL_V = TOL_F = 1e-6)
static const double TOL_RK_EXACT = 1e-9;
static const double TOL_OP_V = 1e-6;             // V: the recentred operating point vs build_pack's (the Newton tol_v)

static const double FS = 48000.0;
static const std::size_t N0 = 4800;              // the move / switch: 0.1 s in, at a 64-sample block start
static const std::size_t NS = 14400;             // 0.3 s

static int g_fail = 0;
static std::ostringstream g_json;
static void check(bool ok, const std::string& what) {
    std::printf("%s  %s\n", ok ? "ok  " : "FAIL", what.c_str());
    if (!ok) ++g_fail;
}
static void metric(const std::string& k, double v) {
    if (g_json.tellp() > 0) g_json << ", ";
    char b[64];
    std::snprintf(b, sizeof b, "%.6g", v);
    g_json << "\"" << k << "\": " << (std::isfinite(v) ? b : "null");
}
static std::string fmt(const char* f, double a = 0, double b = 0, double c = 0, double d = 0, double e = 0) {
    char buf[512];
    std::snprintf(buf, sizeof buf, f, a, b, c, d, e);
    return buf;
}
static double worst_step(const Vec& y, const Vec& r, std::size_t from = 1) {
    double w = 0.0;
    for (std::size_t i = std::max<std::size_t>(from, 1); i < y.size(); ++i)
        w = std::max(w, std::fabs((y[i] - r[i]) - (y[i - 1] - r[i - 1])));
    return w;
}
static double maxdiff(const Vec& a, const Vec& b, std::size_t from = 0) {
    double w = 0.0;
    for (std::size_t i = from; i < a.size(); ++i) w = std::max(w, std::fabs(a[i] - b[i]));
    return w;
}
static Vec sine(double amp, double f = 220.0, std::size_t n = NS) {
    Vec x(n);
    for (std::size_t i = 0; i < n; ++i) x[i] = amp * std::sin(2 * PI * f * double(i) / FS);
    return x;
}

// the plugin's ranges (map_params.h kBias6Range, kTBiasCapMa; P2 experimental)
static const LineRange RANGES[3] = {{0.1, 0.6, false}, {0.1, 0.5, false}, {0.1, 0.6, true}};
static double hi_of(Mode m) { return RANGES[int(m)].hi_ma; }

struct Bench {
    Quality q;
    std::unique_ptr<Lines> lines{new Lines};
    explicit Bench(Quality qq) : q(qq) {
        const Status st = build_lines(*lines, FS * q.os, Settings{}, RANGES);
        check(st == Status::ok, std::string("build_lines at ") + (q.os == 8 ? "standard" : "hq") + ": " + to_string(st));
    }
};

static std::unique_ptr<LiveStage> make_live(const Bench& b, Mode m, double bias, Lpf lpf = Lpf::off,
                                            double warm = LIVE_WARM_S, double fade = LIVE_FADE_S) {
    // the pack at the bias the mode allows (T: at most 0.5 mA), the target the bias asked for (as the plugin does)
    std::unique_ptr<LiveStage> s(new LiveStage);
    Pack p;
    Status st = build_pack(p, FS * b.q.os, settings_of(m, std::min(bias, hi_of(m)), lpf));
    if (st == Status::ok) st = s->prepare(FS, b.q.os, *b.lines, p, lpf, DEFAULT_FIR, LIVE_RAMP_S, warm, fade);
    if (st != Status::ok) check(false, std::string("LiveStage prepare: ") + to_string(st));
    s->set_bias(bias);
    return s;
}
static std::unique_ptr<Stage> make_stage(const Bench& b, Mode m, double bias, Lpf lpf = Lpf::off) {
    std::unique_ptr<Stage> s(new Stage);
    const Status st = s->prepare(FS, b.q, settings_of(m, std::min(bias, hi_of(m)), lpf));
    if (st != Status::ok) check(false, std::string("Stage prepare: ") + to_string(st));
    return s;
}
// run s over x in blocks of `block`; host(i) is called before the block that starts at sample i
static long g_process_allocs = 0;
template <class S> static Vec run(S& s, const Vec& x, std::size_t block, const std::function<void(std::size_t)>& host = {}) {
    Vec y(x.size());
    for (std::size_t i = 0; i < x.size(); i += block) {
        if (host) host(i);
        const std::size_t n = std::min(block, x.size() - i);
        CountAllocs c;
        s.process(x.data() + i, y.data() + i, n);
        g_process_allocs += c.n();
    }
    return y;
}

// ---- 1. the line ----------------------------------------------------------------------------------------------
static double rel(const double* a, const double* r, int n) {
    double d = 0.0, mx = 0.0;
    for (int i = 0; i < n; ++i) { d = std::max(d, std::fabs(a[i] - r[i])); mx = std::max(mx, std::fabs(r[i])); }
    return mx > 0.0 ? d / mx : d;
}
static double mats_rel(const Pack& c, const Pack& ref) {
    return std::max({rel(&c.A[0][0], &ref.A[0][0], NX * NX), rel(&c.B[0][0], &ref.B[0][0], NX * NU_MAX),
                     rel(&c.C[0][0], &ref.C[0][0], NX * NI_MAX), rel(&c.D[0][0], &ref.D[0][0], NOUT * NX),
                     rel(&c.E[0][0], &ref.E[0][0], NOUT * NU_MAX), rel(&c.F[0][0], &ref.F[0][0], NOUT * NI_MAX),
                     rel(&c.G[0][0], &ref.G[0][0], NV * NX), rel(&c.H[0][0], &ref.H[0][0], NV * NU_MAX),
                     rel(&c.K[0][0], &ref.K[0][0], NV * NI_MAX)});
}
// the rest constants and operating point of c against ref (V; currents in mA; x relative to max(1, |x|))
static double rest_abs(const Pack& c, const Pack& ref, bool op) {
    double w = 0.0;
    for (int i = 0; i < NX; ++i) w = std::max(w, std::fabs(c.x_dc[i] - ref.x_dc[i]) / std::max(1.0, std::fabs(ref.x_dc[i])));
    for (int q = 0; q < NV; ++q) w = std::max(w, std::fabs(c.p_dc[q] - ref.p_dc[q]));
    for (int q = 0; q < NOUT; ++q) w = std::max(w, std::fabs(c.y_dc[q] - ref.y_dc[q]));
    for (int k = 0; k < NI_MAX; ++k) w = std::max(w, 1e3 * std::fabs(c.i_dc[k] - ref.i_dc[k]));
    if (op) {
        for (int q = 0; q < NV; ++q) w = std::max(w, std::fabs(c.op.v[q] - ref.op.v[q]));
        for (int nd : {OUT, GRID, CATH, ANODE, GL}) w = std::max(w, std::fabs(c.op.nodes[nd] - ref.op.nodes[nd]));
        w = std::max(w, 1e3 * std::fabs(c.op.ik - ref.op.ik));
    }
    return w;
}
static void test_line(const Bench& b) {
    double wm = 0.0, wr = 0.0, wt = 0.0, we = 0.0, wop = 0.0;
    int steps_max = 0;
    for (int m = 0; m < 3; ++m) {
        const BiasLine& l = b.lines->line[m];
        const Mode mode = Mode(m);
        for (double ma : {0.1, 0.137, 0.2, 0.3, 0.35, 0.41, 0.45, 0.5, 0.55, 0.6}) {
            if (ma > hi_of(mode) + 1e-12) continue;
            Pack ref;
            if (build_pack(ref, FS * b.q.os, settings_of(mode, ma)) != Status::ok) { check(false, "build_pack ref"); continue; }
            // the line at Brent's Rk: matrices, and the rest constants for the reference's own operating currents
            Pack c = l.base;
            double wac, wdc;
            l.weights(ref.rk, wac, wdc);
            l.matrices(c, ref.rk, wac);
            l.rest(c, wdc, ref.i_dc);
            wm = std::max(wm, mats_rel(c, ref));
            wr = std::max(wr, rest_abs(c, ref, false));
            wt = std::max(wt, std::fabs(std::exp(l.log_rk(ma)) / ref.rk - 1.0));
            // exact(): Rk and the recentred operating point, from the table's start
            Pack e = l.base;
            double i0[NI_MAX];
            l.log_rk(ma, nullptr, &i0);
            bool conv = false;
            int steps = 0;
            if (!l.exact(e, ma, i0, conv, steps) || !conv) { check(false, fmt("exact() at %.3g mA, mode %.0f", ma, m)); continue; }
            steps_max = std::max(steps_max, steps);
            we = std::max(we, std::fabs(e.rk / ref.rk - 1.0));
            wop = std::max(wop, rest_abs(e, ref, true));
        }
    }
    const std::string qn = b.q.os == 8 ? "standard" : "hq";
    metric("line_matrix_rel_" + qn, wm);
    metric("line_rest_abs_" + qn, wr);
    metric("line_table_rk_rel_" + qn, wt);
    metric("line_exact_rk_rel_" + qn, we);
    metric("line_exact_op_abs_" + qn, wop);
    metric("line_exact_steps_max_" + qn, steps_max);
    check(wm <= TOL_COEF && wr <= TOL_OP_V,
          qn + fmt(": the line at Brent's Rk vs build_pack: matrices %.3g relative (tol %.0e), rest constants %.3g V / mA "
                   "(tol %.0e)", wm, TOL_COEF, wr, TOL_OP_V));
    check(we <= TOL_RK_EXACT && wop <= TOL_OP_V,
          qn + fmt(": exact() vs build_pack: Rk %.3g relative (tol %.0e), operating point and rest %.3g V / mA (tol %.0e), "
                   "<= %.0f Newton steps", we, TOL_RK_EXACT, wop, TOL_OP_V, steps_max) +
              fmt("; the table alone: Rk %.3g relative", wt));
}

// ---- 2. at rest after a move ----------------------------------------------------------------------------------
static void test_rest(const Bench& b) {
    double w = 0.0;
    struct C { Mode m; double from, to; };
    for (const C& c : {C{Mode::P1, 0.6, 0.37}, C{Mode::T, 0.1, 0.43}, C{Mode::P2, 0.2, 0.55}, C{Mode::P1, 0.45, 0.1}}) {
        auto live = make_live(b, c.m, c.from);
        const Vec x = sine(2.0, 220.0, 9600);
        live->set_bias(c.to);
        run(*live, x, 64);
        live->reset();
        auto ref = make_stage(b, c.m, c.to);
        const Vec y = run(*live, x, 64), r = run(*ref, x, 64);
        w = std::max(w, maxdiff(y, r));
    }
    metric(std::string("rest_vs_prepared_abs_") + (b.q.os == 8 ? "standard" : "hq"), w);
    check(w <= TOL_BUILT_ABS, fmt("at rest after a move (then reset) vs a stage prepared at the target, 2 V sine: %.3g V "
                                  "(tol %.0e)", w, TOL_BUILT_ABS));
}

// ---- 3. fast full-range bias moves ----------------------------------------------------------------------------
static double g_ref_err = 0.0;
static Vec bias_reference(const Bench& b, Mode m, const Vec& x, double b0, double b1, bool step) {
    return liveref::bias_reference(FS, b.q, m, x, b0, b1, N0, int(std::nearbyint(LIVE_RAMP_S * FS)), step, &g_ref_err);
}
static void test_bias_moves(const Bench& b, bool full) {
    const char* qn = b.q.os == 8 ? "standard" : "hq";
    double ws_sil = 0.0, ws_sine = 0.0, red = 1e300, md = 0.0, thump = 0.0;
    for (int m = 0; m < 3; ++m) {
        const Mode mode = Mode(m);
        const double lo = 0.1, hi = hi_of(mode);
        for (int dir = 0; dir < 2; ++dir) {
            const double b0 = dir ? hi : lo, b1 = dir ? lo : hi;
            for (double amp : {0.0, 1.0, 8.0}) {
                if (!full && amp == 1.0 && mode == Mode::P2) continue;
                const Vec x = sine(amp);
                auto live = make_live(b, mode, b0);
                const Vec y = run(*live, x, 64, [&](std::size_t i) { if (i == N0) live->set_bias(b1); });
                const Vec ref = bias_reference(b, mode, x, b0, b1, false);
                const Vec unsm = bias_reference(b, mode, x, b0, b1, true);
                auto stat = make_stage(b, mode, b0);
                const Vec still = run(*stat, x, 64);
                const double w = worst_step(y, ref), wu = worst_step(unsm, ref), d = maxdiff(y, ref);
                const double th = maxdiff(ref, still);
                (amp == 0.0 ? ws_sil : ws_sine) = std::max(amp == 0.0 ? ws_sil : ws_sine, w);
                red = std::min(red, wu / std::max(w, 1e-300));
                md = std::max(md, d);
                thump = std::max(thump, th);
                const double lim = amp == 0.0 ? LIMIT_STEP_SILENCE : LIMIT_STEP_SINE;
                check(w <= lim && wu / std::max(w, 1e-300) >= MIN_STEP_REDUCTION,
                      std::string(qn) + " " + to_string(mode) +
                          fmt(" bias %.2f -> %.2f mA, %.0f V sine: worst step %.3g V (limit %.0e)", b0, b1, amp, w, lim) +
                          fmt(", max|y-ref| %.3g V; unsmoothed %.3g V (%.0fx); the move itself %.3g V", d, wu,
                              wu / std::max(w, 1e-300), th));
            }
        }
    }
    metric(std::string("bias_step_silence_") + qn, ws_sil);
    metric(std::string("bias_step_sine_") + qn, ws_sine);
    metric(std::string("bias_step_reduction_min_") + qn, red);
    metric(std::string("bias_maxdiff_vs_continuous_") + qn, md);
    metric(std::string("bias_move_vs_constant_") + qn, thump);
    metric("bias_reference_log_ik_err", g_ref_err);
    check(g_ref_err <= 1e-12, fmt("continuous reference: every pack draws its bias to %.3g in log Ik", g_ref_err));
}

// ---- 4. mode switches and 5. filter switches ------------------------------------------------------------------
/* Two references for a mode switch A -> B at N0 (the new mode's fade starts after the warm-up, at N0 + W):
 *   always running  (1 - w) A + w B, A and B each run continuously from the start: the ideal a switch could reach.
 *                   Against it the residual also holds how far the new mode, started at the switch, still is from
 *                   having always run: the circuit's own settling (Cout (Ra + Rl) = 0.127 s, the grid-leak bias on
 *                   Cin with grid current), not a step of the switch. It is small at 1 V and in silence and large at
 *                   8 V; measured separately as "settling" (one mode started at N0 against the same mode always run).
 *   started at N0   (1 - w) A + w B', B' the new mode started from its operating point at N0 on the same input: what
 *                   the switch itself adds (its timing, mixing and the voice it installs), whatever the circuit does.
 * The hard switch (B' from N0, no fade) and the engine's pre-#115 5 ms fade-out / fade-in (to a B' started at the
 * swap) are measured against both. */
static Vec cold(const Bench& b, Mode m, double bias, Lpf lpf, const Vec& x, std::size_t from) {
    auto s = make_stage(b, m, bias, lpf);
    Vec y(x.size(), 0.0), part(x.begin() + std::ptrdiff_t(from), x.end());
    const Vec o = run(*s, part, 64);
    std::copy(o.begin(), o.end(), y.begin() + std::ptrdiff_t(from));
    return y;
}
struct SwitchResult {
    double w, wu, wf, d;          // against "always running": worst step (live, hard, 5 ms fade), max |y - ref| (live)
    double wc, wuc, wfc;          // against "started at N0": worst step (live, hard, 5 ms fade)
    double settle_step, settle_end;   // B' against B: worst step after the fade, max |B' - B| over the last 50 ms
};
static SwitchResult mode_switch(const Bench& b, Mode ma, Mode mb, double bias, double amp, double warm, double fade,
                                std::size_t n0 = N0) {
    const std::size_t L = n0 + (NS - N0);
    const Vec x = sine(amp, 220.0, L);
    auto live = make_live(b, ma, bias, Lpf::off, warm, fade);
    const Vec y = run(*live, x, 64, [&](std::size_t i) { if (i == n0) live->set_mode(mb); });
    auto sa = make_stage(b, ma, bias);
    auto sb = make_stage(b, mb, bias);
    const Vec ya = run(*sa, x, 64), yb = run(*sb, x, 64);
    const std::size_t start = n0 + std::size_t(live->warm_samples());
    const int F = live->fade_samples();
    const Vec ref = liveref::crossfade(ya, yb, start, F);
    const Vec yc = cold(b, mb, bias, Lpf::off, x, n0);              // B', started at n0
    const Vec refc = liveref::crossfade(ya, yc, start, F);
    Vec hard(L), fadeio(L);
    const std::size_t f5 = std::size_t(std::nearbyint(0.005 * FS));
    const Vec yc5 = cold(b, mb, bias, Lpf::off, x, n0 + f5);
    for (std::size_t i = 0; i < L; ++i) {
        hard[i] = i < n0 ? ya[i] : yc[i];
        if (i < n0) fadeio[i] = ya[i];
        else if (i < n0 + f5) fadeio[i] = ya[i] * (1.0 - double(i - n0 + 1) / double(f5));
        else fadeio[i] = yc5[i] * std::min(1.0, double(i - n0 - f5 + 1) / double(f5));
    }
    const std::size_t from = n0 - 64, after = start + std::size_t(F);
    SwitchResult r{};
    r.w = worst_step(y, ref, from); r.wu = worst_step(hard, ref, from); r.wf = worst_step(fadeio, ref, from);
    r.d = maxdiff(y, ref, n0);
    r.wc = worst_step(y, refc, from); r.wuc = worst_step(hard, refc, from); r.wfc = worst_step(fadeio, refc, from);
    r.settle_step = worst_step(yc, yb, after);
    r.settle_end = maxdiff(yc, yb, L - std::size_t(0.05 * FS));
    return r;
}
static void test_mode_switches(const Bench& b, bool full) {
    const std::string qn = b.q.os == 8 ? "standard" : "hq";
    double ws_sil = 0.0, ws_1 = 0.0, ws_8 = 0.0, wc_max = 0.0, red = 1e300, red_f = 1e300, md = 0.0, st8 = 0.0, se8 = 0.0;
    const Mode modes[3] = {Mode::P1, Mode::T, Mode::P2};
    for (Mode ma : modes)
        for (Mode mb : modes) {
            if (ma == mb) continue;
            for (double bias : {0.45, 0.6}) {
                if (!full && bias == 0.6 && ma != Mode::T && mb != Mode::T) continue;   // 0.6: where T's cap differs
                for (double amp : {0.0, 1.0, 8.0}) {
                    const SwitchResult r = mode_switch(b, ma, mb, bias, amp, LIVE_WARM_S, LIVE_FADE_S);
                    const double lim = amp == 0.0 ? LIMIT_STEP_SILENCE : LIMIT_STEP_SINE;
                    const std::string name = qn + " mode " + to_string(ma) + " -> " + to_string(mb) + fmt(" at %.2f mA, %.0f V sine", bias, amp);
                    wc_max = std::max(wc_max, r.wc);
                    // the switch itself, every signal: against "started at N0", the #73 limits, and it beats both baselines
                    const bool red_c = r.wuc <= lim ? true : r.wuc / std::max(r.wc, 1e-300) >= MIN_STEP_REDUCTION;
                    check(r.wc <= lim && red_c,
                          name + fmt(": the switch itself (vs the new mode started at the switch): worst step %.3g V (limit "
                                     "%.0e); hard switch %.3g V, 5 ms fade out/in %.3g V", r.wc, lim, r.wuc, r.wfc));
                    if (amp == 8.0) {
                        // reported, not asserted: the new mode's settling dominates (see the comment above)
                        ws_8 = std::max(ws_8, r.w);
                        st8 = std::max(st8, r.settle_step);
                        se8 = std::max(se8, r.settle_end);
                        std::printf("info  %s: vs always running: worst step %.3g V, max|y-ref| %.3g V (hard switch %.3g V, "
                                    "5 ms fade %.3g V); settling of the new mode alone: worst step %.3g V after the fade, "
                                    "%.3g V in the last 50 ms\n", name.c_str(), r.w, r.d, r.wu, r.wf, r.settle_step, r.settle_end);
                        continue;
                    }
                    (amp == 0.0 ? ws_sil : ws_1) = std::max(amp == 0.0 ? ws_sil : ws_1, r.w);
                    if (r.wu > lim) red = std::min(red, r.wu / std::max(r.w, 1e-300));
                    if (r.wf > lim) red_f = std::min(red_f, r.wf / std::max(r.w, 1e-300));
                    md = std::max(md, r.d);
                    const bool reduced = r.wu <= lim ? true : r.wu / std::max(r.w, 1e-300) >= MIN_STEP_REDUCTION;
                    check(r.w <= lim && reduced,
                          name + fmt(": vs always running: worst step %.3g V (limit %.0e)", r.w, lim) + fmt(", max|y-ref| %.3g V; ", r.d) +
                              fmt("hard switch %.3g V (%.0fx), 5 ms fade out/in %.3g V (%.0fx)", r.wu,
                                  r.wu / std::max(r.w, 1e-300), r.wf, r.wf / std::max(r.w, 1e-300)));
                }
            }
        }
    metric("mode_step_silence_" + qn, ws_sil);
    metric("mode_step_1V_" + qn, ws_1);
    metric("mode_step_8V_vs_always_running_" + qn, ws_8);
    metric("mode_step_switch_itself_" + qn, wc_max);
    metric("mode_step_reduction_min_" + qn, red);
    metric("mode_step_reduction_vs_fadeio_min_" + qn, red_f);
    metric("mode_maxdiff_1V_vs_always_running_" + qn, md);
    metric("mode_settling_step_8V_" + qn, st8);
    metric("mode_settling_last50ms_8V_" + qn, se8);
}
static void test_filter_switches(const Bench& b) {
    const char* qn = b.q.os == 8 ? "standard" : "hq";
    double ws = 0.0, red = 1e300;
    struct C { Lpf from, to; };
    const char* names[3] = {"Off", "4k", "7k"};
    for (const C& c : {C{Lpf::off, Lpf::k7}, C{Lpf::k7, Lpf::k4}, C{Lpf::k4, Lpf::off}, C{Lpf::off, Lpf::k4}})
        for (double amp : {0.0, 1.0, 8.0}) {
            const Vec x = sine(amp, 3150.0);             // a tone the filters treat differently
            auto live = make_live(b, Mode::P1, 0.45, c.from);
            const Vec y = run(*live, x, 64, [&](std::size_t i) { if (i == N0) live->set_lpf(c.to); });
            // The reference: the ideal crossfade of the two positions at the internal rate, where PostFilter mixes
            // (before the decimator): two stages at the internal rate (process_internal), one per position, mixed with
            // weight min(1, (t + 1) / n) from the internal sample of N0, then one decimator. Hard switch: the same
            // with the weight a step at N0.
            const int os = b.q.os, n = int(std::nearbyint(LPF_XFADE_S * FS * os));
            auto sa = make_stage(b, Mode::P1, 0.45, c.from);
            auto sb = make_stage(b, Mode::P1, 0.45, c.to);
            Stage::Resampler up, dr, dh;
            up.prepare(os, FS, DEFAULT_FIR); dr.prepare(os, FS, DEFAULT_FIR); dh.prepare(os, FS, DEFAULT_FIR);
            Vec ref(NS), hard(NS);
            double u[dkcore::MAX_OS], va[dkcore::MAX_OS], vb[dkcore::MAX_OS], vr[dkcore::MAX_OS], vh[dkcore::MAX_OS];
            for (std::size_t i = 0; i < NS; ++i) {
                up.up(x[i], u);
                sa->process_internal(u, va, std::size_t(os));
                sb->process_internal(u, vb, std::size_t(os));
                for (int j = 0; j < os; ++j) {
                    const long t = long(i) * os + j - long(N0) * os;
                    const double w = t < 0 ? 0.0 : std::fmin(double(t + 1) / double(n), 1.0);
                    vr[j] = (1.0 - w) * va[j] + w * vb[j];
                    vh[j] = t < 0 ? va[j] : vb[j];
                }
                ref[i] = dr.down(vr);
                hard[i] = dh.down(vh);
            }
            const double w = worst_step(y, ref), wu = worst_step(hard, ref);
            ws = std::max(ws, amp == 0.0 ? 0.0 : w);
            const double lim = amp == 0.0 ? LIMIT_STEP_SILENCE : LIMIT_STEP_SINE;
            if (wu > lim) red = std::min(red, wu / std::max(w, 1e-300));
            const bool reduced = wu <= lim ? true : wu / std::max(w, 1e-300) >= MIN_STEP_REDUCTION;
            check(w <= lim && reduced, std::string(qn) + " filter " + names[int(c.from)] + " -> " + names[int(c.to)] +
                                           fmt(" (internal fade %.0f samples), %.0f V 3150 Hz: worst step %.3g V (limit %.0e)",
                                               n, amp, w, lim) +
                                           fmt(", hard switch %.3g V (%.3gx)", wu, wu / std::max(w, 1e-300)));
        }
    metric(std::string("filter_step_sine_") + qn, ws);
    metric(std::string("filter_step_reduction_min_") + qn, red);
}

// ---- option sweep: the switch timing --------------------------------------------------------------------------
static void option_sweep(const Bench& b) {
    std::printf("option sweep (%s): mode switches P1 <-> T, P1 <-> P2, T <-> P2 at 0.45 mA, against always running; V\n",
                b.q.os == 8 ? "standard" : "hq");
    for (std::size_t n0 : {N0, std::size_t(48000)})
        for (double warm : {0.0, 0.005, 0.010, 0.020, 0.050, 0.100})
            for (double fade : {0.010, 0.020, 0.040}) {
                double w8 = 0.0, w1 = 0.0, d8 = 0.0;
                for (auto pr : {std::make_pair(Mode::P1, Mode::T), std::make_pair(Mode::T, Mode::P1),
                                std::make_pair(Mode::P1, Mode::P2), std::make_pair(Mode::P2, Mode::P1),
                                std::make_pair(Mode::T, Mode::P2), std::make_pair(Mode::P2, Mode::T)}) {
                    const SwitchResult r8 = mode_switch(b, pr.first, pr.second, 0.45, 8.0, warm, fade, n0);
                    const SwitchResult r1 = mode_switch(b, pr.first, pr.second, 0.45, 1.0, warm, fade, n0);
                    w8 = std::max(w8, r8.w); w1 = std::max(w1, r1.w); d8 = std::max(d8, r8.d);
                }
                std::printf("  switch at %4.2f s, warm %5.1f ms, fade %5.1f ms: worst step 1 V %.3g, 8 V %.3g; max|y-ref| 8 V %.3g\n",
                            double(n0) / FS, 1e3 * warm, 1e3 * fade, w1, w8, d8);
                std::fflush(stdout);
            }
}

// ---- 6. block sizes -------------------------------------------------------------------------------------------
static void test_block_sizes(const Bench& b) {
    const Vec x = sine(3.0, 220.0, 24000);
    Vec out[3];
    const std::size_t blocks[3] = {16, 64, 512};
    for (int k = 0; k < 3; ++k) {
        auto live = make_live(b, Mode::P1, 0.3);
        out[k] = run(*live, x, blocks[k], [&](std::size_t i) {
            if (i == 5120) { live->set_bias(0.55); live->set_mode(Mode::T); }        // T caps at 0.5
            if (i == 5632) live->set_lpf(Lpf::k7);                                  // during the switch
            if (i == 6144) live->set_bias(0.2);                                     // re-plan mid-ramp
            if (i == 10240) { live->set_mode(Mode::P2); live->set_bias(0.6); }
            if (i == 10752) live->set_mode(Mode::P1);                               // during the switch: waits
        });
    }
    check(out[0] == out[2] && out[1] == out[2],
          "blocks 16 / 64 / 512: bias, mode and filter changes at samples common to the three give the same output, bit for bit");
}

// ---- 7. NaN / inf audio and out-of-range targets ---------------------------------------------------------------
static void test_nonfinite(const Bench& b) {
    Vec x = sine(4.0, 220.0, 9600);
    for (std::size_t i = 4800; i < 6400; i += 37) x[i] = (i / 37) % 2 ? NAN : INFINITY;
    x[4900] = -INFINITY;
    x[5000] = 1e300;                                  // finite, far outside any signal
    auto live = make_live(b, Mode::P1, 0.45);
    const Vec y = run(*live, x, 64, [&](std::size_t i) {
        if (i == 4800) { live->set_mode(Mode::T); live->set_bias(0.2); }
        if (i == 4864) { live->set_bias(NAN); live->set_bias(INFINITY); live->set_mode(Mode(7)); }   // ignored
    });
    bool fin = true;
    for (double v : y) fin = fin && std::isfinite(v);
    check(fin && live->mode() == Mode::T && live->bias() == 0.2 && live->switch_failures() == 0,
          fmt("NaN / inf audio and NaN / inf / out-of-range targets during a switch and a ramp: output finite, the "
              "valid targets reached (mode %.0f, bias %.3g mA)", double(int(live->mode())), live->bias()));
}

// ---- 8. timings (informal) ------------------------------------------------------------------------------------
static void timings(const Bench& b) {
    using clk = std::chrono::steady_clock;
    const BiasLine& l = b.lines->line[0];
    Pack c = l.base;
    double i0[NI_MAX] = {c.i_dc[0], c.i_dc[1], c.i_dc[2]};
    const int N = 2000;
    auto t0 = clk::now();
    for (int k = 0; k < N; ++k) {
        const double rk = std::exp(l.log_rk(0.1 + 0.5 * k / N));
        double wac, wdc;
        l.weights(rk, wac, wdc);
        l.matrices(c, rk, wac);
        l.rest(c, wdc, i0);
    }
    auto t1 = clk::now();
    for (int k = 0; k < N / 10; ++k) {
        Pack e = l.base;
        double j0[NI_MAX];
        bool cv; int st;
        l.exact(e, 0.1 + 0.5 * k / (N / 10), j0, cv, st);
    }
    auto t2 = clk::now();
    auto s = make_stage(b, Mode::P1, 0.45);
    const Vec x = sine(1.0, 220.0, 4800);
    auto t3 = clk::now();
    run(*s, x, 64);
    auto t4 = clk::now();
    const double up = std::chrono::duration<double, std::micro>(t1 - t0).count() / N;
    const double ex = std::chrono::duration<double, std::micro>(t2 - t1).count() / (N / 10);
    const double bs = std::chrono::duration<double, std::micro>(t4 - t3).count() / 4800;
    const char* qn = b.q.os == 8 ? "standard" : "hq";
    if (!std::isfinite(c.x_dc[0])) std::printf("(timing pack non-finite)\n");
    metric(std::string("t_line_update_us_") + qn, up);
    metric(std::string("t_exact_us_") + qn, ex);
    metric(std::string("t_base_sample_us_") + qn, bs);
    std::printf("timing (%s, informal): line update %.3g us, exact() %.3g us, one base sample of the stage %.3g us (1 V)\n",
                qn, up, ex, bs);
}

int main(int argc, char** argv) {
    bool hq = false, options = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--hq") hq = true;
        if (a == "--options") options = true;
        if (a == "--options-only") { Bench b(STANDARD); option_sweep(b); return 0; }

    }
    auto t0 = std::chrono::steady_clock::now();
    std::vector<Quality> qs{STANDARD};
    if (hq) qs.push_back(HQ);
    for (const Quality& q : qs) {
        Bench b(q);
        test_line(b);
        test_rest(b);
        test_bias_moves(b, hq);
        test_mode_switches(b, hq);
        test_filter_switches(b);
        if (q.os == 8) { test_block_sizes(b); test_nonfinite(b); }
        if (options) option_sweep(b);
        timings(b);
    }
    check(!ALLOC_HOOKS || g_process_allocs == 0,
          fmt("no allocation in LiveStage::process() / Stage::process(), throughout (%.0f)", double(g_process_allocs)));
    metric("alloc_hooks", ALLOC_HOOKS ? 1 : 0);
    metric("process_allocations", double(g_process_allocs));
    metric("seconds", std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count());
    std::printf("{%s, \"failures\": %d}\n", g_json.str().c_str(), g_fail);
    return g_fail ? 1 : 0;
}
