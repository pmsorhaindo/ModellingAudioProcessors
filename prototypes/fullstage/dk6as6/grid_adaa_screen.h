/* Offline-only counterpart of grid_adaa.py. No code in the shipped core
 * uses this header. Table construction is in Python, outside the timed loop. */
#ifndef DK6AS6_GRID_ADAA_SCREEN_H
#define DK6AS6_GRID_ADAA_SCREEN_H

#include <algorithm>

#include "../cpp/dk6as6_core.h"

namespace dk6as6::grid_research {

constexpr double TABLE_MIN = -4.0, TABLE_MAX = 32.0, STEP = 1.0 / 256, EPS = 0.002;
constexpr int ROWS = int((TABLE_MAX - TABLE_MIN) / STEP) + 1;
constexpr int COLS = 6;

struct Values { double f, fp, fpp, fppp, f1, f2; };
struct Pair { double f, fp; };

inline Values evaluate(const double* table, double q) {
    if (q < TABLE_MIN) return {};
    const int k = int(std::min((q - TABLE_MIN) / STEP, double(ROWS - 1)));
    const double t = q - (TABLE_MIN + k * STEP);
    const double* row = table + k * COLS;
    const double a = row[0], b = row[1], c = row[2], d = row[3], e = row[4], f = row[5];
    return {((a*t + b)*t + c)*t + d, (3*a*t + 2*b)*t + c, 6*a*t + 2*b, 6*a,
            (((a/4*t + b/3)*t + c/2)*t + d)*t + e,
            ((((a/20*t + b/12)*t + c/6)*t + d/2)*t + e)*t + f};
}

template <int Order>
inline Pair divided(const double* table, double x, double y, Values ex, Values ey) {
    const double d = x - y;
    if (std::abs(d) >= EPS) {
        const double f = (Order == 1 ? ex.f1 - ey.f1 : ex.f2 - ey.f2) / d;
        return {f, ((Order == 1 ? ex.f : ex.f1) - f) / d};
    }
    const Values m = evaluate(table, (x + y) / 2);
    if constexpr (Order == 1)
        return {m.f + d*d*m.fpp/24, m.fp/2 + d*m.fpp/12 + d*d*m.fppp/48};
    return {m.f1 + d*d*m.fp/24, m.f/2 + d*m.fp/12 + d*d*m.fpp/48};
}

struct Branch {
    const double* table = nullptr;
    const double* instant = nullptr;
    double prev = 0, older = 0, slope = 0;
    Values ep{}, eo{};

    void reset(double dc) {
        prev = older = dc;
        ep = eo = evaluate(table, dc);
        slope = ep.f1;
    }

    template <int Order>
    Pair current(double q) const {
        if (q < TABLE_MIN && prev < TABLE_MIN && (Order == 1 || older < TABLE_MIN)) return {};
        const Values cur = evaluate(table, q);
        if constexpr (Order == 1) return divided<1>(table, q, prev, cur, ep);
        double p = prev, o = older;
        Values e = ep;
        if (std::abs(q - p) > std::abs(q - o)) { std::swap(p, o); e = eo; }
        const double d = q - o;
        if (std::abs(d) >= EPS) {
            const Pair left = divided<2>(table, q, p, cur, e);
            const double f = 2 * (left.f - slope) / d;
            return {f, (2 * left.fp - f) / d};
        }
        constexpr double nodes[] = {0.1127016653792583, 0.5, 0.8872983346207417};
        constexpr double weights[] = {5.0 / 18, 4.0 / 9, 5.0 / 18};
        Pair result{};
        for (int i = 0; i < 3; ++i) {
            const double t = nodes[i];
            for (int j = 0; j < 3; ++j) {
                const double u = nodes[j];
                const Values a = evaluate(table, t*q + (1-t)*(u*p + (1-u)*o));
                const double w = 2 * (1-t) * weights[i] * weights[j];
                result.f += w * a.f;
                result.fp += w * t * a.fp;
            }
        }
        return result;
    }

    template <int Order>
    void advance(double v) {
        const Values e = evaluate(table, v);
        if constexpr (Order == 2) {
            slope = divided<2>(table, v, prev, e, ep).f;
            older = prev;
            eo = ep;
        }
        prev = v;
        ep = e;
    }

