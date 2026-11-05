* 6AS6 dual-control pentode, behavioural model fitted to the Raytheon 5725/6AS6W datasheet
* curves (15 Dec 1960, https://frank.pocnet.net/sheets/138/5/5725.pdf, pages 8-11), log-scale error.
* Model: Koren E1 total cathode current, split plate/screen by a g3- and plate-dependent share.
* Empirical extension, NOT a published model. Fitted for Ec2 60-140 V, Ec1 -5..0 V, Ec3 -9..+12 V,
* Eb 20-400 V, currents down to ~0.1 mA. Cathode current is independent of g3, so screen current
* is over-predicted by up to ~25 % at deep g3 cut-off (Ec3 < -6 V; README). No g1/g3 grid current,
* no heater; capacitances are GE datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
Bik ik 0 V=2*pwr(max((max(V(G2,K),1m)/151.285)*ln(1+exp(151.285*(1/29.9151+V(G1,K)/max(V(G2,K),1m)))),0),1.50888)/716.014
Rik ik 0 1G
Bshare sh 0 V=0.821524*0.63661977*atan(max(V(P,K),0)/17.9204)*exp(-3.19834*ln(1+exp(-(V(G3,K)+max(V(P,K),0)/225.356-(-6.52266)-63.7291*V(ik))/2.47478)))
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
