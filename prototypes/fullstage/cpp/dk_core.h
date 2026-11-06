/* dk_core.h -- header-only realtime C++17 core of the 12AX7 nodal-DK stage (#19).
 *
 * A port of dk/stage.py + dk/solver.py + dk/circuit.py + dk/triode.py + the "fir" path of dk/resample.py. The Python
 * stage stays the reference: cpp/vectors.txt (exported by cpp/export_vectors.py) holds its outputs, and
 * cpp/test_core.cpp checks this core against them (tolerances and method in cpp/README.md).
 *
 * Fixed-size state, as in Stage: 6 capacitor history states x, the 2 Newton unknowns v = (Vgk, Vak) and the previous
 * solution for the predictor. Fixed matrices, as in Stage.m: A 6x6, B 6x2, C 6x2, D 5x6, E 5x2, F 5x2, G 2x6,
 * H 2x2, K 2x2 (circuit.build), all in one trivially copyable Coeffs pack. Nothing here allocates: no std::vector,
 * no new, no exceptions. process() is noexcept, lock-free and wait-free.
 *
 * Threads (cpp/README.md, "Coefficient swap"):
 *   prepare()                   non-RT, and never concurrently with process() (a host's prepare / setup call).
 *   request_bias() / publish()  any non-RT thread. They build a new Coeffs pack (matrices + DC operating point) off
 *                               the audio thread, then publish it lock-free through a triple buffer. A std::mutex
 *                               serialises producers only; the audio thread never touches it.
 *   process() / reset()         the audio thread. A published pack is adopted at the start of the next block.
 *   set_drive() / set_output()  any thread (relaxed atomics, read once per block).
 * On a swap the state x, v is kept, not reset: bias changes only Rk, so the companion conductances (Gx) and with them
 * the meaning of x are unchanged, and the circuit moves from its present state to the new operating point, as a real
 * pot would. reset() goes to the active pack's operating point.
 *
 * Denormals: process() and process_internal() hold a ScopedFlushDenormals (FTZ + DAZ on x86 SSE, FPCR.FZ on
 * AArch64), restoring the caller's mode on return (RESEARCH-numerics.md c).
 *
 * Non-convergence: solve() is the #7 contract of dk/solver.py (comments there and at solve() below): at the
 * iteration cap it returns the best finite iterate, flagged; the stage keeps it as the sample's state and counts it
 * (Stats::nonconverged, nonconv_block, nonconverged_total()). The DC solve has no state to fall back on, so a pack
 * whose DC solve fails is never built (BuildStatus::dc_nonconvergence) and never published.
 *
 * Triode math: TriodeMath::libm (default, as in Python) or TriodeMath::fast (dk/fastmath.h, #18), per pack,
 * dispatched once per block.
 *
 * Do not build with -ffast-math: the fallback relies on NaN / inf comparisons.
 */
#ifndef DK_CORE_H
#define DK_CORE_H

#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <type_traits>
#include <utility>

#if defined(__x86_64__) || defined(_M_X64) || (defined(__i386__) && defined(__SSE2__))
#include <xmmintrin.h>
#define DKCORE_X86_SSE 1
#endif

#include "../dk/fastmath.h"

#if defined(__FAST_MATH__)
#error "dk_core.h needs IEEE semantics (the non-convergence fallback tests NaN / inf): do not build with -ffast-math"
#endif

