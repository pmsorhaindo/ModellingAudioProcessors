/* Tests for cpp/dk_core.h (#19) against the Python DK stage's vectors (cpp/vectors.txt), plus the realtime
 * properties: no allocation in process(), FTZ/DAZ scoping, the lock-free coefficient swap and the #7 fallback.
 *
 *   test_core [vectors.txt]        run every test; prints one JSON line of measured errors; exit 1 on any failure
 *   test_core rates              prepare/process at six host rates, all qualities, mono/stereo and libm/fast
 *   test_core cli OS PREWARP BIAS DRIVE OUTPUT MATH [vgk] [fs=HZ]
 *                                  stdin: float64 base-rate input; stdout: float64 output (then, with "vgk", the
 *                                  internal-rate Vgk); stderr: one JSON line (pack, op point, latency, stats).
 *                                  Used by cpp/tests/test_cpp_core.py to run dk.validate on the core.
 *   test_core solve | triode       stdin rows -> stdout: solve() / the triode currents, for the bit-exact comparison
 *                                  with the live Python functions (cpp/tests/test_cpp_core.py)
 *   test_core swap                 the threaded coefficient-swap test alone (for a -fsanitize=thread build)
 *
 * Allocation check: this file replaces global operator new / delete (every form) and, on glibc, interposes
 * malloc / calloc / realloc / free / memalign (forwarding to glibc's __libc_* entry points). Both count while
 * g_count is set; every process() call in the tests runs with it set and the count must stay 0. Build with
 * -DDKCORE_NO_ALLOC_HOOKS for sanitizer builds (they intercept the allocator themselves).
 * Build: see cpp/README.md (g++ -std=c++17 -O2 -pthread ...). */
#include "dk_core.h"

#include <algorithm>
#include <chrono>
#include <cfloat>
#include <cinttypes>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <limits>
#include <map>
#include <new>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using namespace dkcore;

#include "alloc_hooks.h"   // allocation counting: g_count, CountAllocs, ALLOC_HOOKS

// ---- tolerances (cpp/README.md "Tolerances") -------------------------------------------------------------------
// Set from the worst error measured over four x86-64 builds (gcc 14 -O0, -O2, -O2 -march=x86-64-v3 with FMA
// contraction, -O3 -march=native) and a run with every libm result nudged by +-1 ulp (another libm), with the margin
// in brackets. All are many orders below validate.CRIT (op 1e-3 relative, 0.1 dB).
static const double TOL_TAPS = 1e-14;        // absolute; taps <= 0.5; measured 1.1e-16 (1 ulp)            [90x]
static const double TOL_COEF = 1e-11;        // max |dM| / max |M| per matrix, op point, nodes, x0; 9.4e-13 [10x]
static const double TOL_TRIODE = 1e-13;      // per output column / its max |value|; 0 (glibc), 2.8e-16 jittered
static const double TOL_RS = 1e-13;          // resampler alone, / max |output|; 1.1e-15                    [90x]
static const double TOL_PINNED_ABS = 1e-9;   // V, stage on the Python pack (per-sample arithmetic); 2.7e-11 [37x]
static const double TOL_BUILT_ABS = 1e-5;    // V, stage on the C++-built pack (matrix rounding, README); 7.7e-7 [13x]
static const double TOL_SOLVE = 1e-12;      // solver alone: |dVgk|, |dVak|/max(1,|Vak|), currents rel; 0 (FMA 3.2e-15)
static const double TOL_ITER_FRAC = 1e-3;    // Newton iteration sums (borderline samples); measured: identical

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
static std::map<std::string, Vec> load(const std::string& path) {
    std::map<std::string, Vec> m;
    std::ifstream f(path);
    if (!f) { std::fprintf(stderr, "cannot open %s\n", path.c_str()); std::exit(2); }
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream is(line);
        std::string name; size_t n;
        is >> name >> n;
        Vec v(n);
        for (size_t i = 0; i < n; ++i) { std::string t; is >> t; v[i] = std::strtod(t.c_str(), nullptr); }
        m[name] = v;
    }
    return m;
}
static const Vec& need(const std::map<std::string, Vec>& m, const std::string& k) {
    auto it = m.find(k);
    if (it == m.end()) { std::fprintf(stderr, "missing vector %s\n", k.c_str()); std::exit(2); }
    return it->second;
}
static double maxabs(const double* a, size_t n) { double m = 0; for (size_t i = 0; i < n; ++i) m = std::max(m, std::fabs(a[i])); return m; }
static double maxdiff(const double* a, const double* b, size_t n) {
    double m = 0;
    for (size_t i = 0; i < n; ++i) { const double d = std::fabs(a[i] - b[i]); if (!(d <= m)) m = d; }   // NaN -> NaN
    return m;
}
static double relerr(const double* got, const Vec& ref) {
    const double s = maxabs(ref.data(), ref.size());
    return maxdiff(got, ref.data(), ref.size()) / (s > 0 ? s : 1.0);
}
static bool allfinite(const double* a, size_t n) { for (size_t i = 0; i < n; ++i) if (!std::isfinite(a[i])) return false; return true; }

// ---- tests ---------------------------------------------------------------------------------------------------
static void test_taps(const std::map<std::string, Vec>& V) {
    for (int nt : {FIR_FIRST_TAPS, FIR_LATER_TAPS, FIR_FIRST_44100_TAPS}) {
        double h[FIR_MAX_TAPS];
        firwin_halfband(nt, FIR_BETA, h);
        const Vec r = need(V, "taps.fir" + std::to_string(nt));
        const double e = maxdiff(h, r.data(), r.size());
        metric("taps_fir" + std::to_string(nt) + "_abs", e);
        check(r.size() == size_t(nt) && e <= TOL_TAPS, "taps fir" + std::to_string(nt));
    }
}

static void test_coeffs(const std::map<std::string, Vec>& V) {
    double worst = 0;
    for (const char* name : {"standard", "hq", "eco", "standard_b15"}) {
        const std::string p = std::string("coef.") + name + ".";
        const Vec cfg = need(V, p + "cfg");
        Coeffs c;
        const BuildStatus st = build_coeffs(c, 48000.0 * cfg[0], cfg[2], cfg[1]);
        check(st == BuildStatus::ok, p + "build");
        const std::pair<const char*, const double*> mats[] = {
            {"A", &c.A[0][0]}, {"B", &c.B[0][0]}, {"C", &c.C[0][0]}, {"D", &c.D[0][0]}, {"E", &c.E[0][0]},
            {"F", &c.F[0][0]}, {"G", &c.G[0][0]}, {"H", &c.H[0][0]}, {"K", &c.K[0][0]}};
        for (auto& m : mats) {
            const double e = relerr(m.second, need(V, p + m.first));
            worst = std::max(worst, e);
            check(e <= TOL_COEF, p + m.first + " rel " + std::to_string(e));
        }
        const Vec op = need(V, p + "op");
        const double opv[4] = {c.op.vgk, c.op.vak, c.op.ig, c.op.ia};
        for (int i = 0; i < 4; ++i) {
            const double e = std::fabs(opv[i] / op[i] - 1.0);
            worst = std::max(worst, e);
            check(e <= TOL_COEF, p + "op[" + std::to_string(i) + "] rel " + std::to_string(e));
        }
        const double en = relerr(c.op.nodes, need(V, p + "nodes")), ex = relerr(c.x0, need(V, p + "x0"));
        worst = std::max({worst, en, ex});
        check(en <= TOL_COEF && ex <= TOL_COEF, p + "nodes / x0");
    }
    metric("coef_rel", worst);
    // the DC solve fails cleanly, with diagnostics (stage.OperatingPointError)
    Coeffs c;
    check(build_coeffs(c, 384000.0, 1.0, 0.0, TriodeMath::libm, Newton{1, 1e-6, 1e-6}) == BuildStatus::dc_nonconvergence &&
          c.dc_step == 1 && c.dc_iterations == 1 && std::isfinite(c.dc_best_vgk), "DC non-convergence is reported");
    check(build_coeffs(c, 0.0) == BuildStatus::bad_argument && build_coeffs(c, 384000.0, -1.0) == BuildStatus::bad_argument,
          "bad arguments are rejected");
}

static void test_triode(const std::map<std::string, Vec>& V) {
    const Vec in = need(V, "triode.in");
    const size_t n = in.size() / 2;
    const Vec rl = need(V, "triode.libm");
    const Vec ro = need(V, "triode.libm_only");
    const Vec rf = need(V, "triode.fast");
    Vec gl(6 * n), go(2 * n), gf(6 * n);
    for (size_t i = 0; i < n; ++i) {
        Currents c;
        Triode<LibmMath>::currents(in[2 * i], in[2 * i + 1], DZ_12AX7_RSD1, c);
        const double a[6] = {c.ig, c.ia, c.j00, c.j01, c.j10, c.j11};
        std::copy(a, a + 6, &gl[6 * i]);
        Triode<LibmMath>::currents_only(in[2 * i], in[2 * i + 1], DZ_12AX7_RSD1, go[2 * i], go[2 * i + 1]);
        Triode<FastMath>::currents(in[2 * i], in[2 * i + 1], DZ_12AX7_RSD1, c);
        const double b[6] = {c.ig, c.ia, c.j00, c.j01, c.j10, c.j11};
        std::copy(b, b + 6, &gf[6 * i]);
    }
    auto colerr = [&](const Vec& got, const Vec& ref, int w) {
        double worst = 0; long exact = 0;
        for (int k = 0; k < w; ++k) {
            double s = 0, d = 0;
            for (size_t i = 0; i < n; ++i) {
                s = std::max(s, std::fabs(ref[w * i + k]));
                d = std::max(d, std::fabs(got[w * i + k] - ref[w * i + k]));
                exact += got[w * i + k] == ref[w * i + k];
            }
            worst = std::max(worst, s > 0 ? d / s : d);
        }
        return std::make_pair(worst, exact);
    };
    auto el = colerr(gl, rl, 6), eo = colerr(go, ro, 2), ef = colerr(gf, rf, 6);
    metric("triode_libm_col_rel", el.first); metric("triode_libm_bit_equal_frac", double(el.second) / (6 * n));
    metric("triode_only_col_rel", eo.first);
    metric("triode_fast_col_rel", ef.first); metric("triode_fast_bit_equal_frac", double(ef.second) / (6 * n));
    check(el.first <= TOL_TRIODE && eo.first <= TOL_TRIODE && ef.first <= TOL_TRIODE, "triode currents");
}

static void test_solver(const std::map<std::string, Vec>& V) {
    const Vec in = need(V, "solve.in"), ref = need(V, "solve.out"), Kv = need(V, "coef.standard.K");
    const double K[2][2] = {{Kv[0], Kv[1]}, {Kv[2], Kv[3]}};
    const size_t n = in.size() / 7;
    double ev = 0, ec = 0;
    long bad = 0, exact = 0;
    for (size_t i = 0; i < n; ++i) {
        const double* r = &in[7 * i];
        const Solution s = solve<LibmMath>(r[0], r[1], K, r[2], r[3], DZ_12AX7_RSD1, Newton{int(r[4]), r[5], r[6]});
        const double* q = &ref[6 * i];
        ev = std::max({ev, std::fabs(s.v0 - q[0]), std::fabs(s.v1 - q[1]) / std::max(1.0, std::fabs(q[1]))});
        ec = std::max({ec, std::fabs(s.ig - q[2]) / std::max(std::fabs(q[2]), 1e-12),
                       std::fabs(s.ia - q[3]) / std::max(std::fabs(q[3]), 1e-12)});
        bad += double(s.it) != q[4] || double(s.ok) != q[5];
        exact += s.v0 == q[0] && s.v1 == q[1] && s.ig == q[2] && s.ia == q[3];
    }
    metric("solve_v_err", ev); metric("solve_i_rel", ec); metric("solve_it_ok_mismatch", double(bad));
    metric("solve_bit_equal_frac", double(exact) / double(n));
    check(ev <= TOL_SOLVE && ec <= TOL_SOLVE && bad == 0, "solver from hard starts (" + std::to_string(bad) + " it/ok mismatches)");
    // the quality constants are stage.QUALITY
    const Vec e = need(V, "coef.eco.cfg"), h = need(V, "coef.hq.cfg"), st = need(V, "coef.standard.cfg");
    check(ECO.os == int(e[0]) && ECO.prewarp_hz == e[1] && HQ.os == int(h[0]) && HQ.prewarp_hz == h[1] &&
          STANDARD.os == int(st[0]) && STANDARD.prewarp_hz == st[1], "HQ / STANDARD / ECO = stage.QUALITY");
}

static void test_resampler(const std::map<std::string, Vec>& V) {
    double worst = 0;
    for (double fs : {48000.0, 44100.0}) for (int os : {4, 8, 16}) {
        const std::string p = (fs == 48000.0 ? "rs" : "rs44100_") + std::to_string(os) + ".";
        Resampler r;
        check(r.prepare(os, fs), p + "prepare");
        const Vec x = need(V, p + "up.in");
        Vec y(x.size() * os);
        for (size_t i = 0; i < x.size(); ++i) r.up(x[i], &y[i * os]);
        const double eu = relerr(y.data(), need(V, p + "up.out"));
        r.reset();
        const Vec yi = need(V, p + "down.in");
        Vec z(yi.size() / os);
        for (size_t i = 0; i < z.size(); ++i) z[i] = r.down(&yi[i * os]);
        const double ed = relerr(z.data(), need(V, p + "down.out"));
        worst = std::max({worst, eu, ed});
        check(eu <= TOL_RS && ed <= TOL_RS, p + "up/down");
    }
    metric("resampler_rel", worst);
    Resampler r;
    r.prepare(8, 48000.0);   // dk.resample.Resampler(48000, 8): pad 6, latency 72, up delay 285, macs 143 (dk/RESULTS.md)
    check(r.pad() == 6 && r.latency_samples() == 72 && r.up_delay_internal() == 285 && r.macs() == 143, "resampler 8x geometry");
    r.prepare(8, 44100.0);
    check(r.pad() == 6 && r.latency_samples() == 116 && r.up_delay_internal() == 461 && r.macs() == 187,
          "resampler 44.1 kHz 8x geometry");
    r.prepare(16, 44100.0);
    check(r.latency_samples() == 117, "resampler 44.1 kHz HQ latency");
    r.prepare(4, 44100.0);
    check(r.latency_samples() == 113, "resampler 44.1 kHz eco latency");
    check(!r.prepare(3, 48000.0) && !r.prepare(32, 48000.0), "os must be 2, 4, 8 or 16");
}

