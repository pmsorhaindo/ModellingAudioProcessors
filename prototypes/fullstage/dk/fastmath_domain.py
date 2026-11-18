"""#18: the arguments dk/fastmath.py's functions actually receive in the validation runs -> dk/fastmath_domain.json.
Runs validate.run(quality=q, triode_math="fast") for every setting (sines at every drive level, the 4 V burst, the
DC solve with its source stepping, every Newton trial and line-search point) with the fast functions wrapped to
record min / max of their arguments, and of the (Vgk, Vak) the triode is evaluated at (vgk, vak; #60).
dk/tests/test_fastmath.py checks that its dense error grids cover these ranges.
Run from prototypes/fullstage:  python -m dk.fastmath_domain   (~ as long as the fast validation, a few minutes)
"""
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from dk import fastmath as fm  # noqa: E402 -- after the sys.path setup


def _record(q):
    from dk import validate as V
    rng = {}

    def upd(k, v):
        lo, hi, n = rng.get(k, (math.inf, -math.inf, 0))
        rng[k] = (min(lo, v), max(hi, v), n + 1)

    f_exp, f_log1p, f_pow = fm.fast_exp, fm.fast_log1p, fm.fast_pow

    def exp_(x):
        upd("exp_x", x)
        return f_exp(x)

    def log1p_(e):
        upd("log1p_e", e)
        if e > 0.0:
            upd("log1p_e_pos", e)
        return f_log1p(e)

    def pow_(s, g):
        upd(f"pow_s_g{g!r}", s)
        if s > 0.0:
            upd(f"pow_s_pos_g{g!r}", s)
            upd(f"pow_y_g{g!r}", g * math.log(s))
        return f_pow(s, g)

    from dk import triode as T
    ev = T.EVAL["fast"]

    def cur_(vgk, vak, *a):
        upd("vgk", vgk)
        upd("vak", vak)
        return ev[0](vgk, vak, *a)

    def cur_only_(vgk, vak, *a):
        upd("vgk", vgk)
        upd("vak", vak)
        return ev[1](vgk, vak, *a)

    fm.fast_exp, fm.fast_log1p, fm.fast_pow = exp_, log1p_, pow_
    T.EVAL["fast"] = (cur_, cur_only_)          # Stage(triode_math="fast") picks the pair up at construction
    try:
        V.run(quality=q, triode_math="fast")
    finally:
        fm.fast_exp, fm.fast_log1p, fm.fast_pow = f_exp, f_log1p, f_pow
        T.EVAL["fast"] = ev
    return q, rng


if __name__ == "__main__":
    from dk.stage import QUALITY
    with ProcessPoolExecutor(len(QUALITY)) as ex:
        per = dict(ex.map(_record, list(QUALITY)))
    allk = sorted({k for r in per.values() for k in r})
    tot = {k: (min(per[q][k][0] for q in per if k in per[q]), max(per[q][k][1] for q in per if k in per[q]),
               sum(per[q][k][2] for q in per if k in per[q])) for k in allk}
    out = dict(method=__doc__.strip().splitlines()[1:5], settings=list(QUALITY),
               ranges={k: dict(min=v[0], max=v[1], calls=v[2]) for k, v in tot.items()},
               per_setting={q: {k: dict(min=v[0], max=v[1], calls=v[2]) for k, v in r.items()} for q, r in per.items()})
    json.dump(out, open(os.path.join(HERE, "fastmath_domain.json"), "w"), indent=1)
    print(json.dumps(out["ranges"], indent=1))