namespace dkcore {

// ---- constants: dk/triode.py, dk/circuit.py, dk/solver.py, dk/stage.py, dk/resample.py ----------------------------

struct TriodeParams { double G, mu, gamma, C, Gg, xi, Cg, Ig0; };
// Dempwolf-Zoelzer DAFx-11, Table 1, RSD-1 (dk/triode.py DZ_12AX7_RSD1)
inline constexpr TriodeParams DZ_12AX7_RSD1{2.242e-3, 103.2, 1.26, 3.40, 6.177e-4, 1.314, 9.901, 8.025e-8};

// dk/circuit.py VALUES (chosen example values, not Culture Vulture values)
struct Values {
    double HT = 300.0, Rs = 1e3, Cin = 22e-9, Rg = 1e6, Rgs = 68e3, Ra = 100e3, Rk = 1.5e3, Ck = 22e-6,
           Cout = 22e-9, Rl = 1e6, CCG = 2.3e-12, CGP = 2.4e-12, CCP = 0.9e-12;
};
inline constexpr double PI = 3.141592653589793;   // == np.pi
inline constexpr double SMALL_C = 1e-9;    // F: capacitors below this are pre-warped (circuit.SMALL_C)

struct Newton { int max_it = 50; double tol_v = 1e-6; double tol_f = 1e-6; };   // solver.MAX_IT, TOL_V, TOL_F
inline constexpr int LS_MAX = 10;
inline constexpr double VCRIT = 0.0;

enum class TriodeMath : std::uint8_t { libm, fast };

struct Quality { int os; double prewarp_hz; };          // prewarp_hz <= 0: plain trapezoidal
inline constexpr Quality HQ{16, 0.0}, STANDARD{8, 0.0}, ECO{4, 17300.0};   // stage.QUALITY, PREWARP_HZ
inline constexpr int MAX_OS = 16;                       // fixed buffers: os = 2, 4, 8 or 16

inline constexpr int FIR_FIRST_TAPS = 127, FIR_LATER_TAPS = 23;   // resample.FIR_FIRST / FIR_LATER, Kaiser beta 9
inline constexpr double FIR_BETA = 9.0;

// node order of circuit.NODES: src in1 gl g k a out ht
enum Node { SRC, IN1, GL, GRID, CATH, ANODE, OUT, HTN, NNODES };
inline constexpr int NX = 6, NNL = 2, NU = 2, NOUT = 5, NS = NNODES + NU;

// ---- triode (dk/triode.py): same expressions, same order --------------------------------------------------------

struct Currents { double ig, ia, j00, j01, j10, j11; };   // J = d(Ig, Ia)/d(Vgk, Vak)

struct LibmMath {
    static double exp(double x) { return std::exp(x); }
    static double log1p(double x) { return std::log1p(x); }
    static double pow(double x, double y) { return std::pow(x, y); }
};
struct FastMath {     // dk/fastmath.h (#18)
    static double exp(double x) { return fm_exp(x); }
    static double log1p(double x) { return fm_log1p(x); }
    static double pow(double x, double y) { return fm_pow(x, y); }
};

template <class M> struct Triode {
    static double softplus(double x) { return (x > 0.0 ? x : 0.0) + M::log1p(M::exp(-std::fabs(x))); }
    // (softplus(x), sigmoid(x)) from one shared exp(-|x|)
    static void softplus_sigmoid(double x, double& sp, double& sg) {
        const double e = M::exp(-std::fabs(x));
        if (x >= 0.0) { sp = x + M::log1p(e); sg = 1.0 / (1.0 + e); }
        else          { sp = M::log1p(e);     sg = e / (1.0 + e); }
    }
    static void currents(double vgk, double vak, const TriodeParams& p, Currents& o) {
        const double zk = p.C * (vak / p.mu + vgk);
        double spk, sigk; softplus_sigmoid(zk, spk, sigk);
        const double sk = spk / p.C, pk = M::pow(sk, p.gamma), ik = p.G * pk;
        const double dik_dz = p.G * p.gamma * (sk > 0.0 ? pk / sk : 0.0) * sigk;
        const double zg = p.Cg * vgk;
        double spg, sigg; softplus_sigmoid(zg, spg, sigg);
        const double sg = spg / p.Cg, pg = M::pow(sg, p.xi), ig = p.Gg * pg + p.Ig0;
        const double dig = p.Gg * p.xi * (sg > 0.0 ? pg / sg : 0.0) * sigg;
        o.ig = ig; o.ia = ik - ig;
        o.j00 = dig; o.j01 = 0.0; o.j10 = dik_dz - dig; o.j11 = dik_dz / p.mu;
    }
    // (Ig, Ia) without the Jacobian, for line-search trials
    static void currents_only(double vgk, double vak, const TriodeParams& p, double& ig, double& ia) {
        const double ik = p.G * M::pow(softplus(p.C * (vak / p.mu + vgk)) / p.C, p.gamma);
        ig = p.Gg * M::pow(softplus(p.Cg * vgk) / p.Cg, p.xi) + p.Ig0;
        ia = ik - ig;
    }
};

// ---- per-sample Newton-Raphson (dk/solver.py) -----------------------------------------------------------------

struct Solution { double v0, v1, ig, ia; int it; bool ok; };

namespace detail {
inline double pmax(double a, double b) { return b > a ? b : a; }   // Python's max(a, b), NaN included

// SPICE pnjlim on Vgk (Nagel 1975). vt = 1/Cg. (Python uses the module constant 1/DZ_12AX7_RSD1["Cg"]; equal for
// the one parameter set the stage uses.)
inline double pnjlim(double vnew, double vold, double vt) {
    if (vnew > VCRIT && std::fabs(vnew - vold) > 2.0 * vt) {
        if (vold > 0.0) {
            const double arg = 1.0 + (vnew - vold) / vt;
            return arg > 0.0 ? vold + vt * std::log(arg) : VCRIT;
        }
        return vnew > vt ? vt * std::log(vnew / vt) : vnew;
    }
    return vnew;
}
}  // namespace detail

/* Solve F(v) = p - K i(v) - v = 0 for v = (Vgk, Vak) from the warm start (v0, v1); step limiting on Vgk,
 * backtracking line search (full step with its Jacobian, halvings with currents only, Jacobian once at the
 * accepted point), converged when ||dv||inf < tol_v and ||F||inf < tol_f, at most max_it iterations.
 *
 * Non-convergence (#7, the contract of dk/solver.py). When the cap is reached, solve() returns the BEST iterate it
 * saw, i.e. the one with the smallest finite ||F||inf (the warm start counts), and not the last one. It returns
 * ok = false with it. A NaN/inf iterate never wins, so a finite start always gives a finite result. The caller keeps
 * that iterate as the sample's state; it does not reset it or NaN it, so the next sample warm-starts from a finite
 * point. The caller must also count the failure and report it (Stage: Stats::nonconverged, nonconv_block,
 * nonconverged_total()). The DC solve is the exception: it has no previous state to fall back on, so it fails the
 * build (BuildStatus::dc_nonconvergence). */
template <class M>
inline Solution solve(double p0, double p1, const double (&K)[2][2], double v0, double v1, const TriodeParams& par,
                      const Newton& nw) {
    using T = Triode<M>;
    const double vt = 1.0 / par.Cg;
    Currents c;
    T::currents(v0, v1, par, c);
    double f0 = p0 - K[0][0] * c.ig - K[0][1] * c.ia - v0;
    double f1 = p1 - K[1][0] * c.ig - K[1][1] * c.ia - v1;
    double fb = detail::pmax(std::fabs(f0), std::fabs(f1));
    Solution best{v0, v1, c.ig, c.ia, 0, false};                // best iterate so far, returned on failure
    for (int it = 1; it <= nw.max_it; ++it) {
        // Jacobian of F: -K J - I
        const double a = -(K[0][0] * c.j00 + K[0][1] * c.j10) - 1.0;
        const double b = -(K[0][0] * c.j01 + K[0][1] * c.j11);
        const double cc = -(K[1][0] * c.j00 + K[1][1] * c.j10);
        const double d = -(K[1][0] * c.j01 + K[1][1] * c.j11) - 1.0;
        const double det = a * d - b * cc;
        const double d0 = -(d * f0 - b * f1) / det;
        const double d1 = -(-cc * f0 + a * f1) / det;
        const double n0 = detail::pnjlim(v0 + d0, v0, vt);
        const double s0 = n0 - v0, s1 = d1 * (d0 != 0.0 ? (n0 - v0) / d0 : 1.0);   // keep the direction
        const double fn = detail::pmax(std::fabs(f0), std::fabs(f1));
        double lam = 1.0;
        Currents g;
        T::currents(v0 + s0, v1 + s1, par, g);
        double g0 = p0 - K[0][0] * g.ig - K[0][1] * g.ia - (v0 + s0);
        double g1 = p1 - K[1][0] * g.ig - K[1][1] * g.ia - (v1 + s1);
        if (!(detail::pmax(std::fabs(g0), std::fabs(g1)) < fn || fn < nw.tol_f)) {
            for (int k = 0; k < LS_MAX - 1; ++k) {
                lam *= 0.5;
                const double t0 = v0 + lam * s0, t1 = v1 + lam * s1;
                T::currents_only(t0, t1, par, g.ig, g.ia);
                g0 = p0 - K[0][0] * g.ig - K[0][1] * g.ia - t0;
                g1 = p1 - K[1][0] * g.ig - K[1][1] * g.ia - t1;
                if (detail::pmax(std::fabs(g0), std::fabs(g1)) < fn) break;
            }
            Currents gj;                                       // Jacobian at the accepted point; Ig, Ia stay
            T::currents(v0 + lam * s0, v1 + lam * s1, par, gj); // the currents_only ones, as in Python
            g.j00 = gj.j00; g.j01 = gj.j01; g.j10 = gj.j10; g.j11 = gj.j11;
        }
        const double dv = detail::pmax(std::fabs(lam * s0), std::fabs(lam * s1));
        v0 = v0 + lam * s0; v1 = v1 + lam * s1;
        f0 = g0; f1 = g1; c = g;
        const double fa = detail::pmax(std::fabs(f0), std::fabs(f1));
        if (dv < nw.tol_v && fa < nw.tol_f) return Solution{v0, v1, c.ig, c.ia, it, true};
        // NaN/inf never wins (sum, not max); a non-finite start loses to any finite iterate
        if ((fa < fb || !(fb < INFINITY)) && std::fabs(f0) + std::fabs(f1) < INFINITY) {
            fb = fa; best = Solution{v0, v1, c.ig, c.ia, 0, false};
        }
    }
    best.it = nw.max_it; best.ok = false;
    return best;
}

// ---- the coefficient pack ------------------------------------------------------------------------------------

enum class BuildStatus : std::uint8_t { ok, bad_argument, singular_matrix, dc_nonconvergence };

inline const char* to_string(BuildStatus s) {
    switch (s) {
        case BuildStatus::ok: return "ok";
        case BuildStatus::bad_argument: return "bad_argument";
        case BuildStatus::singular_matrix: return "singular_matrix";
        case BuildStatus::dc_nonconvergence: return "dc_nonconvergence";
    }
    return "?";
}

struct OperatingPoint { double vgk, vak, ig, ia; double nodes[NNODES]; };

/* Everything the audio thread reads per sample, plus the DC state reset() starts from. Plain data (trivially
 * copyable), so a publish is a memcpy into a preallocated slot. Matrix rows / columns as in circuit.build:
 * x and A..C rows: capacitors Cin, Ck, Cout, CCG, CGP, CCP; u = (vin, HT); i_nl = (Ig, Ia); v = (Vgk, Vak);
 * D..F rows: out, g, k, a, gl. */
struct Coeffs {
    double fs = 0.0, bias = 1.0, prewarp_hz = 0.0;    // internal rate, Rk scale, pre-warp (0 = none)
    TriodeParams par = DZ_12AX7_RSD1;
    Newton newton{};
    TriodeMath math = TriodeMath::libm;
    double HT = Values{}.HT;
    double A[NX][NX], B[NX][NU], C[NX][NNL], D[NOUT][NX], E[NOUT][NU], F[NOUT][NNL], G[NNL][NX], H[NNL][NU],
           K[NNL][NNL];
    OperatingPoint op;
    double x0[NX];                                     // capacitor history with every i_c = 0 at the op point
    // DC failure diagnostics (stage.OperatingPointError's message)
    int dc_step = 0, dc_iterations = 0;
    double dc_best_vgk = 0.0, dc_best_vak = 0.0;
};
static_assert(std::is_trivially_copyable<Coeffs>::value, "Coeffs is published by copy");

namespace detail {
template <int R, int C> struct Mat {
    double a[R][C];
    Mat() { std::memset(a, 0, sizeof a); }
    double* operator[](int r) { return a[r]; }
    const double* operator[](int r) const { return a[r]; }
};
template <int R, int K, int C> Mat<R, C> mul(const Mat<R, K>& x, const Mat<K, C>& y) {
    Mat<R, C> o;
    for (int r = 0; r < R; ++r)
        for (int c = 0; c < C; ++c) {
            double s = 0.0;
            for (int k = 0; k < K; ++k) s += x[r][k] * y[k][c];
            o[r][c] = s;
        }
    return o;
}
template <int R, int C> Mat<C, R> tr(const Mat<R, C>& x) {
    Mat<C, R> o;
    for (int r = 0; r < R; ++r) for (int c = 0; c < C; ++c) o[c][r] = x[r][c];
    return o;
}
template <int R, int C> Mat<R, C> scale_rows(const double (&d)[R], const Mat<R, C>& x) {   // diag(d) @ x
    Mat<R, C> o;
    for (int r = 0; r < R; ++r) for (int c = 0; c < C; ++c) o[r][c] = d[r] * x[r][c];
    return o;
}
// Gauss-Jordan with partial pivoting; false if singular
template <int N> bool invert(const Mat<N, N>& in, Mat<N, N>& out) {
    Mat<N, N> a = in;
    for (int r = 0; r < N; ++r) for (int c = 0; c < N; ++c) out[r][c] = r == c ? 1.0 : 0.0;
    for (int col = 0; col < N; ++col) {
        int piv = col;
        for (int r = col + 1; r < N; ++r) if (std::fabs(a[r][col]) > std::fabs(a[piv][col])) piv = r;
        if (!(std::fabs(a[piv][col]) > 0.0)) return false;
        if (piv != col)
            for (int c = 0; c < N; ++c) { std::swap(a[piv][c], a[col][c]); std::swap(out[piv][c], out[col][c]); }
        const double inv = 1.0 / a[col][col];
        for (int c = 0; c < N; ++c) { a[col][c] *= inv; out[col][c] *= inv; }
        for (int r = 0; r < N; ++r) {
            if (r == col || a[r][col] == 0.0) continue;
            const double f = a[r][col];
            for (int c = 0; c < N; ++c) { a[r][c] -= f * a[col][c]; out[r][c] -= f * out[col][c]; }
        }
    }
    return true;
}

struct Pair { int p, q; };   // q < 0: ground
inline constexpr Pair R_PAIRS[6] = {{SRC, IN1}, {GL, -1}, {GL, GRID}, {HTN, ANODE}, {CATH, -1}, {OUT, -1}};
inline constexpr Pair C_PAIRS[NX] = {{IN1, GL}, {CATH, -1}, {ANODE, OUT}, {GRID, CATH}, {GRID, ANODE}, {ANODE, CATH}};
inline constexpr Pair NL_PAIRS[NNL] = {{GRID, CATH}, {ANODE, CATH}};
inline constexpr Pair OUT_PAIRS[NOUT] = {{OUT, -1}, {GRID, -1}, {CATH, -1}, {ANODE, -1}, {GL, -1}};
inline constexpr int U_NODES[NU] = {SRC, HTN};

template <int P> Mat<P, NNODES> incidence(const Pair (&pairs)[P]) {
    Mat<P, NNODES> m;
    for (int r = 0; r < P; ++r) {
        m[r][pairs[r].p] = 1.0;
        if (pairs[r].q >= 0) m[r][pairs[r].q] = -1.0;
    }
    return m;
}
template <int P> Mat<P, NS> pad(const Mat<P, NNODES>& m) {
    Mat<P, NS> o;
    for (int r = 0; r < P; ++r) for (int c = 0; c < NNODES; ++c) o[r][c] = m[r][c];
    return o;
}

// companion conductance of capacitor c at rate fs: 2C/T, or pre-warped to prewarp_hz if c < SMALL_C
inline double companion_g(double c, double fs, double prewarp_hz) {
    if (!(prewarp_hz > 0.0) || c >= SMALL_C) return 2.0 * fs * c;
    const double w0 = 2.0 * PI * prewarp_hz;
    return c * w0 / std::tan(w0 / (2.0 * fs));
}

struct Full {   // circuit.build's dict, the parts the core needs
    Mat<NX, NX> A; Mat<NX, NU> B; Mat<NX, NNL> C; Mat<NOUT, NX> D; Mat<NOUT, NU> E; Mat<NOUT, NNL> F;
    Mat<NNL, NX> G; Mat<NNL, NU> H; Mat<NNL, NNL> K; Mat<NS, NS> Si; Mat<NNL, NS> Nnp; Mat<NU, NS> Nup;
    Mat<NX, NNODES> Nx; double gx[NX];
};

// circuit.build(fs, rk_scale, dc, prewarp_hz), the same products in the same left-to-right order
inline bool build_full(Full& m, double fs, double rk_scale, bool dc, double prewarp_hz, const Values& v) {
    const double rv[6] = {v.Rs, v.Rg, v.Rgs, v.Ra, v.Rk * rk_scale, v.Rl};
    const double cv[NX] = {v.Cin, v.Ck, v.Cout, v.CCG, v.CGP, v.CCP};
    const auto Nr = incidence(R_PAIRS), Nx = incidence(C_PAIRS);
    const auto Nn = incidence(NL_PAIRS);
    const auto No = incidence(OUT_PAIRS);
    Mat<NU, NNODES> Nu;
    for (int r = 0; r < NU; ++r) Nu[r][U_NODES[r]] = 1.0;
    double gr[6];
    for (int i = 0; i < 6; ++i) gr[i] = 1.0 / rv[i];
    for (int i = 0; i < NX; ++i) m.gx[i] = dc ? 0.0 : companion_g(cv[i], fs, prewarp_hz);
    // S = [[Nr^T Gr Nr + Nx^T Gx Nx, Nu^T], [Nu, 0]]
    const auto Yr = mul(tr(Nr), scale_rows(gr, Nr)), Yc = mul(tr(Nx), scale_rows(m.gx, Nx));
    Mat<NS, NS> S;
    for (int r = 0; r < NNODES; ++r)
        for (int c = 0; c < NNODES; ++c) S[r][c] = Yr[r][c] + Yc[r][c];
    for (int r = 0; r < NU; ++r)
        for (int c = 0; c < NNODES; ++c) { S[c][NNODES + r] = Nu[r][c]; S[NNODES + r][c] = Nu[r][c]; }
    if (!invert(S, m.Si)) return false;
    const auto Nxp = pad(Nx);
    const auto Nop = pad(No);
    m.Nnp = pad(Nn);
    for (int r = 0; r < NU; ++r) m.Nup[r][NNODES + r] = 1.0;
    double gx2[NX];
    for (int i = 0; i < NX; ++i) gx2[i] = 2.0 * m.gx[i];
    const auto XSi = mul(scale_rows(gx2, Nxp), m.Si);                   // 2 Gx Nx' Si
    m.A = mul(XSi, tr(Nxp));
    for (int i = 0; i < NX; ++i) m.A[i][i] -= 1.0;
    m.B = mul(XSi, tr(m.Nup));
    const auto Cp = mul(XSi, tr(m.Nnp));
    for (int r = 0; r < NX; ++r) for (int c = 0; c < NNL; ++c) m.C[r][c] = -Cp[r][c];
    const auto OSi = mul(Nop, m.Si);
    const auto NSi = mul(m.Nnp, m.Si);
    m.D = mul(OSi, tr(Nxp)); m.E = mul(OSi, tr(m.Nup));
    const auto Fp = mul(OSi, tr(m.Nnp));
    for (int r = 0; r < NOUT; ++r) for (int c = 0; c < NNL; ++c) m.F[r][c] = -Fp[r][c];
    m.G = mul(NSi, tr(Nxp)); m.H = mul(NSi, tr(m.Nup)); m.K = mul(NSi, tr(m.Nnp));
    m.Nx = Nx;
    return true;
}

template <int R, int C> void copy_to(double (&dst)[R][C], const Mat<R, C>& src) { std::memcpy(dst, src.a, sizeof dst); }
}  // namespace detail

/* Build a pack at internal rate fs (stage.Stage.__init__ minus the resampler): matrices at fs, then the DC operating
 * point (capacitors open, the same Newton solver, source-stepping HT in 10 steps from Vgk = -1 V, Vak = 200 V), then
 * x0. The DC solve always uses libm (the runtime math switch applies to process() only; with fast math the operating
 * point moves by ~1e-12 relative, dk/RESULTS.md #18). Non-RT: it does no allocation, but it is unbounded work in
 * the RT sense (a 10x10 inverse twice and up to 10 Newton solves). */
inline BuildStatus build_coeffs(Coeffs& out, double fs, double bias = 1.0, double prewarp_hz = 0.0,
                                TriodeMath math = TriodeMath::libm, const Newton& nw = Newton{},
                                const TriodeParams& par = DZ_12AX7_RSD1, const Values& vals = Values{}) {
    if (!(fs > 0.0) || !(bias > 0.0) || !std::isfinite(fs) || !std::isfinite(bias) || nw.max_it < 1 ||
        !(nw.tol_v >= 0.0) || !(nw.tol_f >= 0.0))
        return BuildStatus::bad_argument;
    Coeffs c{};
    c.fs = fs; c.bias = bias; c.prewarp_hz = prewarp_hz > 0.0 ? prewarp_hz : 0.0;
    c.par = par; c.newton = nw; c.math = math; c.HT = vals.HT;
    detail::Full m, m0;
    if (!detail::build_full(m, fs, bias, false, c.prewarp_hz, vals) || !detail::build_full(m0, fs, bias, true, 0.0, vals))
        return BuildStatus::singular_matrix;
    detail::copy_to(c.A, m.A); detail::copy_to(c.B, m.B); detail::copy_to(c.C, m.C);
    detail::copy_to(c.D, m.D); detail::copy_to(c.E, m.E); detail::copy_to(c.F, m.F);
    detail::copy_to(c.G, m.G); detail::copy_to(c.H, m.H); detail::copy_to(c.K, m.K);

    // DC operating point: capacitors open, same NR solver, started from a cutoff-ish guess
    const double u[NU] = {0.0, vals.HT};
    const double p0 = m0.H[0][0] * u[0] + m0.H[0][1] * u[1], p1 = m0.H[1][0] * u[0] + m0.H[1][1] * u[1];
    double v0 = -1.0, v1 = 200.0;
    Solution r{};
    const double step = (1.0 - 0.1) / 9.0;                                  // np.linspace(0.1, 1.0, 10)
    for (int k = 1; k <= 10; ++k) {
        const double s = k == 10 ? 1.0 : (k - 1) * step + 0.1;
        r = solve<LibmMath>(p0 * s, p1 * s, m0.K.a, v0, v1, par, nw);
        v0 = r.v0; v1 = r.v1;
        if (!r.ok) {
            out = c;
            out.dc_step = k; out.dc_iterations = r.it; out.dc_best_vgk = v0; out.dc_best_vak = v1;
            return BuildStatus::dc_nonconvergence;
        }
    }
    // w = Si (Nup^T u - Nnp^T i_nl)
    double rhs[NS];
    for (int j = 0; j < NS; ++j)
        rhs[j] = (m0.Nup[0][j] * u[0] + m0.Nup[1][j] * u[1]) - (m0.Nnp[0][j] * r.ig + m0.Nnp[1][j] * r.ia);
    c.op.vgk = r.v0; c.op.vak = r.v1; c.op.ig = r.ig; c.op.ia = r.ia;
    for (int i = 0; i < NNODES; ++i) {
        double s = 0.0;
        for (int j = 0; j < NS; ++j) s += m0.Si[i][j] * rhs[j];
        c.op.nodes[i] = s;
    }
    for (int i = 0; i < NX; ++i) {         // x = Gx Nx vn: capacitor history so that every i_c = 0 at t = 0
        double s = 0.0;
        for (int j = 0; j < NNODES; ++j) s += m.Nx[i][j] * c.op.nodes[j];
        c.x0[i] = m.gx[i] * s;
    }
    out = c;
    return BuildStatus::ok;
}

// ---- half-band FIR resampler (dk/resample.py, kind "fir") -------------------------------------------------------

namespace detail {
inline double bessel_i0(double x) {        // power series; x <= ~20 here (Kaiser beta 9)
    const double q = 0.25 * x * x;
    double term = 1.0, sum = 1.0;
    for (int k = 1; k < 200; ++k) {
        term *= q / (double(k) * double(k));
        sum += term;
        if (term < sum * 1e-18) break;
    }
    return sum;
}
}  // namespace detail

// scipy.signal.firwin(ntaps, 0.5, window=("kaiser", beta)): half-band lowpass, scaled to unit DC gain. ntaps <= 127.
inline void firwin_halfband(int ntaps, double beta, double* h) {
    const double alpha = 0.5 * (ntaps - 1);
    double s = 0.0;
    for (int n = 0; n < ntaps; ++n) {
        const double m = n - alpha, x = 0.5 * m;
        const double y = PI * (x == 0.0 ? 1.0e-20 : x);                  // np.sinc
        const double r = (n - alpha) / alpha;
        const double w = detail::bessel_i0(beta * std::sqrt(1.0 - r * r)) / detail::bessel_i0(beta);
        h[n] = 0.5 * (std::sin(y) / y) * w;
        s += h[n];
    }
    for (int n = 0; n < ntaps; ++n) h[n] /= s;
}

/* scipy.signal.lfilter(b, [1.0], x, zi) with persistent state: the same transposed direct form II, so the same
 * operation order (y = z0 + b0 x; z[k] = z[k+1] + b[k+1] x). N = max taps. */
template <int N> struct Fir {
    int m = 0;                 // taps - 1 (state length)
    double b[N]{}, z[N]{};
    double dc = 0.0;           // b.sum()
    void set(const double* taps, int ntaps) {
        m = ntaps - 1; dc = 0.0;
        for (int k = 0; k < ntaps; ++k) { b[k] = taps[k]; dc += taps[k]; }
        clear();
    }
    void clear() { for (int k = 0; k < N; ++k) z[k] = 0.0; }
    // steady state for a constant input c (lfilter_zi * c); returns the constant output
    double prime(double c) {
        double zi = 0.0;
        for (int k = m - 1; k >= 0; --k) { zi += b[k + 1]; z[k] = zi * c; }
        return dc * c;
    }
    double tick(double x) {
        if (m == 0) return b[0] * x;
        const double y = z[0] + b[0] * x;
        for (int k = 0; k < m - 1; ++k) z[k] = z[k + 1] + x * b[k + 1];
        z[m - 1] = x * b[m];
        return y;
    }
};

/* Streaming os-times up -> (stage at os*fs) -> down, as dk/resample.Resampler(fs, os, "fir"): a cascade of 2x
 * linear-phase half-band FIR stages (stage 0 next to the base rate: 127 taps, later ones 23), polyphase branches,
 * a pad delay so the latency is a whole number of base samples. up() starts from zero history; the first down()
 * after reset() primes the decimator with its first sample (the operating-point value). */
class Resampler {
public:
    static constexpr int BR = (FIR_FIRST_TAPS + 1) / 2 + 1;     // branch taps: 64, +1 for the down odd branch