// The Python pack for a vectors coef.<name>: the C++ build (parameters, Newton, math) with Python's matrices, DC
// operating point, nodes and x0 put in. Running the C++ stage on it isolates the per-sample arithmetic.
static Coeffs pinned_pack(const std::map<std::string, Vec>& V, const std::string& name, TriodeMath math) {
    const std::string p = "coef." + name + ".";
    const Vec cfg = need(V, p + "cfg");
    Coeffs c;
    build_coeffs(c, 48000.0 * cfg[0], cfg[2], cfg[1], math);
    const std::pair<const char*, double*> mats[] = {
        {"A", &c.A[0][0]}, {"B", &c.B[0][0]}, {"C", &c.C[0][0]}, {"D", &c.D[0][0]}, {"E", &c.E[0][0]},
        {"F", &c.F[0][0]}, {"G", &c.G[0][0]}, {"H", &c.H[0][0]}, {"K", &c.K[0][0]}};
    for (auto& m : mats) { const Vec v = need(V, p + m.first); std::copy(v.begin(), v.end(), m.second); }
    const Vec op = need(V, p + "op"), nodes = need(V, p + "nodes"), x0 = need(V, p + "x0");
    c.op.vgk = op[0]; c.op.vak = op[1]; c.op.ig = op[2]; c.op.ia = op[3];
    std::copy(nodes.begin(), nodes.end(), c.op.nodes);
    std::copy(x0.begin(), x0.end(), c.x0);
    return c;
}
static std::string coef_name(int os, double bias) {
    return os == 16 ? "hq" : os == 4 ? "eco" : bias == 1.5 ? "standard_b15" : "standard";
}
// prepare, then (pinned) publish the Python pack and reset to its operating point
static void setup(Stage& s, const std::map<std::string, Vec>& V, int os, double prewarp, double bias, TriodeMath m, bool pinned) {
    check(s.prepare(48000.0, Quality{os, prewarp}, bias, m) == BuildStatus::ok, "prepare");
    if (pinned) { s.publish(pinned_pack(V, coef_name(os, bias), m)); s.reset(); }
}

static void test_internal(const std::map<std::string, Vec>& V) {
    for (bool pinned : {false, true}) {
        const std::string mode = pinned ? "pinned" : "built";
        Stage* s = new Stage;
        setup(*s, V, 8, 0.0, 1.0, TriodeMath::libm, pinned);
        const Vec in = need(V, "internal.std8v.in"), ro = need(V, "internal.std8v.out"), rv = need(V, "internal.std8v.vgk");
        const Vec ri = need(V, "internal.std8v.iters");
        const size_t n = in.size();
        Vec out(n), vgk(n);
        std::vector<std::uint16_t> it(n);
        long allocs;
        { CountAllocs ca; s->process_internal(in.data(), out.data(), vgk.data(), it.data(), n); allocs = ca.n(); }
        const double eo = maxdiff(out.data(), ro.data(), n), ev = maxdiff(vgk.data(), rv.data(), n);
        long mism = 0;
        for (size_t i = 0; i < n; ++i) mism += double(it[i]) != ri[i];
        metric("internal_" + mode + "_out_abs", eo); metric("internal_" + mode + "_vgk_abs", ev);
        metric("internal_" + mode + "_iter_mismatch", double(mism));
        const double tol = pinned ? TOL_PINNED_ABS : TOL_BUILT_ABS;
        check(eo <= tol && ev <= tol, "internal " + mode + " out / vgk");
        check(double(mism) <= TOL_ITER_FRAC * n, "internal " + mode + " iteration counts");
        check(allocs == 0, "process_internal allocates nothing");
        delete s;
    }
}

struct CaseCfg {
    int os; double prewarp, bias, drive, output; bool fast; Newton nw; long swap_at; double swap_bias; Newton nw2;
};
static CaseCfg parse_cfg(const Vec& c) {
    return CaseCfg{int(c[0]), c[1], c[2], c[3], c[4], c[5] != 0.0, Newton{int(c[6]), c[7], c[8]}, long(c[9]), c[10],
                   Newton{int(c[11]), c[12], c[13]}};
}

// Runs one case of n samples on a set-up stage (Stage or StereoStage), process(i, m) doing samples [i, i + m): the
// cfg's runtime Newton settings and the optional swap go through publish / request_bias, as a host would (pinned:
// the swap publishes the Python pack for the new bias). Returns allocations counted inside process().
template <class S, class P>
static long run_blocks(S& s, const std::map<std::string, Vec>& V, const CaseCfg& k, bool pinned, size_t n,
                       size_t block, P process) {
    Coeffs c = s.latest();
    c.newton = k.nw;
    s.publish(c);
    long allocs = 0;
    const size_t at = k.swap_at >= 0 ? size_t(k.swap_at) : n;
    auto blocks = [&](size_t a, size_t b) {
        for (size_t i = a; i < b; i += block) {
            const size_t m = std::min(block, b - i);
            CountAllocs ca;
            process(i, m);
            allocs += ca.n();
        }
    };
    blocks(0, at);
    if (at < n) {
        Coeffs c2;
        if (pinned) c2 = pinned_pack(V, coef_name(k.os, k.swap_bias), c.math);
        else { if (k.swap_bias != s.latest().bias) s.request_bias(k.swap_bias); c2 = s.latest(); }
        c2.newton = k.nw2;
        s.publish(c2);
        blocks(at, n);
    }
    return allocs;
}
static long run_case(Stage& s, const std::map<std::string, Vec>& V, const CaseCfg& k, bool pinned, const Vec& x,
                     Vec& y, size_t block) {
    s.set_drive(k.drive); s.set_output(k.output);
    y.assign(x.size(), 0.0);
    return run_blocks(s, V, k, pinned, x.size(), block, [&](size_t i, size_t m) { s.process(x.data() + i, y.data() + i, m); });
}

static void test_cases(const std::map<std::string, Vec>& V) {
    double worst[2] = {0, 0}, worst_iter = 0;
    long total_allocs = 0;
    for (const auto& kv : V) {
        const std::string& key = kv.first;
        if (key.rfind("case.", 0) != 0 || key.size() < 4 || key.compare(key.size() - 4, 4, ".cfg") != 0) continue;
        const std::string p = key.substr(0, key.size() - 3), name = p.substr(5, p.size() - 6);
        const CaseCfg k = parse_cfg(kv.second);
        const Vec x = need(V, p + "in"), ref = need(V, p + "out"), rst = need(V, p + "stats");
        for (bool pinned : {false, true}) {
            const std::string mode = pinned ? "pinned" : "built";
            Stage* s = new Stage;
            setup(*s, V, k.os, k.prewarp, k.bias, k.fast ? TriodeMath::fast : TriodeMath::libm, pinned);
            Vec y;
            total_allocs += run_case(*s, V, k, pinned, x, y, 64);
            const double e = maxdiff(y.data(), ref.data(), ref.size());
            const Stats& S = s->stats();
            const double di = std::fabs(double(S.iterations) - rst[1]) / rst[1];
            worst[pinned] = std::max(worst[pinned], e); worst_iter = std::max(worst_iter, di);
            metric("case_" + name + "_" + mode + "_abs", e);
            check(e <= (pinned ? TOL_PINNED_ABS : TOL_BUILT_ABS), name + " " + mode + " output abs " + std::to_string(e));
            check(double(S.samples) == rst[0] && double(S.nonconverged) == rst[2] && double(S.max_iterations) == rst[3] &&
                  di <= TOL_ITER_FRAC, name + " " + mode + " iteration stats (" + std::to_string(S.iterations) +
                  " vs " + std::to_string(rst[1]) + ", nonconv " + std::to_string(S.nonconverged) + ")");
            check(allfinite(y.data(), y.size()) && allfinite(s->state_x(), NX) && allfinite(s->state_v(), 2), name + " finite");
            // block-size invariance and reset(): one call == blocks of 64 == ragged blocks of 37, bit for bit
            if (k.swap_at < 0 && !pinned) {
                Vec y1, y2;
                s->reset(); run_case(*s, V, k, pinned, x, y1, x.size());
                s->reset(); run_case(*s, V, k, pinned, x, y2, 37);
                check(y1 == y && y2 == y, name + " block-size invariance and reset()");
            }
            delete s;
        }
    }
    metric("cases_built_abs_worst", worst[0]); metric("cases_pinned_abs_worst", worst[1]);
    metric("cases_iter_frac_worst", worst_iter);
    metric("process_allocations", double(total_allocs));
    check(total_allocs == 0, "process() allocates nothing (" + std::to_string(total_allocs) + ")");
}

static void test_nonconvergence() {
    // (a) the cap is reached: best finite iterate, flagged, never worse than the start or a shorter run
    Stage* s = new Stage;
    s->prepare(48000.0);
    const Coeffs& c = s->active();
    double p0 = c.H[0][0] * 0 + c.H[0][1] * c.HT, p1 = c.H[1][0] * 0 + c.H[1][1] * c.HT;
    for (int j = 0; j < NX; ++j) { p0 += c.G[0][j] * c.x0[j]; p1 += c.G[1][j] * c.x0[j]; }
    auto resid = [&](double v0, double v1) {
        Currents q; Triode<LibmMath>::currents(v0, v1, c.par, q);
        return std::max(std::fabs(p0 - c.K[0][0] * q.ig - c.K[0][1] * q.ia - v0), std::fabs(p1 - c.K[1][0] * q.ig - c.K[1][1] * q.ia - v1));
    };
    const double r0 = resid(2.0, 50.0);
    double prev = INFINITY;
    bool ok = true;
    for (int k = 1; k <= 5; ++k) {
        const Solution r = solve<LibmMath>(p0, p1, c.K, 2.0, 50.0, c.par, Newton{k, 0.0, 0.0});
        const double rr = resid(r.v0, r.v1);
        ok = ok && !r.ok && r.it == k && std::isfinite(r.v0) && std::isfinite(r.v1) && std::isfinite(r.ig) &&
             std::isfinite(r.ia) && rr <= r0 && rr <= prev;
        prev = rr;
    }
    check(ok, "solver returns the best finite iterate on failure");

    // (b) NaN never wins: every evaluation after the start is NaN -> the (finite) start comes back, flagged
    struct NanAfterStart {
        static int& n() { static int k = 0; return k; }
        static double exp(double x) { return ++n() <= 2 ? std::exp(x) : NAN; }   // the start's currents(): 2 exp
        static double log1p(double x) { return std::log1p(x); }
        static double pow(double x, double y) { return std::pow(x, y); }
    };
    NanAfterStart::n() = 0;
    const Solution r = solve<NanAfterStart>(p0, p1, c.K, c.op.vgk + 0.5, c.op.vak - 5.0, c.par, Newton{5, 1e-6, 1e-6});
    check(!r.ok && r.v0 == c.op.vgk + 0.5 && r.v1 == c.op.vak - 5.0 && std::isfinite(r.ig) && std::isfinite(r.ia),
          "a NaN iterate never wins");

    // (c) NaN / inf input: replaced by 0, counted, the state stays finite
    double x[64], y[64];
    for (int i = 0; i < 64; ++i) x[i] = i % 7 == 3 ? NAN : (i % 11 == 5 ? INFINITY : std::sin(0.1 * i));
    long allocs;
    { CountAllocs ca; s->process(x, y, 64); allocs = ca.n(); }
    check(allocs == 0, "process allocates nothing (NaN input)");
    check(s->stats().nonfinite_inputs == 14 && allfinite(y, 64) && allfinite(s->state_x(), NX), "non-finite input");

    // (d) forced runtime non-convergence is counted per block and in total, then recovers
    s->reset();
    Coeffs bad = s->latest(); bad.newton = Newton{1, 0.0, 0.0};
    s->publish(bad);
    for (int i = 0; i < 64; ++i) x[i] = 4.0 * std::sin(2 * PI * 1000.0 * i / 48000.0);
    s->process(x, y, 64);
    const std::uint64_t n1 = 64u * 8u;
    check(s->stats().nonconverged == n1 && s->stats().nonconv_block == n1 && s->nonconverged_total() == n1 &&
          allfinite(y, 64) && allfinite(s->state_v(), 2), "forced non-convergence is counted, state finite");
    Coeffs good = s->latest(); good.newton = Newton{};
    s->publish(good);
    s->process(x, y, 64);
    check(s->stats().nonconv_block == 0 && s->stats().nonconverged == n1 && allfinite(y, 64), "recovers after the fallback");
    s->reset();
    check(s->stats().nonconverged == 0 && s->nonconverged_total() == 0, "reset() clears the report");
    delete s;

    // (e) a stage whose DC solve fails is not prepared and outputs silence; a bad publish is refused
    s = new Stage;
    check(s->prepare(48000.0, STANDARD, 1.0, TriodeMath::libm, Newton{1, 1e-6, 1e-6}) == BuildStatus::dc_nonconvergence &&
          !s->prepared(), "prepare reports DC non-convergence");
    for (int i = 0; i < 64; ++i) y[i] = 1.0;
    s->process(x, y, 64);
    check(maxabs(y, 64) == 0.0, "an unprepared stage outputs zeros");
    s->prepare(48000.0);
    Coeffs other = s->latest(); other.fs = 192000.0;
    check(!s->publish(other), "a pack for another rate is refused");
    delete s;
}

