/* bias_options.cpp -- the bias-update options of #73, measured against one continuous reference
 * (cpp/RESULTS-smoothing.md quotes its output).
 *
 *   c++ -std=c++17 -O2 -o bias_options cpp/bias_options.cpp && ./bias_options          # table + one JSON line
 *   ./bias_options --smoke                                                             # one move (cpp/tests)
 *
 * Every option moves the bias 0.5 -> 2.0 and 2.0 -> 0.5 (the plugin's full range) at 0.1 s into a 0.3 s signal
 * (silence, or a 220 Hz sine of 1 V or 8 V peak at the stage input), 48 kHz, in blocks of 64 unless stated. The
 * intended move is a 20 ms linear ramp. The reference is that ramp with an exact pack (build_coeffs) at every
 * internal sample (as cpp/test_core.cpp test_bias_smoothing). Metric: worst step = max |r[n] - r[n-1]|,
 * r = y - y_ref, V at the stage output (1 V = 1/64 FS in the plugin).
 *
 *   unsmoothed      request_bias(b1) at the move: one pack swap at a block boundary (the behaviour before #73)
 *   swaps/B         rate-limited many small swaps: request_bias(ramp value) at every block start, blocks of B
 *                   (B = 240 is the wrapper's 5 ms worker period; any B is a host block size)
 *   crossfade       two stages from the same state, one swapped to b1 at the move; output crossfaded over 20 ms
 *   interp-op       the bias line's exact matrices per base sample, with the operating point and x0 interpolated
 *                   linearly between the two end packs (finish_coeffs as for any published pack)
 *   bias line       the chosen approach: StageBase::set_bias (dk_core.h "bias smoothing")
 * Timings (informal, median of repeated calls with std::chrono::steady_clock on this machine, not a benchmark,
 * cpp/BENCHMARK.md): build_coeffs (what request_bias builds), its matrix part, one bias-line update without and with
 * the warm-started DC solve, and the Newton iterations of that solve for a full-range jump.
 */
#include "dk_core.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

using namespace dkcore;
using Vec = std::vector<double>;
static const double FS = 48000.0, RAMP_S = 0.020;
static const size_t N0 = 4800, N = 14400;
static const int L = 960;

static Vec sine(double amp) {
    Vec x(N);
    for (size_t i = 0; i < N; ++i) x[i] = amp * std::sin(2 * PI * 220.0 * double(i) / FS);
    return x;
}
static double ramp_b(double b0, double b1, double k) { return k <= 0 ? b0 : k >= L ? b1 : b0 + k * (b1 - b0) / L; }

// a Stage at internal rate with its own resampler; pack(i, j, c) may fill c for internal sample j of base sample i
static Vec run_internal(Quality q, double b0, const Vec& x, const std::function<bool(size_t, int, Coeffs&)>& pack) {
    Stage* s = new Stage;
    s->prepare(FS, q, b0);
    Resampler r;
    r.prepare(q.os);
    Vec y(x.size());
    double u[MAX_OS], v[MAX_OS];
    for (size_t i = 0; i < x.size(); ++i) {
        r.up(x[i], u);
        for (int j = 0; j < q.os; ++j) {
            Coeffs c;
            if (pack(i, j, c)) s->publish(c);
            s->process_internal(&u[j], &v[j], nullptr, nullptr, 1);
        }
        y[i] = r.down(v);
    }
    delete s;
    return y;
}
static Vec reference(Quality q, double b0, double b1, const Vec& x) {
    double last = b0;
    return run_internal(q, b0, x, [&](size_t i, int j, Coeffs& c) {
        const double b = i < N0 ? b0 : ramp_b(b0, b1, double(i - N0) + double(j + 1) / q.os);
        if (b == last) return false;
        last = b;
        build_coeffs(c, FS * q.os, b, q.prewarp_hz);
        return true;
    });
}
// blocks of B; at each block start f(stage, i)
static Vec run_blocks(Quality q, double b0, const Vec& x, size_t B, const std::function<void(Stage&, size_t)>& f) {
    Stage* s = new Stage;
    s->prepare(FS, q, b0);
    s->set_bias_ramp(RAMP_S);
    Vec y(x.size());
    for (size_t i = 0; i < x.size(); i += B) {
        f(*s, i);
        s->process(x.data() + i, y.data() + i, std::min(B, x.size() - i));
    }
    delete s;
    return y;
}
static double worst_step(const Vec& y, const Vec& r) {
    double w = 0.0;
    for (size_t i = 1; i < y.size(); ++i) w = std::max(w, std::fabs((y[i] - r[i]) - (y[i - 1] - r[i - 1])));
    return w;
}
static double max_diff(const Vec& y, const Vec& r) {
    double w = 0.0;
    for (size_t i = 0; i < y.size(); ++i) w = std::max(w, std::fabs(y[i] - r[i]));
    return w;
}

