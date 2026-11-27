/* smoothing_test.cpp -- parameter smoothing through mapvs::Engine (#73, #115), stereo, against references built from
 * the direct cores. Method and numbers: prototypes/fullstage/cpp/RESULTS-smoothing.md (12AX7, sections 1-3) and
 * RESULTS-smoothing-6as6.md (6AS6, section 5).
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
 *   5. The 6AS6 path (#115), the same method: fast full-range Bias moves (P1 0.1 <-> 0.6 mA, T 0.1 <-> 0.5 mA) against
 *      the continuous reference of cpp/live_ref_6as6.h (an exact pack at every internal sample); Mode switches against
 *      the ideal crossfade of the two modes each run continuously from the start, beside a hard switch; Drive / Output
 *      moves as in 1; block sizes as in 2 with Mode and Filter stepped too; the Drive-ramp aliasing check of 3.
 * Prints one line per check; exit status 0 if all pass.
 */
#include "../tests/null_common.h"
#include "live_ref_6as6.h"

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
    e.set_valve(mapvs::k12AX7);                         // #73 is the 12AX7 path's smoothing (6AS6: #115)
    e.set_drive(a.drive); e.set_bias(a.bias); e.set_output(a.output);
    e.set_quality(mapvs::kStandard);
    e.set_bypass(false);
    e.prepare(FS);
    // The engine pads the stage's latency up to the reported one (#114, map_engine.h); the references here carry the
    // stage's own. Run pad samples longer (zero input) and drop the first pad: the stage's output, sample for sample.
    const std::size_t pad = std::size_t(e.latency_samples() - e.active_latency_samples()), total = NS + pad;
    std::vector<float> inL(x.L), inR(x.R), fl(total), fr(total);
    inL.resize(total, 0.0f);
    inR.resize(total, 0.0f);
    for (std::size_t i = 0; i < total; i += block) {
        const Params p = curve(i, a, b);
        e.set_drive(p.drive); e.set_bias(p.bias); e.set_output(p.output);
        const int n = int(std::min(block, total - i));
        const float* in[2] = {inL.data() + i, inR.data() + i};
        float* out[2] = {fl.data() + i, fr.data() + i};
        g_armed = true;
        e.process(in, out, 2, n);
        g_armed = false;
    }
    e.release();
    oL.assign(fl.begin() + std::ptrdiff_t(pad), fl.end());
    oR.assign(fr.begin() + std::ptrdiff_t(pad), fr.end());
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