// #137 (as test_nonfinite_recovery in test_core_6as6.cpp, #104): NaN / inf audio and finite-but-absurd samples or gains
// must not leave the state non-finite. The input is checked after the drive gain; a non-finite drive / output gain is
// ignored; a state that still goes non-finite recovers to the operating point without reset(). Mono, libm and fast,
// every quality.
static bool same_bits(double a, double b);
static void test_nonfinite_recovery() {
    long allocs = 0;
    double worst_after = 0.0;
    bool finite = true, counted = true;
    for (Quality q : {ECO, STANDARD, HQ})
        for (TriodeMath m : {TriodeMath::libm, TriodeMath::fast}) {
            Stage* s = new Stage; Stage* ref = new Stage;
            check(s->prepare(48000.0, q, 1.0, m) == BuildStatus::ok && ref->prepare(48000.0, q, 1.0, m) == BuildStatus::ok,
                  "recovery: prepare");
            Vec x(512, 0.0), y(512), z(512, 0.0), w(512), r(512);
            // otherwise silent, so after the recovery the stage must sit on its operating point
            x[10] = DBL_MAX; x[20] = -DBL_MAX; x[30] = NAN; x[40] = INFINITY;
            {
                CountAllocs c;
                s->process(x.data(), y.data(), x.size());                // the bad samples
                for (double v : y) finite = finite && std::isfinite(v);
                s->set_drive(NAN);                                        // ignored: the drive stays 1
                s->set_output(INFINITY);                                  // ignored
                s->process(z.data(), w.data(), z.size());
                for (double v : w) finite = finite && std::isfinite(v);
                s->set_drive(1e308);                                      // finite, but 4 V x 1e308 overflows
                std::fill(x.begin(), x.end(), 4.0);
                s->process(x.data(), y.data(), 8);
                for (int i = 0; i < 8; ++i) finite = finite && std::isfinite(y[i]);
                s->set_drive(1.0);
                for (int b = 0; b < 6; ++b) s->process(z.data(), w.data(), z.size());   // silence, no reset()
                allocs += c.n();
            }
            for (int b = 0; b < 7; ++b) ref->process(z.data(), r.data(), z.size());   // a stage that only saw silence
            for (size_t i = 0; i < w.size(); ++i) {
                finite = finite && std::isfinite(w[i]);
                worst_after = std::max(worst_after, std::fabs(w[i] - r[i]));
            }
            finite = finite && allfinite(s->state_x(), NX) && allfinite(s->state_v(), 2);
            const Stats& st = s->stats();
            // +-DBL_MAX recovered (at least once: a recovery clears the resampler, so it can flush both); NaN, inf and
            // the 8 overflowing driven samples replaced by 0
            counted = counted && st.recoveries >= 1 && st.nonfinite_inputs == 2 + 8 && st.nonfinite_outputs == 0;
            if (q.os == STANDARD.os && m == TriodeMath::libm) metric("recovery_count_standard_libm", double(st.recoveries));
            delete s; delete ref;
        }
    metric("recovery_after_max_dev_v", worst_after);
    check(finite, "recovery: every output and the final state finite");
    check(counted, "recovery: counted (recoveries, nonfinite_inputs)");
    check(worst_after <= TOL_PINNED_ABS, "recovery: silence settles back on the operating point without reset() " +
          std::to_string(worst_after));
    check(!ALLOC_HOOKS || allocs == 0, "recovery: no allocation");

    // a non-finite gain is ignored: the last finite one stays (drive 2.5, output 0.4 on both stages)
    {
        Stage* a = new Stage; Stage* b = new Stage;
        a->prepare(48000.0); b->prepare(48000.0);
        a->set_drive(2.5); a->set_output(0.4); b->set_drive(2.5); b->set_output(0.4);
        a->set_drive(NAN); a->set_output(NAN); a->set_drive(INFINITY); a->set_output(-INFINITY);
        Vec x(1024), ya(1024), yb(1024);
        for (size_t i = 0; i < x.size(); ++i) x[i] = 0.3 * std::sin(0.05 * double(i));
        a->process(x.data(), ya.data(), x.size()); b->process(x.data(), yb.data(), x.size());
        bool same = true;
        for (size_t i = 0; i < x.size(); ++i) same = same && same_bits(ya[i], yb[i]);
        check(same && a->stats().nonfinite_inputs == 0, "recovery: a non-finite gain is ignored");
        delete a; delete b;
    }

    // process_internal(): the same input and state rule
    {
        Stage* s = new Stage;
        s->prepare(48000.0);
        Vec u(512, 0.0), o(512), v(512);
        u[5] = NAN; u[9] = -INFINITY; u[100] = DBL_MAX; u[200] = -DBL_MAX;
        long a;
        { CountAllocs c; s->process_internal(u.data(), o.data(), v.data(), nullptr, u.size()); a = c.n(); }
        check(a == 0 && allfinite(o.data(), o.size()) && allfinite(v.data(), v.size()) && allfinite(s->state_x(), NX) &&
              allfinite(s->state_v(), 2) && s->stats().nonfinite_inputs == 2 && s->stats().recoveries >= 1,
              "recovery: process_internal (NaN / inf replaced, +-DBL_MAX recovered, no allocation)");
        delete s;
    }

    // a finite output gain that overflows the destination writes 0 and is counted; the state is untouched: back at
    // unity gain, the next block equals that of a stage that never saw the gain (double and float I/O)
    bool ok = true;
    const size_t n = 4800;
    Vec x(n);
    for (size_t i = 0; i < n; ++i) x[i] = 0.5 * std::sin(2 * PI * 997.0 * double(i) / 48000.0);
    for (int pass = 0; pass < 2; ++pass) {          // 0: double I/O, 1: float I/O
        Stage* s = new Stage; Stage* ref = new Stage;
        s->prepare(48000.0); ref->prepare(48000.0);
        s->set_output(pass == 0 ? DBL_MAX : 1e38);  // 1e38: finite as a double, over FLT_MAX times the stage's volts
        Vec y1(n), y2(n), r1(n), r2(n);
        std::vector<float> xf(x.begin(), x.end()), yf(n);
        auto run = [&](Stage& st, Vec& y) {
            if (pass == 0) { st.process(x.data(), y.data(), n); return; }
            st.process(xf.data(), yf.data(), n);
            for (size_t i = 0; i < n; ++i) y[i] = yf[i];
        };
        long a;
        { CountAllocs c; run(*s, y1); a = c.n(); }
        size_t bad = 0;
        for (double v : y1) bad += !std::isfinite(v);
        const Stats& st = s->stats();
        ok = ok && bad == 0 && st.nonfinite_outputs > 0 && st.recoveries == 0 && st.nonconverged == 0 && a == 0;
        s->set_output(1.0);
        run(*s, y2);
        run(*ref, r1); run(*ref, r2);
        for (size_t i = 0; i < n; ++i) ok = ok && same_bits(y2[i], r2[i]);
        metric(pass == 0 ? "output_overflow_double_zeroed" : "output_overflow_float_zeroed", double(st.nonfinite_outputs));
        delete s; delete ref;
    }
    check(ok, "an overflowing output gain writes 0, counted; the state is untouched (double and float I/O)");

    // out-of-range bias and bias-ramp values: a non-finite bias is ignored, a finite one is clamped to the range, and a
    // NaN / inf / negative / huge ramp length is safe; the stage stays finite and within the range
    {
        Stage* s = new Stage;
        s->prepare(48000.0);
        Vec xs(256), ys(256);
        for (size_t i = 0; i < xs.size(); ++i) xs[i] = 2.0 * std::sin(0.03 * double(i));
        bool fin = true, in_range = true;
        const double biases[] = {NAN, INFINITY, -INFINITY, -5.0, 0.0, 1e300, -1e300, 1.3};
        const double ramps[] = {NAN, INFINITY, -INFINITY, -1.0, 0.0, 1e300, 1e-300, 0.005};
        long a = 0;
        for (double rp : ramps)
            for (double b : biases) {
                s->set_bias_ramp(rp);
                s->set_bias(b);
                CountAllocs c;
                s->process(xs.data(), ys.data(), xs.size());
                a += c.n();
                fin = fin && allfinite(ys.data(), ys.size()) && allfinite(s->state_x(), NX) && allfinite(s->state_v(), 2);
                in_range = in_range && s->bias_now() >= 0.5 && s->bias_now() <= 2.0;
            }
        check(fin && in_range && a == 0 && s->stats().recoveries == 0,
              "out-of-range bias / ramp values: finite, within the bias range, no allocation");
        delete s;
    }
}

// #70: the state runs as the deviation from the operating point, so 100 ms of silence stays at the rest point: out =
// the DC out node (0 V) up to per-sample rounding (measured 1.2e-13 V at hq, Apple M1). The absolute-state form
// settled 7.7e-8 / 7.4e-7 / 1.2e-6 V off (eco / standard / hq, Apple M1, Apple clang -O2).
static void test_silence() {
    const Quality qs[3] = {ECO, STANDARD, HQ};
    const char* names[3] = {"eco", "standard", "hq"};
    Vec x(4800, 0.0), y(4800);
    for (int k = 0; k < 3; ++k) {
        Stage* s = new Stage;
        check(s->prepare(48000.0, qs[k]) == BuildStatus::ok, "silence prepare");
        s->process(x.data(), y.data(), x.size());
        double e = 0.0;
        for (double v : y) e = std::max(e, std::fabs(v));
        metric(std::string("silence_abs_") + names[k], e);
        check(e <= TOL_PINNED_ABS, std::string("silence stays at the operating point, ") + names[k] + " " + std::to_string(e));
        delete s;
    }
}

// #74: actual prepare/process at each supported host rate, all fixed quality settings, mono and stereo.
// Keep the existing rest and mono/stereo tolerance (TOL_PINNED_ABS). Full reference sweeps are offline.
static void test_sample_rates() {
    for (double fs : {44100.0, 48000.0, 88200.0, 96000.0, 176400.0, 192000.0}) {
        for (Quality q : {ECO, STANDARD, HQ}) {
            for (TriodeMath math : {TriodeMath::libm, TriodeMath::fast}) {
                Stage* mono = new Stage;
                StereoStage* stereo = new StereoStage;
                check(mono->prepare(fs, q, 1.0, math) == BuildStatus::ok, "host-rate mono prepare");
                check(stereo->prepare(fs, q, 1.0, math) == BuildStatus::ok, "host-rate stereo prepare");
                const int latency = fir_geometry(q.os, fs).latency_samples;
                check(mono->latency_samples() == latency && stereo->latency_samples() == latency,
                      "host-rate latency in base samples");
                Vec x(size_t(fs * 0.1), 0.0), y(x.size()), l(x.size()), r(x.size());
                mono->process(x.data(), y.data(), x.size());
                stereo->process(x.data(), x.data(), l.data(), r.data(), x.size());
                double rest = 0.0;
                for (size_t i = 0; i < x.size(); ++i)
                    rest = std::max({rest, std::fabs(y[i]), std::fabs(l[i]), std::fabs(r[i])});
                check(rest <= TOL_PINNED_ABS, "host-rate startup/rest");
                x.resize(size_t(fs * 0.02));
                for (size_t i = 0; i < x.size(); ++i) x[i] = 8.0 * std::sin(2 * PI * 1000.0 * i / fs);
                for (size_t i = 0; i < x.size(); i += 64) {
                    const size_t n = std::min(size_t(64), x.size() - i);
                    long allocations;
                    {
                        CountAllocs ca;
                        mono->process(x.data() + i, y.data() + i, n);
                        stereo->process(x.data() + i, x.data() + i, l.data() + i, r.data() + i, n);
                        allocations = ca.n();
                    }
                    check(allocations == 0, "host-rate process allocation");
                }
                double peak = 0.0, err = 0.0;
                for (size_t i = 0; i < x.size(); ++i) {
                    check(std::isfinite(y[i]) && std::isfinite(l[i]) && std::isfinite(r[i]), "host-rate finite");
                    peak = std::max(peak, std::fabs(y[i]));
                    err = std::max({err, std::fabs(y[i] - l[i]), std::fabs(y[i] - r[i])});
                }
                check(peak > 1.0 && err <= TOL_PINNED_ABS, "host-rate signal and mono/stereo agreement");
                check(mono->stats().nonconverged == 0 && stereo->nonconverged_total() == 0,
                      "host-rate Newton convergence");
                const std::string name = "rate_" + std::to_string(int(fs)) + "_" + std::to_string(q.os) +
                                         (math == TriodeMath::fast ? "_fast" : "_libm");
                metric(name + "_rest_v", rest);
                metric(name + "_stereo_diff_v", err);
                delete mono; delete stereo;
            }
        }
    }
}

