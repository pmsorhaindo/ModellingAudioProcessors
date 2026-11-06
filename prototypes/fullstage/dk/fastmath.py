"""#18: table + minimax-polynomial exp / log / log1p / pow for the triode evaluation, with tested error bounds.

This module is the reference implementation in Python (double). dk/fastmath.h is the C implementation of the same
algorithm, operation for operation (no FMA contraction), so on any IEEE-754 double machine both return the same bits
(dk/tests/test_fastmath.py checks it); its tables are generated from this module (fastmath_tables.h, `python -m
dk.fastmath --header`). In Python these functions are SLOWER than libm (a math.exp call is one C call; this is
~20 bytecodes): the Python path exists to run the stage with exactly the C arithmetic. The speedup is in C
(dk/bench_triode.py, dk/RESULTS.md).

Algorithms (glibc-style table reduction, our own tables and coefficients, dk/fastmath_fit.py):
  exp(x)    k = round(x N / ln2), r = x - k ln2/N (Cody-Waite, two constants), |r| <= ln2 / 2N, N = 128;
            exp(x) = 2^(k div N) * T[k mod N] * P(r),  T[j] = 2^(j/N) correctly rounded,  P = 1 + r + c2 r^2 ...
  log(x)    x = 2^k m, m in [0.6875, 1.375) (bit offset trick), bin i of m (7 bits: 80 bins of 2^-8 below 1, 48
            of 2^-7 above), c_i = bin centre, except c = 1 exactly for the two bins next to 1, [1 - 2^-8, 1) and
            [1, 1 + 2^-7); r = (m - c_i) * (1/c_i) (m - c_i is exact, so r has a relative, not absolute, rounding
            error); log x = k ln2 + ln c_i + P(r), P = r + a2 r^2 + ... (minimax for log1p on the r range).
            Near 1 (k = 0, c = 1) this is log x = P(x - 1) with x - 1 exact, so the error stays relative as x -> 1
            from either side (#60: with the centre 0.998046875 below 1, ln c + P(r) cancelled and the relative
            error had no bound, 0.95 at nextafter(1, 0)).
  log1p(e)  for 0 < e <= 1 (all the softplus needs): t = 1 + e, c = e - (t - 1) exactly (Fast2Sum), then the log
            kernel on t plus the first-order correction c / t ~ c (1/c_i) 2^-k. Relative accuracy down to e -> 0
            (the c_i = 1 bin has ln c_i = 0 and r = t - 1 exact).
  pow(s, g) exp(g log s) (= exp2(g log2 s), natural base so the exp and log kernels are shared). Its relative error
            grows with |g log s| (rounding of g log s): see POW_* below.
Outside each function's domain the libm function is called (math.exp / math.log / math.log1p / **; in C exp /
log / log1p / pow): exp for x < EXP_LO (result below ~2.2e-308, libm gives a subnormal) or x > EXP_HI, log / pow
for x not a positive normal double, log1p for e outside (0, 1]. In C the results there are libm's bit for bit; in
Python they are math's, which raises where C returns inf / NaN (math.exp(710), math.log(0), math.log(-1)). The
stage never gets there (dk/fastmath_domain.json).

Error bounds (relative, against glibc libm, measured by dk/tests/test_fastmath.py on dense grids and asserted):
see the BOUND dict. They are measured bounds plus margin, not proofs; the polynomial part is exact
(fastmath_fit.poly_err: exp 3.86e-13, log1p kernel 9.21e-14), the rest is double rounding (a few ulps).
"""
import math, sys
from decimal import Decimal as D, localcontext

# ---- parameters and tables ------------------------------------------------------------------------------
EXP_BITS = 7
N_EXP = 1 << EXP_BITS
LOG_BITS = 7                        # 128 bins; the offset trick below assumes 7 (80 + 48 bins)
LOG_OFF = 0x3fe6000000000000        # bits of 0.6875
SHIFT = float.fromhex("0x1.8p52")  # round-to-nearest-integer shifter
EXP_LO, EXP_HI = -708.39, 709.0     # [EXP_LO, EXP_HI]: 2^(k div N) stays a normal double; outside: libm
DBL_MIN = sys.float_info.min


