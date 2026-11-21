/* Tests for cpp/dk6as6_core.h (#104) against the Python 6AS6 DK stage's vectors (cpp/vectors_6as6.txt), plus the
 * realtime properties: no allocation in process(), FTZ/DAZ scoping, block-size invariance, the #7 / #112 fallbacks.
 *
 *   test_core_6as6 [vectors_6as6.txt]   run every test; one JSON line of measured errors; exit 1 on any failure
 *   test_core_6as6 cli FS OS MODE BIAS HARDNESS LPF DRIVE OUTPUT FIR [rec]
 *                    stdin: float64 base-rate input; stdout: float64 output (then, with "rec", the internal-rate
 *                    Vg1k and Vg2k); stderr: one JSON line (pack, operating point, latency, stats). MODE P1 / T / P2,
 *                    LPF off / 4k / 7k, FIR shipped / k140. Experimental settings are allowed (as validate.py runs
 *                    them). Used by cpp/validate_cpp_6as6.py and cpp/aliasing_6as6.py.
 *   test_core_6as6 law | solve2 | solve3 | restarts
 *                    stdin rows -> stdout: the valve law / the solvers, for the bit-exact comparison with the live
 *                    Python functions (cpp/tests/test_cpp_core_6as6.py)
 * Build: c++ -std=c++17 -O2 -Wall -Wextra -pedantic -pthread -o test_core_6as6 cpp/test_core_6as6.cpp */
#include "dk6as6_core.h"

#include <algorithm>
#include <cinttypes>
#include <cstdio>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#include "alloc_hooks.h"   // allocation counting: g_count, CountAllocs, ALLOC_HOOKS

using namespace dk6as6;

// ---- tolerances (cpp/README-6as6.md, "Tolerances") ----------------------------------------------------------------
// Set from the worst error measured (Apple clang 17 -O2 on arm64, with and without FP contraction) with the margin in
// brackets; CI runs them with g++ on x86-64 Linux and Apple clang on macOS (README-6as6.md, "Tests and tolerances"). All are many orders below
// dk/validate.CRIT (op 1e-3 relative, 0.1 dB) and below what the stage's own Newton tolerance (1e-6 V) moves.
static const double TOL_LAW = 1e-13;        // per output column / its max |value|; measured 4.3e-16      [230x]
static const double TOL_SOLVE = 1e-11;      // solvers alone: V abs, currents / 1 mA; measured 6.0e-13     [17x]
static const double TOL_COEF = 1e-10;       // max |dM| / max |M| per matrix, op point, rest; meas. 9.8e-12 [10x]
static const double TOL_RK = 1e-11;         // Rk relative (Brent, matrices rounding differently); 1.1e-13 [90x]
static const double TOL_RS = 1e-13;         // resampler / post-filter alone, / max |output|
static const double TOL_PINNED_ABS = 1e-9;  // V, stage on the Python pack (per-sample arithmetic); 6.8e-13
static const double TOL_BUILT_ABS = 1e-5;   // V, stage on the C++-built pack (matrix rounding); 4.2e-8
static const double TOL_ITER_FRAC = 1e-3;   // Newton iteration sums (borderline samples); measured: identical

// ---- small helpers -------------------------------------------------------------------------------------------
static int g_fail = 0;
static std::ostringstream g_json;
static void check(bool ok, const std::string& what) {
    if (!ok) { ++g_fail; std::fprintf(stderr, "FAIL: %s\n", what.c_str()); }
}
static void metric(const std::string& k, double v) {
    g_json << (g_json.tellp() > 0 ? ", " : "") << "\"" << k << "\": " << v;
}
using Vec = std::vector<double>;
using Vectors = std::map<std::string, Vec>;
static Vectors load(const std::string& path) {
    Vectors m;
    std::ifstream f(path);
    if (!f) { std::fprintf(stderr, "cannot open %s\n", path.c_str()); std::exit(2); }
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream is(line);
        std::string name; size_t n;
        is >> name >> n;
        Vec v(n);
        for (size_t i = 0; i < n; ++i) {
            std::string t; is >> t;
            v[i] = std::strtod(t.c_str(), nullptr);       // "nan" reads as NaN
        }
        m[name] = std::move(v);
    }
    return m;
}
static const Vec& need(const Vectors& m, const std::string& k) {
    auto it = m.find(k);
    if (it == m.end()) { std::fprintf(stderr, "missing vector %s\n", k.c_str()); std::exit(2); }
    return it->second;
}
static double maxabs(const double* a, size_t n) { double m = 0; for (size_t i = 0; i < n; ++i) m = std::max(m, std::fabs(a[i])); return m; }
static double maxdiff(const double* a, const double* b, size_t n) {
    double m = 0;
    for (size_t i = 0; i < n; ++i) {
        if (std::isnan(a[i]) && std::isnan(b[i])) continue;
        const double d = std::fabs(a[i] - b[i]);
        m = std::isnan(d) ? INFINITY : std::max(m, d);
    }
    return m;
}
static bool same_bits(double a, double b) { return std::memcmp(&a, &b, 8) == 0 || (std::isnan(a) && std::isnan(b)); }
static std::string fmt(double v) { char b[32]; std::snprintf(b, sizeof b, "%g", v); return b; }
static const char* MODE_NAMES[3] = {"P1", "T", "P2"};
static std::string pack_name(const std::string& mode, double h, double bias, int os, double fs) {
    return "pack." + mode + "." + (h != 0.0 ? "g" : "n") + "." + fmt(bias) + "." + std::to_string(os) + "." +
           std::to_string(long(fs));
}
static const FirDesign& fir_of(double k) { return k == 1.0 ? dkcore::K140_FIR : dkcore::SHIPPED_FIR; }

