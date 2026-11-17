"""Single common-cathode stage: DC operating point + memoryless dynamic transfer.

Topology (all values are ILLUSTRATIVE TEXTBOOK VALUES, not Culture Vulture values):

    B+ --Rp-- plate --Cc(large)-- RL -- gnd        (RL = next-stage grid leak)
    vin --Rs-- grid ;  grid leak to gnd (sets grid DC = 0 V)
    cathode --Rk||Ck(large)-- gnd                    (bypassed cathode resistor)
    pentode screen held at a fixed, stiff Vg2 (ground-referenced)

"Memoryless" assumption: Cc and Ck are large, so over an audio cycle the
cathode stays at its DC value Vk0 and the coupling cap holds Va0. The plate
then obeys KCL with an AC load of Rp || RL:
    (B - Va)/Rp = Ip(Vgk, Vg2k, Vak) + (Va - Va0)/RL
Grid current through Rs is included: vg = vin - Rs * Ig(vg - Vk0).
What this ignores (on purpose, for now): bias shift from grid current charging
Cc/Ck (blocking distortion), Miller capacitance, and supply sag. Those need a
stateful (DK / WDF) model and are listed as next steps.
"""
from dataclasses import dataclass, field
import numpy as np


def solve_monotone(h, lo, hi, x0=None, tol=1e-9, maxit=80):
    """Vectorised safeguarded Newton-Raphson for a *decreasing* function h.

    Keeps a bracket [lo, hi] with h(lo) >= 0 >= h(hi); takes a Newton step
    (numerical derivative) when it lands inside the bracket, bisects otherwise.
    Returns (x, iterations_used).
    """
    lo = np.array(lo, float, copy=True)
    hi = np.array(hi, float, copy=True)
    x = 0.5 * (lo + hi) if x0 is None else np.clip(np.array(x0, float), lo, hi)
    for it in range(1, maxit + 1):
        fx = h(x)
        pos = fx > 0
        lo = np.where(pos, x, lo)
        hi = np.where(pos, hi, x)
        d = 1e-6 * np.maximum(1.0, np.abs(x))
        dfx = (h(x + d) - fx) / d
        with np.errstate(divide="ignore", invalid="ignore"):
            xn = x - fx / dfx
        bad = ~np.isfinite(xn) | (xn <= lo) | (xn >= hi)
        xn = np.where(bad, 0.5 * (lo + hi), xn)
        if np.all(np.abs(xn - x) < tol * np.maximum(1.0, np.abs(x))):
            return xn, it
        x = xn
    return x, maxit


