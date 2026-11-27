/* dk6as6_core.h -- header-only realtime C++17 core of the 6AS6 pentode nodal-DK stage, Gyre's v1 distortion stage
 * (#104; v1 scope #75).
 *
 * A port of dk6as6/stage.py + solver.py + circuit.py + pentode.py + lpf.py (the Python stage stays the reference:
 * cpp/vectors_6as6.txt, exported by cpp/export_vectors_6as6.py, holds its outputs; cpp/test_core_6as6.cpp checks this
 * core against them; tolerances and method in cpp/README-6as6.md). The 12AX7 stage (dk_core.h) stays in the core,
 * unchanged, as the optional swap-in; how the plugin exposes either is #114.
 *
 * What is ported, with the settled behaviour of the merged queue:
 *   circuit   #102 values (circuit.VALUES: HT 200 V, Ra 270k, Rl 1M, Rg 1M, unbypassed cathode rheostat, no Ck)
 *   bias      #110: a target DC cathode current Ik in mA (0.1-0.6 mA documented, default 0.45); prepare() solves
 *             the rheostat Rk that draws it (Brent on log10 Rk, stage.cathode_resistor) and Rk is fixed for a Stage's
 *             run; LiveStage moves it live (#115, "live bias" below: C++ only, not in the Python stage)
 *   modes     #111: P1 (default), T, P2 (circuit.MODES), fixed for a Stage's run; LiveStage switches them live (#115)
 *   grid      #112: the optional g1 grid current (pentode.grid, Dempwolf & Zoelzer's 12AX7 law scaled by the fitted
 *             cathode-perveance ratio; UNVALIDATED, no 6AS6 grid-current data exists), as a third current port with a
 *             3x3 Newton step and the restart contract (solver.solve_grid_restarts)
 *   post-LPF  #103: the Off / 4k / 7k TPT SVF low-pass inside the oversampled block, after the nonlinearity and
 *             before decimation, with lpf.PostFilter's crossfade on a position change
 *   quality   standard 8x and hq 16x, no pre-warp (stage.QUALITY). There is no eco: eco is not a 6AS6 setting.
 *   resampler dk_core.h's streaming half-band FIR (ResamplerT); the design is DEFAULT_FIR below: #113's k140
 *
 * Fixed-size state: 7 capacitor histories x (as the deviation from the operating point, #70), the valve currents
 * i = (Ip, Ig2[, Ig1]) of the last two samples (the warm start extrapolates them, 2 i[n-1] - i[n-2]), the two SVF
 * states per filter position, the resampler. Nothing in process() allocates, locks or makes a syscall; process() is
 * noexcept and wait-free. Denormals: process() and process_internal() hold dkcore::ScopedFlushDenormals.
 *
 * Threads:
 *   prepare() / prepare_pack()      non-RT, never concurrently with process() (a host's prepare / setup call). They
 *                                   do all the building: Brent's Rk solve, the matrices, the DC operating point.
 *   set_drive() / set_output()      any thread (relaxed atomics, read once per block)
 *   set_lpf()                       any thread (relaxed atomic, read once per block; a change starts PostFilter's
 *                                   XFADE_S crossfade, lpf.PostFilter.set_position)
 *   process() / process_internal() / reset()   the audio thread
 *   build_line() / build_lines()    non-RT (#115); LiveStage: prepare() non-RT, set_bias() / set_mode() / set_lpf() /
 *                                   set_drive() / set_output() any thread, process() / reset() the audio thread
 *
 * Non-convergence (#7, #112): the 2-current solve returns the best finite iterate at the cap, flagged; the 3-current
 * solve (with grid current) tries the extrapolated warm start, then the previous sample's currents, then the operating
 * point, and if none converges keeps the candidate with the smallest finite residual over all three, flagged. Every
 * flagged sample is counted (Stats::nonconverged), and so is every sample that needed a restart (Stats::restarted).
 * The iteration count of one sample is bounded by 3 * Newton::max_it (measured worst on the validated signals: 15,
 * PR #128). The DC solve has no state to fall back on: a setting whose DC solve fails is never built.
 *
 * Deliberate differences from the Python reference, all outside what it computes on any validated signal:
 *   - Python raises where an evaluation overflows (math.exp / ** OverflowError). The 3-current solve catches that and
 *     treats it as a failed evaluation (solver._resid3_safe); this port detects the same overflow and does the same.
 *     The 2-current solve does not catch it in Python (the stage would raise); here it continues with the best iterate.
 *   - NaN / inf input samples (after the drive gain) are replaced by 0 and counted (Stats::nonfinite_inputs), and
 *     set_drive() / set_output() ignore a non-finite gain. If the state still goes non-finite (a finite input far
 *     outside any signal, such as the largest double), that sample outputs 0 and the state returns to the operating
 *     point, the resampler and filter states cleared (Stats::recoveries): realtime-safe, no reset() needed. An output
 *     sample whose gained value does not fit the destination type (double or float) is written as 0 and counted
 *     (Stats::nonfinite_outputs).
 *   - Python's out-of-law check warns; here it is counted (Stats::out_of_law).
 *
 * Do not build with -ffast-math (dk_core.h #errors): the fallbacks rely on NaN / inf comparisons.
 */
#ifndef DK6AS6_CORE_H
#define DK6AS6_CORE_H

#include "dk_core.h"

#include <cfloat>
#include <limits>

