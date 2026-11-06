/* #18: test driver for dk/fastmath.h and dk/triode_eval.h's cur_fast, used by dk/tests/test_fastmath.py.
 *   fastmath_check exp|log|log1p          reads doubles x on stdin, writes f(x)
 *   fastmath_check pow G                  reads doubles s, writes fm_pow(s, G)
 *   fastmath_check currents               reads pairs (Vgk, Vak), writes cur_fast's 6 outputs per pair
 * Raw native-endian doubles in and out. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "triode_eval.h"

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    const char *k = argv[1];
    double g = argc > 2 ? atof(argv[2]) : 0, x[2], o[6];
    if (!strcmp(k, "currents")) {
        while (fread(x, sizeof(double), 2, stdin) == 2) { cur_fast(x[0], x[1], o); fwrite(o, sizeof(double), 6, stdout); }
        return 0;
    }
    while (fread(x, sizeof(double), 1, stdin) == 1) {
        double y;
        if (!strcmp(k, "exp")) y = fm_exp(x[0]);
        else if (!strcmp(k, "log")) y = fm_log(x[0]);
        else if (!strcmp(k, "log1p")) y = fm_log1p(x[0]);
        else if (!strcmp(k, "pow")) y = fm_pow(x[0], g);
        else return 2;
        fwrite(&y, sizeof(double), 1, stdout);
    }
    return 0;
}