    bool prepare(int os) {
        int n = 0;
        while ((1 << n) < os) ++n;
        if ((1 << n) != os || n < 1 || os > MAX_OS) return false;
        os_ = os; nst_ = n;
        int d_int = 0;
        for (int i = 0; i < n; ++i) {
            const int nt = i == 0 ? FIR_FIRST_TAPS : FIR_LATER_TAPS;
            double h[FIR_FIRST_TAPS], e[BR], o[BR];
            firwin_halfband(nt, FIR_BETA, h);
            std::memcpy(taps_[i], h, sizeof(double) * nt); ntaps_[i] = nt;
            const int ne = (nt + 1) / 2, no = nt / 2;
            for (int k = 0; k < ne; ++k) e[k] = 2.0 * h[2 * k];
            for (int k = 0; k < no; ++k) o[k] = 2.0 * h[2 * k + 1];
            upe_[i].set(e, ne); upo_[i].set(o, no);
            for (int k = 0; k < ne; ++k) e[k] = h[2 * k];
            o[0] = 0.0;                                             // odd branch is one low-rate sample late
            for (int k = 0; k < no; ++k) o[k + 1] = h[2 * k + 1];
            dne_[i].set(e, ne); dno_[i].set(o, no + 1);
            d_int += 2 * ((nt - 1) / 2) * os / (1 << (i + 1));
        }
        pad_ = ((-d_int) % os + os) % os;                           // extra internal-rate delay -> whole base samples
        latency_ = (d_int + pad_) / os;
        up_delay_ = d_int / 2;
        double pd[MAX_OS + 1] = {};
        pd[pad_] = 1.0;
        delay_.set(pd, pad_ + 1);
        reset();
        return true;
    }
    void reset() {
        for (int i = 0; i < nst_; ++i) { upe_[i].clear(); upo_[i].clear(); dne_[i].clear(); dno_[i].clear(); }
        delay_.clear();
        primed_ = false;
    }
    int os() const { return os_; }
    int latency_samples() const { return latency_; }        // base-rate delay of up -> down (exact for fir)
    int up_delay_internal() const { return up_delay_; }     // interpolator delay, internal-rate samples
    int pad() const { return pad_; }
    const double* taps(int stage) const { return taps_[stage]; }
    int ntaps(int stage) const { return ntaps_[stage]; }
    int macs() const {                                       // non-zero multiplies per base sample, each way
        int s = 0;
        for (int i = 0; i < nst_; ++i) {
            int nz = 0;
            for (int k = 0; k < ntaps_[i]; ++k) nz += std::fabs(taps_[i][k]) > 1e-15;
            s += nz << i;
        }
        return s;
    }

