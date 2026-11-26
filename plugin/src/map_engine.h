/* map_engine.h -- the realtime engine shared by the AU and VST 3 wrappers (#22, #114).
 *
 * Two valve paths (#114): the 6AS6 DK stage (dk6as6::Stage, one per channel; Gyre's v1 stage, #104) and the 12AX7 DK
 * stage (dkcore::StereoStage, #75 option A). The Valve parameter picks one. The wrappers only translate host calls;
 * everything with realtime rules lives here. Design: plugin/README.md, "Valve paths".
 *
 * Threads
 *   prepare(), release()          non-RT, never concurrently with process() (VST 3 setActive, AU Initialize/Cleanup).
 *   set_*(), request_reset()      any thread, including the audio thread (relaxed atomics; values are sanitized).
 *   latency_samples() etc.        any thread.
 *   process()                     the audio thread. No allocation, no locks, no syscalls. Holds ScopedFlushDenormals.
 *   worker thread (own)           started by prepare(), joined by release(). It runs what may not run on the audio
 *                                 thread: a stage's prepare() (matrix build, DC solve; the 6AS6 Rk solve).
 *
 * Slots. prepare() of a stage must not run while that stage is processed, so the engine owns two slots. Each slot
 * holds both kinds of stage (a StereoStage and two dk6as6::Stage), allocated once in the constructor, and the
 * configuration it was last built for (valve, quality, and on the 6AS6 path mode and bias). The worker builds the idle
 * slot whenever the requested configuration differs from the running one, and publishes its index with a release
 * store. The audio thread fades the output out (5 ms), switches slots at a block boundary, acknowledges, and fades
 * back in (5 ms). The worker only touches a slot after the audio thread has acknowledged that it left it. The same
 * mechanism carries a quality change, a valve change, and a 6AS6 mode or bias change (the 6AS6 core fixes mode and
 * Rk for a prepared stage; smoothing those is #115). Nothing on that path allocates, locks or waits on the audio thread.
 *
 * Applied live by the audio thread, once per chunk, without the worker: Drive, Output, Bypass (both paths), the
 * 12AX7 Bias (StereoStage::set_bias, the core's 20 ms ramp, #73) and the 6AS6 Filter (Stage::set_lpf, the core's 15 ms
 * crossfade, #103).
 *
 * Drive and Output: linear ramps over 20 ms in the wrapper, applied per sample at the host rate. Drive is applied to
 * the input before the stage, so before its upsampler (the drive-aliasing check in tests/smoothing_test.cpp). The
 * cores' own drive and output stay 1.0, so the host-to-volts mapping (map_params.h) is folded into the ramp targets,
 * in one place, and is the same for both paths.
 *
 * Bypass (parameter or host bypass): a 10 ms crossfade to the dry input delayed by the active latency, so the
 * bypassed signal lines up with the reported latency. The stage keeps running while bypassed.
 */
#ifndef MAP_ENGINE_H
#define MAP_ENGINE_H

