"""Why the C++-built stage differs from Python by ~1e-7 V (#19): rounding of the DK matrices, not the per-sample code.

S (circuit.build) mixes conductances from 1e-6 S (Rg) to ~34 S (Ck at 16x), and the "out" rows of D / F hold entries
~7.5e4 that cancel to volts, so a few-ulp change in the matrices moves the output by ~1e-7 V within 10 ms (the slow
coupling-cap modes integrate it). This script measures, at standard (8x):
  1. numpy's and the C++ core's matrices against the exact matrices of the same double inputs (fractions.Fraction:
     exact sums, exact Gauss-Jordan, rounded once at the end);
  2. on cpp/vectors.txt inputs: |Python - Python with the exact matrices| (Python's own rounding floor),
     |C++ - Python| and |C++ - Python with the exact matrices|.
Run from prototypes/fullstage:  python -m cpp.matrix_rounding   (~5 s; builds test_core)
"""
import os
import sys
from fractions import Fraction as Fr
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk import circuit  # noqa: E402 -- after the sys.path setup
from dk.stage import Stage  # noqa: E402 -- after the sys.path setup
from cpp import validate_cpp as VC, export_vectors as EV  # noqa: E402 -- after the sys.path setup


def exact_build(fs, rk_scale=1.0, prewarp_hz=None):
    """circuit.build's A..K in exact rational arithmetic from the same double conductances, rounded at the end."""
    R, Cs, U, NL, OUT = circuit.netlist(rk_scale)
    nn, nu = len(circuit.NODES), len(U)
    def inc(L):
        return [[Fr(float(v)) for v in row] for row in circuit._inc([(p, q) for p, q, *_ in L])]
    Nr, Nx, Nn, No = inc(R), inc(Cs), inc(NL), inc(OUT)
    Nu = [[Fr(int(j == circuit.NODES.index(n))) for j in range(nn)] for n in U]
    gr = [Fr(1.0 / r) for _, _, r in R]
    gx = [Fr(circuit.companion_g(c, fs, prewarp_hz)) for _, _, c in Cs]
    def T(M):
        return [list(r) for r in zip(*M)]
    def mm(A, B):
        return [[sum((A[i][k] * B[k][j] for k in range(len(B))), Fr(0)) for j in range(len(B[0]))]
                           for i in range(len(A))]
    def dg(d):
        return [[d[i] if i == j else Fr(0) for j in range(len(d))] for i in range(len(d))]
    Y = [[a + b for a, b in zip(r1, r2)] for r1, r2 in zip(mm(mm(T(Nr), dg(gr)), Nr), mm(mm(T(Nx), dg(gx)), Nx))]
    n = nn + nu
    S = [[Fr(0)] * n for _ in range(n)]
    for i in range(nn):
        S[i][:nn] = Y[i]
    for r in range(nu):
        for c in range(nn):
            S[c][nn + r] = S[nn + r][c] = Nu[r][c]
    A = [row[:] + [Fr(int(i == j)) for j in range(n)] for i, row in enumerate(S)]
    for c in range(n):
        p = next(r for r in range(c, n) if A[r][c] != 0)
        A[c], A[p] = A[p], A[c]
        inv = 1 / A[c][c]
        A[c] = [v * inv for v in A[c]]
        for r in range(n):
            if r != c and A[r][c] != 0:
                f = A[r][c]
                A[r] = [a - f * b for a, b in zip(A[r], A[c])]
    Si = [row[n:] for row in A]
    def pad(M):
        return [row + [Fr(0)] * nu for row in M]
    Nxp, Nnp, Nop = pad(Nx), pad(Nn), pad(No)
    Nup = [[Fr(0)] * nn + [Fr(int(i == j)) for j in range(nu)] for i in range(nu)]
    XSi = mm(mm(dg([2 * g for g in gx]), Nxp), Si)
    OSi, NSi = mm(Nop, Si), mm(Nnp, Si)
    m = dict(A=mm(XSi, T(Nxp)), B=mm(XSi, T(Nup)), C=mm(XSi, T(Nnp)), D=mm(OSi, T(Nxp)), E=mm(OSi, T(Nup)),
             F=mm(OSi, T(Nnp)), G=mm(NSi, T(Nxp)), H=mm(NSi, T(Nup)), K=mm(NSi, T(Nnp)))
    for i in range(len(Cs)):
        m["A"][i][i] -= 1
    m["C"] = [[-v for v in r] for r in m["C"]]
    m["F"] = [[-v for v in r] for r in m["F"]]
    return {k: np.array([[float(v) for v in r] for r in M]) for k, M in m.items()}


def main():
    VC.EXE = VC.build()
    s = Stage(48000.0)
    ex = exact_build(s.fs)
    cs = VC.CppStage(48000.0)
    print("matrix error vs exact, max |dM| / max |M|:")
    for k in "ABCDEFGHK":
        sc = np.abs(ex[k]).max()
        print(f"  {k}: numpy {np.abs(s.m[k] - ex[k]).max() / sc:.2e}   C++ {np.abs(cs.m[k] - ex[k]).max() / sc:.2e}")
    V = EV.read()
    print("stage output on the vectors (480 base samples = 10 ms, standard), max |difference| in V:")
    for name in ("std_s005", "std_s2", "std_s8", "std_noise3"):
        x, ref = V[f"case.{name}.in"], V[f"case.{name}.out"]
        se = Stage(48000.0)
        for k in "ABCDEFGHK":
            se.m[k] = ex[k]
        se.K = se.m["K"].tolist()
        y_ex = se.process(x)["out"]
        y_c, _, _ = VC.cli(x, 8, 0.0)
        print(f"  {name}: Python vs Python(exact M) {np.abs(ref - y_ex).max():.2e}"
              f"   C++ vs Python {np.abs(y_c - ref).max():.2e}"
              f"   C++ vs Python(exact M) {np.abs(y_c - y_ex).max():.2e}   (peak {np.abs(ref).max():.1f} V)")


if __name__ == "__main__":
    main()