    // one base-rate sample in, os internal-rate samples out
    void up(double x, double* y) {
        double a[MAX_OS], b[MAX_OS];
        a[0] = x;
        int len = 1;
        double* cur = a; double* nxt = b;
        for (int i = 0; i < nst_; ++i) {
            for (int j = 0; j < len; ++j) { const double v = cur[j]; nxt[2 * j] = upe_[i].tick(v); nxt[2 * j + 1] = upo_[i].tick(v); }
            len *= 2;
            double* t = cur; cur = nxt; nxt = t;
        }
        std::memcpy(y, cur, sizeof(double) * os_);
    }
    // os internal-rate samples in, one base-rate sample out
    double down(const double* y) {
        double a[MAX_OS], b[MAX_OS];
        if (!primed_) {
            double c = delay_.prime(y[0]);
            for (int i = nst_ - 1; i >= 0; --i) c = dne_[i].prime(c) + dno_[i].prime(c);
            primed_ = true;
        }
        for (int j = 0; j < os_; ++j) a[j] = pad_ ? delay_.tick(y[j]) : y[j];
        int len = os_;
        double* cur = a; double* nxt = b;
        for (int i = nst_ - 1; i >= 0; --i) {
            len /= 2;
            for (int j = 0; j < len; ++j) nxt[j] = dne_[i].tick(cur[2 * j]) + dno_[i].tick(cur[2 * j + 1]);
            double* t = cur; cur = nxt; nxt = t;
        }
        return cur[0];
    }

private:
    int os_ = 0, nst_ = 0, pad_ = 0, latency_ = 0, up_delay_ = 0;
    double taps_[4][FIR_FIRST_TAPS] = {};
    int ntaps_[4] = {};
    Fir<BR> upe_[4], upo_[4], dne_[4], dno_[4];
    Fir<MAX_OS + 1> delay_;
    bool primed_ = false;
};

// ---- realtime plumbing ---------------------------------------------------------------------------------------

/* RAII: flush denormals to zero for the scope, restore the caller's mode on exit (the host owns the thread).
 * x86: MXCSR FTZ (bit 15) + DAZ (bit 6). AArch64: FPCR.FZ (bit 24). Elsewhere a no-op (active() == false). */
class ScopedFlushDenormals {
public:
    ScopedFlushDenormals() noexcept {
#if defined(DKCORE_X86_SSE)
        old_ = _mm_getcsr();
        _mm_setcsr(old_ | 0x8040u);
#elif defined(__aarch64__)
        std::uint64_t v;
        __asm__ __volatile__("mrs %0, fpcr" : "=r"(v));
        old_ = v;
        v |= (std::uint64_t(1) << 24);
        __asm__ __volatile__("msr fpcr, %0" : : "r"(v));
#endif
    }
    ~ScopedFlushDenormals() {
#if defined(DKCORE_X86_SSE)
        _mm_setcsr(old_);
#elif defined(__aarch64__)
        std::uint64_t v = old_;
        __asm__ __volatile__("msr fpcr, %0" : : "r"(v));
#endif
    }
    ScopedFlushDenormals(const ScopedFlushDenormals&) = delete;
    ScopedFlushDenormals& operator=(const ScopedFlushDenormals&) = delete;
    static constexpr bool active() {
#if defined(DKCORE_X86_SSE) || defined(__aarch64__)
        return true;
#else
        return false;
#endif
    }

private:
#if defined(DKCORE_X86_SSE)
    unsigned old_ = 0;
#else
    std::uint64_t old_ = 0;
#endif
};

/* Single-producer / single-consumer "latest value" triple buffer. The producer fills back() and publish()es it;
 * the consumer calls acquire() at block start and reads front(). Both sides are wait-free (one atomic exchange),
 * no slot is ever written while the consumer can read it, and nothing is freed: the three slots live in the
 * object. If the producer publishes twice before the consumer looks, the consumer gets the newer one. */
template <class T> class TripleBuffer {
public:
    static_assert(std::is_trivially_copyable<T>::value, "slots are copied with memcpy semantics");
    static constexpr std::uint8_t FRESH = 4;
    T& back() { return slot_[back_]; }                                // producer
    void publish() { back_ = std::uint8_t(mid_.exchange(std::uint8_t(back_ | FRESH), std::memory_order_acq_rel) & 3); }
    bool acquire() {                                                  // consumer: true if front() changed
        if (!(mid_.load(std::memory_order_relaxed) & FRESH)) return false;
        front_ = std::uint8_t(mid_.exchange(front_, std::memory_order_acq_rel) & 3);
        return true;
    }
    const T& front() const { return slot_[front_]; }                  // consumer
    void reset(const T& v) {                                          // neither side running
        for (auto& s : slot_) s = v;
        front_ = 0; mid_.store(1, std::memory_order_relaxed); back_ = 2;
    }

private:
    T slot_[3]{};
    std::atomic<std::uint8_t> mid_{1};
    std::uint8_t front_ = 0, back_ = 2;
    static_assert(std::atomic<std::uint8_t>::is_always_lock_free, "lock-free index");
};

struct Stats {                         // audio-thread side; since reset()
    static constexpr int HIST = 65;    // histogram bins 0..64 (iteration counts above 64 land in the last bin)
    std::uint64_t samples = 0;         // internal-rate samples solved
    std::uint64_t iterations = 0;      // Newton iterations, summed
    std::uint64_t nonconverged = 0;    // samples that hit the cap and kept the best iterate (#7)
    std::uint64_t nonfinite_inputs = 0;  // base-rate input samples that were NaN / inf and were replaced by 0
    std::uint64_t swaps = 0;           // coefficient packs adopted
    std::uint32_t nonconv_block = 0;   // ... nonconverged in the last process() / process_internal() call
    int max_iterations = 0;
    std::uint64_t histogram[HIST] = {};
};

// ---- the stage -----------------------------------------------------------------------------------------------

class Stage {
public:
    static_assert(std::atomic<double>::is_always_lock_free, "lock-free drive / output");

