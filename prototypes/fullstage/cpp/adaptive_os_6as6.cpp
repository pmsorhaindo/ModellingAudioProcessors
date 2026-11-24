// Offline harness for #148. The processing segments use prebuilt packs and fixed storage.
#include "dk6as6_core.h"
#include "alloc_hooks.h"

#include <algorithm>
#include <array>
#include <ctime>
#include <iostream>
#include <map>
#include <stdexcept>
#include <vector>

namespace dk6as6 {
struct AdaptiveOsProbe {
    template <int MaxOs> static void change_pack(StageT<MaxOs>& stage, const Pack& next) noexcept {
        const Pack& old = stage.pack_;
        // Same clock: preserve absolute trapezoidal histories when rebasing the DC deviation.
        for (int i = 0; i < NX; ++i) stage.x_[i] += old.x_dc[i] - next.x_dc[i];
        if (old.mode != next.mode) {
            // CG3 and CG1G3 reconnect. Use the new rest histories for those two only.
            stage.x_[3] = stage.x_[6] = 0.0;
            for (int i = 0; i < NI_MAX; ++i) stage.i_[i] = stage.q_[i] = next.i_dc[i];
        }
        stage.pack_ = next;
    }
};
}  // namespace dk6as6

namespace {
using namespace dk6as6;
constexpr int block = 64;
constexpr double fade_seconds = 0.015;  // same duration as the existing post-filter fade, an experimental choice
constexpr double crossover_ma = 0.35;
using ResearchStage = StageT<64>;
static_assert(std::atomic<double>::is_always_lock_free && std::atomic<std::uint8_t>::is_always_lock_free,
              "the probe requires lock-free stage controls");

double thread_cpu_ns() {
    timespec t{};
    if (clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t) != 0) throw std::runtime_error("thread CPU clock unavailable");
    return 1e9 * double(t.tv_sec) + double(t.tv_nsec);
}

struct Control {
    Settings settings;
    std::array<Pack, 2> packs;
};

void check(Status s) {
    if (s != Status::ok) throw std::runtime_error(to_string(s));
}

void make_pack(Pack& pack, double internal_fs, const Settings& settings, const GridParams* grid) {
    check(detail::build_pack_with_grid(pack, internal_fs, settings, grid));
}

struct Path {
    ResearchStage stage;
    const Pack* current = nullptr;
    int delay = 0;
    // With k140 the 16× path needs one sample of compensation for 32× and two for 64×.
    std::array<double, 2> history{};
    bool primed = false;
    Stats retired{};

    void start(const Pack& pack) noexcept {
        const Stats& s = stage.stats();
        retired.nonconverged += s.nonconverged;
        retired.out_of_law += s.out_of_law;
        retired.recoveries += s.recoveries;
        AdaptiveOsProbe::change_pack(stage, pack);
        current = &pack;
        stage.reset();
        primed = false;
    }

    void run(const Pack& pack, const double* x, double* y, int n) noexcept {
        if (&pack != current) {
            AdaptiveOsProbe::change_pack(stage, pack);
            current = &pack;
        }
        stage.process(x, y, n);
        if (delay == 0) return;
        if (!primed) { history.fill(y[0]); primed = true; }
        for (int i = 0; i < n; ++i)
            for (int j = 0; j < delay; ++j) std::swap(y[i], history[j]);
    }
};

enum class Method { fixed16, fixed_high, fade, handoff, long_fade };
struct Frame { double output, weight_high, dual; };
struct Command { std::size_t first; const Control* control; int target; };

class Switch {
public:
    Switch(double fs, const Control& first, int target, int high, Method method, double long_warm)
        : samples_(int(std::lround(fs * fade_seconds))),
          warm_samples_(int(std::lround(fs * (method == Method::long_fade ? long_warm : fade_seconds)))),
          active_(target), method_(method) {
        const int factors[] = {16, high};
        for (int i = 0; i < 2; ++i) {
            check(paths_[i].stage.prepare_pack(fs, factors[i], first.packs[i], first.settings.lpf));
            paths_[i].current = &first.packs[i];
        }
        latency = std::max(paths_[0].stage.latency_samples(), paths_[1].stage.latency_samples());
        for (auto& path : paths_) {
            path.delay = latency - path.stage.latency_samples();
            if (path.delay > int(path.history.size()))
                throw std::runtime_error("latency difference exceeds the probe's alignment buffer");
        }
        if (method == Method::fixed16) active_ = 0;
        if (method == Method::fixed_high) active_ = 1;
    }

