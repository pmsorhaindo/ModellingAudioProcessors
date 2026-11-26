/* rt_hooks.cpp -- a test-only dylib that counts locks, waits, allocations and common syscalls on one thread (#114).
 *
 * Linked into map_engine_test (never into a plugin). It interposes (dyld __DATA,__interpose, honoured for images
 * loaded at launch) the libSystem entry points below, so calls from anywhere in the process are seen: our code, the
 * cores, libc++ (std::mutex::lock is pthread_mutex_lock), libdispatch. While a thread has armed it (map_rt_arm), each
 * call made on that thread is counted and the last one's name kept. Calls from the hooks themselves go to the real
 * functions (dyld does not interpose an interposing image's own calls).
 *
 * What it cannot see: inline system calls that bypass libSystem's wrappers, and kernel waits taken through functions
 * not listed (e.g. __ulock_wait, which is private; the public lock functions that use it are listed). It is a
 * tripwire for the common cases, next to the allocation counter and the lock-free static_asserts in map_engine.cpp.
 */
#include <atomic>
#include <cstddef>
#include <cstdlib>

#include <dispatch/dispatch.h>
#include <fcntl.h>
#include <malloc/malloc.h>
#include <mach/mach.h>
#include <os/lock.h>
#include <poll.h>
#include <pthread.h>
#include <sched.h>
#include <semaphore.h>
#include <sys/event.h>
#include <sys/mman.h>
#include <sys/select.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>

namespace {
std::atomic<pthread_t> g_thread{nullptr};
std::atomic<long> g_count{0};
std::atomic<const char*> g_last{""};
inline void hit(const char* what) {
    if (pthread_self() == g_thread.load(std::memory_order_relaxed)) {
        g_count.fetch_add(1, std::memory_order_relaxed);
        g_last.store(what, std::memory_order_relaxed);
    }
}
}  // namespace

extern "C" {
__attribute__((visibility("default"))) void map_rt_arm(bool on) {
    g_thread.store(on ? pthread_self() : nullptr, std::memory_order_relaxed);
}
__attribute__((visibility("default"))) long map_rt_count() { return g_count.load(std::memory_order_relaxed); }
__attribute__((visibility("default"))) void map_rt_clear() {
    g_count.store(0, std::memory_order_relaxed);
    g_last.store("", std::memory_order_relaxed);
}
__attribute__((visibility("default"))) const char* map_rt_last() { return g_last.load(std::memory_order_relaxed); }
}

namespace {
// allocation
void* h_malloc(size_t n) { hit("malloc"); return malloc(n); }
void* h_calloc(size_t a, size_t b) { hit("calloc"); return calloc(a, b); }
void* h_realloc(void* p, size_t n) { hit("realloc"); return realloc(p, n); }
void h_free(void* p) { if (p) hit("free"); free(p); }
int h_posix_memalign(void** p, size_t a, size_t n) { hit("posix_memalign"); return posix_memalign(p, a, n); }
void* h_aligned_alloc(size_t a, size_t n) { hit("aligned_alloc"); return aligned_alloc(a, n); }
void* h_valloc(size_t n) { hit("valloc"); return valloc(n); }
void* h_zone_malloc(malloc_zone_t* z, size_t n) { hit("malloc_zone_malloc"); return malloc_zone_malloc(z, n); }
// locks and waits
int h_mutex_lock(pthread_mutex_t* m) { hit("pthread_mutex_lock"); return pthread_mutex_lock(m); }
int h_mutex_trylock(pthread_mutex_t* m) { hit("pthread_mutex_trylock"); return pthread_mutex_trylock(m); }
int h_rw_rd(pthread_rwlock_t* l) { hit("pthread_rwlock_rdlock"); return pthread_rwlock_rdlock(l); }
int h_rw_wr(pthread_rwlock_t* l) { hit("pthread_rwlock_wrlock"); return pthread_rwlock_wrlock(l); }
int h_rw_tryrd(pthread_rwlock_t* l) { hit("pthread_rwlock_tryrdlock"); return pthread_rwlock_tryrdlock(l); }
int h_rw_trywr(pthread_rwlock_t* l) { hit("pthread_rwlock_trywrlock"); return pthread_rwlock_trywrlock(l); }
int h_cond_wait(pthread_cond_t* c, pthread_mutex_t* m) { hit("pthread_cond_wait"); return pthread_cond_wait(c, m); }
int h_cond_timedwait(pthread_cond_t* c, pthread_mutex_t* m, const struct timespec* t) {
    hit("pthread_cond_timedwait");
    return pthread_cond_timedwait(c, m, t);
}
int h_join(pthread_t t, void** r) { hit("pthread_join"); return pthread_join(t, r); }
void h_unfair_lock(os_unfair_lock_t l) { hit("os_unfair_lock_lock"); os_unfair_lock_lock(l); }
bool h_unfair_trylock(os_unfair_lock_t l) { hit("os_unfair_lock_trylock"); return os_unfair_lock_trylock(l); }
long h_dsema_wait(dispatch_semaphore_t s, dispatch_time_t t) { hit("dispatch_semaphore_wait"); return dispatch_semaphore_wait(s, t); }
void h_dsync(dispatch_queue_t q, dispatch_block_t b) { hit("dispatch_sync"); dispatch_sync(q, b); }
void h_dsync_f(dispatch_queue_t q, void* c, dispatch_function_t f) { hit("dispatch_sync_f"); dispatch_sync_f(q, c, f); }
void h_dasync_f(dispatch_queue_t q, void* c, dispatch_function_t f) { hit("dispatch_async_f"); dispatch_async_f(q, c, f); }
int h_sem_wait(sem_t* s) { hit("sem_wait"); return sem_wait(s); }
kern_return_t h_msema_wait(semaphore_t s) { hit("semaphore_wait"); return semaphore_wait(s); }
// sleeps and syscalls
int h_nanosleep(const struct timespec* a, struct timespec* b) { hit("nanosleep"); return nanosleep(a, b); }
int h_usleep(useconds_t u) { hit("usleep"); return usleep(u); }
unsigned h_sleep(unsigned s) { hit("sleep"); return sleep(s); }
int h_yield() { hit("sched_yield"); return sched_yield(); }
ssize_t h_write(int fd, const void* b, size_t n) { hit("write"); return write(fd, b, n); }
ssize_t h_read(int fd, void* b, size_t n) { hit("read"); return read(fd, b, n); }
int h_close(int fd) { hit("close"); return close(fd); }
void* h_mmap(void* a, size_t n, int p, int f, int fd, off_t o) { hit("mmap"); return mmap(a, n, p, f, fd, o); }
int h_munmap(void* a, size_t n) { hit("munmap"); return munmap(a, n); }
int h_madvise(void* a, size_t n, int adv) { hit("madvise"); return madvise(a, n, adv); }
int h_poll(struct pollfd* f, nfds_t n, int t) { hit("poll"); return poll(f, n, t); }
int h_select(int n, fd_set* r, fd_set* w, fd_set* e, struct timeval* t) { hit("select"); return select(n, r, w, e, t); }
int h_kevent(int kq, const struct kevent* c, int nc, struct kevent* e, int ne, const struct timespec* t) {
    hit("kevent");
    return kevent(kq, c, nc, e, ne, t);
}
int h_sysctl(int* name, u_int nl, void* o, size_t* ol, void* n, size_t nn) { hit("sysctl"); return sysctl(name, nl, o, ol, n, nn); }
mach_msg_return_t h_mach_msg(mach_msg_header_t* m, mach_msg_option_t o, mach_msg_size_t s, mach_msg_size_t r,
                             mach_port_name_t p, mach_msg_timeout_t t, mach_port_name_t n) {
    hit("mach_msg");
    return mach_msg(m, o, s, r, p, t, n);
}
}  // namespace