// ---- params, law, solvers ----------------------------------------------------------------------------------------
static void test_params(const Vectors& V) {
    const Vec& p = need(V, "par.dual_control");
    const double mine[13] = {DUAL_CONTROL.MU, DUAL_CONTROL.EX, DUAL_CONTROL.KG1, DUAL_CONTROL.KP, DUAL_CONTROL.FMAX,
                             DUAL_CONTROL.MU3, DUAL_CONTROL.V3C, DUAL_CONTROL.S3, DUAL_CONTROL.P3, DUAL_CONTROL.RI,
                             DUAL_CONTROL.KVB, DUAL_CONTROL.KR, DUAL_CONTROL.XA};
    bool eq = p.size() == 13;
    for (int k = 0; eq && k < 13; ++k) eq = same_bits(mine[k], p[k]);
    check(eq, "DUAL_CONTROL equals fit_results.json bit for bit");
    const Vec& g = need(V, "par.grid");
    const GridParams gp = grid();
    check(same_bits(gp.GG, g[0]) && same_bits(gp.XI, g[1]) && same_bits(gp.CG, g[2]) && same_bits(gp.hardness, g[3]),
          "grid() equals pentode.GRID bit for bit");
}

static void test_law(const Vectors& V) {
    const Vec& in = need(V, "law.in");
    const Vec& rn = need(V, "law.nogrid");
    const Vec& rg = need(V, "law.grid");
    const size_t n = in.size() / 4;
    Vec gn(n * 10), gg(n * 15);
    const GridParams g = grid();
    for (size_t k = 0; k < n; ++k) {
        const double v[4] = {in[4 * k], in[4 * k + 1], in[4 * k + 2], in[4 * k + 3]};
        Law a, b, c;
        currents<true>(v, DUAL_CONTROL, a);
        currents<false>(v, DUAL_CONTROL, c);
        gn[10 * k] = a.i[0]; gn[10 * k + 1] = a.i[1];
        for (int j = 0; j < 4; ++j) { gn[10 * k + 2 + j] = a.J[0][j]; gn[10 * k + 6 + j] = a.J[1][j]; }
        check(same_bits(a.i[0], c.i[0]) && same_bits(a.i[1], c.i[1]), "currents_only == currents' values");
        currents_grid<true>(v, DUAL_CONTROL, g, b);
        for (int q = 0; q < 3; ++q) gg[15 * k + q] = b.i[q];
        for (int q = 0; q < 3; ++q) for (int j = 0; j < 4; ++j) gg[15 * k + 3 + 4 * q + j] = b.J[q][j];
    }
    auto colerr = [&](const Vec& got, const Vec& ref, int w) {
        double worst = 0.0;
        long eq = 0;
        for (int c = 0; c < w; ++c) {
            double m = 0.0, d = 0.0;
            for (size_t k = 0; k < n; ++k) m = std::max(m, std::fabs(ref[w * k + c]));
            for (size_t k = 0; k < n; ++k) {
                d = std::max(d, std::fabs(got[w * k + c] - ref[w * k + c]));
                eq += same_bits(got[w * k + c], ref[w * k + c]);
            }
            worst = std::max(worst, m > 0 ? d / m : d);
        }
        return std::make_pair(worst, double(eq) / double(n * w));
    };
    const auto en = colerr(gn, rn, 10), eg = colerr(gg, rg, 15);
    metric("law_nogrid_err", en.first); metric("law_nogrid_bit_equal_frac", en.second);
    metric("law_grid_err", eg.first); metric("law_grid_bit_equal_frac", eg.second);
    check(en.first <= TOL_LAW && eg.first <= TOL_LAW, "valve law vs pentode.currents / currents_grid");
}

static void K_from(const Vec& k, int ni, double (&K)[4][3]) {
    for (int r = 0; r < 4; ++r) for (int c = 0; c < 3; ++c) K[r][c] = c < ni ? k[ni * r + c] : 0.0;
}

static void test_solvers(const Vectors& V) {
    double worst = 0.0;
    long it_mismatch = 0, rows = 0, bit_eq = 0;
    for (int which = 2; which <= 3; ++which) {
        const std::string tag = which == 2 ? "solve2" : "solve3";
        double K[4][3];
        K_from(need(V, tag + ".K"), which, K);
        const Vec& in = need(V, tag + ".in");
        const Vec& out = need(V, tag + ".out");
        const size_t n = in.size() / 10;
        for (size_t k = 0; k < n; ++k) {
            const double* r = &in[10 * k];
            const double p[4] = {r[0], r[1], r[2], r[3]}, i0[3] = {r[4], r[5], r[6]};
            const Newton nw{int(r[7]), r[8], r[9]};
            const Solution s = which == 2 ? solve(p, K, i0[0], i0[1], DUAL_CONTROL, nw)
                                          : solve_grid(p, K, i0, DUAL_CONTROL, grid(), nw);
            const double* o = &out[10 * k];
            double e = 0.0;
            bool eq = true;
            for (int q = 0; q < 4; ++q) { e = std::max(e, std::fabs(s.v[q] - o[q])); eq = eq && same_bits(s.v[q], o[q]); }
            for (int q = 0; q < 3; ++q) { e = std::max(e, std::fabs(s.i[q] - o[4 + q]) / 1e-3); eq = eq && same_bits(s.i[q], o[4 + q]); }
            if (std::isnan(e)) e = INFINITY;
            worst = std::max(worst, e);
            it_mismatch += (s.it != int(o[7])) || (s.ok != (o[8] != 0.0));
            bit_eq += eq;
            ++rows;
        }
    }
    {   // the restart contract
        const Vec& in = need(V, "restarts.in");
        const Vec& out = need(V, "restarts.out");
        double K[4][3];
        K_from(need(V, "solve3.K"), 3, K);
        const size_t n = in.size() / 16;
        long recovered = 0;
        for (size_t k = 0; k < n; ++k) {
            const double* r = &in[16 * k];
            const double p[4] = {r[0], r[1], r[2], r[3]};
            const double st[3][3] = {{r[4], r[5], r[6]}, {r[7], r[8], r[9]}, {r[10], r[11], r[12]}};
            const Newton nw{int(r[13]), r[14], r[15]};
            const Solution s = solve_grid_restarts(p, K, st, DUAL_CONTROL, grid(), nw);
            const double* o = &out[11 * k];
            double e = 0.0;
            bool eq = true;
            for (int q = 0; q < 4; ++q) { e = std::max(e, std::fabs(s.v[q] - o[q])); eq = eq && same_bits(s.v[q], o[q]); }
            for (int q = 0; q < 3; ++q) { e = std::max(e, std::fabs(s.i[q] - o[4 + q]) / 1e-3); eq = eq && same_bits(s.i[q], o[4 + q]); }
            if (std::isnan(e)) e = INFINITY;
            worst = std::max(worst, e);
            it_mismatch += (s.it != int(o[7])) || (s.ok != (o[8] != 0.0)) || (s.attempts != int(o[10]));
            recovered += s.ok && s.attempts > 1;
            check(s.it <= 3 * nw.max_it, "restarts: iterations bounded by 3 x max_it");
            bit_eq += eq;
            ++rows;
        }
        metric("restart_rows_recovered", double(recovered));
    check(recovered >= 4, "restarts: some rows converge only on a later start");
    }
    metric("solve_rows", double(rows));
    metric("solve_err", worst);
    metric("solve_bit_equal_frac", double(bit_eq) / double(rows));
    metric("solve_it_mismatch", double(it_mismatch));
    check(worst <= TOL_SOLVE && it_mismatch == 0, "solvers vs solver.solve / solve_grid / solve_grid_restarts");
}