static void test_denormals() {
    if (!ScopedFlushDenormals::active()) { metric("ftz_tested", 0); return; }
    volatile double tiny = 1e-310, one = 1.0;           // a double denormal
    // The results are volatile too (#64): the compiler does not know that the guard changes the FP mode, so it may
    // move a plain multiply out of the scope (Apple clang 17 -O2 on AArch64 did the "inside" one after the restore).
    // A volatile store keeps each multiply between the volatile reads before it and the mode change after it.
    volatile double before, inside, after;
    before = tiny * one;
    { ScopedFlushDenormals g; inside = tiny * one; }
    after = tiny * one;
    check(before != 0.0 && inside == 0.0 && after != 0.0, "ScopedFlushDenormals flushes in scope and restores");
#if defined(DKCORE_X86_SSE) || defined(__aarch64__)
    const auto fp_mode = [] {                           // MXCSR on x86, FPCR on AArch64
#if defined(DKCORE_X86_SSE)
        return std::uint64_t(_mm_getcsr());
#else
        std::uint64_t v;
        __asm__ __volatile__("mrs %0, fpcr" : "=r"(v));
        return v;
#endif
    };
    const std::uint64_t mode = fp_mode();
    Stage* s = new Stage;
    s->prepare(48000.0);
    double x[16] = {}, y[16];
    s->process(x, y, 16);
    check(fp_mode() == mode, "process() restores the caller's FP mode (MXCSR / FPCR)");
    delete s;
#endif
    metric("ftz_tested", 1);
}

// matrices, op point and x0 bit for bit (contiguous doubles, no padding), and the bias
static bool same_pack(const Coeffs& a, const Coeffs& b) {
    const size_t off = offsetof(Coeffs, A), len = offsetof(Coeffs, dc_step) - off;
    return a.bias == b.bias && std::memcmp(reinterpret_cast<const char*>(&a) + off, reinterpret_cast<const char*>(&b) + off, len) == 0;
}

// Producer rebuilds and publishes while the audio thread processes: every adopted pack must be one of the built
// ones, bit for bit (no torn read), and the output stays finite.
static void test_swap_threads() {
    Stage* s = new Stage;
    s->prepare(48000.0);
    const double biases[3] = {1.0, 1.5, 0.8};
    Coeffs ref[3];
    for (int i = 0; i < 3; ++i) build_coeffs(ref[i], 384000.0, biases[i]);
    std::atomic<bool> stop{false};
    std::atomic<long> published{0};
    std::thread producer([&] {
        for (int k = 0; !stop.load(); ++k) { s->request_bias(biases[k % 3]); ++published; }
    });
    double x[64], y[64];
    long blocks = 0, torn = 0, nonfinite = 0;
    const auto t0 = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - t0 < std::chrono::milliseconds(300) || published.load() < 50) {
        for (int i = 0; i < 64; ++i) x[i] = 2.0 * std::sin(2 * PI * 1000.0 * (blocks * 64 + i) / 48000.0);
        s->process(x, y, 64);
        const Coeffs& a = s->active();
        int which = -1;
        for (int i = 0; i < 3; ++i) if (same_pack(a, ref[i])) which = i;
        torn += which < 0;
        nonfinite += !allfinite(y, 64);
        ++blocks;
    }
    stop = true;
    producer.join();
    metric("swap_blocks", double(blocks)); metric("swap_published", double(published.load()));
    metric("swap_adopted", double(s->stats().swaps));
    check(torn == 0 && nonfinite == 0 && s->stats().swaps > 0, "lock-free swap: no torn pack, finite output (torn " +
          std::to_string(torn) + ")");
    // producer side allocates nothing either (fixed-size pack, no heap)
    long allocs;
    { CountAllocs ca; s->request_bias(1.25); allocs = ca.n(); }
    check(allocs == 0, "request_bias allocates nothing");
    delete s;
}

static void test_float_io() {
    Stage* a = new Stage; Stage* b = new Stage;
    a->prepare(48000.0); b->prepare(48000.0);
    double xd[480], yd[480]; float xf[480], yf[480];
    for (int i = 0; i < 480; ++i) { xf[i] = float(2.0 * std::sin(2 * PI * 1000.0 * i / 48000.0)); xd[i] = xf[i]; }
    long allocs;
    { CountAllocs ca; a->process(xd, yd, 480); b->process(xf, yf, 480); allocs = ca.n(); }
    check(allocs == 0, "float process allocates nothing");
    double e = 0;
    for (int i = 0; i < 480; ++i) e = std::max(e, std::fabs(double(yf[i]) - float(yd[i])));
    check(e == 0.0, "float I/O = double path rounded to float");
    delete a; delete b;
}

// ---- stereo (#21): StereoStage against two independent mono Stages ------------------------------------------------
/* Each lane of the stereo kernel does the mono operations in the mono order. So the outputs, the per-channel Stats and
 * the final states are bit-equal to two mono Stages wherever the build contracts (FMA) scalar and vector code at the
 * same places:
 *   - no contraction: -ffp-contract=off, or x86-64 without -march (SSE2 has no FMA);
 *   - clang's per-expression contraction with the native NEON / SSE2 vector types.
 * g_stereo_exact reports whether every comparison was bit-equal; cpp/tests asserts it for those builds. Where the
 * two codes may contract differently (the struct fallback with contraction on; GCC's -ffp-contract=fast with FMA,
 * -march=x86-64-v3), the arithmetic is the same and only the rounding differs. That is what TOL_PINNED_ABS bounds
 * between builds (same pack, same per-sample code; measured 2.7e-11 V over the FMA / non-FMA builds), so the stereo
 * tolerance is the same, with TOL_ITER_FRAC for the Newton statistics. */
static const double TOL_STEREO_ABS = TOL_PINNED_ABS;
/* The vector-math path (#67, LaneMath::vector, VecFastMath) is held to the same rule against two mono fast Stages:
 * each lane does FastMath's operations in FastMath's order, so it is bit-equal on the same builds, and otherwise
 * differs by rounding only (TOL_STEREO_ABS, TOL_ITER_FRAC). Its own tallies: g_vec_*. */
static bool g_stereo_exact = true, g_vec_exact = true;
static double g_stereo_worst = 0.0, g_vec_worst = 0.0;
static long g_stereo_runs = 0, g_stereo_allocs = 0, g_stereo_lanes_differ = 0, g_vec_runs = 0, g_vec_lanes_differ = 0;

static bool same_stats(const Stats& a, const Stats& b) {
    return a.samples == b.samples && a.iterations == b.iterations && a.nonconverged == b.nonconverged &&
           a.nonfinite_inputs == b.nonfinite_inputs && a.recoveries == b.recoveries &&
           a.nonfinite_outputs == b.nonfinite_outputs && a.swaps == b.swaps && a.max_iterations == b.max_iterations &&
           std::equal(a.histogram, a.histogram + Stats::HIST, b.histogram);
}

// channel ch of a stereo run against its mono reference: output, Stats, final state
static void compare_channel(const std::string& what, const Vec& ys, const Vec& ym, const StereoStage& s, int ch, const Stage& m,
                            bool vec) {
    const Stats &a = s.stats(ch), &b = m.stats();
    bool state_eq = true;
    double ds = 0;
    for (int i = 0; i < NX; ++i) { state_eq = state_eq && s.state_x(ch, i) == m.state_x()[i]; ds = std::max(ds, std::fabs(s.state_x(ch, i) - m.state_x()[i])); }
    for (int i = 0; i < 2; ++i) { state_eq = state_eq && s.state_v(ch, i) == m.state_v()[i]; ds = std::max(ds, std::fabs(s.state_v(ch, i) - m.state_v()[i])); }
    const double e = maxdiff(ys.data(), ym.data(), ym.size());
    const bool exact = ys == ym && same_stats(a, b) && state_eq;
    (vec ? g_vec_exact : g_stereo_exact) &= exact;
    double& worst = vec ? g_vec_worst : g_stereo_worst;
    worst = std::max(worst, e);
    const double di = std::fabs(double(a.iterations) - double(b.iterations)) / double(b.iterations);
    const double dn = std::fabs(double(a.nonconverged) - double(b.nonconverged));
    check(e <= TOL_STEREO_ABS && ds <= TOL_STEREO_ABS, what + " output / state abs " + std::to_string(e));
    check(a.samples == b.samples && a.nonfinite_inputs == b.nonfinite_inputs && a.recoveries == b.recoveries &&
          a.nonfinite_outputs == b.nonfinite_outputs && a.swaps == b.swaps &&
          di <= TOL_ITER_FRAC && dn <= TOL_ITER_FRAC * double(b.samples), what + " stats");
    if (!exact) std::fprintf(stderr, "note: %s not bit-equal to mono (abs %.3g, iterations %" PRIu64 " vs %" PRIu64 ")\n",
                             what.c_str(), e, a.iterations, b.iterations);
}

// a right-channel input unlike the left one: the left reversed, plus a tone that is loud in the second half, and
// some NaN / inf samples (replaced by 0 and counted per channel)
static Vec other_signal(const Vec& x) {
    const size_t n = x.size();
    Vec r(n);
    for (size_t i = 0; i < n; ++i) {
        r[i] = 0.6 * x[n - 1 - i] + (i < n / 2 ? 0.2 : 5.0) * std::sin(2 * PI * 1370.0 * double(i) / 48000.0);
        if (i % 97 == 13) r[i] = NAN;
        if (i % 211 == 50) r[i] = -INFINITY;
    }
    return r;
}

// One stereo run of case k against two mono runs: L gets x with k's gains, R gets xr with its gains scaled by
// (dr, orr). Mono runs in blocks of 64, stereo in blocks of sblock (mono is block-size invariant, test_cases).
// lm: the stereo lane maths (vector: the #67 path; k.fast must be set).
static void stereo_case(const std::map<std::string, Vec>& V, const std::string& what, const CaseCfg& k, const Vec& x,
                        const Vec& xr, double dr, double orr, size_t sblock, LaneMath lm = LaneMath::per_lane) {
    const TriodeMath math = k.fast ? TriodeMath::fast : TriodeMath::libm;
    const bool vec = lm == LaneMath::vector;
    CaseCfg kr = k;
    kr.drive *= dr; kr.output *= orr;
    Stage* ml = new Stage; Stage* mr = new Stage; StereoStage* s = new StereoStage;
    for (Stage* m : {ml, mr}) check(m->prepare(48000.0, Quality{k.os, k.prewarp}, k.bias, math) == BuildStatus::ok, "prepare");
    check(s->prepare(48000.0, Quality{k.os, k.prewarp}, k.bias, math) == BuildStatus::ok, "stereo prepare");
    Vec yml, ymr;
    run_case(*ml, V, k, false, x, yml, 64);
    run_case(*mr, V, kr, false, xr, ymr, 64);
    Vec yl(x.size()), yr(x.size());
    s->set_drive(k.drive, kr.drive); s->set_output(k.output, kr.output);
    s->set_lane_math(lm);
    g_stereo_allocs += run_blocks(*s, V, k, false, x.size(), sblock, [&](size_t i, size_t m) {
        s->process(x.data() + i, xr.data() + i, yl.data() + i, yr.data() + i, m);
    });
    compare_channel(what + " L", yl, yml, *s, 0, *ml, vec);
    compare_channel(what + " R", yr, ymr, *s, 1, *mr, vec);
    check(s->nonconverged_total() == s->stats(0).nonconverged + s->stats(1).nonconverged, what + " nonconverged_total");
    // the kernel ran max(itL, itR) per sample: between the larger and the sum of the per-channel totals; equal to
    // them when both lanes got the same problems
    const std::uint64_t il = s->stats(0).iterations, ir = s->stats(1).iterations, kit = s->kernel_iterations();
    const bool same = &x == &xr && dr == 1.0 && orr == 1.0;
    check(kit >= std::max(il, ir) && kit <= il + ir && (!same || (kit == il && il == ir)), what + " kernel_iterations");
    // different iteration-count multisets: some sample had a different count per lane, so the masking was exercised
    (vec ? g_vec_lanes_differ : g_stereo_lanes_differ) +=
        !std::equal(ml->stats().histogram, ml->stats().histogram + Stats::HIST, mr->stats().histogram);
    ++(vec ? g_vec_runs : g_stereo_runs);
    delete ml; delete mr; delete s;
}

static void stereo_bias_case(const std::string& what, TriodeMath math, LaneMath lm);   // #73, below

