/* Rough per-sample cost of the antialiasing options on the tabulated valve
 * stage, in plain scalar C (float audio path, double antiderivative table).
 * Not a plugin: no SIMD, no block tricks.
 * Build: gcc -O2 -std=c11 -D_POSIX_C_SOURCE=199309L -o bench/adaa_bench bench/adaa_bench.c -lm
 * Run from the lab root: ./bench/adaa_bench
 * Inputs written by scripts/run_all.py: table_f32.bin, table_meta.txt, fir_L{2,4,8}_f32.bin
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int M; static double XMAX, AMP, F0;
static float *tf; static double *tF; static double X0, DX, INVDX;

static float *load(const char *p, int *n) {
    FILE *f = fopen(p, "rb"); if (!f) { perror(p); exit(1); }
    fseek(f, 0, SEEK_END); long b = ftell(f); fseek(f, 0, SEEK_SET);
    float *d = malloc(b); if (fread(d, 1, b, f) != (size_t)b) exit(2); fclose(f);
    *n = (int)(b / sizeof(float)); return d;
}

static inline float f_lookup(float x) {
    double u = ((double)x - X0) * INVDX;
    if (u < 0) u = 0;
    if (u > M - 1.000001) u = M - 1.000001;
    int i = (int)u; float t = (float)(u - i);
    return tf[i] + t * (tf[i + 1] - tf[i]);
}
static inline double F_lookup(double x) {           /* exact integral of the linear-interp table */
    double xc = x < -XMAX ? -XMAX : (x > XMAX ? XMAX : x);
    double u = (xc - X0) * INVDX; if (u > M - 1.000001) u = M - 1.000001;
    int i = (int)u; double h = (u - i) * DX, f0 = tf[i], f1 = tf[i + 1];
    double r = tF[i] + f0 * h + 0.5 * (f1 - f0) * INVDX * h * h;
    if (x > XMAX) r += (x - XMAX) * tf[M - 1];
    if (x < -XMAX) r += (x + XMAX) * tf[0];
    return r;
}

typedef struct { int L, T; float *ph; float *hist; int hp; float *dec; int dp; int nh; float *h; } OS;

static void os_init(OS *o, int L, float *h, int nh) {
    o->L = L; o->nh = nh; o->h = h; o->T = (nh + L - 1) / L;
    o->ph = calloc((size_t)L * o->T, sizeof(float));   /* polyphase split, scaled by L */
    for (int k = 0; k < nh; k++) o->ph[(k % L) * o->T + k / L] = h[k] * L;
    o->hist = calloc(2 * o->T, sizeof(float)); o->hp = 0;
    o->dec = calloc(2 * nh, sizeof(float)); o->dp = 0;
}
/* one base-rate sample in, one out; nonlinearity mode 0 naive, 1 ADAA1 */
static double adaa_prev = 0;
static inline float nl_adaa(float x) {
    double xp = adaa_prev, d = (double)x - xp, y;
    if (fabs(d) < 1e-5) y = f_lookup((float)(0.5 * ((double)x + xp)));
    else y = (F_lookup(x) - F_lookup(xp)) / d;
    adaa_prev = x; return (float)y;
}
static inline float os_tick(OS *o, float x, int mode) {
    int T = o->T, L = o->L, nh = o->nh;
    o->hp = (o->hp == 0 ? T : o->hp) - 1;              /* newest at hp, mirrored at hp+T */
    o->hist[o->hp] = o->hist[o->hp + T] = x;
    for (int p = 0; p < L; p++) {
        const float *c = o->ph + p * T, *hs = o->hist + o->hp; float acc = 0;
        for (int k = 0; k < T; k++) acc += c[k] * hs[k];
        float y = mode ? nl_adaa(acc) : f_lookup(acc);
        o->dp = (o->dp == 0 ? nh : o->dp) - 1;
        o->dec[o->dp] = o->dec[o->dp + nh] = y;
    }
    const float *hs = o->dec + o->dp; float acc = 0;    /* decimate: one FIR output per L */
    for (int k = 0; k < nh; k++) acc += o->h[k] * hs[k];
    return acc;
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + 1e-9 * t.tv_nsec; }

int main(int argc, char **argv) {
    int dump = argc > 1 && strcmp(argv[1], "--dump") == 0;
    FILE *m = fopen("bench/table_meta.txt", "r"); if (!m) m = fopen("table_meta.txt", "r");
    if (!m || fscanf(m, "%d %lf %lf %lf", &M, &XMAX, &AMP, &F0) != 4) { fprintf(stderr, "run scripts/run_all.py first\n"); return 1; }
    const char *pre = fopen("bench/table_f32.bin", "rb") ? "bench/" : "";
    char p[256]; int n;
    snprintf(p, sizeof p, "%stable_f32.bin", pre); tf = load(p, &n);
    X0 = -XMAX; DX = 2 * XMAX / (M - 1); INVDX = 1 / DX;
    tF = malloc(sizeof(double) * M); tF[0] = 0;
    for (int i = 1; i < M; i++) tF[i] = tF[i - 1] + 0.5 * ((double)tf[i] + tf[i - 1]) * DX;
    const int N = 1 << 20; float *x = malloc(N * sizeof(float)), *y = malloc(N * sizeof(float));
    for (int i = 0; i < N; i++) x[i] = (float)(AMP * sin(2 * 3.14159265358979323846 * F0 * i / 48000.0));
    printf("table %d pts, amp %.3f V, f0 %.1f Hz, N=%d samples, best of 5 runs\n", M, AMP, F0, N);
    printf("%-22s %10s %12s\n", "method", "ns/sample", "x realtime@48k");
    struct { const char *name; int L; int mode; } cases[] = {
        {"naive", 1, 0}, {"ADAA1", 1, 1}, {"2x OS", 2, 0}, {"4x OS", 4, 0}, {"8x OS", 8, 0}, {"ADAA1 + 2x OS", 2, 1}};
    for (unsigned c = 0; c < sizeof cases / sizeof *cases; c++) {
        double best = 1e9; volatile float sink = 0;
        OS o = {0}; int L = cases[c].L;
        if (L > 1) { snprintf(p, sizeof p, "%sfir_L%d_f32.bin", pre, L); int nh; float *h = load(p, &nh); os_init(&o, L, h, nh); }
        for (int r = 0; r < 5; r++) {
            adaa_prev = 0; double t0 = now();
            if (L == 1 && cases[c].mode == 0) for (int i = 0; i < N; i++) y[i] = f_lookup(x[i]);
            else if (L == 1) for (int i = 0; i < N; i++) y[i] = nl_adaa(x[i]);
            else for (int i = 0; i < N; i++) y[i] = os_tick(&o, x[i], cases[c].mode);
            double dt = now() - t0; if (dt < best) best = dt; sink += y[N / 2];
        }
        double ns = 1e9 * best / N;
        if (dump) {   /* write output for the Python cross-check (tests/test_c_bench.py) */
            char q[256]; snprintf(q, sizeof q, "%sout_%u.f32", pre, c);
            FILE *fo = fopen(q, "wb"); fwrite(y, sizeof(float), N, fo); fclose(fo);
        }
        printf("%-22s %10.2f %12.0f\n", cases[c].name, ns, 1e9 / 48000.0 / ns);
    }
    return 0;
}
