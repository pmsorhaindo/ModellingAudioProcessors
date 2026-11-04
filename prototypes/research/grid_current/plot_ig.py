"""Ig vs Vgk for source-given grid-current models (12AX7). Only published parameters used."""
import numpy as np, matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
v = np.linspace(-2, 3, 1001)
def dz(v, Gg, xi, Cg, Ig0):  # Dempwolf & Zolzer DAFx-11 eq.(11), Table 1
    return Gg*(np.logaddexp(0, Cg*v)/Cg)**xi + Ig0
def ch(v, Vg, Rgk, Kn):      # Cohen & Helie AES 2010 eq.(2)-(3), Table 3
    a = 1/(4*Kn*Rgk); b = (Kn-Vg)/(2*Kn*Rgk); c = -a*(Vg-Kn)**2 - b*(Vg-Kn)
    return np.where(v < Vg-Kn, 0, np.where(v > Vg+Kn, (v-Vg)/Rgk, a*v*v+b*v+c))
def koren(v, Rgi=2000.0, Is=1e-9, n=1.0, Vt=0.02585):  # Koren subckt: diode(IS=1n)+RGI 2k in series, solved
    i = np.zeros_like(v)
    for k, vk in enumerate(v):
        lo, hi = 0.0, max(vk, 0)/Rgi + 1e-6
        for _ in range(200):
            m = (lo+hi)/2; f = Is*np.expm1((vk-m*Rgi)/(n*Vt)) - m
            lo, hi = (m, hi) if f > 0 else (lo, m)
        i[k] = (lo+hi)/2
    return i
plt.figure(figsize=(7,4.5))
for name, p in [("RSD-1",(6.177e-4,1.314,9.901,8.025e-8)),("RSD-2",(5.911e-4,1.358,11.76,4.527e-8)),("EHX-1",(3.263e-4,1.156,11.99,3.917e-8))]:
    plt.plot(v, dz(v,*p)*1e3, label=f"Dempwolf-Zolzer {name}")
for name, p in [("new 1",(0.35,1300,0.5)),("new 2",(0.18,1280,0.49)),("aged 3",(0.33,1350,0.55))]:
    plt.plot(v, ch(v,*p)*1e3, "--", label=f"Cohen-Helie {name}")
plt.plot(v, koren(v)*1e3, "k:", label="Koren diode IS=1n + RGI=2k")
plt.xlabel("Vgk (V)"); plt.ylabel("Ig (mA)"); plt.grid(alpha=.3); plt.legend(fontsize=7)
plt.title("12AX7 grid-current models (published parameters only)")
plt.tight_layout(); plt.savefig(__file__.replace("plot_ig.py","ig_vs_vgk.png"), dpi=120)
for x in (-1,-0.5,0,0.5,1,2): i=np.argmin(abs(v-x)); print(x, dz(v,6.177e-4,1.314,9.901,8.025e-8)[i]*1e3, ch(v,0.35,1300,0.5)[i]*1e3, koren(v)[i]*1e3)