#define MAP_INTERPOSE(repl, orig) {reinterpret_cast<const void*>(&repl), reinterpret_cast<const void*>(&orig)}
__attribute__((used)) static const struct { const void* replacement; const void* original; } kInterposers[]
    __attribute__((section("__DATA,__interpose"))) = {
        MAP_INTERPOSE(h_malloc, malloc), MAP_INTERPOSE(h_calloc, calloc), MAP_INTERPOSE(h_realloc, realloc),
        MAP_INTERPOSE(h_free, free), MAP_INTERPOSE(h_posix_memalign, posix_memalign),
        MAP_INTERPOSE(h_aligned_alloc, aligned_alloc), MAP_INTERPOSE(h_valloc, valloc),
        MAP_INTERPOSE(h_zone_malloc, malloc_zone_malloc),
        MAP_INTERPOSE(h_mutex_lock, pthread_mutex_lock), MAP_INTERPOSE(h_mutex_trylock, pthread_mutex_trylock),
        MAP_INTERPOSE(h_rw_rd, pthread_rwlock_rdlock), MAP_INTERPOSE(h_rw_wr, pthread_rwlock_wrlock),
        MAP_INTERPOSE(h_rw_tryrd, pthread_rwlock_tryrdlock), MAP_INTERPOSE(h_rw_trywr, pthread_rwlock_trywrlock),
        MAP_INTERPOSE(h_cond_wait, pthread_cond_wait), MAP_INTERPOSE(h_cond_timedwait, pthread_cond_timedwait),
        MAP_INTERPOSE(h_join, pthread_join), MAP_INTERPOSE(h_unfair_lock, os_unfair_lock_lock),
        MAP_INTERPOSE(h_unfair_trylock, os_unfair_lock_trylock),
        MAP_INTERPOSE(h_dsema_wait, dispatch_semaphore_wait), MAP_INTERPOSE(h_dsync, dispatch_sync),
        MAP_INTERPOSE(h_dsync_f, dispatch_sync_f), MAP_INTERPOSE(h_dasync_f, dispatch_async_f),
        MAP_INTERPOSE(h_sem_wait, sem_wait), MAP_INTERPOSE(h_msema_wait, semaphore_wait),
        MAP_INTERPOSE(h_nanosleep, nanosleep), MAP_INTERPOSE(h_usleep, usleep), MAP_INTERPOSE(h_sleep, sleep),
        MAP_INTERPOSE(h_yield, sched_yield), MAP_INTERPOSE(h_write, write), MAP_INTERPOSE(h_read, read),
        MAP_INTERPOSE(h_close, close), MAP_INTERPOSE(h_mmap, mmap), MAP_INTERPOSE(h_munmap, munmap),
        MAP_INTERPOSE(h_madvise, madvise), MAP_INTERPOSE(h_poll, poll), MAP_INTERPOSE(h_select, select),
        MAP_INTERPOSE(h_kevent, kevent), MAP_INTERPOSE(h_sysctl, sysctl), MAP_INTERPOSE(h_mach_msg, mach_msg),
};