namespace dk6as6 {

using dkcore::FirDesign;
using dkcore::Newton;
using dkcore::Quality;
using dkcore::PI;
inline constexpr int LS_MAX = dkcore::LS_MAX;   // solver.LS_MAX (dk/solver.py, unchanged)

// The one line that picks the 6AS6 stage's resampler: #113's K140_FIR (PR #129), adopted for the C++ core on PR #133
// (Michael's decision there; #134). With SHIPPED_FIR, the resampler of every committed Python 6AS6 result, P1 0.6 mA
// at 2 V and 44.1 kHz is over the aliasing gate from ~19957 Hz up (cpp/README-6as6.md, "Antialiasing"). The port is
// checked against Python with SHIPPED_FIR selected explicitly (Settings::fir); the k140 stage against ngspice on its own
// (cpp/validate_cpp_6as6.py --fir k140).
inline constexpr const FirDesign& DEFAULT_FIR = dkcore::K140_FIR;
inline const char* fir_name(const FirDesign& d) {
    return d.later_taps == dkcore::K140_FIR.later_taps && d.first_beta == dkcore::K140_FIR.first_beta ? "k140"
         : d.later_taps == dkcore::SHIPPED_FIR.later_taps && d.first_beta == dkcore::SHIPPED_FIR.first_beta ? "shipped"
         : "other";
}

// ---- the valve: fit6as6 `dual_control` (#55), dk6as6/pentode.py ---------------------------------------------------

// OUR empirical extension of Koren's pentode (prototypes/fit6as6/model6as6.py), parameters from
// prototypes/fit6as6/results/fit_results.json["dual_control"]["params"] (copied here; the tests check them against the
// file bit for bit). Not a published model.
struct PentodeParams { double MU, EX, KG1, KP, FMAX, MU3, V3C, S3, P3, RI, KVB, KR, XA; };
inline constexpr PentodeParams DUAL_CONTROL{
    30.120202041620328, 1.4904598465136472, 696.4800292646072, 149.58905578870014, 0.7079965046203495,
    33.596891928864856, -4.077035360004601, 2.3468320300815333, 3.9918010228526213, 46.54300284711192,
    0.5000000000000001, 0.2737370619770903, 0.5742673753852175};
inline constexpr double VA0 = 120.0;         // V, model6as6.VA0 (Eb of the Raytheon / GE Ec3 transfer curves)
inline constexpr double VG2_FLOOR = 1e-3;    // V, model6as6.koren_e1: Vg2 floored at 1 mV
inline constexpr double VA_FLOOR = 1e-3;     // V, model6as6.plate_term: Va floored at 1 mV in the power (XA != 1)

// g1 grid current (#112): Ig1 = GG (softplus(CG Vg1k) / CG)^XI, Dempwolf & Zoelzer DAFx-11 eq. (11) without Ig0,
// XI and CG from their 12AX7 RSD-1 fit (Table 1), GG = hardness x (2 / KG1) / G_RSD1 x Gg_RSD1 (pentode.grid).
// BORROWED, NOT 6AS6 DATA; hardness 1 (HARDNESS) is our stated guess, 0 turns it off (the #111 two-port stage).
struct GridParams { double GG, XI, CG, hardness; };
inline constexpr double HARDNESS = 1.0;      // pentode.HARDNESS, the default (our choice, #112)
inline GridParams grid(double hardness = HARDNESS, const PentodeParams& p = DUAL_CONTROL) {
    const auto& dz = dkcore::DZ_12AX7_RSD1;
    const double ratio = (2.0 / p.KG1) / dz.G;
    return GridParams{hardness * ratio * dz.Gg, dz.xi, dz.Cg, hardness};
}

// ---- circuit values (#102), dk6as6/circuit.py VALUES: every passive INFERRED, none measured ----------------------
struct Values {
    double HT = 200.0;          // V, inferred (#102 E2/E3/E7); low confidence
    double Rs = 1e3;            // test bench source resistance, kept from the 12AX7 stage (our round value)
    double Cin = 100e-9;        // inferred (HP corner of a few Hz)
    double Rg = 1e6;            // S9 maximum grid-circuit resistance
    double Rgs = 68e3;          // grid stopper, kept from the 12AX7 stage (our round value)
    double Ra = 270e3;          // inferred (#102 E2/E3/E7); low confidence
    double Cout = 100e-9;       // inferred
    double Rl = 1e6;            // next-stage grid leak (#102 table)
    double EC2 = 120.0;         // V, stiff screen supply, datasheet typical (S9, GE ET-T706 p.1)
    double CG1K = 4e-12, CG3 = 3.4e-12, CPK = 3e-12, CG1P = 0.02e-12, CG1G3 = 0.15e-12;   // GE sheet p.1
    double Rstrap = 100.0;      // T: g2 + g3 -> plate stopper (#102, inferred)
};

// The Distortion switch (#111), circuit.MODES. No source states the wiring (dk6as6/RESULTS.md, "Modes (#111)"):
//   P1  g3 -> cathode, g2 the stiff EC2 (S8 speculation, the datasheet's standard pentode connection; the default)
//   T   g2 + g3 strapped to the plate through Rstrap, no EC2 (S8 speculation + standard triode strapping)
//   P2  g3 -> ground (Vg3k = -Vk), g2 the stiff EC2 (OUR hypothesis, #102 E9)
enum class Mode : std::uint8_t { P1, T, P2 };
inline const char* to_string(Mode m) { return m == Mode::P1 ? "P1" : m == Mode::T ? "T" : "P2"; }

// ---- bias (#110) and the supported envelope, dk6as6/stage.py ------------------------------------------------------
inline constexpr double BIAS_MA = 0.45;                       // default target cathode current, mA (S6; #102)
inline constexpr double BIAS_RANGE_MA[2] = {0.6, 0.1};        // target Ik at the knob's two ends (#110, OUR choice)
inline constexpr double KNOB[2] = {1.0, 11.0};                // the panel's Bias scale (SOS 2003: "one to 11")
inline constexpr double RK_LOG10[2] = {2.7, 6.0};             // cathode_resistor's bracket, log10(Rk / ohm)

// stage.knob_bias: Ik = 0.6 mA * (0.1 / 0.6) ** ((knob - 1) / 10), reverse acting, log taper (OUR choices, #110).
// Returns NaN outside the 1-11 scale (Python raises).
inline double knob_bias(double knob) {
    if (!(KNOB[0] <= knob && knob <= KNOB[1])) return NAN;
    const double a = BIAS_RANGE_MA[0], b = BIAS_RANGE_MA[1];
    return a * std::pow(b / a, (knob - KNOB[0]) / (KNOB[1] - KNOB[0]));
}
// stage.bias_knob, the inverse; NaN outside 0.1-0.6 mA
inline double bias_knob(double bias_ma) {
    const double a = BIAS_RANGE_MA[0], b = BIAS_RANGE_MA[1];
    if (!(b <= bias_ma && bias_ma <= a)) return NAN;
    return KNOB[0] + (KNOB[1] - KNOB[0]) * std::log(bias_ma / a) / std::log(b / a);
}

// stage.SUPPORTED_MA (with the default grid current) and SUPPORTED_MA_NO_GRID (hardness 0): mode -> (lo, hi) mA where
// the stage is in the valve law for host signals up to 8 V peak, validated against ngspice, and within the aliasing
// gate (dk6as6/RESULTS.md, "Supported envelope" and "Grid current (#112)"). P2: nowhere. Any other hardness: none.
inline bool envelope(Mode m, double hardness, double& lo, double& hi) {
    if (m == Mode::P1 && (hardness == HARDNESS || hardness == 0.0)) { lo = 0.1; hi = 0.6; return true; }
    if (m == Mode::T && hardness == HARDNESS) { lo = 0.1; hi = 0.5; return true; }
    if (m == Mode::T && hardness == 0.0) { lo = 0.1; hi = 0.3; return true; }
    return false;
}
inline bool supported(Mode m, double bias_ma, double hardness = HARDNESS) {   // stage.supported (to 1e-9 mA)
    double lo, hi;
    return envelope(m, hardness, lo, hi) && lo - 1e-9 <= bias_ma && bias_ma <= hi + 1e-9;
}

// ---- quality (stage.QUALITY): no pre-warp, no eco -----------------------------------------------------------------
inline constexpr Quality STANDARD{8, 0.0}, HQ{16, 0.0};

// ---- the post-distortion low-pass (#103), dk6as6/lpf.py ----------------------------------------------------------
// Sources and labels: lpf.py's docstring (the #103 research comment). 12 dB/oct after the stage (sourced); positions
// Off / 4k / 7k (2003 revision, sourced); the shape a 2-pole fit to the digitised S5 LPF curve (f0 9535 Hz, Q 0.4277);
// 7k = the S5 curve and 4k = the same shape scaled by 4/7 (both INFERRED); decoupled from the plate (INFERRED).
enum class Lpf : std::uint8_t { off, k4, k7 };              // lpf.POSITIONS order: "off", "4k", "7k"
inline constexpr double LPF_Q = 0.4277;                       // lpf.Q
inline constexpr double LPF_F0_HZ[2] = {9535.0 * 4.0 / 7.0, 9535.0};   // lpf.F0_HZ["4k"], ["7k"]
inline constexpr double LPF_XFADE_S = 0.015;                  // lpf.XFADE_S
inline constexpr double LPF_PREWARP_HZ = 15700.0;             // lpf.PREWARP_HZ (prewarp_minimax(), rounded)

// ---- valve law (pentode.py): the same expressions in the same order ------------------------------------------------

namespace detail {
using dkcore::detail::pmax;   // Python's max(a, b): b > a ? b : a, NaN included

// CPython's float ** float for the bases used here (all >= 0): libm pow, plus the OverflowError CPython raises when
// a finite pair overflows (Python's ** never returns inf from finite operands; it raises).
inline double pypow(double x, double y, bool& raised) {
    const double r = std::pow(x, y);
    if (std::isinf(r) && std::isfinite(x) && std::isfinite(y)) raised = true;
    return r;
}
// A literal exponent the optimiser cannot see: compilers fold std::pow(x, 2.0) into x * x, but CPython's (Va / KVB) ** 2
// calls libm pow, which is not x * x on every libm (Apple's differs by an ulp for some x)
inline double opaque(double y) { volatile double v = y; return v; }
inline double softplus(double x) { return (x > 0.0 ? x : 0.0) + std::log1p(std::exp(-std::fabs(x))); }
inline double sigmoid(double x) {
    if (x >= 0.0) return 1.0 / (1.0 + std::exp(-x));
    const double e = std::exp(x);
    return e / (1.0 + e);
}
// softplus(x) and sigmoid(x) from one exp: pentode.py's sigmoid takes exp(-x) for x >= 0 and exp(x) below, which is
// exp(-|x|) bit for bit, the value softplus takes too
inline void softplus_sigmoid(double x, double& sp, double& sg) {
    const double e = std::exp(-std::fabs(x));
    sp = (x > 0.0 ? x : 0.0) + std::log1p(e);
    sg = x >= 0.0 ? 1.0 / (1.0 + e) : e / (1.0 + e);
}
// CPython >= 3.12's sum() of four floats from the int start 0 (Neumaier compensated summation, bltinmodule.c): the
// 2-current solver's dR/di entries use Python's sum(), so this keeps them bit-equal.
inline double pysum4(double a0, double a1, double a2, double a3) {
    double f = 0.0 + a0, c = 0.0;
    const double xs[3] = {a1, a2, a3};
    for (double x : xs) {
        const double t = f + x;
        if (std::fabs(f) >= std::fabs(x)) c += (f - t) + x;
        else c += (x - t) + f;
        f = t;
    }
    if (c != 0.0 && std::isfinite(c)) f += c;
    return f;
}

/* pentode.cathode_share: (Ik, dIk, f, df), gradients d/d(Vg1k, Vg2k, Vg3k, Vak). JAC = false skips what only the
 * gradients need (pentode.currents_only computes them and drops them): the values are the same, and so is whether
 * Python would raise (pow(e1, EX - 1) overflows only where pow(e1, EX) does; (Va / KVB) ** 2 is checked directly). */
template <bool JAC>
inline void cathode_share(double vg1, double vg2, double vg3, double va, const PentodeParams& p, double& ik,
                          double (&dik)[4], double& f, double (&dfs)[4], bool& raised) {
    const bool floor = vg2 <= VG2_FLOOR;
    const double g2 = floor ? VG2_FLOOR : vg2;
    const double a = p.KP * (1.0 / p.MU + vg1 / g2);
    double spa, sa = 0.0;
    if (JAC) softplus_sigmoid(a, spa, sa);
    else spa = softplus(a);
    const double e1 = g2 / p.KP * spa;
    const double ik0 = 2.0 * pypow(e1, p.EX, raised) / p.KG1;
    double up, dup = 0.0;
    if (p.XA == 1.0) {
        up = va > 0.0 ? va / p.MU3 : 0.0;
        if (JAC) dup = va > 0.0 ? 1.0 / p.MU3 : 0.0;
    } else {
        const double x = (va > VA_FLOOR ? va : VA_FLOOR) / VA0;
        const double xa = pypow(x, p.XA, raised);
        up = VA0 / p.MU3 * (1.0 + (xa - 1.0) / p.XA);
        if (JAC) dup = va > VA_FLOOR ? xa / x / p.MU3 : 0.0;
    }
    const double z = (vg3 + up - p.V3C - p.RI * ik0) / p.S3;
    double spz, sm;
    softplus_sigmoid(-z, spz, sm);
    const double s = std::exp(-p.P3 * spz);
    const double knee = va > 0.0 ? 2.0 / PI * std::atan(va / p.KVB) : 0.0;
    f = p.FMAX * knee * s;
    const double kf = 1.0 - p.KR * sm;
    ik = ik0 * kf;
    if (!JAC) {
        if (va > 0.0) { const double q = va / p.KVB; if (std::isinf(q * q) && std::isfinite(q)) raised = true; }
        return;
    }
    const double de1_g1 = sa;
    const double de1_g2 = floor ? 0.0 : spa / p.KP - sa * vg1 / g2;
    const double dik0 = 2.0 * p.EX * pypow(e1, p.EX - 1.0, raised) / p.KG1;
    const double dik0_g1 = dik0 * de1_g1, dik0_g2 = dik0 * de1_g2;
    const double dz_g1 = -p.RI * dik0_g1 / p.S3, dz_g2 = -p.RI * dik0_g2 / p.S3, dz_g3 = 1.0 / p.S3,
                 dz_a = dup / p.S3;
    const double ds = s * p.P3 * sm;
    const double dknee = va > 0.0 ? 2.0 / PI / p.KVB / (1.0 + pypow(va / p.KVB, opaque(2.0), raised)) : 0.0;
    const double df_z = p.FMAX * knee * ds, df_a = p.FMAX * dknee * s;
    const double dik_z = ik0 * p.KR * sm * (1.0 - sm);
    dik[0] = dik0_g1 * kf + dik_z * dz_g1;
    dik[1] = dik0_g2 * kf + dik_z * dz_g2;
    dik[2] = dik_z * dz_g3;
    dik[3] = dik_z * dz_a;
    dfs[0] = df_z * dz_g1; dfs[1] = df_z * dz_g2; dfs[2] = df_z * dz_g3; dfs[3] = df_z * dz_a + df_a;
}

// pentode.grid_current: (Ig1, dIg1/dVg1k); (0, 0) where softplus underflows
template <bool JAC>
inline void grid_current(double vg1, const GridParams& g, double& ig1, double& dig1, bool& raised) {
    const double zg = g.CG * vg1;
    const double e = std::exp(-std::fabs(zg));
    double sp, sg;
    if (zg >= 0.0) { sp = zg + std::log1p(e); sg = 1.0 / (1.0 + e); }
    else { sp = std::log1p(e); sg = e / (1.0 + e); }
    const double s = sp / g.CG;
    if (s <= 0.0) { ig1 = 0.0; dig1 = 0.0; return; }
    const double pg = pypow(s, g.XI, raised);
    ig1 = g.GG * pg;
    if (JAC) dig1 = g.GG * g.XI * pg / s * sg;
}
}  // namespace detail

// i = (Ip, Ig2[, Ig1]) and J = d i / d(Vg1k, Vg2k, Vg3k, Vak); `raised`: where Python would raise
struct Law { double i[3]; double J[3][4]; bool raised; };

// pentode.currents (JAC) / currents_only: (Ip, Ig2), no grid current
template <bool JAC = true> inline void currents(const double (&v)[4], const PentodeParams& p, Law& o) {
    double ik, dik[4], f, dfs[4];
    o.raised = false;
    detail::cathode_share<JAC>(v[0], v[1], v[2], v[3], p, ik, dik, f, dfs, o.raised);
    const double ip = ik * f;
    o.i[0] = ip; o.i[1] = ik - ip; o.i[2] = 0.0;
    if (!JAC) return;
    for (int j = 0; j < 4; ++j) {
        const double dip = dik[j] * f + ik * dfs[j];
        o.J[0][j] = dip; o.J[1][j] = dik[j] - dip; o.J[2][j] = 0.0;
    }
}
// pentode.currents_grid (JAC) / currents_grid_only: (Ip, Ig2, Ig1), Ip = (Ik - Ig1) f, Ig2 = (Ik - Ig1)(1 - f)
template <bool JAC = true> inline void currents_grid(const double (&v)[4], const PentodeParams& p, const GridParams& g,
                                                     Law& o) {
    double ik, dik[4], f, dfs[4], ig1, dig1 = 0.0;
    o.raised = false;
    detail::cathode_share<JAC>(v[0], v[1], v[2], v[3], p, ik, dik, f, dfs, o.raised);
    detail::grid_current<JAC>(v[0], g, ig1, dig1, o.raised);
    const double ipk = ik - ig1;
    const double ip = ipk * f;
    o.i[0] = ip; o.i[1] = ipk - ip; o.i[2] = ig1;
    if (!JAC) return;
    const double dipk[4] = {dik[0] - dig1, dik[1], dik[2], dik[3]};
    for (int j = 0; j < 4; ++j) {
        const double dip = dipk[j] * f + ipk * dfs[j];
        o.J[0][j] = dip; o.J[1][j] = dipk[j] - dip;
    }
    o.J[2][0] = dig1; o.J[2][1] = 0.0; o.J[2][2] = 0.0; o.J[2][3] = 0.0;
}

// ---- per-sample Newton-Raphson (dk6as6/solver.py) -----------------------------------------------------------------

/* v = (Vg1k, Vg2k, Vg3k, Vak) at the returned iterate; i = the valve law there (as solver.py returns it: i - R);
 * it = iterations (summed over restarts); fv = ||K R||inf there (inf if no finite iterate); attempts = starts tried. */
struct Solution { double v[4]; double i[3]; int it; bool ok; double fv; int attempts; };

namespace detail {
// solver._resid: R = i - i(p - K i) and ||K R||inf
template <bool JAC>
inline void resid2(const double (&p)[4], const double (&K)[4][3], double i0, double i1, const PentodeParams& par,
                   double& r0, double& r1, double& fv, double (&v)[4], Law& L) {
    for (int r = 0; r < 4; ++r) v[r] = p[r] - K[r][0] * i0 - K[r][1] * i1;
    currents<JAC>(v, par, L);
    r0 = i0 - L.i[0]; r1 = i1 - L.i[1];
    fv = std::fabs(K[0][0] * r0 + K[0][1] * r1);
    for (int r = 1; r < 4; ++r) fv = pmax(fv, std::fabs(K[r][0] * r0 + K[r][1] * r1));
}
}  // namespace detail

// solver.solve: the two-current law (no grid current); K is 4x3 storage, its first two columns used
inline Solution solve(const double (&p)[4], const double (&K)[4][3], double i0, double i1, const PentodeParams& par,
                      const Newton& nw) {
    using detail::resid2;
    Solution best{};
    double r0, r1, fv, v[4];
    Law L;
    resid2<true>(p, K, i0, i1, par, r0, r1, fv, v, L);
    double fb = fv;
    auto keep = [&](Solution& b) {
        for (int k = 0; k < 4; ++k) b.v[k] = v[k];
        b.i[0] = i0 - r0; b.i[1] = i1 - r1; b.i[2] = 0.0;
    };
    keep(best);
    for (int it = 1; it <= nw.max_it; ++it) {
        const auto& J = L.J;
        const double a = 1.0 + detail::pysum4(J[0][0] * K[0][0], J[0][1] * K[1][0], J[0][2] * K[2][0], J[0][3] * K[3][0]);
        const double b = detail::pysum4(J[0][0] * K[0][1], J[0][1] * K[1][1], J[0][2] * K[2][1], J[0][3] * K[3][1]);
        const double c = detail::pysum4(J[1][0] * K[0][0], J[1][1] * K[1][0], J[1][2] * K[2][0], J[1][3] * K[3][0]);
        const double d = 1.0 + detail::pysum4(J[1][0] * K[0][1], J[1][1] * K[1][1], J[1][2] * K[2][1], J[1][3] * K[3][1]);
        const double det = a * d - b * c;
        const double s0 = -(d * r0 - b * r1) / det;
        const double s1 = -(-c * r0 + a * r1) / det;
        double lam = 1.0;
        double g0, g1, gv, gvolt[4];
        Law gL;
        resid2<true>(p, K, i0 + s0, i1 + s1, par, g0, g1, gv, gvolt, gL);
        if (!(gv < fv || fv < nw.tol_f)) {
            for (int h = 0; h < LS_MAX - 1; ++h) {
                lam *= 0.5;
                resid2<false>(p, K, i0 + lam * s0, i1 + lam * s1, par, g0, g1, gv, gvolt, gL);
                if (gv < fv) break;
            }
            currents<true>(gvolt, par, gL);      // the Jacobian at the accepted point (its currents equal gL's)
        }
        double dv = std::fabs(K[0][0] * lam * s0 + K[0][1] * lam * s1);
        for (int r = 1; r < 4; ++r) dv = detail::pmax(dv, std::fabs(K[r][0] * lam * s0 + K[r][1] * lam * s1));
        i0 = i0 + lam * s0; i1 = i1 + lam * s1;
        r0 = g0; r1 = g1; fv = gv;
        for (int k = 0; k < 4; ++k) v[k] = gvolt[k];
        for (int q = 0; q < 2; ++q) for (int j = 0; j < 4; ++j) L.J[q][j] = gL.J[q][j];
        if (dv < nw.tol_v && fv < nw.tol_f) {
            Solution s{};
            keep(s);
            s.it = it; s.ok = true; s.fv = fv; s.attempts = 1;
            return s;
        }
        if ((fv < fb || !(fb < INFINITY)) && std::fabs(r0) + std::fabs(r1) < INFINITY) { fb = fv; keep(best); }
    }
    best.it = nw.max_it; best.ok = false; best.fv = fb; best.attempts = 1;
    return best;
}

namespace detail {
// solver._resid3_safe: false where Python's evaluation raises, or the residual (or, with JAC, the Jacobian) is not
// finite: the trial counts as failed
template <bool JAC>
inline bool resid3(const double (&p)[4], const double (&K)[4][3], const double (&i)[3], const PentodeParams& par,
                   const GridParams& g, double (&r)[3], double& fv, double (&v)[4], Law& L) {
    for (int q = 0; q < 4; ++q) v[q] = p[q] - K[q][0] * i[0] - K[q][1] * i[1] - K[q][2] * i[2];
    currents_grid<JAC>(v, par, g, L);
    if (L.raised) { fv = INFINITY; return false; }
    for (int k = 0; k < 3; ++k) r[k] = i[k] - L.i[k];
    fv = std::fabs(K[0][0] * r[0] + K[0][1] * r[1] + K[0][2] * r[2]);
    for (int q = 1; q < 4; ++q) fv = pmax(fv, std::fabs(K[q][0] * r[0] + K[q][1] * r[1] + K[q][2] * r[2]));
    if (!(fv < INFINITY)) { fv = INFINITY; return false; }
    if (JAC)
        for (int a = 0; a < 3; ++a) for (int b = 0; b < 4; ++b) if (!std::isfinite(L.J[a][b])) { fv = INFINITY; return false; }
    return true;
}
// solver._step3: -(I + J K)^-1 r by Cramer's rule; false where Python's division by det raises (det == 0)
inline bool step3(const double (&J)[3][4], const double (&K)[4][3], const double (&r)[3], double (&x)[3]) {
    const double j00 = J[0][0], j01 = J[0][1], j02 = J[0][2], j03 = J[0][3];
    const double j10 = J[1][0], j11 = J[1][1], j12 = J[1][2], j13 = J[1][3], j20 = J[2][0];
    const double m00 = 1.0 + j00 * K[0][0] + j01 * K[1][0] + j02 * K[2][0] + j03 * K[3][0];
    const double m01 = j00 * K[0][1] + j01 * K[1][1] + j02 * K[2][1] + j03 * K[3][1];
    const double m02 = j00 * K[0][2] + j01 * K[1][2] + j02 * K[2][2] + j03 * K[3][2];
    const double m10 = j10 * K[0][0] + j11 * K[1][0] + j12 * K[2][0] + j13 * K[3][0];
    const double m11 = 1.0 + j10 * K[0][1] + j11 * K[1][1] + j12 * K[2][1] + j13 * K[3][1];
    const double m12 = j10 * K[0][2] + j11 * K[1][2] + j12 * K[2][2] + j13 * K[3][2];
    const double m20 = j20 * K[0][0], m21 = j20 * K[0][1], m22 = 1.0 + j20 * K[0][2];
    const double r0 = r[0], r1 = r[1], r2 = r[2];
    const double c0 = m11 * m22 - m12 * m21;
    const double c1 = m12 * m20 - m10 * m22;
    const double c2 = m10 * m21 - m11 * m20;
    const double det = m00 * c0 + m01 * c1 + m02 * c2;
    if (det == 0.0) return false;
    const double x0 = (r0 * c0 + m01 * (r2 * m12 - r1 * m22) + m02 * (r1 * m21 - r2 * m11)) / det;
    const double x1 = (m00 * (r1 * m22 - r2 * m12) + r0 * c1 + m02 * (r2 * m10 - r1 * m20)) / det;
    const double x2 = (m00 * (r2 * m11 - r1 * m21) + m01 * (r1 * m20 - r2 * m10) + r0 * c2) / det;
    x[0] = -x0; x[1] = -x1; x[2] = -x2;
    return true;
}
}  // namespace detail

// solver.solve_grid: the three-current law (#112), K 4x3, i the start
inline Solution solve_grid(const double (&p)[4], const double (&K)[4][3], const double (&i_start)[3],
                           const PentodeParams& par, const GridParams& g, const Newton& nw) {
    double i[3] = {i_start[0], i_start[1], i_start[2]}, r[3], fv, v[4];
    Law L;
    Solution best{};
    if (!detail::resid3<true>(p, K, i, par, g, r, fv, v, L)) {
        for (int q = 0; q < 4; ++q) best.v[q] = p[q] - K[q][0] * i[0] - K[q][1] * i[1] - K[q][2] * i[2];
        for (int k = 0; k < 3; ++k) best.i[k] = i[k];
        best.it = 0; best.ok = false; best.fv = INFINITY; best.attempts = 1;
        return best;
    }
    auto keep = [&](Solution& b) {
        for (int q = 0; q < 4; ++q) b.v[q] = v[q];
        for (int k = 0; k < 3; ++k) b.i[k] = i[k] - r[k];
    };
    double fb = fv;
    keep(best);
    int it = 0;
    for (it = 1; it <= nw.max_it; ++it) {
        double s[3];
        if (!detail::step3(L.J, K, r, s)) break;                          // singular I + J K
        if (!(std::isfinite(s[0]) && std::isfinite(s[1]) && std::isfinite(s[2]))) break;
        double lam = 1.0, t[3] = {i[0] + s[0], i[1] + s[1], i[2] + s[2]}, gr[3], gv, gvolt[4];
        Law gL;
        bool gok = detail::resid3<true>(p, K, t, par, g, gr, gv, gvolt, gL);
        if (!(gv < fv || fv < nw.tol_f)) {
            for (int h = 0; h < LS_MAX - 1; ++h) {
                lam *= 0.5;
                for (int k = 0; k < 3; ++k) t[k] = i[k] + lam * s[k];
                gok = detail::resid3<false>(p, K, t, par, g, gr, gv, gvolt, gL);
                if (gv < fv) break;
            }
            if (gok) gok = detail::resid3<true>(p, K, t, par, g, gr, gv, gvolt, gL);
        }
        if (!gok) break;                                                    // no finite trial along the step
        double dv = std::fabs(lam * (K[0][0] * s[0] + K[0][1] * s[1] + K[0][2] * s[2]));
        for (int q = 1; q < 4; ++q) dv = detail::pmax(dv, std::fabs(lam * (K[q][0] * s[0] + K[q][1] * s[1] + K[q][2] * s[2])));
        for (int k = 0; k < 3; ++k) { i[k] = t[k]; r[k] = gr[k]; }
        fv = gv;
        for (int q = 0; q < 4; ++q) v[q] = gvolt[q];
        L = gL;
        if (dv < nw.tol_v && fv < nw.tol_f) {
            Solution s2{};
            keep(s2);
            s2.it = it; s2.ok = true; s2.fv = fv; s2.attempts = 1;
            return s2;
        }
        if (fv < fb) { fb = fv; keep(best); }
    }
    if (it > nw.max_it) it = nw.max_it;     // Python's loop variable after a full range(1, max_it + 1)
    best.it = it; best.ok = false; best.fv = fb; best.attempts = 1;
    return best;
}

// solver.solve_grid_restarts: each start in turn until one converges; else the smallest finite residual of all
inline Solution solve_grid_restarts(const double (&p)[4], const double (&K)[4][3], const double (&starts)[3][3],
                                    const PentodeParams& par, const GridParams& g, const Newton& nw) {
    Solution best{};
    bool have = false;
    int total = 0;
    for (int k = 0; k < 3; ++k) {
        Solution s = solve_grid(p, K, starts[k], par, g, nw);
        total += s.it;
        if (s.ok) { s.it = total; s.attempts = k + 1; return s; }
        if (!have || s.fv < best.fv) { best = s; have = true; }
    }
    best.it = total; best.ok = false; best.attempts = 3;
    return best;
}

// ---- the netlist and its nodal-DK matrices (dk6as6/circuit.py) -----------------------------------------------------

// circuit.NODES: src in1 gl g k a out ht g2
enum Node { SRC, IN1, GL, GRID, CATH, ANODE, OUT, HTN, G2N, NNODES };
inline constexpr int GND = -1;
inline constexpr int NX = 7, NV = 4, NI_MAX = 3, NU_MAX = 3, NOUT = 5, NR_MAX = 7, NS_MAX = NNODES + NU_MAX;

namespace detail {
struct Pair { int p, q; };   // node indices, GND for ground
// circuit.MODES pins: P, G2, G3, G1, K -> nodes; and the pin CG3 goes to from g3
struct Pins { int P, G2, G3, G1, K, cg3; };
inline Pins pins(Mode m) {
    if (m == Mode::T) return {ANODE, G2N, G2N, GRID, CATH, CATH};
    if (m == Mode::P2) return {ANODE, G2N, GND, GRID, CATH, CATH};
    return {ANODE, G2N, CATH, GRID, CATH, G2N};          // P1: cg3 = "G2", the g2 node
}
inline bool strapped(Mode m) { return m == Mode::T; }  // screen fed from the plate (no EC2 source)

// circuit.valve_caps: (pins[a], pins[b]) or, with pin a grounded, (pins[b], ground)
inline Pair cap_pair(int a, int b) { return a != GND ? Pair{a, b} : Pair{b, GND}; }

// a small dense matrix with runtime dimensions (non-RT build only)
struct DM {
    int r = 0, c = 0;
    double a[NS_MAX][NS_MAX];
    DM() { std::memset(a, 0, sizeof a); }
    DM(int rr, int cc) : r(rr), c(cc) { std::memset(a, 0, sizeof a); }
};
/* Compensated dot product (Ogita, Rump & Oishi 2005, "Accurate sum and dot product", Dot2): the result is as accurate
 * as if computed in twice the working precision, then rounded. The products of the build cancel (the incidence
 * matrices take differences of entries of Si), so naive sums leave the matrices ~1e-12 from exact; with Dot2 and the
 * refined inverse below they round once (cpp/matrix_rounding_6as6.py). Non-RT only. */
inline void two_sum(double a, double b, double& s, double& e) { s = a + b; const double z = s - a; e = (a - (s - z)) + (b - z); }
// sum_k x[k] y[k] + init, compensated; x and y are contiguous arrays of n values: a row of DM::a, or a column copied
// out by column(), so no pointer strides across the rows of the 2-D array
inline double dot2(const double* x, const double* y, int n, double init = 0.0) {
    double s = init, c = 0.0;
    for (int k = 0; k < n; ++k) {
        const double p = x[k] * y[k], pe = std::fma(x[k], y[k], -p);
        double t, te;
        two_sum(s, p, t, te);
        s = t; c += te + pe;
    }
    return s + c;
}
inline void column(const DM& m, int j, double (&c)[NS_MAX]) { for (int k = 0; k < m.r; ++k) c[k] = m.a[k][j]; }
inline DM mul(const DM& x, const DM& y) {
    DM o(x.r, y.c);
    double c[NS_MAX];
    for (int j = 0; j < y.c; ++j) {
        column(y, j, c);
        for (int i = 0; i < x.r; ++i) o.a[i][j] = dot2(x.a[i], c, x.c);
    }
    return o;
}
inline DM tr(const DM& x) {
    DM o(x.c, x.r);
    for (int i = 0; i < x.r; ++i) for (int j = 0; j < x.c; ++j) o.a[j][i] = x.a[i][j];
    return o;
}
inline bool invert(const DM& in, DM& out) {        // Gauss-Jordan, partial pivoting (as dk_core.h), refined
    const int N = in.r;
    DM a = in;
    out = DM(N, N);
    for (int i = 0; i < N; ++i) out.a[i][i] = 1.0;
    for (int col = 0; col < N; ++col) {
        int piv = col;
        for (int r = col + 1; r < N; ++r) if (std::fabs(a.a[r][col]) > std::fabs(a.a[piv][col])) piv = r;
        if (!(std::fabs(a.a[piv][col]) > 0.0)) return false;
        if (piv != col)
            for (int c = 0; c < N; ++c) { std::swap(a.a[piv][c], a.a[col][c]); std::swap(out.a[piv][c], out.a[col][c]); }
        const double inv = 1.0 / a.a[col][col];
        for (int c = 0; c < N; ++c) { a.a[col][c] *= inv; out.a[col][c] *= inv; }
        for (int r = 0; r < N; ++r) {
            if (r == col || a.a[r][col] == 0.0) continue;
            const double f = a.a[r][col];
            for (int c = 0; c < N; ++c) { a.a[r][c] -= f * a.a[col][c]; out.a[r][c] -= f * out.a[col][c]; }
        }
    }
    // iterative refinement with a compensated residual: X <- X + X (I - S X), twice
    for (int pass = 0; pass < 2; ++pass) {
        DM R(N, N);
        double c[NS_MAX];
        for (int j = 0; j < N; ++j) {
            column(out, j, c);
            for (int i = 0; i < N; ++i) R.a[i][j] = -dot2(in.a[i], c, N, i == j ? -1.0 : 0.0);
        }
        DM X = out;
        for (int j = 0; j < N; ++j) {
            column(R, j, c);
            for (int i = 0; i < N; ++i) out.a[i][j] = dot2(X.a[i], c, N, X.a[i][j]);
        }
    }
    return true;
}
// circuit._inc: +1 on p, -1 on q (accumulated: p == q gives a zero row)
inline DM incidence(const Pair* pairs, int n) {
    DM m(n, NNODES);
    for (int r = 0; r < n; ++r) {
        if (pairs[r].p != GND) m.a[r][pairs[r].p] += 1.0;
        if (pairs[r].q != GND) m.a[r][pairs[r].q] -= 1.0;
    }
    return m;
}
inline DM pad(const DM& m, int nu) {
    DM o(m.r, m.c + nu);
    for (int i = 0; i < m.r; ++i) for (int j = 0; j < m.c; ++j) o.a[i][j] = m.a[i][j];
    return o;
}

// circuit.build's dict, the parts the core needs
struct Full {
    int nu = 0, ni = 0;
    DM A, B, C, D, E, F, G, H, K, Si, Nup, Nip, Nx;
    double gx[NX] = {};
};

// circuit.build(fs, rk, dc, prewarp_hz=None, mode, grid): the same products in the same association
inline bool build_full(Full& m, double fs, double rk, bool dc, Mode mode, bool grid, const Values& v) {
    const Pins pn = pins(mode);
    Pair R[NR_MAX] = {{SRC, IN1}, {GL, GND}, {GL, GRID}, {HTN, ANODE}, {CATH, GND}, {OUT, GND}, {G2N, ANODE}};
    double rv[NR_MAX] = {v.Rs, v.Rg, v.Rgs, v.Ra, rk, v.Rl, v.Rstrap};
    const int nr = strapped(mode) ? 7 : 6;
    // [Cin, Cout] + valve caps in circuit.valve_cap_pins order: CG1K (G1, K), CG3 (G3, cg3), CPK (P, K),
    // CG1P (G1, P), CG1G3 (G1, G3)
    const Pair Cp[NX] = {{IN1, GL}, {ANODE, OUT}, cap_pair(pn.G1, pn.K), cap_pair(pn.G3, pn.cg3),
                         cap_pair(pn.P, pn.K), cap_pair(pn.G1, pn.P), cap_pair(pn.G1, pn.G3)};
    const double cv[NX] = {v.Cin, v.Cout, v.CG1K, v.CG3, v.CPK, v.CG1P, v.CG1G3};
    const Pair NVp[NV] = {{pn.G1, pn.K}, {pn.G2, pn.K}, {pn.G3, pn.K}, {pn.P, pn.K}};
    const Pair NIp[NI_MAX] = {{ANODE, CATH}, {G2N, CATH}, {GRID, CATH}};
    const Pair NOp[NOUT] = {{OUT, GND}, {GRID, GND}, {CATH, GND}, {ANODE, GND}, {GL, GND}};
    const int U[NU_MAX] = {SRC, HTN, G2N};
    const int nu = strapped(mode) ? 2 : 3, ni = grid ? 3 : 2;
    m.nu = nu; m.ni = ni;
    const DM Nr = incidence(R, nr), Nx = incidence(Cp, NX), Nv = incidence(NVp, NV), Ni = incidence(NIp, ni),
             No = incidence(NOp, NOUT);
    DM Nu(nu, NNODES);
    for (int r = 0; r < nu; ++r) Nu.a[r][U[r]] = 1.0;
    double gr[NR_MAX];
    for (int i = 0; i < nr; ++i) gr[i] = 1.0 / rv[i];
    for (int i = 0; i < NX; ++i) m.gx[i] = dc ? 0.0 : 2.0 * fs * cv[i];        // companion_g, no pre-warp
    // S = [[Nr^T Gr Nr + Nx^T Gx Nx, Nu^T], [Nu, 0]]  (numpy: (Nr.T @ Gr) @ Nr)
    DM GrD(nr, nr), GxD(NX, NX);
    for (int i = 0; i < nr; ++i) GrD.a[i][i] = gr[i];
    for (int i = 0; i < NX; ++i) GxD.a[i][i] = m.gx[i];
    const DM Y1 = mul(mul(tr(Nr), GrD), Nr), Y2 = mul(mul(tr(Nx), GxD), Nx);
    const int ns = NNODES + nu;
    DM S(ns, ns);
    for (int i = 0; i < NNODES; ++i) for (int j = 0; j < NNODES; ++j) S.a[i][j] = Y1.a[i][j] + Y2.a[i][j];
    for (int r = 0; r < nu; ++r)
        for (int c = 0; c < NNODES; ++c) { S.a[c][NNODES + r] = Nu.a[r][c]; S.a[NNODES + r][c] = Nu.a[r][c]; }
    if (!invert(S, m.Si)) return false;
    const DM Nxp = pad(Nx, nu), Nvp = pad(Nv, nu), Nop = pad(No, nu);
    m.Nip = pad(Ni, nu);
    m.Nup = DM(nu, ns);
    for (int r = 0; r < nu; ++r) m.Nup.a[r][NNODES + r] = 1.0;
    // 2 * Gx @ Nxp @ Si: ((2 Gx) @ Nxp) @ Si
    DM G2x(NX, NX);
    for (int i = 0; i < NX; ++i) G2x.a[i][i] = 2.0 * m.gx[i];
    const DM XSi = mul(mul(G2x, Nxp), m.Si);
    m.A = mul(XSi, tr(Nxp));
    for (int i = 0; i < NX; ++i) m.A.a[i][i] -= 1.0;
    m.B = mul(XSi, tr(m.Nup));
    m.C = mul(XSi, tr(m.Nip));
    for (int i = 0; i < m.C.r; ++i) for (int j = 0; j < m.C.c; ++j) m.C.a[i][j] = -m.C.a[i][j];
    const DM OSi = mul(Nop, m.Si), VSi = mul(Nvp, m.Si);
    m.D = mul(OSi, tr(Nxp)); m.E = mul(OSi, tr(m.Nup));
    m.F = mul(OSi, tr(m.Nip));
    for (int i = 0; i < m.F.r; ++i) for (int j = 0; j < m.F.c; ++j) m.F.a[i][j] = -m.F.a[i][j];
    m.G = mul(VSi, tr(Nxp)); m.H = mul(VSi, tr(m.Nup)); m.K = mul(VSi, tr(m.Nip));
    m.Nx = Nx;
    return true;
}
}  // namespace detail

// ---- the pack: everything the audio thread reads, plus the DC state reset() starts from ---------------------------

enum class Status : std::uint8_t {
    ok, bad_argument, unsupported_setting, bias_out_of_range, singular_matrix, dc_nonconvergence, rk_nonconvergence
};
inline const char* to_string(Status s) {
    switch (s) {
        case Status::ok: return "ok";
        case Status::bad_argument: return "bad_argument";
        case Status::unsupported_setting: return "unsupported_setting";
        case Status::bias_out_of_range: return "bias_out_of_range";
        case Status::singular_matrix: return "singular_matrix";
        case Status::dc_nonconvergence: return "dc_nonconvergence";
        case Status::rk_nonconvergence: return "rk_nonconvergence";
    }
    return "?";
}

// stage.operating_point: v = (Vg1k, Vg2k, Vg3k, Vak), i = (Ip, Ig2, Ig1), ik = the cathode current, node voltages
struct OperatingPoint { double v[4]; double i[3]; double ik; double nodes[NNODES]; };

/* Matrix rows / columns as circuit.build: x and A..C rows the capacitors Cin, Cout, CG1K, CG3, CPK, CG1P, CG1G3;
 * u = (vin, HT, EC2) in P1 / P2, (vin, HT) in T; i = (Ip, Ig2[, Ig1]); v = (Vg1k, Vg2k, Vg3k, Vak); D..F rows: out, g,
 * k, a, gl. Unused columns (nu, ni below their maxima) are zero. Plain data: trivially copyable. */
struct Pack {
    double fs = 0.0;                      // internal rate
    double bias_ma = BIAS_MA, rk = 0.0;   // target cathode current (mA) and the solved rheostat (ohm)
    Mode mode = Mode::P1;
    bool grid = true;                     // the g1 current port (hardness > 0)
    int nu = 3, ni = 3;
    PentodeParams par = DUAL_CONTROL;
    GridParams g{0.0, 0.0, 0.0, 0.0};
    Newton newton{};
    double A[NX][NX], B[NX][NU_MAX], C[NX][NI_MAX], D[NOUT][NX], E[NOUT][NU_MAX], F[NOUT][NI_MAX], G[NV][NX],
           H[NV][NU_MAX], K[NV][NI_MAX];
    OperatingPoint op;
    double supplies[2];                   // u without the input: (HT, EC2), or (HT) in T
    // the deviation form's rest constants (stage.Stage.reset): x_dc = Gx Nx vn, p_dc = v_dc + K i_dc,
    // y_dc = D x_dc + E[:, 1:] supplies + F i_dc
    double x_dc[NX], i_dc[NI_MAX], p_dc[NV], y_dc[NOUT];
    bool supported = true;
    int dc_step = 0, dc_iterations = 0;   // DC failure diagnostics (stage.OperatingPointError's message)
};
static_assert(std::is_trivially_copyable<Pack>::value, "Pack is plain data");

namespace detail {
template <int R, int C> inline void copy(double (&dst)[R][C], const DM& src) {
    for (int i = 0; i < R; ++i) for (int j = 0; j < C; ++j) dst[i][j] = (i < src.r && j < src.c) ? src.a[i][j] : 0.0;
}
inline int n_supplies(Mode m) { return strapped(m) ? 1 : 2; }
inline void supplies(Mode m, const Values& v, double (&s)[2]) { s[0] = v.HT; s[1] = strapped(m) ? 0.0 : v.EC2; }

// stage.operating_point: capacitors open, the stage's Newton solver from zero valve current, source-stepping all
// supplies in 10 steps (np.linspace(0.1, 1.0, 10)). With grid current ik = ip + ig2 + ig1.
inline Status operating_point(double rk, const PentodeParams& par, const Newton& nw, Mode mode, bool use_grid,
                              const GridParams& g, const Values& vals, OperatingPoint& op, int* fail_step = nullptr,
                              int* fail_it = nullptr) {
    Full m0;
    if (!build_full(m0, 1.0, rk, true, mode, use_grid, vals)) return Status::singular_matrix;
    double sup[2];
    supplies(mode, vals, sup);
    const double u[NU_MAX] = {0.0, sup[0], sup[1]};
    double p[4];
    for (int r = 0; r < 4; ++r) {
        double s = 0.0;
        for (int c = 0; c < m0.nu; ++c) s += m0.H.a[r][c] * u[c];
        p[r] = s;
    }
    double K0[4][3];
    copy(K0, m0.K);
    double i[3] = {0.0, 0.0, 0.0}, v[4] = {};
    const double step = (1.0 - 0.1) / 9.0;
    for (int k = 1; k <= 10; ++k) {
        const double s = k == 10 ? 1.0 : (k - 1) * step + 0.1;
        const double ps[4] = {p[0] * s, p[1] * s, p[2] * s, p[3] * s};
        Solution r = use_grid ? solve_grid(ps, K0, i, par, g, nw) : solve(ps, K0, i[0], i[1], par, nw);
        for (int q = 0; q < 4; ++q) v[q] = r.v[q];
        i[0] = r.i[0]; i[1] = r.i[1]; i[2] = use_grid ? r.i[2] : 0.0;
        if (!r.ok) {
            if (fail_step) *fail_step = k;
            if (fail_it) *fail_it = r.it;
            return Status::dc_nonconvergence;
        }
    }
    // w = Si (Nup^T u - Nip^T i)
    const int ns = NNODES + m0.nu;
    double rhs[NS_MAX];
    for (int j = 0; j < ns; ++j) {
        double a = 0.0, b = 0.0;
        for (int c = 0; c < m0.nu; ++c) a += m0.Nup.a[c][j] * u[c];
        for (int c = 0; c < m0.ni; ++c) b += m0.Nip.a[c][j] * i[c];
        rhs[j] = a - b;
    }
    for (int n = 0; n < NNODES; ++n) {
        double s = 0.0;
        for (int j = 0; j < ns; ++j) s += m0.Si.a[n][j] * rhs[j];
        op.nodes[n] = s;
    }
    for (int q = 0; q < 4; ++q) op.v[q] = v[q];
    for (int k = 0; k < 3; ++k) op.i[k] = i[k];
    op.ik = use_grid ? i[0] + i[1] + i[2] : i[0] + i[1];
    return Status::ok;
}

// scipy.optimize.brentq, maxiter 100, as stage.cathode_resistor calls it. f returns a Status other than ok if an
// evaluation failed (Python raises: the error propagates). Returns the root in x.
// Adapted from SciPy's scipy/optimize/Zeros/brentq.c (SciPy 1.18.1, "Written by Charles Harris"): the control flow and
// expressions are kept, the callback, status codes and statistics adapted. Copyright (c) 2001-2002 Enthought, Inc.
// 2003, SciPy Developers. All rights reserved. Used under SciPy's BSD 3-Clause licence, whose full text, conditions and
// disclaimer are in THIRD_PARTY_NOTICES.md ("SciPy") at the repository root.
template <class Fn> inline Status brentq(Fn f, double xa, double xb, double xtol, double rtol, double& x) {
    double xpre = xa, xcur = xb, xblk = 0.0, fpre, fcur, fblk = 0.0, spre = 0.0, scur = 0.0, sbis, delta, stry, dpre, dblk;
    Status st;
    if ((st = f(xpre, fpre)) != Status::ok || (st = f(xcur, fcur)) != Status::ok) return st;
    if (fpre == 0) { x = xpre; return Status::ok; }
    if (fcur == 0) { x = xcur; return Status::ok; }
    if (std::signbit(fpre) == std::signbit(fcur)) return Status::bias_out_of_range;
    for (int i = 0; i < 100; ++i) {
        if (fpre != 0 && fcur != 0 && (std::signbit(fpre) != std::signbit(fcur))) {
            xblk = xpre; fblk = fpre;
            spre = scur = xcur - xpre;
        }
        if (std::fabs(fblk) < std::fabs(fcur)) {
            xpre = xcur; xcur = xblk; xblk = xpre;
            fpre = fcur; fcur = fblk; fblk = fpre;
        }
        delta = (xtol + rtol * std::fabs(xcur)) / 2;
        sbis = (xblk - xcur) / 2;
        if (fcur == 0 || std::fabs(sbis) < delta) { x = xcur; return Status::ok; }
        if (std::fabs(spre) > delta && std::fabs(fcur) < std::fabs(fpre)) {
            if (xpre == xblk) {
                stry = -fcur * (xcur - xpre) / (fcur - fpre);                 // interpolate
            } else {
                dpre = (fpre - fcur) / (xpre - xcur);                         // extrapolate
                dblk = (fblk - fcur) / (xblk - xcur);
                stry = -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre));
            }
            const double lim = std::fabs(spre) < 3 * std::fabs(sbis) - delta ? std::fabs(spre) : 3 * std::fabs(sbis) - delta;
            if (2 * std::fabs(stry) < lim) { spre = scur; scur = stry; }   // good short step
            else { spre = sbis; scur = sbis; }                              // bisect
        } else {
            spre = sbis; scur = sbis;                                       // bisect
        }
        xpre = xcur; fpre = fcur;
        if (std::fabs(scur) > delta) xcur += scur;
        else xcur += (sbis > 0 ? delta : -delta);
        if ((st = f(xcur, fcur)) != Status::ok) return st;
    }
    x = xcur;
    return Status::rk_nonconvergence;
}
}  // namespace detail

