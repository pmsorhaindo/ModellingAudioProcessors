"""One 12AX7 common-cathode stage as a wave digital filter, with the triode as the multiport nonlinearity at the root.

Same circuit as the DK stage: the component values are dk/circuit.VALUES (example values, not Culture Vulture values;
dk/circuit.py says where each comes from), the topology is dk/circuit.netlist(), the valve is the Dempwolf-Zoelzer
12AX7 with grid current (dk/triode.py, RSD-1 parameters), the root equation is solved by the DK stage's own damped
Newton (dk/solver.py: pnjlim, line search, tolerances, best-iterate fallback), and the quality settings, resamplers,
Drive / Bias / Output and warm start are the DK stage's (dk/stage.py, dk/resample.py).

WDF tree (adaptors.py). The R-type adaptor joins the nodes g, k, a and ground; its adapted ports are
  [g, 0]  grid     Series(Rgs, Parallel(Rg, Series(Cin, source(vin, Rs))))
  [k, 0]  cathode  Parallel(Rk, Ck)
  [a, 0]  plate    Parallel(source(HT, Ra), Series(Cout, Rl))
  [g, k] CCG, [g, a] CGP, [a, k] CCP   the valve capacitances, capacitor leaves on the R-type adaptor
and the two unadapted ports above it are the triode's [g, k] (Vgk, Ig) and [a, k] (Vak, Ia). With the valve
capacitances, every pair of those four nodes is connected (a bridge), which no series / parallel tree can represent;
hence the R-type adaptor of Werner et al. (DAFx-15), its K-method root, and Newton at the root (Olsen et al.,
DAFx-16). Every capacitor is the trapezoidal (bilinear) one-port with the DK stage's companion conductance
(circuit.companion_g), so this is the same discrete-time system as the DK stage; the two differ in how it is
organised and computed (state = capacitor waves, per-sample tree passes, local adaptor updates).

State. The WDF runs on absolute waves (HT = 300 V and the bias currents flow through the tree). The operating point
is the fixed point of the WDF's own sample map at zero input (operating_point()), and reset() starts the capacitors
there, so the stage starts at rest. Bias (Rk scale) is a leaf change: swap_bias() re-adapts the tree (the cathode
adaptor and the R-type root) and keeps the capacitor waves, which are the circuit state; it needs no DC solve.

dk.validate.run uses this class in place of dk.stage.Stage (wdf/validate.py). For its small-signal criterion the
WDF's sample map is probed into the DK form (`m`: A..K, the matrices dk/linear.py linearises); the probe is exact
because everything below the root is linear. Processing never uses `m`.
"""
import warnings
import numpy as np
from dk import circuit
from dk.solver import solve, MAX_IT, TOL_V, TOL_F
from dk.stage import QUALITY, OperatingPointError, NonConvergenceWarning
from dk.triode import DZ_12AX7_RSD1, EVAL
from dk.resample import Resampler
from .adaptors import (Resistor, ResistiveSource, Capacitor, Series, Parallel, voltage, incidence, rtype_scattering,
                       kmethod_root)

RTYPE_NODES = ["g", "k", "a"]
ROOT_PORTS = [("g", "k"), ("a", "k")]                    # triode: Vgk / Ig, Vak / Ia (dk/circuit.netlist NL)
EXT_PORTS = [("g", None), ("k", None), ("a", None), ("g", "k"), ("g", "a"), ("a", "k")]
R_INTERNAL = (1e3, 1e3)        # ohm, the unadapted root ports: arbitrary (E, F, M, N do not depend on them)
CAPS = ["Cin", "Ck", "Cout", "CCG", "CGP", "CCP"]        # state order (the DK stage's capacitor order)
OUT_NAMES = ["out", "g", "k", "a", "gl", "in1"]          # "out" first, as dk/linear.py expects


