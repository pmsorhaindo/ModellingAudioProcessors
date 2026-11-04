"""Analyse the v2 long sine runs: harmonics of v(out) over the last 100 cycles (400-500 ms),
plus a settling check against the previous 100 cycles (300-400 ms). Writes spice/reference_sine.json."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from analysis import load_wrdata, resample_uniform, harmonics, thd, db_rel

FS, F0 = 1e6, 1e3
res = {}
for amp in ["0.05", "0.5", "2", "8"]:
    t, v = load_wrdata(os.path.join(HERE, "out", f"tran_long_{amp}v.txt"), 4)  # ngspice lower-cases the name
    tu, out = resample_uniform(t, v[:, 3], FS, 0.3, 0.5)
    h_late = harmonics(out, FS, F0, 100)
    h_early = harmonics(out[: len(out) // 2], FS, F0, 100)
    res[amp] = dict(H_peak_V=h_late[:5].tolist(), H_dBc=db_rel(h_late)[:5].tolist(), thd_pct=100 * thd(h_late),
                    # H1: absolute dB change; H2..H5: change in dBc
                    settle_dB_change_H1_H5=[float(20 * np.log10(h_late[0] / h_early[0]))]
                    + (db_rel(h_late)[1:5] - db_rel(h_early)[1:5]).tolist())
    print(amp, "THD %.4f %%" % res[amp]["thd_pct"], "dBc", np.round(res[amp]["H_dBc"], 2), "settle dB", np.round(res[amp]["settle_dB_change_H1_H5"], 4))
json.dump(dict(method="ngspice 41, Koren 12AX7, tran 2u 500m (saved from 300m), v(out) resampled to 1 MHz, "
               "single-bin DFT over last 100 cycles (400-500 ms); settle = change vs 300-400 ms window",
               results=res), open(os.path.join(HERE, "reference_sine.json"), "w"), indent=1)