static void test_knob(const Vectors& V) {
    const Vec& k = need(V, "knob.in"); const Vec& b = need(V, "knob.bias");
    double e = 0.0;
    for (size_t i = 0; i < k.size(); ++i) e = std::max(e, std::fabs(knob_bias(k[i]) / b[i] - 1.0));
    const Vec& bi = need(V, "knob.bias_in"); const Vec& kn = need(V, "knob.knob");
    for (size_t i = 0; i < bi.size(); ++i) e = std::max(e, std::fabs(bias_knob(bi[i]) - kn[i]) / 11.0);
    metric("knob_err", e);
    check(e <= 1e-14, "knob_bias / bias_knob");
    check(std::isnan(knob_bias(0.5)) && std::isnan(bias_knob(0.7)), "knob law outside its range");
}

// ---- packs ---------------------------------------------------------------------------------------------------
static Pack pinned_pack(const Vectors& V, const std::string& name) {
    const Vec& cfg = need(V, name + ".cfg");
    Pack p{};
    p.fs = cfg[0] * cfg[1];
    p.mode = Mode(int(cfg[2]));
    p.grid = cfg[3] != 0.0;
    p.g = grid(cfg[3]);
    p.bias_ma = cfg[4];
    p.nu = int(cfg[5]); p.ni = int(cfg[6]);
    p.supported = cfg[7] != 0.0;
    p.rk = need(V, name + ".rk")[0];
    auto fill = [&](const char* k, double* dst, int R, int C, int rc, int cc) {
        const Vec& v = need(V, name + "." + k);
        for (int r = 0; r < R; ++r) for (int c = 0; c < C; ++c) dst[r * C + c] = (r < rc && c < cc) ? v[r * cc + c] : 0.0;
    };
    fill("A", &p.A[0][0], NX, NX, NX, NX);
    fill("B", &p.B[0][0], NX, NU_MAX, NX, p.nu);
    fill("C", &p.C[0][0], NX, NI_MAX, NX, p.ni);
    fill("D", &p.D[0][0], NOUT, NX, NOUT, NX);
    fill("E", &p.E[0][0], NOUT, NU_MAX, NOUT, p.nu);
    fill("F", &p.F[0][0], NOUT, NI_MAX, NOUT, p.ni);
    fill("G", &p.G[0][0], NV, NX, NV, NX);
    fill("H", &p.H[0][0], NV, NU_MAX, NV, p.nu);
    fill("K", &p.K[0][0], NV, NI_MAX, NV, p.ni);
    const Vec& op = need(V, name + ".op");
    for (int q = 0; q < 4; ++q) p.op.v[q] = op[q];
    for (int q = 0; q < 3; ++q) p.op.i[q] = op[4 + q];
    p.op.ik = op[7];
    const Vec& nd = need(V, name + ".nodes");
    for (int q = 0; q < NNODES; ++q) p.op.nodes[q] = nd[q];
    const Vec& rest = need(V, name + ".rest");
    size_t j = 0;
    for (int q = 0; q < NX; ++q) p.x_dc[q] = rest[j++];
    for (int q = 0; q < NI_MAX; ++q) p.i_dc[q] = q < p.ni ? rest[j++] : 0.0;
    for (int q = 0; q < NV; ++q) p.p_dc[q] = rest[j++];
    for (int q = 0; q < NOUT; ++q) p.y_dc[q] = rest[j++];
    return p;
}
static Settings settings_of(Mode m, double hardness, double bias, Lpf lpf = Lpf::off, const FirDesign& fir = DEFAULT_FIR) {
    Settings s;
    s.mode = m; s.grid_hardness = hardness; s.bias_ma = bias; s.lpf = lpf; s.experimental = true; s.fir = fir;
    return s;
}

static double rel_block(const double* a, const double* b, size_t n) {
    const double m = maxabs(b, n);
    return m > 0 ? maxdiff(a, b, n) / m : maxdiff(a, b, n);
}