    template <int Order>
    bool load(double q, double resistance, double& v, Pair& result, bool& outside) const {
        if (!std::isfinite(q)) return false;
        outside |= q > TABLE_MAX;
        const double guess = evaluate(instant, q).f;
        double hi = q, lo = -INFINITY;
        v = hi - resistance * guess;
        for (int n = 0; n < 40; ++n) {
            outside |= v > TABLE_MAX;
            const Pair a = current<Order>(v);
            const double r = v + resistance * a.f - q;
            const double denominator = 1 + resistance * a.fp;
            if (std::abs(r) < 1e-12) {
                result = {a.f, a.fp / denominator};
                return true;
            }
            if (r < 0) lo = v; else hi = v;
            const double nxt = v - r / denominator;
            v = lo < nxt && nxt < hi ? nxt : (lo + hi) / 2;
            if (!std::isfinite(v)) return false;
        }
        return false;
    }
};

class Screen {
public:
    Status prepare(double fs, int os, const Settings& settings, int order,
                   const double* table, const double* instant) {
        if ((order != 1 && order != 2) || !table || !instant) return Status::bad_argument;
        if (!rs_.prepare(os, fs, settings.fir) || !lpf_.prepare(fs * os, settings.lpf)) return Status::bad_argument;
        const Status status = build_pack(c_, fs * os, settings);
        if (status != Status::ok) return status;
        order_ = order;
        branch_.table = table;
        branch_.instant = instant;
        reset();
        return Status::ok;
    }

    void reset() {
        rs_.reset();
        lpf_.reset();
        stats_ = {};
        table_outside_ = 0;
        std::fill_n(x_, NX, 0.0);
        std::copy_n(c_.i_dc, 2, i_);
        std::copy_n(i_, 2, previous_);
        branch_.reset(c_.op.v[0]);
    }

    int latency_samples() const { return rs_.latency_samples(); }
    int up_delay_internal() const { return rs_.up_delay_internal(); }
    const Stats& stats() const { return stats_; }
    std::uint64_t table_outside() const { return table_outside_; }

    void process(const double* in, double* out, std::size_t n, const Record* rec = nullptr) {
        dkcore::ScopedFlushDenormals ftz;
        if (order_ == 1) run<1>(in, out, n, rec);
        else run<2>(in, out, n, rec);
    }

private:
    struct Trial {
        Solution sol{};
        double jac[2][4]{};
        double r[2]{};
    };

    template <int Order, bool Jac>
    bool trial(const double (&p)[NV], const double (&i)[2], Trial& t, bool& outside) const {
        const auto& K = c_.K;
        double q[NV];
        for (int r = 0; r < NV; ++r) q[r] = p[r] - K[r][0] * i[0] - K[r][1] * i[1];
        auto& s = t.sol;
        Pair grid;
        if (!branch_.load<Order>(q[0], K[0][2], s.v[0], grid, outside)) return false;
        for (int r = 1; r < NV; ++r) s.v[r] = q[r] - K[r][2] * grid.f;

        double ik, dik[4], f, df[4];
        bool raised = false;
        detail::cathode_share<Jac>(s.v[0], s.v[1], s.v[2], s.v[3], c_.par, ik, dik, f, df, raised);
        const double ipk = ik - grid.f;
        s.i[0] = ipk * f;
        s.i[1] = ipk - s.i[0];
        s.i[2] = grid.f;
        t.r[0] = i[0] - s.i[0];
        t.r[1] = i[1] - s.i[1];
        s.fv = voltage_norm(t.r);
        if (raised || !std::isfinite(s.fv)) return false;
        if constexpr (Jac) {
            double cathode_drop = 0, share_drop = 0;
            for (int j = 0; j < NV; ++j) {
                cathode_drop += K[j][2] * dik[j];
                share_drop += K[j][2] * df[j];
            }
            dik[0] -= grid.fp * (1 + cathode_drop);
            df[0] -= grid.fp * share_drop;
            for (int j = 0; j < NV; ++j) {
                t.jac[0][j] = dik[j] * f + ipk * df[j];
                t.jac[1][j] = dik[j] - t.jac[0][j];
            }
        }
        return true;
    }

    double voltage_norm(const double (&r)[2]) const {
        double value = 0.0;
        for (int j = 0; j < NV; ++j) value = std::max(value, std::abs(c_.K[j][0]*r[0] + c_.K[j][1]*r[1]));
        return value;
    }

