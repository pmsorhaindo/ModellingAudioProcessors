* Dempwolf-Zoelzer 12AX7 triode as an ngspice behavioural subcircuit (written by us).
* Equations: K. Dempwolf, U. Zoelzer, "A physically-motivated triode model for circuit simulations",
*   DAFx-11, https://www.dafx.de/paper-archive/2011/Papers/76_e.pdf
*   Ik = G*(ln(1+exp(C*(Vak/mu+Vgk)))/C)^gamma ; Ig = Gg*(ln(1+exp(Cg*Vgk))/Cg)^xi + Ig0 ; Ia = Ik - Ig
* Parameters: Table 1, column RSD-1 (same as ../../dk/triode.py).
* Inter-electrode capacitances: Koren's 12AX7 values (CCG 2.3p, CGP 2.4p, CCP 0.9p), as in the DK model.
* softplus written overflow-safe: sp(x) = max(x,0) + ln(1+exp(-abs(x))).
* Pins: plate grid cathode (same order as Koren's 12AX7).
.SUBCKT DZ12AX7 1 2 3
.param G=2.242e-3 mu=103.2 gam=1.26 C=3.40 Gg=6.177e-4 xi=1.314 Cg=9.901 Ig0=8.025e-8
Bg 2 3 I = Gg*pwr((max(Cg*V(2,3),0)+ln(1+exp(-abs(Cg*V(2,3)))))/Cg, xi) + Ig0
Ba 1 3 I = G*pwr((max(C*(V(1,3)/mu+V(2,3)),0)+ln(1+exp(-abs(C*(V(1,3)/mu+V(2,3))))))/C, gam) - (Gg*pwr((max(Cg*V(2,3),0)+ln(1+exp(-abs(Cg*V(2,3)))))/Cg, xi) + Ig0)
C1 2 3 2.3p
C2 2 1 2.4p
C3 1 3 0.9p
.ENDS DZ12AX7