static void test_packs(const Vectors& V) {
    double worst = 0.0, worst_rk = 0.0, worst_op = 0.0;
    long n = 0;
    for (const auto& kv : V) {
        const std::string& k = kv.first;
        if (k.rfind("pack.", 0) != 0 || k.size() < 4 || k.compare(k.size() - 4, 4, ".cfg") != 0) continue;
        const std::string name = k.substr(0, k.size() - 4);
        const Vec& cfg = kv.second;
        const Pack ref = pinned_pack(V, name);
        Pack got;
        const Status st = build_pack(got, cfg[0] * cfg[1], settings_of(Mode(int(cfg[2])), cfg[3], cfg[4]));
        check(st == Status::ok, name + " builds (" + to_string(st) + ")");
        if (st != Status::ok) continue;
        ++n;
        worst_rk = std::max(worst_rk, std::fabs(got.rk / ref.rk - 1.0));
        const double* gm[] = {&got.A[0][0], &got.B[0][0], &got.C[0][0], &got.D[0][0], &got.E[0][0], &got.F[0][0],
                              &got.G[0][0], &got.H[0][0], &got.K[0][0]};
        const double* rm[] = {&ref.A[0][0], &ref.B[0][0], &ref.C[0][0], &ref.D[0][0], &ref.E[0][0], &ref.F[0][0],
                              &ref.G[0][0], &ref.H[0][0], &ref.K[0][0]};
        const size_t sz[] = {NX * NX, NX * NU_MAX, NX * NI_MAX, NOUT * NX, NOUT * NU_MAX, NOUT * NI_MAX, NV * NX,
                             NV * NU_MAX, NV * NI_MAX};
        for (int m = 0; m < 9; ++m) worst = std::max(worst, rel_block(gm[m], rm[m], sz[m]));
        // the operating point: voltages / node voltages relative to their max, currents relative to Ik
        double e = std::max(rel_block(got.op.v, ref.op.v, 4), rel_block(got.op.nodes, ref.op.nodes, NNODES));
        for (int q = 0; q < 3; ++q) e = std::max(e, std::fabs(got.op.i[q] - ref.op.i[q]) / ref.op.ik);
        e = std::max(e, std::fabs(got.op.ik / ref.op.ik - 1.0));
        e = std::max(e, rel_block(got.x_dc, ref.x_dc, NX));
        e = std::max(e, std::max(rel_block(got.p_dc, ref.p_dc, NV), std::fabs(got.y_dc[0] - ref.y_dc[0]) / 200.0));
        worst_op = std::max(worst_op, e);
        check(got.supported == ref.supported, name + " supported flag");
        check(got.nu == ref.nu && got.ni == ref.ni, name + " dimensions");
        check(std::fabs(got.op.ik / (cfg[4] * 1e-3) - 1.0) < 1e-10, name + " draws its target current");
    }
    metric("packs", double(n));
    metric("coef_err", worst); metric("rk_err", worst_rk); metric("op_err", worst_op);
    check(n >= 24 && worst <= TOL_COEF && worst_rk <= TOL_RK && worst_op <= TOL_COEF, "packs vs circuit.build / stage");
}

static void test_lpf_and_k140(const Vectors& V) {
    {
        const Vec& x = need(V, "lpf.in"); const Vec& y = need(V, "lpf.out");
        PostFilter pf;
        check(pf.prepare(8 * 48000.0, Lpf::off), "PostFilter prepares");
        Vec g(x.size());
        for (size_t i = 0; i < x.size(); ++i) {
            if (i == 100) pf.set_position(Lpf::k4);
            if (i == 3000) pf.set_position(Lpf::k7);
            g[i] = pf.tick(x[i]);
        }
        const double e = rel_block(g.data(), y.data(), y.size());
        metric("lpf_err", e);
        check(e <= TOL_RS, "PostFilter vs lpf.PostFilter (fade, mid-fade change)");
        check(pf.fade_samples() == 5760, "XFADE_S at 8 x 48 kHz is 5760 samples");
    }
    double et = 0.0, er = 0.0;
    for (double fs : {48000.0, 44100.0}) {
        dk6as6::Stage::Resampler r;
        check(r.prepare(8, fs, dkcore::K140_FIR), "k140 prepares");
        const std::string n0 = "taps.k140_" + std::to_string(dkcore::first_fir_taps(fs, dkcore::K140_FIR));
        et = std::max(et, maxdiff(r.taps(0), need(V, n0).data(), need(V, n0).size()));
        et = std::max(et, maxdiff(r.taps(1), need(V, "taps.k140_47").data(), 47));
        for (int os : {8, 16}) {
            const std::string p = "rsk140_" + std::to_string(long(fs)) + "_" + std::to_string(os);
            dk6as6::Stage::Resampler u, d;
            u.prepare(os, fs, dkcore::K140_FIR); d.prepare(os, fs, dkcore::K140_FIR);
            const Vec& ui = need(V, p + ".up.in"); const Vec& uo = need(V, p + ".up.out");
            Vec go(uo.size());
            for (size_t i = 0; i < ui.size(); ++i) u.up(ui[i], &go[i * os]);
            er = std::max(er, rel_block(go.data(), uo.data(), uo.size()));
            const Vec& di = need(V, p + ".down.in"); const Vec& dout = need(V, p + ".down.out");
            Vec gd(dout.size());
            for (size_t i = 0; i < dout.size(); ++i) gd[i] = d.down(&di[i * os]);
            er = std::max(er, rel_block(gd.data(), dout.data(), dout.size()));
        }
    }
    metric("k140_taps_err", et); metric("k140_rs_err", er);
    check(et <= 1e-14 && er <= TOL_RS, "k140 taps and resampler vs dk.resample (fir=)");
    // the shipped design through the 6AS6 stage's (larger) resampler instantiation equals dk_core.h's Resampler
    dkcore::Resampler a; dk6as6::Stage::Resampler b;
    a.prepare(8, 44100.0); b.prepare(8, 44100.0, dkcore::SHIPPED_FIR);
    bool same = a.latency_samples() == b.latency_samples();
    double ua[8], ub[8];
    for (int i = 0; i < 300 && same; ++i) {
        const double x = std::sin(0.01 * i) + (i == 7 ? 1.0 : 0.0);
        a.up(x, ua); b.up(x, ub);
        for (int j = 0; j < 8; ++j) same = same && same_bits(ua[j], ub[j]);
        same = same && same_bits(a.down(ua), b.down(ub));
    }
    check(same, "shipped design in the 6AS6 instantiation == dkcore::Resampler bit for bit");
    check(!dkcore::Resampler().prepare(8, 48000.0, dkcore::K140_FIR), "k140 refused by the 12AX7 resampler's capacity");
}