class WdfStage:
    def __init__(self, fs, bias=1.0, drive=1.0, output=1.0, triode=DZ_12AX7_RSD1, quality="standard", filt="fir",
                 newton=None, triode_math="libm", r_internal=R_INTERNAL):
        nw = dict(max_it=MAX_IT, tol_v=TOL_V, tol_f=TOL_F)
        if set(newton or {}) - set(nw):
            raise TypeError(f"unknown newton option(s) {sorted(set(newton) - set(nw))}, expected {sorted(nw)}")
        nw.update(newton or {})
        self.newton = (nw["max_it"], nw["tol_v"], nw["tol_f"])
        if triode_math not in EVAL:
            raise ValueError(f"triode_math must be one of {sorted(EVAL)}, got {triode_math!r}")
        self.triode_math, self.ev = triode_math, EVAL[triode_math]
        self.fs_base = fs
        self.os, self.prewarp_hz = QUALITY[quality] if isinstance(quality, str) else quality
        self.rs = Resampler(fs, self.os, filt)
        self.fs = fs * self.os
        self.drive, self.output, self.bias, self.par = drive, output, bias, triode
        self.r_internal = np.asarray(r_internal, float)
        self._build_tree()
        self.adapt()
        self.reset()

    # ---- tree and adaptation
    def _build_tree(self):
        V = circuit.VALUES

        def cap(name):
            return Capacitor(circuit.companion_g(V[name], self.fs, self.prewarp_hz))
        self.caps = {n: cap(n) for n in CAPS}
        c = self.caps
        self.src = ResistiveSource(V["Rs"])
        self.rk = Resistor(V["Rk"] * self.bias)
        self.rl = Resistor(V["Rl"])
        self.ht = ResistiveSource(V["Ra"], V["HT"])
        self.gl = Parallel(Resistor(V["Rg"]), Series(c["Cin"], self.src))
        self.grid = Series(Resistor(V["Rgs"]), self.gl)
        self.cathode = Parallel(self.rk, c["Ck"])
        self.plate = Parallel(self.ht, Series(c["Cout"], self.rl))
        self.ports = [self.grid, self.cathode, self.plate, c["CCG"], c["CGP"], c["CCP"]]
        self.inc = incidence(ROOT_PORTS + EXT_PORTS, RTYPE_NODES)

    def adapt(self):
        """Port resistances from the leaves up, then the R-type scattering and the K-method root matrices."""
        R_E = [p.adapt() for p in self.ports]
        self.S = rtype_scattering(self.inc, np.r_[self.r_internal, R_E])
        self.E, F, self.M, self.N = kmethod_root(self.S, self.r_internal)
        self.F = F
        self.K = (-F).tolist()                     # the DK solver's v = p - K i
        self._m = self._dc = None                  # analysis views of the old adaptation

    def swap_bias(self, bias):
        """Live bias change: the new Rk on the cathode leaf, then adapt(). The capacitor waves are kept as they are
        (they are the circuit state; no capacitor's port resistance depends on Rk), so the circuit moves to its new
        operating point by itself, as the DK stage's swap_bias does with its re-expressed history. No DC solve."""
        self.bias = bias
        self.rk.R = circuit.VALUES["Rk"] * bias
        self.adapt()

    # ---- the sample map
    def _pass(self, vin):
        """The up pass for one sample: the waves a_E arriving at the R-type adaptor and the root input p = E a_E."""
        self.src.e = vin
        aE = np.array([p.up() for p in self.ports])
        p = self.E @ aE
        return aE, p

    def _scatter(self, aE, ig, ia):
        bE = (self.M @ aE + self.N[:, 0] * ig + self.N[:, 1] * ia).tolist()
        for port, b in zip(self.ports, bE):
            port.down(b)

    def _outputs(self):
        return [voltage(self.rl), voltage(self.grid), voltage(self.cathode), voltage(self.plate), voltage(self.gl),
                voltage(self.src)]

    def states(self):
        return np.array([self.caps[n].s for n in CAPS])

    def set_states(self, x):
        for n, v in zip(CAPS, x):
            self.caps[n].s = float(v)

    @property
    def m(self):
        """The WDF's sample map in the DK form of dk/circuit.build (x = capacitor waves in CAPS order, u = [vin, HT],
        i = [Ig, Ia]):  v = G x + H u - K i,  x' = A x + B u + C i,  y = D x + E u + F i  (y: OUT_NAMES).
        Probed with unit vectors through _pass / _scatter; exact, since the tree below the root is linear."""
        if self._m is None:
            saved, ht = self.states(), self.ht.e
            nx = len(CAPS)
            cols = []
            for z in np.eye(nx + 4):
                self.set_states(z[:nx])
                self.ht.e = z[nx + 1]
                aE, p = self._pass(z[nx])
                self._scatter(aE, z[nx + 2], z[nx + 3])
                cols.append(np.r_[p, self.states(), self._outputs()])
            self.set_states(saved)
            self.ht.e = ht
            P = np.array(cols).T
            pv, px, py = P[:2], P[2:2 + nx], P[2 + nx:]
            u, i = slice(nx, nx + 2), slice(nx + 2, nx + 4)
            self._m = dict(G=pv[:, :nx], H=pv[:, u], K=-self.F, A=px[:, :nx], B=px[:, u], C=px[:, i],
                           D=py[:, :nx], E=py[:, u], F=py[:, i], fs=self.fs, out_names=list(OUT_NAMES))
        return self._m

    # ---- DC operating point
    @property
    def dc(self):
        if self._dc is None:
            self._dc = self.operating_point()
        return self._dc

    def operating_point(self):
        """The fixed point of the WDF's sample map at vin = 0: x = A x + B u + C i (every capacitor's wave stays
        put, i.e. it carries no current), with v = G x + H u - K i and i = i(v). Eliminating x leaves the same
        2-port problem as the DK stage's DC solve, v = p_dc - K_dc i(v), solved by the same Newton with the same
        10-step source stepping on HT. Raises OperatingPointError if it does not converge."""
        m = self.m
        u = np.array([0.0, circuit.VALUES["HT"]])
        IA = np.eye(len(CAPS)) - m["A"]
        xu, xi = np.linalg.solve(IA, m["B"] @ u), np.linalg.solve(IA, m["C"])
        p = m["G"] @ xu + m["H"] @ u
        K0 = (m["K"] - m["G"] @ xi).tolist()
        v0, v1 = -1.0, 200.0
        for k, s in enumerate(np.linspace(0.1, 1.0, 10), 1):
            v0, v1, ig, ia, it, ok = solve(p[0] * s, p[1] * s, K0, v0, v1, self.par, *self.newton, ev=self.ev)
            if not ok:
                raise OperatingPointError(
                    f"DC operating point did not converge at source step {k}/10 (HT x {s:.1f}): "
                    f"{it} Newton iterations (max_it, tol_v, tol_f = {self.newton}), "
                    f"best iterate Vgk={v0:.6g} V, Vak={v1:.6g} V, bias={self.bias}")
        inl = np.array([ig, ia])
        x = xu + xi @ inl
        y = m["D"] @ x + m["E"] @ u + m["F"] @ inl
        nodes = dict(zip(OUT_NAMES, y.tolist()), src=0.0, ht=float(u[1]))
        return dict(vgk=v0, vak=v1, ig=ig, ia=ia, nodes={n: nodes[n] for n in circuit.NODES}, x=x)

    def reset(self):
        dc = self.dc
        self.set_states(dc["x"])
        self.ht.e = circuit.VALUES["HT"]
        self.v = [dc["vgk"], dc["vak"]]
        self.v_prev = list(self.v)
        self.iters = []
        self.nonconv = 0
        self.nonconv_block = 0
        self.rs.reset()

    # ---- processing (the interface of dk.stage.Stage)
    @property
    def latency_samples(self):
        return self.rs.latency_samples

    @property
    def resampler_macs(self):
        return self.rs.macs()

    def process_internal(self, vin, record=("out",)):
        """Run the stage at the internal rate on an input already at self.fs. Non-convergence is handled as in
        dk.stage.Stage (best finite iterate kept, counted, one NonConvergenceWarning per call)."""
        ports, src, rl = self.ports, self.src, self.rl
        E, M, N0, N1 = self.E, self.M, self.N[:, 0], self.N[:, 1]
        K, par, nw, ev = self.K, self.par, self.newton, self.ev
        rec = {n: np.empty(len(vin)) for n in record}
        rec_out, rec_vgk = rec.get("out"), rec.get("vgk")
        others = [n for n in record if n not in ("out", "vgk")]
        (v0, v1), (q0, q1) = self.v, self.v_prev
        iters = np.empty(len(vin), dtype=np.int16)
        bad = 0
        for n, s in enumerate(vin.tolist() if isinstance(vin, np.ndarray) else vin):
            src.e = s
            aE = np.array([pt.up() for pt in ports])
            p0, p1 = (E @ aE).tolist()
            e0, e1 = 2.0 * v0 - q0, 2.0 * v1 - q1          # linear-extrapolation warm start, as the DK stage (#15)
            q0, q1 = v0, v1
            v0, v1, ig, ia, it, ok = solve(p0, p1, K, e0, e1, par, *nw, ev=ev)
            iters[n] = it
            if not ok:
                bad += 1
            for pt, b in zip(ports, (M @ aE + N0 * ig + N1 * ia).tolist()):
                pt.down(b)
            if rec_out is not None:
                rec_out[n] = 0.5 * rl.A                     # v(out) = v(Rl); a resistor reflects B = 0
            if rec_vgk is not None:
                rec_vgk[n] = v0
            if others:
                y = dict(zip(OUT_NAMES, self._outputs()))
                for nm in others:
                    rec[nm][n] = y[nm]
        self.v, self.v_prev = [v0, v1], [q0, q1]
        self.iters.append(iters)
        self.nonconv += bad
        self.nonconv_block = bad
        if bad:
            warnings.warn(f"Newton did not converge on {bad} of {len(vin)} samples at {self.fs:g} Hz "
                          f"(max_it, tol_v, tol_f = {self.newton}); kept the best finite iterate",
                          NonConvergenceWarning, stacklevel=2)
        return rec

    def process(self, vin, record=("out",)):
        """Base-rate in, base-rate out, oversampled by self.os around the stage; stateful, block by block; the output
        lags the input by self.latency_samples (the resampler's)."""
        vin = self.drive * np.asarray(vin, dtype=float)
        rec = self.process_internal(self.rs.up(vin), record)
        out = {}
        for k, v in rec.items():
            y = self.rs.down(v, channel=k)
            out[k] = self.output * y if k == "out" else y
        out["_internal"] = rec
        return out

    def iteration_stats(self):
        it = np.concatenate(self.iters) if self.iters else np.zeros(0, int)
        hist = np.bincount(it) if len(it) else np.zeros(0)
        return dict(samples=int(len(it)), mean=float(it.mean()) if len(it) else 0.0,
                    max=int(it.max()) if len(it) else 0, nonconverged=int(self.nonconv),
                    converged=self.nonconv == 0,
                    histogram={int(i): int(c) for i, c in enumerate(hist) if c})