/* stage.cathode_resistor: the Rk (ohm) whose DC operating point in `mode` draws bias_ma mA of cathode current: Brent
 * on log10(Rk) over RK_LOG10, xtol 1e-13, rtol 4 eps (Ik falls monotonically with Rk). Non-RT. */
inline Status cathode_resistor(double bias_ma, const PentodeParams& par, const Newton& nw, Mode mode, bool use_grid,
                               const GridParams& g, const Values& vals, double& rk) {
    if (!(bias_ma > 0.0) || !std::isfinite(bias_ma)) return Status::bad_argument;
    const double ik = bias_ma * 1e-3;
    auto err = [&](double lg, double& e) {
        OperatingPoint op{};
        const Status st = detail::operating_point(std::pow(10.0, lg), par, nw, mode, use_grid, g, vals, op);
        e = st == Status::ok ? op.ik / ik - 1.0 : NAN;
        return st;
    };
    double elo, ehi;
    Status st;
    if ((st = err(RK_LOG10[0], elo)) != Status::ok || (st = err(RK_LOG10[1], ehi)) != Status::ok) return st;
    if (!(elo > 0.0 && 0.0 > ehi)) return Status::bias_out_of_range;
    double root = 0.0;
    st = detail::brentq(err, RK_LOG10[0], RK_LOG10[1], 1e-13, 4 * DBL_EPSILON, root);
    if (st != Status::ok) return st;
    rk = std::pow(10.0, root);
    return Status::ok;
}

