/* live_ref_6as6.h -- the continuous references for the 6AS6 stage's live bias and mode (#115), shared by
 * cpp/test_live_6as6.cpp (the core, volts) and plugin/tests/smoothing_test.cpp (the engine, full scale). Test code
 * only: it allocates and builds packs freely. Method: cpp/RESULTS-smoothing-6as6.md.
 *
 *   bias_reference()   a bias move b0 -> b1 at base sample n0: an exact pack at EVERY internal sample along the same
 *                      linear ramp (the Rk that draws each bias to 1e-14 in log Ik, solved by a secant on the
 *                      source-stepped DC solve, then build_pack_at_rk), adopted keeping the absolute histories; or,
 *                      with step, one exact pack (build_pack) adopted at n0: the unsmoothed swap. Independent of
 *                      BiasLine, its table and its rest maps.
 *   crossfade()        the ideal switch between two settings, each run continuously from the start: (1 - w) a + w b,
 *                      w = 0 before `start`, then (k + 1) / fade.
 */
#ifndef DK6AS6_LIVE_REF_H
#define DK6AS6_LIVE_REF_H

#include "dk6as6_core.h"

#include <memory>
#include <vector>

namespace dk6as6 {
namespace liveref {

using Vec = std::vector<double>;

inline Settings settings_of(Mode m, double bias, Lpf lpf = Lpf::off) {
    Settings s;
    s.mode = m;
    s.bias_ma = bias;
    s.lpf = lpf;
    s.experimental = m == Mode::P2;                  // P2 is never supported (the plugin builds it as experimental)
    return s;
}

// The Rk that draws `ma` exactly: secant on log Rk with the source-stepped DC solve (the one Brent uses), warm-started
// from the previous call. err: the last |log(Ik / ma)|.
struct ExactRk {
    Mode m;
    Settings s;
    GridParams g = grid();
    double t = 0.0, y = 0.0, slope = -1.0;
    double ly_of(double tt) const {
        OperatingPoint op{};
        if (detail::operating_point(std::exp(tt), s.par, s.newton, m, true, g, s.values, op) != Status::ok) return NAN;
        return std::log(op.ik * 1e3);
    }
    ExactRk(Mode mm, double ma) : m(mm), s(settings_of(mm, ma)) {
        double rk = 0.0;
        cathode_resistor(ma, s.par, s.newton, m, true, g, s.values, rk);
        t = std::log(rk);
        y = ly_of(t);
        slope = 1e-3 / (ly_of(t + 1e-3) - y);
    }
    double rk(double ma, double& err) {
        const double ly = std::log(ma);
        double ta = t + slope * (ly - y), fa = ly_of(ta) - ly;
        double tb = ta - slope * fa, fb = ly_of(tb) - ly;
        for (int k = 0; k < 12 && std::fabs(fb) > 1e-14 && fb != fa; ++k) {
            const double tn = tb - fb * (tb - ta) / (fb - fa);
            ta = tb; fa = fb;
            tb = tn; fb = ly_of(tb) - ly;
        }
        if (fb != fa && std::fabs(tb - ta) > 0.0) slope = (tb - ta) / (fb - fa);
        t = tb; y = fb + ly;
        err = std::fabs(fb);
        return std::exp(tb);
    }
};

/* Stage output (V) for input x (V, after the drive) with a bias move b0 -> b1 at base sample n0 over `ramp` base
 * samples: continuous, or with step the unsmoothed swap. mode's bias must be valid at b0 and b1. max_err (optional):
 * the largest |log(Ik / bias)| of the reference's packs. */
inline Vec bias_reference(double fs, const Quality& q, Mode m, const Vec& x, double b0, double b1, std::size_t n0, int ramp,
                          bool step, double* max_err = nullptr, Lpf lpf = Lpf::off) {
    std::unique_ptr<Stage> s(new Stage);
    s->prepare(fs, q, settings_of(m, b0, lpf));
    Stage::Resampler r;
    r.prepare(q.os, fs, DEFAULT_FIR);
    ExactRk ex(m, b0);
    const int os = q.os;
    Vec y(x.size());
    double u[dkcore::MAX_OS], v[dkcore::MAX_OS], last = b0;
    Pack p;
    for (std::size_t i = 0; i < x.size(); ++i) {
        r.up(x[i], u);
        for (int j = 0; j < os; ++j) {
            double bb = b0;
            if (i >= n0) {
                const double k = double(i - n0) + double(j + 1) / os;
                bb = step || k >= ramp ? b1 : b0 + k * (b1 - b0) / ramp;
            }
            if (bb != last) {
                double err = 0.0;
                if (step) build_pack(p, fs * os, settings_of(m, bb, lpf));
                else build_pack_at_rk(p, fs * os, settings_of(m, bb, lpf), ex.rk(bb, err));
                if (max_err && err > *max_err) *max_err = err;
                s->adopt_pack(p);
                last = bb;
            }
            s->process_internal(&u[j], &v[j], 1);
        }
        y[i] = r.down(v);
    }
    return y;
}

inline Vec crossfade(const Vec& a, const Vec& b, std::size_t start, int fade) {
    Vec y(a.size());
    for (std::size_t i = 0; i < a.size(); ++i) {
        const double w = i < start ? 0.0 : std::fmin(1.0, double(i - start + 1) / double(fade));
        y[i] = (1.0 - w) * a[i] + w * b[i];
    }
    return y;
}

}  // namespace liveref
}  // namespace dk6as6
#endif
