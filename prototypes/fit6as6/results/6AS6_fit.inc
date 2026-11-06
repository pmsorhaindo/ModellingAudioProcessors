* 6AS6 dual-control pentode, behavioural model fitted to the Raytheon 5725/6AS6W datasheet
* curves (15 Dec 1960, https://frank.pocnet.net/sheets/138/5/5725.pdf, pages 8-11, incl. the p.8 bottom
* plate family at Ec3 = -1..-14 V since issue #51 and the p.11 top Ic2-vs-Ec3 curves since #55), log-scale error.
* Model: Koren E1 cathode current Ik0 (node ik0), lowered past g3 cut-off by up to KR (node ik; issue #53/#55:
* Ik(Ec3 = -10 V)/Ik(0) = 0.79-0.81, Raytheon p.11 0.75-0.87), split plate/screen by a g3- and
* plate-dependent share with a power-law plate term. Empirical extension, NOT a published model.
* Fitted for Ec2 60-140 V, Ec1 -5..0 V, Ec3 -14..+12 V (Ec3 < -9 V only at Ec2 = 120 V, Ec1 = -2 V),
* Eb 20-435 V, currents down to ~0.1 mA. gm(g3) is too low for Ec3 > +5 V (about -65 %) and too high
* below -6 V (README). No g1/g3 grid current, no heater; capacitances are GE datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
Bik0 ik0 0 V=2*pwr(max((max(V(G2,K),1m)/149.589)*ln(1+exp(149.589*(1/30.1202+V(G1,K)/max(V(G2,K),1m)))),0),1.49046)/696.48
Rik0 ik0 0 1G
Bik ik 0 V=V(ik0)*(1-0.273737/(1+exp((V(G3,K)+(120/33.5969)*(1+(pwr(max(V(P,K),1m)/120,0.574267)-1)/0.574267)-(-4.07704)-46.543*V(ik0))/2.34683)))
Rik ik 0 1G
Bshare sh 0 V=0.707997*0.63661977*atan(max(V(P,K),0)/0.5)*exp(-3.9918*ln(1+exp(-(V(G3,K)+(120/33.5969)*(1+(pwr(max(V(P,K),1m)/120,0.574267)-1)/0.574267)-(-4.07704)-46.543*V(ik0))/2.34683)))
Rsh sh 0 1G
Bp P K I=V(ik)*V(sh)
Bg2 G2 K I=V(ik)*(1-V(sh))
* inter-electrode capacitances from the GE sheet, page 1 (with external shield to pin 2)
Cg1 G1 K 4p
Cg3 G3 K 3.4p
Cpk P K 3p
Cg1p G1 P 0.02p
Cg1g3 G1 G3 0.15p
.ENDS 6AS6_FIT