@dataclass
class Stage:
    valve: object
    B: float = 250.0        # supply, V
    Rp: float = 100e3       # plate resistor, ohm
    Rk: float = 1.5e3       # cathode resistor, ohm (bypassed)
    RL: float = 1e6         # AC load (next grid leak), ohm
    Rs: float = 33e3        # source / grid-stopper resistance, ohm
    Vg2: float = 100.0      # pentode screen voltage (ground ref), V
    label: str = ""
    # filled by operating_point()
    Vk0: float = field(default=np.nan, init=False)
    Va0: float = field(default=np.nan, init=False)
    Ia0: float = field(default=np.nan, init=False)

    @property
    def is_pentode(self):
        return getattr(self.valve, "kind", "") == "pentode"

    # --- currents with ground-referenced node voltages -----------------
    def _ip(self, vgk, va, vk):
        if self.is_pentode:
            return self.valve.ip(vgk, self.Vg2 - vk, va - vk)
        return self.valve.ip(vgk, va - vk)

    def _ik(self, vgk, va, vk):
        if self.is_pentode:
            return self.valve.ik(vgk, self.Vg2 - vk, va - vk)
        return self.valve.ik(vgk, va - vk)

    def _plate_dc(self, vk):
        vk = np.asarray(vk, float)
        def h(va):
            return (self.B - va) / self.Rp - self._ip(-vk, va, vk)
        va, _ = solve_monotone(h, vk + 0.0 * vk, self.B + 0.0 * vk)
        return va

    # --- DC operating point (cathode bias, Rk) ----------------------------
    def operating_point(self):
        """Solve Vk = Rk * Ik(Vgk=-Vk, Va) with Va on the DC load line."""
        def g(vk):
            va = self._plate_dc(vk)
            return self.Rk * self._ik(-vk, va, vk) - vk   # decreasing in vk
        vk, _ = solve_monotone(g, np.array(0.0), np.array(min(self.B, 50.0)))
        self.Vk0 = float(vk)
        self.Va0 = float(self._plate_dc(vk))
        self.Ia0 = float(self._ip(-self.Vk0, self.Va0, self.Vk0))
        return self

    def set_bias(self, vgk0):
        """Pick Rk so the quiescent grid-cathode voltage equals vgk0 (<0).
        This is how we turn a 'bias' knob into a physical cathode resistor."""
        vk = -float(vgk0)
        va = float(self._plate_dc(np.array(vk)))
        ik = float(self._ik(-vk, va, vk))
        self.Rk = vk / ik
        self.Vk0, self.Va0 = vk, va
        self.Ia0 = float(self._ip(-vk, va, vk))
        return self

    # --- dynamic (memoryless) transfer ------------------------------------
    def grid(self, vin):
        """Effective Vgk given input vin through Rs with grid current."""
        vin = np.asarray(vin, float)
        if self.Rs == 0:
            return vin - self.Vk0
        ig = self.valve.ig
        def h(vg):
            return vin - self.Rs * ig(vg - self.Vk0) - vg   # decreasing
        vg, _ = solve_monotone(h, np.minimum(vin, 0.0) - 1.0, np.maximum(vin, 0.0) + 1.0, x0=vin)
        return vg - self.Vk0

    def plate(self, vin, return_iters=False):
        vgk = self.grid(vin)
        vk = self.Vk0
        def h(va):
            return (self.B - va) / self.Rp - self._ip(vgk, va, vk) - (va - self.Va0) / self.RL
        lo = np.full_like(vgk, vk)
        hi = np.full_like(vgk, self.B)
        va, it = solve_monotone(h, lo, hi, x0=np.full_like(vgk, self.Va0))
        return (va, it) if return_iters else va

    def transfer(self, vin):
        """Output = AC plate swing (inverting stage), volts."""
        return self.plate(vin) - self.Va0

    def small_signal_gain(self, dv=1e-4):
        y = self.transfer(np.array([-dv, dv]))
        return (y[1] - y[0]) / (2 * dv)

    def cutoff_grid_voltage(self, i_cut=10e-6):
        """Grid-cathode voltage where Ip falls to i_cut with the plate at B+
        (used to normalise 'bias' and 'drive' between different valves)."""
        def h(vg):  # increasing in vg -> negate
            if self.is_pentode:
                return -(self.valve.ip(vg, self.Vg2, self.B) - i_cut)
            return -(self.valve.ip(vg, self.B) - i_cut)
        v, _ = solve_monotone(h, np.array(-100.0), np.array(0.0))
        return float(v)


def _set_bias_va(self, va0):
    """Pick the bias (and hence Rk) that puts the quiescent plate at va0.
    Sweeping va0 from near the knee to near B+ is our stand-in for a 'bias'
    knob: hot bias = low va0, cold bias (toward cutoff) = va0 near B+."""
    ia = (self.B - va0) / self.Rp
    # find vgk0 < 0 with Ip(vgk0, Vak=va0+vgk0) = ia ; Ip increasing in vgk
    def h(vg):
        vk = -vg
        return -(self._ip(vg, va0, vk) - ia)       # decreasing
    lo = -(self.Vg2 - 1.0) if self.is_pentode else -200.0
    vg, _ = solve_monotone(h, np.array(lo), np.array(0.0))
    return self.set_bias(float(vg))


Stage.set_bias_va = _set_bias_va