// ---- the stage -----------------------------------------------------------------------------------------------
struct CaseCfg {
    double fs; int os; Mode mode; double h, bias, drive, output; Lpf lpf; long switch_at; Lpf to; Newton nw;
    double fir; bool supported;
};
static CaseCfg parse_cfg(const Vec& c) {
    return CaseCfg{c[0], int(c[1]), Mode(int(c[2])), c[3], c[4], c[5], c[6], Lpf(int(c[7])), long(c[8]), Lpf(int(c[9])),
                   Newton{int(c[10]), c[11], c[12]}, c[13], c[14] != 0.0};
}
static Status setup(dk6as6::Stage& s, const Vectors& V, const CaseCfg& k, bool pinned) {
    Status st;
    if (pinned) {
        Pack p = pinned_pack(V, pack_name(MODE_NAMES[int(k.mode)], k.h, k.bias, k.os, k.fs));
        p.newton = k.nw;
        st = s.prepare_pack(k.fs, k.os, p, k.lpf, fir_of(k.fir));
    } else {
        Settings set = settings_of(k.mode, k.h, k.bias, k.lpf, fir_of(k.fir));
        st = s.prepare(k.fs, Quality{k.os, 0.0}, set);
        if (st == Status::ok && !(k.nw.max_it == 50 && k.nw.tol_v == 1e-6 && k.nw.tol_f == 1e-6)) {   // runtime only
            Pack p = s.pack();
            p.newton = k.nw;
            st = s.prepare_pack(k.fs, k.os, p, k.lpf, fir_of(k.fir));
        }
    }
    s.set_drive(k.drive);
    s.set_output(k.output);
    s.set_lpf(k.lpf);
    s.reset();
    return st;
}
// process x in blocks of `block` (0: one call), switching the post-filter at k.switch_at; returns allocations
static long run_blocks(dk6as6::Stage& s, const CaseCfg& k, const Vec& x, Vec& y, size_t block) {
    y.assign(x.size(), 0.0);
    long allocs = 0;
    size_t i = 0;
    while (i < x.size()) {
        size_t n = block ? std::min(block, x.size() - i) : x.size() - i;
        if (k.switch_at >= 0 && i < size_t(k.switch_at)) n = std::min(n, size_t(k.switch_at) - i);
        if (k.switch_at >= 0 && i == size_t(k.switch_at)) s.set_lpf(k.to);
        CountAllocs c;
        s.process(&x[i], &y[i], n);
        allocs += c.n();
        i += n;
    }
    return allocs;
}

static void test_cases(const Vectors& V) {
    double worst_p = 0.0, worst_b = 0.0, worst_it = 0.0;
    long cases = 0, allocs = 0, block_diff = 0;
    std::string worst_b_name, worst_p_name;
    for (const auto& kv : V) {
        const std::string& key = kv.first;
        if (key.rfind("case.", 0) != 0 || key.compare(key.size() - 4, 4, ".cfg") != 0) continue;
        const std::string name = key.substr(0, key.size() - 4);
        const CaseCfg k = parse_cfg(kv.second);
        const Vec& x = need(V, name + ".in");
        const Vec& ref = need(V, name + ".out");
        const Vec& rs = need(V, name + ".stats");
        ++cases;
        for (int pinned = 1; pinned >= 0; --pinned) {
            dk6as6::Stage s;
            const Status st = setup(s, V, k, pinned);
            check(st == Status::ok, name + " prepares (" + to_string(st) + ")");
            if (st != Status::ok) continue;
            Vec y, y2, y3;
            allocs += run_blocks(s, k, x, y, 0);
            const Stats a = s.stats();
            const double e = maxdiff(y.data(), ref.data(), ref.size());
            if (pinned) { if (e > worst_p) { worst_p = e; worst_p_name = name; } }
            else if (e > worst_b) { worst_b = e; worst_b_name = name; }
            check(e <= (pinned ? TOL_PINNED_ABS : TOL_BUILT_ABS), name + (pinned ? " pinned" : " built") + " output, err " + fmt(e));
            check(a.samples == std::uint64_t(rs[0]), name + " sample count");
            const double fi = std::fabs(double(a.iterations) - rs[1]) / rs[1];
            worst_it = std::max(worst_it, fi);
            check(fi <= TOL_ITER_FRAC, name + " iteration sum");
            check(std::fabs(double(a.nonconverged) - rs[2]) <= TOL_ITER_FRAC * rs[0], name + " nonconverged");
            check(std::fabs(double(a.restarted) - rs[4]) <= TOL_ITER_FRAC * rs[0], name + " restarted");
            check(std::fabs(double(a.out_of_law) - rs[5]) <= TOL_ITER_FRAC * rs[0], name + " out of law");
            check(a.max_iterations <= 3 * k.nw.max_it, name + " iterations bounded by 3 x max_it");
            if (pinned) {
                const Vec& hist = need(V, name + ".hist");
                double l1 = 0.0;
                for (int b = 0; b < Stats::HIST; ++b)
                    l1 += std::fabs(double(a.histogram[b]) - (size_t(b) < hist.size() ? hist[b] : 0.0));
                check(l1 / rs[0] <= TOL_ITER_FRAC, name + " iteration histogram");
            }
            // block-size invariance: bit for bit
            setup(s, V, k, pinned); allocs += run_blocks(s, k, x, y2, 1);
            setup(s, V, k, pinned); allocs += run_blocks(s, k, x, y3, 37);
            for (size_t i = 0; i < y.size(); ++i) block_diff += !same_bits(y[i], y2[i]) || !same_bits(y[i], y3[i]);
        }
    }
    metric("cases", double(cases));
    metric("pinned_err", worst_p); metric("built_err", worst_b); metric("iter_frac_err", worst_it);
    metric("process_allocations", double(allocs));
    metric("block_size_diff_samples", double(block_diff));
    std::fprintf(stderr, "worst pinned %s %.3g, worst built %s %.3g\n", worst_p_name.c_str(), worst_p,
                 worst_b_name.c_str(), worst_b);
    check(cases >= 40, "case count");
    check(!ALLOC_HOOKS || allocs == 0, "no allocation in process()");
    check(block_diff == 0, "block-size invariance (1 / 37 / whole), bit for bit");
}

