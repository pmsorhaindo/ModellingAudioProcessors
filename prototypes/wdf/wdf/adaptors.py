"""Wave digital filter building blocks: one-port leaves, two-child series / parallel adaptors, and the R-type root
adaptor with a multiport nonlinearity above it.

Waves (voltage waves, Fettweis 1986). At every port, with v the port voltage and i the current into the subtree below
the port, the wave travelling down (incident on the subtree) is A = v + R i and the wave travelling up (reflected by
it) is B = v - R i, R being the port resistance. Every subtree is adapted towards its parent (its B never depends on
the A of the same sample), so a sample is two passes over the tree: up() from the leaves to the root, then down(A)
from the root to the leaves. v = (A + B) / 2 at every port.
  Resistor R               B = 0
  ResistiveSource e, R     B = e                       (v = e + R i: an ideal source e in series with R)
  Capacitor                B[n] = A[n-1], R = 1/Gc     Gc = 2C/T is the trapezoidal rule (the bilinear map), the same
                                                       companion conductance as the DK stage (dk/circuit.companion_g,
                                                       which also gives the pre-warped Gc of the eco setting)
  Series (same i)          R = R1 + R2, B = B1 + B2;           down: A_k = B_k + R_k (A - B) / R
  Parallel (same v)        G = G1 + G2, B = (G1 B1 + G2 B2) / G; down: A_k = A + B - B_k
The subtree convention (current into the subtree) keeps every port voltage the physical one, so a series adaptor's
port voltage is the sum of its children's, with no sign flips.

Root. K. J. Werner, V. Nangia, J. O. Smith III, J. S. Abel, "Resolving Wave Digital Filters with Multiple/Multiport
Nonlinearities", DAFx-15 (https://www.dafx.de/paper-archive/2015/DAFx-15_submission_54.pdf): all nonlinear ports
sit above an R-type adaptor, whose scattering matrix S (b = S a, adaptor-side waves) comes from Modified Nodal
Analysis, their eq. (19)-(20): S = I + 2 [0 R] X^-1 [0 I]^T, X the MNA matrix of the adaptor with every port n
replaced by its Thevenin equivalent (source a_n, resistance R_n). The derivation is in the companion paper,
K. J. Werner, J. O. Smith III, J. S. Abel, "Wave Digital Filter Adaptors for Arbitrary Topologies and Multiport
Linear Elements", DAFx-15 (https://dafx.de/paper-archive/2015/DAFx-15_submission_53.pdf). rtype_scattering() uses
the same X with each port's internal node and source current eliminated, which leaves the nodal matrix
Y = inc G inc^T of the adaptor's own nodes: S = 2 inc^T Y^-1 inc G - I (tests/test_adaptors.py checks it against
eq. (20) built element by element). The adaptor's incident wave at port n is the subtree's B, its reflected wave the
subtree's A (a = v + R i_in, i_in = current into the adaptor = -i).
The nonlinear ports cannot be adapted, which leaves a delay-free loop through S11. Werner et al. resolve it with
the K-method: the w-K converter C of their eq. (18) and S give, by their eq. (14)-(16),
    v_C = E a_E + F i_C,     b_E = M a_E + N i_C,     p = E a_E,
so the root solves v_C = p + F i_C(v_C) (a Kirchhoff-domain equation in Vgk, Vak) and scatters b_E back down.
E, F, M, N do not depend on the arbitrary internal port resistances R_I (tests/test_adaptors.py). The DK solver
(dk/solver.py) solves F(v) = p - K i(v) - v = 0, i.e. the same equation with K = -F. Newton's method at a grouped WDF
root is the approach of M. J. Olsen, K. J. Werner, J. O. Smith III, "Resolving Grouped Nonlinearities in Wave Digital
Filters Using Iterative Techniques", DAFx-16 (https://dafx.de/paper-archive/2016/dafxpapers/39-DAFx-16_paper_14-PN.pdf).
"""
import numpy as np


class Resistor:
    def __init__(self, R):
        self.R, self.A, self.B = float(R), 0.0, 0.0

    def adapt(self):
        return self.R

    def up(self):
        return 0.0

    def down(self, A):
        self.A = A