// What a setting is: everything prepare() builds from (stage.Stage's constructor arguments)
struct Settings {
    double bias_ma = BIAS_MA;            // target DC cathode current, mA (#110)
    Mode mode = Mode::P1;                // #111
    double grid_hardness = HARDNESS;     // #112: 1 (default) = the borrowed grid law, 0 = none
    Lpf lpf = Lpf::off;                  // #103: the initial post-filter position (set_lpf() changes it)
    bool experimental = false;           // false: settings outside supported() are refused (Python raises)
    Newton newton{};                     // solver.MAX_IT / TOL_V / TOL_F (dk/solver.py, the 12AX7 stage's, unchanged)
    PentodeParams par = DUAL_CONTROL;
    Values values{};
    FirDesign fir = DEFAULT_FIR;
};

/* Build a pack at internal rate fs (stage.Stage.__init__ minus the resampler): Rk for the target current, the matrices
 * at fs, the DC operating point at that Rk, and the deviation form's rest constants. Non-RT, no allocation. */
namespace detail {
// Explicit grid fits are used by the offline #148 probe. The shipped prepare path supplies the nominal fit.
// rk_given (#115): build at that Rk instead of solving it for s.bias_ma (build_pack_at_rk).
inline Status build_pack_with_grid(Pack& out, double fs, const Settings& s, const GridParams* g,
                                   const double* rk_given = nullptr) {
    if (!(fs > 0.0) || !std::isfinite(fs) || !(s.bias_ma > 0.0) || !std::isfinite(s.bias_ma) || !(s.grid_hardness >= 0.0)
        || !std::isfinite(s.grid_hardness) || s.newton.max_it < 1 || !(s.newton.tol_v >= 0.0) || !(s.newton.tol_f >= 0.0))
        return Status::bad_argument;
    if (rk_given && !(*rk_given > 0.0 && std::isfinite(*rk_given))) return Status::bad_argument;
    Pack c{};
    c.fs = fs; c.bias_ma = s.bias_ma; c.mode = s.mode; c.par = s.par; c.newton = s.newton;
    c.grid = s.grid_hardness != 0.0;
    c.g = g ? *g : grid(s.grid_hardness, s.par);
    c.supported = supported(s.mode, s.bias_ma, s.grid_hardness);
    if (!c.supported && !s.experimental) return Status::unsupported_setting;
    Status st = Status::ok;
    if (rk_given) c.rk = *rk_given;
    else st = cathode_resistor(s.bias_ma, s.par, s.newton, s.mode, c.grid, c.g, s.values, c.rk);
    if (st != Status::ok) return st;
    detail::Full m;
    if (!detail::build_full(m, fs, c.rk, false, s.mode, c.grid, s.values)) return Status::singular_matrix;
    c.nu = m.nu; c.ni = m.ni;
    detail::copy(c.A, m.A); detail::copy(c.B, m.B); detail::copy(c.C, m.C); detail::copy(c.D, m.D);
    detail::copy(c.E, m.E); detail::copy(c.F, m.F); detail::copy(c.G, m.G); detail::copy(c.H, m.H);
    detail::copy(c.K, m.K);
    st = detail::operating_point(c.rk, s.par, s.newton, s.mode, c.grid, c.g, s.values, c.op, &c.dc_step,
                                 &c.dc_iterations);
    if (st != Status::ok) { out = c; return st; }
    detail::supplies(s.mode, s.values, c.supplies);
    // rest constants (stage.Stage.reset): x_dc = (Gx Nx) vn
    for (int i = 0; i < NX; ++i) {
        double a = 0.0;
        for (int j = 0; j < NNODES; ++j) a += (m.gx[i] * m.Nx.a[i][j]) * c.op.nodes[j];
        c.x_dc[i] = a;
    }
    for (int k = 0; k < NI_MAX; ++k) c.i_dc[k] = k < c.ni ? c.op.i[k] : 0.0;
    for (int r = 0; r < NV; ++r) {
        double a = 0.0;
        for (int k = 0; k < c.ni; ++k) a += c.K[r][k] * c.i_dc[k];
        c.p_dc[r] = c.op.v[r] + a;
    }
    const int ns = detail::n_supplies(s.mode);
    for (int r = 0; r < NOUT; ++r) {
        double a = 0.0, b = 0.0, f = 0.0;
        for (int j = 0; j < NX; ++j) a += c.D[r][j] * c.x_dc[j];
        for (int j = 0; j < ns; ++j) b += c.E[r][1 + j] * c.supplies[j];
        for (int k = 0; k < c.ni; ++k) f += c.F[r][k] * c.i_dc[k];
        c.y_dc[r] = a + b + f;
    }
    out = c;
    return Status::ok;
}
}  // namespace detail

