/* map_engine.cpp -- see map_engine.h for the thread rules. */
#include "map_engine.h"

#include <chrono>
#include <cmath>

namespace mapvs {

namespace {
constexpr double kRampSeconds = 0.020, kBypassSeconds = 0.010, kSwitchSeconds = 0.005;
constexpr auto kWorkerPeriod = std::chrono::milliseconds(5);
int seconds_to_samples(double s, double fs) { return int(std::lround(s * fs)); }
}  // namespace

dkcore::Quality Engine::core_quality(int q) {
    switch (clamp_quality(q)) {
    case kEco: return dkcore::ECO;
    case kHQ: return dkcore::HQ;
    default: return dkcore::STANDARD;
    }
}

int Engine::latency_for(int q, double sample_rate) {
    // #96: the first FIR is longer at 44.1 kHz. Derive both tables from the core, once in the constructor.
    auto latency = [](int quality, double fs) {
        dkcore::Resampler r; r.prepare(core_quality(quality).os, fs); return r.latency_samples();
    };
    static const int table[2][kNumQualities] = {
        {latency(kEco, 48000.0), latency(kStandard, 48000.0), latency(kHQ, 48000.0)},
        {latency(kEco, 44100.0), latency(kStandard, 44100.0), latency(kHQ, 44100.0)},
    };
    return table[sample_rate == 44100.0 ? 1 : 0][clamp_quality(q)];
}

Engine::Engine() {
    slot_[0] = std::make_unique<dkcore::StereoStage>();
    slot_[1] = std::make_unique<dkcore::StereoStage>();
    for (auto& s : slot_) s->set_bias_range(kBiasRange.min, kBiasRange.max);   // the bias line (#73), per prepare
    (void)latency_for(kQualityDefault);                 // build the table here, not on the audio thread
    active_latency_.store(latency_for(kQualityDefault), std::memory_order_relaxed);
}

Engine::~Engine() { release(); }

bool Engine::prepare(double sample_rate) {
    stop_worker();
    prepared_.store(false, std::memory_order_relaxed);
    if (!(sample_rate > 0.0) || !std::isfinite(sample_rate)) return false;
    fs_ = sample_rate;
    latency_fs_.store(sample_rate, std::memory_order_relaxed);
    const int q = quality_target();
    double b = bias_t_.load(std::memory_order_relaxed);
    dkcore::BuildStatus st = slot_[0]->prepare(fs_, core_quality(q), b);
    if (st != dkcore::BuildStatus::ok) {                // e.g. a bias whose DC point does not solve: fall back to 1
        bias_status_.store(int(st), std::memory_order_relaxed);
        b = kBiasRange.def;
        st = slot_[0]->prepare(fs_, core_quality(q), b);
        if (st != dkcore::BuildStatus::ok) return false;
    }
    for (auto& s : slot_) {
        s->set_drive(1.0);                              // the wrapper applies drive and output
        s->set_output(1.0);
        s->set_bias_ramp(kRampSeconds);                 // the core ramps bias (#73), like the wrapper's gain ramps
    }
    active_ = designated_ = 0;
    des_quality_ = q;
    applied_bias_.store(b, std::memory_order_relaxed);
    pending_.store(-1, std::memory_order_relaxed);
    audio_slot_.store(0, std::memory_order_relaxed);
    active_quality_.store(q, std::memory_order_relaxed);
    active_latency_.store(slot_[0]->latency_samples(), std::memory_order_relaxed);
    notified_latency_ = slot_[0]->latency_samples();
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
    return slot_[audio_slot_.load(std::memory_order_relaxed)]->nonconverged_total();
}

// ---- audio thread ----------------------------------------------------------------------------------------------

void Engine::reset_audio_state() noexcept {
    slot_[active_]->reset();
    drive_.snap(drive_t_.load(std::memory_order_relaxed) * kVoltsPerFullScale);
    output_.snap(output_t_.load(std::memory_order_relaxed) * kFullScalePerVolt);
    const bool byp = bypass_t_.load(std::memory_order_relaxed) || host_bypass_t_.load(std::memory_order_relaxed);
    bypass_.snap(byp ? 1.0 : 0.0);
    sw_env_.snap(1.0);
    sw_ = Switch::idle;
    dry_delay_ = slot_[active_]->latency_samples();
    ring_pos_ = 0;
    for (auto& ch : ring_)
        for (double& v : ch) v = 0.0;
}

void Engine::begin_chunk() noexcept {
    if (reset_req_.exchange(false, std::memory_order_relaxed)) reset_audio_state();

    // quality switch: idle -> fade_out -> (swap at the block boundary) -> fade_in -> idle
    if (sw_ == Switch::idle) {
        const int p = pending_.load(std::memory_order_acquire);
        if (p >= 0 && p != active_) {
            sw_ = Switch::fade_out;
            sw_env_.set(0.0, switch_len_);
        }
    }
    if (sw_ == Switch::fade_out && !sw_env_.moving()) {
        const int p = pending_.load(std::memory_order_acquire);
        active_ = p;
        dry_delay_ = slot_[active_]->latency_samples();
        active_latency_.store(dry_delay_, std::memory_order_relaxed);
        active_quality_.store(-1, std::memory_order_relaxed);   // the worker knows the quality; see worker_poll
        pending_.store(-1, std::memory_order_relaxed);
        audio_slot_.store(active_, std::memory_order_release);  // ack: the old slot is free for the worker
        sw_ = Switch::fade_in;
        sw_env_.set(1.0, switch_len_);
    } else if (sw_ == Switch::fade_in && !sw_env_.moving()) {
        sw_ = Switch::idle;
    }

    // bias (#73): the target goes straight to the running stage, which ramps along its bias line per base sample on
    // this thread (no rebuild, no lock); read here, once per chunk, like the gain targets
    const double b = bias_t_.load(std::memory_order_relaxed);
    if (std::isfinite(b)) {
        const double bc = b < kBiasRange.min ? kBiasRange.min : (b > kBiasRange.max ? kBiasRange.max : b);
        slot_[active_]->set_bias(bc);
        applied_bias_.store(bc, std::memory_order_relaxed);
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
        bufL_[i] = xl * d;                              // NaN / inf stay non-finite: the core replaces them by 0
        bufR_[i] = xr * d;
    }

    // 2. the stage (core drive and output are 1.0); in place on the scratch buffers
    slot_[active_]->process(bufL_, bufR_, bufL_, bufR_, std::size_t(n));

    // 3. output gain, bypass crossfade, quality-switch envelope
    float* outL = out[0] + off;
    float* outR = nch > 1 ? out[1] + off : nullptr;
    const bool plain = sw_ == Switch::idle && !bypass_.moving() && bypass_.cur == 0.0;
    if (plain) {
        for (int i = 0; i < n; ++i) {
            const double g = output_.next();
            outL[i] = float(g * bufL_[i]);
            if (outR) outR[i] = float(g * bufR_[i]);
        }
    } else {
        for (int i = 0; i < n; ++i) {
            const double g = output_.next(), b = bypass_.next(), e = sw_env_.next();
            const std::uint32_t r = (ring_pos_ + std::uint32_t(i) - std::uint32_t(dry_delay_)) & mask;
            outL[i] = float(((1.0 - b) * (g * bufL_[i]) + b * ring_[0][r]) * e);
            if (outR) outR[i] = float(((1.0 - b) * (g * bufR_[i]) + b * ring_[1][r]) * e);
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

    // the audio thread runs designated_; tell listeners once its latency is in effect
    if (active_quality_.load(std::memory_order_relaxed) < 0) active_quality_.store(des_quality_, std::memory_order_relaxed);
    const int lat = active_latency_.load(std::memory_order_relaxed);
    if (lat != notified_latency_) {
        notified_latency_ = lat;
        if (latency_listener_) latency_listener_();
    }

    const int q = quality_target();
    const double b = bias_t_.load(std::memory_order_relaxed);
    if (q != des_quality_) {
        const int idle = 1 - designated_;               // not processed: the audio thread acknowledged leaving it
        const dkcore::BuildStatus st = slot_[idle]->prepare(fs_, core_quality(q), b);
        des_quality_ = q;                               // also on failure: do not retry the same request every poll
        if (st != dkcore::BuildStatus::ok) { bias_status_.store(int(st), std::memory_order_relaxed); return; }
        slot_[idle]->set_drive(1.0);
        slot_[idle]->set_output(1.0);
        designated_ = idle;
        bias_status_.store(0, std::memory_order_relaxed);
        pending_.store(idle, std::memory_order_release);
    }
    // Bias needs no worker since #73: the audio thread passes the target to the running stage (begin_chunk).
}

}  // namespace mapvs