class ResistiveSource:
    """Ideal voltage source e in series with R (v = e + R i). e may be changed between samples."""

    def __init__(self, R, e=0.0):
        self.R, self.e, self.A, self.B = float(R), float(e), 0.0, 0.0

    def adapt(self):
        return self.R

    def up(self):
        self.B = self.e
        return self.e

    def down(self, A):
        self.A = A


class Capacitor:
    """Trapezoidal capacitor with companion conductance g (port resistance 1/g); its state is next sample's B."""

    def __init__(self, g, state=0.0):
        self.R, self.s, self.A, self.B = 1.0 / float(g), float(state), 0.0, 0.0

    def adapt(self):
        return self.R

    def up(self):
        self.B = self.s
        return self.s

    def down(self, A):
        self.A = self.s = A


class Series:
    def __init__(self, c1, c2):
        self.c1, self.c2, self.A, self.B = c1, c2, 0.0, 0.0
        self.adapt()

    def adapt(self):
        """Port resistances from the leaves up (call after a leaf's R changed)."""
        r1, r2 = self.c1.adapt(), self.c2.adapt()
        self.R = r1 + r2
        self.k1, self.k2 = r1 / self.R, r2 / self.R
        return self.R

    def up(self):
        self.b1, self.b2 = self.c1.up(), self.c2.up()
        self.B = self.b1 + self.b2
        return self.B

    def down(self, A):
        self.A = A
        d = A - self.B
        self.c1.down(self.b1 + self.k1 * d)
        self.c2.down(self.b2 + self.k2 * d)


class Parallel:
    def __init__(self, c1, c2):
        self.c1, self.c2, self.A, self.B = c1, c2, 0.0, 0.0
        self.adapt()

    def adapt(self):
        r1, r2 = self.c1.adapt(), self.c2.adapt()
        self.R = r1 * r2 / (r1 + r2)
        self.g1, self.g2 = self.R / r1, self.R / r2          # G_k / G
        return self.R

    def up(self):
        self.b1, self.b2 = self.c1.up(), self.c2.up()
        self.B = self.g1 * self.b1 + self.g2 * self.b2
        return self.B

    def down(self, A):
        self.A = A
        t = A + self.B
        self.c1.down(t - self.b1)
        self.c2.down(t - self.b2)


def voltage(port):
    """Port voltage after a full up / down pass."""
    return 0.5 * (port.A + port.B)


def incidence(ports, nodes):
    """(len(nodes), len(ports)) incidence of an adaptor: +1 at each port's + node, -1 at its - node (None = ground)."""
    inc = np.zeros((len(nodes), len(ports)))
    for n, (p, q) in enumerate(ports):
        if p is not None:
            inc[nodes.index(p), n] = 1.0
        if q is not None:
            inc[nodes.index(q), n] = -1.0
    return inc


def rtype_scattering(inc, R):
    """Scattering matrix S of an R-type adaptor with port incidence inc (non-ground nodes x ports) and port
    resistances R: Werner et al. eq. (20) with the ports' internal nodes and source currents eliminated (module
    docstring)."""
    G = np.diag(1.0 / np.asarray(R, float))
    Y = inc @ G @ inc.T
    return 2.0 * inc.T @ np.linalg.solve(Y, inc @ G) - np.eye(inc.shape[1])


def kmethod_root(S, R_I):
    """E, F, M, N of Werner et al. eq. (14) for an adaptor whose first len(R_I) ports face the nonlinearity:
    v_C = E a_E + F i_C, b_E = M a_E + N i_C, with the w-K converter of their eq. (18),
    C11 = -R_I, C12 = I, C21 = -2 R_I, C22 = I, and H = (I - C22 S11)^-1."""
    nI = len(R_I)
    RI = np.diag(np.asarray(R_I, float))
    S11, S12, S21, S22 = S[:nI, :nI], S[:nI, nI:], S[nI:, :nI], S[nI:, nI:]
    C11, C12, C21, C22 = -RI, np.eye(nI), -2.0 * RI, np.eye(nI)
    H = np.linalg.inv(np.eye(nI) - C22 @ S11)
    E = C12 @ (np.eye(nI) + S11 @ H @ C22) @ S12
    F = C12 @ S11 @ H @ C21 + C11
    M = S21 @ H @ C22 @ S12 + S22
    N = S21 @ H @ C21
    return E, F, M, N