    // May return early at a warm-up or fade boundary. The caller resumes with the same control.
    int segment(const Command& command, const double* x, Frame* out, int n) noexcept {
        const bool adaptive = method_ != Method::fixed16 && method_ != Method::fixed_high;
        const bool toward_pending = adaptive && command.target != active_;
        if (toward_pending && !pending_) {
            pending_ = true;
            warm_ = warm_samples_;
            paths_[1 - active_].start(command.control->packs[1 - active_]);
        }
        if (!toward_pending && mix_ == 0) pending_ = false;

        const bool dual = pending_;
        if (dual) {
            const int remaining = warm_ > 0 ? warm_ : toward_pending ? samples_ - mix_ : mix_;
            n = std::min(n, remaining);
        }

        double a[block], b[block];
        paths_[active_].run(command.control->packs[active_], x, a, n);
        if (dual) paths_[1 - active_].run(command.control->packs[1 - active_], x, b, n);

        if (dual && warm_ == 0) {
            const int direction = toward_pending ? 1 : -1;
            for (int i = 0; i < n; ++i) {
                mix_ += direction;
                const double w = double(mix_) / samples_;
                out[i] = {w == 1.0 ? b[i] : a[i] + w * (b[i] - a[i]), active_ ? 1.0 - w : w, 1.0};
            }
            if (mix_ == samples_) finish();
            if (mix_ == 0) pending_ = false;
        } else {
            for (int i = 0; i < n; ++i) out[i] = {a[i], double(active_), double(dual)};
            if (dual) {
                warm_ -= n;
                if (warm_ == 0 && method_ == Method::handoff) finish();
            }
        }
        return n;
    }

    Stats counts() const {
        Stats result{};
        for (const auto& p : paths_) {
            result.nonconverged += p.retired.nonconverged + p.stage.stats().nonconverged;
            result.out_of_law += p.retired.out_of_law + p.stage.stats().out_of_law;
            result.recoveries += p.retired.recoveries + p.stage.stats().recoveries;
        }
        return result;
    }

    int latency = 0;

private:
    void finish() noexcept {
        active_ = 1 - active_;
        pending_ = false;
        mix_ = 0;
    }

    Path paths_[2];
    int samples_, warm_samples_, active_;
    Method method_;
    bool pending_ = false;
    int warm_ = 0, mix_ = 0;
};

struct Input { double volts, bias_ma, triode, hq; };

struct Timeline {
    std::vector<double> volts;
    std::vector<Command> commands;
    std::map<std::pair<double, Mode>, Control> controls;

    Timeline(const std::vector<Input>& input, double fs, Lpf lpf, const GridParams* grid, int high) {
        const int factors[] = {16, high};
        for (const auto& row : input) {
            if (!std::isfinite(row.bias_ma) || (row.triode != 0 && row.triode != 1) || (row.hq != 0 && row.hq != 1))
                throw std::runtime_error("invalid control row");
            const Mode mode = row.triode ? Mode::T : Mode::P1;
            auto [at, inserted] = controls.try_emplace({row.bias_ma, mode});
            auto& c = at->second;
            if (inserted) {
                c.settings.bias_ma = row.bias_ma;
                c.settings.mode = mode;
                c.settings.lpf = lpf;
                for (int i = 0; i < 2; ++i) make_pack(c.packs[i], fs * factors[i], c.settings, grid);
            }
            const int target = row.hq || (mode == Mode::T && row.bias_ma >= crossover_ma);
            if (commands.empty() || commands.back().control != &c || commands.back().target != target)
                commands.push_back({volts.size(), &c, target});
            volts.push_back(row.volts);
        }
    }
};

void transition(double fs, Lpf lpf, const GridParams* grid, int reps, int high, double long_warm) {
    std::vector<Input> input;
    Input row;
    while (std::cin.read(reinterpret_cast<char*>(&row), sizeof(row))) input.push_back(row);
    if (std::cin.gcount() != 0 || input.empty()) throw std::runtime_error("expected complete input frames");
    const Timeline timeline(input, fs, lpf, grid, high);
    const auto& commands = timeline.commands;
    const auto& x = timeline.volts;
    std::vector<Frame> out(x.size());
    for (auto method : {Method::fixed16, Method::fixed_high, Method::fade, Method::handoff, Method::long_fade}) {
        if (method == Method::long_fade && long_warm == 0.0) continue;
        for (int rep = 0; rep < reps; ++rep) {
            Switch sw(fs, *commands.front().control, commands.front().target, high, method, long_warm);
            double single_ns = 0, dual_ns = 0;
            std::size_t dual_samples = 0;
            long allocs;
            {
                CountAllocs counter;
                for (std::size_t c = 0; c < commands.size(); ++c) {
                    const std::size_t end = c + 1 < commands.size() ? commands[c + 1].first : x.size();
                    for (std::size_t i = commands[c].first; i < end;) {
                        const int n = int(std::min<std::size_t>(end - i, block - i % block));
                        // Clock reads belong to this offline caller, outside segment().
                        const double start = thread_cpu_ns();
                        const int done = sw.segment(commands[c], x.data() + i, out.data() + i, n);
                        const double elapsed = thread_cpu_ns() - start;
                        if (out[i].dual) {
                            dual_ns += elapsed;
                            dual_samples += done;
                        } else {
                            single_ns += elapsed;
                        }
                        i += done;
                    }
                }
                allocs = counter.n();
            }
            const auto s = sw.counts();
            std::cerr << sw.latency << ' ' << single_ns << ' ' << dual_ns << ' ' << dual_samples << ' '
                      << allocs << ' ' << s.nonconverged << ' ' << s.out_of_law << ' ' << s.recoveries << '\n';
        }
        std::cout.write(reinterpret_cast<const char*>(out.data()), out.size() * sizeof(Frame));
    }
}

