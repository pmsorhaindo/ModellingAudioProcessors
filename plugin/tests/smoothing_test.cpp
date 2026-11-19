/* smoothing_test.cpp -- parameter smoothing through mapvs::Engine (#73), stereo, against references built from the
 * direct core. Method and numbers: prototypes/fullstage/cpp/RESULTS-smoothing.md.
 *
 *   1. Fast full-range moves of Drive, Output and Bias (host steps min -> max and max -> min at a block start),
 *      during a sine and during silence. Worst step = max_n |r[n] - r[n-1]|, r = engine output - continuous
 *      reference, full-scale units, both channels. Continuous reference:
 *        Drive / Output: the direct StereoStage with the same 20 ms linear ramp evaluated per sample, in double.
 *        Bias: an exact pack (build_coeffs) at every internal sample along the same 20 ms line, per channel
 *              (mono Stage + its own Resampler, as in cpp/test_core.cpp test_bias_smoothing).
 *      The unsmoothed baseline (an instant gain jump; one request_bias() swap) is measured against the same
 *      reference and printed beside it.
 *   2. Block-size invariance at host blocks of 16 / 64 / 512: all three parameters stepped at a sample common to the
 *      three block sizes (bit-exact), and a host automation curve sampled per block (stated tolerance).
 *   3. Drive and aliasing: the engine's drive ramp runs per host sample on the input, before the stage and so before
 *      its upsampler. It is compared with the same ramp applied per internal sample after the upsampler (the
 *      alternative the issue raises); the difference, in dB re the output, bounds any aliasing the base-rate ramp adds.
 *   4. No allocation in process() throughout.
 * Prints one line per check; exit status 0 if all pass.
 */
#include "../tests/null_common.h"

#include <algorithm>
#include <atomic>
#include <new>