#include "dk6as6_core.h"   // includes dk_core.h
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
    static constexpr int kRing = 512;           // dry delay ring, >= kChunk + max latency (130 at 44.1 kHz, 6AS6 HQ)
    static constexpr int kMaxChannels = 2;

    // What a slot is built for. On the 12AX7 path mode6 and bias6 are not used (0); on the 6AS6 path quality is never
    // Eco (quality_6as6) and bias6 is bias6_effective(). Two equal configs build the same stages.
    struct Config {
        int valve = kValveDefault, quality = kQualityDefault, mode6 = kMode6Default;
        double bias6 = kBias6Range.def;
        bool operator==(const Config& o) const {
            return valve == o.valve && quality == o.quality && mode6 == o.mode6 && bias6 == o.bias6;
        }
        bool operator!=(const Config& o) const { return !(*this == o); }
    };

    Engine();
    ~Engine();
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    // ---- non-RT, never concurrently with process() ----------------------------------------------------------
    // Prepare at the host's sample rate with the current parameter targets (no ramps: the gains start at target).
    // If the requested setting does not build, the path's default setting is tried, and if the 6AS6 path does not
    // build at this rate at all, the 12AX7 path's (active_valve() tells). Returns false if nothing builds (process()
    // then outputs silence).
    bool prepare(double sample_rate);
    void release();                                     // stops the worker; process() outputs silence afterwards
    // Called by the worker thread after the audio thread adopted a slot with a different latency (AU uses it to
    // send kAudioUnitProperty_Latency). Set before prepare().
    void set_latency_listener(std::function<void()> f) { latency_listener_ = std::move(f); }

    // ---- any thread -------------------------------------------------------------------------------------------
    // Plain values. NaN is ignored (the last value stays); anything else is clamped to the parameter's range.
    void set_drive(double g) { store(drive_t_, kDriveRange, g); }
    void set_bias(double b) { store(bias_t_, kBiasRange, b); }                // 12AX7 path, x Rk
    void set_output(double g) { store(output_t_, kOutputRange, g); }
    void set_quality(int q) { quality_t_.store(clamp_quality(q), std::memory_order_relaxed); }
    void set_valve(int v) { valve_t_.store(clamp_index(v, kNumValves), std::memory_order_relaxed); }
    void set_bias6(double ma) { store(bias6_t_, kBias6Range, ma); }           // 6AS6 path, mA (T capped at build)
    void set_mode6(int m) { mode6_t_.store(clamp_index(m, kNumModes6), std::memory_order_relaxed); }
    void set_filter6(int f) { filter6_t_.store(clamp_index(f, kNumFilters6), std::memory_order_relaxed); }
    void set_bypass(bool b) { bypass_t_.store(b, std::memory_order_relaxed); }
    void set_host_bypass(bool b) { host_bypass_t_.store(b, std::memory_order_relaxed); }
    void request_reset() { reset_req_.store(true, std::memory_order_relaxed); }

    int quality_target() const { return quality_t_.load(std::memory_order_relaxed); }
    int valve_target() const { return valve_t_.load(std::memory_order_relaxed); }
    Config target_config() const;                       // what the worker builds for the current targets
    // latency of the requested valve and quality (what the host should compensate once the switch is done)
    int latency_samples() const {
        return target_latency_[valve_target()][quality_target()].load(std::memory_order_relaxed);
    }
    // the running slot: latency, valve, quality (6AS6: Eco reads Standard), 6AS6 mode and bias as built (mA), and
    // whether it is outside the supported envelope (only P2 can be: experimental)
    int active_latency_samples() const { return active_latency_.load(std::memory_order_relaxed); }
    int active_valve() const { return active_valve_.load(std::memory_order_relaxed); }
    int active_quality() const { return active_quality_.load(std::memory_order_relaxed); }
    int active_mode6() const { return active_mode6_.load(std::memory_order_relaxed); }
    double active_bias6() const { return active_bias6_.load(std::memory_order_relaxed); }
    bool active_experimental() const { return active_experimental_.load(std::memory_order_relaxed); }
    double sample_rate() const { return fs_; }
    bool prepared() const { return prepared_.load(std::memory_order_relaxed); }
    std::uint64_t nonconverged_total() const;
    // status of the last stage build (0 = ok; else the core's dkcore::BuildStatus or dk6as6::Status as int, for the
    // valve of build_status_valve()), and the 12AX7 bias target the audio thread last passed to the running stage
    int build_status() const { return build_status_.load(std::memory_order_relaxed); }
    int build_status_valve() const { return build_status_valve_.load(std::memory_order_relaxed); }
    int bias_status() const { return build_status(); }  // the pre-#114 name
    double applied_bias() const { return applied_bias_.load(std::memory_order_relaxed); }
    static int latency_for(int valve, int quality, double sample_rate);
    static int latency_for(int quality, double sample_rate) { return latency_for(k12AX7, quality, sample_rate); }
    static dkcore::Quality core_quality(int quality);               // 12AX7
    static dkcore::Quality core_quality_6as6(int quality);          // 6AS6: STANDARD or HQ
    // the 6AS6 settings the engine builds for a config and filter position (also what the tests' reference uses)
    static dk6as6::Settings settings_6as6(const Config& c, int filter6);

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

    // Both stage kinds, preallocated; cfg / latency / experimental are written by the worker (or prepare()) before
    // the slot is published, and read by the audio thread after it adopts it.
    struct Slot {
        dkcore::StereoStage tri;                        // 12AX7, both channels
        dk6as6::Stage pen[kMaxChannels];                // 6AS6, one per channel
        Config cfg;
        int latency = 0;
        bool experimental = false;
    };

    static void store(std::atomic<double>& a, const Range& r, double v) {
        if (!std::isnan(v)) a.store(clamp_range(r, v), std::memory_order_relaxed);
    }
    // non-RT: build slot s for c (the 12AX7 path at bias12); 0 on success, else the core's status
    int build(Slot& s, const Config& c, double bias12);
    void process_chunk(const float* const* in, float* const* out, int nch, int off, int n) noexcept;
    void begin_chunk() noexcept;
    void adopt(int slot) noexcept;
    void reset_audio_state() noexcept;
    void start_worker();
    void stop_worker();
    void worker_main();
    void worker_poll();

    std::unique_ptr<Slot> slot_[2];

    // targets (any thread)
    std::atomic<double> drive_t_{kDriveRange.def}, bias_t_{kBiasRange.def}, output_t_{kOutputRange.def},
        bias6_t_{kBias6Range.def};
    std::atomic<int> quality_t_{kQualityDefault}, valve_t_{kValveDefault}, mode6_t_{kMode6Default},
        filter6_t_{kFilter6Default};
    std::atomic<bool> bypass_t_{false}, host_bypass_t_{false}, reset_req_{false};

    // worker <-> audio thread handshake
    std::atomic<int> pending_{-1};                      // worker -> audio: slot prepared, switch to it
    std::atomic<int> audio_slot_{0};                    // audio -> worker: the slot the audio thread is using
    std::atomic<int> active_latency_{0}, active_valve_{kValveDefault}, active_quality_{kQualityDefault},
        active_mode6_{kMode6Default};
    std::atomic<double> active_bias6_{kBias6Range.def};
    std::atomic<bool> active_experimental_{false};
    std::atomic<int> target_latency_[kNumValves][kNumQualities]{};   // populated off the audio thread
    std::atomic<int> build_status_{0}, build_status_valve_{kValveDefault};
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
    int designated_ = 0;
    Config des_cfg_;                                    // the config last requested of a build (also if it failed)
    int notified_latency_ = 0;
    std::function<void()> latency_listener_;
    std::thread worker_;
    std::mutex wmu_;
    std::condition_variable wcv_;
    bool wstop_ = false;
};

}  // namespace mapvs
#endif