def _round_bits(x, bits):
    """x rounded to `bits` significant bits (so k * x is exact for |k| < 2^(53 - bits))."""
    m, e = math.frexp(x)
    return math.ldexp(round(m * (1 << bits)), e - bits)


def _tables():
    with localcontext() as ctx:
        ctx.prec = 40
        ln2 = D(2).ln()
        t = [float(D(2) ** (D(j) / N_EXP)) for j in range(N_EXP)]
        inv_ln2n = float(N_EXP / ln2)
        hi = _round_bits(float(ln2 / N_EXP), 35)                     # |k| <= 2^17
        lo = float(ln2 / N_EXP - D(hi))
        l2hi = _round_bits(float(ln2), 40)                            # |k| <= 1075
        l2lo = float(ln2 - D(l2hi))
        c, inv, lc = [], [], []
        for i in range(1 << LOG_BITS):
            a, w = (0.6875 + i / 256.0, 1 / 256.0) if i < 80 else (1.0 + (i - 80) / 128.0, 1 / 128.0)
            ci = 1.0 if i in (79, 80) else a + 0.5 * w         # 79 = [1 - 2^-8, 1): see fast_log
            c.append(ci)
            inv.append(float(1 / D(ci)))
            lc.append(float(D(ci).ln()))
    return t, inv_ln2n, hi, lo, l2hi, l2lo, tuple(c), tuple(inv), tuple(lc)


EXP_T, INV_LN2N, LN2N_HI, LN2N_LO, LN2_HI, LN2_LO, LOG_C, LOG_INV, LOG_LC = _tables()
EXP_T = tuple(EXP_T)


def log_bins(bits=LOG_BITS):
    """[(lo, hi, c)] of the log reduction bins (m in [lo, hi), centre c)."""
    assert bits == 7
    return [((0.6875 + i / 256.0, 0.6875 + (i + 1) / 256.0) if i < 80 else
             (1.0 + (i - 80) / 128.0, 1.0 + (i - 79) / 128.0)) + (LOG_C[i],) for i in range(128)]


def log_r_range(bits=LOG_BITS):
    """Range of r = (m - c) / c over all bins."""
    rs = [(v - c) / c for lo, hi, c in log_bins(bits) for v in (lo, hi)]
    return min(rs), max(rs)


# ---- coefficients (python -m dk.fastmath_fit) -----------------------------------------------------------
H = float.fromhex
EXP_C = tuple(map(H, ("0x1.0000000000000p+0", "0x1.0000000000000p+0", "0x1.0000088170cb7p-1", "0x1.5555567dc582ep-3")))
LOG_A = tuple(map(H, ("0x0.0p+0", "0x1.0000000000000p+0", "-0x1.000000000978cp-1", "0x1.555556e3a1bc5p-2",
                      "-0x1.000036696d7d0p-2", "0x1.9673fc75c2346p-3")))

# ---- error bounds (dk/tests/test_fastmath.py) -----------------------------------------------------------
BOUND = dict(
    # Kernels: relative |fast - libm| / |libm|, worst on the grids in dk/tests/test_fastmath.py (#60, 6 Nov 2026,
    # this box; the same worst for C built with and without FMA contraction), then ~2.5-3.5x margin. Not proofs.
    exp=1e-12,          # measured 3.87e-13 on [EXP_LO, EXP_HI] (incl. the EXP_LO edge)
    log=3e-13,          # measured 9.21e-14 on positive normals: logspace 1e-307..1e308, every bin, 1 -+ 2^-k, 1 -+ 1e-16..1e-2
    log1p=3e-13,        # measured 9.21e-14 on (0, 1]
    pow_g1_26=1e-12,    # measured 3.97e-13 on the stage domain for gamma=1.26 (and a bit beyond)
    pow_g1_314=1e-12,   # measured 4.21e-13 for xi=1.314
    # currents_fast vs currents: componentwise against the term magnitudes, |d out| / (sum of |terms| of out), e.g.
    # Ia = Ik - Ig -> |d Ia| / (Ik + Ig) (test_fastmath.currents_err; a plain relative error has no bound where Ia
    # crosses zero). Measured 8.78e-13 on Vgk -16..+2 x Vak 0..450 V (121 x 121, the recorded stage domain is
    # Vgk -13.9..+0.2, Vak 12.8..297) plus 108 points on and next to the Ia = 0 curve; 9.22e-13 on 2e5 random points.
    currents=3e-12,
)