static void test_stereo(const std::map<std::string, Vec>& V) {
    // every vectors case (all settings, libm and fast, bias, live swap, forced non-convergence), three ways
    for (const auto& kv : V) {
        const std::string& key = kv.first;
        if (key.rfind("case.", 0) != 0 || key.size() < 4 || key.compare(key.size() - 4, 4, ".cfg") != 0) continue;
        const std::string p = key.substr(0, key.size() - 3), name = p.substr(5, p.size() - 6);
        const CaseCfg k = parse_cfg(kv.second);
        const Vec x = need(V, p + "in"), xr = other_signal(x);
        stereo_case(V, "stereo " + name + " same", k, x, x, 1.0, 1.0, 64);
        stereo_case(V, "stereo " + name + " diff", k, x, xr, 1.0, 1.0, 64);
        stereo_case(V, "stereo " + name + " gains", k, x, xr, 2.5, 0.4, 37);   // + ragged blocks
        // the vector-math path (#67) on the same case with fast math, against two mono fast Stages
        CaseCfg kf = k;
        kf.fast = true;
        stereo_case(V, "vector " + name + " same", kf, x, x, 1.0, 1.0, 64, LaneMath::vector);
        stereo_case(V, "vector " + name + " diff", kf, x, xr, 1.0, 1.0, 64, LaneMath::vector);
        stereo_case(V, "vector " + name + " gains", kf, x, xr, 2.5, 0.4, 37, LaneMath::vector);
    }
    // capped Newton (3 iterations): the loud lane hits the cap and falls back (#7) while the quiet one converges
    {
        Vec x(960), xr(960);
        for (size_t i = 0; i < x.size(); ++i) {
            x[i] = 8.0 * std::sin(2 * PI * 1000.0 * double(i) / 48000.0);
            xr[i] = 0.05 * std::sin(2 * PI * 700.0 * double(i) / 48000.0);
        }
        const CaseCfg k{8, 0.0, 1.0, 1.0, 1.0, false, Newton{3, 1e-6, 1e-6}, -1, 1.0, Newton{}};
        stereo_case(V, "stereo capped", k, x, xr, 1.0, 1.0, 64);
        CaseCfg kf = k;
        kf.fast = true;
        stereo_case(V, "vector capped", kf, x, xr, 1.0, 1.0, 64, LaneMath::vector);
        StereoStage* s = new StereoStage;          // the fallback split per lane, checked on a fresh run
        s->prepare(48000.0);
        Coeffs c = s->latest(); c.newton = k.nw; s->publish(c);
        Vec yl(x.size()), yr(x.size());
        s->process(x.data(), xr.data(), yl.data(), yr.data(), x.size());
        check(s->stats(0).nonconverged > 0 && s->stats(1).nonconverged == 0 && allfinite(yl.data(), yl.size()),
              "capped Newton: one lane falls back, the other converges");
        metric("stereo_capped_nonconv_l", double(s->stats(0).nonconverged));
        delete s;
    }
    stereo_bias_case("stereo bias move", TriodeMath::libm, LaneMath::per_lane);    // #73
    stereo_bias_case("vector bias move", TriodeMath::fast, LaneMath::vector);
    check(g_stereo_lanes_differ >= g_stereo_runs / 2 && g_vec_lanes_differ >= g_vec_runs / 2,
          "the lanes ran different Newton iteration counts");
    metric("stereo_runs", double(g_stereo_runs));
    metric("stereo_lanes_differ_runs", double(g_stereo_lanes_differ));
    metric("stereo_abs_worst", g_stereo_worst);
    metric("vector_runs", double(g_vec_runs));
    metric("vector_lanes_differ_runs", double(g_vec_lanes_differ));
    metric("vector_abs_worst", g_vec_worst);
    metric("stereo_process_allocations", double(g_stereo_allocs));
    check(g_stereo_allocs == 0, "StereoStage::process allocates nothing (" + std::to_string(g_stereo_allocs) + ")");
}

// solve2 against solve on two different problems per call: the vectors' hard problems (pnjlim, line search, caps)
// and random ones, each lane bit for bit (exact builds) or within TOL_SOLVE with the same iteration count
static void test_stereo_solve(const std::map<std::string, Vec>& V) {
    const Vec in = need(V, "solve.in"), Kv = need(V, "coef.standard.K");
    const double K[2][2] = {{Kv[0], Kv[1]}, {Kv[2], Kv[3]}};
    struct P { double p0, p1, v0, v1; Newton nw; };
    std::vector<P> rows;
    for (size_t i = 0; i + 7 <= in.size(); i += 7)
        rows.push_back(P{in[i], in[i + 1], in[i + 2], in[i + 3], Newton{int(in[i + 4]), in[i + 5], in[i + 6]}});
    const size_t nvec = rows.size();
    Coeffs c;
    build_coeffs(c, 384000.0);
    double pr0 = c.H[0][1] * c.HT, pr1 = c.H[1][1] * c.HT;           // p at rest: G x0 + H (0, HT)
    for (int j = 0; j < NX; ++j) { pr0 += c.G[0][j] * c.x0[j]; pr1 += c.G[1][j] * c.x0[j]; }
    std::uint32_t r = 21;
    auto uni = [&r](double a, double b) { r = r * 1664525u + 1013904223u; return a + (b - a) * double(r >> 8) / 16777216.0; };
    for (int i = 0; i < 4000; ++i)
        rows.push_back(P{pr0 + uni(-12, 3), pr1 + uni(-80, 80), uni(-3, 3), uni(0, 450), Newton{}});
    long pairs = 0, exact = 0, it_mismatch = 0;
    double ev = 0;
    for (size_t i = 0; i < rows.size(); ++i) {
        const P& a = rows[i];
        size_t j = (i * 37 + 11) % rows.size();                          // a partner with the same Newton settings
        while (rows[j].nw.max_it != a.nw.max_it || rows[j].nw.tol_v != a.nw.tol_v || rows[j].nw.tol_f != a.nw.tol_f) j = (j + 1) % rows.size();
        const P& b = rows[j];
        const Solution2 s = solve2<PerLane<LibmMath>>(simd::make(a.p0, b.p0), simd::make(a.p1, b.p1), K, simd::make(a.v0, b.v0),
                                             simd::make(a.v1, b.v1), DZ_12AX7_RSD1, a.nw);
        for (int l = 0; l < 2; ++l) {
            const P& q = l ? b : a;
            const Solution m = solve<LibmMath>(q.p0, q.p1, K, q.v0, q.v1, DZ_12AX7_RSD1, q.nw);
            const double g[4] = {simd::lane(s.v0, l), simd::lane(s.v1, l), simd::lane(s.ig, l), simd::lane(s.ia, l)};
            const bool same_it = s.it[l] == m.it && s.ok[l] == m.ok;
            exact += same_it && g[0] == m.v0 && g[1] == m.v1 && g[2] == m.ig && g[3] == m.ia;
            it_mismatch += !same_it;
            if (same_it && m.ok)
                ev = std::max({ev, std::fabs(g[0] - m.v0), std::fabs(g[1] - m.v1) / std::max(1.0, std::fabs(m.v1))});
            ++pairs;
        }
    }
    // the fast-math triode in the same kernel, on the random problems
    long fexact = 0, fn = 0;
    for (size_t i = nvec; i + 1 < rows.size(); i += 2) {
        const P &a = rows[i], &b = rows[i + 1];
        const Solution2 s = solve2<PerLane<FastMath>>(simd::make(a.p0, b.p0), simd::make(a.p1, b.p1), K, simd::make(a.v0, b.v0),
                                             simd::make(a.v1, b.v1), DZ_12AX7_RSD1, a.nw);
        for (int l = 0; l < 2; ++l) {
            const P& q = l ? b : a;
            const Solution m = solve<FastMath>(q.p0, q.p1, K, q.v0, q.v1, DZ_12AX7_RSD1, q.nw);
            fexact += s.it[l] == m.it && s.ok[l] == m.ok && simd::lane(s.v0, l) == m.v0 && simd::lane(s.v1, l) == m.v1 &&
                      simd::lane(s.ig, l) == m.ig && simd::lane(s.ia, l) == m.ia;
            ++fn;
        }
    }
    // the vector maths (#67) on every problem (the hard ones exercise pnjlim and the line search), against solve<FastMath>
    long vexact = 0, vn = 0, vit = 0;
    double vev = 0;
    for (size_t i = 0; i < rows.size(); ++i) {
        const P& a = rows[i];
        size_t j = (i * 37 + 11) % rows.size();
        while (rows[j].nw.max_it != a.nw.max_it || rows[j].nw.tol_v != a.nw.tol_v || rows[j].nw.tol_f != a.nw.tol_f) j = (j + 1) % rows.size();
        const P& b = rows[j];
        const Solution2 s = solve2<VecFastMath>(simd::make(a.p0, b.p0), simd::make(a.p1, b.p1), K, simd::make(a.v0, b.v0),
                                                simd::make(a.v1, b.v1), DZ_12AX7_RSD1, a.nw);
        for (int l = 0; l < 2; ++l) {
            const P& q = l ? b : a;
            const Solution m = solve<FastMath>(q.p0, q.p1, K, q.v0, q.v1, DZ_12AX7_RSD1, q.nw);
            const double g[4] = {simd::lane(s.v0, l), simd::lane(s.v1, l), simd::lane(s.ig, l), simd::lane(s.ia, l)};
            const bool same_it = s.it[l] == m.it && s.ok[l] == m.ok;
            vexact += same_it && g[0] == m.v0 && g[1] == m.v1 && g[2] == m.ig && g[3] == m.ia;
            vit += !same_it;
            if (same_it && m.ok)
                vev = std::max({vev, std::fabs(g[0] - m.v0), std::fabs(g[1] - m.v1) / std::max(1.0, std::fabs(m.v1))});
            ++vn;
        }
    }
    g_vec_exact = g_vec_exact && vexact == vn;
    metric("vector_solve_lanes", double(vn));
    metric("vector_solve_bit_equal_frac", double(vexact) / double(vn));
    metric("vector_solve_it_mismatch", double(vit));
    metric("vector_solve_v_err", vev);
    check(vev <= TOL_SOLVE && double(vit) <= TOL_ITER_FRAC * double(vn), "solve2<VecFastMath> lanes = solve<FastMath>");
    g_stereo_exact = g_stereo_exact && exact == pairs && fexact == fn;
    metric("stereo_solve_lanes", double(pairs));
    metric("stereo_solve_bit_equal_frac", double(exact) / double(pairs));
    metric("stereo_solve_fast_bit_equal_frac", double(fexact) / double(fn));
    metric("stereo_solve_it_mismatch", double(it_mismatch));
    metric("stereo_solve_v_err", ev);
    check(ev <= TOL_SOLVE && double(it_mismatch) <= TOL_ITER_FRAC * double(pairs), "solve2 lanes = solve");
}

static void test_stereo_misc() {
    // float I/O = the double path rounded to float; NaN input; an unprepared stage outputs zeros
    StereoStage* a = new StereoStage; StereoStage* b = new StereoStage;
    a->prepare(48000.0); b->prepare(48000.0);
    a->set_drive(1.0, 3.0); b->set_drive(1.0, 3.0);
    double xl[480], xr[480], yl[480], yr[480];
    float fl[480], fr[480], gl[480], gr[480];
    for (int i = 0; i < 480; ++i) {
        fl[i] = float(2.0 * std::sin(2 * PI * 1000.0 * i / 48000.0)); fr[i] = float(0.5 * std::sin(2 * PI * 300.0 * i / 48000.0));
        xl[i] = fl[i]; xr[i] = fr[i];
    }
    long allocs;
    { CountAllocs ca; a->process(xl, xr, yl, yr, 480); b->process(fl, fr, gl, gr, 480); allocs = ca.n(); }
    double e = 0;
    for (int i = 0; i < 480; ++i) e = std::max({e, std::fabs(double(gl[i]) - float(yl[i])), std::fabs(double(gr[i]) - float(yr[i]))});
    check(allocs == 0 && e == 0.0, "stereo float I/O = double path rounded to float, no allocation");
    // in place
    a->reset(); b->reset();
    double il[480], ir[480];
    std::copy(xl, xl + 480, il); std::copy(xr, xr + 480, ir);
    a->process(il, ir, il, ir, 480);
    b->process(xl, xr, yl, yr, 480);
    check(std::equal(il, il + 480, yl) && std::equal(ir, ir + 480, yr), "stereo in place");
    // Default is LaneMath::vector (used with TriodeMath::fast). With libm there is no vector libm, so the
    // per-lane reference still runs, bit for bit, including after an explicit switch to per_lane.
    a->reset(); b->reset();
    check(b->lane_math() == LaneMath::vector, "lane maths default is vector");
    a->set_lane_math(LaneMath::per_lane);
    check(a->lane_math() == LaneMath::per_lane && b->lane_math() == LaneMath::vector, "lane maths switch to per_lane");
    a->process(xl, xr, il, ir, 480);
    b->process(xl, xr, yl, yr, 480);
    check(std::equal(il, il + 480, yl) && std::equal(ir, ir + 480, yr), "LaneMath::vector with libm = the per-lane path");
    delete a; delete b;
    StereoStage* s = new StereoStage;
    check(s->prepare(48000.0, STANDARD, 1.0, TriodeMath::libm, Newton{1, 1e-6, 1e-6}) == BuildStatus::dc_nonconvergence &&
          !s->prepared(), "stereo prepare reports DC non-convergence");
    for (int i = 0; i < 480; ++i) yl[i] = yr[i] = 1.0;
    s->process(xl, xr, yl, yr, 480);
    check(maxabs(yl, 480) == 0.0 && maxabs(yr, 480) == 0.0, "an unprepared stereo stage outputs zeros");
    delete s;
#if defined(DKCORE_X86_SSE)
    const unsigned csr = _mm_getcsr();
    s = new StereoStage;
    s->prepare(48000.0);
    s->process(xl, xr, yl, yr, 16);
    check(_mm_getcsr() == csr, "StereoStage::process restores the caller's MXCSR");
    delete s;
#endif
}

// ---- vector maths (#67): VecFastMath against libm and against the scalar dk/fastmath.h ----------------------------
/* Accuracy, measured here against this machine's libm: max ulp and max relative error of each VecFastMath function on
 * dense grids over the arguments the stage actually passes (dk/fastmath_domain.json "ranges", every setting and drive
 * of dk.validate, extended; cpp/tests checks the cover), and of exp over its whole fast domain. Each lane is also
 * compared bit for bit with the scalar fm_* (g_vec_exact). The bounds are dk/fastmath.py BOUND (#18 / #60, the scalar
 * kernels' tested bounds): the vector code computes the same thing. Edges: special and out-of-domain values, mixed
 * with in-domain ones in the other lane, with and without FTZ: every lane must give the scalar fm_*'s bits (libm's
 * outside the fast domain; NaN for NaN). */
static const double BOUND_EXP = 1e-12, BOUND_LOG1P = 3e-13, BOUND_POW = 1e-12;   // dk/fastmath.py BOUND
struct Domain { const char* name; double lo, hi; bool log; };
// the grids, in the argument's own units; dk/fastmath_domain.json ranges, rounded outwards (cpp/tests checks it)
static const Domain VM_EXP = {"exp", -200.0, 1.0, false}, VM_EXP_FULL = {"exp_full", FM_EXP_LO, FM_EXP_HI, false},
                    VM_LOG1P = {"log1p", 1e-70, 1.0, true}, VM_POW126 = {"pow_g1_26", 1e-20, 2.0, true},
                    VM_POW1314 = {"pow_g1_314", 1e-70, 1.0, true};