    template <int Order>
    Solution solve(const double (&p)[NV], const double* start, bool& outside) const {
        // Same voltage residual, step limits and backtracking as the DK
        // two-current solve. Its law now substitutes the scalar grid root.
        double i[2] = {start[0], start[1]};
        Trial t;
        Solution best{};
        best.fv = INFINITY;
        if (!trial<Order, true>(p, i, t, outside)) return best;
        best = t.sol;
        for (int n = 1; n <= c_.newton.max_it; ++n) {
            double j[2][2] = {{1, 0}, {0, 1}};
            for (int a = 0; a < 2; ++a)
                for (int b = 0; b < 2; ++b)
                    for (int r = 0; r < NV; ++r) j[a][b] += t.jac[a][r] * c_.K[r][b];
            const double det = j[0][0]*j[1][1] - j[0][1]*j[1][0];
            const double step[2] = {-(j[1][1]*t.r[0] - j[0][1]*t.r[1]) / det,
                                     (j[1][0]*t.r[0] - j[0][0]*t.r[1]) / det};
            if (!std::isfinite(step[0]) || !std::isfinite(step[1])) break;
            Trial next;
            double lam = 1.0;
            double candidate[2] = {i[0] + step[0], i[1] + step[1]};
            bool valid = trial<Order, true>(p, candidate, next, outside);
            if (!(valid && (next.sol.fv < t.sol.fv || t.sol.fv < c_.newton.tol_f))) {
                for (int k = 1; k < LS_MAX; ++k) {
                    lam *= 0.5;
                    candidate[0] = i[0] + lam * step[0];
                    candidate[1] = i[1] + lam * step[1];
                    valid = trial<Order, false>(p, candidate, next, outside);
                    if (valid && next.sol.fv < t.sol.fv) break;
                }
                if (valid) valid = trial<Order, true>(p, candidate, next, outside);
            }
            best.it = n;
            if (!valid) break;
            if (lam * voltage_norm(step) < c_.newton.tol_v && next.sol.fv < c_.newton.tol_f) {
                next.sol.ok = true;
                next.sol.it = n;
                return next.sol;
            }
            if (next.sol.fv < best.fv) { best = next.sol; best.it = n; }
            t = next;
            std::copy_n(candidate, 2, i);
        }
        return best;
    }

    template <int Order>
    double tick(double u, const Record* rec, std::size_t index) {
        double p[NV];
        for (int r = 0; r < NV; ++r) {
            double g = 0.0;
            for (int j = 0; j < NX; ++j) g += c_.G[r][j] * x_[j];
            p[r] = (g + c_.H[r][0] * u) + c_.p_dc[r];
        }
        bool outside = false;
        const double extrapolated[2] = {2*i_[0] - previous_[0], 2*i_[1] - previous_[1]};
        const double* starts[] = {extrapolated, i_, c_.i_dc};
        Solution s{};
        s.fv = INFINITY;
        int iterations = 0;
        for (int k = 0; k < 3; ++k) {
            const Solution candidate = solve<Order>(p, starts[k], outside);
            if (k == 1) ++stats_.restarted;
            iterations += candidate.it;
            if (candidate.ok || candidate.fv < s.fv) s = candidate;
            if (s.ok) break;
        }
        table_outside_ += outside;
        ++stats_.samples;
        stats_.iterations += iterations;
        stats_.max_iterations = std::max(stats_.max_iterations, iterations);
        stats_.nonconverged += !s.ok;
        if (!std::isfinite(s.fv)) return NAN;
        stats_.out_of_law += (s.i[0] + s.i[1] < 0 || s.v[3] < 0);
        branch_.advance<Order>(s.v[0]);
        std::copy_n(i_, 2, previous_);
        std::copy_n(s.i, 2, i_);
        if (rec) {
            if (rec->vgk) rec->vgk[index] = s.v[0];
            if (rec->vg2k) rec->vg2k[index] = s.v[1];
            if (rec->ig1) rec->ig1[index] = s.i[2];
        }
        double di[NI_MAX];
        for (int k = 0; k < NI_MAX; ++k) di[k] = s.i[k] - c_.i_dc[k];
        double d = 0.0, f = 0.0;
        for (int j = 0; j < NX; ++j) d += c_.D[0][j] * x_[j];
        for (int j = 0; j < NI_MAX; ++j) f += c_.F[0][j] * di[j];
        const double y = d + c_.E[0][0]*u + f + c_.y_dc[0];
        double next[NX];
        for (int r = 0; r < NX; ++r) {
            double a = 0.0, c = 0.0;
            for (int j = 0; j < NX; ++j) a += c_.A[r][j] * x_[j];
            for (int j = 0; j < NI_MAX; ++j) c += c_.C[r][j] * di[j];
            next[r] = a + c_.B[r][0]*u + c;
        }
        std::copy_n(next, NX, x_);
        return y;
    }

    template <int Order>
    void run(const double* in, double* out, std::size_t n, const Record* rec) {
        const int os = rs_.os();
        double u[dkcore::MAX_OS], y[dkcore::MAX_OS];
        for (std::size_t k = 0; k < n; ++k) {
            rs_.up(in[k], u);
            for (int j = 0; j < os; ++j) y[j] = lpf_.tick(tick<Order>(u[j], rec, k * os + j));
            out[k] = rs_.down(y);
        }
    }

    Pack c_{};
    double x_[NX]{}, i_[2]{}, previous_[2]{};
    Branch branch_{};
    int order_ = 0;
    Stage::Resampler rs_;
    PostFilter lpf_;
    Stats stats_{};
    std::uint64_t table_outside_ = 0;
};

}  // namespace dk6as6::grid_research
#endif