// ---- 5. the 6AS6 path (#115) -------------------------------------------------------------------------------------
struct Params6 { double drive = 1.0, bias6 = 0.45, output = 1.0; int mode = mapvs::kP1, filter = mapvs::kFilterOff; };
using Curve6 = Params6 (*)(std::size_t, const Params6&, const Params6&);
static Params6 step6_at_n0(std::size_t i, const Params6& a, const Params6& b) { return i >= N0 ? b : a; }
static void set6(mapvs::Engine& e, const Params6& p) {
    e.set_drive(p.drive); e.set_bias6(p.bias6); e.set_output(p.output); e.set_mode6(p.mode); e.set_filter6(p.filter);
}
// the engine on the 6AS6 path, standard, at host blocks of `block`; as run_engine (the pad dropped)
static void run_engine6(mapvs::Engine& e, const Signal& x, const Params6& a, const Params6& b, Curve6 curve,
                        std::size_t block, Vec& oL, Vec& oR) {
    e.set_valve(mapvs::k6AS6);
    e.set_quality(mapvs::kStandard);
    e.set_bypass(false);
    set6(e, a);
    e.prepare(FS);
    const std::size_t pad = std::size_t(e.latency_samples() - e.active_latency_samples()), total = NS + pad;
    std::vector<float> inL(x.L), inR(x.R), fl(total), fr(total);
    inL.resize(total, 0.0f);
    inR.resize(total, 0.0f);
    for (std::size_t i = 0; i < total; i += block) {
        set6(e, curve(i, a, b));
        const int n = int(std::min(block, total - i));
        const float* in[2] = {inL.data() + i, inR.data() + i};
        float* out[2] = {fl.data() + i, fr.data() + i};
        g_armed = true;
        e.process(in, out, 2, n);
        g_armed = false;
    }
    e.release();
    oL.assign(fl.begin() + std::ptrdiff_t(pad), fl.end());
    oR.assign(fr.begin() + std::ptrdiff_t(pad), fr.end());
}
static dk6as6::Mode mode6(int m) { return dk6as6::Mode(m); }
static dk6as6::Lpf lpf6(int f) { return dk6as6::Lpf(f); }
static Vec volts_in(const std::vector<float>& xf, double drive) {
    Vec v(xf.size());
    for (std::size_t i = 0; i < xf.size(); ++i) v[i] = double(xf[i]) * drive * mapvs::kVoltsPerFullScale;
    return v;
}
static Vec to_fs(const Vec& y, double output) {      // stage volts -> the engine's float output
    Vec o(y.size());
    for (std::size_t i = 0; i < y.size(); ++i) o[i] = double(float(output * mapvs::kFullScalePerVolt * y[i]));
    return o;
}
// one channel through the direct 6AS6 core at constant settings (drive and output applied as the engine does)
static Vec core6(const std::vector<float>& xf, const Params6& p) {
    auto s = std::make_unique<dk6as6::Stage>();
    const double b = mapvs::bias6_effective(p.mode, p.bias6);
    s->prepare(FS, dk6as6::STANDARD, dk6as6::liveref::settings_of(mode6(p.mode), b, lpf6(p.filter)));
    const Vec x = volts_in(xf, p.drive);
    Vec y(x.size());
    s->process(x.data(), y.data(), x.size());
    return to_fs(y, p.output);
}
// the same, started from rest at sample `from` (the unsmoothed switch's new stage)
static Vec core6_from(const std::vector<float>& xf, const Params6& p, std::size_t from) {
    std::vector<float> part(xf.begin() + std::ptrdiff_t(from), xf.end());
    const Vec y = core6(part, p);
    Vec o(xf.size(), 0.0);
    std::copy(y.begin(), y.end(), o.begin() + std::ptrdiff_t(from));
    return o;
}

