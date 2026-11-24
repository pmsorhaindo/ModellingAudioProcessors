/* Offline numerical screen for adaa.py. Uses the shipped DK current solver,
 * resampler and post-filter, with the Python prototype's state and quadrature.
 * Included only by the research renderer and the optional CPU benchmark. */
#ifndef DK6AS6_ADAA_SCREEN_H
#define DK6AS6_ADAA_SCREEN_H

#include "../cpp/dk6as6_core.h"

namespace dk6as6::research {

struct Node { double t, w; };

// Gauss–Legendre nodes on [0,1], from adaa.quadrature.
inline constexpr Node Q2[] = {
    {0.21132486540518713, 0.5}, {0.7886751345948129, 0.5},
};
inline constexpr Node Q4[] = {
    {0.06943184420297371, 0.17392742256872684}, {0.33000947820757187, 0.3260725774312731},
    {0.6699905217924281, 0.3260725774312731}, {0.9305681557970262, 0.17392742256872684},
};
inline constexpr Node Q8[] = {
    {0.019855071751231912, 0.050614268145188344}, {0.10166676129318664, 0.11119051722668717},
    {0.2372337950418355, 0.15685332293894352}, {0.4082826787521751, 0.18134189168918088},
    {0.5917173212478248, 0.18134189168918088}, {0.7627662049581645, 0.15685332293894352},
    {0.8983332387068134, 0.11119051722668717}, {0.9801449282487681, 0.050614268145188344},
};

class Screen {
public:
    Status prepare(double fs, Quality q, const Settings& s, int order, int nodes, const Node* supplied = nullptr) {
        if (order != 1 && order != 2) return Status::bad_argument;
        if (supplied && nodes >= 2) {
            quadrature_ = supplied;
        } else {
            switch (nodes) {
                case 2: quadrature_ = Q2; break;
                case 4: quadrature_ = Q4; break;
                case 8: quadrature_ = Q8; break;
                default: return Status::bad_argument;
            }
        }
        if (!rs_.prepare(q.os, fs, s.fir) || !lpf_.prepare(fs * q.os, s.lpf)) return Status::bad_argument;
        const Status status = build_pack(c_, fs * q.os / (1.0 + 0.5 * order), s);
        if (status != Status::ok) return status;
        order_ = order;
        nodes_ = nodes;
        reset();
        return Status::ok;
    }

    void reset() {
        rs_.reset();
        lpf_.reset();
        stats_ = {};
        hist_ = {};
        std::copy_n(c_.p_dc, NV, hist_.p);
        std::copy_n(c_.i_dc, NI_MAX, hist_.i);
        std::copy_n(c_.i_dc, NI_MAX, hist_.q);
    }

    int latency_samples() const { return rs_.latency_samples(); }
    int up_delay_internal() const { return rs_.up_delay_internal(); }
    const Stats& stats() const { return stats_; }

    void process(const double* in, double* out, std::size_t n, const Record* rec = nullptr) {
        dkcore::ScopedFlushDenormals ftz;
        if (c_.grid) run<true>(in, out, n, rec);
        else run<false>(in, out, n, rec);
    }

private:
    struct History {
        double x[NX], prev_x[NX], older_x[NX];
        double p[NV], i[NI_MAX], q[NI_MAX], half[NI_MAX];
        double u, older_u;
    };

    template <bool Grid> void run(const double* in, double* out, std::size_t n, const Record* rec) {
        const int os = rs_.os();
        double u[dkcore::MAX_OS], y[dkcore::MAX_OS];
        for (std::size_t k = 0; k < n; ++k) {
            rs_.up(in[k], u);
            for (int j = 0; j < os; ++j) y[j] = lpf_.tick(tick<Grid>(u[j], rec, k * os + j));
            out[k] = rs_.down(y);
        }
    }