inline Status build_pack(Pack& out, double fs, const Settings& s) {
    return detail::build_pack_with_grid(out, fs, s, nullptr);
}
// The pack at a given Rk (ohm), everything else as build_pack: matrices, DC operating point, rest constants. bias_ma
// is recorded as s.bias_ma (the caller's claim; the tests' continuous reference solves Rk for it, #115). Non-RT.
inline Status build_pack_at_rk(Pack& out, double fs, const Settings& s, double rk) {
    return detail::build_pack_with_grid(out, fs, s, nullptr, &rk);
}

// ---- the post-distortion low-pass (lpf.SVF, lpf.PostFilter) --------------------------------------------------------

// lpf.SVF, increment form, pre-warped at a fixed frequency
struct Svf {
    double a2 = 0.0, a3 = 0.0, b1 = 0.0, ic1 = 0.0, ic2 = 0.0;
    bool set(double fs, double f0, double q, double prewarp_hz) {
        if (!(0.0 < prewarp_hz && prewarp_hz < fs / 2) || !(0.0 < f0 && f0 < fs / 2)) return false;
        const double g = std::tan(PI * prewarp_hz / fs) * f0 / prewarp_hz;
        const double a1 = 1.0 / (1.0 + g * (g + 1.0 / q));
        a2 = g * a1; a3 = g * g * a1; b1 = g * (g + 1.0 / q) * a1;
        return true;
    }
    void reset() { ic1 = ic2 = 0.0; }
    double tick(double x) {
        const double v3 = x - ic2;
        const double d1 = a2 * v3 - b1 * ic1;
        const double d2 = a2 * ic1 + a3 * v3;
        const double y = ic2 + d2;
        ic1 = ic1 + 2 * d1; ic2 = ic2 + 2 * d2;
        return y;
    }
};

// lpf.PostFilter: both filters always run; the output is a linear crossfade over LPF_XFADE_S between positions
class PostFilter {
public:
    bool prepare(double fs, Lpf pos) {
        for (int k = 0; k < 2; ++k) if (!svf_[k].set(fs, LPF_F0_HZ[k], LPF_Q, LPF_PREWARP_HZ)) return false;
        const double n = std::nearbyint(LPF_XFADE_S * fs);                 // Python round(): half to even
        n_fade_ = n > 1.0 ? long(n) : 1;
        onehot(pos, g_to_);
        reset();
        return true;
    }
    void clear_states() { for (auto& f : svf_) f.reset(); }   // the SVF states only (recovery from a non-finite state)
    void reset() {                                       // states to zero, the mix settled on the target position
        for (auto& f : svf_) f.reset();
        for (int k = 0; k < 3; ++k) g_from_[k] = g_to_[k];
        t_ = n_fade_;
    }
    void set_position(Lpf pos) {                         // a new request mid-fade starts from wherever the mix is
        double g[3];
        gains(g);
        for (int k = 0; k < 3; ++k) g_from_[k] = g[k];
        onehot(pos, g_to_);
        t_ = 0;
    }
    Lpf position() const { return g_to_[1] == 1.0 ? Lpf::k4 : g_to_[2] == 1.0 ? Lpf::k7 : Lpf::off; }
    void gains(double (&g)[3]) const {
        const double w = std::fmin(double(t_) / double(n_fade_), 1.0);
        for (int k = 0; k < 3; ++k) g[k] = g_from_[k] + (g_to_[k] - g_from_[k]) * w;
    }
    double tick(double x) {
        const double y1 = svf_[0].tick(x), y2 = svf_[1].tick(x);
        const double w = std::fmin(double(t_ + 1) / double(n_fade_), 1.0);
        if (t_ < n_fade_) ++t_;
        double g[3];
        for (int k = 0; k < 3; ++k) g[k] = g_from_[k] + (g_to_[k] - g_from_[k]) * w;
        return g[0] * x + g[1] * y1 + g[2] * y2;
    }
    long fade_samples() const { return n_fade_; }

private:
    static void onehot(Lpf p, double (&g)[3]) { for (int k = 0; k < 3; ++k) g[k] = k == int(p) ? 1.0 : 0.0; }
    Svf svf_[2];
    long n_fade_ = 1, t_ = 1;
    double g_from_[3] = {1.0, 0.0, 0.0}, g_to_[3] = {1.0, 0.0, 0.0};
};

// ---- live bias (#115): the bias line, the Rk table and the rest constants -----------------------------------------

/* The #73 method (dk_core.h, "bias smoothing"; cpp/RESULTS-smoothing.md), ported to the 6AS6 stage's bias.
 *
 * The 6AS6 Bias is a target DC cathode current Ik (mA, #110); the stage realises it as the cathode rheostat Rk that
 * draws that current (cathode_resistor: Brent on log10 Rk, each evaluation a source-stepped DC solve; about 0.6 ms).
 * Rk is the one resistor from the cathode to ground, so, as in the 12AX7 stage, a bias change adds dg = 1/Rk - 1/Rk0 at
 * S[k][k] of the MNA matrix and nothing else. By Sherman-Morrison every matrix A..K of a pack is affine in
 * beta(g) = dg / (1 + dg Si(g0)[k][k]): the matrices at the two ends of the bias range give the exact matrices for any
 * Rk in between (BiasLine::matrices). The same holds for the DC network (capacitors open), with its own Si: the DC node
 * voltages for given valve currents i0 are affine in beta_dc(g), and with them the deviation form's rest constants
 * (#70), which are linear in the DC node voltages:
 *     x0 = Gx Nx nodes,   v0 = Nv nodes = Vs + Vi i0,   p_dc = v0 + K i0,   y_dc = No nodes = Ys + Yi i0
 * (BiasLine::rest). The deviation form is exact for ANY reference currents i0 (x0 is then the fixed point of the pack's
 * linear part), so during a move i0 stays fixed and only these are updated; no matrix is built or inverted.
 *
 * The 6AS6 stage has no cathode bypass capacitor: Rk is in the signal path, and a step of Rk steps the output at once
 * (the 12AX7 stage's Ck smooths it). So the bias moves at every INTERNAL sample (the 12AX7 line moves per base sample),
 * which a per-base-sample update would turn into an audible staircase (measured in cpp/RESULTS-smoothing-6as6.md).
 *
 * Rk(Ik) is a nonlinear DC problem. BiasLine keeps a table of it (NT nodes uniform in log Rk, each one source-stepped DC
 * solve at prepare time) and interpolates log Rk against log Ik with a cubic Hermite. exact() makes it exact for one
 * target on the audio thread: Newton on log Rk, each step one DC solve (the stage's own solver on the DC network at
 * full supply, warm-started from the table's currents), which also recentres i0 on the operating point. A move plans
 * its end point with exact() and corrects the table's Rk linearly along the move so that it starts at the pack's Rk and
 * lands on the exact one (StageT::line_plan): no jump at either end. Bounded audio-thread work, no allocation, no lock:
 * per internal sample of a move a table lookup, 208 + 72 multiply-adds; per plan at most EXACT_STEPS DC solves (each
 * at most 3 x newton.max_it iterations with grid current, as one sample of the stage). */
namespace detail {
struct PackMats {
    double A[NX][NX], B[NX][NU_MAX], C[NX][NI_MAX], D[NOUT][NX], E[NOUT][NU_MAX], F[NOUT][NI_MAX], G[NV][NX],
           H[NV][NU_MAX], K[NV][NI_MAX];
};
// the DC rest maps: x0 = Xs + Xi i, v0 = Vs + Vi i (valve port voltages; the DC problem's K is -Vi), y0 = Ys + Yi i
struct RestMaps {
    double Xs[NX], Xi[NX][NI_MAX], Vs[NV], Vi[NV][NI_MAX], Ys[NOUT], Yi[NOUT][NI_MAX];
};
template <int R, int C> inline void m_axpy(double (&o)[R][C], const double (&a)[R][C], double w, const double (&d)[R][C]) {
    for (int i = 0; i < R; ++i) for (int j = 0; j < C; ++j) o[i][j] = a[i][j] + w * d[i][j];
}
template <int R, int C> inline void m_sub(double (&o)[R][C], const double (&a)[R][C], const double (&b)[R][C]) {
    for (int i = 0; i < R; ++i) for (int j = 0; j < C; ++j) o[i][j] = a[i][j] - b[i][j];
}
template <int R, int C> inline void m_copy(double (&o)[R][C], const double (&a)[R][C]) { std::memcpy(o, a, sizeof o); }
template <int R> inline void v_sub(double (&o)[R], const double (&a)[R], const double (&b)[R]) {
    for (int i = 0; i < R; ++i) o[i] = a[i] - b[i];
}
// RestMaps at Rk from the DC build m0 (caps open) and the AC build's companion conductances gx and incidence Nx
inline void rest_maps(RestMaps& r, const Full& m0, const double (&gx)[NX], const DM& Nx, const double (&sup)[2], int ns) {
    const int nsz = NNODES + m0.nu;
    double rhs_s[NS_MAX] = {};
    for (int j = 0; j < nsz; ++j) {
        double a = 0.0;
        for (int c = 0; c < ns; ++c) a += m0.Nup.a[1 + c][j] * sup[c];
        rhs_s[j] = a;
    }
    double ns_v[NNODES], ni_v[NNODES][NI_MAX] = {};
    for (int n = 0; n < NNODES; ++n) {
        double a = 0.0;
        for (int j = 0; j < nsz; ++j) a += m0.Si.a[n][j] * rhs_s[j];
        ns_v[n] = a;
        for (int k = 0; k < m0.ni; ++k) {
            double b = 0.0;
            for (int j = 0; j < nsz; ++j) b += m0.Si.a[n][j] * m0.Nip.a[k][j];
            ni_v[n][k] = -b;
        }
    }
    for (int i = 0; i < NX; ++i) {
        double a = 0.0, b[NI_MAX] = {};
        for (int n = 0; n < NNODES; ++n) {
            a += Nx.a[i][n] * ns_v[n];
            for (int k = 0; k < NI_MAX; ++k) b[k] += Nx.a[i][n] * ni_v[n][k];
        }
        r.Xs[i] = gx[i] * a;
        for (int k = 0; k < NI_MAX; ++k) r.Xi[i][k] = gx[i] * b[k];
    }
    for (int q = 0; q < NV; ++q) {
        double a = 0.0;
        for (int c = 0; c < ns; ++c) a += m0.H.a[q][1 + c] * sup[c];
        r.Vs[q] = a;
        for (int k = 0; k < NI_MAX; ++k) r.Vi[q][k] = k < m0.ni ? -m0.K.a[q][k] : 0.0;
    }
    for (int q = 0; q < NOUT; ++q) {
        double a = 0.0;
        for (int c = 0; c < ns; ++c) a += m0.E.a[q][1 + c] * sup[c];
        r.Ys[q] = a;
        for (int k = 0; k < NI_MAX; ++k) r.Yi[q][k] = k < m0.ni ? m0.F.a[q][k] : 0.0;
    }
}
}  // namespace detail

/* One mode's bias line over [lo_ma, hi_ma]: the AC matrices and the DC rest maps at both ends (exact packs,
 * build_pack), the Rk(Ik) table, and the exact pack at lo_ma (base: everything that is not on the line). Plain data;
 * built by build_line() (non-RT), read-only afterwards, so any number of stages may share one. */
struct BiasLine {
    static constexpr int NT = 65;               // table nodes (its error: RESULTS-smoothing-6as6.md)
    static constexpr int EXACT_STEPS = 6;       // Newton steps of exact(), at most
    static constexpr double EXACT_TOL = 1e-13;  // |log(Ik / target)| at which exact() stops
    bool ok = false;
    double lo_ma = 0.0, hi_ma = 0.0;            // the range; targets are clamped to it
    double g0 = 0.0, d0 = 0.0, beta1 = 0.0;     // 1/Rk at lo_ma; AC: Si(lo_ma)[k][k], beta at hi_ma
    double d0dc = 0.0, beta1dc = 0.0;           // DC: the same
    detail::PackMats m0{}, dm{};                // the AC matrices at lo_ma; (at hi_ma) - (at lo_ma)
    detail::RestMaps r0{}, dr{};                // the DC rest maps at lo_ma; (at hi_ma) - (at lo_ma)
    double t[NT] = {}, y[NT] = {}, d[NT] = {};  // log Rk (uniform, t[0] at hi_ma), log(Ik / mA) (decreasing), dt/dy
    double cur[NT][NI_MAX] = {};                // the operating point's currents at each node (exact()'s warm start)
    Pack base{};                                // the exact pack at lo_ma