static std::int64_t ordered(double x) {          // doubles -> integers in the same order, adjacent doubles 1 apart
    std::int64_t i; std::memcpy(&i, &x, 8);
    return i < 0 ? std::numeric_limits<std::int64_t>::min() - i : i;
}
static bool same_bits(double a, double b) { return std::memcmp(&a, &b, 8) == 0 || (std::isnan(a) && std::isnan(b)); }

// n points of d: half a regular grid (linear, or geometric if d.log), half pseudo-random (uniform, or log-uniform)
static Vec grid(const Domain& d, size_t n) {
    Vec g(n);
    std::uint64_t r = 67;
    for (size_t i = 0; i < n; ++i) {
        double t = double(i) / double(n / 2 - 1);
        if (i >= n / 2) { r = r * 6364136223846793005ull + 1442695040888963407ull; t = double(r >> 11) * 0x1p-53; }
        t = std::min(t, 1.0);
        g[i] = d.log ? std::exp(std::log(d.lo) + t * (std::log(d.hi) - std::log(d.lo))) : d.lo + t * (d.hi - d.lo);
    }
    g[0] = d.lo; g[n / 2 - 1] = d.hi;
    return g;
}

template <class FV, class FS, class FL>
static void vm_measure(const Domain& d, double bound, FV vec, FS scalar, FL libm) {
    const Vec g = grid(d, 400000);
    const size_t h = g.size() / 2;
    double rel = 0;
    std::int64_t ulp = 0;
    long eq = 0;
    for (size_t i = 0; i < h; ++i) {
        const simd::V2 y = vec(simd::make(g[i], g[i + h]));           // lanes from different halves
        for (int l = 0; l < 2; ++l) {
            const double x = g[i + h * size_t(l)], v = simd::lane(y, l), ref = libm(x);
            rel = std::max(rel, std::fabs(v - ref) / std::fabs(ref));
            ulp = std::max(ulp, std::abs(ordered(v) - ordered(ref)));
            eq += same_bits(v, scalar(x));
        }
    }
    const std::string k = std::string("vecmath_") + d.name;
    metric(k + "_lo", d.lo); metric(k + "_hi", d.hi);
    metric(k + "_max_rel", rel); metric(k + "_max_ulp", double(ulp));
    metric(k + "_bit_equal_frac", double(eq) / double(g.size()));
    g_vec_exact = g_vec_exact && eq == long(g.size());
    check(rel <= bound, k + " relative error " + std::to_string(rel));
}

static void test_vecmath() {
    using simd::V2;
    const auto vexp = [](V2 x) { return VecFastMath::exp(x); };
    const auto vlog1p = [](V2 x) { return VecFastMath::log1p(x); };
    const auto sexp = [](double x) { return fm_exp(x); };
    const auto slog1p = [](double x) { return fm_log1p(x); };
    const auto lexp = [](double x) { return std::exp(x); };
    const auto llog1p = [](double x) { return std::log1p(x); };
    vm_measure(VM_EXP, BOUND_EXP, vexp, sexp, lexp);
    vm_measure(VM_EXP_FULL, BOUND_EXP, vexp, sexp, lexp);
    vm_measure(VM_LOG1P, BOUND_LOG1P, vlog1p, slog1p, llog1p);
    for (double g : {1.26, 1.314})
        vm_measure(g == 1.26 ? VM_POW126 : VM_POW1314, BOUND_POW, [g](V2 s) { return VecFastMath::pow(s, g); },
                   [g](double s) { return fm_pow(s, g); }, [g](double s) { return std::pow(s, g); });
    check(DZ_12AX7_RSD1.gamma == 1.26 && DZ_12AX7_RSD1.xi == 1.314, "the pow exponents measured are the stage's");

    // edges: every pair of these, in both lanes, with and without FTZ / DAZ
    const double dmin = std::numeric_limits<double>::min(), dmax = std::numeric_limits<double>::max();
    const double sp[] = {NAN, -NAN, INFINITY, -INFINITY, 0.0, -0.0, 1e-310, -1e-310, dmin, std::nextafter(dmin, 0.0),
                         -dmin, dmax, -dmax, 1.0, std::nextafter(1.0, 2.0), std::nextafter(1.0, 0.0), -1.0, 0.5, 2.0,
                         -183.0, 0.04, 1e-60, 1e300, -1e-300, FM_EXP_LO, std::nextafter(FM_EXP_LO, -1e3), FM_EXP_HI,
                         std::nextafter(FM_EXP_HI, 1e3), 709.8, 710.0, -745.0, -746.0, -1e4};
    // a lane whose call left the fast path (either lane out of its domain) must give the scalar bits by construction;
    // an in-domain pair runs the vector code: bit-equal on the exact builds (g_vec_exact), else within the bound
    const double bnd[4] = {BOUND_EXP, BOUND_LOG1P, BOUND_POW, BOUND_POW};
    auto in_dom = [dmin](int f, double v) {
        return f == 0 ? v >= FM_EXP_LO && v <= FM_EXP_HI : f == 1 ? v > 0.0 && v <= 1.0 : v >= dmin && v < INFINITY;
    };
    long bad = 0, n = 0, vec_ne = 0;
    for (int ftz = 0; ftz < 2; ++ftz) {
        auto run = [&] {
            for (double a : sp)
                for (double b : sp) {
                    const V2 x = simd::make(a, b);
                    const V2 r[4] = {VecFastMath::exp(x), VecFastMath::log1p(x), VecFastMath::pow(x, 1.26), VecFastMath::pow(x, 1.314)};
                    for (int l = 0; l < 2; ++l) {
                        const double v = l ? b : a;
                        const double s[4] = {fm_exp(v), fm_log1p(v), fm_pow(v, 1.26), fm_pow(v, 1.314)};
                        for (int f = 0; f < 4; ++f) {
                            const double got = simd::lane(r[f], l);
                            const bool vec_path = in_dom(f, a) && in_dom(f, b), eq = same_bits(got, s[f]);
                            const bool ok = eq || (vec_path && std::fabs(got - s[f]) <= 2.0 * bnd[f] * std::fabs(s[f]));
                            bad += !ok; vec_ne += !eq; ++n;
                            if (!ok) std::fprintf(stderr, "vecmath edge f%d ftz%d lane %d: %a -> %a, scalar %a\n", f, ftz, l, v,
                                                  got, s[f]);
                        }
                    }
                }
        };
        if (ftz) { ScopedFlushDenormals g; run(); } else run();
    }
    // outside the fast domain fm_* is libm (no FTZ): so the vector path is too
    long bad_libm = 0;
    for (double v : sp) {
        const bool exp_in = v >= FM_EXP_LO && v <= FM_EXP_HI, log1p_in = v > 0.0 && v <= 1.0, pow_in = v >= dmin && v < INFINITY;
        const V2 x = simd::splat(v);
        if (!exp_in) bad_libm += !same_bits(simd::lane(VecFastMath::exp(x), 0), std::exp(v));
        if (!log1p_in) bad_libm += !same_bits(simd::lane(VecFastMath::log1p(x), 0), std::log1p(v));
        if (!pow_in) bad_libm += !same_bits(simd::lane(VecFastMath::pow(x, 1.26), 0), std::pow(v, 1.26));
    }
    g_vec_exact = g_vec_exact && vec_ne == 0;
    metric("vecmath_edge_cases", double(n)); metric("vecmath_edge_not_bit_equal", double(vec_ne));
    check(bad == 0 && bad_libm == 0, "vecmath edges = scalar fm_* (" + std::to_string(bad) + "), = libm outside the domain (" +
          std::to_string(bad_libm) + ")");

    // pnjlim on two lanes against the scalar one: limited and unlimited steps, both branches, mixed lanes
    const double vt = 1.0 / DZ_12AX7_RSD1.Cg;
    Vec vs;
    for (double v = -3.0; v <= 3.0; v += 0.0625) vs.push_back(v);
    for (double v : {-1e-3, 1e-3, vt, 2 * vt, 3 * vt, 10.0, 100.0, double(NAN), double(INFINITY), -double(INFINITY)}) vs.push_back(v);
    long pb = 0, pn = 0, limited = 0;
    double pe = 0;
    for (size_t i = 0; i < vs.size(); ++i)
        for (size_t j = 0; j < vs.size(); ++j) {
            const size_t i2 = (i * 7 + 3) % vs.size(), j2 = (j * 5 + 1) % vs.size();
            const V2 r = VecFastMath::pnjlim(simd::make(vs[i], vs[i2]), simd::make(vs[j], vs[j2]), vt);
            for (int l = 0; l < 2; ++l) {
                const double vn = l ? vs[i2] : vs[i], vo = l ? vs[j2] : vs[j], s = detail::pnjlim(vn, vo, vt);
                pb += !same_bits(simd::lane(r, l), s); ++pn;
                if (!std::isnan(s)) pe = std::max(pe, std::fabs(simd::lane(r, l) - s) / std::max(1.0, std::fabs(s)));
                limited += !same_bits(s, vn);
            }
        }
    metric("vecmath_pnjlim_cases", double(pn)); metric("vecmath_pnjlim_limited", double(limited));
    metric("vecmath_pnjlim_mismatch", double(pb));
    g_vec_exact = g_vec_exact && pb == 0;
    check(limited > pn / 10, "pnjlim cases include limited steps");
    check(pe <= 1e-14, "VecFastMath::pnjlim = detail::pnjlim (rounding only) " + std::to_string(pe));
}

// #137: the non-finite rule per channel. Each lane against a mono Stage given the same input and gains (compare_channel:
// output, Stats including recoveries, final state; bit-equal where the build guarantees it, g_stereo_exact /
// g_vec_exact). The left lane gets +-DBL_MAX, NaN / inf and an overflowing drive; the right lane a plain sine, which a
// recovery on the left must leave untouched. Then non-finite gains (ignored per channel) and an overflowing output
// gain on one channel.
static void test_stereo_nonfinite() {
    const size_t n = 2048;
    Vec xl(n), xr(n);
    for (size_t i = 0; i < n; ++i) {
        xl[i] = 0.8 * std::sin(2 * PI * 440.0 * double(i) / 48000.0);
        xr[i] = 2.0 * std::sin(2 * PI * 997.0 * double(i) / 48000.0);
    }
    xl[100] = DBL_MAX; xl[300] = -DBL_MAX; xl[500] = NAN; xl[501] = INFINITY;
    for (size_t i = 1024; i < 1032; ++i) xl[i] = 4.0;              // x the 1e308 drive below: overflows
    long allocs = 0;
    bool split = true;
    struct Way { const char* name; TriodeMath math; LaneMath lm; };
    for (const Way& wy : {Way{"per_lane libm", TriodeMath::libm, LaneMath::per_lane},
                          Way{"per_lane fast", TriodeMath::fast, LaneMath::per_lane},
                          Way{"vector fast", TriodeMath::fast, LaneMath::vector}})
        for (Quality q : {STANDARD, HQ}) {
            const bool vec = wy.lm == LaneMath::vector;
            Stage* ml = new Stage; Stage* mr = new Stage; StereoStage* s = new StereoStage;
            ml->prepare(48000.0, q, 1.0, wy.math); mr->prepare(48000.0, q, 1.0, wy.math);
            s->prepare(48000.0, q, 1.0, wy.math);
            s->set_lane_math(wy.lm);
            Vec yml(n), ymr(n), yl(n), yr(n);
            // the same sequence of blocks and gain changes on the stereo stage and on both mono stages
            auto blocks = [&](size_t a, size_t b) {
                ml->process(xl.data() + a, yml.data() + a, b - a);
                mr->process(xr.data() + a, ymr.data() + a, b - a);
                CountAllocs c;
                s->process(xl.data() + a, xr.data() + a, yl.data() + a, yr.data() + a, b - a);
                allocs += c.n();
            };
            blocks(0, 1024);
            ml->set_drive(1e308); s->set_drive(1e308, 1.0);            // the left lane's input overflows
            blocks(1024, 1032);
            ml->set_drive(1.0); s->set_drive(1.0, 1.0);
            ml->set_drive(NAN); s->set_drive(NAN, NAN);                // ignored, both channels
            mr->set_output(INFINITY); s->set_output(1.0, INFINITY);     // ignored on the right, 1 on the left
            blocks(1032, n);
            const std::string what = std::string("stereo recovery ") + wy.name + " os" + std::to_string(q.os);
            compare_channel(what + " L", yl, yml, *s, 0, *ml, vec);
            compare_channel(what + " R", yr, ymr, *s, 1, *mr, vec);
            split = split && s->stats(0).recoveries >= 1 && s->stats(1).recoveries == 0 &&
                    s->stats(0).nonfinite_inputs == 2 + 8 && s->stats(1).nonfinite_inputs == 0 &&
                    allfinite(yl.data(), n) && allfinite(yr.data(), n);
            delete ml; delete mr; delete s;
        }
    check(split, "stereo recovery: per channel (left recovered and counted, right untouched)");
    check(!ALLOC_HOOKS || allocs == 0, "stereo recovery: no allocation");

    // an overflowing output gain on one channel: 0 written and counted there, the other channel and both states as at
    // unity gain (float I/O, where 1e38 x the stage's volts is over FLT_MAX)
    StereoStage* a = new StereoStage; StereoStage* b = new StereoStage;
    a->prepare(48000.0); b->prepare(48000.0);
    a->set_output(1e38, 1.0);
    std::vector<float> fl(xr.begin(), xr.end()), fr(xr.begin(), xr.end()), al(n), ar(n), bl(n), br(n);
    a->process(fl.data(), fr.data(), al.data(), ar.data(), n);
    b->process(fl.data(), fr.data(), bl.data(), br.data(), n);
    bool ok = a->stats(0).nonfinite_outputs > 0 && a->stats(1).nonfinite_outputs == 0 && a->stats(0).recoveries == 0 &&
              std::equal(ar.begin(), ar.end(), br.begin());
    for (float v : al) ok = ok && std::isfinite(v);
    a->set_output(1.0, 1.0);
    a->process(fl.data(), fr.data(), al.data(), ar.data(), n);
    b->process(fl.data(), fr.data(), bl.data(), br.data(), n);
    ok = ok && std::equal(al.begin(), al.end(), bl.begin()) && std::equal(ar.begin(), ar.end(), br.begin());
    check(ok, "stereo: an overflowing output gain on one channel writes 0 there, counted; the states are untouched");
    delete a; delete b;
}

