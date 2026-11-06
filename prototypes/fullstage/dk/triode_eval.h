/* C ports of dk.triode.currents(), shared by dk/bench_triode.c and dk/tests/fastmath_check.c:
 *   cur_before  the pre-#16 formulas (4 exp + 2 log1p + 4 pow), as dk/tests/test_triode.py's gold copy
 *   cur_after   #16, triode.currents() (shared exp, divided powers; 2 exp + 2 log1p + 2 pow, libm)
 *   cur_fast    #18, triode.currents_fast(): cur_after with dk/fastmath.h's exp / log1p / pow (double only)
 * Same expressions, in the same order, as the Python. R = double, or float with -DR=float -DR_IS_FLOAT=1.
 * Output o[6] = (Ig, Ia, dIg/dVgk, dIg/dVak, dIa/dVgk, dIa/dVak). */
#ifndef DK_TRIODE_EVAL_H
#define DK_TRIODE_EVAL_H
#include <math.h>
#ifndef R
#define R double
#endif
#if R_IS_FLOAT
#define EXP expf
#define LOG1P log1pf
#define POW powf
#define FABS fabsf
#define RNAME "float"
#else
#define EXP exp
#define LOG1P log1p
#define POW pow
#define FABS fabs
#define RNAME "double"
#include "fastmath.h"
#endif

static const R G = 2.242e-3, MU = 103.2, GAM = 1.26, C = 3.40, GG = 6.177e-4, XI = 1.314, CG = 9.901, IG0 = 8.025e-8;

static R softplus(R x) { return (x > 0 ? x : 0) + LOG1P(EXP(-FABS(x))); }
static R sigmoid(R x) {
    if (x >= 0) return 1 / (1 + EXP(-x));
    R e = EXP(x);
    return e / (1 + e);
}

__attribute__((noinline)) static void cur_before(R vgk, R vak, R o[6]) {
    R zk = C * (vak / MU + vgk), sk = softplus(zk) / C;
    R ik = G * POW(sk, GAM), dik = G * GAM * POW(sk, GAM - 1) * sigmoid(zk);
    R zg = CG * vgk, sg = softplus(zg) / CG;
    R ig = GG * POW(sg, XI) + IG0, dig = GG * XI * POW(sg, XI - 1) * sigmoid(zg);
    o[0] = ig; o[1] = ik - ig; o[2] = dig; o[3] = 0; o[4] = dik - dig; o[5] = dik / MU;
}

static void sp_sig(R x, R *sp, R *sg) {
    R e = EXP(-FABS(x));
    if (x >= 0) { *sp = x + LOG1P(e); *sg = 1 / (1 + e); }
    else        { *sp = LOG1P(e);     *sg = e / (1 + e); }
}

__attribute__((noinline)) static void cur_after(R vgk, R vak, R o[6]) {
    R zk = C * (vak / MU + vgk), spk, sigk; sp_sig(zk, &spk, &sigk);
    R sk = spk / C, pk = POW(sk, GAM), ik = G * pk, dik = G * GAM * (sk > 0 ? pk / sk : 0) * sigk;
    R zg = CG * vgk, spg, sigg; sp_sig(zg, &spg, &sigg);
    R sg = spg / CG, pg = POW(sg, XI), ig = GG * pg + IG0, dig = GG * XI * (sg > 0 ? pg / sg : 0) * sigg;
    o[0] = ig; o[1] = ik - ig; o[2] = dig; o[3] = 0; o[4] = dik - dig; o[5] = dik / MU;
}

#if !R_IS_FLOAT
static inline void sp_sig_fast(double x, double *sp, double *sg) {
    double e = fm_exp(-fabs(x));
    if (x >= 0) { *sp = x + fm_log1p(e); *sg = 1 / (1 + e); }
    else        { *sp = fm_log1p(e);     *sg = e / (1 + e); }
}

__attribute__((noinline)) static void cur_fast(double vgk, double vak, double o[6]) {
    double zk = C * (vak / MU + vgk), spk, sigk; sp_sig_fast(zk, &spk, &sigk);
    double sk = spk / C, pk = fm_pow(sk, GAM), ik = G * pk, dik = G * GAM * (sk > 0 ? pk / sk : 0) * sigk;
    double zg = CG * vgk, spg, sigg; sp_sig_fast(zg, &spg, &sigg);
    double sg = spg / CG, pg = fm_pow(sg, XI), ig = GG * pg + IG0, dig = GG * XI * (sg > 0 ? pg / sg : 0) * sigg;
    o[0] = ig; o[1] = ik - ig; o[2] = dig; o[3] = 0; o[4] = dik - dig; o[5] = dik / MU;
}
#endif
#endif