static void test_6as6_moves(mapvs::Engine& e) {
    struct Move { const char* name; Params6 a, b; };
    Params6 p1lo, p1hi, tlo, thi, d8lo, d8hi;
    p1lo.bias6 = 0.1; p1hi.bias6 = 0.6;
    tlo = p1lo; tlo.mode = mapvs::kT; thi = tlo; thi.bias6 = mapvs::kTBiasCapMa;
    d8lo = p1lo; d8lo.drive = 8.0; d8hi = p1hi; d8hi.drive = 8.0;
    Params6 dlo, dhi, olo, ohi;
    dlo.drive = mapvs::kDriveRange.min; dhi.drive = mapvs::kDriveRange.max;
    olo.output = mapvs::kOutputRange.min; ohi.output = mapvs::kOutputRange.max;
    const Move moves[] = {
        {"6AS6 Drive up", dlo, dhi},  {"6AS6 Drive down", dhi, dlo},  {"6AS6 Output up", olo, ohi},
        {"6AS6 Output down", ohi, olo}, {"6AS6 P1 Bias 0.1 -> 0.6 mA", p1lo, p1hi}, {"6AS6 P1 Bias 0.6 -> 0.1 mA", p1hi, p1lo},
        {"6AS6 P1 Bias 0.1 -> 0.6 mA, Drive 8", d8lo, d8hi}, {"6AS6 P1 Bias 0.6 -> 0.1 mA, Drive 8", d8hi, d8lo},
        {"6AS6 T Bias 0.1 -> 0.5 mA", tlo, thi},  {"6AS6 T Bias 0.5 -> 0.1 mA", thi, tlo},
    };
    const int ramp = int(std::nearbyint(dk6as6::LIVE_RAMP_S * FS));
    double worst_gain = 0.0, worst_sil = 0.0, worst_sine = 0.0, red_min = 1e300;
    for (const Move& m : moves) {
        const bool bias = m.a.bias6 != m.b.bias6;
        for (double amp : {0.0, 0.9}) {
            if (bias && m.a.drive == 8.0 && amp == 0.0) continue;       // silence: the Drive 1 rows
            const Signal x = sines(amp);
            Vec yl, yr, rl, rr, bl, br;
            run_engine6(e, x, m.a, m.b, step6_at_n0, 64, yl, yr);
            if (bias) {
                const dkcore::Quality q = dk6as6::STANDARD;
                const auto md = mode6(m.a.mode);
                const Vec xl = volts_in(x.L, m.a.drive), xr = volts_in(x.R, m.a.drive);
                rl = to_fs(dk6as6::liveref::bias_reference(FS, q, md, xl, m.a.bias6, m.b.bias6, N0, ramp, false), m.a.output);
                rr = to_fs(dk6as6::liveref::bias_reference(FS, q, md, xr, m.a.bias6, m.b.bias6, N0, ramp, false), m.a.output);
                bl = to_fs(dk6as6::liveref::bias_reference(FS, q, md, xl, m.a.bias6, m.b.bias6, N0, ramp, true), m.a.output);
                br = to_fs(dk6as6::liveref::bias_reference(FS, q, md, xr, m.a.bias6, m.b.bias6, N0, ramp, true), m.a.output);
            } else {
                // Drive / Output: the core at the ideal per-sample 20 ms ramp (len 1: the unsmoothed jump)
                for (int len : {RAMP, 1}) {
                    auto run_ch = [&](const std::vector<float>& xf) {
                        auto s = std::make_unique<dk6as6::Stage>();
                        s->prepare(FS, dk6as6::STANDARD, dk6as6::liveref::settings_of(dk6as6::Mode::P1, m.a.bias6));
                        Vec v(NS), y(NS);
                        for (std::size_t i = 0; i < NS; ++i)
                            v[i] = double(xf[i]) * ramp_at(i, m.a.drive, m.b.drive, len) * mapvs::kVoltsPerFullScale;
                        s->process(v.data(), y.data(), NS);
                        for (std::size_t i = 0; i < NS; ++i)
                            y[i] = double(float(ramp_at(i, m.a.output, m.b.output, len) * mapvs::kFullScalePerVolt * y[i]));
                        return y;
                    };
                    (len == 1 ? bl : rl) = run_ch(x.L);
                    (len == 1 ? br : rr) = run_ch(x.R);
                }
            }
            const double w = std::max(worst_step(yl, rl), worst_step(yr, rr));
            const double wb = std::max(worst_step(bl, rl), worst_step(br, rr));
            const double lim = !bias ? LIMIT_GAIN : amp == 0.0 ? LIMIT_BIAS_SILENCE : LIMIT_BIAS_SINE;
            const bool reduced = wb <= lim ? true : wb / std::max(w, 1e-300) >= MIN_REDUCTION;
            if (wb > lim) red_min = std::min(red_min, wb / std::max(w, 1e-300));
            double& acc = !bias ? worst_gain : amp == 0.0 ? worst_sil : worst_sine;
            acc = std::max(acc, w);
            check(w <= lim && reduced, std::string(m.name) + (amp == 0.0 ? ", silence" : ", sine 0.9 FS"),
                  fmt("worst step %.3g FS (limit %.3g), unsmoothed %.3g FS (%.0fx)", w, lim, wb, wb / std::max(w, 1e-300)) +
                      fmt(", max |y - ref| %.3g FS", std::max(maxdiff(yl, rl), maxdiff(yr, rr))));
        }
    }
    std::printf("summary  6AS6 worst step: Drive/Output %.3g FS, Bias silence %.3g FS, Bias sine %.3g FS; min reduction %.0fx\n",
                worst_gain, worst_sil, worst_sine, red_min);
}

