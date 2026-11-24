"""Experimental state-space ADAA for the 6AS6 (#136).

Holters, Applied Sciences 10(1), 20 (2020), doi:10.3390/app10010020,
sections 2–3: integrate the implicit solve along a line in the vector p, then
apply the same filter to the linear and nonlinear parts of both state and
output. ADAA2 uses the triangular kernel of Parker, Zavalishin and Le Bivic,
DAFx-16, §3 (its second-antiderivative form).

Evaluate the integral by Gauss–Legendre quadrature in normalized time,
including for very short or zero segments. Each node uses the DK current map.
"""
from functools import lru_cache
import warnings

import numpy as np

from . import circuit, linear
from .solver import solve, solve_grid_restarts
from .stage import Stage as DKStage, NonConvergenceWarning, OutOfLawWarning

# Integral of the piecewise-linear reconstruction with each kernel, for a
# linear map. The same coefficients apply to the input and each state entry.
TAPS = {1: (0.5, 0.5), 2: (1 / 6, 2 / 3, 1 / 6)}


@lru_cache(maxsize=None)
def quadrature(nodes):
    t, w = np.polynomial.legendre.leggauss(nodes)
    return (t + 1.0) / 2.0, w / 2.0


class Stage(DKStage):
    """Research stage at the host rate and oversampling factor of DKStage.

    ADAA1 uses a box, with 0.5 internal sample of output delay. ADAA2
    uses a triangle, with one sample of output delay. The feedback delay is
    one sample longer than the output delay. The circuit design rate is
    fs/(1 + adaa/2), to recover the low-frequency timing.
    """

    def __init__(self, fs, *, adaa=1, nodes=4, **settings):
        if adaa not in (1, 2) or not isinstance(nodes, int) or nodes < 2:
            raise ValueError("expected ADAA order 1 or 2 and at least two quadrature nodes")
        self.adaa, self.nodes = adaa, nodes
        super().__init__(fs, **settings)
        self.m = circuit.build(self.fs / (1 + adaa / 2), self.rk, prewarp_hz=self.prewarp_hz,
                               mode=self.mode, grid=self.grid is not None)
        self.K = self.m["K"].tolist()
        self.reset()

    @property
    def adaa_delay_samples(self):
        """Extra nominal delay at the host rate, in addition to latency_samples."""
        return self.adaa / (2 * self.os)

    def reset(self):
        super().reset()
        self.previous_p = self.p_dc.copy()
        self.previous_x = self.x.copy()
        self.older_x = self.x.copy()
        self.previous_input = self.older_input = 0.0
        self.previous_half = np.zeros(self.n_nl)

    def process_internal(self, vin, record=("out",)):
        m = self.m
        rec = {name: np.empty(len(vin)) for name in record}
        idx = {name: m["out_names"].index(name) for name in record if name in m["out_names"]}
        voltage_idx = {name: j for j, name in enumerate(("vgk", "vg2k", None, "vak")) if name in rec}
        iters = np.zeros(len(vin), dtype=np.int32)
        nodes, weights = quadrature(self.nodes)
        x, prev_x, prev_p = self.x, self.previous_x, self.previous_p
        prev_u, prev_half = self.previous_input, self.previous_half
        older_x, older_u = self.older_x, self.older_input
        i, q = self.i, self.i_prev
        dc = self.i_dc.tolist()
        B, E, H = (m[k][:, 0] for k in ("B", "E", "H"))
        K, par, nw, grid = self.K, self.par, self.newton, self.grid
        bad = law = restarts = 0
        for n, u in enumerate(vin):
            p = m["G"] @ x + H * u + self.p_dc
            dp = p - prev_p
            current = np.zeros(self.n_nl)
            next_half = np.zeros(self.n_nl)
            for t, w in zip(nodes, weights):
                arg = (prev_p + t * dp).tolist()
                e = [2.0 * a - b for a, b in zip(i, q)]
                q = i
                if grid is None:
                    v, a, b, it, ok = solve(arg, K, *e, par, *nw)
                    i = [a, b]
                else:
                    v, i, it, ok, _, attempts = solve_grid_restarts(arg, K, (e, q, dc), par, grid, *nw)
                    restarts += attempts > 1
                iters[n] += it
                bad += not ok
                law += (v[0] > 0.0 if grid is None else i[0] + i[1] < 0.0) or v[3] < 0.0
                di = np.asarray(i) - self.i_dc
                if self.adaa == 1:
                    current += w * di
                else:
                    # Current segment contributes the falling half of the
                    # causal triangular kernel, previous segment the rising.
                    current += w * (1.0 - t) * di
                    next_half += w * t * di
            if self.adaa == 1:
                x_f, u_f = (x + prev_x) * 0.5, (u + prev_u) * 0.5
            else:
                x_f, u_f = (x + 4 * prev_x + older_x) / 6, (u + 4 * prev_u + older_u) / 6
                current += prev_half
                prev_half = next_half
                older_x, older_u = prev_x, prev_u
            y = m["D"] @ x_f + E * u_f + m["F"] @ current + self.y_dc
            for name, j in idx.items():
                rec[name][n] = y[j]
            if voltage_idx:
                # Voltage is linear in p and i; integrate both with the same kernel.
                v_f = self.p_dc + m["G"] @ x_f + H * u_f - m["K"] @ (current + self.i_dc)
                for name, j in voltage_idx.items():
                    rec[name][n] = v_f[j]
            if "ig1" in rec:
                rec["ig1"][n] = (current[2] + self.i_dc[2]) if grid is not None else 0.0
            if "ipk" in rec:
                rec["ipk"][n] = current[0] + current[1] + self.i_dc[0] + self.i_dc[1]
            x, prev_x = m["A"] @ x_f + B * u_f + m["C"] @ current, x
            prev_p, prev_u = p, u
        self.x, self.previous_x, self.previous_p = x, prev_x, prev_p
        self.previous_input, self.previous_half = prev_u, prev_half
        self.older_input, self.older_x = older_u, older_x
        self.i, self.i_prev = i, q
        self.iters.append(iters)
        self.nonconv_block, self.out_of_law_block = bad, law
        self.nonconv += bad
        self.out_of_law += law
        self.restarted += restarts
        if bad:
            warnings.warn(f"{bad} ADAA quadrature nodes did not converge", NonConvergenceWarning, stacklevel=2)
        if law:
            warnings.warn(f"{law} ADAA quadrature nodes outside the valve law", OutOfLawWarning, stacklevel=2)
        return rec

    def response(self, frequencies):
        """Complex linearized stage response, before host-rate resampling."""
        A, B, C, D = linear.linearised(self)
        z1 = np.exp(-2j * np.pi * np.atleast_1d(frequencies) / self.fs)
        h = np.polynomial.polynomial.polyval(z1, TAPS[self.adaa])
        eye = np.eye(len(A))
        return np.array([(weight * (D + C @ np.linalg.solve(eye - delay * A, delay * B)))[0, 0]
                         for weight, delay in zip(h, z1 * h)])