    double clamp(double ma) const { return ma < lo_ma ? lo_ma : (ma > hi_ma ? hi_ma : ma); }
    // the AC and DC weights of Rk = rk: 0 at lo_ma, 1 at hi_ma
    void weights(double rk, double& wac, double& wdc) const {
        const double dg = 1.0 / rk - g0;
        wac = dg / (1.0 + dg * d0) / beta1;
        wdc = dg / (1.0 + dg * d0dc) / beta1dc;
    }
    // c.A..K and c.rk at Rk = rk, AC weight wac (the rest of c untouched). 208 multiply-adds.
    void matrices(Pack& c, double rk, double wac) const noexcept {
        detail::m_axpy(c.A, m0.A, wac, dm.A); detail::m_axpy(c.B, m0.B, wac, dm.B); detail::m_axpy(c.C, m0.C, wac, dm.C);
        detail::m_axpy(c.D, m0.D, wac, dm.D); detail::m_axpy(c.E, m0.E, wac, dm.E); detail::m_axpy(c.F, m0.F, wac, dm.F);
        detail::m_axpy(c.G, m0.G, wac, dm.G); detail::m_axpy(c.H, m0.H, wac, dm.H); detail::m_axpy(c.K, m0.K, wac, dm.K);
        c.rk = rk;
    }
    // c's rest constants for reference currents i0 at DC weight wdc (c.K already at this Rk): x_dc, i_dc, p_dc, y_dc
    void rest(Pack& c, double wdc, const double (&i0)[NI_MAX]) const noexcept {
        for (int i = 0; i < NX; ++i) {
            double a = r0.Xs[i] + wdc * dr.Xs[i];
            for (int k = 0; k < NI_MAX; ++k) a += (r0.Xi[i][k] + wdc * dr.Xi[i][k]) * i0[k];
            c.x_dc[i] = a;
        }
        for (int q = 0; q < NV; ++q) {
            double a = r0.Vs[q] + wdc * dr.Vs[q];
            for (int k = 0; k < NI_MAX; ++k) a += (r0.Vi[q][k] + wdc * dr.Vi[q][k] + c.K[q][k]) * i0[k];
            c.p_dc[q] = a;
        }
        for (int q = 0; q < NOUT; ++q) {
            double a = r0.Ys[q] + wdc * dr.Ys[q];
            for (int k = 0; k < NI_MAX; ++k) a += (r0.Yi[q][k] + wdc * dr.Yi[q][k]) * i0[k];
            c.y_dc[q] = a;
        }
        for (int k = 0; k < NI_MAX; ++k) c.i_dc[k] = k < c.ni ? i0[k] : 0.0;
    }
    // The DC operating point at DC weight wdc: the stage's solver on v = Vs - (-Vi) i, from guess, then i0
    Solution dc_solve(double wdc, const double (&guess)[NI_MAX], const double (&i0)[NI_MAX]) const noexcept {
        double p[NV], K[NV][NI_MAX];
        for (int q = 0; q < NV; ++q) {
            p[q] = r0.Vs[q] + wdc * dr.Vs[q];
            for (int k = 0; k < NI_MAX; ++k) K[q][k] = -(r0.Vi[q][k] + wdc * dr.Vi[q][k]);
        }
        if (base.grid) {
            const double starts[3][3] = {{guess[0], guess[1], guess[2]}, {i0[0], i0[1], i0[2]}, {guess[0], guess[1], guess[2]}};
            return solve_grid_restarts(p, K, starts, base.par, base.g, base.newton);
        }
        return solve(p, K, guess[0], guess[1], base.par, base.newton);
    }
    // the table: log Rk for ma (clamped), and optionally d(log Rk)/d(log Ik) there and the interpolated currents
    double log_rk(double ma, double* dtdy = nullptr, double (*i)[NI_MAX] = nullptr) const noexcept {
        const double yy = std::log(clamp(ma));
        int lo = 0, hi = NT - 1;
        while (hi - lo > 1) {
            const int mid = (lo + hi) / 2;
            if (y[mid] >= yy) lo = mid;
            else hi = mid;
        }
        const int j = lo;
        const double h = y[j + 1] - y[j], u = (yy - y[j]) / h, u2 = u * u, u3 = u2 * u;
        const double tt = (2 * u3 - 3 * u2 + 1) * t[j] + (u3 - 2 * u2 + u) * h * d[j] + (-2 * u3 + 3 * u2) * t[j + 1] +
                          (u3 - u2) * h * d[j + 1];
        if (dtdy)
            *dtdy = ((6 * u2 - 6 * u) * t[j] + (3 * u2 - 4 * u + 1) * h * d[j] + (-6 * u2 + 6 * u) * t[j + 1] +
                     (3 * u2 - 2 * u) * h * d[j + 1]) / h;
        if (i)
            for (int k = 0; k < NI_MAX; ++k) (*i)[k] = cur[j][k] + u * (cur[j + 1][k] - cur[j][k]);
        return tt;
    }
    /* c (a copy of base, or any pack of this line) at ma (clamped), with the Rk that draws it: Newton on log Rk from the
     * table's value, each step one DC solve (start: the table's currents, then i0). Stops at EXACT_TOL or after
     * EXACT_STEPS. On return c's matrices and rest constants are at that Rk, referenced to its operating point (c.op:
     * v, i, ik and the nodes out, g, k, a, gl; the others are base's), and i0 is that point's currents. converged: every
     * DC solve converged; if one does not, c is at the last Rk whose solve did (the table's if none), referenced to i0
     * as given (the deviation form is exact for any i0). False: a non-finite result; c may be partly written.
     * Audio-thread safe (bounded, no allocation). */
    bool exact(Pack& c, double ma, double (&i0)[NI_MAX], bool& converged, int& steps) const noexcept {
        const double b = clamp(ma), ly = std::log(b);
        double dtdy = 0.0, g[NI_MAX];
        double tt = log_rk(b, &dtdy, &g), t_ok = tt;
        Solution best{};
        bool have = false;
        converged = true;
        steps = 0;
        for (int k = 0; k < EXACT_STEPS; ++k) {
            double wac, wdc;
            weights(std::exp(tt), wac, wdc);
            const Solution s = dc_solve(wdc, g, i0);
            ++steps;
            bool fin = s.ok;
            for (int q = 0; q < NI_MAX; ++q) fin = fin && std::isfinite(s.i[q]);
            if (!fin) { converged = false; break; }
            best = s; have = true; t_ok = tt;
            for (int q = 0; q < NI_MAX; ++q) g[q] = s.i[q];
            const double ik = base.grid ? s.i[0] + s.i[1] + s.i[2] : s.i[0] + s.i[1];
            const double f = std::log(ik * 1e3) - ly;
            if (!(std::fabs(f) > EXACT_TOL)) break;
            tt -= f * dtdy;
        }
        const double rk = std::exp(t_ok);
        double wac, wdc;
        weights(rk, wac, wdc);
        matrices(c, rk, wac);
        if (have) for (int q = 0; q < NI_MAX; ++q) i0[q] = q < base.ni ? best.i[q] : 0.0;
        rest(c, wdc, i0);
        if (have) {
            for (int q = 0; q < NV; ++q) c.op.v[q] = best.v[q];
            for (int q = 0; q < NI_MAX; ++q) c.op.i[q] = c.i_dc[q];
            c.op.ik = base.grid ? i0[0] + i0[1] + i0[2] : i0[0] + i0[1];
            c.op.nodes[OUT] = c.y_dc[0]; c.op.nodes[GRID] = c.y_dc[1]; c.op.nodes[CATH] = c.y_dc[2];
            c.op.nodes[ANODE] = c.y_dc[3]; c.op.nodes[GL] = c.y_dc[4];
        }
        c.bias_ma = b;
        c.supported = supported(c.mode, b, c.g.hardness);
        bool fin = std::isfinite(rk);
        for (double v : c.x_dc) fin = fin && std::isfinite(v);
        for (double v : c.p_dc) fin = fin && std::isfinite(v);
        for (double v : c.y_dc) fin = fin && std::isfinite(v);
        return fin;
    }
};

/* Build line l for mode s.mode over [lo_ma, hi_ma] at internal rate fs (s.experimental as build_pack: both ends must
 * build). Non-RT, no allocation: two build_pack, four build_full and NT source-stepped DC solves (about 7 ms per
 * mode on an M1, informal). bias_out_of_range also if the table is not strictly monotonic. */
inline Status build_line(BiasLine& l, double fs, const Settings& s, double lo_ma, double hi_ma) {
    l.ok = false;
    if (!(lo_ma > 0.0) || !(hi_ma > lo_ma) || !std::isfinite(hi_ma)) return Status::bad_argument;
    Settings a = s;
    a.bias_ma = lo_ma;
    Pack plo, phi;
    Status st = build_pack(plo, fs, a);
    if (st != Status::ok) return st;
    a.bias_ma = hi_ma;
    if ((st = build_pack(phi, fs, a)) != Status::ok) return st;
    detail::Full alo, ahi, dlo, dhi;
    if (!detail::build_full(alo, fs, plo.rk, false, s.mode, plo.grid, s.values) ||
        !detail::build_full(ahi, fs, phi.rk, false, s.mode, plo.grid, s.values) ||
        !detail::build_full(dlo, 1.0, plo.rk, true, s.mode, plo.grid, s.values) ||
        !detail::build_full(dhi, 1.0, phi.rk, true, s.mode, plo.grid, s.values))
        return Status::singular_matrix;
    l.lo_ma = lo_ma; l.hi_ma = hi_ma;
    l.g0 = 1.0 / plo.rk;
    l.d0 = alo.Si.a[CATH][CATH];
    l.d0dc = dlo.Si.a[CATH][CATH];
    const double dg1 = 1.0 / phi.rk - l.g0;
    l.beta1 = dg1 / (1.0 + dg1 * l.d0);
    l.beta1dc = dg1 / (1.0 + dg1 * l.d0dc);
    if (!std::isfinite(l.beta1) || l.beta1 == 0.0 || !std::isfinite(l.beta1dc) || l.beta1dc == 0.0)
        return Status::singular_matrix;
    detail::m_copy(l.m0.A, plo.A); detail::m_copy(l.m0.B, plo.B); detail::m_copy(l.m0.C, plo.C);
    detail::m_copy(l.m0.D, plo.D); detail::m_copy(l.m0.E, plo.E); detail::m_copy(l.m0.F, plo.F);
    detail::m_copy(l.m0.G, plo.G); detail::m_copy(l.m0.H, plo.H); detail::m_copy(l.m0.K, plo.K);
    detail::m_sub(l.dm.A, phi.A, plo.A); detail::m_sub(l.dm.B, phi.B, plo.B); detail::m_sub(l.dm.C, phi.C, plo.C);
    detail::m_sub(l.dm.D, phi.D, plo.D); detail::m_sub(l.dm.E, phi.E, plo.E); detail::m_sub(l.dm.F, phi.F, plo.F);
    detail::m_sub(l.dm.G, phi.G, plo.G); detail::m_sub(l.dm.H, phi.H, plo.H); detail::m_sub(l.dm.K, phi.K, plo.K);
    detail::RestMaps rhi;
    const int ns = detail::n_supplies(s.mode);
    detail::rest_maps(l.r0, dlo, alo.gx, alo.Nx, plo.supplies, ns);
    detail::rest_maps(rhi, dhi, ahi.gx, ahi.Nx, plo.supplies, ns);
    detail::v_sub(l.dr.Xs, rhi.Xs, l.r0.Xs); detail::m_sub(l.dr.Xi, rhi.Xi, l.r0.Xi);
    detail::v_sub(l.dr.Vs, rhi.Vs, l.r0.Vs); detail::m_sub(l.dr.Vi, rhi.Vi, l.r0.Vi);
    detail::v_sub(l.dr.Ys, rhi.Ys, l.r0.Ys); detail::m_sub(l.dr.Yi, rhi.Yi, l.r0.Yi);
    l.base = plo;
    const double t0 = std::log(phi.rk), t1 = std::log(plo.rk);
    constexpr int NT = BiasLine::NT;
    for (int j = 0; j < NT; ++j) {
        l.t[j] = j == 0 ? t0 : j == NT - 1 ? t1 : t0 + (t1 - t0) * double(j) / double(NT - 1);
        OperatingPoint op{};
        st = detail::operating_point(std::exp(l.t[j]), s.par, s.newton, s.mode, plo.grid, plo.g, s.values, op);
        if (st != Status::ok) return st;
        l.y[j] = std::log(op.ik * 1e3);
        for (int k = 0; k < NI_MAX; ++k) l.cur[j][k] = k < plo.ni ? op.i[k] : 0.0;
        if (!std::isfinite(l.y[j]) || (j > 0 && !(l.y[j] < l.y[j - 1]))) return Status::bias_out_of_range;
    }
    // dt/dy at the nodes: three-point differences on the non-uniform y grid (one-sided at the ends)
    for (int j = 0; j < NT; ++j) {
        const int a0 = j == 0 ? 0 : j == NT - 1 ? NT - 3 : j - 1;
        const double h1 = l.y[a0 + 1] - l.y[a0], h2 = l.y[a0 + 2] - l.y[a0 + 1];
        const double s1 = (l.t[a0 + 1] - l.t[a0]) / h1, s2 = (l.t[a0 + 2] - l.t[a0 + 1]) / h2;
        if (j == 0) l.d[j] = ((2 * h1 + h2) * s1 - h1 * s2) / (h1 + h2);
        else if (j == NT - 1) l.d[j] = ((2 * h2 + h1) * s2 - h2 * s1) / (h1 + h2);
        else l.d[j] = (h2 * s1 + h1 * s2) / (h1 + h2);
    }
    l.ok = true;
    return Status::ok;
}

// ---- the stage ---------------------------------------------------------------------------------------------------

struct Stats {                            // audio-thread side; since reset()
    static constexpr int HIST = 3 * 50 + 1;   // bins 0 .. 3 x MAX_IT (the restart bound at the default cap)
    std::uint64_t samples = 0;            // internal-rate samples solved
    std::uint64_t iterations = 0;         // Newton iterations, summed (over restarts too)
    std::uint64_t nonconverged = 0;       // samples that kept a best iterate (#7; with grid current: after 3 starts)
    std::uint64_t restarted = 0;          // samples whose first start failed (grid path, #112)
    std::uint64_t out_of_law = 0;         // internal samples outside the law in force (stage.py's runtime guard)
    std::uint64_t nonfinite_inputs = 0;   // input samples that were NaN / inf after the drive gain, replaced by 0
    std::uint64_t recoveries = 0;         // samples whose state went non-finite: the state went back to the op point
    std::uint64_t nonfinite_outputs = 0;  // output samples whose gained value overflowed the destination type: 0 written
    std::uint64_t line_updates = 0;       // bias-line packs adopted (#115: one per base sample while the bias moves)
    std::uint64_t line_unconverged = 0;   // ... of which an end-of-ramp DC solve did not converge (i0 kept)
    std::uint64_t line_failures = 0;      // ... not adopted (singular I - A or a non-finite result; the pack stayed)
    std::uint32_t nonconv_block = 0, out_of_law_block = 0;   // ... in the last process() / process_internal() call
    int max_iterations = 0;
    std::uint64_t histogram[HIST] = {};
};

/* Internal-rate signals, as Stage.process(record=...)["_internal"]: n * oversampling() values each, any may be null
 * (vgk = Vg1k, vg2k, vak, ig1 (0 without grid current), ipk = Ip + Ig2, it = Newton iterations). */
struct Record { double* vgk = nullptr; double* vg2k = nullptr; double* vak = nullptr; double* ig1 = nullptr;
                double* ipk = nullptr; std::uint16_t* it = nullptr; };

