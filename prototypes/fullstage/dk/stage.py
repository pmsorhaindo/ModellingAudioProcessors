"""One 12AX7 common-cathode stage, nodal DK method, with Drive / Bias / Output and 8x oversampling (default).

Parameters (all our choices, documented here):
  drive   : linear gain applied to the input voltage before the stage (1.0 = signal as given, in volts).
  bias    : scale factor on the cathode resistor Rk (1.0 = 1.5 kOhm). Larger -> colder bias (more
            negative Vgk). Changing it rebuilds the matrices and re-solves the operating point, so it
            is a "set before processing" control in this prototype (no smoothing yet).
  output  : linear gain applied to the stage output.
Oversampling: default 8x (parameter `os`), linear-phase FIR (see OVERSAMPLING_FILTER); the stage runs at
os*fs. 8x chosen by Michael (4 Nov 2026) to bring the trapezoidal frequency-warping error at 20 kHz under
the 0.1 dB target (0.21 dB at 4x, ~0.05 dB at 8x; error ~1/os^2), at ~2x the CPU of 4x.
"""
import numpy as np
from scipy.signal import firwin, upfirdn
from . import circuit
from .solver import solve
from .triode import currents, DZ_12AX7_RSD1

OS = 8


def ntaps(os=OS):
    """64*os - 1 taps (255 at 4x, 511 at 8x): keeps the transition band the same width in Hz at any factor."""
    return 64 * os - 1


OVERSAMPLING_FILTER = ("firwin, 64*os-1 taps (511 at 8x), Kaiser beta 8, cutoff 22 kHz at 48 kHz base (scaled to fs), "
                       "used for both interpolation (gain os) and decimation; zero-latency by group-delay trim")


def os_filter(fs_base, os=OS):
    return firwin(ntaps(os), 22000.0 * fs_base / 48000.0, window=("kaiser", 8.0), fs=os * fs_base)


class Stage:
    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, triode=DZ_12AX7_RSD1, oversample=True, os=OS):
        self.fs_base = fs
        self.os = int(os) if oversample else 1
        self.fs = fs * self.os
        self.drive, self.output, self.bias, self.par = drive, output, bias, triode
        self.m = circuit.build(self.fs, rk_scale=bias)
        self.K = self.m["K"].tolist()
        self.dc = self.operating_point()
        self.reset()

    # ---- DC operating point: capacitors open, same NR solver, started from cutoff-ish guess
    def operating_point(self):
        m0 = circuit.build(self.fs, rk_scale=self.bias, dc=True)
        u = np.array([0.0, circuit.VALUES["HT"]])
        p = m0["H"] @ u
        K0 = m0["K"].tolist()
        v0, v1 = -1.0, 200.0
        # source-stepping on HT for robustness: ramp the supply in 10 steps
        for s in np.linspace(0.1, 1.0, 10):
            v0, v1, ig, ia, it, ok = solve(p[0] * s, p[1] * s, K0, v0, v1, self.par)
            assert ok, "DC operating point did not converge"
        inl = np.array([ig, ia])
        w = m0["Si"] @ (m0["Nup"].T @ u - m0["Nnp"].T @ inl)
        nodes = dict(zip(circuit.NODES, w[:len(circuit.NODES)]))
        return dict(vgk=v0, vak=v1, ig=ig, ia=ia, nodes=nodes, w=w)

    def reset(self):
        m = self.m
        vn = self.dc["w"][:len(circuit.NODES)]
        self.x = m["Gx"] @ m["Nx"] @ vn          # capacitor history so that every i_c = 0 at t=0
        self.v = [self.dc["vgk"], self.dc["vak"]]
        self.iters = []
        self.nonconv = 0

    def process_internal(self, vin, record=("out",)):
        """Run the stage at the internal rate on an input already at self.fs. Returns dict of signals."""
        m = self.m
        A, B, C, D, E, F, G, H = (m[k] for k in "ABCDEFGH")
        HT = circuit.VALUES["HT"]
        rec = {n: np.empty(len(vin)) for n in record}
        idx = {n: m["out_names"].index(n) for n in record if n in m["out_names"]}
        x, (v0, v1), K, par = self.x, self.v, self.K, self.par
        iters = np.empty(len(vin), dtype=np.int16)
        Gu, Hu = G, H
        for n, s in enumerate(vin):
            u = np.array([s, HT])
            p = Gu @ x + Hu @ u
            v0, v1, ig, ia, it, ok = solve(p[0], p[1], K, v0, v1, par)
            iters[n] = it
            if not ok:
                self.nonconv += 1
            inl = np.array([ig, ia])
            y = D @ x + E @ u + F @ inl
            for nm, j in idx.items():
                rec[nm][n] = y[j]
            if "vgk" in rec:
                rec["vgk"][n] = v0
            x = A @ x + B @ u + C @ inl
        self.x, self.v = x, [v0, v1]
        self.iters.append(iters)
        return rec

    def process(self, vin, record=("out",)):
        """Base-rate in, base-rate out (with self.os-times oversampling around the stage)."""
        vin = self.drive * np.asarray(vin, dtype=float)
        if self.os == 1:
            rec = self.process_internal(vin, record)
            return {k: self.output * v if k == "out" else v for k, v in rec.items()}
        h = os_filter(self.fs_base, self.os)
        dly = (len(h) - 1) // 2
        up = upfirdn(h * self.os, vin, up=self.os)[dly:dly + len(vin) * self.os]
        rec = self.process_internal(up, record)
        out = {}
        for k, v in rec.items():
            # remove the DC before filtering so the FIR edge does not see a step, add it back after
            dc = v[0]
            y = upfirdn(h, v - dc, down=1)[dly:dly + len(v)][:: self.os] + dc
            out[k] = self.output * y if k == "out" else y
        out["_internal"] = rec
        return out

    def iteration_stats(self):
        it = np.concatenate(self.iters) if self.iters else np.zeros(0, int)
        hist = np.bincount(it) if len(it) else np.zeros(0)
        return dict(samples=int(len(it)), mean=float(it.mean()) if len(it) else 0.0,
                    max=int(it.max()) if len(it) else 0, nonconverged=int(self.nonconv),
                    histogram={int(i): int(c) for i, c in enumerate(hist) if c})
