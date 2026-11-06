/* Tests for cpp/dk_core.h (#19) against the Python DK stage's vectors (cpp/vectors.txt), plus the realtime
 * properties: no allocation in process(), FTZ/DAZ scoping, the lock-free coefficient swap and the #7 fallback.
 *
 *   test_core [vectors.txt]        run every test; prints one JSON line of measured errors; exit 1 on any failure
 *   test_core cli OS PREWARP BIAS DRIVE OUTPUT MATH [vgk]
 *                                  stdin: float64 base-rate input; stdout: float64 output (then, with "vgk", the
 *                                  internal-rate Vgk); stderr: one JSON line (pack, op point, latency, stats).
 *                                  Used by cpp/tests/test_cpp_core.py to run dk.validate on the core.
 *   test_core bench                ns per base-rate sample of process() (cpp/README.md)
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
#include <cinttypes>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <new>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using namespace dkcore;

// ---- allocation counting --------------------------------------------------------------------------------------
static std::atomic<bool> g_count{false};
static std::atomic<long> g_allocs{0};

#if !defined(DKCORE_NO_ALLOC_HOOKS)
#if defined(__GLIBC__)
extern "C" {
void* __libc_malloc(size_t);
void* __libc_calloc(size_t, size_t);
void* __libc_realloc(void*, size_t);
void __libc_free(void*);
void* __libc_memalign(size_t, size_t);
void* malloc(size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_malloc(n); }
void* calloc(size_t a, size_t b) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_calloc(a, b); }
void* realloc(void* p, size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_realloc(p, n); }
void free(void* p) { if (p && g_count.load(std::memory_order_relaxed)) ++g_allocs; __libc_free(p); }
void* memalign(size_t a, size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_memalign(a, n); }
void* aligned_alloc(size_t a, size_t n) { return memalign(a, n); }
int posix_memalign(void** p, size_t a, size_t n) { *p = memalign(a, n); return *p ? 0 : 12; }
}
#define DK_RAW_ALLOC(n) __libc_malloc(n)
#define DK_RAW_ALIGNED(a, n) __libc_memalign(a, n)
#define DK_RAW_FREE(p) __libc_free(p)
#else
#define DK_RAW_ALLOC(n) std::malloc(n)
#define DK_RAW_ALIGNED(a, n) std::aligned_alloc(a, ((n) + (a) - 1) / (a) * (a))
#define DK_RAW_FREE(p) std::free(p)
#endif
static void* dk_new(size_t n) {
    if (g_count.load(std::memory_order_relaxed)) ++g_allocs;
    void* p = DK_RAW_ALLOC(n ? n : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
static void* dk_new_al(size_t n, std::align_val_t a) {
    if (g_count.load(std::memory_order_relaxed)) ++g_allocs;
    void* p = DK_RAW_ALIGNED(size_t(a), n ? n : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
static void dk_del(void* p) {
    if (p && g_count.load(std::memory_order_relaxed)) ++g_allocs;
    DK_RAW_FREE(p);
}
void* operator new(size_t n) { return dk_new(n); }
void* operator new[](size_t n) { return dk_new(n); }
void* operator new(size_t n, const std::nothrow_t&) noexcept { try { return dk_new(n); } catch (...) { return nullptr; } }
void* operator new[](size_t n, const std::nothrow_t&) noexcept { try { return dk_new(n); } catch (...) { return nullptr; } }
void* operator new(size_t n, std::align_val_t a) { return dk_new_al(n, a); }
void* operator new[](size_t n, std::align_val_t a) { return dk_new_al(n, a); }
void operator delete(void* p) noexcept { dk_del(p); }
void operator delete[](void* p) noexcept { dk_del(p); }
void operator delete(void* p, size_t) noexcept { dk_del(p); }
void operator delete[](void* p, size_t) noexcept { dk_del(p); }
void operator delete(void* p, std::align_val_t) noexcept { dk_del(p); }
void operator delete[](void* p, std::align_val_t) noexcept { dk_del(p); }
void operator delete(void* p, size_t, std::align_val_t) noexcept { dk_del(p); }
void operator delete[](void* p, size_t, std::align_val_t) noexcept { dk_del(p); }
static constexpr bool ALLOC_HOOKS = true;
#else
static constexpr bool ALLOC_HOOKS = false;
#endif

struct CountAllocs {          // counts allocations in its scope (single-threaded sections only)
    long start;
    CountAllocs() : start(g_allocs.load()) { g_count = true; }
    ~CountAllocs() { g_count = false; }
    long n() const { return g_allocs.load() - start; }
};

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
    for (int nt : {FIR_FIRST_TAPS, FIR_LATER_TAPS}) {
        double h[FIR_FIRST_TAPS];
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
    for (int os : {4, 8, 16}) {
        const std::string p = "rs" + std::to_string(os) + ".";
        Resampler r;
        check(r.prepare(os), p + "prepare");
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
    r.prepare(8);   // dk.resample.Resampler(48000, 8): pad 6, latency 72, up delay 285, macs 143 (dk/RESULTS.md)
    check(r.pad() == 6 && r.latency_samples() == 72 && r.up_delay_internal() == 285 && r.macs() == 143, "resampler 8x geometry");
    check(!r.prepare(3) && !r.prepare(32), "os must be 2, 4, 8 or 16");
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

// Runs one case on a set-up stage: the cfg's runtime Newton settings and the optional swap go through publish /
// request_bias, as a host would (pinned: the swap publishes the Python pack for the new bias). Returns allocations
// counted inside process().
static long run_case(Stage& s, const std::map<std::string, Vec>& V, const CaseCfg& k, bool pinned, const Vec& x,
                     Vec& y, size_t block) {
    Coeffs c = s.latest();
    c.newton = k.nw;
    s.publish(c);
    s.set_drive(k.drive); s.set_output(k.output);
    long allocs = 0;
    y.assign(x.size(), 0.0);
    const size_t at = k.swap_at >= 0 ? size_t(k.swap_at) : x.size();
    auto blocks = [&](size_t a, size_t b) {
        for (size_t i = a; i < b; i += block) {
            const size_t m = std::min(block, b - i);
            CountAllocs ca;
            s.process(x.data() + i, y.data() + i, m);
            allocs += ca.n();
        }
    };
    blocks(0, at);
    if (at < x.size()) {
        Coeffs c2;
        if (pinned) c2 = pinned_pack(V, coef_name(k.os, k.swap_bias), c.math);
        else { if (k.swap_bias != s.latest().bias) s.request_bias(k.swap_bias); c2 = s.latest(); }
        c2.newton = k.nw2;
        s.publish(c2);
        blocks(at, x.size());
    }
    return allocs;
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

static void test_denormals() {
    if (!ScopedFlushDenormals::active()) { metric("ftz_tested", 0); return; }
    volatile double tiny = 1e-310, one = 1.0;           // a double denormal
    const double before = tiny * one;
    double inside;
    { ScopedFlushDenormals g; inside = tiny * one; }
    const double after = tiny * one;
    check(before != 0.0 && inside == 0.0 && after != 0.0, "ScopedFlushDenormals flushes in scope and restores");
#if defined(DKCORE_X86_SSE)
    const unsigned csr = _mm_getcsr();
    Stage* s = new Stage;
    s->prepare(48000.0);
    double x[16] = {}, y[16];
    s->process(x, y, 16);
    check(_mm_getcsr() == csr, "process() restores the caller's MXCSR");
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

// ---- cli / bench ---------------------------------------------------------------------------------------------
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
    if (argc < 8) { std::fprintf(stderr, "usage: test_core cli OS PREWARP BIAS DRIVE OUTPUT MATH [vgk]\n"); return 2; }
    const int os = std::atoi(argv[2]);
    const double pw = std::atof(argv[3]), bias = std::atof(argv[4]), dr = std::atof(argv[5]), og = std::atof(argv[6]);
    const TriodeMath math = std::string(argv[7]) == "fast" ? TriodeMath::fast : TriodeMath::libm;
    const bool rec_vgk = argc > 8 && std::string(argv[8]) == "vgk";
    const std::vector<double> in = read_stdin();
    Stage* s = new Stage;
    const BuildStatus st = s->prepare(48000.0, Quality{os, pw}, bias, math);
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

static int bench() {
    const int N = 48000, BLOCK = 64, REPEAT = 7;
    std::vector<double> x(N), y(N);
    std::printf("{\"method\": \"process() on 1 s of a 1 kHz sine at 48 kHz in blocks of %d, median of %d runs after one "
                "warm-up, steady_clock; ns per base-rate sample\"", BLOCK, REPEAT);
    const std::pair<const char*, Quality> qs[] = {{"hq", HQ}, {"standard", STANDARD}, {"eco", ECO}};
    for (const auto& q : qs)
        for (TriodeMath m : {TriodeMath::libm, TriodeMath::fast})
            for (double a : {2.0, 8.0}) {
                for (int i = 0; i < N; ++i) x[i] = a * std::sin(2 * PI * 1000.0 * i / 48000.0);
                Stage* s = new Stage;
                s->prepare(48000.0, q.second, 1.0, m);
                std::vector<double> t;
                for (int r = 0; r <= REPEAT; ++r) {
                    s->reset();
                    const auto t0 = std::chrono::steady_clock::now();
                    for (int i = 0; i < N; i += BLOCK) s->process(&x[i], &y[i], BLOCK);
                    const auto t1 = std::chrono::steady_clock::now();
                    if (r) t.push_back(std::chrono::duration<double, std::nano>(t1 - t0).count() / N);
                }
                std::sort(t.begin(), t.end());
                const double it = double(s->stats().iterations) / double(s->stats().samples);
                std::printf(", \"%s_%s_%gV\": {\"ns_per_sample\": %.1f, \"realtime_x\": %.1f, \"newton_mean\": %.3f}",
                            q.first, m == TriodeMath::fast ? "fast" : "libm", a, t[t.size() / 2], 1e9 / 48000.0 / t[t.size() / 2], it);
                delete s;
            }
    std::printf("}\n");
    return 0;
}

int main(int argc, char** argv) {
    if (argc > 1 && std::string(argv[1]) == "cli") return cli(argc, argv);
    if (argc > 1 && std::string(argv[1]) == "bench") return bench();
    if (argc > 1 && std::string(argv[1]) == "solve") return cli_solve();
    if (argc > 1 && std::string(argv[1]) == "triode") return cli_triode();
    if (argc > 1 && std::string(argv[1]) == "swap") {      // the threaded swap test alone (for -fsanitize=thread)
        test_swap_threads();
        std::printf("{%s, \"failures\": %d}\n", g_json.str().c_str(), g_fail);
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
    test_denormals();
    test_swap_threads();
    test_float_io();
    metric("alloc_hooks", ALLOC_HOOKS ? 1 : 0);
    metric("failures", g_fail);
    std::printf("{%s}\n", g_json.str().c_str());
    return g_fail ? 1 : 0;
}