template <int MaxOs> class StageT {
public:
    using Resampler = dkcore::ResamplerT<double, dkcore::K140_FIRST_MAX_TAPS, dkcore::K140_LATER_TAPS, MaxOs>;
    static_assert(dkcore::K140_FIRST_MAX_TAPS >= dkcore::FIR_MAX_TAPS && dkcore::K140_LATER_TAPS >= dkcore::FIR_LATER_TAPS,
                  "the stage's resampler holds both designs");

    // ---- non-RT, never concurrently with process() ------------------------------------------------------------
    Status prepare(double fs_base, Quality q = STANDARD, const Settings& s = Settings{}) {
        prepared_ = false;
        if (!(fs_base > 0.0) || !std::isfinite(fs_base) || q.prewarp_hz > 0.0) return Status::bad_argument;
        Pack p;
        const Status st = build_pack(p, fs_base * q.os, s);
        if (st != Status::ok) { pack_ = p; return st; }
        return install(fs_base, q.os, p, s.lpf, s.fir);
    }
    // Run a given pack (e.g. the Python stage's, for the tests' "pinned" vectors). p.fs must be fs_base * os.
    Status prepare_pack(double fs_base, int os, const Pack& p, Lpf lpf = Lpf::off, const FirDesign& fir = DEFAULT_FIR) {
        prepared_ = false;
        if (!(fs_base > 0.0) || p.fs != fs_base * os) return Status::bad_argument;
        return install(fs_base, os, p, lpf, fir);
    }

    // ---- any thread ---------------------------------------------------------------------------------------------
    // linear gains before / after the stage; a non-finite value is ignored (the last finite one stays)
    void set_drive(double g) { if (std::isfinite(g)) drive_.store(g, std::memory_order_relaxed); }
    void set_output(double g) { if (std::isfinite(g)) output_.store(g, std::memory_order_relaxed); }
    void set_lpf(Lpf p) { lpf_req_.store(std::uint8_t(p), std::memory_order_relaxed); }

    // ---- audio thread -------------------------------------------------------------------------------------------
    // state to the operating point, the filter states to zero and its mix on the requested position; clears stats
    void reset() noexcept {
        for (double& v : x_) v = 0.0;
        for (int k = 0; k < NI_MAX; ++k) i_[k] = q_[k] = pack_.i_dc[k];
        stats_ = Stats{};
        rs_.reset();
        const Lpf want = Lpf(lpf_req_.load(std::memory_order_relaxed));
        if (want != lpf_.position()) { lpf_.set_position(want); }
        lpf_.reset();
        lpf_seen_ = want;
    }
    // Base rate in, base rate out, oversampled. Stateful; the output lags the input by latency_samples(). in may equal
    // out. rec (optional): internal-rate signals.
    void process(const double* in, double* out, std::size_t n, const Record* rec = nullptr) noexcept {
        process_t(in, out, n, rec, nullptr, -1);
    }
    void process(const float* in, float* out, std::size_t n) noexcept { process_t(in, out, n, nullptr, nullptr, -1); }

    /* The stage alone at the internal rate (lpf.Stage.process_internal: the stage, then the post-filter): no
     * resampler, no drive / output gain. For tests and analysis. */
    void process_internal(const double* vin, double* out, std::size_t n, const Record* rec = nullptr) noexcept {
        dkcore::ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = 0.0; return; }
        for (std::size_t i = 0; i < n; ++i) {
            double x = vin[i];
            if (!std::isfinite(x)) { x = 0.0; ++stats_.nonfinite_inputs; }
            const double y = lpf_.tick(pack_.grid ? tick<true>(x, rec, i) : tick<false>(x, rec, i));
            if (!(std::isfinite(y) && state_finite())) { recover(); out[i] = 0.0; continue; }
            out[i] = y;
        }
    }

    /* Audio thread: run p from the next sample, keeping the absolute capacitor histories x_dc + x and the currents (a
     * pot moved between two samples). p must be of this stage's mode and internal rate (else false, nothing changes);
     * any bias line is detached. For tests and analysis: the continuous reference of cpp/test_live_6as6.cpp. */
    bool adopt_pack(const Pack& p) noexcept {
        if (!prepared_ || p.mode != pack_.mode || p.fs != pack_.fs || p.ni != pack_.ni) return false;
        for (int i = 0; i < NX; ++i) x_[i] = x_[i] + (pack_.x_dc[i] - p.x_dc[i]);
        pack_ = p;
        line_ = nullptr;
        return true;
    }
    bool prepared() const { return prepared_; }
    const Pack& pack() const { return pack_; }            // also the bias meter: rk, op.nodes[CATH] = Vk, op.ik
    int oversampling() const { return rs_.os(); }
    int latency_samples() const { return rs_.latency_samples(); }
    int up_delay_internal() const { return rs_.up_delay_internal(); }
    const Resampler& resampler() const { return rs_; }
    const Stats& stats() const { return stats_; }           // audio thread, or while stopped
    // the bias line pack() moves on (#115, LiveStageT), or null, and pack()'s bias on it (mA)
    const BiasLine* line() const { return line_; }
    double line_bias() const { return b_line_; }
    bool line_exact() const { return at_exact_; }         // pack() is the exact pack at line_bias() (not mid-move)

    // ---- the bias line (#115): LiveStageT's interface -------------------------------------------------------------
    // Non-RT, after prepare / prepare_pack: the line the prepared pack lies on. False (nothing changes) if l is not
    // built for this pack's mode and internal rate, or pack().bias_ma is outside its range.
    bool attach_line(const BiasLine* l) {
        if (!l || !l->ok || l->base.mode != pack_.mode || l->base.fs != pack_.fs || l->clamp(pack_.bias_ma) != pack_.bias_ma)
            return false;
        line_ = l;
        b_line_ = pack_.bias_ma;
        planned_ = false;
        at_exact_ = true;
        return true;
    }
    /* Audio thread: run line l's pack at ma (clamped to l; BiasLine::exact: the Rk that draws it, recentred), then
     * reset() (state to that operating point, resampler and post-filter states cleared, stats cleared). For a mode
     * change, on a stage prepared at the same internal rate. False, and nothing changes, if l does not fit this stage
     * or the pack is unusable. Bounded: a pack copy and exact(). */
    bool install_line(const BiasLine& l, double ma) noexcept {
        if (!prepared_ || !l.ok || l.base.fs != pack_.fs) return false;
        const double b = l.clamp(ma);
        work_ = l.base;
        double i0[NI_MAX];
        l.log_rk(b, nullptr, &i0);
        bool conv = false;
        int steps = 0;
        if (!l.exact(work_, b, i0, conv, steps)) return false;
        pack_ = work_;
        line_ = &l;
        b_line_ = b;
        planned_ = false;
        at_exact_ = true;
        reset();
        return true;
    }
    /* Audio thread: plan a move along line() from the current bias to `to` (mA, clamped to the line): its end point
     * is made exact now (BiasLine::exact, into a scratch pack), and the table's Rk is corrected linearly along the move
     * so that it starts at pack()'s Rk and lands on the exact one. The move itself is driven by process_line()'s bias
     * values; the pack becomes the exact one when the bias reaches `to`. A later plan replaces this one. False (no plan:
     * the bias stays where it is) if the end point is unusable; counted in Stats::line_failures. */
    bool line_plan(double to) noexcept {
        if (!line_) return false;
        const double b = line_->clamp(to);
        planned_ = false;
        if (b == b_line_ && at_exact_) return true;      // already there
        final_ = line_->base;
        double i0[NI_MAX];
        line_->log_rk(b, nullptr, &i0);
        bool conv = false;
        int steps = 0;
        if (!line_->exact(final_, b, i0, conv, steps)) { ++stats_.line_failures; return false; }
        if (!conv) ++stats_.line_unconverged;
        if (b == b_line_) {                              // stopped between two points (a re-plan back): exact now
            for (int i = 0; i < NX; ++i) x_[i] = x_[i] + (pack_.x_dc[i] - final_.x_dc[i]);
            pack_ = final_;
            at_exact_ = true;
            ++stats_.line_updates;
            return true;
        }
        for (int k = 0; k < NI_MAX; ++k) ref_i_[k] = pack_.i_dc[k];
        b_from_ = b_line_;
        b_to_ = b;
        off_from_ = std::log(pack_.rk) - line_->log_rk(b_from_);
        off_to_ = std::log(final_.rk) - line_->log_rk(b_to_);
        planned_ = true;
        return true;
    }
    /* Audio thread: process() with the bias moving along line() (after line_plan): bias[i] (mA) is the bias at the
     * end of base sample i, b_start the bias before the first; the internal samples between step linearly (one pack per
     * internal sample: the 6AS6 stage has no cathode bypass capacitor, so the bias is in the signal path). bias may be
     * null (no move). No line attached or no plan: as process(). */
    void process_line(const double* in, double* out, std::size_t n, const double* bias, double b_start) noexcept {
        b_start_ = b_start;
        process_t(in, out, n, nullptr, line_ && planned_ ? bias : nullptr, 0);
    }
    const double* state_x() const { return x_; }            // deviation from the operating point's x_dc (#70)
    const double* state_i() const { return i_; }
    Lpf lpf_position() const { return lpf_.position(); }
    const PostFilter& post_filter() const { return lpf_; }

private:
    friend struct AdaptiveOsProbe;  // #148 research access to prepared packs and histories, separate from #115.

    Status install(double fs_base, int os, const Pack& p, Lpf lpf, const FirDesign& fir) {
        line_ = nullptr;
        if (!rs_.prepare(os, fs_base, fir)) return Status::bad_argument;
        if (!lpf_.prepare(p.fs, lpf)) return Status::bad_argument;
        pack_ = p;
        fs_base_ = fs_base;
        lpf_req_.store(std::uint8_t(lpf), std::memory_order_relaxed);
        prepared_ = true;
        reset();
        return Status::ok;
    }
    void begin_block() noexcept {
        const Lpf want = Lpf(lpf_req_.load(std::memory_order_relaxed));
        if (want != lpf_seen_) { lpf_seen_ = want; lpf_.set_position(want); }
        stats_.nonconv_block = 0;
        stats_.out_of_law_block = 0;
    }

    // one internal-rate sample (the loop body of dk6as6 Stage.process_internal); returns the "out" node voltage.
    // Deviation form (#70): x_ is the deviation from x_dc, the currents enter as i - i_dc, the supplies only through
    // p_dc and y_dc.
    template <bool GRID> double tick(double s, const Record* rec, std::size_t idx) noexcept {
        const Pack& c = pack_;
        double p[NV];
        for (int r = 0; r < NV; ++r) {
            double a = c.G[r][0] * x_[0];
            for (int j = 1; j < NX; ++j) a += c.G[r][j] * x_[j];
            p[r] = a + c.H[r][0] * s + c.p_dc[r];
        }
        // warm start: linear extrapolation of the last two current solutions
        double e[NI_MAX];
        for (int k = 0; k < NI_MAX; ++k) e[k] = 2.0 * i_[k] - q_[k];
        for (int k = 0; k < NI_MAX; ++k) q_[k] = i_[k];
        Solution r;
        if (GRID) {
            const double starts[3][3] = {{e[0], e[1], e[2]}, {q_[0], q_[1], q_[2]}, {c.i_dc[0], c.i_dc[1], c.i_dc[2]}};
            r = solve_grid_restarts(p, c.K, starts, c.par, c.g, c.newton);
            if (r.attempts > 1) ++stats_.restarted;
        } else {
            r = solve(p, c.K, e[0], e[1], c.par, c.newton);
        }
        for (int k = 0; k < NI_MAX; ++k) i_[k] = r.i[k];     // kept even when !r.ok: the best finite iterate (#7)
        ++stats_.samples;
        stats_.iterations += std::uint64_t(r.it);
        ++stats_.histogram[r.it < Stats::HIST ? r.it : Stats::HIST - 1];
        if (r.it > stats_.max_iterations) stats_.max_iterations = r.it;
        if (!r.ok) { ++stats_.nonconverged; ++stats_.nonconv_block; }
        // the runtime guard: without grid current g1 positive or the plate below the cathode; with it the plate below
        // the cathode or Ip + Ig2 < 0 (Ik < Ig1)
        if ((GRID ? r.i[0] + r.i[1] < 0.0 : r.v[0] > 0.0) || r.v[3] < 0.0) { ++stats_.out_of_law; ++stats_.out_of_law_block; }
        if (rec) {
            if (rec->vgk) rec->vgk[idx] = r.v[0];
            if (rec->vg2k) rec->vg2k[idx] = r.v[1];
            if (rec->vak) rec->vak[idx] = r.v[3];
            if (rec->ig1) rec->ig1[idx] = GRID ? r.i[2] : 0.0;
            if (rec->ipk) rec->ipk[idx] = r.i[0] + r.i[1];
            if (rec->it) rec->it[idx] = std::uint16_t(r.it);
        }
        double inl[NI_MAX];
        constexpr int NI = GRID ? 3 : 2;
        for (int k = 0; k < NI; ++k) inl[k] = i_[k] - c.i_dc[k];
        double dx = c.D[0][0] * x_[0];
        for (int j = 1; j < NX; ++j) dx += c.D[0][j] * x_[j];
        double fi = c.F[0][0] * inl[0];
        for (int k = 1; k < NI; ++k) fi += c.F[0][k] * inl[k];
        const double y = dx + c.E[0][0] * s + fi + c.y_dc[0];
        double xn[NX];
        for (int i = 0; i < NX; ++i) {
            double a = c.A[i][0] * x_[0];
            for (int j = 1; j < NX; ++j) a += c.A[i][j] * x_[j];
            double ci = c.C[i][0] * inl[0];
            for (int k = 1; k < NI; ++k) ci += c.C[i][k] * inl[k];
            xn[i] = a + c.B[i][0] * s + ci;
        }
        for (int i = 0; i < NX; ++i) x_[i] = xn[i];
        return y;
    }

    // One internal sample's bias on the line (#115, after line_plan): the matrices and the rest constants at bias b
    // (clamped), referenced to the plan's currents, the table's Rk corrected along the move; x re-expressed so that the
    // absolute history x_dc + x is kept, as #73 does. At the plan's end the exact pack (final_). Nothing when there.
    void line_follow(double b) noexcept {
        const double bl = line_->clamp(b);
        if (bl == b_line_) return;
        double xo[NX];
        for (int i = 0; i < NX; ++i) xo[i] = pack_.x_dc[i];
        if (bl == b_to_) {
            pack_ = final_;
            planned_ = false;
            at_exact_ = true;
        } else {
            const double lam = (bl - b_from_) / (b_to_ - b_from_);
            const double rk = std::exp(line_->log_rk(bl) + (1.0 - lam) * off_from_ + lam * off_to_);
            double wac, wdc;
            line_->weights(rk, wac, wdc);
            line_->matrices(pack_, rk, wac);
            line_->rest(pack_, wdc, ref_i_);
            pack_.bias_ma = bl;
            pack_.supported = supported(pack_.mode, bl, pack_.g.hardness);
            at_exact_ = false;
        }
        for (int i = 0; i < NX; ++i) x_[i] = x_[i] + (xo[i] - pack_.x_dc[i]);
        b_line_ = bl;
        ++stats_.line_updates;
    }

    template <bool GRID, class S> void run(const S* in, S* out, std::size_t n, double drive, double og, const Record* rec,
                                           const double* bias, long) noexcept {
        double u[MaxOs], y[MaxOs];
        const int os = rs_.os();
        for (std::size_t i = 0; i < n; ++i) {
            double b0 = 0.0, db = 0.0;
            if (bias) { b0 = i ? bias[i - 1] : b_start_; db = bias[i] - b0; }
            double x = drive * double(in[i]);            // checked after the gain: a finite sample times a gain can overflow
            if (!std::isfinite(x)) { x = 0.0; ++stats_.nonfinite_inputs; }
            rs_.up(x, u);
            for (int j = 0; j < os; ++j) {
                if (bias && planned_) line_follow(j == os - 1 ? bias[i] : b0 + db * double(j + 1) / double(os));
                y[j] = lpf_.tick(tick<GRID>(u[j], rec, i * std::size_t(os) + std::size_t(j)));
            }
            const double yd = rs_.down(y);
            // an input finite but far outside any signal (e.g. the largest double) can still drive the solve to inf /
            // NaN; it must not stay in the state: back to the operating point, filters cleared, this sample silent
            if (!(std::isfinite(yd) && state_finite())) { recover(); out[i] = S(0); continue; }
            // a finite output gain can still overflow the destination: range-checked in double before the conversion
            // (an out-of-range double -> float conversion is undefined), written as 0 and counted; the state is fine
            const double o = og * yd;
            if (!(std::fabs(o) <= double(std::numeric_limits<S>::max()))) { out[i] = S(0); ++stats_.nonfinite_outputs; continue; }
            out[i] = S(o);
        }
    }
    // the persistent per-sample state (capacitor histories, currents) is finite: v * 0 is 0 for finite v, NaN else
    bool state_finite() const noexcept {
        double z = 0.0;
        for (double v : x_) z += v * 0.0;
        for (int k = 0; k < NI_MAX; ++k) z += i_[k] * 0.0 + q_[k] * 0.0;
        return z == 0.0;
    }
    // realtime-safe: what reset() does to the state, without clearing the stats or the post-filter position
    void recover() noexcept {
        for (double& v : x_) v = 0.0;
        for (int k = 0; k < NI_MAX; ++k) i_[k] = q_[k] = pack_.i_dc[k];
        rs_.reset();
        lpf_.clear_states();
        ++stats_.recoveries;
    }
    template <class S> void process_t(const S* in, S* out, std::size_t n, const Record* rec, const double* bias,
                                      long last) noexcept {
        dkcore::ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = S(0); return; }
        const double drive = drive_.load(std::memory_order_relaxed), og = output_.load(std::memory_order_relaxed);
        if (pack_.grid) run<true>(in, out, n, drive, og, rec, bias, last);
        else run<false>(in, out, n, drive, og, rec, bias, last);
    }

    // audio-thread state
    double x_[NX] = {}, i_[NI_MAX] = {}, q_[NI_MAX] = {};
    Pack pack_{};
    Resampler rs_;
    PostFilter lpf_;
    Stats stats_;
    Lpf lpf_seen_ = Lpf::off;
    double fs_base_ = 0.0;
    bool prepared_ = false;
    // the bias line (#115): the line, pack_'s bias on it, whether pack_ is referenced to its own operating point, and
    // the scratch pack the next one is built in (so a failed update leaves pack_ as it was)
    // the line, pack_'s bias on it, the plan of a move (its end pack, ends, Rk corrections and reference currents), and
    // the scratch pack an install is built in (so a failed one leaves pack_ as it was)
    const BiasLine* line_ = nullptr;
    double b_line_ = 0.0, b_start_ = 0.0;
    bool planned_ = false, at_exact_ = true;       // a move is planned; pack_ is the exact pack at b_line_
    double b_from_ = 0.0, b_to_ = 0.0, off_from_ = 0.0, off_to_ = 0.0, ref_i_[NI_MAX] = {};
    Pack work_{}, final_{};
    // shared
    std::atomic<double> drive_{1.0}, output_{1.0};
    std::atomic<std::uint8_t> lpf_req_{0};
};
using Stage = StageT<dkcore::MAX_OS>;

