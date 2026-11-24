// Native offline renderer, callable by adaa_measure.py. No plug-in entry point.
#include "adaa_screen.h"

using namespace dk6as6;

template <class S>
static int render(S& stage, const double* x, double* y, std::size_t n, double* vgk, double* vg2k,
                  std::uint64_t* info) {
    Record rec{};
    rec.vgk = vgk;
    rec.vg2k = vg2k;
    stage.process(x, y, n, (vgk || vg2k) ? &rec : nullptr);
    info[0] = stage.latency_samples();
    info[1] = stage.up_delay_internal();
    info[2] = stage.stats().nonconverged;
    info[3] = stage.stats().out_of_law;
    info[4] = stage.stats().restarted;
    return 0;
}

extern "C" int adaa_render(double fs, int os, int order, int nodes, const char* mode, double bias, double hardness,
                           int lpf, const double* x, double* y, std::size_t n, double* vgk, double* vg2k,
                           std::uint64_t* info, const research::Node* quadrature) {
    Settings settings;
    settings.mode = mode[0] == 'T' ? Mode::T : mode[1] == '1' ? Mode::P1 : Mode::P2;
    settings.bias_ma = bias;
    settings.grid_hardness = hardness;
    settings.experimental = true;
    settings.lpf = Lpf(lpf);
    const Quality q{os, 0.0};
    if (order == 0) {
        Stage stage;
        const auto status = stage.prepare(fs, q, settings);
        if (status != Status::ok) return int(status);
        return render(stage, x, y, n, vgk, vg2k, info);
    }
    research::Screen stage;
    const auto status = stage.prepare(fs, q, settings, order, nodes, quadrature);
    if (status != Status::ok) return int(status);
    return render(stage, x, y, n, vgk, vg2k, info);
}