/* Mode switches (the method and the two references: cpp/test_live_6as6.cpp, section 4). Against "always running"
 * (both modes from the start) the #73 limits hold in silence and at Drive 1; at Drive 8 the new mode's own settling
 * (the circuit's ~0.1 s coupling and grid-leak states, which a mode started at the switch has not built yet) dominates
 * that residual, so there it is printed, and the switch itself is checked against the crossfade to the new mode
 * started at the switch. */
static void test_6as6_switches(mapvs::Engine& e) {
    const std::size_t warm = std::size_t(std::nearbyint(dk6as6::LIVE_WARM_S * FS));
    const int fade = int(std::nearbyint(dk6as6::LIVE_FADE_S * FS));
    struct Sw { int from, to; };
    double worst_sil = 0.0, worst_sine = 0.0, worst_itself = 0.0, worst_8 = 0.0;
    for (const Sw& sw : {Sw{mapvs::kP1, mapvs::kT}, Sw{mapvs::kT, mapvs::kP1}, Sw{mapvs::kP1, mapvs::kP2},
                         Sw{mapvs::kP2, mapvs::kP1}})
        for (double drive : {1.0, 8.0})
            for (double amp : {0.0, 0.9}) {
                if (drive == 8.0 && amp == 0.0) continue;
                Params6 a, b;
                a.drive = b.drive = drive;
                a.mode = sw.from;
                b.mode = sw.to;
                const Signal x = sines(amp);
                Vec yl, yr;
                run_engine6(e, x, a, b, step6_at_n0, 64, yl, yr);
                const Vec al = core6(x.L, a), ar = core6(x.R, a), bl = core6(x.L, b), br = core6(x.R, b);
                const Vec cl = core6_from(x.L, b, N0), cr = core6_from(x.R, b, N0);     // the new mode started at N0
                const Vec rl = dk6as6::liveref::crossfade(al, bl, N0 + warm, fade);
                const Vec rr = dk6as6::liveref::crossfade(ar, br, N0 + warm, fade);
                const Vec ql = dk6as6::liveref::crossfade(al, cl, N0 + warm, fade);
                const Vec qr = dk6as6::liveref::crossfade(ar, cr, N0 + warm, fade);
                Vec hl(NS), hr(NS);
                for (std::size_t i = 0; i < NS; ++i) {
                    hl[i] = i < N0 ? al[i] : cl[i];
                    hr[i] = i < N0 ? ar[i] : cr[i];
                }
                const double lim = amp == 0.0 ? LIMIT_BIAS_SILENCE : LIMIT_BIAS_SINE;
                const std::string name = std::string("6AS6 Mode ") + mapvs::kMode6Names[sw.from] + " -> " +
                                         mapvs::kMode6Names[sw.to] + fmt(", Drive %.0f, ", drive) +
                                         (amp == 0.0 ? "silence" : "sine 0.9 FS");
                const double w = std::max(worst_step(yl, rl), worst_step(yr, rr));
                const double wh = std::max(worst_step(hl, rl), worst_step(hr, rr));
                const double wc = std::max(worst_step(yl, ql), worst_step(yr, qr));
                const double whc = std::max(worst_step(hl, ql), worst_step(hr, qr));
                worst_itself = std::max(worst_itself, wc);
                const bool red_c = whc <= lim ? true : whc / std::max(wc, 1e-300) >= MIN_REDUCTION;
                check(wc <= lim && red_c, name + ": the switch itself",
                      fmt("worst step %.3g FS (limit %.3g) vs the crossfade to the new mode started at the switch; hard "
                          "switch %.3g FS (%.0fx)", wc, lim, whc, whc / std::max(wc, 1e-300)));
                if (drive == 8.0) {
                    worst_8 = std::max(worst_8, w);
                    std::printf("info  %s: vs always running: worst step %.3g FS, max |y - ref| %.3g FS; hard switch %.3g FS\n",
                                name.c_str(), w, std::max(maxdiff(yl, rl), maxdiff(yr, rr)), wh);
                    continue;
                }
                const bool reduced = wh <= lim ? true : wh / std::max(w, 1e-300) >= MIN_REDUCTION;
                (amp == 0.0 ? worst_sil : worst_sine) = std::max(amp == 0.0 ? worst_sil : worst_sine, w);
                check(w <= lim && reduced, name + ": vs always running",
                      fmt("worst step %.3g FS (limit %.3g) vs the ideal crossfade, hard switch %.3g FS (%.0fx)", w, lim, wh,
                          wh / std::max(w, 1e-300)) +
                          fmt(", max |y - ref| %.3g FS", std::max(maxdiff(yl, rl), maxdiff(yr, rr))));
            }
    std::printf("summary  6AS6 mode switch worst step vs always running: silence %.3g FS, Drive 1 sine %.3g FS, Drive 8 "
                "sine %.3g FS (reported); the switch itself %.3g FS\n", worst_sil, worst_sine, worst_8, worst_itself);
}

