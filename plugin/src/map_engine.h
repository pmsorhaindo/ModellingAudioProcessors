/* map_engine.h -- the realtime engine shared by the AU and VST 3 wrappers (#22).
 *
 * Wraps dkcore::StereoStage (prototypes/fullstage/cpp/dk_core.h, #75 option A: the validated 12AX7 stage). The
 * wrappers only translate host calls; everything with realtime rules lives here.
 *
 * Threads
 *   prepare(), release()          non-RT, never concurrently with process() (VST 3 setActive, AU Initialize/Cleanup).
 *   set_*(), request_reset()      any thread, including the audio thread (relaxed atomics).
 *   latency_samples() etc.        any thread.
 *   process()                     the audio thread. No allocation, no locks, no syscalls. Holds ScopedFlushDenormals.
 *   worker thread (own)           started by prepare(), joined by release(). It runs everything that may not run on
 *                                 the audio thread: StereoStage::request_bias() (mutex + matrix build) and
 *                                 StereoStage::prepare() for a quality change.
 *
 * Quality change. prepare() of a stage must not run while that stage is processed. The engine owns two stages
 * ("slots"). The worker prepares the idle slot at the new quality and publishes its index with a release store; the
 * audio thread fades the output out (5 ms), switches slots at a block boundary, acknowledges, and fades back in
 * (5 ms). The worker only touches a slot after the audio thread has acknowledged that it left it.
 *
 * Bias. The audio thread (or any thread) stores the target; the worker (polling every 5 ms) calls request_bias() on
 * the active slot, and the core adopts the published pack at the next block, as it already does (no smoothing of
 * the bias itself: that is #73).
 *
 * Drive and Output: linear ramps over 20 ms in the wrapper, applied per sample. The core's own drive and output
 * stay 1.0, so the host-to-volts mapping (map_params.h) is folded into the ramp targets, in one place.
 *
 * Bypass (parameter or host bypass): a 10 ms crossfade to the dry input delayed by the active latency, so the
 * bypassed signal lines up with the reported latency. The stage keeps running while bypassed.
 */
#ifndef MAP_ENGINE_H
#define MAP_ENGINE_H

#include "dk_core.h"
#include "map_params.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <thread>

namespace mapvs {

class Engine {
public:
    static constexpr int kChunk = 256;          // internal block: the scratch buffers live in the object
    static constexpr int kRing = 512;           // dry delay ring, >= kChunk + max latency (73 at hq)
    static constexpr int kMaxChannels = 2;

    Engine();
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    // ---- non-RT, never concurrently with process() ----------------------------------------------------------
    // Prepare at the host's sample rate with the current parameter targets (no ramps: the gains start at target).
    // Returns false if the stage could not be built (process() then outputs silence).
    bool prepare(double sample_rate);
    void release();                                     // stops the worker; process() outputs silence afterwards
    // Called by the worker thread after the audio thread adopted a slot with a different latency (AU uses it to
    // send kAudioUnitProperty_Latency). Set before prepare().
    void set_latency_listener(std::function<void()> f) { latency_listener_ = std::move(f); }

    // ---- any thread -------------------------------------------------------------------------------------------
    void set_drive(double g) { drive_t_.store(g, std::memory_order_relaxed); }
    void set_bias(double b) { bias_t_.store(b, std::memory_order_relaxed); }
    void set_output(double g) { output_t_.store(g, std::memory_order_relaxed); }
    void set_quality(int q) { quality_t_.store(clamp_quality(q), std::memory_order_relaxed); }
    void set_bypass(bool b) { bypass_t_.store(b, std::memory_order_relaxed); }
    void set_host_bypass(bool b) { host_bypass_t_.store(b, std::memory_order_relaxed); }
    void request_reset() { reset_req_.store(true, std::memory_order_relaxed); }

    int quality_target() const { return quality_t_.load(std::memory_order_relaxed); }
    // latency of the requested quality (what the host should compensate once the switch is done)
    int latency_samples() const { return latency_for(quality_target()); }
    // latency of the slot the audio thread is running now
    int active_latency_samples() const { return active_latency_.load(std::memory_order_relaxed); }
    int active_quality() const { return active_quality_.load(std::memory_order_relaxed); }
    double sample_rate() const { return fs_; }
    bool prepared() const { return prepared_.load(std::memory_order_relaxed); }
    std::uint64_t nonconverged_total() const;
    // last request_bias() status (dkcore::BuildStatus as int; 0 = ok) and the bias the worker last applied
    int bias_status() const { return bias_status_.load(std::memory_order_relaxed); }
    double applied_bias() const { return applied_bias_.load(std::memory_order_relaxed); }
    static int latency_for(int quality);
    static dkcore::Quality core_quality(int quality);

    // ---- audio thread -----------------------------------------------------------------------------------------
    // in[c], out[c] for c < nch (1 or 2; more are ignored). in and out may alias channel by channel. Any n.
    void process(const float* const* in, float* const* out, int nch, int n) noexcept;

private:
    struct Ramp {                                       // linear ramp that lands exactly on its target
        double cur = 0.0, target = 0.0, step = 0.0;
        int left = 0;
        void snap(double v) { cur = target = v; step = 0.0; left = 0; }
        void set(double t, int len) {
            if (t == target) return;
            target = t;
            if (len <= 0) { snap(t); return; }
            left = len;
            step = (t - cur) / double(len);
        }
        double next() {
            if (left > 0) {
                if (--left == 0) cur = target;
                else cur += step;
            }
            return cur;
        }
        bool moving() const { return left > 0; }
    };
    enum class Switch : std::uint8_t { idle, fade_out, fade_in };

    void process_chunk(const float* const* in, float* const* out, int nch, int off, int n) noexcept;
    void begin_chunk() noexcept;
    void reset_audio_state() noexcept;
    void start_worker();
    void stop_worker();
    void worker_main();
    void worker_poll();

    std::unique_ptr<dkcore::StereoStage> slot_[2];

    // targets (any thread)
    std::atomic<double> drive_t_{kDriveRange.def}, bias_t_{kBiasRange.def}, output_t_{kOutputRange.def};
    std::atomic<int> quality_t_{kQualityDefault};
    std::atomic<bool> bypass_t_{false}, host_bypass_t_{false}, reset_req_{false};

    // worker <-> audio thread handshake
    std::atomic<int> pending_{-1};                      // worker -> audio: slot prepared, switch to it
    std::atomic<int> audio_slot_{0};                    // audio -> worker: the slot the audio thread is using
    std::atomic<int> active_latency_{0}, active_quality_{kQualityDefault};
    std::atomic<int> bias_status_{0};
    std::atomic<double> applied_bias_{kBiasRange.def};
    std::atomic<bool> prepared_{false};

    // audio-thread state
    int active_ = 0;
    Switch sw_ = Switch::idle;
    Ramp drive_, output_, bypass_, sw_env_;
    int ramp_len_ = 0, bypass_len_ = 0, switch_len_ = 0;
    int dry_delay_ = 0;
    std::uint32_t ring_pos_ = 0;
    double ring_[kMaxChannels][kRing] = {};
    double bufL_[kChunk] = {}, bufR_[kChunk] = {};

    // worker-side state (worker thread, or prepare() with the worker stopped)
    double fs_ = 0.0;
    int designated_ = 0, des_quality_ = kQualityDefault;
    double des_bias_ = kBiasRange.def;
    int notified_latency_ = 0;
    std::function<void()> latency_listener_;
    std::thread worker_;
    std::mutex wmu_;
    std::condition_variable wcv_;
    bool wstop_ = false;
};

}  // namespace mapvs
#endif
