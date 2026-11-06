/* #16: C port of dk.triode.currents() before / after (same expressions as the Python gold and the new code),
 * timed per call on the grid of dk/bench_triode.py. Built and run by `python -m dk.bench_triode`:
 *   gcc -O2 -fno-builtin-exp -fno-builtin-log1p -fno-builtin-pow [-DR=float -DR_IS_FLOAT=1] bench_triode.c -lm
 * -fno-builtin-* keeps every exp/log1p/pow a real libm call in both versions (no CSE across them).
 * Prints one JSON object. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

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

#define NG 30
#define NA 31
#define NP (NG * NA)
#define PASSES 2000
#define WARMUP 3
#define REPEAT 41
static R VG[NP], VA[NP];
static volatile R sink;

static double block(void (*f)(R, R, R *)) {
    struct timespec t0, t1; R o[6], acc = 0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int p = 0; p < PASSES; p++)
        for (int i = 0; i < NP; i++) { f(VG[i], VA[i], o); acc += o[0] + o[1] + o[2] + o[4] + o[5]; }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sink = acc;
    return ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / ((double)PASSES * NP);
}

static int cmp(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

int main(void) {
    for (int i = 0; i < NG; i++)                     /* linspace(-4, 1, 30) x linspace(50, 350, 31) */
        for (int j = 0; j < NA; j++) { VG[i * NA + j] = -4.0 + 5.0 * i / (NG - 1); VA[i * NA + j] = 50.0 + 300.0 * j / (NA - 1); }
    double maxrel = 0;                               /* after vs before, relative to the entry's magnitude */
    for (int i = 0; i < NP; i++) {
        R a[6], b[6]; cur_before(VG[i], VA[i], a); cur_after(VG[i], VA[i], b);
        for (int k = 0; k < 6; k++) if (a[k] != b[k]) { double r = fabs((double)a[k] - b[k]) / fabs((double)a[k]); if (r > maxrel) maxrel = r; }
    }
    double tb[REPEAT], ta[REPEAT];
    for (int w = 0; w < WARMUP; w++) { block(cur_before); block(cur_after); }
    for (int r = 0; r < REPEAT; r++) { tb[r] = block(cur_before); ta[r] = block(cur_after); }   /* interleaved */
    qsort(tb, REPEAT, sizeof(double), cmp); qsort(ta, REPEAT, sizeof(double), cmp);
    printf("{\"type\": \"%s\", \"points\": %d, \"passes\": %d, \"warmup\": %d, \"repeat\": %d, "
           "\"before_ns_per_call_median\": %.3f, \"before_ns_per_call_min\": %.3f, "
           "\"after_ns_per_call_median\": %.3f, \"after_ns_per_call_min\": %.3f, \"max_rel_diff\": %.3g}\n",
           RNAME, NP, PASSES, WARMUP, REPEAT, tb[REPEAT / 2], tb[0], ta[REPEAT / 2], ta[0], maxrel);
    return 0;
}