static thread_local bool g_armed = false;
static std::atomic<long> g_allocs{0};
void* operator new(std::size_t n) {
    if (g_armed) g_allocs.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void* operator new[](std::size_t n) { return operator new(n); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
void operator delete[](void* p, std::size_t) noexcept { std::free(p); }

using Vec = std::vector<double>;
static const double FS = 48000.0;
static const int RAMP = 960;                         // 20 ms at 48 kHz: the engine's kRampSeconds
static const std::size_t N0 = 5120;                  // the move: a multiple of 16, 64, 256 and 512
static const std::size_t NS = 14400;                 // 0.3 s

/* Limits (full scale at the engine output). Bias: the core limits of cpp/test_core.cpp (3e-4 V silence, 2e-2 V with
 * a sine, at the stage output) through the plugin's 1/64 FS per volt. Drive / Output: the engine's ramps are the
 * reference ramps, so only float output rounding remains; the limit is 1e-6 FS (-120 dBFS); measured 0 on an Apple M1 (bit-equal
 * to the reference ramps, RESULTS-smoothing.md). Every move must also beat the unsmoothed jump by MIN_REDUCTION. */
static const double LIMIT_BIAS_SILENCE = 3e-4 * mapvs::kFullScalePerVolt;
static const double LIMIT_BIAS_SINE = 2e-2 * mapvs::kFullScalePerVolt;
static const double LIMIT_GAIN = 1e-6;
static const double MIN_REDUCTION = 30.0;

static int g_fail = 0;
static void check(bool ok, const std::string& what, const std::string& detail = "") {
    std::printf("%s  %s%s%s\n", ok ? "ok  " : "FAIL", what.c_str(), detail.empty() ? "" : ": ", detail.c_str());
    if (!ok) ++g_fail;
}
static std::string fmt(const char* f, double a, double b = 0.0, double c = 0.0, double d = 0.0) {
    char buf[256];
    std::snprintf(buf, sizeof buf, f, a, b, c, d);
    return buf;
}

struct Signal { std::vector<float> L, R; };
static Signal sines(double amp) {                    // L 220 Hz, R 330 Hz at 0.7 x amp: the channels differ
    Signal s;
    s.L.resize(NS);
    s.R.resize(NS);
    for (std::size_t i = 0; i < NS; ++i) {
        s.L[i] = float(amp * std::sin(2 * dkcore::PI * 220.0 * double(i) / FS));
        s.R[i] = float(0.7 * amp * std::sin(2 * dkcore::PI * 330.0 * double(i) / FS));
    }
    return s;
}

struct Params { double drive = 1.0, bias = 1.0, output = 1.0; };
using Curve = Params (*)(std::size_t, const Params&, const Params&);
static Params step_at_n0(std::size_t i, const Params& a, const Params& b) { return i >= N0 ? b : a; }

// The engine at host blocks of `block`; the host sets the parameters curve(block start) before each block.
static void run_engine(mapvs::Engine& e, const Signal& x, const Params& a, const Params& b, Curve curve,
                       std::size_t block, Vec& oL, Vec& oR) {
    e.set_drive(a.drive); e.set_bias(a.bias); e.set_output(a.output);
    e.set_quality(mapvs::kStandard);
    e.set_bypass(false);
    e.prepare(FS);
    std::vector<float> fl(NS), fr(NS);
    for (std::size_t i = 0; i < NS; i += block) {
        const Params p = curve(i, a, b);
        e.set_drive(p.drive); e.set_bias(p.bias); e.set_output(p.output);
        const int n = int(std::min(block, NS - i));
        const float* in[2] = {x.L.data() + i, x.R.data() + i};
        float* out[2] = {fl.data() + i, fr.data() + i};
        g_armed = true;
        e.process(in, out, 2, n);
        g_armed = false;
    }
    e.release();
    oL.assign(fl.begin(), fl.end());
    oR.assign(fr.begin(), fr.end());
}

// the ideal 20 ms linear ramp from a to b starting at N0 (len 1: the unsmoothed jump), per sample
static double ramp_at(std::size_t i, double a, double b, int len) {
    if (i < N0) return a;
    const std::size_t k = i - N0 + 1;
    return k >= std::size_t(len) ? b : a + double(k) * (b - a) / double(len);
}

// Drive / Output reference: the direct core (gains 1) with the per-sample ramps, then rounded to float like the engine
static void gain_reference(const Signal& x, const Params& a, const Params& b, int len, Vec& oL, Vec& oR) {
    dkcore::StereoStage s;
    s.prepare(FS, dkcore::STANDARD, a.bias);
    Vec il(NS), ir(NS), yl(NS), yr(NS);
    for (std::size_t i = 0; i < NS; ++i) {
        const double d = ramp_at(i, a.drive, b.drive, len) * mapvs::kVoltsPerFullScale;
        il[i] = double(x.L[i]) * d;
        ir[i] = double(x.R[i]) * d;
    }
    s.process(il.data(), ir.data(), yl.data(), yr.data(), NS);
    oL.resize(NS);
    oR.resize(NS);
    for (std::size_t i = 0; i < NS; ++i) {
        const double g = ramp_at(i, a.output, b.output, len) * mapvs::kFullScalePerVolt;
        oL[i] = double(float(g * yl[i]));
        oR[i] = double(float(g * yr[i]));
    }
}

// Bias reference (one channel): an exact pack at every internal sample along the ramp; drive / output constant.
// step: request_bias(b1) at N0 instead (the unsmoothed swap).
static Vec bias_reference(const std::vector<float>& xf, double drive, double output, double b0, double b1, bool step) {
    const dkcore::Quality q = dkcore::STANDARD;
    auto* s = new dkcore::Stage;
    s->prepare(FS, q, b0);
    dkcore::Resampler r;
    r.prepare(q.os, FS);
    Vec y(NS);
    double u[dkcore::MAX_OS], v[dkcore::MAX_OS], last = b0;
    for (std::size_t i = 0; i < NS; ++i) {
        r.up(double(xf[i]) * drive * mapvs::kVoltsPerFullScale, u);
        for (int j = 0; j < q.os; ++j) {
            double b = b0;
            if (i >= N0) {
                const double k = double(i - N0) + double(j + 1) / q.os;
                b = step || k >= RAMP ? b1 : b0 + k * (b1 - b0) / RAMP;
            }
            if (b != last) {
                dkcore::Coeffs c;
                dkcore::build_coeffs(c, FS * q.os, b, q.prewarp_hz);
                s->publish(c);
                last = b;
            }
            s->process_internal(&u[j], &v[j], nullptr, nullptr, 1);
        }
        y[i] = double(float(output * mapvs::kFullScalePerVolt * r.down(v)));
    }
    delete s;
    return y;
}

static double worst_step(const Vec& y, const Vec& r) {
    double w = 0.0;
    for (std::size_t i = 1; i < y.size(); ++i) w = std::max(w, std::fabs((y[i] - r[i]) - (y[i - 1] - r[i - 1])));
    return w;
}
static double maxdiff(const Vec& a, const Vec& b) {
    double w = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) w = std::max(w, std::fabs(a[i] - b[i]));
    return w;
}
static double peak(const Vec& a) {
    double w = 0.0;
    for (double v : a) w = std::max(w, std::fabs(v));
    return w;
}
static double rms(const Vec& a, std::size_t from, std::size_t to) {
    double s = 0.0;
    for (std::size_t i = from; i < to; ++i) s += a[i] * a[i];
    return std::sqrt(s / double(to - from));
}
// energy of a[from, to) in [f0, f1) Hz: periodic Hann window, plain DFT over the window's bins (no FFT needed)
static double band_energy(const Vec& a, std::size_t from, std::size_t to, double f0, double f1) {
    const std::size_t n = to - from;
    Vec w(n);
    for (std::size_t i = 0; i < n; ++i) w[i] = a[from + i] * (0.5 - 0.5 * std::cos(2 * dkcore::PI * double(i) / double(n)));
    double e = 0.0;
    for (std::size_t k = 1; k < n / 2; ++k) {
        const double f = double(k) * FS / double(n);
        if (f < f0 || f >= f1) continue;
        double re = 0.0, im = 0.0;
        for (std::size_t i = 0; i < n; ++i) {
            const double ph = 2 * dkcore::PI * double(k) * double(i) / double(n);
            re += w[i] * std::cos(ph);
            im -= w[i] * std::sin(ph);
        }
        e += re * re + im * im;
    }
    return e;
}

// ---- 1. fast full-range moves ------------------------------------------------------------------------------------
static void test_moves(mapvs::Engine& e) {
    struct Move { const char* name; Params a, b; };
    const double dlo = mapvs::kDriveRange.min, dhi = mapvs::kDriveRange.max, olo = mapvs::kOutputRange.min,
                 ohi = mapvs::kOutputRange.max, blo = mapvs::kBiasRange.min, bhi = mapvs::kBiasRange.max;
    const Move moves[] = {
        {"Drive up", {dlo, 1.0, 1.0}, {dhi, 1.0, 1.0}},   {"Drive down", {dhi, 1.0, 1.0}, {dlo, 1.0, 1.0}},
        {"Output up", {1.0, 1.0, olo}, {1.0, 1.0, ohi}},  {"Output down", {1.0, 1.0, ohi}, {1.0, 1.0, olo}},
        {"Bias up", {1.0, blo, 1.0}, {1.0, bhi, 1.0}},    {"Bias down", {1.0, bhi, 1.0}, {1.0, blo, 1.0}},
        {"Bias up, Drive 8", {8.0, blo, 1.0}, {8.0, bhi, 1.0}}, {"Bias down, Drive 8", {8.0, bhi, 1.0}, {8.0, blo, 1.0}},
    };
    double worst_gain = 0.0, worst_bias_sil = 0.0, worst_bias_sine = 0.0, red_min = 1e300;
    for (const Move& m : moves) {
        const bool bias = m.a.bias != m.b.bias;
        for (double amp : {0.0, 0.9}) {
            const Signal x = sines(amp);
            Vec yl, yr, rl, rr, bl, br;
            run_engine(e, x, m.a, m.b, step_at_n0, 64, yl, yr);
            if (bias) {
                rl = bias_reference(x.L, m.a.drive, m.a.output, m.a.bias, m.b.bias, false);
                rr = bias_reference(x.R, m.a.drive, m.a.output, m.a.bias, m.b.bias, false);
                bl = bias_reference(x.L, m.a.drive, m.a.output, m.a.bias, m.b.bias, true);
                br = bias_reference(x.R, m.a.drive, m.a.output, m.a.bias, m.b.bias, true);
            } else {
                gain_reference(x, m.a, m.b, RAMP, rl, rr);
                gain_reference(x, m.a, m.b, 1, bl, br);
            }
            const double w = std::max(worst_step(yl, rl), worst_step(yr, rr));
            const double wb = std::max(worst_step(bl, rl), worst_step(br, rr));
            const double raw = std::max(worst_step(yl, Vec(NS, 0.0)), worst_step(yr, Vec(NS, 0.0)));
            const double lim = !bias ? LIMIT_GAIN : amp == 0.0 ? LIMIT_BIAS_SILENCE : LIMIT_BIAS_SINE;
            // in silence the gain moves have nothing to step (the stage rests at 0 V): w = wb = 0
            const bool reduced = wb == 0.0 ? w == 0.0 : wb / std::max(w, 1e-300) >= MIN_REDUCTION;
            if (wb > 0.0) red_min = std::min(red_min, wb / std::max(w, 1e-300));
            (!bias ? worst_gain : amp == 0.0 ? worst_bias_sil : worst_bias_sine) =
                std::max(!bias ? worst_gain : amp == 0.0 ? worst_bias_sil : worst_bias_sine, w);
            check(w <= lim && reduced, std::string(m.name) + (amp == 0.0 ? ", silence" : ", sine 0.9 FS"),
                  fmt("worst step %.3g FS (limit %.3g), unsmoothed %.3g FS, raw output max step %.3g FS", w, lim, wb, raw));
        }
    }
    std::printf("summary  worst step: Drive/Output %.3g FS, Bias silence %.3g FS, Bias sine %.3g FS; min reduction %.0fx\n",
                worst_gain, worst_bias_sil, worst_bias_sine, red_min);
}

// ---- 2. block-size invariance ------------------------------------------------------------------------------------
static Params curve_at(std::size_t i, const Params& a, const Params& b) {    // all three, linear over 0.1 s from N0
    const double t = i < N0 ? 0.0 : std::min(1.0, double(i - N0) / (0.1 * FS));
    return Params{a.drive + t * (b.drive - a.drive), a.bias + t * (b.bias - a.bias), a.output + t * (b.output - a.output)};
}
static Params curve_all(std::size_t i, const Params& a, const Params& b) { return curve_at(i, a, b); }
// the same curve, read one 512-sample host block late: what a block of 512 can do to the timing of automation
static Params curve_late(std::size_t i, const Params& a, const Params& b) { return curve_at(i < 512 ? 0 : i - 512, a, b); }
static void test_block_sizes(mapvs::Engine& e) {
    const Signal x = sines(0.5);
    const Params a{1.0, 0.5, 1.0}, b{6.0, 2.0, 0.5};
    Vec l[3], r[3];
    const std::size_t blocks[3] = {16, 64, 512};
    for (int k = 0; k < 3; ++k) run_engine(e, x, a, b, step_at_n0, blocks[k], l[k], r[k]);
    check(l[0] == l[2] && l[1] == l[2] && r[0] == r[2] && r[1] == r[2],
          "blocks 16 / 64 / 512: Drive, Bias, Output stepped at sample 5120 give the same output (bit-exact)");
    for (int k = 0; k < 3; ++k) run_engine(e, x, a, b, curve_all, blocks[k], l[k], r[k]);
    double d = 0.0;
    for (int k = 0; k < 2; ++k) d = std::max({d, maxdiff(l[k], l[2]), maxdiff(r[k], r[2])});
    const double pk = std::max(peak(l[2]), peak(r[2]));
    /* Tolerance. A host that hands over a parameter once per block reads the curve up to block - 1 samples late, so
     * blocks of 512 and 16 see different curves; that is the host's sampling, not the smoother. The tolerance is what
     * that lag alone does: the block-16 run against the same block-16 run with the curve read 512 samples late. The
     * block sizes must differ by no more than that (measured in RESULTS-smoothing.md). */
    Vec ll, lr;
    run_engine(e, x, a, b, curve_late, 16, ll, lr);
    const double lag = std::max(maxdiff(ll, l[0]), maxdiff(lr, r[0]));
    check(d <= lag, "blocks 16 / 64 / 512: one automation curve of all three parameters, within the 512-sample lag bound",
          fmt("max diff %.3g FS (%.3g %% of peak %.3g FS); ", d, 100.0 * d / pk, pk) +
              fmt("lag bound %.3g FS (%.3g %%)", lag, 100.0 * lag / pk));
}

// ---- 3. drive smoothing and aliasing -----------------------------------------------------------------------------
// The same Drive ramp applied per internal sample after the upsampler (delayed by the interpolator's delay, so it
// lines up with the engine's base-rate ramp), on the left channel through a mono Stage.
static Vec drive_internal(const std::vector<float>& xf, double d0, double d1, int len) {
    const dkcore::Quality q = dkcore::STANDARD;
    auto* s = new dkcore::Stage;
    s->prepare(FS, q, 1.0);
    dkcore::Resampler r;
    r.prepare(q.os, FS);
    const double D = r.up_delay_internal();
    Vec y(NS);
    double u[dkcore::MAX_OS], v[dkcore::MAX_OS];
    for (std::size_t i = 0; i < NS; ++i) {
        r.up(double(xf[i]) * mapvs::kVoltsPerFullScale, u);
        for (int j = 0; j < q.os; ++j) {
            const double t = (double(i * q.os + std::size_t(j)) - D) / q.os;     // base-rate time of this sample
            const double f = std::floor(t);
            const double g0 = f < 0 ? d0 : ramp_at(std::size_t(f), d0, d1, len);
            const double g1 = f + 1 < 0 ? d0 : ramp_at(std::size_t(f + 1), d0, d1, len);
            u[j] *= g0 + (t - f) * (g1 - g0);
            s->process_internal(&u[j], &v[j], nullptr, nullptr, 1);
        }
        y[i] = double(float(mapvs::kFullScalePerVolt * r.down(v)));
    }
    delete s;
    return y;
}
static void test_drive_aliasing(mapvs::Engine& e) {
    double worst = -1e300;
    for (double f : {1037.0, 9037.0, 19037.0}) {
        Signal x;
        x.L.resize(NS);
        x.R.resize(NS);
        for (std::size_t i = 0; i < NS; ++i) x.L[i] = x.R[i] = float(0.9 * std::sin(2 * dkcore::PI * f * double(i) / FS));
        const Params a{1.0, 1.0, 1.0}, b{8.0, 1.0, 1.0};
        Vec yl, yr;
        run_engine(e, x, a, b, step_at_n0, 64, yl, yr);
        const Vec ref = drive_internal(x.L, a.drive, b.drive, RAMP);
        const Vec jump = drive_internal(x.L, a.drive, b.drive, 1);
        Vec dif(NS), djump(NS);
        for (std::size_t i = 0; i < NS; ++i) { dif[i] = yl[i] - ref[i]; djump[i] = jump[i] - ref[i]; }
        // a 4800-sample window from 20 ms before the move: the ramp, and 60 ms of the circuit settling after it
        const std::size_t from = N0 - RAMP, to = from + 4800;
        const double eref = band_energy(ref, from, to, 0.0, FS / 2);
        const double db = 10.0 * std::log10(std::max(band_energy(dif, from, to, 20.0, 20000.0), 1e-300) / eref);
        const double dbo = 10.0 * std::log10(std::max(band_energy(dif, from, to, 20000.0, FS / 2), 1e-300) / eref);
        const double dbt = 20.0 * std::log10(std::max(rms(dif, from, to), 1e-300) / rms(ref, from, to));
        const double dbj = 10.0 * std::log10(band_energy(djump, from, to, 20.0, 20000.0) / eref);
        worst = std::max(worst, db);
        /* Limit -90 dB: 38 dB below the stage's own aliasing at standard 8 V (worst -51.7 dBc at 19037 Hz, the
         * measurement behind dk/aliasing.py LIMIT_DBC). Measured worst -99.5 dB (19037 Hz; RESULTS-smoothing.md). */
        check(db <= -90.0, fmt("Drive 1 -> 8 ramp on %.0f Hz: base-rate ramp before the upsampler vs internal-rate ramp", f),
              fmt("difference in 20 Hz-20 kHz %.1f dB re output (limit -90); 20-24 kHz %.1f dB; all %.1f dB", db, dbo, dbt) +
                  fmt("; an unsmoothed jump instead: %.1f dB in band", dbj));
    }
    std::printf("summary  drive ramp, base rate vs internal rate: worst %.1f dB re output\n", worst);
}

int main() {
    auto engine = std::make_unique<mapvs::Engine>();
    test_moves(*engine);
    test_block_sizes(*engine);
    test_drive_aliasing(*engine);
    check(g_allocs.load() == 0, "no allocation in process()", fmt("%.0f allocations", double(g_allocs.load())));
    std::printf("%s (%d failed)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
