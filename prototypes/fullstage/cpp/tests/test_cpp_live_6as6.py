"""Live Bias, Mode and Filter on the C++ 6AS6 core (#115): cpp/test_live_6as6.cpp, which measures the bias line and
the two-voice LiveStage as #73 measured the 12AX7 bias (method and numbers: cpp/RESULTS-smoothing-6as6.md). Needs a
C++17 compiler ($CXX, else g++, c++ or clang++): skipped without one, except under CI (CI set), where it fails.
  test_live_6as6   every check of the program at standard 8x, 48 kHz (-O2): the line against build_pack, at rest
                   against a prepared stage, the fast full-range bias moves against the continuous reference, the mode
                   and filter switches, block sizes 16 / 64 / 512 bit for bit, NaN / inf audio, no allocation
Run from prototypes/fullstage:  python -m pytest -q cpp/tests/test_cpp_live_6as6.py
"""
import json
import os
import signal
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from cpp import build as B  # noqa: E402 -- after sys.path


def test_live_6as6():
    if B.compiler() is None:
        if os.environ.get("CI"):
            pytest.fail("no C++ compiler on CI (g++ expected on the runner)")
        pytest.skip("no C++ compiler (set CXX)")
    exe = B.build(("-O2",), (), "test_live_6as6")
    p = subprocess.run([exe], capture_output=True, text=True)
    if p.returncode == -signal.SIGILL:
        pytest.skip("this CPU cannot run the build")
    res = json.loads(p.stdout.strip().splitlines()[-1])
    failed = [line for line in p.stdout.splitlines() if line.startswith("FAIL")]
    assert p.returncode == 0 and res["failures"] == 0, "\n".join(failed) + p.stderr[-2000:]
    assert res["alloc_hooks"] == 1 and res["process_allocations"] == 0
