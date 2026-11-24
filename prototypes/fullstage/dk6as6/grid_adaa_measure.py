"""Measure #154 with the exact #136/#146 criterion, coherent tone grid and k140 FIR."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import ctypes as C
from functools import lru_cache
from itertools import product
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import numpy as np

from cpp.build import compiler
from dk.aliasing import SWEEP_HZ, tone_bin

from . import inaudible, pentode
from .aliasing import SETTLE_S, WINDOW_S
from .grid_adaa import load_line
from .lpf import POSITIONS

RATES = (44100, 48000)
TONES = tuple(SWEEP_HZ) + (19237, 19437, 19637, 19831, 19837, 19937, 19957, 19977, 19987, 19997)
CASES = (tuple(("P1", b) for b in (0.1, 0.25, 0.45, 0.6))
         + tuple(("T", b) for b in (0.1, 0.25, 0.3, 0.35, 0.375, 0.4, 0.45, 0.5)))
CONFIGS = ("os4", "a1x4", "a2x4", "os8", "a1x8", "a2x8", "os16", "a1x16", "a2x16")
MODE_NUMBER = {"P1": 0, "T": 1, "P2": 2}
INFO = ("latency", "up_delay", "nonconverged", "out_of_law", "restarts", "table_outside")
BUF = C.POINTER(C.c_double)


def build_screen(out, shape=pentode.GRID_SHAPE):
    """For #131 sensitivity builds, substitute the Table 1 fit in a scratch copy."""
    cxx = compiler()
    if cxx is None:
        raise RuntimeError("a C++17 compiler is required")
    source = Path(__file__).resolve().parent.parent
    out = Path(out).resolve()
    cmd = [cxx, "-std=c++17", "-O3", "-ffp-contract=off", "-shared", "-fPIC"]
    if shape == pentode.GRID_SHAPE:
        subprocess.run([*cmd, str(source / "dk6as6/grid_adaa_screen.cpp"), "-o", str(out)], check=True)
        return
    with TemporaryDirectory(dir=out.parent) as scratch:
        root = Path(scratch)
        for name in ("dk6as6", "cpp", "dk"):
            (root / name).mkdir()
        for name in ("dk6as6/grid_adaa_screen.cpp", "dk6as6/grid_adaa_screen.h", "cpp/dk_core.h",
                     "dk/fastmath.h", "dk/fastmath_tables.h"):
            shutil.copyfile(source / name, root / name)
        name = "cpp/dk6as6_core.h"
        code = (source / name).read_text()
        line = "const auto& dz = dkcore::DZ_12AX7_RSD1;"
        if code.count(line) != 1:
            raise RuntimeError("expected one grid-law selection in the core")
        dz = pentode.GRID_SHAPES[shape]
        coeffs = ", ".join(repr(float(dz[k])) for k in ("G", "mu", "gamma", "C", "Gg", "xi", "Cg", "Ig0"))
        (root / name).write_text(code.replace(line, f"const dkcore::TriodeParams dz{{{coeffs}}};"))
        subprocess.run([*cmd, str(root / "dk6as6/grid_adaa_screen.cpp"), "-o", str(out)], check=True)


def config(name):
    return (0, int(name[2:])) if name.startswith("os") else tuple(map(int, name[1:].split("x")))


@lru_cache(maxsize=None)
def renderer(path):
    api = C.CDLL(str(path))
    api.grid_adaa_shape.argtypes = [BUF]
    api.grid_adaa_shape.restype = None
    api.grid_adaa_prepare.argtypes = [C.c_double, C.c_int, C.c_int, C.c_double, BUF]
    api.grid_adaa_prepare.restype = C.c_int
    api.grid_adaa_render.argtypes = [C.c_double, C.c_int, C.c_int, C.c_int, C.c_double, C.c_int,
                                    BUF, BUF, C.c_size_t, BUF, BUF, C.POINTER(C.c_uint64), BUF, BUF]
    api.grid_adaa_render.restype = C.c_int
    return api