static void test_internal(const Vectors& V) {
    dk6as6::Stage s;
    Pack p = pinned_pack(V, "pack.T.g.0.45.8.48000");
    check(s.prepare_pack(48000.0, 8, p, Lpf::k7) == Status::ok, "internal: prepare_pack");
    const Vec& in = need(V, "internal.in");
    const size_t n = in.size();
    Vec out(n), vgk(n), vg2k(n), vak(n), ig1(n), ipk(n);
    std::vector<std::uint16_t> it(n);
    Record r; r.vgk = vgk.data(); r.vg2k = vg2k.data(); r.vak = vak.data(); r.ig1 = ig1.data(); r.ipk = ipk.data();
    r.it = it.data();
    long allocs;
    { CountAllocs c; s.process_internal(in.data(), out.data(), n, &r); allocs = c.n(); }
    double e = maxdiff(out.data(), need(V, "internal.out").data(), n);
    e = std::max(e, maxdiff(vgk.data(), need(V, "internal.vgk").data(), n));
    e = std::max(e, maxdiff(vg2k.data(), need(V, "internal.vg2k").data(), n));
    e = std::max(e, maxdiff(vak.data(), need(V, "internal.vak").data(), n));
    const double ei = std::max(maxdiff(ig1.data(), need(V, "internal.ig1").data(), n),
                               maxdiff(ipk.data(), need(V, "internal.ipk").data(), n));
    long itd = 0;
    const Vec& ri = need(V, "internal.iters");
    for (size_t i = 0; i < n; ++i) itd += it[i] != std::uint16_t(ri[i]);
    metric("internal_err", e); metric("internal_current_err", ei); metric("internal_iter_mismatch", double(itd));
    check(e <= TOL_PINNED_ABS && ei <= 1e-12 && itd == 0 && (!ALLOC_HOOKS || allocs == 0), "process_internal vs Python");
}

// #7 / #112: forced failures keep a finite best iterate, are counted, and the bound holds; NaN input
static void test_fallbacks() {
    dk6as6::Stage s;
    Settings set = settings_of(Mode::T, 1.0, 0.6);
    check(s.prepare(48000.0, STANDARD, set) == Status::ok, "fallback: prepare");
    Pack p = s.pack();
    p.newton = Newton{1, 0.0, 0.0};
    s.prepare_pack(48000.0, 8, p);
    Vec x(256), y(256);
    for (size_t i = 0; i < x.size(); ++i) x[i] = 8.0 * std::sin(2 * PI * 1000.0 * double(i) / 48000.0);
    x[10] = NAN; x[11] = INFINITY;
    long allocs;
    { CountAllocs c; s.process(x.data(), y.data(), x.size()); allocs = c.n(); }
    bool finite = true;
    for (double v : y) finite = finite && std::isfinite(v);
    const Stats& a = s.stats();
    check(finite, "fallback: finite output with every sample failing");
    check(a.nonconverged == a.samples && a.restarted == a.samples && a.nonfinite_inputs == 2, "fallback: counted");
    check(a.max_iterations <= 3, "fallback: 3 x max_it");
    check(!ALLOC_HOOKS || allocs == 0, "fallback: no allocation");
    metric("fallback_nonconverged", double(a.nonconverged));
    // the warm start never restarted at the defaults on a validated signal (PR #128: worst 15 iterations)
    set = settings_of(Mode::P1, 1.0, 0.45);
    s.prepare(48000.0, STANDARD, set);
    s.process(x.data(), y.data(), x.size());
    check(s.stats().nonconverged == 0 && s.stats().max_iterations <= 15, "defaults converge on 8 V");
}

static void test_settings() {
    Pack p;
    Settings s;
    check(build_pack(p, 384000.0, s) == Status::ok && p.supported, "default setting builds, supported");
    s.mode = Mode::P2;
    check(build_pack(p, 384000.0, s) == Status::unsupported_setting, "P2 refused without experimental");
    s.experimental = true;
    check(build_pack(p, 384000.0, s) == Status::ok && !p.supported, "P2 runs experimental, flagged");
    s = Settings{}; s.mode = Mode::T; s.bias_ma = 0.55;
    check(build_pack(p, 384000.0, s) == Status::unsupported_setting, "T 0.55 mA with grid current refused");
    s.grid_hardness = 0.0; s.bias_ma = 0.35;
    check(build_pack(p, 384000.0, s) == Status::unsupported_setting, "T 0.35 mA without grid current refused");
    s = Settings{}; s.bias_ma = 50.0; s.experimental = true;
    check(build_pack(p, 384000.0, s) == Status::bias_out_of_range, "50 mA is outside what Rk can set");
    s = Settings{}; s.bias_ma = -1.0;
    check(build_pack(p, 384000.0, s) == Status::bad_argument, "negative bias");
    dk6as6::Stage st;
    check(st.prepare(48000.0, dkcore::ECO) == Status::bad_argument, "no eco (a pre-warped quality is refused)");
    check(st.prepare(0.0) == Status::bad_argument, "fs 0 refused");
    // the bias meter: Vk = Rk Ik at the operating point
    check(st.prepare(48000.0) == Status::ok, "default prepare");
    const Pack& q = st.pack();
    check(std::fabs(q.op.nodes[CATH] / (q.rk * q.op.ik) - 1.0) < 1e-9, "Vk = Rk Ik");
    metric("default_rk", q.rk); metric("default_vk", q.op.nodes[CATH]); metric("default_va", q.op.nodes[ANODE]);
}