// Drive, Bias and Output linear over 0.1 s from N0 (mode and filter as a); and the same read 512 samples late
static Params6 curve6_all(std::size_t i, const Params6& p, const Params6& q) {
    const double t = i < N0 ? 0.0 : std::min(1.0, double(i - N0) / (0.1 * FS));
    Params6 c = p;
    c.drive = p.drive + t * (q.drive - p.drive);
    c.bias6 = p.bias6 + t * (q.bias6 - p.bias6);
    c.output = p.output + t * (q.output - p.output);
    return c;
}
static Params6 curve6_late(std::size_t i, const Params6& p, const Params6& q) { return curve6_all(i < 512 ? 0 : i - 512, p, q); }
static void test_6as6_block_sizes(mapvs::Engine& e) {
    const Signal x = sines(0.5);
    Params6 a, b;
    a.bias6 = 0.2;
    b.drive = 6.0; b.bias6 = 0.6; b.output = 0.5; b.mode = mapvs::kT; b.filter = mapvs::kFilter7k;   // T caps 0.6 at 0.5
    Vec l[3], r[3];
    const std::size_t blocks[3] = {16, 64, 512};
    for (int k = 0; k < 3; ++k) run_engine6(e, x, a, b, step6_at_n0, blocks[k], l[k], r[k]);
    check(l[0] == l[2] && l[1] == l[2] && r[0] == r[2] && r[1] == r[2],
          "6AS6 blocks 16 / 64 / 512: Drive, Bias, Output, Mode and Filter stepped at sample 5120 give the same output (bit-exact)");
    Params6 bb = b;
    bb.mode = a.mode;
    bb.filter = a.filter;
    for (int k = 0; k < 3; ++k) run_engine6(e, x, a, bb, curve6_all, blocks[k], l[k], r[k]);
    double d = 0.0;
    for (int k = 0; k < 2; ++k) d = std::max({d, maxdiff(l[k], l[2]), maxdiff(r[k], r[2])});
    const double pk = std::max(peak(l[2]), peak(r[2]));
    Vec ll, lr;
    run_engine6(e, x, a, bb, curve6_late, 16, ll, lr);
    const double lag = std::max(maxdiff(ll, l[0]), maxdiff(lr, r[0]));
    check(d <= lag, "6AS6 blocks 16 / 64 / 512: one automation curve of Drive, Bias and Output, within the 512-sample lag bound",
          fmt("max diff %.3g FS (%.3g %% of peak %.3g FS); ", d, 100.0 * d / pk, pk) +
              fmt("lag bound %.3g FS (%.3g %%)", lag, 100.0 * lag / pk));
}

