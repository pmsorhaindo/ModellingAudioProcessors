/* resampler_cost.cpp -- CPU cost of an FIR resampler candidate alone (#113, dk6as6/resampler_study.py "cost").
 *
 * The cascade is cpp/dk_core.h's FirHalfBand (the shipped C++ arithmetic: polyphase transposed direct form, mono
 * double), driven the way ResamplerT drives it: one base-rate sample up through every 2x stage, the internal-rate
 * block straight back down (no stage in between, so this is the resampler's own cost only). Lengths and Kaiser betas
 * are compile-time (-DFIRST, -DLATER, -DBETA0, -DBETA1), because the capacities are template constants as in the core.
 *
 * Usage: resampler_cost OS FS SECONDS  -> prints "ns_per_base_sample checksum"
 */
#include "dk_core.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>

#ifndef FIRST
#define FIRST 127
#endif
#ifndef LATER
#define LATER 23
#endif
#ifndef BETA0
#define BETA0 9.0
#endif
#ifndef BETA1
#define BETA1 9.0
#endif

using namespace dkcore;

// FirHalfBand::prepare with a given beta (prepare itself uses the core's FIR_BETA); same branch layout.
template <int N> void prepare_beta(FirHalfBand<N, double>& f, int taps, double beta) {
    f.nt = taps;
    constexpr int BR = FirHalfBand<N, double>::BR;
    double e[BR], o[BR];
    firwin_halfband(taps, beta, f.h);
    const int ne = (taps + 1) / 2, no = taps / 2;
    for (int k = 0; k < ne; ++k) e[k] = 2.0 * f.h[2 * k];
    for (int k = 0; k < no; ++k) o[k] = 2.0 * f.h[2 * k + 1];
    f.upe.set(e, ne); f.upo.set(o, no);
    for (int k = 0; k < ne; ++k) e[k] = f.h[2 * k];
    o[0] = 0.0;
    for (int k = 0; k < no; ++k) o[k + 1] = f.h[2 * k + 1];
    f.dne.set(e, ne); f.dno.set(o, no + 1);
}

int main(int argc, char** argv) {
    if (argc != 4) { std::fprintf(stderr, "usage: %s OS FS SECONDS\n", argv[0]); return 2; }
    const int os = std::atoi(argv[1]);
    const double fs = std::atof(argv[2]), seconds = std::atof(argv[3]);
    int nst = 0;
    while ((1 << nst) < os) ++nst;
    if ((1 << nst) != os || os < 2 || os > MAX_OS) return 2;
    static FirHalfBand<FIRST, double> first;
    static FirHalfBand<LATER, double> later[3];
    prepare_beta(first, FIRST, BETA0);
    for (int i = 1; i < nst; ++i) prepare_beta(later[i - 1], LATER, BETA1);
    const long n = long(seconds * fs);
    double acc = 0.0, a[MAX_OS], b[MAX_OS];
    auto one = [&](double x) {
        a[0] = x;
        int len = 1;
        double *cur = a, *nxt = b;
        for (int i = 0; i < nst; ++i) {
            if (i == 0) first.up(cur, nxt, len); else later[i - 1].up(cur, nxt, len);
            len *= 2;
            double* t = cur; cur = nxt; nxt = t;
        }
        for (int i = nst - 1; i >= 0; --i) {
            len /= 2;
            if (i == 0) first.down(cur, nxt, len); else later[i - 1].down(cur, nxt, len);
            double* t = cur; cur = nxt; nxt = t;
        }
        acc += cur[0];
    };
    static double in[4096];                                                       // input outside the timed loop
    for (int j = 0; j < 4096; ++j) in[j] = std::sin(0.13 * double(j));
    for (long j = 0; j < n / 10; ++j) one(in[j & 4095]);                          // warm-up
    const auto t0 = std::chrono::steady_clock::now();
    for (long j = 0; j < n; ++j) one(in[j & 4095]);
    const auto t1 = std::chrono::steady_clock::now();
    const double ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / double(n);
    std::printf("%.3f %.17g\n", ns, acc);
    return 0;
}
