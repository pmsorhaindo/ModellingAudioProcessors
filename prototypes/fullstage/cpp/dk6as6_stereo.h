/* dk6as6_stereo.h -- the two-lane stereo kernel of the 6AS6 stage (#135), as dk_core.h's StereoStage (#21, #67) is
 * for the 12AX7.
 *
 * dk6as6::StereoStage runs left and right as the two lanes of one 2 x double SIMD kernel (dkcore::simd: NEON, SSE2 or
 * the portable struct) on one shared Pack. Each lane does the mono operations in the mono order: Pentode2, solve2,
 * solve_grid2 and StereoStage::tick are dk6as6_core.h's law, solve, solve_grid and StageT::tick written with V2 for
 * double, the same expression trees, branches as per-lane selects. The resampler is the same template on V2; the
 * post-filter is the mono PostFilter, one per lane.
 *
 * Per-lane Newton (#21's method). Both lanes iterate in lock step under an "active" mask; a lane that converges, or
 * leaves its solve for any of solve_grid's reasons (a singular step, a non-finite step, no finite trial), is recorded
 * with its iteration count and frozen (every later update to it is select(active, new, old)). The line search is
 * masked the same way. The lazier Jacobian (#135, dk6as6_core.h "per-sample Newton") is the mono one, per lane: the
 * Jacobian is evaluated where any lane needs it and kept on those lanes only.
 * The #112 restart contract, per lane: solve_grid_restarts2 runs each start on the lanes that have not converged
 * yet (a lane that converges on its first start sits out the second), so the lanes diverge in restarts as they do
 * in iterations; each lane's result, iteration count (summed over its starts), attempts and flags are mono's. The
 * price: a sample costs max(itL, itR) iterations of both lanes per start that either lane runs.
 *
 * Lane maths: with LawMath::libm, exp / log1p / pow / atan run per lane as scalar libm calls (PerLaneLaw<LibmLaw>;
 * there is no vector libm), so each lane keeps mono's (and Python's) bits. With LawMath::fast the default is
 * dkcore::LaneMath::vector (VecFastLaw: dk_core.h's VecFastMath, i.e. dk/fastmath.h on both lanes at once; atan per
 * lane); LaneMath::per_lane is the scalar FastLaw per lane, the bit-exact reference against two mono fast Stages.
 * Exactness against two mono Stages: as dk_core.h's StereoStage (cpp/README.md, "Stereo"): bit for bit where the
 * build contracts the scalar and vector code at the same places (clang; any build with -ffp-contract=off), else
 * the same arithmetic with other rounding (GCC with FMA contraction), within the tests' TOL_PINNED_ABS.
 *
 * Realtime: as Stage. Nothing in process() allocates, locks or makes a syscall; process() holds a
 * ScopedFlushDenormals. NaN / inf inputs are replaced per channel, non-finite gains ignored per channel, and a
 * non-finite state recovers its own lane only (the resampler's reset_lanes), the other lane untouched.
 * Threads: prepare() / prepare_pack() non-RT; set_drive() / set_output() / set_lpf() / set_lane_math() any thread
 * (relaxed atomics, read once per block); process() / reset() the audio thread.
 */
#ifndef DK6AS6_STEREO_H
#define DK6AS6_STEREO_H

#include "dk6as6_core.h"