static void test_rates_and_silence() {
    long bad = 0;
    double silence = 0.0;
    for (double fs : {44100.0, 48000.0, 88200.0, 96000.0, 176400.0, 192000.0})
        for (Quality q : {STANDARD, HQ}) {
            dk6as6::Stage s;
            if (s.prepare(fs, q) != Status::ok) { ++bad; continue; }
            Vec x(512), y(512);
            for (size_t i = 0; i < x.size(); ++i) x[i] = 4.0 * std::sin(2 * PI * 997.0 * double(i) / fs);
            s.process(x.data(), y.data(), x.size());
            for (double v : y) bad += !std::isfinite(v);
            bad += s.stats().nonconverged != 0;
            s.reset();
            Vec z(2048, 0.0), w(2048);
            s.process(z.data(), w.data(), z.size());
            for (double v : w) silence = std::max(silence, std::fabs(v - s.pack().y_dc[0]));
        }
    metric("rates_bad", double(bad)); metric("silence_max_v", silence);
    check(bad == 0, "every host rate x quality prepares and runs");
    check(silence < 1e-12, "silence stays at the operating point's out node (deviation form)");
}

static void test_denormals_and_float() {
#if defined(__aarch64__) || defined(DKCORE_X86_SSE)
    dk6as6::Stage s;
    s.prepare(48000.0);
    double x[64] = {}, y[64];
#if defined(__aarch64__)
    std::uint64_t before, after;
    __asm__ __volatile__("mrs %0, fpcr" : "=r"(before));
    s.process(x, y, 64);
    __asm__ __volatile__("mrs %0, fpcr" : "=r"(after));
#else
    const unsigned before = _mm_getcsr();
    s.process(x, y, 64);
    const unsigned after = _mm_getcsr();
#endif
    check(before == after, "process() restores the caller's FP mode");
#endif
    dk6as6::Stage a, b;
    a.prepare(48000.0); b.prepare(48000.0);
    Vec xd(480), yd(480);
    std::vector<float> xf(480), yf(480);
    for (size_t i = 0; i < 480; ++i) { xf[i] = float(2.0 * std::sin(0.13 * double(i))); xd[i] = xf[i]; }
    a.process(xd.data(), yd.data(), 480);
    b.process(xf.data(), yf.data(), 480);
    double e = 0.0;
    for (size_t i = 0; i < 480; ++i) e = std::max(e, std::fabs(double(yf[i]) - double(float(yd[i]))));
    check(e == 0.0, "float I/O = the double path rounded to float");
}

// ---- CLI modes ---------------------------------------------------------------------------------------------------
static Vec read_stdin() {
    Vec v;
    double buf[4096];
    size_t n;
    while ((n = std::fread(buf, sizeof(double), 4096, stdin)) > 0) v.insert(v.end(), buf, buf + n);
    return v;
}
static void write_stdout(const double* p, size_t n) { std::fwrite(p, sizeof(double), n, stdout); }
static Mode parse_mode(const std::string& m) { return m == "T" ? Mode::T : m == "P2" ? Mode::P2 : Mode::P1; }
static Lpf parse_lpf(const std::string& l) { return l == "4k" ? Lpf::k4 : l == "7k" ? Lpf::k7 : Lpf::off; }

static void json_mat(std::ostringstream& o, const char* k, const double* a, int C, int rc, int cc) {
    o << ", \"" << k << "\": [";
    bool first = true;
    for (int r = 0; r < rc; ++r) for (int c = 0; c < cc; ++c) { o << (first ? "" : ", ") << a[r * C + c]; first = false; }
    o << "]";
}

static int cli(int argc, char** argv) {
    if (argc < 11) { std::fprintf(stderr, "usage: cli FS OS MODE BIAS HARDNESS LPF DRIVE OUTPUT FIR [rec]\n"); return 2; }
    const double fs = std::strtod(argv[2], nullptr);
    const int os = std::atoi(argv[3]);
    Settings set = settings_of(parse_mode(argv[4]), std::strtod(argv[6], nullptr), std::strtod(argv[5], nullptr),
                               parse_lpf(argv[7]), std::string(argv[10]) == "k140" ? dkcore::K140_FIR : dkcore::SHIPPED_FIR);
    const bool rec = argc > 11 && std::string(argv[11]) == "rec";
    dk6as6::Stage s;
    const Status st = s.prepare(fs, Quality{os, 0.0}, set);
    if (st != Status::ok) { std::fprintf(stderr, "{\"status\": \"%s\"}\n", to_string(st)); return 1; }
    s.set_drive(std::strtod(argv[8], nullptr));
    s.set_output(std::strtod(argv[9], nullptr));
    s.reset();
    const Vec x = read_stdin();
    Vec y(x.size()), vgk(rec ? x.size() * os : 0), vg2k(rec ? x.size() * os : 0);
    Record r; r.vgk = rec ? vgk.data() : nullptr; r.vg2k = rec ? vg2k.data() : nullptr;
    // in chunks of 256, as the plugin engine runs
    for (size_t i = 0; i < x.size(); i += 256) {
        const size_t n = std::min<size_t>(256, x.size() - i);
        Record ri = r;
        if (rec) { ri.vgk = &vgk[i * os]; ri.vg2k = &vg2k[i * os]; }
        s.process(&x[i], &y[i], n, rec ? &ri : nullptr);
    }
    write_stdout(y.data(), y.size());
    if (rec) { write_stdout(vgk.data(), vgk.size()); write_stdout(vg2k.data(), vg2k.size()); }
    const Pack& p = s.pack();
    const Stats& a = s.stats();
    std::ostringstream o;
    o.precision(17);
    o << "{\"status\": \"ok\", \"fs\": " << p.fs << ", \"rk\": " << p.rk << ", \"nu\": " << p.nu << ", \"ni\": " << p.ni
      << ", \"supported\": " << (p.supported ? "true" : "false")
      << ", \"latency_samples\": " << s.latency_samples() << ", \"up_delay_internal\": " << s.up_delay_internal()
      << ", \"macs\": " << s.resampler().macs();
    json_mat(o, "A", &p.A[0][0], NX, NX, NX); json_mat(o, "B", &p.B[0][0], NU_MAX, NX, p.nu);
    json_mat(o, "C", &p.C[0][0], NI_MAX, NX, p.ni); json_mat(o, "D", &p.D[0][0], NX, NOUT, NX);
    json_mat(o, "E", &p.E[0][0], NU_MAX, NOUT, p.nu); json_mat(o, "F", &p.F[0][0], NI_MAX, NOUT, p.ni);
    json_mat(o, "G", &p.G[0][0], NX, NV, NX); json_mat(o, "H", &p.H[0][0], NU_MAX, NV, p.nu);
    json_mat(o, "K", &p.K[0][0], NI_MAX, NV, p.ni);
    json_mat(o, "op_v", p.op.v, 4, 1, 4); json_mat(o, "op_i", p.op.i, 3, 1, 3);
    json_mat(o, "nodes", p.op.nodes, NNODES, 1, NNODES);
    o << ", \"ik\": " << p.op.ik << ", \"stats\": {\"samples\": " << a.samples << ", \"iterations\": " << a.iterations
      << ", \"nonconverged\": " << a.nonconverged << ", \"restarted\": " << a.restarted << ", \"out_of_law\": "
      << a.out_of_law << ", \"nonfinite_inputs\": " << a.nonfinite_inputs << ", \"max\": " << a.max_iterations
      << ", \"histogram\": {";
    bool first = true;
    for (int b = 0; b < Stats::HIST; ++b)
        if (a.histogram[b]) { o << (first ? "" : ", ") << "\"" << b << "\": " << a.histogram[b]; first = false; }
    o << "}}}";
    std::fprintf(stderr, "%s\n", o.str().c_str());
    return 0;
}

