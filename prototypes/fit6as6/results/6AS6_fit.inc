* 6AS6 dual-control pentode, behavioural model fitted to the Raytheon 5725/6AS6W datasheet
* curves (15 Dec 1960, https://frank.pocnet.net/sheets/138/5/5725.pdf, pages 8-11, incl. the p.8 bottom
* plate family at Ec3 = -1..-14 V since issue #51), log-scale error.
* Model: Koren E1 total cathode current, split plate/screen by a g3- and plate-dependent share.
* Empirical extension, NOT a published model. Fitted for Ec2 60-140 V, Ec1 -5..0 V, Ec3 -14..+12 V
* (Ec3 < -9 V only at Ec2 = 120 V, Ec1 = -2 V), Eb 20-435 V, currents down to ~0.1 mA. Cathode current
* is independent of g3, so screen current is over-predicted by up to ~40 % at deep g3 cut-off
* (Ec3 < -6 V), and gm(g3) is too low for Ec3 > 0 (README). No g1/g3 grid current, no heater;
* capacitances are GE datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
Bik ik 0 V=2*pwr(max((max(V(G2,K),1m)/149.548)*ln(1+exp(149.548*(1/30.1428+V(G1,K)/max(V(G2,K),1m)))),0),1.49418)/697.642
Rik ik 0 1G
Bshare sh 0 V=0.710652*0.63661977*atan(max(V(P,K),0)/5.23137)*exp(-2.30242*ln(1+exp(-(V(G3,K)+max(V(P,K),0)/43.2167-(-3.03062)-53.9338*V(ik))/2.0747)))
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