struct Row { std::string option; double step[2][3]; double diff[2][3]; };   // [move up/down][0 V, 1 V, 8 V]

template <class F> static double median_us(F f, int reps) {
    std::vector<double> t;
    for (int i = 0; i < reps; ++i) {
        const auto a = std::chrono::steady_clock::now();
        f();
        const auto b = std::chrono::steady_clock::now();
        t.push_back(std::chrono::duration<double, std::micro>(b - a).count());
    }
    std::sort(t.begin(), t.end());
    return t[t.size() / 2];
}

int main(int argc, char** argv) {
    const bool smoke = argc > 1 && std::string(argv[1]) == "--smoke";
    const Quality q = STANDARD;
    const double amps[3] = {0.0, 1.0, 8.0};
    detail::BiasLine line;
    line.build(FS * q.os, q.prewarp_hz, 0.5, 2.0);
    Coeffs P[2];
    build_coeffs(P[0], FS * q.os, 0.5, q.prewarp_hz);
    build_coeffs(P[1], FS * q.os, 2.0, q.prewarp_hz);

    const std::vector<std::string> names = {"unsmoothed", "swaps/512", "swaps/240", "swaps/64", "swaps/16",
                                            "crossfade", "interp-op", "bias line"};
    std::vector<Row> rows(names.size());
    double thump[2][3] = {};          // max |ref - the same signal at constant b0|: the circuit's own transient
    for (size_t o = 0; o < names.size(); ++o) rows[o].option = names[o];
    for (int dir = 0; dir < 2; ++dir)
        for (int a = 0; a < 3; ++a) {
            if (smoke && (dir == 1 || a == 1)) continue;
            const double b0 = dir ? 2.0 : 0.5, b1 = dir ? 0.5 : 2.0;
            const Vec x = sine(amps[a]);
            const Vec ref = reference(q, b0, b1, x);
            std::vector<Vec> ys(names.size());
            ys[0] = run_blocks(q, b0, x, 64, [&](Stage& s, size_t i) { if (i == N0) s.request_bias(b1); });
            const size_t Bs[4] = {512, 240, 64, 16};
            for (int k = 0; k < 4; ++k) {
                // the move starts at N0 whatever the block size; each block start takes the ramp's value there
                // (a block of 512 straddles the move at 4800: it takes its share of the ramp from its start, 4608)
                double last = b0;
                ys[1 + k] = run_blocks(q, b0, x, Bs[k], [&](Stage& s, size_t i) {
                    const double b = i < N0 ? b0 : ramp_b(b0, b1, double(i + Bs[k] - N0));
                    if (i + Bs[k] > N0 && b != last) { s.request_bias(b); last = b; }
                });
            }
            {   // crossfade between a stage that stays at b0 and one swapped to b1 at N0 (same state up to N0)
                const Vec still = run_blocks(q, b0, x, 64, [](Stage&, size_t) {});
                const Vec& sw = ys[0];
                Vec y(N);
                for (size_t i = 0; i < N; ++i) {
                    const double w = i < N0 ? 0.0 : std::min(1.0, double(i - N0 + 1) / L);
                    y[i] = (1.0 - w) * still[i] + w * sw[i];
                }
                ys[5] = y;
                thump[dir][a] = max_diff(ref, still);
            }
            {   // exact matrices from the line, operating point and x0 interpolated between the end packs
                const Coeffs& A = P[dir], &B = P[1 - dir];
                double last = b0;
                ys[6] = run_internal(q, b0, x, [&](size_t i, int j, Coeffs& c) {
                    if (j != 0 || i < N0) return false;
                    const double b = ramp_b(b0, b1, double(i - N0 + 1));
                    if (b == last) return false;
                    last = b;
                    if (b == b1) { c = B; return true; }
                    c = A;
                    line.matrices(c, b);
                    const double w = line.weight(b), wa = line.weight(A.bias), wb = line.weight(B.bias);
                    const double t = (w - wa) / (wb - wa);
                    auto lerp = [&](const double* pa, const double* pb, double* po, int n) {
                        for (int m = 0; m < n; ++m) po[m] = pa[m] + t * (pb[m] - pa[m]);
                    };
                    lerp(&A.op.vgk, &B.op.vgk, &c.op.vgk, 4);
                    lerp(A.op.nodes, B.op.nodes, c.op.nodes, NNODES);
                    lerp(A.x0, B.x0, c.x0, NX);
                    c.bias = b;
                    return true;
                });
            }
            ys[7] = run_blocks(q, b0, x, 64, [&](Stage& s, size_t i) { if (i == N0) s.set_bias(b1); });
            for (size_t o = 0; o < names.size(); ++o) {
                rows[o].step[dir][a] = worst_step(ys[o], ref);
                rows[o].diff[dir][a] = max_diff(ys[o], ref);
            }
        }

    std::printf("worst step vs the continuous reference, V at the stage output (max |y - ref| in brackets); "
                "standard 8x, 48 kHz, 20 ms move\n");
    std::printf("| option | up, silence | up, 1 V | up, 8 V | down, silence | down, 1 V | down, 8 V |\n|---|---|---|---|---|---|---|\n");
    for (const Row& r : rows) {
        std::printf("| %s |", r.option.c_str());
        for (int dir = 0; dir < 2; ++dir)
            for (int a = 0; a < 3; ++a) {
                if (smoke && (dir == 1 || a == 1)) { std::printf(" - |"); continue; }
                std::printf(" %.3g (%.3g) |", r.step[dir][a], r.diff[dir][a]);
            }
        std::printf("\n");
    }

    std::printf("| (the move itself: max |ref - constant bias|) |");
    for (int dir = 0; dir < 2; ++dir)
        for (int a = 0; a < 3; ++a) std::printf(smoke && (dir == 1 || a == 1) ? " - |" : " %.3g V |", thump[dir][a]);
    std::printf("\n");

    // timings (skipped in --smoke)
    double t_build = 0, t_mats = 0, t_upd = 0, t_upd_dc = 0;
    int it_jump = 0;
    if (!smoke) {
        double sink = 0;
        t_build = median_us([&] { Coeffs c; build_coeffs(c, FS * q.os, 1.3, q.prewarp_hz); sink += c.op.vak; }, 401);
        t_mats = median_us([&] {
            detail::Full m, m0;
            detail::build_full(m, FS * q.os, 1.3, false, q.prewarp_hz, Values{});
            detail::build_full(m0, FS * q.os, 1.3, true, 0.0, Values{});
            sink += m.A[0][0] + m0.K[0][0];
        }, 401);
        Coeffs c = P[0];
        double i0[2] = {c.op.ig, c.op.ia}, v[2] = {c.op.vgk, c.op.vak}, b = 0.5;
        auto upd = [&](bool dc) {
            b += 1.5 / L;
            if (b > 2.0) b = 0.5;
            line.matrices(c, b);
            detail::rereference(c, i0, v, dc, nullptr);
            sink += c.x0[0];
        };
        t_upd = median_us([&] { upd(false); }, 4001);
        t_upd_dc = median_us([&] { upd(true); }, 4001);
        // a full-range jump, 0.5 -> 2.0, from the 0.5 operating point: Newton iterations of the one DC solve
        Coeffs j = P[0];
        double ji[2] = {j.op.ig, j.op.ia}, jv[2] = {j.op.vgk, j.op.vak};
        bool conv = false;
        line.matrices(j, 2.0);
        detail::rereference(j, ji, jv, true, &conv, &it_jump);
        if (!conv) it_jump = -it_jump;
        std::printf("\ntimings (informal, median, us): build_coeffs %.2f, of which matrices (2 x build_full) %.2f; "
                    "bias-line update %.3f, with the warm-started DC solve %.3f; full-range jump DC solve: %d "
                    "Newton iterations%s (sink %g)\n", t_build, t_mats, t_upd, t_upd_dc, it_jump < 0 ? -it_jump : it_jump,
                    it_jump < 0 ? " (NOT converged)" : "", sink * 0.0);
    }
    std::printf("{\"smoke\": %d", smoke ? 1 : 0);
    for (const Row& r : rows)
        for (int dir = 0; dir < 2; ++dir)
            for (int a = 0; a < 3; ++a)
                if (!(smoke && (dir == 1 || a == 1)))
                    std::printf(", \"%s|%s|%g\": %.6g", r.option.c_str(), dir ? "down" : "up", amps[a], r.step[dir][a]);
    std::printf(", \"t_build_us\": %.4g, \"t_matrices_us\": %.4g, \"t_update_us\": %.4g, \"t_update_dc_us\": %.4g, "
                "\"jump_dc_iterations\": %d}\n", t_build, t_mats, t_upd, t_upd_dc, it_jump);
    return 0;
}