    // ---- non-RT, never concurrently with process() ------------------------------------------------------------
    BuildStatus prepare(double fs_base, Quality q = STANDARD, double bias = 1.0, TriodeMath math = TriodeMath::libm,
                        const Newton& nw = Newton{}, const TriodeParams& par = DZ_12AX7_RSD1) {
        std::lock_guard<std::mutex> lk(producer_);
        prepared_ = false;
        if (!(fs_base > 0.0) || !rs_.prepare(q.os)) return BuildStatus::bad_argument;
        Coeffs& c = latest_;
        const BuildStatus st = build_coeffs(c, fs_base * q.os, bias, q.prewarp_hz, math, nw, par);
        if (st != BuildStatus::ok) return st;
        fs_base_ = fs_base;
        coeffs_.reset(c);
        prepared_ = true;
        reset();
        return st;
    }

    // ---- producers: any non-RT thread -------------------------------------------------------------------------
    // Rebuild the matrices and the DC operating point for a new bias (Rk scale) and publish them. Nothing is
    // published if the build fails (the audio thread carries on with the old pack).
    BuildStatus request_bias(double bias) {
        std::lock_guard<std::mutex> lk(producer_);
        if (!prepared_) return BuildStatus::bad_argument;
        Coeffs c;
        const Coeffs& l = latest_;
        const BuildStatus st = build_coeffs(c, l.fs, bias, l.prewarp_hz, l.math, l.newton, l.par);
        if (st == BuildStatus::ok) publish_locked(c);
        return st;
    }
    // Publish a prepared pack (e.g. a modified copy of latest()). It must be for the prepared internal rate.
    bool publish(const Coeffs& c) {
        std::lock_guard<std::mutex> lk(producer_);
        if (!prepared_ || c.fs != latest_.fs || c.newton.max_it < 1) return false;
        publish_locked(c);
        return true;
    }
    Coeffs latest() const {                // the last pack built or published (producer side)
        std::lock_guard<std::mutex> lk(producer_);
        return latest_;
    }

