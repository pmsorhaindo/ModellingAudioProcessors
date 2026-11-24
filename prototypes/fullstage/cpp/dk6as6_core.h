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
 *             the rheostat Rk that draws it (Brent on log10 Rk, stage.cathode_resistor) and Rk is fixed for the run
 *             (live bias changes are #115)
 *   modes     #111: P1 (default), T, P2 (circuit.MODES), fixed for the run (switching is #115)
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
inline Status build_pack_with_grid(Pack& out, double fs, const Settings& s, const GridParams* g) {
    if (!(fs > 0.0) || !std::isfinite(fs) || !(s.bias_ma > 0.0) || !std::isfinite(s.bias_ma) || !(s.grid_hardness >= 0.0)
        || !std::isfinite(s.grid_hardness) || s.newton.max_it < 1 || !(s.newton.tol_v >= 0.0) || !(s.newton.tol_f >= 0.0))
        return Status::bad_argument;
    Pack c{};
    c.fs = fs; c.bias_ma = s.bias_ma; c.mode = s.mode; c.par = s.par; c.newton = s.newton;
    c.grid = s.grid_hardness != 0.0;
    c.g = g ? *g : grid(s.grid_hardness, s.par);
    c.supported = supported(s.mode, s.bias_ma, s.grid_hardness);
    if (!c.supported && !s.experimental) return Status::unsupported_setting;
    Status st = cathode_resistor(s.bias_ma, s.par, s.newton, s.mode, c.grid, c.g, s.values, c.rk);
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
        process_t(in, out, n, rec);
    }
    void process(const float* in, float* out, std::size_t n) noexcept { process_t(in, out, n, nullptr); }

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

    bool prepared() const { return prepared_; }
    const Pack& pack() const { return pack_; }            // also the bias meter: rk, op.nodes[CATH] = Vk, op.ik
    int oversampling() const { return rs_.os(); }
    int latency_samples() const { return rs_.latency_samples(); }
    int up_delay_internal() const { return rs_.up_delay_internal(); }
    const Resampler& resampler() const { return rs_; }
    const Stats& stats() const { return stats_; }           // audio thread, or while stopped
    const double* state_x() const { return x_; }            // deviation from the operating point's x_dc (#70)
    const double* state_i() const { return i_; }
    Lpf lpf_position() const { return lpf_.position(); }
    const PostFilter& post_filter() const { return lpf_; }

private:
    friend struct AdaptiveOsProbe;  // #148 research access to prepared packs and histories, separate from #115.

    Status install(double fs_base, int os, const Pack& p, Lpf lpf, const FirDesign& fir) {
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

    template <bool GRID, class S> void run(const S* in, S* out, std::size_t n, double drive, double og, const Record* rec) noexcept {
        double u[MaxOs], y[MaxOs];
        const int os = rs_.os();
        for (std::size_t i = 0; i < n; ++i) {
            double x = drive * double(in[i]);            // checked after the gain: a finite sample times a gain can overflow
            if (!std::isfinite(x)) { x = 0.0; ++stats_.nonfinite_inputs; }
            rs_.up(x, u);
            for (int j = 0; j < os; ++j) y[j] = lpf_.tick(tick<GRID>(u[j], rec, i * std::size_t(os) + std::size_t(j)));
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
    template <class S> void process_t(const S* in, S* out, std::size_t n, const Record* rec) noexcept {
        dkcore::ScopedFlushDenormals ftz;
        begin_block();
        if (!prepared_) { for (std::size_t i = 0; i < n; ++i) out[i] = S(0); return; }
        const double drive = drive_.load(std::memory_order_relaxed), og = output_.load(std::memory_order_relaxed);
        if (pack_.grid) run<true>(in, out, n, drive, og, rec);
        else run<false>(in, out, n, drive, og, rec);
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
    // shared
    std::atomic<double> drive_{1.0}, output_{1.0};
    std::atomic<std::uint8_t> lpf_req_{0};
};
using Stage = StageT<dkcore::MAX_OS>;

}  // namespace dk6as6
#endif