// ---- live bias and mode (#115): two voices, one bias ramp, a crossfaded mode switch --------------------------------

/* The defaults (cpp/RESULTS-smoothing-6as6.md): the bias ramp is #73's (and the plugin's Drive / Output ramps') 20 ms;
 * a mode switch warms the new voice up for LIVE_WARM_S on the live input, at zero weight, then crossfades linearly
 * over LIVE_FADE_S. */
inline constexpr double LIVE_RAMP_S = 0.020, LIVE_WARM_S = 0.010, LIVE_FADE_S = 0.020;

// The three modes' bias lines (index = Mode), built for one internal rate; shared, read-only, by any number of stages.
struct LineRange { double lo_ma, hi_ma; bool experimental; };
struct Lines {
    bool ok = false;
    BiasLine line[3];
    double lo_ma = 0.0, hi_ma = 0.0;     // the union of the three ranges: where the bias target is clamped
};
// Non-RT: line[m] over r[m] with base's other settings (base.mode, bias_ma and experimental are replaced).
inline Status build_lines(Lines& out, double fs, const Settings& base, const LineRange (&r)[3]) {
    out.ok = false;
    out.lo_ma = r[0].lo_ma;
    out.hi_ma = r[0].hi_ma;
    for (int m = 0; m < 3; ++m) {
        Settings s = base;
        s.mode = Mode(m);
        s.experimental = r[m].experimental;
        const Status st = build_line(out.line[m], fs, s, r[m].lo_ma, r[m].hi_ma);
        if (st != Status::ok) return st;
        out.lo_ma = std::fmin(out.lo_ma, r[m].lo_ma);
        out.hi_ma = std::fmax(out.hi_ma, r[m].hi_ma);
    }
    out.ok = true;
    return Status::ok;
}

/* One channel of the 6AS6 stage with live Bias and Mode (#115), on the audio thread without the worker:
 *   Bias  set_bias(mA) is read once per block; the bias ramps linearly to it over LIVE_RAMP_S of base-rate time, one
 *         pack per base sample along the mode's BiasLine (no build_pack, no matrix inversion, no lock, no allocation),
 *         and lands on the exact pack (BiasLine::exact). A new target re-plans the ramp from where it is. The ramp is
 *         in the union of the modes' ranges; each voice clamps it to its own mode's (T: to 0.5 mA in the plugin).
 *   Mode  set_mode() is read once per block. A switch installs the idle voice at the new mode and the current bias
 *         (StageT::install_line: a pack copy and exact(), bounded), runs it for LIVE_WARM_S on the live input at zero
 *         weight (its resampler and valve states fill from rest), then crossfades the two voices linearly over
 *         LIVE_FADE_S; afterwards the old voice stops. A mode change during a switch waits for it to finish. Twice the
 *         CPU while switching (30 ms).
 *   Filter set_lpf() goes to both voices (StageT's 15 ms crossfade, #103).
 * Every step happens at a base sample of the stream, never at a block boundary of the host's choosing, so the output
 * is the same for any block size when the targets change at a sample common to the block sizes. */
template <int MaxOs> class LiveStageT {
public:
    using Voice = StageT<MaxOs>;
    static constexpr int SUB = 64;       // processing sub-block (scratch buffers live in the object)

    // ---- non-RT, never concurrently with process() ------------------------------------------------------------
    /* Run `start` (an exact pack: build_pack at the mode and bias, at lines' internal rate) on lines, which must stay
     * alive and unchanged while this stage runs. The bias target starts at start.bias_ma, the mode target at
     * start.mode. Times in seconds (non-finite or negative: 0; the ramp and fade at least one sample). */
    Status prepare(double fs_base, int os, const Lines& lines, const Pack& start, Lpf lpf = Lpf::off,
                   const FirDesign& fir = DEFAULT_FIR, double ramp_s = LIVE_RAMP_S, double warm_s = LIVE_WARM_S,
                   double fade_s = LIVE_FADE_S) {
        prepared_ = false;
        if (!lines.ok || int(start.mode) > 2) return Status::bad_argument;
        for (Voice& v : voice_) {
            const Status st = v.prepare_pack(fs_base, os, start, lpf, fir);
            if (st != Status::ok) return st;
            if (!v.attach_line(&lines.line[int(start.mode)])) return Status::bad_argument;
        }
        lines_ = &lines;
        auto len = [&](double s, int min) {
            const double n = s > 0.0 && std::isfinite(s) ? std::nearbyint(s * fs_base) : 0.0;
            return n < double(min) ? min : (n > 1e9 ? 1000000000 : int(n));
        };
        ramp_len_ = len(ramp_s, 1);
        warm_len_ = len(warm_s, 0);
        fade_len_ = len(fade_s, 1);
        bias_t_.store(start.bias_ma, std::memory_order_relaxed);
        mode_t_.store(std::uint8_t(start.mode), std::memory_order_relaxed);
        lpf_t_.store(std::uint8_t(lpf), std::memory_order_relaxed);
        cur_ = 0;
        phase_ = Phase::idle;
        b_ = b_tgt_ = seen_b_ = start.bias_ma;
        b_left_ = 0;
        want_ = seen_mode_ = start.mode;
        failed_ = false;
        retired_nonconv_ = switches_ = switch_failures_ = 0;
        prepared_ = true;
        return Status::ok;
    }

    // ---- any thread ---------------------------------------------------------------------------------------------
    void set_bias(double ma) { if (std::isfinite(ma)) bias_t_.store(ma, std::memory_order_relaxed); }   // mA
    void set_mode(Mode m) { if (int(m) <= 2) mode_t_.store(std::uint8_t(m), std::memory_order_relaxed); }
    void set_lpf(Lpf p) { lpf_t_.store(std::uint8_t(p), std::memory_order_relaxed); }
    void set_drive(double g) { for (Voice& v : voice_) v.set_drive(g); }
    void set_output(double g) { for (Voice& v : voice_) v.set_output(g); }

    // ---- audio thread -----------------------------------------------------------------------------------------
    // Settle: the bias jumps to its target, a switch in progress completes, the mode target is taken, and the voice
    // in charge goes to its operating point (StageT::reset: stats cleared). The pack is rebuilt only if mode or bias
    // changed (so a stage reset at its prepared setting runs the prepared, exact pack).
    void reset() noexcept {
        if (!prepared_) return;
        read_targets();
        b_ = b_tgt_;
        b_left_ = 0;
        if (phase_ != Phase::idle) { cur_ ^= 1; phase_ = Phase::idle; }
        Voice& v = voice_[cur_];
        const BiasLine& l = lines_->line[int(want_)];
        if (v.pack().mode != want_ || v.line_bias() != l.clamp(b_) || !v.line_exact()) {
            if (!v.install_line(l, b_)) { ++switch_failures_; v.reset(); }
        } else {
            v.reset();
        }
        retired_nonconv_ = 0;
        voice_[cur_ ^ 1].reset();
    }
    void process(const double* in, double* out, std::size_t n) noexcept {
        dkcore::ScopedFlushDenormals ftz;
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = 0.0; return; }
        read_targets();
        for (std::size_t off = 0; off < n;) {
            if (phase_ == Phase::idle && want_ != voice_[cur_].pack().mode && !failed_) start_switch();
            std::size_t len = n - off < std::size_t(SUB) ? n - off : std::size_t(SUB);
            if (phase_ != Phase::idle && std::size_t(left_) < len) len = std::size_t(left_);
            const double* bp = nullptr;
            const double b_start = b_;
            if (b_left_ > 0) {                           // the bias at the end of each base sample of this sub-block
                for (std::size_t i = 0; i < len; ++i) {
                    if (b_left_ > 0) {
                        --b_left_;
                        b_ = b_left_ == 0 ? b_tgt_ : b_ + b_step_;
                    }
                    bias_[i] = b_;
                }
                bp = bias_;
            }
            Voice& a = voice_[cur_];
            if (phase_ == Phase::idle) {
                a.process_line(in + off, out + off, len, bp, b_start);
            } else {
                Voice& b = voice_[cur_ ^ 1];
                a.process_line(in + off, ya_, len, bp, b_start);
                b.process_line(in + off, yb_, len, bp, b_start);
                if (phase_ == Phase::warm) {
                    for (std::size_t i = 0; i < len; ++i) out[off + i] = ya_[i];
                } else {
                    const int k0 = fade_len_ - left_;
                    for (std::size_t i = 0; i < len; ++i) {
                        const double w = double(k0 + int(i) + 1) / double(fade_len_);
                        out[off + i] = (1.0 - w) * ya_[i] + w * yb_[i];
                    }
                }
                left_ -= int(len);
                if (left_ == 0) {
                    if (phase_ == Phase::warm) { phase_ = Phase::fade; left_ = fade_len_; }
                    else { cur_ ^= 1; phase_ = Phase::idle; ++switches_; }
                }
            }
            off += len;
        }
    }

    // ---- state (audio thread, or while stopped) -----------------------------------------------------------------
    bool prepared() const { return prepared_; }
    const Voice& voice() const { return voice_[cur_]; }          // the voice in charge (the old one until a fade ends)
    const Voice& voice(int k) const { return voice_[k & 1]; }
    int current() const { return cur_; }
    Mode mode() const { return voice_[cur_].pack().mode; }
    double bias() const { return voice_[cur_].line_bias(); }     // mA, the voice in charge's (clamped to its mode)
    double bias_ramp() const { return b_; }                      // the ramp's position (unclamped), mA
    bool bias_moving() const { return b_left_ > 0; }
    bool switching() const { return phase_ != Phase::idle; }
    bool supported() const { return voice_[cur_].pack().supported; }
    int latency_samples() const { return voice_[0].latency_samples(); }
    int oversampling() const { return voice_[0].oversampling(); }
    int ramp_samples() const { return ramp_len_; }
    int warm_samples() const { return warm_len_; }
    int fade_samples() const { return fade_len_; }
    std::uint64_t switches() const { return switches_; }
    std::uint64_t switch_failures() const { return switch_failures_; }
    // non-converged samples since reset(), over both voices (a voice's count is kept when it is re-installed)
    std::uint64_t nonconverged() const {
        return retired_nonconv_ + voice_[0].stats().nonconverged + voice_[1].stats().nonconverged;
    }

private:
    enum class Phase : std::uint8_t { idle, warm, fade };
    void read_targets() noexcept {
        const double t = bias_t_.load(std::memory_order_relaxed);
        if (t != seen_b_) {
            seen_b_ = t;
            const double b = t < lines_->lo_ma ? lines_->lo_ma : (t > lines_->hi_ma ? lines_->hi_ma : t);
            if (!(b == b_tgt_ && (b_left_ > 0 || b == b_))) {        // not already there, or going there
                b_tgt_ = b;
                b_left_ = ramp_len_;
                b_step_ = (b - b_) / double(b_left_);
                voice_[cur_].line_plan(b);
                if (phase_ != Phase::idle) voice_[cur_ ^ 1].line_plan(b);
            }
        }
        const Mode m = Mode(mode_t_.load(std::memory_order_relaxed));
        if (m != seen_mode_) { seen_mode_ = m; failed_ = false; }
        want_ = m;
        const Lpf f = Lpf(lpf_t_.load(std::memory_order_relaxed));
        for (Voice& v : voice_) v.set_lpf(f);
    }
    void start_switch() noexcept {
        Voice& b = voice_[cur_ ^ 1];
        retired_nonconv_ += b.stats().nonconverged;
        if (!b.install_line(lines_->line[int(want_)], b_)) { failed_ = true; ++switch_failures_; return; }
        if (b_left_ > 0) b.line_plan(b_tgt_);
        phase_ = warm_len_ > 0 ? Phase::warm : Phase::fade;
        left_ = warm_len_ > 0 ? warm_len_ : fade_len_;
    }

    Voice voice_[2];
    const Lines* lines_ = nullptr;
    bool prepared_ = false;
    // audio-thread state
    int cur_ = 0;
    Phase phase_ = Phase::idle;
    int left_ = 0;                                  // samples left in the warm / fade phase
    int ramp_len_ = 1, warm_len_ = 0, fade_len_ = 1;
    double b_ = 0.0, b_tgt_ = 0.0, b_step_ = 0.0, seen_b_ = 0.0;   // the bias ramp (mA)
    int b_left_ = 0;
    Mode want_ = Mode::P1, seen_mode_ = Mode::P1;
    bool failed_ = false;                           // the last install of want_ failed: not retried until it changes
    std::uint64_t retired_nonconv_ = 0, switches_ = 0, switch_failures_ = 0;
    double bias_[SUB] = {}, ya_[SUB] = {}, yb_[SUB] = {};
    // shared
    std::atomic<double> bias_t_{BIAS_MA};
    std::atomic<std::uint8_t> mode_t_{0}, lpf_t_{0};
};
using LiveStage = LiveStageT<dkcore::MAX_OS>;

}  // namespace dk6as6
#endif
