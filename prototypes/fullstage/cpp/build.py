"""Compile a cpp/*.cpp harness (test_core, bench) with the project's warning flags. Standard library only, so that
cpp/bench.py runs without numpy / scipy. Used by cpp/validate_cpp.py, cpp/bench.py and cpp/tests."""
import os
import platform
import shutil
import subprocess
import tempfile
HERE = os.path.dirname(os.path.abspath(__file__))

CXXFLAGS = ["-std=c++17", "-Wall", "-Wextra", "-pedantic", "-pthread"]


def compiler():
    """$CXX, else g++ / c++ / clang++ on PATH, else None."""
    for c in (os.environ.get("CXX"), "g++", "c++", "clang++"):
        if c and shutil.which(c):
            return shutil.which(c)
    return None


_BUILT = {}


def build(flags=("-O2",), defines=(), name="test_core"):
    """Compile cpp/<name>.cpp with flags (cached per name, flags, defines); returns the executable's path."""
    key = (name, tuple(flags), tuple(defines))
    if key not in _BUILT:
        cxx = compiler()
        if cxx is None:
            raise FileNotFoundError("no C++ compiler (set CXX)")
        exe = os.path.join(tempfile.mkdtemp(prefix="dkcore_"), name)
        link = (["-static-libstdc++", "-static-libgcc"]
                if platform.system() == "Linux" and "-fsanitize=thread" not in flags else [])
        cmd = [cxx, *CXXFLAGS, *flags, *(f"-D{d}" for d in defines), "-o", exe, os.path.join(HERE, name + ".cpp"),
               *link]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        _BUILT[key] = exe
    return _BUILT[key]
