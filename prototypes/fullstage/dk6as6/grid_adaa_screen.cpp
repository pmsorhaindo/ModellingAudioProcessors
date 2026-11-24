#include "grid_adaa_screen.h"

namespace {
using namespace dk6as6;

template <typename Processor>
void render(Processor& stage, const double* x, double* y, std::size_t n,
            double* vgk, double* vg2k, std::uint64_t* info) {
    const Record record{vgk, vg2k};
    stage.process(x, y, n, vgk || vg2k ? &record : nullptr);
    info[0] = stage.latency_samples();
    info[1] = stage.up_delay_internal();
    info[2] = stage.stats().nonconverged;
    info[3] = stage.stats().out_of_law;
    info[4] = stage.stats().restarted;
}
}  // namespace

extern "C" void grid_adaa_shape(double* coefficients) {
    const auto g = grid();
    coefficients[0] = g.GG;
    coefficients[1] = g.XI;
    coefficients[2] = g.CG;
}

extern "C" int grid_adaa_prepare(double fs, int os, int mode, double bias, double* resistance) {
    Settings s;
    s.mode = Mode(mode);
    s.bias_ma = bias;
    s.experimental = true;
    Pack p;
    const Status status = build_pack(p, fs * os, s);
    if (status == Status::ok) *resistance = p.K[0][2];
    return int(status);
}

extern "C" int grid_adaa_render(double fs, int os, int order, int mode, double bias, int lpf,
                                const double* x, double* y, std::size_t n, double* vgk, double* vg2k,
                                std::uint64_t* info, const double* table, const double* instant) {
    Settings s;
    s.mode = Mode(mode);
    s.bias_ma = bias;
    s.lpf = Lpf(lpf);
    s.experimental = true;
    if (order == 0) {
        Stage stage;
        const Status status = stage.prepare(fs, Quality{os, 0}, s);
        if (status == Status::ok) render(stage, x, y, n, vgk, vg2k, info);
        return int(status);
    }
    grid_research::Screen screen;
    const Status status = screen.prepare(fs, os, s, order, table, instant);
    if (status == Status::ok) {
        render(screen, x, y, n, vgk, vg2k, info);
        info[5] = screen.table_outside();
    }
    return int(status);
}
