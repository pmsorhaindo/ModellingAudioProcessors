// The CPU measure behind dk6as6/RESULTS.md, "Inaudible aliasing ... (#113 rework)", CPU: thread CPU time per base-rate
// sample of one mono 6AS6 Stage of the C++ core at 4x / 8x / 16x / 32x / 64x with the k140 resampler, 256-sample blocks,
// best (and median) of ROUNDS rounds of SECONDS each, the factors interleaved within each round. Signal: a 1 + 7 + 13 kHz
// sum at 2 or 8 V peak. NOT built by CI: it needs cpp/dk6as6_core.h (#104, PR #133) with dk_core.h's MAX_OS
// raised to 64 and ResamplerT's later_[3] raised to later_[5] (the screening build of dk6as6/inaudible_sweep.py).
//   c++ -std=c++17 -O3 -I <patched copy of>/prototypes/fullstage/cpp -o inaudible_bench dk6as6/inaudible_bench.cpp
//   ./inaudible_bench FS SECONDS ROUNDS        -> one JSON line per case and factor
#include "dk6as6_core.h"
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <ctime>
#include <vector>
#include <algorithm>
using namespace dk6as6;
static double tcpu() { timespec t{}; clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t); return t.tv_sec * 1e9 + t.tv_nsec; }
int main(int argc, char** argv) {
    const double fs = argc > 1 ? std::atof(argv[1]) : 48000.0, sec = argc > 2 ? std::atof(argv[2]) : 0.5;
    const int rounds = argc > 3 ? std::atoi(argv[3]) : 7;
    struct C { Mode m; double b; double v; const char* name; };
    const C cs[] = {{Mode::P1, 0.45, 2.0, "P1 0.45 mA 2 V"}, {Mode::P1, 0.45, 8.0, "P1 0.45 mA 8 V"},
                    {Mode::T, 0.5, 8.0, "T 0.5 mA 8 V"}};
    const int oss[] = {4, 8, 16, 32, 64};
    const size_t n = size_t(sec * fs), blk = 256;
    for (const C& c : cs) {
        std::vector<double> x(n), y(n);
        for (size_t i = 0; i < n; ++i) {
            const double t = double(i) / fs;
            x[i] = c.v / 3.0 * (std::sin(2 * M_PI * 1000 * t) + std::sin(2 * M_PI * 7000 * t) + std::sin(2 * M_PI * 13000 * t));
        }
        std::vector<std::vector<double>> best(5);
        for (int r = 0; r < rounds; ++r)
            for (int k = 0; k < 5; ++k) {
                Settings s; s.mode = c.m; s.bias_ma = c.b; s.experimental = true; s.fir = dkcore::K140_FIR;
                static dk6as6::Stage st;
                if (st.prepare(fs, Quality{oss[k], 0.0}, s) != Status::ok) { std::fprintf(stderr, "prepare\n"); return 1; }
                st.reset();
                const double t0 = tcpu();
                for (size_t i = 0; i < n; i += blk) st.process(&x[i], &y[i], std::min(blk, n - i), nullptr);
                best[k].push_back((tcpu() - t0) / double(n));
            }
        for (int k = 0; k < 5; ++k) {
            auto v = best[k]; std::sort(v.begin(), v.end());
            std::printf("{\"case\": \"%s\", \"fs\": %g, \"os\": %d, \"ns_per_sample_min\": %.1f, \"ns_per_sample_median\": %.1f, "
                        "\"rounds\": %d, \"seconds\": %g}\n", c.name, fs, oss[k], v[0], v[v.size() / 2], rounds, sec);
        }
    }
}