    // ---- any thread ---------------------------------------------------------------------------------------------
    void set_drive(double g) { drive_.store(g, std::memory_order_relaxed); }     // linear gain before the stage
    void set_output(double g) { output_.store(g, std::memory_order_relaxed); }   // linear gain after it
    std::uint64_t nonconverged_total() const { return nonconv_total_.load(std::memory_order_relaxed); }

    // ---- audio thread -------------------------------------------------------------------------------------------
    void reset() noexcept {                // state to the active pack's operating point; clears the stats
        if (coeffs_.acquire()) {}
        const Coeffs& c = coeffs_.front();
        std::memcpy(x_, c.x0, sizeof x_);
        v_[0] = q_[0] = c.op.vgk;
        v_[1] = q_[1] = c.op.vak;
        stats_ = Stats{};
        nonconv_total_.store(0, std::memory_order_relaxed);
        rs_.reset();
    }

    // Base rate in, base rate out, os-times oversampled. Stateful (call it block by block); the output lags the input
    // by latency_samples(). in may equal out. NaN / inf input samples are replaced by 0 and counted.
    // vgk_internal (optional, n * oversampling() values): Vgk at the internal rate, as Stage.process(record=("vgk",)).
    void process(const double* in, double* out, std::size_t n, double* vgk_internal = nullptr) noexcept {
        process_t(in, out, n, vgk_internal);
    }
    void process(const float* in, float* out, std::size_t n) noexcept { process_t(in, out, n, nullptr); }

