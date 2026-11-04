"""Compare the ngspice 6AS6_FIT sweeps (out/*.txt) with model6as6.py. Writes spice_check.json."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import model6as6 as M
P = json.load(open(os.path.join(HERE, "..", "results", "fit_results.json")))["dual_control"]["params"]
out, worst = {}, dict(abs_mA=0.0, rel=0.0)
for page, ecs, sweep in [("p3", [0, -1, -2, -3, -4], "Eb"), ("p4", [-1, -2, -3], "Ec3")]:
    for e in ecs:
        d = np.loadtxt(os.path.join(HERE, "out", f"{page}_ec1_{e}.txt"), skiprows=1)
        x, ib, ic2 = d[:, 0], d[:, 1], d[:, 2]
        if page == "p3":
            vg1, vg2, vg3, va = e, 120.0, 0.0, x
        else:
            vg1, vg2, vg3, va = e, 120.0, x, 120.0
        pib = M.dual_ip(vg1, vg2, vg3, va, P); pic2 = M.dual_ig2(vg1, vg2, vg3, va, P)
        r = {}
        for nm, s, p in [("Ib", ib, pib), ("Ic2", ic2, pic2)]:
            da = np.abs(s - p)
            rel = da / np.maximum(np.abs(p), 1e-6)   # relative where current > 1 uA
            r[nm] = dict(max_abs_mA=float(da.max() * 1e3), max_rel=float(rel.max()))
            worst["abs_mA"] = max(worst["abs_mA"], r[nm]["max_abs_mA"]); worst["rel"] = max(worst["rel"], r[nm]["max_rel"])
        out[f"{page}_Ec1_{e}"] = r
json.dump(dict(method="ngspice 41 DC sweeps of results/6AS6_fit.inc vs model6as6.dual_ip/dual_ig2 with the same params; "
               "rel = |dI|/max(|I_python|, 1 uA)", worst=worst, curves=out),
          open(os.path.join(HERE, "spice_check.json"), "w"), indent=1)
print(json.dumps(worst))
