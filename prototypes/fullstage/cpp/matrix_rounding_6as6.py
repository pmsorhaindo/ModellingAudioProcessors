"""Why the C++-built 6AS6 stage differs from Python by more at hq than on its own pack (#104): rounding of the DK
matrices, not the per-sample code (cpp/matrix_rounding.py's method, for the 6AS6 netlist).

The C++ core builds its matrices with a Gauss-Jordan inverse, numpy with LAPACK; both round, differently, by ~1e-12
relative. The stage's slow coupling-cap modes integrate a small difference, more at 16x than at 8x. With Python's own
pack loaded the C++ per-sample code agrees to ~1e-12 V (cpp/test_core_6as6.cpp, "pinned"). This script measures, per
setting:
  1. numpy's and the C++ core's matrices against the exact matrices of the same double inputs (fractions.Fraction:
     exact sums, exact Gauss-Jordan, rounded once at the end), at the same Rk (Python's);
  2. on a 0.1 s, 1 kHz sine through Stage.process: |Python - Python with the exact matrices| (Python's own rounding
     floor), |C++ - Python| and |C++ - Python with the exact matrices|.
Run from prototypes/fullstage:  python -m cpp.matrix_rounding_6as6   (~1 min; builds test_core_6as6)
"""
import os
import sys
import warnings
from fractions import Fraction as Fr
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk6as6 import circuit  # noqa: E402 -- after the sys.path setup
from dk6as6.stage import Stage  # noqa: E402 -- after the sys.path setup
from cpp import validate_cpp_6as6 as VC  # noqa: E402 -- after the sys.path setup

# (mode, grid hardness, bias mA, os, drive V): the settings with the largest hq deviations in cpp/results_6as6.json,
# and the default setting at both qualities
SETTINGS = [("P1", 1.0, 0.45, 8, 4.0), ("P1", 1.0, 0.45, 16, 4.0), ("P2", 0.0, 0.45, 8, 4.0),
            ("P2", 0.0, 0.45, 16, 4.0), ("T", 0.0, 0.35, 16, 4.0)]


def exact_build(fs, rk, mode, grid):
    """circuit.build's A..K in exact rational arithmetic from the same double conductances, rounded at the end."""
    R, Cs, U, NV, NI, OUT = circuit.netlist(rk, mode=mode, grid=grid)
    nn, nu = len(circuit.NODES), len(U)
    def inc(L):
        return [[Fr(float(v)) for v in row] for row in circuit._inc([(p, q) for p, q, *_ in L])]
    Nr, Nx, Nv, Ni, No = inc(R), inc(Cs), inc(NV), inc(NI), inc(OUT)
    Nu = [[Fr(float(v)) for v in row] for row in circuit._nu(U)]
    gr = [Fr(1.0 / r) for _, _, r in R]
    gx = [Fr(circuit.companion_g(c, fs)) for _, _, c in Cs]
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
    Nxp, Nvp, Nip, Nop = pad(Nx), pad(Nv), pad(Ni), pad(No)
    Nup = [[Fr(0)] * nn + [Fr(int(i == j)) for j in range(nu)] for i in range(nu)]
    XSi = mm(mm(dg([2 * g for g in gx]), Nxp), Si)
    OSi, VSi = mm(Nop, Si), mm(Nvp, Si)
    m = dict(A=mm(XSi, T(Nxp)), B=mm(XSi, T(Nup)), C=mm(XSi, T(Nip)), D=mm(OSi, T(Nxp)), E=mm(OSi, T(Nup)),
             F=mm(OSi, T(Nip)), G=mm(VSi, T(Nxp)), H=mm(VSi, T(Nup)), K=mm(VSi, T(Nip)))
    for i in range(len(Cs)):
        m["A"][i][i] -= 1
    m["C"] = [[-v for v in r] for r in m["C"]]
    m["F"] = [[-v for v in r] for r in m["F"]]
    return {k: np.array([[float(v) for v in r] for r in M]) for k, M in m.items()}


def measure(mode, h, bias, os_, drive, fs=48000.0, seconds=0.1):
    warnings.simplefilter("ignore")
    st = Stage(fs, bias=bias, mode=mode, quality=(os_, None), experimental=True, grid_hardness=h)
    ex = exact_build(st.fs, st.rk, mode, h != 0.0)
    cs = VC.CppStage(fs, bias=bias, mode=mode, quality=(os_, None), experimental=True, grid_hardness=h)
    mat = {k: (float(np.abs(st.m[k] - ex[k]).max() / np.abs(ex[k]).max()),
               float(np.abs(cs.m[k] - ex[k]).max() / np.abs(ex[k]).max())) for k in "ABCDEFGHK"}
    t = np.arange(int(seconds * fs)) / fs
    x = drive * np.sin(2 * np.pi * 1000 * t)
    y_py = st.process(x)["out"]
    se = Stage(fs, bias=bias, mode=mode, quality=(os_, None), experimental=True, grid_hardness=h)
    for k in "ABCDEFGHK":
        se.m[k] = ex[k]
    se.K = se.m["K"].tolist()
    se.reset()                                  # the rest constants from the exact matrices
    y_ex = se.process(x)["out"]
    y_c = cs.process(x)["out"]
    return dict(mode=mode, hardness=h, bias_ma=bias, os=os_, drive=drive, matrices=mat,
                py_vs_py_exact=float(np.abs(y_py - y_ex).max()), cpp_vs_py=float(np.abs(y_c - y_py).max()),
                cpp_vs_py_exact=float(np.abs(y_c - y_ex).max()), peak=float(np.abs(y_py).max()))


def main():
    VC.exe()
    for s in SETTINGS:
        r = measure(*s)
        print(f"{r['mode']} h={r['hardness']:g} {r['bias_ma']} mA {r['os']}x {r['drive']:g} V "
              f"(peak {r['peak']:.1f} V): "
              f"Python vs Python(exact M) {r['py_vs_py_exact']:.2e}, C++ vs Python {r['cpp_vs_py']:.2e}, "
              f"C++ vs Python(exact M) {r['cpp_vs_py_exact']:.2e} V")
        print("   matrices vs exact, max |dM| / max |M| (numpy, C++): " +
              ", ".join(f"{k} {a:.1e} / {b:.1e}" for k, (a, b) in r["matrices"].items()))


if __name__ == "__main__":
    main()