    /* Stage alone at the internal rate (Stage.process_internal): no resampler, no drive / output gain. vgk and iters
     * may be null. For tests and analysis. */
    void process_internal(const double* vin, double* out, double* vgk, std::uint16_t* iters, std::size_t n) noexcept {
        ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = 0.0; return; }
        const Coeffs& c = coeffs_.front();
        for (std::size_t i = 0; i < n; ++i) {
            int it;
            out[i] = c.math == TriodeMath::fast ? tick<FastMath>(c, vin[i], it) : tick<LibmMath>(c, vin[i], it);
            if (vgk) vgk[i] = v_[0];
            if (iters) iters[i] = std::uint16_t(it);
        }
        end_block();
    }

    bool prepared() const { return prepared_; }
    int oversampling() const { return rs_.os(); }
    int latency_samples() const { return rs_.latency_samples(); }
    int up_delay_internal() const { return rs_.up_delay_internal(); }
    double fs_base() const { return fs_base_; }
    const Resampler& resampler() const { return rs_; }
    const Coeffs& active() const { return coeffs_.front(); }        // audio thread
    const Stats& stats() const { return stats_; }                   // audio thread, or while stopped
    const double* state_x() const { return x_; }
    const double* state_v() const { return v_; }

private:
    void publish_locked(const Coeffs& c) {
        coeffs_.back() = c;
        coeffs_.publish();
        latest_ = c;
    }
    void begin_block() {
        if (coeffs_.acquire()) ++stats_.swaps;
        stats_.nonconv_block = 0;
    }
    void end_block() { nonconv_total_.store(stats_.nonconverged, std::memory_order_relaxed); }