# ---- functions -----------------------------------------------------------------------------------------
def fast_exp(x):
    if not (EXP_LO <= x <= EXP_HI):
        return math.exp(x)
    kd = x * INV_LN2N + SHIFT
    kd = kd - SHIFT
    k = int(kd)
    r = (x - kd * LN2N_HI) - kd * LN2N_LO
    r2 = r * r
    c = EXP_C
    p = 1.0 + (r + r2 * (c[2] + r * c[3]))
    return math.ldexp(EXP_T[k & (N_EXP - 1)], k >> EXP_BITS) * p


def _log_parts(x):
    """x (positive normal) = 2^k m, m in [0.6875, 1.375), and m's bin i: the same k, i, m as fastmath.h's bit trick."""
    m, k = math.frexp(x)
    if m < 0.6875:
        m, k = m * 2.0, k - 1
        return k, int((m - 1.0) * 128.0) + 80, m
    return k, int((m * 2.0 - 1.0) * 128.0) - 48, m


def _log_poly(r):
    a = LOG_A
    r2 = r * r
    return r + r2 * ((a[2] + r * a[3]) + r2 * (a[4] + r * a[5]))


def fast_log(x):
    if not (DBL_MIN <= x < math.inf):
        return math.log(x)
    k, i, m = _log_parts(x)
    r = (m - LOG_C[i]) * LOG_INV[i]
    kd = float(k)
    return (kd * LN2_HI + LOG_LC[i]) + (kd * LN2_LO + _log_poly(r))


def fast_log1p(e):
    if not (0.0 < e <= 1.0):
        return math.log1p(e)
    t = 1.0 + e
    c = e - (t - 1.0)
    k, i, m = _log_parts(t)
    r = (m - LOG_C[i]) * LOG_INV[i]
    kd = float(k)
    return (kd * LN2_HI + LOG_LC[i]) + ((kd * LN2_LO + _log_poly(r)) + c * LOG_INV[i] * (1.0 - 0.5 * kd))


def fast_pow(s, g):
    if not (DBL_MIN <= s < math.inf):
        return s ** g
    return fast_exp(g * fast_log(s))


# ---- C header ------------------------------------------------------------------------------------------
def header():
    """dk/fastmath_tables.h: the tables and coefficients above as C hex literals."""
    arr = lambda name, v: (f"static const double {name}[{len(v)}] = {{\n" +
                           "".join(f"    {x.hex()},\n" for x in v) + "};\n")
    out = ["/* GENERATED by `python -m dk.fastmath --header > dk/fastmath_tables.h` from dk/fastmath.py (#18). */\n",
           "#define FM_EXP_BITS %d\n#define FM_LOG_BITS %d\n" % (EXP_BITS, LOG_BITS),
           "#define FM_LOG_OFF 0x%016xULL\n" % LOG_OFF,
           "".join(f"#define FM_{n} {v.hex()}\n" for n, v in (("SHIFT", SHIFT), ("EXP_LO", EXP_LO),
                   ("EXP_HI", EXP_HI), ("INV_LN2N", INV_LN2N), ("LN2N_HI", LN2N_HI), ("LN2N_LO", LN2N_LO),
                   ("LN2_HI", LN2_HI), ("LN2_LO", LN2_LO))),
           arr("FM_EXP_C", EXP_C), arr("FM_LOG_A", LOG_A), arr("FM_EXP_T", EXP_T),
           arr("FM_LOG_C", LOG_C), arr("FM_LOG_INV", LOG_INV), arr("FM_LOG_LC", LOG_LC)]
    return "".join(out)


if __name__ == "__main__":
    if sys.argv[1:] == ["--header"]:
        sys.stdout.write(header())
    else:
        sys.exit("usage: python -m dk.fastmath --header > dk/fastmath_tables.h")