@lru_cache(maxsize=8)
def grid_coefficients(path, shape):
    values = (C.c_double * 3)()
    renderer(path).grid_adaa_shape(values)
    grid = pentode.grid(shape=shape)
    expected = tuple(grid[k] for k in ("GG", "XI", "CG"))
    if tuple(values) != expected:
        raise ValueError(f"native library does not use grid shape {shape}")
    return expected


@lru_cache(maxsize=96)
def tables(path, fs, os, mode, bias, shape=pentode.GRID_SHAPE):
    resistance = C.c_double()
    status = renderer(path).grid_adaa_prepare(fs, os, MODE_NUMBER[mode], bias, C.byref(resistance))
    if status:
        raise RuntimeError(f"grid line preparation failed ({status})")
    args = grid_coefficients(path, shape)
    return load_line(0.0, *args), load_line(resistance.value, *args)


def render(path, fs, os, order, mode, bias, x, lpf="off", voltages=False, shape=pentode.GRID_SHAPE):
    grid_coefficients(path, shape)
    api = renderer(path)
    x = np.ascontiguousarray(x, dtype=np.float64)
    y = np.empty_like(x)
    vgk = np.empty(len(x) * os) if voltages else None
    vg2k = np.empty_like(vgk) if voltages else None
    info = (C.c_uint64 * len(INFO))()

    def ptr(a):
        return None if a is None else a.ctypes.data_as(BUF)

    raw, instant = tables(path, fs, os, mode, bias, shape) if order else (None, None)
    status = api.grid_adaa_render(fs, os, order, MODE_NUMBER[mode], bias, POSITIONS.index(lpf),
                                  ptr(x), ptr(y), len(x), ptr(vgk), ptr(vg2k), info, ptr(raw), ptr(instant))
    if status or not np.all(np.isfinite(y)):
        raise RuntimeError(f"render failed ({status}, {list(info)}): {order=}, {mode=}, {bias=}, {fs=}, {os=}")
    return y, dict(zip(INFO, info)), vgk, vg2k


def measure(job):
    lib, name, fs, (mode, bias), tone, lpf, shape = job
    order, os = config(name)
    n, n0 = round(WINDOW_S * fs), round(SETTLE_S * fs)
    m = tone_bin(tone, n, fs)
    x = 8.0 * np.sin(2 * np.pi * m * np.arange(n0 + n + 512) / n)
    y, stats, _, _ = render(lib, fs, os, order, mode, bias, x, lpf, shape=shape)
    start = n0 + stats["latency"]
    result = inaudible.assess(y[start:start+n], m, fs, os_=os)
    return dict(config=name, fs=fs, mode=mode, bias=bias, tone=tone, lpf=lpf, shape=shape, **stats, **result)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lib", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--configs", nargs="+", default=CONFIGS)
    p.add_argument("--rates", type=int, nargs="+", choices=RATES, default=RATES)
    p.add_argument("--cases", nargs="+", metavar="MODE:mA")
    p.add_argument("--tones", type=int, nargs="+", default=TONES)
    p.add_argument("--lpfs", nargs="+", choices=POSITIONS, default=("off",))
    p.add_argument("--shape", choices=pentode.GRID_SHAPES, default=pentode.GRID_SHAPE)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    cases = [(m, float(b)) for m, b in (s.split(":") for s in args.cases)] if args.cases else CASES
    # Group work by setting to reuse that setting's table in each process.
    done = set()
    keys = ("config", "fs", "mode", "bias", "tone", "lpf", "shape")
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            row = json.loads(line)
            done.add(tuple(row[k] for k in keys))
    jobs = ((args.lib.resolve(), name, fs, case, tone, lpf, args.shape)
            for case, name, fs, lpf, tone in product(cases, args.configs, args.rates, args.lpfs, args.tones)
            if (name, fs, *case, tone, lpf, args.shape) not in done)
    with ProcessPoolExecutor(args.workers) as pool, args.out.open("a") as out:
        for row in pool.map(measure, jobs, chunksize=4):
            out.write(json.dumps(row, allow_nan=False) + "\n")
            out.flush()


if __name__ == "__main__":
    main()
