/* #16 / #18: C ports of dk.triode.currents() (dk/triode_eval.h: before #16, after #16 = libm, fast = #18 fastmath),
 * timed per call on the grid of dk/bench_triode.py. Built and run by `python -m dk.bench_triode`:
 *   gcc -O2 -fno-builtin-exp -fno-builtin-log1p -fno-builtin-pow [-march=x86-64-v3] [-DR=float -DR_IS_FLOAT=1]
 *       bench_triode.c -lm
 * -fno-builtin-* keeps every exp/log1p/pow a real libm call (no CSE across them). The fast form is double only.
 * Two timings per form:
 *   throughput  independent points, as #16 measured it: consecutive calls can overlap in the CPU;
 *   chain       each call's Vgk depends on the previous call's output (by 0 * Ig, a real data dependency), so calls
 *               cannot overlap: the latency of one evaluation, which is what one Newton iteration waits for.
 * Prints one JSON object. */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include "triode_eval.h"

#define NG 30
#define NA 31
#define NP (NG * NA)
#define PASSES 2000
#define WARMUP 3
#define REPEAT 41
static R VG[NP], VA[NP];
static volatile R sink;
static volatile R zero = 0;

typedef void (*fn)(R, R, R *);

static double elapsed(struct timespec t0, struct timespec t1) {
    return ((t1.tv_sec - t0.tv_sec) * 1e9 + (t1.tv_nsec - t0.tv_nsec)) / ((double)PASSES * NP);
}

static double block(fn f) {
    struct timespec t0, t1; R o[6], acc = 0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int p = 0; p < PASSES; p++)
        for (int i = 0; i < NP; i++) { f(VG[i], VA[i], o); acc += o[0] + o[1] + o[2] + o[4] + o[5]; }
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sink = acc;
    return elapsed(t0, t1);
}

static double chain(fn f) {
    struct timespec t0, t1; R o[6] = {0}, z = zero;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int p = 0; p < PASSES; p++)
        for (int i = 0; i < NP; i++) f(VG[i] + z * o[0], VA[i], o);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    sink = o[1];
    return elapsed(t0, t1);
}

static int cmp(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return (x > y) - (x < y); }

static double maxrel(fn a_, fn b_) {      /* largest |b - a| / |a| over the outputs */
    double m = 0;
    for (int i = 0; i < NP; i++) {
        R a[6], b[6]; a_(VG[i], VA[i], a); b_(VG[i], VA[i], b);
        for (int k = 0; k < 6; k++) if (a[k] != b[k]) { double r = fabs((double)a[k] - b[k]) / fabs((double)a[k]); if (r > m) m = r; }
    }
    return m;
}

#if R_IS_FLOAT
#define NF 2
static const char *NAMES[NF] = {"before", "after"};
static fn FNS[NF] = {cur_before, cur_after};
#else
#define NF 3
static const char *NAMES[NF] = {"before", "after", "fast"};
static fn FNS[NF] = {cur_before, cur_after, cur_fast};
#endif

int main(void) {
    for (int i = 0; i < NG; i++)                     /* linspace(-4, 1, 30) x linspace(50, 350, 31) */
        for (int j = 0; j < NA; j++) { VG[i * NA + j] = -4.0 + 5.0 * i / (NG - 1); VA[i * NA + j] = 50.0 + 300.0 * j / (NA - 1); }
    static double t[2][NF][REPEAT];
    for (int w = 0; w < WARMUP; w++) for (int f = 0; f < NF; f++) { block(FNS[f]); chain(FNS[f]); }
    for (int r = 0; r < REPEAT; r++)                 /* interleaved across forms and both timings */
        for (int f = 0; f < NF; f++) { t[0][f][r] = block(FNS[f]); t[1][f][r] = chain(FNS[f]); }
    printf("{\"type\": \"%s\", \"points\": %d, \"passes\": %d, \"warmup\": %d, \"repeat\": %d", RNAME, NP, PASSES, WARMUP, REPEAT);
    for (int m = 0; m < 2; m++)
        for (int f = 0; f < NF; f++) {
            qsort(t[m][f], REPEAT, sizeof(double), cmp);
            printf(", \"%s%s_ns_per_call_median\": %.3f, \"%s%s_ns_per_call_min\": %.3f", NAMES[f], m ? "_chain" : "",
                   t[m][f][REPEAT / 2], NAMES[f], m ? "_chain" : "", t[m][f][0]);
        }
    printf(", \"max_rel_diff\": %.3g", maxrel(cur_before, cur_after));
#if !R_IS_FLOAT
    printf(", \"max_rel_diff_fast\": %.3g", maxrel(cur_after, cur_fast));
#endif
    printf("}\n");
    return 0;
}