void resample_impulse(double fs, int factor) {
    ResearchStage::Resampler rs;
    if (!rs.prepare(factor, fs, DEFAULT_FIR)) throw std::runtime_error("invalid resampler configuration");
    std::array<double, 64> up{};
    // Place the impulse after the decimator has been primed with silence.
    for (int n = 0; n < 512; ++n) {
        rs.up(n == 32 ? 1.0 : 0.0, up.data());
        const double down = rs.down(up.data());
        std::cout.write(reinterpret_cast<const char*>(up.data()), factor * sizeof(double));
        std::cout.write(reinterpret_cast<const char*>(&down), sizeof(down));
    }
}

void steady(double fs, int os, const Settings& settings, const GridParams* grid, int tone) {
    ResearchStage stage;
    Pack p;
    make_pack(p, fs * os, settings, grid);
    check(stage.prepare_pack(fs, os, p, settings.lpf));
    const int latency = stage.latency_samples();
    const int size = int(fs) + int(std::lround(1.25 * fs)) + latency;
    double x[block], y[block];
    for (int i = 0; i < size; i += block) {
        const int n = std::min(block, size - i);
        for (int j = 0; j < n; ++j) x[j] = 8.0 * std::sin(2.0 * PI * tone * (i + j) / fs);
        stage.process(x, y, n);
        std::cout.write(reinterpret_cast<const char*>(y), n * sizeof(double));
    }
    const auto& s = stage.stats();
    std::cerr << latency << ' ' << s.nonconverged << ' ' << s.out_of_law << ' ' << s.recoveries << '\n';
}
}  // namespace

int main(int argc, char** argv) {
    try {
        const std::string action = argc > 1 ? argv[1] : "";
        if ((action != "transition" || argc != 10) && (action != "steady" || argc != 11) &&
            (action != "resample-impulse" || argc != 4))
            throw std::runtime_error("transition fs lpf gg xi cg reps high long_warm | "
                                     "steady fs lpf gg xi cg os mode bias tone | resample-impulse fs os");
        const double fs = std::stod(argv[2]);
        if (fs != 44100.0 && fs != 48000.0) throw std::runtime_error("use 44100 or 48000 Hz");
        if (action == "resample-impulse") {
            resample_impulse(fs, std::stoi(argv[3]));
            return 0;
        }
        const Lpf lpf = Lpf(std::stoi(argv[3]));
        if (lpf != Lpf::off && lpf != Lpf::k4 && lpf != Lpf::k7) throw std::runtime_error("invalid filter");
        const GridParams bound{std::stod(argv[4]), std::stod(argv[5]), std::stod(argv[6]), HARDNESS};
        const GridParams* grid = bound.GG == 0.0 ? nullptr : &bound;
        if (action == "transition") {
            const int reps = std::stoi(argv[7]);
            const int high = std::stoi(argv[8]);
            const double long_warm = std::stod(argv[9]);
            if (reps < 1 || (high != 32 && high != 64) ||
                !(long_warm == 0.0 || (fade_seconds <= long_warm && long_warm <= 10.0)))
                throw std::runtime_error("invalid repetition count, high factor or warm-up");
            transition(fs, lpf, grid, reps, high, long_warm);
        } else {
            Settings settings;
            settings.mode = std::string(argv[8]) == "T" ? Mode::T : Mode::P1;
            settings.bias_ma = std::stod(argv[9]);
            settings.lpf = lpf;
            steady(fs, std::stoi(argv[7]), settings, grid, std::stoi(argv[10]));
        }
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
}
