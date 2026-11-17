"""#18: derive the polynomial coefficients of dk/fastmath.py (minimax, relative error) and their approximation error.

Method. Each kernel polynomial is Taylor + a correction: P(r) = T_n(r) + sum_k d_k r^k, with the low coefficients
pinned (exp: 1 + r, log1p: r) so that exp(0) = 1 and log1p(r) ~ r stay exact. The corrections minimise the
maximum weighted error max_i |f(r_i) - P(r_i)| w(r_i) over 4001 Chebyshev-spaced points of the interval, as a
linear program (scipy HiGHS). The Taylor remainder f - T_n is computed in Decimal (60 digits) and scaled to O(1)
before the LP sees it, so double-precision LP tolerances are far below the error being minimised. The
coefficients are then rounded to double and their error is re-measured exactly (Decimal evaluation of the
rounded polynomial) on 20001 points: that is the approximation error `poly_err`. The rounding error of the double
evaluation comes on top; dk/tests/test_fastmath.py measures the whole function.
  exp kernel   e^r ~ 1 + r + c2 r^2 + ... + cn r^n     |r| <= ln2 / (2 N_EXP) (+0.1 %)     weight 1 / e^r
  log1p kernel log1p(r) ~ r + a2 r^2 + ... + an r^n    r in fastmath.log_r_range() (+0.1 %)  weight 1 / |log1p(r)|
Run from prototypes/fullstage:  python -m dk.fastmath_fit [--exp-deg N] [--log-deg N]
prints the coefficients as hex literals (paste into dk/fastmath.py; the evaluation in fastmath.py / fastmath.h is
written out for the committed degrees, 3 and 5) and their errors.
"""
import argparse
import math
import os
import sys
from decimal import Decimal as D, getcontext
import numpy as np
from scipy.optimize import linprog
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dk import fastmath as FM

getcontext().prec = 60
NFIT, NCHECK = 4001, 20001


def _cheb(lo, hi, n):
    k = np.arange(n)
    return [D(float(v)) for v in (0.5 * (lo + hi) - 0.5 * (hi - lo) * np.cos(np.pi * k / (n - 1)))]


def _fexp(r):
    return r.exp()


def _flog1p(r):
    return (1 + r).ln()


def _poly(c, r):
    p = D(0)
    for ck in reversed(c):
        p = p * r + ck
    return p


def fit(f, taylor, pinned, n, lo, hi):
    """Minimax (weight 1/|f|) of f on [lo, hi] by Taylor + corrections of degree pinned..n. Returns rounded coeffs."""
    rs = [r for r in _cheb(lo, hi, NFIT) if r != 0]
    fr = [f(r) for r in rs]
    q = [(v - _poly(taylor, r)) / abs(v) for r, v in zip(rs, fr)]     # weighted Taylor remainder
    s = max(abs(v) for v in q)
    # weighted error / s = q / s - sum_k x_k B[:, k],  correction coefficient k = x_k * s / colscale_k
    B = np.array([[float(r ** k / abs(v)) for k in range(pinned, n + 1)] for r, v in zip(rs, fr)])
    col = np.abs(B).max(axis=0)
    B = B / col
    qv = np.array([float(v / s) for v in q])
    m, one = B.shape[1], np.ones((len(rs), 1))
    A = np.vstack([np.hstack([-B, -one]), np.hstack([B, -one])])         # |q - B x| <= E, minimise E
    res = linprog(np.r_[np.zeros(m), 1.0], A_ub=A, b_ub=np.concatenate([-qv, qv]),
                  bounds=[(None, None)] * m + [(0, None)], method="highs")
    assert res.status == 0, res.message
    c = list(taylor) + [D(0)] * (n + 1 - len(taylor))
    for j, k in enumerate(range(pinned, n + 1)):
        c[k] += D(float(res.x[j])) * s / D(float(col[j]))
    return [float(v) for v in c]


def exp_interval():
    h = math.log(2.0) / (2 * FM.N_EXP) * 1.001
    return -h, h


def log_interval():
    lo, hi = FM.log_r_range()
    return lo * 1.001, hi * 1.001


def poly_err(f, c, lo, hi, n=NCHECK):
    """Exact (Decimal) max relative error of the polynomial with the given (double) coefficients on [lo, hi]."""
    cd = [D(v) for v in c]
    return float(max(abs(_poly(cd, r) / f(r) - 1) for r in _cheb(lo, hi, n) if r != 0))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--exp-deg", type=int, default=None)
    ap.add_argument("--log-deg", type=int, default=None)
    a = ap.parse_args(argv)
    de, dl = a.exp_deg or len(FM.EXP_C) - 1, a.log_deg or len(FM.LOG_A) - 1
    (he_lo, he), (ll, lh) = exp_interval(), log_interval()
    ce = fit(_fexp, [D(1), D(1)] + [1 / D(math.factorial(k)) for k in range(2, de + 1)], 2, de, he_lo, he)
    cl = fit(_flog1p, [D(0), D(1)] + [D((-1) ** (k + 1)) / k for k in range(2, dl + 1)], 2, dl, ll, lh)
    print(f"exp kernel, degree {de}, |r| <= {he:.6g}: poly_err {poly_err(_fexp, ce, he_lo, he):.3g}")
    print("  EXP_C = (" + ", ".join(v.hex() for v in ce) + ")")
    print(f"log1p kernel, degree {dl}, r in [{ll:.6g}, {lh:.6g}]: poly_err {poly_err(_flog1p, cl, ll, lh):.3g}")
    print("  LOG_A = (" + ", ".join(v.hex() for v in cl) + ")")


if __name__ == "__main__":
    main()