// the 6AS6 path's Drive ramp vs the same ramp per internal sample after the upsampler (left channel, P1 0.45 mA)
static Vec drive_internal6(const std::vector<float>& xf, double d0, double d1, int len) {
    const dkcore::Quality q = dk6as6::STANDARD;
    auto s = std::make_unique<dk6as6::Stage>();
    s->prepare(FS, q, dk6as6::liveref::settings_of(dk6as6::Mode::P1, 0.45));
    dk6as6::Stage::Resampler r;
    r.prepare(q.os, FS, dk6as6::DEFAULT_FIR);
    const double D = r.up_delay_internal();
    Vec y(NS);
    double u[dkcore::MAX_OS], v[dkcore::MAX_OS];
    for (std::size_t i = 0; i < NS; ++i) {
        r.up(double(xf[i]) * mapvs::kVoltsPerFullScale, u);
        for (int j = 0; j < q.os; ++j) {
            const double t = (double(i * q.os + std::size_t(j)) - D) / q.os;
            const double f = std::floor(t);
            const double g0 = f < 0 ? d0 : ramp_at(std::size_t(f), d0, d1, len);
            const double g1 = f + 1 < 0 ? d0 : ramp_at(std::size_t(f + 1), d0, d1, len);
            u[j] *= g0 + (t - f) * (g1 - g0);
            s->process_internal(&u[j], &v[j], 1);
        }
        y[i] = double(float(mapvs::kFullScalePerVolt * r.down(v)));
    }
    return y;
}
static void test_6as6_drive_aliasing(mapvs::Engine& e) {
    double worst = -1e300;
    for (double f : {1037.0, 9037.0, 19037.0}) {
        Signal x;
        x.L.resize(NS);
        x.R.resize(NS);
        for (std::size_t i = 0; i < NS; ++i) x.L[i] = x.R[i] = float(0.9 * std::sin(2 * dkcore::PI * f * double(i) / FS));
        Params6 a, b;
        b.drive = 8.0;
        Vec yl, yr;
        run_engine6(e, x, a, b, step6_at_n0, 64, yl, yr);
        const Vec ref = drive_internal6(x.L, a.drive, b.drive, RAMP);
        const Vec jump = drive_internal6(x.L, a.drive, b.drive, 1);
        Vec dif(NS), djump(NS);
        for (std::size_t i = 0; i < NS; ++i) { dif[i] = yl[i] - ref[i]; djump[i] = jump[i] - ref[i]; }
        const std::size_t from = N0 - RAMP, to = from + 4800;
        const double eref = band_energy(ref, from, to, 0.0, FS / 2);
        const double db = 10.0 * std::log10(std::max(band_energy(dif, from, to, 20.0, 20000.0), 1e-300) / eref);
        const double dbo = 10.0 * std::log10(std::max(band_energy(dif, from, to, 20000.0, FS / 2), 1e-300) / eref);
        const double dbj = 10.0 * std::log10(band_energy(djump, from, to, 20.0, 20000.0) / eref);
        worst = std::max(worst, db);
        // the same -90 dB limit as the 12AX7 path's check (section 3)
        check(db <= -90.0, fmt("6AS6 Drive 1 -> 8 ramp on %.0f Hz: base-rate ramp before the upsampler vs internal-rate ramp", f),
              fmt("difference in 20 Hz-20 kHz %.1f dB re output (limit -90); 20-24 kHz %.1f dB", db, dbo) +
                  fmt("; an unsmoothed jump instead: %.1f dB in band", dbj));
    }
    std::printf("summary  6AS6 drive ramp, base rate vs internal rate: worst %.1f dB re output\n", worst);
}

int main() {
    auto engine = std::make_unique<mapvs::Engine>();
    test_moves(*engine);
    test_block_sizes(*engine);
    test_drive_aliasing(*engine);
    test_6as6_moves(*engine);
    test_6as6_switches(*engine);
    test_6as6_block_sizes(*engine);
    test_6as6_drive_aliasing(*engine);
    check(g_allocs.load() == 0, "no allocation in process()", fmt("%.0f allocations", double(g_allocs.load())));
    std::printf("%s (%d failed)\n", g_fail ? "FAIL" : "PASS", g_fail);
    return g_fail ? 1 : 0;
}