static void run_stereo_tests(const std::map<std::string, Vec>& V) {
    test_vecmath();
    test_stereo(V);
    test_stereo_solve(V);
    test_stereo_misc();
    test_stereo_nonfinite();
    g_json << (g_json.tellp() > 0 ? ", " : "") << "\"simd\": \"" << simd::NAME << "\", \"compiler\": \"" << __VERSION__ << "\"";
    metric("fma", simd::FMA ? 1 : 0);
    metric("stereo_exact", g_stereo_exact ? 1 : 0);
    metric("vector_exact", g_vec_exact ? 1 : 0);
}

// ---- bias smoothing (#73): set_bias() ramps along the bias line -------------------------------------------------
/* The reference ("continuous") is the same bias move with an exact pack (build_coeffs) at every internal sample. It
 * is a Stage run through process_internal() one internal sample at a time, publish()ing the pack for that sample's
 * bias first, with its own Resampler around it: the same up -> stage -> down as Stage::process, and equal to it bit
 * for bit at a constant bias (checked). The stage under test ramps linearly from b0 to b1 over L base samples, one
 * pack per base sample (b0 + k (b1 - b0) / L for the k-th, k = 1..L). The reference moves along the same straight
 * line per internal sample: b0 + (k - 1 + (j + 1) / os) (b1 - b0) / L at internal sample j of the k-th, so both reach
 * b1 at the same sample.
 * Click metric ("worst step"): max_n |r[n] - r[n-1]|, r = y - y_ref, base rate, V at the stage output: the step the
 * update method adds on top of what the continuous move does. The unsmoothed baseline is request_bias(b1) at the same
 * sample (one swap), against the same reference. Numbers and method: cpp/RESULTS-smoothing.md. */
static const double BIAS_RAMP_S = 0.020;
/* Limits, V at the stage output (the plugin's Output 1 maps 1 V to 1/64 of full scale: 1 mV = -96 dBFS). Set from the
 * worst values this test measured on an Apple M1 (Apple clang 17, -O2; RESULTS-smoothing.md), with 2x margin or more:
 * silence 0.119 mV (hq, 0.5 -> 2.0), with a sine 9.30 mV (hq, 8 V, 2.0 -> 0.5). The unsmoothed swap on the same 18
 * moves measured 61-108 mV in silence and 0.127-7.35 V with a sine. */
static const double LIMIT_STEP_SILENCE = 3e-4;     // V   (-107 dBFS at Output 1)
static const double LIMIT_STEP_SINE = 2e-2;        // V   (-70 dBFS at Output 1)
// the smoothed step must also be this much smaller than the unsmoothed one, every case (measured: >= 458x)
static const double MIN_STEP_REDUCTION = 200.0;

static Vec bias_sine(size_t n, double amp, double f) {
    Vec x(n);
    for (size_t i = 0; i < n; ++i) x[i] = amp * std::sin(2 * PI * f * double(i) / 48000.0);
    return x;
}

static Vec bias_reference(Quality q, double b0, double b1, size_t n0, int L, const Vec& x) {
    Stage* s = new Stage;
    s->prepare(48000.0, q, b0);
    Resampler r;
    r.prepare(q.os, 48000.0);
    Vec y(x.size());
    double u[MAX_OS], v[MAX_OS], last = b0;
    for (size_t i = 0; i < x.size(); ++i) {
        r.up(x[i], u);
        for (int j = 0; j < q.os; ++j) {
            double b = b0;
            if (i >= n0) {
                const double k = double(i - n0) + double(j + 1) / q.os;
                b = k >= L ? b1 : b0 + k * (b1 - b0) / L;
            }
            if (b != last) {
                Coeffs c;
                build_coeffs(c, 48000.0 * q.os, b, q.prewarp_hz);
                s->publish(c);
                last = b;
            }
            s->process_internal(&u[j], &v[j], nullptr, nullptr, 1);
        }
        y[i] = r.down(v);
    }
    delete s;
    return y;
}

// the stage under test: blocks of `block`, the move asked for at the block that starts at n0 (n0 % block == 0);
// step: request_bias(b1) there instead (the unsmoothed baseline)
static Vec bias_run(Quality q, double b0, double b1, size_t n0, const Vec& x, size_t block, bool step, long* allocs = nullptr,
                    Stage** keep = nullptr) {
    Stage* s = new Stage;
    s->prepare(48000.0, q, b0);
    s->set_bias_ramp(BIAS_RAMP_S);
    Vec y(x.size());
    long a = 0;
    for (size_t i = 0; i < x.size(); i += block) {
        if (i == n0) { if (step) s->request_bias(b1); else s->set_bias(b1); }
        const size_t m = std::min(block, x.size() - i);
        CountAllocs ca;
        s->process(x.data() + i, y.data() + i, m);
        a += ca.n();
    }
    if (allocs) *allocs += a;
    if (keep) *keep = s; else delete s;
    return y;
}

static double worst_step(const Vec& y, const Vec& r) {
    double w = 0.0;
    for (size_t i = 1; i < y.size(); ++i) w = std::max(w, std::fabs((y[i] - r[i]) - (y[i - 1] - r[i - 1])));
    return w;
}

static void test_bias_smoothing() {
    const int L = int(std::lround(BIAS_RAMP_S * 48000.0));
    const size_t n0 = 4800, N = 14400;           // move at 0.1 s, 0.3 s in all (the move and ~9 time constants of Cout)
    long allocs = 0;

    // 0. the reference harness equals Stage::process at a constant bias
    {
        const Vec x = bias_sine(4800, 1.0, 220.0);
        const Vec r = bias_reference(STANDARD, 1.0, 1.0, 0, 1, x), y = bias_run(STANDARD, 1.0, 1.0, 1u << 30, x, 64, false);
        check(r == y, "bias: continuous-reference harness == Stage::process at a constant bias");
    }

    // 1. the bias line: matrices at any bias == build_coeffs (TOL_COEF, the C++-vs-Python matrix tolerance), the
    //    recentred operating point within the Newton tolerance (tol_v = 1e-6 V)
    {
        double wm = 0.0, wop = 0.0, wx0 = 0.0;
        for (double b : {0.5, 0.6, 0.8, 1.0, 1.25, 1.37, 1.5, 1.9, 2.0}) {
            Stage* s = new Stage;
            s->prepare(48000.0, STANDARD, b == 1.0 ? 1.5 : 1.0);
            s->set_bias_ramp(0.0);
            s->set_bias(b);
            double z[64] = {}, o[64];
            s->process(z, o, 64);
            Coeffs e;
            build_coeffs(e, 48000.0 * 8, b);
            const Coeffs& c = s->active();
            double em = 0.0;
            auto cmp = [&](const double* p, const double* q, int n) { em = std::max(em, maxdiff(p, q, n) / std::max(maxabs(q, n), 1e-300)); };
            cmp(&c.A[0][0], &e.A[0][0], 36); cmp(&c.B[0][0], &e.B[0][0], 12); cmp(&c.C[0][0], &e.C[0][0], 12);
            cmp(&c.D[0][0], &e.D[0][0], 30); cmp(&c.E[0][0], &e.E[0][0], 10); cmp(&c.F[0][0], &e.F[0][0], 10);
            cmp(&c.G[0][0], &e.G[0][0], 12); cmp(&c.H[0][0], &e.H[0][0], 4); cmp(&c.K[0][0], &e.K[0][0], 4);
            wm = std::max(wm, em);
            wop = std::max({wop, std::fabs(c.op.vgk - e.op.vgk), std::fabs(c.op.vak - e.op.vak)});
            wx0 = std::max(wx0, maxdiff(c.x0, e.x0, NX) / maxabs(e.x0, NX));
            check(s->bias_now() == b && !s->bias_moving() && s->bias_unconverged() == 0, "bias line: jump lands on the target");
            delete s;
        }
        metric("bias_line_matrix_rel", wm);
        metric("bias_line_op_abs", wop);
        metric("bias_line_x0_rel", wx0);
        check(wm <= TOL_COEF, "bias line: matrices == build_coeffs within TOL_COEF");
        check(wop <= 1e-6 && wx0 <= 1e-6, "bias line: recentred operating point == build_coeffs within the Newton tolerance");
    }

    // 2. at rest at a bias reached by set_bias, the stage == a stage prepared there (TOL_BUILT_ABS: matrix rounding)
    {
        const Vec x = bias_sine(9600, 2.0, 220.0);
        Stage* a = new Stage; Stage* b = new Stage;
        a->prepare(48000.0, STANDARD, 1.0); a->set_bias_ramp(0.0); a->set_bias(1.37); a->reset();
        b->prepare(48000.0, STANDARD, 1.37);
        Vec ya(x.size()), yb(x.size());
        a->process(x.data(), ya.data(), x.size());
        b->process(x.data(), yb.data(), x.size());
        const double e = maxdiff(ya.data(), yb.data(), x.size());
        metric("bias_rest_vs_prepared_abs", e);
        check(e <= TOL_BUILT_ABS, "bias: set_bias(1.37) + reset() == prepare(1.37) within TOL_BUILT_ABS");
        delete a; delete b;
    }

    // 3. fast full-range moves, silence and sine, every quality: worst step vs the continuous reference
    double w_sil = 0.0, w_sine = 0.0, base_sil_min = 1e300, base_sine_min = 1e300, red_min = 1e300, diff_max = 0.0,
           peak8 = 0.0;
    const Quality qs[3] = {ECO, STANDARD, HQ};
    const char* qn[3] = {"eco", "standard", "hq"};
    for (int qi = 0; qi < 3; ++qi)
        for (int dir = 0; dir < 2; ++dir)
            for (double amp : {0.0, 1.0, 8.0}) {
                const double b0 = dir ? 2.0 : 0.5, b1 = dir ? 0.5 : 2.0;
                const Vec x = bias_sine(N, amp, 220.0);
                const Vec r = bias_reference(qs[qi], b0, b1, n0, L, x);
                const Vec y = bias_run(qs[qi], b0, b1, n0, x, 64, false, &allocs);
                const Vec ys = bias_run(qs[qi], b0, b1, n0, x, 64, true, &allocs);
                const double w = worst_step(y, r), ws = worst_step(ys, r);
                char name[96];
                std::snprintf(name, sizeof name, "bias_step_%s_%s_%gV", qn[qi], dir ? "down" : "up", amp);
                metric(name, w);
                metric(std::string(name) + "_unsmoothed", ws);
                (amp == 0.0 ? w_sil : w_sine) = std::max(amp == 0.0 ? w_sil : w_sine, w);
                (amp == 0.0 ? base_sil_min : base_sine_min) = std::min(amp == 0.0 ? base_sil_min : base_sine_min, ws);
                red_min = std::min(red_min, ws / w);
                diff_max = std::max(diff_max, maxdiff(y.data(), r.data(), N));
                if (amp == 8.0) peak8 = std::max(peak8, maxabs(r.data(), N));
            }
    metric("bias_step_worst_silence", w_sil);
    metric("bias_step_worst_sine", w_sine);
    metric("bias_step_unsmoothed_min_silence", base_sil_min);
    metric("bias_step_unsmoothed_min_sine", base_sine_min);
    metric("bias_step_reduction_min", red_min);
    metric("bias_move_maxdiff_vs_continuous", diff_max);
    metric("bias_move_out_peak_8V", peak8);
    check(w_sil <= LIMIT_STEP_SILENCE, "bias: worst step in silence <= LIMIT_STEP_SILENCE (" + std::to_string(w_sil) + " V)");
    check(w_sine <= LIMIT_STEP_SINE, "bias: worst step with a sine <= LIMIT_STEP_SINE (" + std::to_string(w_sine) + " V)");
    check(red_min >= MIN_STEP_REDUCTION, "bias: worst step >= MIN_STEP_REDUCTION x below the unsmoothed swap");

    // 4. block-size invariance: the move asked for at a sample common to blocks of 16 / 64 / 512 gives the same
    //    output bit for bit; a target that changes at every block start (a host automation curve sampled per block)
    //    differs only by when the curve was sampled (measured, RESULTS-smoothing.md)
    {
        const Vec x = bias_sine(19200, 2.0, 220.0);
        const Vec y16 = bias_run(STANDARD, 0.5, 2.0, 5120, x, 16, false, &allocs);
        const Vec y64 = bias_run(STANDARD, 0.5, 2.0, 5120, x, 64, false, &allocs);
        const Vec y512 = bias_run(STANDARD, 0.5, 2.0, 5120, x, 512, false, &allocs);
        check(y16 == y512 && y64 == y512, "bias: block sizes 16 / 64 / 512 give the same output (move at sample 5120)");
        auto curve = [&](size_t block, size_t late) {
            Stage* s = new Stage;
            s->prepare(48000.0, STANDARD, 0.5);
            s->set_bias_ramp(BIAS_RAMP_S);
            Vec y(x.size());
            for (size_t i = 0; i < x.size(); i += block) {     // 0.5 -> 2.0 linearly over 0.1 s from 0.1 s
                const double t = double(i < late ? 0 : i - late) / 48000.0;
                s->set_bias(t < 0.1 ? 0.5 : t >= 0.2 ? 2.0 : 0.5 + 1.5 * (t - 0.1) / 0.1);
                CountAllocs ca;
                s->process(x.data() + i, y.data() + i, std::min(block, x.size() - i));
                allocs += ca.n();
            }
            delete s;
            return y;
        };
        const Vec c16 = curve(16, 0), c64 = curve(64, 0), c512 = curve(512, 0), late = curve(16, 512);
        const double d = std::max(maxdiff(c16.data(), c512.data(), x.size()), maxdiff(c64.data(), c512.data(), x.size()));
        const double lag = maxdiff(c16.data(), late.data(), x.size()), pk = maxabs(c512.data(), x.size());
        metric("bias_curve_blocksize_maxdiff", d);
        metric("bias_curve_lag512_bound", lag);
        metric("bias_curve_blocksize_peak", pk);
        // tolerance: a host block of 512 reads the curve up to 511 samples late, which is the host's sampling and not
        // the smoother. Bound: the block-16 run against itself with the curve read 512 samples late
        // (plugin/tests/smoothing_test.cpp uses the same rule for all three parameters; RESULTS-smoothing.md)
        check(d <= lag, "bias: an automation curve at blocks 16 / 64 / 512 agrees within the 512-sample lag bound");
    }

    // 5. reset() during a ramp lands on the target at rest; a published pack ends a ramp and takes over; targets are
    //    clamped to the range
    {
        Stage* s = new Stage;
        s->prepare(48000.0, STANDARD, 1.0);
        const Vec x = bias_sine(256, 1.0, 220.0);
        Vec y(x.size());
        s->set_bias(2.0);
        s->process(x.data(), y.data(), 256);
        const bool moving = s->bias_moving();
        s->reset();
        check(moving && !s->bias_moving() && s->bias_now() == 2.0 && maxabs(s->state_x(), NX) == 0.0,
              "bias: reset() during a ramp jumps to the target, at rest");
        s->set_bias(0.5);
        s->process(x.data(), y.data(), 64);
        s->request_bias(1.25);
        s->process(x.data(), y.data(), 64);
        check(!s->bias_moving() && s->bias_now() == 1.25 && s->active().bias == 1.25, "bias: a published pack ends the ramp");
        s->set_bias(9.0);
        s->process(x.data(), y.data(), 256);
        check(s->bias_moving() && s->bias_target() == 9.0, "bias: a target outside the range moves");
        s->reset();
        check(s->bias_now() == 2.0, "bias: ... and is clamped to the range (2.0)");
        delete s;
    }
    metric("bias_process_allocations", double(allocs));
    check(allocs == 0, "bias smoothing: process() allocates nothing (" + std::to_string(allocs) + ")");
}

