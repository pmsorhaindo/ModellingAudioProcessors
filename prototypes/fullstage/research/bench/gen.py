# usage: python gen.py <checkout of af94e3f>/prototypes/fullstage   (writes mats.h to the current directory)
import sys, numpy as np
sys.path.insert(0, sys.argv[1])
from dk import circuit
from dk.stage import Stage, os_filter
out=open('mats.h','w')
for OS in (2,4,8,16):
    st=Stage(48000.0, os=OS); m=st.m
    def arr(n,M): out.write(f"static const double {n}_{OS}[] = {{{','.join(repr(float(v)) for v in np.ravel(M))}}};\n")
    arr('A',m['A']); arr('Bv',m['B'][:,0]); arr('Bh',m['B'][:,1]*300.0); arr('C',m['C'])
    arr('G',m['G']); arr('Hv',m['H'][:,0]); arr('Hh',m['H'][:,1]*300.0); arr('K',m['K'])
    arr('D',m['D'][0]); arr('Ev',[m['E'][0,0]]); arr('Eh',[m['E'][0,1]*300.0]); arr('F',m['F'][0])
    arr('X0',st.x); arr('V0',st.v); arr('FIR',os_filter(48000.0,OS))
out.close(); print("ok")