namespace dk6as6 {

using dkcore::LaneMath;

namespace simd2 {
using namespace dkcore::simd;
inline M2 le(V2 a, V2 b) { return ge(b, a); }
inline M2 finite(V2 a) { return lt(vabs(a), splat(INFINITY)); }        // false for NaN and +-inf
inline M2 is_inf(V2 a) { return ge(vabs(a), splat(INFINITY)); }
inline M2 none() { return ~all(); }
inline M2 lanes(int b) { return lane_mask(b); }
inline bool any(M2 m) { return bits(m) != 0; }
}  // namespace simd2

// ---- lane maths of the law: exp / log1p / pow / atan and the two pow identities on two lanes ----------------------

/* PerLaneLaw<M> (M = LibmLaw or FastLaw): M's scalar functions on each lane, so each lane gets mono's bits; pow's
 * overflow report per lane */
template <class M> struct PerLaneLaw {
    using V2 = dkcore::simd::V2;
    using M2 = dkcore::simd::M2;
    static V2 exp(V2 x) { return dkcore::simd::per_lane([](double a) { return M::exp(a); }, x); }
    static V2 log1p(V2 x) { return dkcore::simd::per_lane([](double a) { return M::log1p(a); }, x); }
    static V2 atan(V2 x) { return dkcore::simd::per_lane([](double a) { return M::atan(a); }, x); }
    static V2 pow(V2 x, double y, M2& raised) {
        using dkcore::simd::lane;
        bool r0 = false, r1 = false;
        const V2 out = dkcore::simd::make(M::pow(lane(x, 0), y, r0), M::pow(lane(x, 1), y, r1));
        raised = raised | simd2::lanes(int(r0) | int(r1) << 1);
        return out;
    }
    static V2 pow_m1(V2 e1, double ex, V2 pe, M2& raised) {
        using dkcore::simd::lane;
        bool r0 = false, r1 = false;
        const V2 out = dkcore::simd::make(M::pow_m1(lane(e1, 0), ex, lane(pe, 0), r0),
                                          M::pow_m1(lane(e1, 1), ex, lane(pe, 1), r1));
        raised = raised | simd2::lanes(int(r0) | int(r1) << 1);
        return out;
    }
    static V2 sq(V2 q, M2& raised) {
        using dkcore::simd::lane;
        bool r0 = false, r1 = false;
        const V2 out = dkcore::simd::make(M::sq(lane(q, 0), r0), M::sq(lane(q, 1), r1));
        raised = raised | simd2::lanes(int(r0) | int(r1) << 1);
        return out;
    }
};

/* VecFastLaw: FastLaw on both lanes at once. exp / log1p / pow are dk_core.h's VecFastMath (dk/fastmath.h's
 * algorithm on V2: per lane the same operations as fm_*, scalar fm_* on a lane outside the fast domain); pow's
 * overflow report as FastLaw's; E1^(EX - 1) and q^2 FastLaw's identities on V2; atan per lane (libm, as FastLaw). */
struct VecFastLaw {
    using V2 = dkcore::simd::V2;
    using M2 = dkcore::simd::M2;
    static V2 exp(V2 x) { return dkcore::VecFastMath::exp(x); }
    static V2 log1p(V2 x) { return dkcore::VecFastMath::log1p(x); }
    static V2 atan(V2 x) { return dkcore::simd::per_lane([](double a) { return FastLaw::atan(a); }, x); }
    static V2 pow(V2 x, double y, M2& raised) {
        const V2 r = dkcore::VecFastMath::pow(x, y);
        if (std::isfinite(y)) raised = raised | (simd2::is_inf(r) & simd2::finite(x));
        return r;
    }
    static V2 pow_m1(V2 e1, double, V2 pe, M2&) {
        using namespace simd2;
        return select(gt(e1, splat(0.0)), pe / e1, splat(0.0));
    }
    static V2 sq(V2 q, M2&) { return q * q; }
};

// ---- the law on two lanes (dk6as6_core.h detail::values* / jacobian*) ---------------------------------------------

// Every member is value-initialised: the solvers blend whole structs under per-lane masks (select_work,
// select_jacobian), so both operands of every blend must be defined, also in members the current law leaves unused
// (the grid fields without grid current, J before the first Jacobian)
struct Law2 { dkcore::simd::V2 i[3]{}; dkcore::simd::V2 J[3][4]{}; dkcore::simd::M2 raised{}; };

namespace detail2 {
using V2 = dkcore::simd::V2;
using M2 = dkcore::simd::M2;
struct LawWork2 {                       // value-initialised for the masked blends (Law2)
    V2 vg1{}, va{}, g2{}, a{}, ea{}, spa{}, e1{}, pe{}, x{}, xa{}, sm{}, s{}, knee{}, ik0{}, kf{}, ik{}, f{};
    V2 zg{}, eg{}, sg1{}, pg{}, ig1{};
    M2 floor{}, gon{};
};
inline LawWork2 select_work(M2 m, const LawWork2& a, const LawWork2& b) {
    using simd2::select;
    LawWork2 o;
    o.vg1 = select(m, a.vg1, b.vg1); o.va = select(m, a.va, b.va); o.g2 = select(m, a.g2, b.g2);
    o.a = select(m, a.a, b.a); o.ea = select(m, a.ea, b.ea); o.spa = select(m, a.spa, b.spa);
    o.e1 = select(m, a.e1, b.e1); o.pe = select(m, a.pe, b.pe); o.x = select(m, a.x, b.x); o.xa = select(m, a.xa, b.xa);
    o.sm = select(m, a.sm, b.sm); o.s = select(m, a.s, b.s); o.knee = select(m, a.knee, b.knee);
    o.ik0 = select(m, a.ik0, b.ik0); o.kf = select(m, a.kf, b.kf); o.ik = select(m, a.ik, b.ik); o.f = select(m, a.f, b.f);
    o.zg = select(m, a.zg, b.zg); o.eg = select(m, a.eg, b.eg); o.sg1 = select(m, a.sg1, b.sg1);
    o.pg = select(m, a.pg, b.pg); o.ig1 = select(m, a.ig1, b.ig1);
    o.floor = (m & a.floor) | (~m & b.floor); o.gon = (m & a.gon) | (~m & b.gon);
    return o;
}

// detail::cathode_values on two lanes
template <class L>
inline void cathode_values(V2 vg1, V2 vg2, V2 vg3, V2 va, const PentodeParams& p, LawWork2& w, M2& raised) {
    using namespace simd2;
    const V2 zero = splat(0.0), one = splat(1.0);
    w.vg1 = vg1; w.va = va;
    w.floor = le(vg2, splat(VG2_FLOOR));
    w.g2 = select(w.floor, splat(VG2_FLOOR), vg2);
    w.a = splat(p.KP) * (splat(1.0 / p.MU) + vg1 / w.g2);
    w.ea = L::exp(-vabs(w.a));
    w.spa = select(gt(w.a, zero), w.a, zero) + L::log1p(w.ea);
    w.e1 = w.g2 / splat(p.KP) * w.spa;
    w.pe = L::pow(w.e1, p.EX, raised);
    w.ik0 = splat(2.0) * w.pe / splat(p.KG1);
    V2 up;
    if (p.XA == 1.0) {
        up = select(gt(va, zero), va / splat(p.MU3), zero);
        w.x = w.xa = one;                                            // unused (dup has no power term)
    } else {
        w.x = select(gt(va, splat(VA_FLOOR)), va, splat(VA_FLOOR)) / splat(VA0);
        w.xa = L::pow(w.x, p.XA, raised);
        up = splat(VA0 / p.MU3) * (one + (w.xa - one) / splat(p.XA));
    }
    const V2 mz = -((vg3 + up - splat(p.V3C) - splat(p.RI) * w.ik0) / splat(p.S3));
    const V2 ez = L::exp(-vabs(mz));
    const V2 spz = select(gt(mz, zero), mz, zero) + L::log1p(ez);
    w.sm = select(ge(mz, zero), one / (one + ez), ez / (one + ez));
    w.s = L::exp(splat(-p.P3) * spz);
    const M2 vpos = gt(va, zero);
    w.knee = select(vpos, splat(2.0 / PI) * L::atan(va / splat(p.KVB)), zero);
    w.f = splat(p.FMAX) * w.knee * w.s;
    w.kf = one - splat(p.KR) * w.sm;
    w.ik = w.ik0 * w.kf;
    const V2 q = va / splat(p.KVB);
    raised = raised | (vpos & is_inf(q * q) & finite(q));
}
// detail::cathode_jacobian on two lanes
template <class L>
inline void cathode_jacobian(const LawWork2& w, const PentodeParams& p, V2 (&dik)[4], V2 (&dfs)[4], M2& raised) {
    using namespace simd2;
    const V2 zero = splat(0.0), one = splat(1.0);
    const V2 sa = select(ge(w.a, zero), one / (one + w.ea), w.ea / (one + w.ea));
    const V2 de1_g1 = sa;
    const V2 de1_g2 = select(w.floor, zero, w.spa / splat(p.KP) - sa * w.vg1 / w.g2);
    const V2 dik0 = splat(2.0) * splat(p.EX) * L::pow_m1(w.e1, p.EX, w.pe, raised) / splat(p.KG1);
    V2 dup;
    if (p.XA == 1.0) dup = select(gt(w.va, zero), splat(1.0 / p.MU3), zero);
    else dup = select(gt(w.va, splat(VA_FLOOR)), w.xa / w.x / splat(p.MU3), zero);
    const V2 dik0_g1 = dik0 * de1_g1, dik0_g2 = dik0 * de1_g2;
    const V2 dz_g1 = splat(-p.RI) * dik0_g1 / splat(p.S3), dz_g2 = splat(-p.RI) * dik0_g2 / splat(p.S3),
             dz_g3 = splat(1.0 / p.S3), dz_a = dup / splat(p.S3);
    const V2 ds = w.s * splat(p.P3) * w.sm;
    const M2 vpos = gt(w.va, zero);
    M2 rq = none();                                   // (Va / KVB) ** 2 is evaluated (and can raise) where Va > 0 only
    const V2 dknee = select(vpos, splat(2.0 / PI / p.KVB) / (one + L::sq(w.va / splat(p.KVB), rq)), zero);
    raised = raised | (rq & vpos);
    const V2 df_z = splat(p.FMAX) * w.knee * ds, df_a = splat(p.FMAX) * dknee * w.s;
    const V2 dik_z = w.ik0 * splat(p.KR) * w.sm * (one - w.sm);
    dik[0] = dik0_g1 * w.kf + dik_z * dz_g1;
    dik[1] = dik0_g2 * w.kf + dik_z * dz_g2;
    dik[2] = dik_z * dz_g3;
    dik[3] = dik_z * dz_a;
    dfs[0] = df_z * dz_g1; dfs[1] = df_z * dz_g2; dfs[2] = df_z * dz_g3; dfs[3] = df_z * dz_a + df_a;
}
// detail::grid_values / grid_jacobian on two lanes
template <class L> inline V2 grid_values(V2 vg1, const GridParams& g, LawWork2& w, M2& raised) {
    using namespace simd2;
    const V2 zero = splat(0.0);
    w.zg = splat(g.CG) * vg1;
    w.eg = L::exp(-vabs(w.zg));
    const V2 l = L::log1p(w.eg);
    const V2 sp = select(ge(w.zg, zero), w.zg + l, l);
    w.sg1 = sp / splat(g.CG);
    w.gon = ~le(w.sg1, zero);
    if (!any(w.gon)) { w.pg = zero; return w.ig1 = zero; }
    M2 r = none();
    w.pg = L::pow(w.sg1, g.XI, r);
    raised = raised | (r & w.gon);
    return w.ig1 = select(w.gon, splat(g.GG) * w.pg, zero);
}
inline V2 grid_jacobian(const LawWork2& w, const GridParams& g) {
    using namespace simd2;
    const V2 zero = splat(0.0), one = splat(1.0);
    const V2 sg = select(ge(w.zg, zero), one / (one + w.eg), w.eg / (one + w.eg));
    return select(w.gon, splat(g.GG) * splat(g.XI) * w.pg / w.sg1 * sg, zero);
}

template <class L> inline void values2(const V2 (&v)[4], const PentodeParams& p, Law2& o, LawWork2& w) {
    o.raised = simd2::none();
    cathode_values<L>(v[0], v[1], v[2], v[3], p, w, o.raised);
    const V2 ip = w.ik * w.f;
    o.i[0] = ip; o.i[1] = w.ik - ip; o.i[2] = simd2::splat(0.0);
}
template <class L> inline void jacobian2(const LawWork2& w, const PentodeParams& p, Law2& o) {
    V2 dik[4], dfs[4];
    cathode_jacobian<L>(w, p, dik, dfs, o.raised);
    for (int j = 0; j < 4; ++j) {
        const V2 dip = dik[j] * w.f + w.ik * dfs[j];
        o.J[0][j] = dip; o.J[1][j] = dik[j] - dip; o.J[2][j] = simd2::splat(0.0);
    }
}
template <class L>
inline void values3(const V2 (&v)[4], const PentodeParams& p, const GridParams& g, Law2& o, LawWork2& w) {
    o.raised = simd2::none();
    cathode_values<L>(v[0], v[1], v[2], v[3], p, w, o.raised);
    const V2 ig1 = grid_values<L>(v[0], g, w, o.raised);
    const V2 ipk = w.ik - ig1;
    const V2 ip = ipk * w.f;
    o.i[0] = ip; o.i[1] = ipk - ip; o.i[2] = ig1;
}
template <class L> inline void jacobian3(const LawWork2& w, const PentodeParams& p, const GridParams& g, Law2& o) {
    V2 dik[4], dfs[4];
    cathode_jacobian<L>(w, p, dik, dfs, o.raised);
    const V2 dig1 = grid_jacobian(w, g);
    const V2 ipk = w.ik - w.ig1;
    const V2 dipk[4] = {dik[0] - dig1, dik[1], dik[2], dik[3]};
    for (int j = 0; j < 4; ++j) {
        const V2 dip = dipk[j] * w.f + ipk * dfs[j];
        o.J[0][j] = dip; o.J[1][j] = dipk[j] - dip;
    }
    const V2 zero = simd2::splat(0.0);
    o.J[2][0] = dig1; o.J[2][1] = zero; o.J[2][2] = zero; o.J[2][3] = zero;
}
// o.J rows [0, n) = a's on the lanes of m, b's elsewhere
inline void select_jacobian(M2 m, const Law2& a, Law2& o, int n) {
    for (int q = 0; q < n; ++q) for (int j = 0; j < 4; ++j) o.J[q][j] = simd2::select(m, a.J[q][j], o.J[q][j]);
}
// a's values (i, raised) on the lanes of m, o's elsewhere
inline void select_values(M2 m, const Law2& a, Law2& o) {
    for (int k = 0; k < 3; ++k) o.i[k] = simd2::select(m, a.i[k], o.i[k]);
    o.raised = (m & a.raised) | (~m & o.raised);
}
}  // namespace detail2

// ---- per-lane Newton on two lanes ---------------------------------------------------------------------------------

/* Per lane, a Solution: v, i (as V2 lanes), it / ok / attempts per lane, fv per lane. kernel: the iterations the
 * two-lane kernel ran, i.e. per start the larger iteration count of the lanes that ran it, summed over the starts
 * (each start costs max(itL, itR) iterations of both lanes; the lane with the longer solve can change between starts) */
struct Solution2 {
    dkcore::simd::V2 v[4]{}, i[3]{}, fv{};
    int it[2] = {0, 0}; bool ok[2] = {false, false}; int attempts[2] = {0, 0};
    int kernel = 0;
};

namespace detail2 {
inline V2 pysum4(V2 a0, V2 a1, V2 a2, V2 a3) {                // detail::pysum4 per lane
    using namespace simd2;
    V2 f = splat(0.0) + a0, c = splat(0.0);
    const V2 xs[3] = {a1, a2, a3};
    for (V2 x : xs) {
        const V2 t = f + x;
        c = c + select(ge(vabs(f), vabs(x)), (f - t) + x, (x - t) + f);
        f = t;
    }
    return select(ne(c, splat(0.0)) & finite(c), f + c, f);
}
template <class L>
inline void resid2(const V2 (&p)[4], const double (&K)[4][3], V2 i0, V2 i1, const PentodeParams& par, V2& r0, V2& r1,
                   V2& fv, V2 (&v)[4], Law2& Lw, LawWork2& w) {
    using namespace simd2;
    for (int r = 0; r < 4; ++r) v[r] = p[r] - splat(K[r][0]) * i0 - splat(K[r][1]) * i1;
    values2<L>(v, par, Lw, w);
    r0 = i0 - Lw.i[0]; r1 = i1 - Lw.i[1];
    fv = vabs(splat(K[0][0]) * r0 + splat(K[0][1]) * r1);
    for (int r = 1; r < 4; ++r) fv = pmax(fv, vabs(splat(K[r][0]) * r0 + splat(K[r][1]) * r1));
}
}  // namespace detail2

// solve() on two independent problems, one per lane: each lane's result, iterations and flag are solve()'s
template <class L>
inline Solution2 solve2(const dkcore::simd::V2 (&p)[4], const double (&K)[4][3], dkcore::simd::V2 i0,
                        dkcore::simd::V2 i1, const PentodeParams& par, const Newton& nw) {
    using namespace simd2;
    using detail2::resid2;
    using detail2::pysum4;
    const V2 one = splat(1.0), half = splat(0.5), tol_v = splat(nw.tol_v), tol_f = splat(nw.tol_f),
             inf = splat(INFINITY);
    V2 r0, r1, fv, v[4];
    Law2 Lw;
    detail2::LawWork2 w;
    resid2<L>(p, K, i0, i1, par, r0, r1, fv, v, Lw, w);
    detail2::jacobian2<L>(w, par, Lw);
    V2 fb = fv;
    V2 bv[4] = {v[0], v[1], v[2], v[3]}, bi0 = i0 - r0, bi1 = i1 - r1;  // best iterate per lane
    Solution2 out{};
    out.it[0] = out.it[1] = nw.max_it;
    M2 act = all();
    for (int it = 1; it <= nw.max_it; ++it) {
        const auto& J = Lw.J;
        const V2 K00 = splat(K[0][0]), K10 = splat(K[1][0]), K20 = splat(K[2][0]), K30 = splat(K[3][0]);
        const V2 K01 = splat(K[0][1]), K11 = splat(K[1][1]), K21 = splat(K[2][1]), K31 = splat(K[3][1]);
        const V2 a = one + pysum4(J[0][0] * K00, J[0][1] * K10, J[0][2] * K20, J[0][3] * K30);
        const V2 b = pysum4(J[0][0] * K01, J[0][1] * K11, J[0][2] * K21, J[0][3] * K31);
        const V2 c = pysum4(J[1][0] * K00, J[1][1] * K10, J[1][2] * K20, J[1][3] * K30);
        const V2 d = one + pysum4(J[1][0] * K01, J[1][1] * K11, J[1][2] * K21, J[1][3] * K31);
        const V2 det = a * d - b * c;
        const V2 s0 = -(d * r0 - b * r1) / det;
        const V2 s1 = -(-c * r0 + a * r1) / det;
        V2 lam = one;
        V2 g0, g1, gv, gvolt[4];
        Law2 gL;
        detail2::LawWork2 gw;
        // inactive lanes evaluate at their frozen point (finite, so the vector maths stays on its fast path)
        resid2<L>(p, K, select(act, i0 + s0, i0), select(act, i1 + s1, i1), par, g0, g1, gv, gvolt, gL, gw);
        const M2 search = act & ~(lt(gv, fv) | lt(fv, tol_f));
        if (any(search)) {
            M2 ls = search;
            for (int h = 0; h < LS_MAX - 1 && any(ls); ++h) {
                lam = select(ls, lam * half, lam);
                V2 h0, h1, hv, hvolt[4];
                Law2 hL;
                detail2::LawWork2 hw;
                resid2<L>(p, K, select(ls, i0 + lam * s0, i0), select(ls, i1 + lam * s1, i1), par, h0, h1, hv, hvolt,
                          hL, hw);
                g0 = select(ls, h0, g0); g1 = select(ls, h1, g1); gv = select(ls, hv, gv);
                for (int k = 0; k < 4; ++k) gvolt[k] = select(ls, hvolt[k], gvolt[k]);
                detail2::select_values(ls, hL, gL);
                gw = detail2::select_work(ls, hw, gw);
                ls = ls & ~lt(hv, fv);
            }
        }
        V2 dv = vabs(K00 * lam * s0 + K01 * lam * s1);
        dv = pmax(dv, vabs(K10 * lam * s0 + K11 * lam * s1));
        dv = pmax(dv, vabs(K20 * lam * s0 + K21 * lam * s1));
        dv = pmax(dv, vabs(K30 * lam * s0 + K31 * lam * s1));
        i0 = select(act, i0 + lam * s0, i0); i1 = select(act, i1 + lam * s1, i1);
        r0 = select(act, g0, r0); r1 = select(act, g1, r1); fv = select(act, gv, fv);
        for (int k = 0; k < 4; ++k) v[k] = select(act, gvolt[k], v[k]);
        const M2 conv = act & lt(dv, tol_v) & lt(fv, tol_f);
        if (const int cb = bits(conv)) {
            for (int l = 0; l < 2; ++l)
                if (cb >> l & 1) { out.it[l] = it; out.ok[l] = true; }
            act = act & ~conv;
            if (!any(act)) break;
        }
        detail2::jacobian2<L>(gw, par, gL);                // the Jacobian at the accepted point (active lanes)
        detail2::select_jacobian(act, gL, Lw, 2);
        const M2 better = act & (lt(fv, fb) | ~lt(fb, inf)) & lt(vabs(r0) + vabs(r1), inf);
        fb = select(better, fv, fb);
        for (int k = 0; k < 4; ++k) bv[k] = select(better, v[k], bv[k]);
        bi0 = select(better, i0 - r0, bi0); bi1 = select(better, i1 - r1, bi1);
    }
    // converged lanes were frozen at their solution; lanes still active hit the cap and return their best iterate
    for (int k = 0; k < 4; ++k) out.v[k] = select(act, bv[k], v[k]);
    out.i[0] = select(act, bi0, i0 - r0); out.i[1] = select(act, bi1, i1 - r1); out.i[2] = splat(0.0);
    out.fv = select(act, fb, fv);
    out.attempts[0] = out.attempts[1] = 1;
    out.kernel = out.it[0] > out.it[1] ? out.it[0] : out.it[1];
    return out;
}

namespace detail2 {
template <class L>
inline M2 resid3(const V2 (&p)[4], const double (&K)[4][3], const V2 (&i)[3], const PentodeParams& par,
                 const GridParams& g, V2 (&r)[3], V2& fv, V2 (&v)[4], Law2& Lw, LawWork2& w) {
    using namespace simd2;
    for (int q = 0; q < 4; ++q) v[q] = p[q] - splat(K[q][0]) * i[0] - splat(K[q][1]) * i[1] - splat(K[q][2]) * i[2];
    values3<L>(v, par, g, Lw, w);
    for (int k = 0; k < 3; ++k) r[k] = i[k] - Lw.i[k];
    fv = vabs(splat(K[0][0]) * r[0] + splat(K[0][1]) * r[1] + splat(K[0][2]) * r[2]);
    for (int q = 1; q < 4; ++q) fv = pmax(fv, vabs(splat(K[q][0]) * r[0] + splat(K[q][1]) * r[1] + splat(K[q][2]) * r[2]));
    const M2 ok = ~Lw.raised & lt(fv, splat(INFINITY));
    fv = select(ok, fv, splat(INFINITY));
    return ok;
}
// detail::jacobian3_safe on the lanes of m (the others' fv and Jacobian are not touched): the lanes of m where it holds
template <class L>
inline M2 jacobian3_safe(M2 m, const LawWork2& w, const PentodeParams& par, const GridParams& g, Law2& Lw, V2& fv) {
    using namespace simd2;
    Law2 t = Lw;
    jacobian3<L>(w, par, g, t);
    M2 ok = ~t.raised;
    for (int a = 0; a < 3; ++a) for (int b = 0; b < 4; ++b) ok = ok & finite(t.J[a][b]);
    select_jacobian(m, t, Lw, 3);
    Lw.raised = (m & t.raised) | (~m & Lw.raised);
    fv = select(m & ~ok, splat(INFINITY), fv);
    return m & ok;
}
inline V2 step_norm3(const double (&K)[4][3], const V2 (&s)[3], V2 lam) {
    using namespace simd2;
    V2 dv = vabs(lam * (splat(K[0][0]) * s[0] + splat(K[0][1]) * s[1] + splat(K[0][2]) * s[2]));
    for (int q = 1; q < 4; ++q) dv = pmax(dv, vabs(lam * (splat(K[q][0]) * s[0] + splat(K[q][1]) * s[1] + splat(K[q][2]) * s[2])));
    return dv;
}
// detail::step3 on two lanes; the lanes where det != 0
inline M2 step3(const V2 (&J)[3][4], const double (&Km)[4][3], const V2 (&r)[3], V2 (&x)[3]) {
    using namespace simd2;
    V2 K[4][3];
    for (int a = 0; a < 4; ++a) for (int b = 0; b < 3; ++b) K[a][b] = splat(Km[a][b]);
    const V2 one = splat(1.0);
    const V2 j00 = J[0][0], j01 = J[0][1], j02 = J[0][2], j03 = J[0][3];
    const V2 j10 = J[1][0], j11 = J[1][1], j12 = J[1][2], j13 = J[1][3], j20 = J[2][0];
    const V2 m00 = one + j00 * K[0][0] + j01 * K[1][0] + j02 * K[2][0] + j03 * K[3][0];
    const V2 m01 = j00 * K[0][1] + j01 * K[1][1] + j02 * K[2][1] + j03 * K[3][1];
    const V2 m02 = j00 * K[0][2] + j01 * K[1][2] + j02 * K[2][2] + j03 * K[3][2];
    const V2 m10 = j10 * K[0][0] + j11 * K[1][0] + j12 * K[2][0] + j13 * K[3][0];
    const V2 m11 = one + j10 * K[0][1] + j11 * K[1][1] + j12 * K[2][1] + j13 * K[3][1];
    const V2 m12 = j10 * K[0][2] + j11 * K[1][2] + j12 * K[2][2] + j13 * K[3][2];
    const V2 m20 = j20 * K[0][0], m21 = j20 * K[0][1], m22 = one + j20 * K[0][2];
    const V2 r0 = r[0], r1 = r[1], r2 = r[2];
    const V2 c0 = m11 * m22 - m12 * m21;
    const V2 c1 = m12 * m20 - m10 * m22;
    const V2 c2 = m10 * m21 - m11 * m20;
    const V2 det = m00 * c0 + m01 * c1 + m02 * c2;
    const V2 x0 = (r0 * c0 + m01 * (r2 * m12 - r1 * m22) + m02 * (r1 * m21 - r2 * m11)) / det;
    const V2 x1 = (m00 * (r1 * m22 - r2 * m12) + r0 * c1 + m02 * (r2 * m10 - r1 * m20)) / det;
    const V2 x2 = (m00 * (r2 * m11 - r1 * m21) + m01 * (r1 * m20 - r2 * m10) + r0 * c2) / det;
    x[0] = -x0; x[1] = -x1; x[2] = -x2;
    return ne(det, splat(0.0));                   // det != 0, as the scalar test (NaN != 0: the step goes on)
}
}  // namespace detail2

/* solve_grid() on the lanes of `run`, one problem per lane: each such lane's result, iteration count and flags are
 * solve_grid's on its problem. The other lanes are not solved (their result is the start, it = 0, not ok). */
template <class L>
inline Solution2 solve_grid2(const dkcore::simd::V2 (&p)[4], const double (&K)[4][3],
                             const dkcore::simd::V2 (&i_start)[3], const PentodeParams& par, const GridParams& g,
                             const Newton& nw, dkcore::simd::M2 run) {
    using namespace simd2;
    using detail2::step_norm3;
    const V2 one = splat(1.0), half = splat(0.5), tol_v = splat(nw.tol_v), tol_f = splat(nw.tol_f),
             inf = splat(INFINITY);
    V2 i[3] = {i_start[0], i_start[1], i_start[2]}, r[3], fv, v[4];
    Law2 Lw;
    detail2::LawWork2 w;
    Solution2 out{};
    out.attempts[0] = out.attempts[1] = 1;
    // the start: failing lanes (and lanes not in run) leave with it = 0, fv = inf, v and i at the start
    M2 act = run & detail2::resid3<L>(p, K, i, par, g, r, fv, v, Lw, w);
    act = detail2::jacobian3_safe<L>(act, w, par, g, Lw, fv);
    V2 res_v[4], res_i[3], res_fv = inf;
    for (int q = 0; q < 4; ++q)
        res_v[q] = select(act, v[q], p[q] - splat(K[q][0]) * i[0] - splat(K[q][1]) * i[1] - splat(K[q][2]) * i[2]);
    for (int k = 0; k < 3; ++k) res_i[k] = select(act, i[k] - r[k], i[k]);
    res_fv = select(act, fv, inf);
    // best iterate per lane (the start counts); res_* are the best until a lane converges
    V2 fb = res_fv;
    int it_l[2] = {0, 0};
    int it = 0;
    for (it = 1; it <= nw.max_it && any(act); ++it) {
        V2 s[3];
        const M2 nonsing = detail2::step3(Lw.J, K, r, s);
        M2 brk = act & ~(nonsing & finite(s[0]) & finite(s[1]) & finite(s[2]));
        if (any(brk)) {                               // singular I + J K or a non-finite step: leave with the best
            for (int l = 0; l < 2; ++l) if (bits(brk) >> l & 1) it_l[l] = it;
            act = act & ~brk;
            if (!any(act)) break;
        }
        V2 lam = one, t[3], gr[3], gv, gvolt[4];
        for (int k = 0; k < 3; ++k) t[k] = select(act, i[k] + s[k], i[k]);
        Law2 gL;
        detail2::LawWork2 gw;
        M2 gok = detail2::resid3<L>(p, K, t, par, g, gr, gv, gvolt, gL, gw);
        for (int a = 0; a < 3; ++a) for (int b = 0; b < 4; ++b) gL.J[a][b] = Lw.J[a][b];
        // the full step accepted on its values: its Jacobian, unless it converges (as solve_grid)
        const M2 need = act & gok & (lt(gv, fv) | lt(fv, tol_f)) & ~(lt(step_norm3(K, s, lam), tol_v) & lt(gv, tol_f));
        if (any(need)) gok = (gok & ~need) | detail2::jacobian3_safe<L>(need, gw, par, g, gL, gv);
        const M2 search = act & ~(lt(gv, fv) | lt(fv, tol_f));
        if (any(search)) {
            M2 ls = search;
            for (int h = 0; h < LS_MAX - 1 && any(ls); ++h) {
                lam = select(ls, lam * half, lam);
                V2 ht[3], hr[3], hv, hvolt[4];
                for (int k = 0; k < 3; ++k) ht[k] = select(ls, i[k] + lam * s[k], i[k]);
                Law2 hL = gL;
                detail2::LawWork2 hw;
                const M2 hok = detail2::resid3<L>(p, K, ht, par, g, hr, hv, hvolt, hL, hw);
                for (int k = 0; k < 3; ++k) { t[k] = select(ls, ht[k], t[k]); gr[k] = select(ls, hr[k], gr[k]); }
                gv = select(ls, hv, gv);
                for (int q = 0; q < 4; ++q) gvolt[q] = select(ls, hvolt[q], gvolt[q]);
                detail2::select_values(ls, hL, gL);
                gw = detail2::select_work(ls, hw, gw);
                gok = (ls & hok) | (~ls & gok);
                ls = ls & ~lt(hv, fv);
            }
            const M2 need2 = search & gok & ~(lt(step_norm3(K, s, lam), tol_v) & lt(gv, tol_f));
            if (any(need2)) gok = (gok & ~need2) | detail2::jacobian3_safe<L>(need2, gw, par, g, gL, gv);
        }
        const M2 fail = act & ~gok;                   // no finite trial along the step: leave with the best
        if (any(fail)) {
            for (int l = 0; l < 2; ++l) if (bits(fail) >> l & 1) it_l[l] = it;
            act = act & ~fail;
            if (!any(act)) break;
        }
        const V2 dv = step_norm3(K, s, lam);
        for (int k = 0; k < 3; ++k) { i[k] = select(act, t[k], i[k]); r[k] = select(act, gr[k], r[k]); }
        fv = select(act, gv, fv);
        for (int q = 0; q < 4; ++q) v[q] = select(act, gvolt[q], v[q]);
        const M2 conv = act & lt(dv, tol_v) & lt(fv, tol_f);
        if (any(conv)) {
            for (int q = 0; q < 4; ++q) res_v[q] = select(conv, v[q], res_v[q]);
            for (int k = 0; k < 3; ++k) res_i[k] = select(conv, i[k] - r[k], res_i[k]);
            res_fv = select(conv, fv, res_fv);
            for (int l = 0; l < 2; ++l) if (bits(conv) >> l & 1) { it_l[l] = it; out.ok[l] = true; }
            act = act & ~conv;
            if (!any(act)) break;
        }
        detail2::select_jacobian(act, gL, Lw, 3);
        const M2 better = act & lt(fv, fb);
        fb = select(better, fv, fb);
        for (int q = 0; q < 4; ++q) res_v[q] = select(better, v[q], res_v[q]);
        for (int k = 0; k < 3; ++k) res_i[k] = select(better, i[k] - r[k], res_i[k]);
    }
    for (int l = 0; l < 2; ++l) if (bits(act) >> l & 1) it_l[l] = nw.max_it;   // at the cap (Python's range)
    for (int l = 0; l < 2; ++l) out.it[l] = it_l[l];
    out.kernel = it_l[0] > it_l[1] ? it_l[0] : it_l[1];     // lanes outside `run` have it 0
    for (int q = 0; q < 4; ++q) out.v[q] = res_v[q];
    for (int k = 0; k < 3; ++k) out.i[k] = res_i[k];
    out.fv = select(lanes(int(out.ok[0]) | int(out.ok[1]) << 1), res_fv, fb);     // converged: fv; else the best
    return out;
}

/* solve_grid_restarts() on two lanes: each start in turn on the lanes that have not converged, else per lane the
 * smallest finite residual of all its starts; it summed over the starts that lane ran */
template <class L>
inline Solution2 solve_grid_restarts2(const dkcore::simd::V2 (&p)[4], const double (&K)[4][3],
                                      const dkcore::simd::V2 (&starts)[3][3], const PentodeParams& par,
                                      const GridParams& g, const Newton& nw) {
    using namespace simd2;
    Solution2 best{}, out{};
    bool have[2] = {false, false}, need[2] = {true, true};
    int total[2] = {0, 0};
    for (int k = 0; k < 3 && (need[0] || need[1]); ++k) {
        const Solution2 s = solve_grid2<L>(p, K, starts[k], par, g, nw, lanes(int(need[0]) | int(need[1]) << 1));
        out.kernel += s.kernel;
        for (int l = 0; l < 2; ++l) {
            if (!need[l]) continue;
            const M2 m = lanes(1 << l);
            total[l] += s.it[l];
            if (s.ok[l]) {
                for (int q = 0; q < 4; ++q) out.v[q] = select(m, s.v[q], out.v[q]);
                for (int c = 0; c < 3; ++c) out.i[c] = select(m, s.i[c], out.i[c]);
                out.fv = select(m, s.fv, out.fv);
                out.it[l] = total[l]; out.ok[l] = true; out.attempts[l] = k + 1;
                need[l] = false;
                continue;
            }
            if (!have[l] || lane(s.fv, l) < lane(best.fv, l)) {
                for (int q = 0; q < 4; ++q) best.v[q] = select(m, s.v[q], best.v[q]);
                for (int c = 0; c < 3; ++c) best.i[c] = select(m, s.i[c], best.i[c]);
                best.fv = select(m, s.fv, best.fv);
                have[l] = true;
            }
        }
    }
    for (int l = 0; l < 2; ++l) {
        if (!need[l]) continue;
        const M2 m = lanes(1 << l);
        for (int q = 0; q < 4; ++q) out.v[q] = select(m, best.v[q], out.v[q]);
        for (int c = 0; c < 3; ++c) out.i[c] = select(m, best.i[c], out.i[c]);
        out.fv = select(m, best.fv, out.fv);
        out.it[l] = total[l]; out.ok[l] = false; out.attempts[l] = 3;
    }
    return out;
}

// ---- the stereo stage -------------------------------------------------------------------------------------------

template <int MaxOs> class StereoStageT {
public:
    using V2 = dkcore::simd::V2;
    using Resampler = dkcore::ResamplerT<V2, dkcore::K140_FIRST_MAX_TAPS, dkcore::K140_LATER_TAPS, MaxOs>;

    // ---- non-RT, never concurrently with process() ------------------------------------------------------------
    // as StageT::prepare / prepare_pack, one pack for both channels
    Status prepare(double fs_base, Quality q = STANDARD, const Settings& s = Settings{}) {
        prepared_ = false;
        if (!(fs_base > 0.0) || !std::isfinite(fs_base) || q.prewarp_hz > 0.0) return Status::bad_argument;
        Pack p;
        const Status st = build_pack(p, fs_base * q.os, s);
        if (st != Status::ok) { pack_ = p; return st; }
        return install(fs_base, q.os, p, s.lpf, s.fir);
    }
    Status prepare_pack(double fs_base, int os, const Pack& p, Lpf lpf = Lpf::off, const FirDesign& fir = DEFAULT_FIR) {
        prepared_ = false;
        if (!(fs_base > 0.0) || p.fs != fs_base * os) return Status::bad_argument;
        return install(fs_base, os, p, lpf, fir);
    }

    // ---- any thread ---------------------------------------------------------------------------------------------
    // per channel, as Stage's: a non-finite value is ignored for its channel
    void set_drive(double l, double r) { store_finite(drive_[0], l); store_finite(drive_[1], r); }
    void set_drive(double g) { set_drive(g, g); }
    void set_output(double l, double r) { store_finite(output_[0], l); store_finite(output_[1], r); }
    void set_output(double g) { set_output(g, g); }
    void set_lpf(Lpf p) { lpf_req_.store(std::uint8_t(p), std::memory_order_relaxed); }
    // the lane maths with LawMath::fast (default vector; per_lane: the bit-exact reference). Ignored with libm.
    void set_lane_math(LaneMath m) { lane_math_.store(m, std::memory_order_relaxed); }
    LaneMath lane_math() const { return lane_math_.load(std::memory_order_relaxed); }

    // ---- audio thread -------------------------------------------------------------------------------------------
    void reset() noexcept {
        for (V2& v : x_) v = dkcore::simd::splat(0.0);
        for (int k = 0; k < NI_MAX; ++k) i_[k] = q_[k] = dkcore::simd::splat(pack_.i_dc[k]);
        stats_[0] = stats_[1] = Stats{};
        kernel_iterations_ = 0;
        rs_.reset();
        const Lpf want = Lpf(lpf_req_.load(std::memory_order_relaxed));
        for (auto& f : lpf_) {
            if (want != f.position()) f.set_position(want);
            f.reset();
        }
        lpf_seen_ = want;
    }
    // As Stage::process, per channel. inL may equal outL, and inR outR. recL / recR (optional): each lane's
    // internal-rate signals, as Stage::process's Record (n * oversampling() values each).
    void process(const double* inL, const double* inR, double* outL, double* outR, std::size_t n,
                 const Record* recL = nullptr, const Record* recR = nullptr) noexcept {
        process_t(inL, inR, outL, outR, n, recL, recR);
    }
    void process(const float* inL, const float* inR, float* outL, float* outR, std::size_t n) noexcept {
        process_t(inL, inR, outL, outR, n, nullptr, nullptr);
    }

    bool prepared() const { return prepared_; }
    const Pack& pack() const { return pack_; }
    int oversampling() const { return rs_.os(); }
    int latency_samples() const { return rs_.latency_samples(); }
    const Stats& stats(int ch) const { return stats_[ch]; }        // ch 0 = left, 1 = right
    // Newton iterations the 2-lane kernel ran since reset(): per internal sample and per start, the larger iteration
    // count of the lanes that ran that start, summed (Solution2::kernel)
    std::uint64_t kernel_iterations() const { return kernel_iterations_; }
    double state_x(int ch, int i) const { return dkcore::simd::lane(x_[i], ch); }
    double state_i(int ch, int k) const { return dkcore::simd::lane(i_[k], ch); }

private:
    static void store_finite(std::atomic<double>& a, double g) { if (std::isfinite(g)) a.store(g, std::memory_order_relaxed); }

    Status install(double fs_base, int os, const Pack& p, Lpf lpf, const FirDesign& fir) {
        if (!rs_.prepare(os, fs_base, fir)) return Status::bad_argument;
        for (auto& f : lpf_) if (!f.prepare(p.fs, lpf)) return Status::bad_argument;
        pack_ = p;
        lpf_req_.store(std::uint8_t(lpf), std::memory_order_relaxed);
        prepared_ = true;
        reset();
        return Status::ok;
    }
    void begin_block() noexcept {
        const Lpf want = Lpf(lpf_req_.load(std::memory_order_relaxed));
        if (want != lpf_seen_) { lpf_seen_ = want; for (auto& f : lpf_) f.set_position(want); }
        for (auto& s : stats_) { s.nonconv_block = 0; s.out_of_law_block = 0; }
    }

    // StageT::tick on both lanes: the same expressions with V2 for double
    template <bool GRID, class L> V2 tick(V2 s, const Record* const (&rec)[2], std::size_t idx) noexcept {
        using namespace simd2;
        const Pack& c = pack_;
        V2 p[NV];
        for (int r = 0; r < NV; ++r) {
            V2 a = splat(c.G[r][0]) * x_[0];
            for (int j = 1; j < NX; ++j) a = a + splat(c.G[r][j]) * x_[j];
            p[r] = a + splat(c.H[r][0]) * s + splat(c.p_dc[r]);
        }
        V2 e[NI_MAX];
        for (int k = 0; k < NI_MAX; ++k) e[k] = splat(2.0) * i_[k] - q_[k];
        for (int k = 0; k < NI_MAX; ++k) q_[k] = i_[k];
        Solution2 r;
        if (GRID) {
            const V2 starts[3][3] = {{e[0], e[1], e[2]}, {q_[0], q_[1], q_[2]},
                                     {splat(c.i_dc[0]), splat(c.i_dc[1]), splat(c.i_dc[2])}};
            r = solve_grid_restarts2<L>(p, c.K, starts, c.par, c.g, c.newton);
        } else {
            r = solve2<L>(p, c.K, e[0], e[1], c.par, c.newton);
        }
        for (int k = 0; k < NI_MAX; ++k) i_[k] = r.i[k];      // kept even when !ok: the best finite iterate (#7)
        for (int l = 0; l < 2; ++l) {
            Stats& st = stats_[l];
            const int it = r.it[l];
            ++st.samples;
            st.iterations += std::uint64_t(it);
            ++st.histogram[it < Stats::HIST ? it : Stats::HIST - 1];
            if (it > st.max_iterations) st.max_iterations = it;
            if (!r.ok[l]) { ++st.nonconverged; ++st.nonconv_block; }
            if (GRID && r.attempts[l] > 1) ++st.restarted;
            const double v0 = lane(r.v[0], l), v3 = lane(r.v[3], l);
            if ((GRID ? lane(r.i[0], l) + lane(r.i[1], l) < 0.0 : v0 > 0.0) || v3 < 0.0) { ++st.out_of_law; ++st.out_of_law_block; }
            if (const Record* q = rec[l]) {                // StageT::tick's record, per lane
                if (q->vgk) q->vgk[idx] = v0;
                if (q->vg2k) q->vg2k[idx] = lane(r.v[1], l);
                if (q->vak) q->vak[idx] = v3;
                if (q->ig1) q->ig1[idx] = GRID ? lane(r.i[2], l) : 0.0;
                if (q->ipk) q->ipk[idx] = lane(r.i[0], l) + lane(r.i[1], l);
                if (q->it) q->it[idx] = std::uint16_t(it);
            }
        }
        kernel_iterations_ += std::uint64_t(r.kernel);
        V2 inl[NI_MAX];
        constexpr int NI = GRID ? 3 : 2;
        for (int k = 0; k < NI; ++k) inl[k] = i_[k] - splat(c.i_dc[k]);
        V2 dx = splat(c.D[0][0]) * x_[0];
        for (int j = 1; j < NX; ++j) dx = dx + splat(c.D[0][j]) * x_[j];
        V2 fi = splat(c.F[0][0]) * inl[0];
        for (int k = 1; k < NI; ++k) fi = fi + splat(c.F[0][k]) * inl[k];
        const V2 y = dx + splat(c.E[0][0]) * s + fi + splat(c.y_dc[0]);
        V2 xn[NX];
        for (int i = 0; i < NX; ++i) {
            V2 a = splat(c.A[i][0]) * x_[0];
            for (int j = 1; j < NX; ++j) a = a + splat(c.A[i][j]) * x_[j];
            V2 ci = splat(c.C[i][0]) * inl[0];
            for (int k = 1; k < NI; ++k) ci = ci + splat(c.C[i][k]) * inl[k];
            xn[i] = a + splat(c.B[i][0]) * s + ci;
        }
        for (int i = 0; i < NX; ++i) x_[i] = xn[i];
        return y;
    }

    template <bool GRID, class L, class S>
    void run(const S* inL, const S* inR, S* outL, S* outR, std::size_t n, V2 drive, V2 og,
             const Record* const (&rec)[2]) noexcept {
        using dkcore::simd::lane;
        V2 u[MaxOs], y[MaxOs];
        const int os = rs_.os();
        for (std::size_t i = 0; i < n; ++i) {
            const V2 xd = drive * dkcore::simd::make(double(inL[i]), double(inR[i]));   // checked after the gain
            double xl = lane(xd, 0), xr = lane(xd, 1);
            if (!std::isfinite(xl)) { xl = 0.0; ++stats_[0].nonfinite_inputs; }
            if (!std::isfinite(xr)) { xr = 0.0; ++stats_[1].nonfinite_inputs; }
            rs_.up(dkcore::simd::make(xl, xr), u);
            for (int j = 0; j < os; ++j) {
                const V2 t = tick<GRID, L>(u[j], rec, i * std::size_t(os) + std::size_t(j));
                y[j] = dkcore::simd::make(lpf_[0].tick(lane(t, 0)), lpf_[1].tick(lane(t, 1)));
            }
            const V2 yd = rs_.down(y);
            const int bad = nonfinite_lanes(yd) | nonfinite_state_lanes();
            if (bad) recover(bad);
            const V2 o = og * yd;
            outL[i] = out_lane<S>(o, 0, bad);
            outR[i] = out_lane<S>(o, 1, bad);
        }
    }
    static int nonfinite_lanes(V2 v) noexcept {
        const V2 zero = dkcore::simd::splat(0.0);
        return dkcore::simd::bits(dkcore::simd::ne(v * zero, zero));
    }
    int nonfinite_state_lanes() const noexcept {
        const V2 zero = dkcore::simd::splat(0.0);
        V2 z = zero;
        for (const V2& v : x_) z = z + v * zero;
        for (int k = 0; k < NI_MAX; ++k) z = z + i_[k] * zero + q_[k] * zero;
        return dkcore::simd::bits(dkcore::simd::ne(z, zero));
    }
    // StageT::recover on the lanes of b only
    void recover(int b) noexcept {
        using dkcore::simd::keep;
        using dkcore::simd::splat;
        for (V2& v : x_) v = keep(b, splat(0.0), v);
        for (int k = 0; k < NI_MAX; ++k) {
            i_[k] = keep(b, splat(pack_.i_dc[k]), i_[k]);
            q_[k] = keep(b, splat(pack_.i_dc[k]), q_[k]);
        }
        rs_.reset_lanes(b);
        for (int k = 0; k < 2; ++k) if (b >> k & 1) { lpf_[k].clear_states(); ++stats_[k].recoveries; }
    }
    template <class S> S out_lane(V2 o, int ch, int bad) noexcept {
        if (bad >> ch & 1) return S(0);
        const double v = dkcore::simd::lane(o, ch);
        if (!(std::fabs(v) <= double(std::numeric_limits<S>::max()))) { ++stats_[ch].nonfinite_outputs; return S(0); }
        return S(v);
    }
    template <class L, class S>
    void run_g(const S* inL, const S* inR, S* outL, S* outR, std::size_t n, V2 d, V2 o,
               const Record* const (&rec)[2]) noexcept {
        if (pack_.grid) run<true, L>(inL, inR, outL, outR, n, d, o, rec);
        else run<false, L>(inL, inR, outL, outR, n, d, o, rec);
    }
    template <class S>
    void process_t(const S* inL, const S* inR, S* outL, S* outR, std::size_t n, const Record* recL,
                   const Record* recR) noexcept {
        dkcore::ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) outL[i] = outR[i] = S(0); return; }
        const V2 d = dkcore::simd::make(drive_[0].load(std::memory_order_relaxed), drive_[1].load(std::memory_order_relaxed));
        const V2 o = dkcore::simd::make(output_[0].load(std::memory_order_relaxed), output_[1].load(std::memory_order_relaxed));
        const Record* const rec[2] = {recL, recR};
        if (pack_.math != LawMath::fast) run_g<PerLaneLaw<LibmLaw>>(inL, inR, outL, outR, n, d, o, rec);
        else if (lane_math_.load(std::memory_order_relaxed) == LaneMath::vector) run_g<VecFastLaw>(inL, inR, outL, outR, n, d, o, rec);
        else run_g<PerLaneLaw<FastLaw>>(inL, inR, outL, outR, n, d, o, rec);
    }

    // audio-thread state: lane 0 = left, lane 1 = right
    V2 x_[NX]{}, i_[NI_MAX]{}, q_[NI_MAX]{};
    Pack pack_{};
    Resampler rs_;
    PostFilter lpf_[2];
    Stats stats_[2];
    std::uint64_t kernel_iterations_ = 0;
    Lpf lpf_seen_ = Lpf::off;
    bool prepared_ = false;
    // shared
    std::atomic<double> drive_[2] = {{1.0}, {1.0}}, output_[2] = {{1.0}, {1.0}};
    std::atomic<std::uint8_t> lpf_req_{0};
    std::atomic<LaneMath> lane_math_{LaneMath::vector};
    static_assert(std::atomic<LaneMath>::is_always_lock_free, "lock-free lane maths switch");
};
using StereoStage = StereoStageT<dkcore::MAX_OS>;

}  // namespace dk6as6
#endif