// StereoStage with a bias move against two mono Stages with the same move (the compare_channel rule: bit-equal on the
// exact builds, else TOL_STEREO_ABS)
static void stereo_bias_case(const std::string& what, TriodeMath math, LaneMath lm) {
    const size_t N = 9600;
    const Vec xl = bias_sine(N, 1.0, 220.0), xr = bias_sine(N, 8.0, 330.0);
    Stage* ml = new Stage; Stage* mr = new Stage; StereoStage* s = new StereoStage;
    for (Stage* m : {ml, mr}) { m->prepare(48000.0, STANDARD, 0.5, math); m->set_bias_ramp(BIAS_RAMP_S); }
    s->prepare(48000.0, STANDARD, 0.5, math);
    s->set_bias_ramp(BIAS_RAMP_S);
    s->set_lane_math(lm);
    Vec yl(N), yr(N), yml(N), ymr(N);
    long a = 0;
    for (size_t i = 0; i < N; i += 64) {
        if (i == 1920) { ml->set_bias(2.0); mr->set_bias(2.0); s->set_bias(2.0); }
        if (i == 4480) { ml->set_bias(1.1); mr->set_bias(1.1); s->set_bias(1.1); }   // re-planned mid-ramp
        ml->process(xl.data() + i, yml.data() + i, 64);
        mr->process(xr.data() + i, ymr.data() + i, 64);
        CountAllocs ca;
        s->process(xl.data() + i, xr.data() + i, yl.data() + i, yr.data() + i, 64);
        a += ca.n();
    }
    compare_channel(what + " L", yl, yml, *s, 0, *ml, lm == LaneMath::vector && math == TriodeMath::fast);
    compare_channel(what + " R", yr, ymr, *s, 1, *mr, lm == LaneMath::vector && math == TriodeMath::fast);
    check(s->bias_now() == 1.1 && s->bias_updates() == ml->bias_updates() && a == 0, what + " bias state, no allocation");
    g_stereo_allocs += a;
    delete ml; delete mr; delete s;
}

// ---- cli ---------------------------------------------------------------------------------------------------
static void json_mat(std::FILE* f, const char* k, const double* a, int n) {
    std::fprintf(f, "\"%s\": [", k);
    for (int i = 0; i < n; ++i) std::fprintf(f, "%s%.17g", i ? ", " : "", a[i]);
    std::fprintf(f, "]");
}

static std::vector<double> read_stdin() {
    std::vector<double> in;
    double buf[4096];
    size_t got;
    while ((got = std::fread(buf, sizeof(double), 4096, stdin)) > 0) in.insert(in.end(), buf, buf + got);
    return in;
}

// stdin rows (p0, p1, v0, v1, K00, K01, K10, K11, max_it, tol_v, tol_f) -> (Vgk, Vak, Ig, Ia, it, ok) per row
static int cli_solve() {
    const std::vector<double> in = read_stdin();
    for (size_t i = 0; i + 11 <= in.size(); i += 11) {
        const double* r = &in[i];
        const double K[2][2] = {{r[4], r[5]}, {r[6], r[7]}};
        const Solution s = solve<LibmMath>(r[0], r[1], K, r[2], r[3], DZ_12AX7_RSD1, Newton{int(r[8]), r[9], r[10]});
        const double o[6] = {s.v0, s.v1, s.ig, s.ia, double(s.it), s.ok ? 1.0 : 0.0};
        std::fwrite(o, sizeof(double), 6, stdout);
    }
    return 0;
}

// stdin pairs (Vgk, Vak) -> currents libm (6), currents_only libm (2), currents fast (6) per pair
static int cli_triode() {
    const std::vector<double> in = read_stdin();
    for (size_t i = 0; i + 2 <= in.size(); i += 2) {
        Currents a, f;
        double o[14];
        Triode<LibmMath>::currents(in[i], in[i + 1], DZ_12AX7_RSD1, a);
        Triode<LibmMath>::currents_only(in[i], in[i + 1], DZ_12AX7_RSD1, o[6], o[7]);
        Triode<FastMath>::currents(in[i], in[i + 1], DZ_12AX7_RSD1, f);
        const double v[6] = {a.ig, a.ia, a.j00, a.j01, a.j10, a.j11}, w[6] = {f.ig, f.ia, f.j00, f.j01, f.j10, f.j11};
        std::copy(v, v + 6, o); std::copy(w, w + 6, o + 8);
        std::fwrite(o, sizeof(double), 14, stdout);
    }
    return 0;
}

static int cli(int argc, char** argv) {
    if (argc < 8) { std::fprintf(stderr, "usage: test_core cli OS PREWARP BIAS DRIVE OUTPUT MATH [vgk] [fs=HZ]\n"); return 2; }
    const int os = std::atoi(argv[2]);
    const double pw = std::atof(argv[3]), bias = std::atof(argv[4]), dr = std::atof(argv[5]), og = std::atof(argv[6]);
    const TriodeMath math = std::string(argv[7]) == "fast" ? TriodeMath::fast : TriodeMath::libm;
    bool rec_vgk = false;
    double fs_base = 48000.0;
    for (int i = 8; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (arg == "vgk") rec_vgk = true;
        else if (arg.rfind("fs=", 0) == 0) fs_base = std::atof(argv[i] + 3);
        else return 2;
    }
    const std::vector<double> in = read_stdin();
    Stage* s = new Stage;
    const BuildStatus st = s->prepare(fs_base, Quality{os, pw}, bias, math);
    if (st != BuildStatus::ok) { std::fprintf(stderr, "{\"status\": \"%s\"}\n", to_string(st)); return 3; }
    s->set_drive(dr); s->set_output(og);
    std::vector<double> out(in.size()), vgk;
    if (rec_vgk) vgk.resize(in.size() * os);
    s->process(in.data(), out.data(), in.size(), rec_vgk ? vgk.data() : nullptr);
    std::fwrite(out.data(), sizeof(double), out.size(), stdout);
    if (rec_vgk) std::fwrite(vgk.data(), sizeof(double), vgk.size(), stdout);
    const Coeffs& c = s->active();
    const Stats& S = s->stats();
    std::FILE* f = stderr;
    std::fprintf(f, "{\"status\": \"ok\", \"fs\": %.17g, \"os\": %d, \"prewarp_hz\": %.17g, \"bias\": %.17g, ", c.fs, os, c.prewarp_hz, c.bias);
    std::fprintf(f, "\"latency_samples\": %d, \"up_delay_internal\": %d, \"macs\": %d, ", s->latency_samples(),
                 s->up_delay_internal(), s->resampler().macs());
    std::fprintf(f, "\"op\": {\"vgk\": %.17g, \"vak\": %.17g, \"ig\": %.17g, \"ia\": %.17g, ", c.op.vgk, c.op.vak, c.op.ig, c.op.ia);
    json_mat(f, "nodes", c.op.nodes, NNODES); std::fprintf(f, "}, ");
    json_mat(f, "x0", c.x0, NX); std::fprintf(f, ", ");
    json_mat(f, "A", &c.A[0][0], 36); std::fprintf(f, ", "); json_mat(f, "B", &c.B[0][0], 12); std::fprintf(f, ", ");
    json_mat(f, "C", &c.C[0][0], 12); std::fprintf(f, ", "); json_mat(f, "D", &c.D[0][0], 30); std::fprintf(f, ", ");
    json_mat(f, "E", &c.E[0][0], 10); std::fprintf(f, ", "); json_mat(f, "F", &c.F[0][0], 10); std::fprintf(f, ", ");
    json_mat(f, "G", &c.G[0][0], 12); std::fprintf(f, ", "); json_mat(f, "H", &c.H[0][0], 4); std::fprintf(f, ", ");
    json_mat(f, "K", &c.K[0][0], 4); std::fprintf(f, ", ");
    std::fprintf(f, "\"stats\": {\"samples\": %" PRIu64 ", \"iterations\": %" PRIu64 ", \"nonconverged\": %" PRIu64
                 ", \"max\": %d, \"nonfinite_inputs\": %" PRIu64 ", \"histogram\": {", S.samples, S.iterations,
                 S.nonconverged, S.max_iterations, S.nonfinite_inputs);
    bool first = true;
    for (int i = 0; i < Stats::HIST; ++i)
        if (S.histogram[i]) { std::fprintf(f, "%s\"%d\": %" PRIu64, first ? "" : ", ", i, S.histogram[i]); first = false; }
    std::fprintf(f, "}}}\n");
    delete s;
    return 0;
}

int main(int argc, char** argv) {
    if (argc > 1 && std::string(argv[1]) == "fir-geometry") {
        double fs; int os;
        while (std::scanf("%lf %d", &fs, &os) == 2) {
            Resampler r;
            if (!r.prepare(os, fs)) return 2;
            std::printf("{\"first_taps\":%d,\"latency\":%d,\"pad\":%d,\"up_delay\":%d,\"macs\":%d}\n",
                        first_fir_taps(fs), r.latency_samples(), r.pad(), r.up_delay_internal(), r.macs());
        }
        return 0;
    }

    if (argc > 1 && std::string(argv[1]) == "cli") return cli(argc, argv);
    if (argc > 1 && std::string(argv[1]) == "solve") return cli_solve();
    if (argc > 1 && std::string(argv[1]) == "triode") return cli_triode();
    if (argc > 1 && std::string(argv[1]) == "swap") {      // the threaded swap test alone (for -fsanitize=thread)
        test_swap_threads();
        std::printf("{%s, \"failures\": %d}\n", g_json.str().c_str(), g_fail);
        return g_fail ? 1 : 0;
    }
    if (argc > 1 && std::string(argv[1]) == "stereo") {    // the stereo parity tests alone (cpp/tests, several builds)
        run_stereo_tests(load(argc > 2 ? argv[2] : "vectors.txt"));
        metric("alloc_hooks", ALLOC_HOOKS ? 1 : 0);
        std::printf("{%s, \"failures\": %d}\n", g_json.str().c_str(), g_fail);
        return g_fail ? 1 : 0;
    }
    if (argc > 1 && std::string(argv[1]) == "rates") {
        test_sample_rates();
        metric("failures", g_fail);
        std::printf("{%s}\n", g_json.str().c_str());
        return g_fail ? 1 : 0;
    }
    const std::string path = argc > 1 ? argv[1] : "vectors.txt";
    const auto V = load(path);
    test_taps(V);
    test_coeffs(V);
    test_triode(V);
    test_solver(V);
    test_resampler(V);
    test_internal(V);
    test_cases(V);
    test_nonconvergence();
    test_nonfinite_recovery();
    test_silence();
    test_sample_rates();
    test_denormals();
    test_swap_threads();
    test_float_io();
    test_bias_smoothing();
    run_stereo_tests(V);
    metric("alloc_hooks", ALLOC_HOOKS ? 1 : 0);
    metric("failures", g_fail);
    std::printf("{%s}\n", g_json.str().c_str());
    return g_fail ? 1 : 0;
}