    // one internal-rate sample (the loop body of Stage.process_internal); returns the "out" node voltage
    template <class M> double tick(const Coeffs& c, double s, int& it) {
        const double HT = c.HT;
        double p0 = c.H[0][0] * s + c.H[0][1] * HT, p1 = c.H[1][0] * s + c.H[1][1] * HT;
        for (int j = 0; j < NX; ++j) { p0 += c.G[0][j] * x_[j]; p1 += c.G[1][j] * x_[j]; }
        // warm start from the linear extrapolation of the last two solutions (#15)
        const double e0 = 2.0 * v_[0] - q_[0], e1 = 2.0 * v_[1] - q_[1];
        q_[0] = v_[0]; q_[1] = v_[1];
        const Solution r = solve<M>(p0, p1, c.K, e0, e1, c.par, c.newton);
        v_[0] = r.v0; v_[1] = r.v1;                     // kept even when !r.ok: the best finite iterate (#7)
        it = r.it;
        ++stats_.samples;
        stats_.iterations += std::uint64_t(r.it);
        ++stats_.histogram[r.it < Stats::HIST ? r.it : Stats::HIST - 1];
        if (r.it > stats_.max_iterations) stats_.max_iterations = r.it;
        if (!r.ok) { ++stats_.nonconverged; ++stats_.nonconv_block; }
        double y = c.E[0][0] * s + c.E[0][1] * HT + c.F[0][0] * r.ig + c.F[0][1] * r.ia;
        for (int j = 0; j < NX; ++j) y += c.D[0][j] * x_[j];
        double xn[NX];
        for (int i = 0; i < NX; ++i) {
            double a = c.B[i][0] * s + c.B[i][1] * HT + c.C[i][0] * r.ig + c.C[i][1] * r.ia;
            for (int j = 0; j < NX; ++j) a += c.A[i][j] * x_[j];
            xn[i] = a;
        }
        std::memcpy(x_, xn, sizeof x_);
        return y;
    }

    template <class M, class S>
    void run(const Coeffs& c, const S* in, S* out, std::size_t n, double drive, double og, double* vgk) {
        double u[MAX_OS], y[MAX_OS];
        const int os = rs_.os();
        for (std::size_t i = 0; i < n; ++i) {
            double x = double(in[i]);
            if (!std::isfinite(x)) { x = 0.0; ++stats_.nonfinite_inputs; }
            rs_.up(drive * x, u);
            int it;
            for (int j = 0; j < os; ++j) {
                y[j] = tick<M>(c, u[j], it);
                if (vgk) vgk[i * std::size_t(os) + std::size_t(j)] = v_[0];
            }
            out[i] = S(og * rs_.down(y));
        }
    }

    template <class S> void process_t(const S* in, S* out, std::size_t n, double* vgk) noexcept {
        ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = S(0); return; }
        const Coeffs& c = coeffs_.front();
        const double drive = drive_.load(std::memory_order_relaxed), og = output_.load(std::memory_order_relaxed);
        if (c.math == TriodeMath::fast) run<FastMath>(c, in, out, n, drive, og, vgk);
        else run<LibmMath>(c, in, out, n, drive, og, vgk);
        end_block();
    }

    // audio-thread state
    double x_[NX] = {}, v_[2] = {}, q_[2] = {};       // capacitor states, (Vgk, Vak), previous (Vgk, Vak)
    Resampler rs_;
    Stats stats_;
    TripleBuffer<Coeffs> coeffs_;
    // shared
    std::atomic<double> drive_{1.0}, output_{1.0};
    std::atomic<std::uint64_t> nonconv_total_{0};
    // producer side
    mutable std::mutex producer_;
    Coeffs latest_{};
    double fs_base_ = 0.0;
    bool prepared_ = false;
};

}  // namespace dkcore
#endif
