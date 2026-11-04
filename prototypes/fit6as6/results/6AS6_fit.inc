* 6AS6 dual-control pentode, behavioural model fitted to GE 6AS6 datasheet curves
* (ET-T706 12-49, https://frank.pocnet.net/sheets/084/6/6AS6.pdf, pages 3-4).
* Model: Koren E1 total cathode current, split plate/screen by a g3- and plate-dependent share.
* Empirical extension, NOT a published model. Fitted only for Ec2=120 V, Ec1 in [-4,0] V,
* Ec3 in [-8,+10] V, Eb in [20,240] V. No g1/g3 grid current, no heater; capacitances are datasheet values.
* Pins: plate screen suppressor grid cathode
.SUBCKT 6AS6_FIT P G2 G3 G1 K
Bik ik 0 V=2*pwr(max((max(V(G2,K),1m)/132.404)*ln(1+exp(132.404*(1/25.3251+V(G1,K)/max(V(G2,K),1m)))),0),1.83233)/1492.17
Rik ik 0 1G
Bshare sh 0 V=0.775428*0.63661977*atan(max(V(P,K),0)/8.5637)*exp(-9.54516*ln(1+exp(-(V(G3,K)+max(V(P,K),0)/98.8108-(-9.9688)-99.7618*V(ik))/2.699)))
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
