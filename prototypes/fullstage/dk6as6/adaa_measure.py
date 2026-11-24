"""Measure the state-space ADAA prototype with the #146 unwindowed alias metric.

Run from prototypes/fullstage, with the offline renderer built as described in
RESULTS.md. A render runs from DC and uses the #113 1.25 s settle and 1 s
window by default. The native renderer is checked against the Python stage.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import ctypes as C
from functools import lru_cache
from itertools import product
import json
from pathlib import Path

import numpy as np

from dk.aliasing import SWEEP_HZ, tone_bin
from dk6as6 import inaudible
from dk6as6.adaa import quadrature
from dk6as6.aliasing import SETTLE_S, WINDOW_S
from dk6as6.circuit import MODES
from dk6as6.lpf import POSITIONS
from dk6as6.stage import supported

# #146 in-band grid, including the near-20 kHz probes.
TONES = tuple(SWEEP_HZ) + (19237, 19437, 19637, 19831, 19837, 19937, 19957, 19977, 19987, 19997)
CASES = (tuple(("P1", b) for b in (0.1, 0.25, 0.45, 0.6))
         + tuple(("T", b) for b in (0.1, 0.25, 0.3, 0.35, 0.375, 0.4, 0.45, 0.5, 0.55, 0.6))
         + tuple(("P2", b) for b in (0.1, 0.25, 0.45, 0.6)))
CONFIGS = ("os2", "os4", "os8", "os16", "a1x2", "a1x4", "a1x8", "a2x2", "a2x4", "a2x8")
INFO = ("latency", "up_delay", "nonconverged", "out_of_law", "restarts")


class Node(C.Structure):
    _fields_ = [("t", C.c_double), ("w", C.c_double)]


@lru_cache(maxsize=None)
def native_quadrature(nodes):
    return (Node * nodes)(*(Node(t, w) for t, w in zip(*quadrature(nodes))))


@lru_cache(maxsize=None)
def renderer(path):
    fn = C.CDLL(path).adaa_render
    buf = C.POINTER(C.c_double)
    fn.argtypes = [C.c_double, C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_double, C.c_double,
                   C.c_int, buf, buf, C.c_size_t, buf, buf, C.POINTER(C.c_uint64), C.POINTER(Node)]
    fn.restype = C.c_int
    return fn


def render(path, fs, os, order, nodes, mode, bias, x, lpf="off", hardness=1.0, voltages=False):
    x = np.ascontiguousarray(x, dtype=np.float64)
    y = np.empty_like(x)
    vgk = np.empty(len(x) * os) if voltages else None
    vg2k = np.empty_like(vgk) if voltages else None
    info = (C.c_uint64 * len(INFO))()

    def ptr(a):
        return None if a is None else a.ctypes.data_as(C.POINTER(C.c_double))

    # Use the benchmark's exact coefficients for its measured configurations.
    q = native_quadrature(nodes) if order and nodes not in (2, 4, 8) else None
    status = renderer(path)(fs, os, order, nodes, mode.encode(), bias, hardness, POSITIONS.index(lpf),
                            ptr(x), ptr(y), len(x), ptr(vgk), ptr(vg2k), info, q)
    if status != 0:
        raise RuntimeError(f"native prepare status {status}: {mode} {bias} mA, {fs} Hz, {os}×")
    if not np.all(np.isfinite(y)):
        raise RuntimeError(f"nonfinite render: {mode} {bias} mA, {fs} Hz, {os}×")
    return y, dict(zip(INFO, info)), vgk, vg2k


def config(name):
    return (0, int(name[2:])) if name.startswith("os") else tuple(map(int, name[1:].split("x")))


def measure(job):
    lib, name, nodes, fs, case, drive, tone, lpf, hardness, window, settle = job
    mode, bias = case
    order, os = config(name)
    n, n0 = round(window * fs), round(settle * fs)
    m = tone_bin(tone, n, fs)
    # Resampler latency is below 512 samples for all tested factors. Use the
    # exact render latency when slicing the analysis window.
    x = drive * np.sin(2 * np.pi * m * np.arange(n0 + n + 512) / n)
    y, stats, _, _ = render(lib, fs, os, order, nodes, mode, bias, x, lpf, hardness)
    start = n0 + stats["latency"]
    result = inaudible.assess(y[start:start + n], m, fs)
    return dict(config=name, nodes=nodes if order else 0, fs=fs, mode=mode, bias=bias, drive=drive, tone=tone,
                f_hz=m * fs / n, lpf=lpf, hardness=hardness, window=window, settle=settle,
                supported=supported(mode, bias, hardness), **stats, **result)


def run(args):
    cases = [(m, float(b)) for m, b in (s.split(":") for s in args.cases)] if args.cases else CASES
    if any(m not in MODES for m, _ in cases):
        raise ValueError(f"unknown mode in {cases}")
    jobs = ((args.lib, name, args.nodes, fs, case, drive, tone, lpf, hardness, args.window, args.settle)
            for name, fs, case, drive, tone, lpf, hardness in
            product(args.configs, args.rates, cases, args.drives, args.tones, args.lpfs, args.hardness))
    done = set()
    path = Path(args.out)
    if args.resume and path.exists():
        for line in path.read_text().splitlines():
            row = json.loads(line)
            done.add((row["config"], row["nodes"], row["fs"], (row["mode"], row["bias"]), row["drive"],
                      row["tone"], row["lpf"], row["hardness"], row["window"], row["settle"]))
    with ProcessPoolExecutor(args.workers) as pool, path.open("a" if args.resume else "w") as out:
        futures = []
        for job in jobs:
            _, name, nodes, *settings = job
            if (name, 0 if name.startswith("os") else nodes, *settings) not in done:
                futures.append(pool.submit(measure, job))
        for future in as_completed(futures):
            row = future.result()
            out.write(json.dumps(row, allow_nan=False) + "\n")
            out.flush()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lib", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--configs", nargs="+", choices=CONFIGS, default=CONFIGS)
    p.add_argument("--nodes", type=int, choices=(2, 4, 8, 16, 32), default=4)
    p.add_argument("--rates", type=int, nargs="+", default=inaudible.RATES)
    p.add_argument("--cases", nargs="+", metavar="MODE:mA")
    p.add_argument("--drives", type=float, nargs="+", default=inaudible.DRIVES)
    p.add_argument("--tones", type=int, nargs="+", default=TONES)
    p.add_argument("--lpfs", nargs="+", choices=POSITIONS, default=("off",))
    p.add_argument("--hardness", type=float, nargs="+", default=(1.0,))
    p.add_argument("--window", type=float, default=WINDOW_S)
    p.add_argument("--settle", type=float, default=SETTLE_S)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--resume", action="store_true", help="append missing points to the existing output")
    run(p.parse_args())


if __name__ == "__main__":
    main()
