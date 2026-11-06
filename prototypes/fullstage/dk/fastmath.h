/* #18: table + minimax-polynomial exp / log / log1p / pow (double), the C twin of dk/fastmath.py.
 * Same algorithm, same constants (fastmath_tables.h is generated from dk/fastmath.py), same operation order:
 * built without FMA contraction (plain -O2 on x86-64, or -ffp-contract=off) it returns the same bits as the
 * Python functions (dk/tests/test_fastmath.py, kernels on the bound grids and cur_fast on the currents grid).
 * Built with FMA contraction (-O2 -march=x86-64-v3) some bits differ; dk/tests/test_fastmath.py checks that build's
 * kernels and cur_fast against libm with the same BOUND (#60). Algorithms, domains and error bounds:
 * dk/fastmath.py docstring and BOUND.
 * Outside its domain each function calls libm, so it never returns something libm would not. */
#ifndef DK_FASTMATH_H
#define DK_FASTMATH_H
#include <math.h>
#include <stdint.h>
#include <string.h>
#include "fastmath_tables.h"

static inline uint64_t fm_asu(double d) { uint64_t u; memcpy(&u, &d, 8); return u; }
static inline double fm_asd(uint64_t u) { double d; memcpy(&d, &u, 8); return d; }

static inline double fm_exp(double x) {
    if (!(x >= FM_EXP_LO && x <= FM_EXP_HI)) return exp(x);
    double kd = x * FM_INV_LN2N + FM_SHIFT;
    int64_t k = (int64_t)(fm_asu(kd) - fm_asu(FM_SHIFT));      /* = (int64_t)(kd - SHIFT), without a cvt */
    kd = kd - FM_SHIFT;
    double r = (x - kd * FM_LN2N_HI) - kd * FM_LN2N_LO;
    double r2 = r * r;
    const double *c = FM_EXP_C;
    double p = 1.0 + (r + r2 * (c[2] + r * c[3]));
    uint64_t sb = fm_asu(FM_EXP_T[k & ((1 << FM_EXP_BITS) - 1)]) + ((uint64_t)(k >> FM_EXP_BITS) << 52);
    return fm_asd(sb) * p;
}

static inline double fm_log_poly(double r) {
    const double *a = FM_LOG_A;
    double r2 = r * r;
    return r + r2 * ((a[2] + r * a[3]) + r2 * (a[4] + r * a[5]));
}

/* x (positive normal) = 2^k m, m in [0.6875, 1.375); i = m's bin. Returns r = (m - c_i) / c_i. */
static inline double fm_log_reduce(double x, double *kd, int *i) {
    uint64_t ix = fm_asu(x), tmp = ix - FM_LOG_OFF;
    *i = (int)((tmp >> (52 - FM_LOG_BITS)) & ((1 << FM_LOG_BITS) - 1));
    *kd = (double)((int64_t)tmp >> 52);
    double m = fm_asd(ix - (tmp & (0xfffULL << 52)));
    return (m - FM_LOG_C[*i]) * FM_LOG_INV[*i];
}

static inline double fm_log(double x) {
    if (!(x >= 0x1p-1022 && x < INFINITY)) return log(x);
    double kd; int i;
    double r = fm_log_reduce(x, &kd, &i);
    return (kd * FM_LN2_HI + FM_LOG_LC[i]) + (kd * FM_LN2_LO + fm_log_poly(r));
}

/* 0 < e <= 1 (the softplus' log1p(exp(-|z|))); libm outside. */
static inline double fm_log1p(double e) {
    if (!(e > 0.0 && e <= 1.0)) return log1p(e);
    double t = 1.0 + e, c = e - (t - 1.0);
    double kd; int i;
    double r = fm_log_reduce(t, &kd, &i);
    return (kd * FM_LN2_HI + FM_LOG_LC[i]) + ((kd * FM_LN2_LO + fm_log_poly(r)) + c * FM_LOG_INV[i] * (1.0 - 0.5 * kd));
}

static inline double fm_pow(double s, double g) {
    if (!(s >= 0x1p-1022 && s < INFINITY)) return pow(s, g);
    return fm_exp(g * fm_log(s));
}
#endif