    template <bool Grid> double tick(double u, const Record* rec, std::size_t k) {
        auto& h = hist_;
        constexpr int ni = Grid ? 3 : 2;
        double p[NV];
        for (int r = 0; r < NV; ++r) {
            double g = 0.0;
            for (int j = 0; j < NX; ++j) g += c_.G[r][j] * h.x[j];
            p[r] = (g + c_.H[r][0] * u) + c_.p_dc[r];
        }
        double i[NI_MAX] = {}, half[NI_MAX] = {};
        if (order_ == 2) std::copy_n(h.half, ni, i);
        int iterations = 0;
        for (int node = 0; node < nodes_; ++node) {
            const auto [t, w] = quadrature_[node];
            double arg[NV];
            for (int r = 0; r < NV; ++r) arg[r] = h.p[r] + t * (p[r] - h.p[r]);
            double starts[3][NI_MAX] = {};
            for (int r = 0; r < ni; ++r) {
                starts[0][r] = 2 * h.i[r] - h.q[r];
                starts[1][r] = h.i[r];
                starts[2][r] = c_.i_dc[r];
            }
            const Solution s = Grid ? solve_grid_restarts(arg, c_.K, starts, c_.par, c_.g, c_.newton)
                                    : solve(arg, c_.K, starts[0][0], starts[0][1], c_.par, c_.newton);
            stats_.nonconverged += !s.ok;
            stats_.restarted += s.attempts > 1;
            stats_.out_of_law += s.v[3] < 0.0 || (Grid ? s.i[0] + s.i[1] < 0.0 : s.v[0] > 0.0);
            iterations += s.it;
            for (int r = 0; r < ni; ++r) {
                h.q[r] = h.i[r];
                h.i[r] = s.i[r];
                const double di = s.i[r] - c_.i_dc[r];
                i[r] += (order_ == 1 ? w : w * (1.0 - t)) * di;
                if (order_ == 2) half[r] += w * t * di;
            }
        }
        ++stats_.samples;
        stats_.iterations += iterations;
        stats_.max_iterations = std::max(stats_.max_iterations, iterations);
        const double uf = order_ == 1 ? (u + h.u) * 0.5 : (u + 4 * h.u + h.older_u) / 6;
        double xf[NX], xn[NX];
        for (int j = 0; j < NX; ++j)
            xf[j] = order_ == 1 ? (h.x[j] + h.prev_x[j]) * 0.5 : (h.x[j] + 4 * h.prev_x[j] + h.older_x[j]) / 6;
        for (int r = 0; r < NX; ++r) {
            double a = 0.0, c = 0.0;
            for (int j = 0; j < NX; ++j) a += c_.A[r][j] * xf[j];
            for (int j = 0; j < ni; ++j) c += c_.C[r][j] * i[j];
            xn[r] = a + c_.B[r][0] * uf + c;
        }
        double d = 0.0, f = 0.0;
        for (int j = 0; j < NX; ++j) d += c_.D[0][j] * xf[j];
        for (int j = 0; j < ni; ++j) f += c_.F[0][j] * i[j];
        if (rec) {
            for (const auto& observation : {std::pair{0, rec->vgk}, std::pair{1, rec->vg2k}}) {
                if (!observation.second) continue;
                const int r = observation.first;
                double g = 0.0, drop = 0.0;
                for (int j = 0; j < NX; ++j) g += c_.G[r][j] * xf[j];
                for (int j = 0; j < ni; ++j) drop += c_.K[r][j] * (i[j] + c_.i_dc[j]);
                observation.second[k] = c_.p_dc[r] + g + c_.H[r][0] * uf - drop;
            }
        }
        h.older_u = h.u;
        h.u = u;
        std::copy_n(h.prev_x, NX, h.older_x);
        std::copy_n(h.x, NX, h.prev_x);
        std::copy_n(xn, NX, h.x);
        std::copy_n(p, NV, h.p);
        std::copy_n(half, ni, h.half);
        return d + c_.E[0][0] * uf + f + c_.y_dc[0];
    }

    Pack c_{};
    History hist_{};
    int order_ = 0, nodes_ = 0;
    const Node* quadrature_ = nullptr;
    Stage::Resampler rs_;
    PostFilter lpf_;
    Stats stats_{};
};

}  // namespace dk6as6::research
#endif
