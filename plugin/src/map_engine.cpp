/* map_engine.cpp -- see map_engine.h for the thread rules. */
#include "map_engine.h"

#include <chrono>
#include <cmath>

namespace mapvs {

namespace {
constexpr double kRampSeconds = 0.020, kBypassSeconds = 0.010, kSwitchSeconds = 0.005;
constexpr auto kWorkerPeriod = std::chrono::milliseconds(5);
int seconds_to_samples(double s, double fs) { return int(std::lround(s * fs)); }
// A build status that is not one of the cores' (a latency the dry ring cannot hold); never 0.
constexpr int kStatusLatencyTooLong = 100;
}  // namespace

// The plugin's 6AS6 ranges are the core's (map_params.h restates them so the wrappers need not include the core).
static_assert(kBias6Range.def == dk6as6::BIAS_MA && kBias6Range.min == dk6as6::BIAS_RANGE_MA[1] &&
              kBias6Range.max == dk6as6::BIAS_RANGE_MA[0], "6AS6 bias range is the core's");
static_assert(int(dk6as6::Mode::P1) == kP1 && int(dk6as6::Mode::T) == kT && int(dk6as6::Mode::P2) == kP2,
              "Mode6 order is dk6as6::Mode's");
static_assert(kTBiasCapMa > kBias6Range.min && kTBiasCapMa <= kBias6Range.max, "T's cap is inside the 6AS6 range");
static_assert(int(dk6as6::Lpf::off) == kFilterOff && int(dk6as6::Lpf::k4) == kFilter4k &&
              int(dk6as6::Lpf::k7) == kFilter7k, "Filter6 order is dk6as6::Lpf's");
// No lock on the audio thread: every atomic it touches must be lock-free (a non-lock-free atomic takes a lock).
static_assert(std::atomic<double>::is_always_lock_free && std::atomic<int>::is_always_lock_free &&
              std::atomic<bool>::is_always_lock_free && std::atomic<std::uint8_t>::is_always_lock_free,
              "the audio thread's atomics are lock-free");

dkcore::Quality Engine::core_quality(int q) {
    switch (clamp_quality(q)) {
    case kEco: return dkcore::ECO;
    case kHQ: return dkcore::HQ;
    default: return dkcore::STANDARD;
    }
}

dkcore::Quality Engine::core_quality_6as6(int q) { return quality_6as6(q) == kHQ ? dk6as6::HQ : dk6as6::STANDARD; }

int Engine::latency_for(int valve, int q, double sample_rate) {
    if (clamp_index(valve, kNumValves) == k12AX7)
        return dkcore::fir_geometry(core_quality(q).os, sample_rate).latency_samples;
    return dkcore::fir_geometry(core_quality_6as6(q).os, sample_rate, dk6as6::DEFAULT_FIR).latency_samples;
}

int Engine::reported_latency_for(double sample_rate) {
    int m = 0;
    for (int v = 0; v < kNumValves; ++v)
        for (int q = 0; q < kNumQualities; ++q) {
            const int l = latency_for(v, q, sample_rate);
            m = l > m ? l : m;
        }
    return m;
}

dk6as6::Settings Engine::settings_6as6(const Config& c, int filter6) {
    dk6as6::Settings s;                                 // grid current on (the default hardness), k140, Newton defaults
    s.mode = dk6as6::Mode(clamp_index(c.mode6, kNumModes6));
    s.bias_ma = bias6_effective(c.mode6, c.bias6);
    s.lpf = dk6as6::Lpf(clamp_index(filter6, kNumFilters6));
    s.experimental = s.mode == dk6as6::Mode::P2;        // P2 is never supported: built only as experimental
    return s;
}

Engine::Config Engine::target_config() const {
    Config c;
    c.valve = valve_target();
    c.quality = quality_target();
    if (c.valve == k12AX7) {
        c.mode6 = 0;
        c.bias6 = 0.0;
    } else {
        c.quality = quality_6as6(c.quality);
        c.mode6 = mode6_t_.load(std::memory_order_relaxed);
        c.bias6 = bias6_effective(c.mode6, bias6_t_.load(std::memory_order_relaxed));
    }
    return c;
}

Engine::Engine() {
    slot_[0] = std::make_unique<Slot>();
    slot_[1] = std::make_unique<Slot>();
    for (auto& s : slot_) s->tri.set_bias_range(kBiasRange.min, kBiasRange.max);   // the bias line (#73), per prepare
    reported_latency_.store(reported_latency_for(48000.0), std::memory_order_relaxed);
    active_latency_.store(latency_for(kValveDefault, kQualityDefault, 48000.0), std::memory_order_relaxed);
}

Engine::~Engine() { release(); }

int Engine::build(Slot& s, const Config& c, double bias12) {
    builds_.fetch_add(1, std::memory_order_relaxed);    // non-RT (prepare() or the worker)
    if (c.valve == k12AX7) {
        const dkcore::BuildStatus st = s.tri.prepare(fs_, core_quality(c.quality), bias12);
        if (st != dkcore::BuildStatus::ok) return int(st);
        s.tri.set_drive(1.0);                           // the wrapper applies drive and output
        s.tri.set_output(1.0);
        s.tri.set_bias_ramp(kRampSeconds);              // the core ramps bias (#73), like the wrapper's gain ramps
        s.latency = s.tri.latency_samples();
        s.experimental = false;
    } else {
        // The three modes' bias lines over the plugin's ranges (T to kTBiasCapMa; P2 experimental only), once for both
        // channels; then one pack (Rk solve, matrices, DC point) at the requested mode and bias, the same as
        // Stage::prepare() on each channel, which the LiveStages start on. Mode and bias then move on the audio thread.
        const dk6as6::Settings set = settings_6as6(c, filter6_t_.load(std::memory_order_relaxed));
        const dkcore::Quality q = core_quality_6as6(c.quality);
        const dk6as6::LineRange ranges[kNumModes6] = {{kBias6Range.min, kBias6Range.max, false},
                                                      {kBias6Range.min, kTBiasCapMa, false},
                                                      {kBias6Range.min, kBias6Range.max, true}};
        dk6as6::Status st = dk6as6::build_lines(s.lines6, fs_ * q.os, set, ranges);
        if (st != dk6as6::Status::ok) return int(st);
        dk6as6::Pack p;
        st = dk6as6::build_pack(p, fs_ * q.os, set);
        if (st != dk6as6::Status::ok) return int(st);
        for (auto& stage : s.pen) {
            st = stage.prepare(fs_, q.os, s.lines6, p, set.lpf, set.fir, kRampSeconds);
            if (st != dk6as6::Status::ok) return int(st);
            stage.set_drive(1.0);
            stage.set_output(1.0);
        }
        s.latency = s.pen[0].latency_samples();
        s.experimental = !p.supported;
    }
    if (s.latency > kRing - kChunk) return kStatusLatencyTooLong;
    s.cfg = c;
    return 0;
}

bool Engine::prepare(double sample_rate) {
    stop_worker();
    prepared_.store(false, std::memory_order_relaxed);
    if (!(sample_rate > 0.0) || !std::isfinite(sample_rate)) return false;
    fs_ = sample_rate;
    const int reported = reported_latency_for(sample_rate);
    if (reported > kRing - kChunk) return false;        // never at the rates the cores have geometry for (<= 130)
    reported_latency_.store(reported, std::memory_order_relaxed);
    const Config want = target_config();
    double b = bias_t_.load(std::memory_order_relaxed);
    int st = build(*slot_[0], want, b);
    build_status_.store(st, std::memory_order_relaxed);
    build_status_valve_.store(want.valve, std::memory_order_relaxed);
    if (st != 0) {
        // 1. the path's default setting (e.g. a 12AX7 bias whose DC point does not solve);
        // 2. then the 12AX7 path's: the 6AS6 core refuses host rates whose internal rate its post-filter cannot run at
        //    (below about 4 kHz at Standard; the Steinberg validator activates at 1234.5678 Hz). The host gets audio
        //    rather than a failed activation; active_valve() and build_status() say what happened.
        Config d;
        d.valve = want.valve;
        d.quality = want.quality;
        if (d.valve == k12AX7) { d.mode6 = 0; d.bias6 = 0.0; }
        b = kBiasRange.def;
        if (build(*slot_[0], d, b) != 0) {
            if (d.valve == k12AX7) return false;
            d.valve = k12AX7;
            d.quality = quality_target();
            d.mode6 = 0;
            d.bias6 = 0.0;
            if (build(*slot_[0], d, b) != 0) return false;
        }
    }
    active_ = designated_ = 0;
    des_cfg_ = want;                                    // a failed request is not retried every poll
    applied_bias_.store(b, std::memory_order_relaxed);
    pending_.store(-1, std::memory_order_relaxed);
    audio_slot_.store(0, std::memory_order_relaxed);
    adopt(0);
    ramp_len_ = seconds_to_samples(kRampSeconds, fs_);
    bypass_len_ = seconds_to_samples(kBypassSeconds, fs_);
    switch_len_ = seconds_to_samples(kSwitchSeconds, fs_);
    reset_audio_state();
    reset_req_.store(false, std::memory_order_relaxed);
    prepared_.store(true, std::memory_order_release);
    start_worker();
    return true;
}

void Engine::release() {
    stop_worker();
    prepared_.store(false, std::memory_order_relaxed);
}

std::uint64_t Engine::nonconverged_total() const {
    const Slot& s = *slot_[audio_slot_.load(std::memory_order_relaxed)];
    if (s.cfg.valve == k12AX7) return s.tri.nonconverged_total();
    return s.pen[0].nonconverged() + s.pen[1].nonconverged();
}

// ---- audio thread ----------------------------------------------------------------------------------------------

// The audio thread runs `slot` from now on (also prepare(), with the worker stopped). Reads only the slot's plain
// fields, which the worker wrote before its release store of pending_; stores relaxed atomics. No ack here.
void Engine::adopt(int slot) noexcept {
    active_ = slot;
    const Slot& s = *slot_[slot];
    pad_ = latency_samples() - s.latency;               // >= 0: reported is the max over every stage at this rate
    if (pad_ < 0) pad_ = 0;
    wet_pos_ = 0;                                       // the old stage's delayed tail does not leak into the new one
    for (auto& ch : wet_)
        for (double& v : ch) v = 0.0;
    active_latency_.store(s.latency, std::memory_order_relaxed);
    active_valve_.store(s.cfg.valve, std::memory_order_relaxed);
    active_quality_.store(s.cfg.quality, std::memory_order_relaxed);
    active_mode6_.store(s.cfg.mode6, std::memory_order_relaxed);
    active_bias6_.store(s.cfg.bias6, std::memory_order_relaxed);
    active_experimental_.store(s.experimental, std::memory_order_relaxed);
    if (s.cfg.valve == k6AS6) publish_6as6(s);
}

// the 6AS6 mode and bias the running stage is at (they move on the audio thread, #115); relaxed stores
void Engine::publish_6as6(const Slot& s) noexcept {
    const dk6as6::LiveStage& st = s.pen[0];
    active_mode6_.store(int(st.mode()), std::memory_order_relaxed);
    active_bias6_.store(st.bias(), std::memory_order_relaxed);
    active_experimental_.store(!st.supported(), std::memory_order_relaxed);
}

void Engine::reset_audio_state() noexcept {
    Slot& s = *slot_[active_];
    if (s.cfg.valve == k12AX7) s.tri.reset();
    else for (auto& stage : s.pen) stage.reset();
    drive_.snap(drive_t_.load(std::memory_order_relaxed) * kVoltsPerFullScale);
    output_.snap(output_t_.load(std::memory_order_relaxed) * kFullScalePerVolt);
    const bool byp = bypass_t_.load(std::memory_order_relaxed) || host_bypass_t_.load(std::memory_order_relaxed);
    bypass_.snap(byp ? 1.0 : 0.0);
    sw_env_.snap(1.0);
    sw_ = Switch::idle;
    dry_delay_ = latency_samples();
    pad_ = dry_delay_ - s.latency;
    if (pad_ < 0) pad_ = 0;
    ring_pos_ = wet_pos_ = 0;
    for (auto& ch : ring_)
        for (double& v : ch) v = 0.0;
    for (auto& ch : wet_)
        for (double& v : ch) v = 0.0;
}

void Engine::begin_chunk() noexcept {
    if (reset_req_.exchange(false, std::memory_order_relaxed)) reset_audio_state();

    // slot switch (quality, valve): idle -> fade_out -> (swap at the block boundary) -> fade_in
    if (sw_ == Switch::idle) {
        const int p = pending_.load(std::memory_order_acquire);
        if (p >= 0 && p != active_) {
            sw_ = Switch::fade_out;
            sw_env_.set(0.0, switch_len_);
        }
    }
    if (sw_ == Switch::fade_out && !sw_env_.moving()) {
        const int p = pending_.load(std::memory_order_acquire);
        adopt(p);
        pending_.store(-1, std::memory_order_relaxed);
        audio_slot_.store(active_, std::memory_order_release);  // ack: the old slot is free for the worker
        sw_ = Switch::fade_in;
        sw_env_.set(1.0, switch_len_);
    } else if (sw_ == Switch::fade_in && !sw_env_.moving()) {
        sw_ = Switch::idle;
    }

    Slot& s = *slot_[active_];
    if (s.cfg.valve == k12AX7) {
        // bias (#73): the target goes straight to the running stage, which ramps along its bias line per base sample
        // on this thread (no rebuild, no lock); read here, once per chunk, like the gain targets. The setter clamps.
        const double b = bias_t_.load(std::memory_order_relaxed);
        s.tri.set_bias(b);
        applied_bias_.store(b, std::memory_order_relaxed);
    } else {
        // #115: the 6AS6 mode, bias and post-filter targets go straight to the running stages, which move there on this
        // thread (LiveStage: a mode switch, a bias ramp, the core's 15 ms filter crossfade, #103); read here once per
        // chunk, like the gain targets. The bias is the requested one: each mode clamps it to its own range (T: the cap).
        const auto f = dk6as6::Lpf(filter6_t_.load(std::memory_order_relaxed));
        const auto m = dk6as6::Mode(mode6_t_.load(std::memory_order_relaxed));
        const double b = bias6_t_.load(std::memory_order_relaxed);
        for (auto& stage : s.pen) {
            stage.set_lpf(f);
            stage.set_mode(m);
            stage.set_bias(b);
        }
    }
    drive_.set(drive_t_.load(std::memory_order_relaxed) * kVoltsPerFullScale, ramp_len_);
    output_.set(output_t_.load(std::memory_order_relaxed) * kFullScalePerVolt, ramp_len_);
    const bool byp = bypass_t_.load(std::memory_order_relaxed) || host_bypass_t_.load(std::memory_order_relaxed);
    bypass_.set(byp ? 1.0 : 0.0, bypass_len_);
}

void Engine::process(const float* const* in, float* const* out, int nch, int n) noexcept {
    if (nch > kMaxChannels) nch = kMaxChannels;
    if (nch < 1 || n <= 0) return;
    if (!prepared_.load(std::memory_order_acquire)) {
        for (int c = 0; c < nch; ++c)
            for (int i = 0; i < n; ++i) out[c][i] = 0.0f;
        return;
    }
    dkcore::ScopedFlushDenormals ftz;
    for (int off = 0; off < n; off += kChunk) {
        const int len = n - off < kChunk ? n - off : kChunk;
        process_chunk(in, out, nch, off, len);
    }
}

void Engine::process_chunk(const float* const* in, float* const* out, int nch, int off, int n) noexcept {
    begin_chunk();
    const float* inL = in[0] + off;
    const float* inR = (nch > 1 ? in[1] : in[0]) + off;
    constexpr std::uint32_t mask = kRing - 1;
    static_assert((kRing & (kRing - 1)) == 0, "ring size is a power of two");

    // 1. read the whole chunk of input first (in and out may alias): dry ring and the driven stage input
    for (int i = 0; i < n; ++i) {
        const double xl = double(inL[i]), xr = double(inR[i]);
        const std::uint32_t w = (ring_pos_ + std::uint32_t(i)) & mask;
        ring_[0][w] = std::isfinite(xl) ? xl : 0.0;
        ring_[1][w] = std::isfinite(xr) ? xr : 0.0;
        const double d = drive_.next();
        bufL_[i] = xl * d;                              // NaN / inf stay non-finite: the cores replace them by 0
        bufR_[i] = xr * d;
    }

    // 2. the stage (core drive and output are 1.0); in place on the scratch buffers. The 6AS6 path has one stage per
    // channel: mono runs the left one only.
    Slot& s = *slot_[active_];
    if (s.cfg.valve == k12AX7) {
        s.tri.process(bufL_, bufR_, bufL_, bufR_, std::size_t(n));
    } else {
        s.pen[0].process(bufL_, bufL_, std::size_t(n));
        if (nch > 1) s.pen[1].process(bufR_, bufR_, std::size_t(n));
        publish_6as6(s);
    }
    // 3. output gain, on the stage's own timeline (as before the pad existed: an Output move lines up with the same
    // stage samples), then the pad from the stage's latency up to the reported one: an exact integer delay
    for (int i = 0; i < n; ++i) {
        const double g = output_.next();
        bufL_[i] *= g;
        bufR_[i] *= g;
    }
    if (pad_ > 0) {
        for (int i = 0; i < n; ++i) {
            const std::uint32_t w = (wet_pos_ + std::uint32_t(i)) & mask, r = (w - std::uint32_t(pad_)) & mask;
            wet_[0][w] = bufL_[i];
            wet_[1][w] = bufR_[i];
            bufL_[i] = wet_[0][r];
            bufR_[i] = wet_[1][r];
        }
        wet_pos_ = (wet_pos_ + std::uint32_t(n)) & mask;
    }

    // 4. bypass crossfade (against the dry input delayed by the reported latency), switch envelope
    float* outL = out[0] + off;
    float* outR = nch > 1 ? out[1] + off : nullptr;
    const bool plain = sw_ == Switch::idle && !bypass_.moving() && bypass_.cur == 0.0;
    if (plain) {
        for (int i = 0; i < n; ++i) {
            outL[i] = float(bufL_[i]);
            if (outR) outR[i] = float(bufR_[i]);
        }
    } else {
        for (int i = 0; i < n; ++i) {
            const double b = bypass_.next(), e = sw_env_.next();
            const std::uint32_t r = (ring_pos_ + std::uint32_t(i) - std::uint32_t(dry_delay_)) & mask;
            outL[i] = float(((1.0 - b) * bufL_[i] + b * ring_[0][r]) * e);
            if (outR) outR[i] = float(((1.0 - b) * bufR_[i] + b * ring_[1][r]) * e);
        }
    }
    ring_pos_ = (ring_pos_ + std::uint32_t(n)) & mask;
}

// ---- worker thread -----------------------------------------------------------------------------------------------

void Engine::start_worker() {
    {
        std::lock_guard<std::mutex> lk(wmu_);
        wstop_ = false;
    }
    worker_ = std::thread([this] { worker_main(); });
}

void Engine::stop_worker() {
    if (!worker_.joinable()) return;
    {
        std::lock_guard<std::mutex> lk(wmu_);
        wstop_ = true;
    }
    wcv_.notify_all();
    worker_.join();
}

void Engine::worker_main() {
    std::unique_lock<std::mutex> lk(wmu_);
    while (!wstop_) {
        lk.unlock();
        worker_poll();
        lk.lock();
        wcv_.wait_for(lk, kWorkerPeriod, [this] { return wstop_; });
    }
}

void Engine::worker_poll() {
    // Wait while the audio thread has not adopted the last prepared slot: the idle slot is not known to be free.
    if (pending_.load(std::memory_order_acquire) >= 0) return;
    if (audio_slot_.load(std::memory_order_acquire) != designated_) return;

    const Config t = target_config();
    if (t.same_build(des_cfg_)) return;                 // a 6AS6 mode or bias change needs no build (#115)
    des_cfg_ = t;                                       // also on failure: do not retry the same request every poll
    if (t.same_build(slot_[designated_]->cfg)) return;  // back to what is running (e.g. after a failed build)
    const int idle = 1 - designated_;                   // not processed: the audio thread acknowledged leaving it
    const int st = build(*slot_[idle], t, bias_t_.load(std::memory_order_relaxed));
    build_status_.store(st, std::memory_order_relaxed);
    build_status_valve_.store(t.valve, std::memory_order_relaxed);
    if (st != 0) return;
    designated_ = idle;
    pending_.store(idle, std::memory_order_release);
    // The 12AX7 bias and the 6AS6 bias, mode and filter need no worker: the audio thread passes them to the running stage.
}

}  // namespace mapvs