// rows -> results, for the live bit-exact comparison (cpp/tests/test_cpp_core_6as6.py)
static int pipe_mode(const std::string& mode) {
    const Vec in = read_stdin();
    Vec out;
    const GridParams g = grid();
    if (mode == "law") {                    // rows: v (4) -> currents (2 + 8), currents_only (2), grid (3 + 12)
        for (size_t k = 0; k + 4 <= in.size(); k += 4) {
            const double v[4] = {in[k], in[k + 1], in[k + 2], in[k + 3]};
            Law a, c, b;
            currents<true>(v, DUAL_CONTROL, a); currents<false>(v, DUAL_CONTROL, c);
            currents_grid<true>(v, DUAL_CONTROL, g, b);
            out.insert(out.end(), {a.i[0], a.i[1]});
            for (int q = 0; q < 2; ++q) for (int j = 0; j < 4; ++j) out.push_back(a.J[q][j]);
            out.insert(out.end(), {c.i[0], c.i[1], b.i[0], b.i[1], b.i[2]});
            for (int q = 0; q < 3; ++q) for (int j = 0; j < 4; ++j) out.push_back(b.J[q][j]);
            out.push_back(b.raised ? 1.0 : 0.0);
        }
    } else if (mode == "solve2" || mode == "solve3") {   // rows: p (4), K (12), start (3), max_it, tol_v, tol_f
        for (size_t k = 0; k + 22 <= in.size(); k += 22) {
            const double* r = &in[k];
            const double p[4] = {r[0], r[1], r[2], r[3]};
            double K[4][3];
            for (int a = 0; a < 4; ++a) for (int b = 0; b < 3; ++b) K[a][b] = r[4 + 3 * a + b];
            const double i0[3] = {r[16], r[17], r[18]};
            const Newton nw{int(r[19]), r[20], r[21]};
            const Solution s = mode == "solve2" ? solve(p, K, i0[0], i0[1], DUAL_CONTROL, nw)
                                                : solve_grid(p, K, i0, DUAL_CONTROL, g, nw);
            out.insert(out.end(), {s.v[0], s.v[1], s.v[2], s.v[3], s.i[0], s.i[1], s.i[2], double(s.it),
                                   s.ok ? 1.0 : 0.0, s.fv});
        }
    } else if (mode == "restarts") {        // rows: p (4), K (12), starts (9), max_it, tol_v, tol_f
        for (size_t k = 0; k + 28 <= in.size(); k += 28) {
            const double* r = &in[k];
            const double p[4] = {r[0], r[1], r[2], r[3]};
            double K[4][3], st[3][3];
            for (int a = 0; a < 4; ++a) for (int b = 0; b < 3; ++b) K[a][b] = r[4 + 3 * a + b];
            for (int a = 0; a < 3; ++a) for (int b = 0; b < 3; ++b) st[a][b] = r[16 + 3 * a + b];
            const Newton nw{int(r[25]), r[26], r[27]};
            const Solution s = solve_grid_restarts(p, K, st, DUAL_CONTROL, g, nw);
            out.insert(out.end(), {s.v[0], s.v[1], s.v[2], s.v[3], s.i[0], s.i[1], s.i[2], double(s.it),
                                   s.ok ? 1.0 : 0.0, s.fv, double(s.attempts)});
        }
    } else {
        return 2;
    }
    write_stdout(out.data(), out.size());
    return 0;
}

static const char* compiler() {
#if defined(__clang__)
    return __VERSION__;
#elif defined(__GNUC__)
    return "gcc " __VERSION__;
#else
    return "unknown";
#endif
}

int main(int argc, char** argv) {
    const std::string a1 = argc > 1 ? argv[1] : "";
    if (a1 == "cli") return cli(argc, argv);
    if (a1 == "law" || a1 == "solve2" || a1 == "solve3" || a1 == "restarts") return pipe_mode(a1);
    const Vectors V = load(argc > 1 ? argv[1] : "vectors_6as6.txt");
    test_params(V);
    test_law(V);
    test_solvers(V);
    test_knob(V);
    test_packs(V);
    test_lpf_and_k140(V);
    test_internal(V);
    test_cases(V);
    test_fallbacks();
    test_settings();
    test_rates_and_silence();
    test_denormals_and_float();
    metric("alloc_hooks", ALLOC_HOOKS ? 1 : 0);
    metric("fma", dkcore::simd::FMA ? 1 : 0);
    std::printf("{%s, \"compiler\": \"%s\", \"failures\": %d}\n", g_json.str().c_str(), compiler(), g_fail);
    return g_fail ? 1 : 0;
}
