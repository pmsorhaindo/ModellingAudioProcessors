/* alloc_hooks.h -- allocation counting for the core tests (cpp/test_core.cpp, cpp/test_core_6as6.cpp). Include it in
 * exactly one translation unit (it defines the global allocation functions).
 *
 * It replaces global operator new / delete (every form) and, on glibc, interposes malloc / calloc / realloc / free /
 * memalign (forwarding to glibc's __libc_* entry points). Both count while g_count is set (CountAllocs). Build with
 * -DDKCORE_NO_ALLOC_HOOKS for sanitizer builds (they intercept the allocator themselves); ALLOC_HOOKS is then false. */
#ifndef DKCORE_ALLOC_HOOKS_H
#define DKCORE_ALLOC_HOOKS_H

#include <atomic>
#include <cstddef>
#include <cstdlib>
#include <new>

static std::atomic<bool> g_count{false};
static std::atomic<long> g_allocs{0};

#if !defined(DKCORE_NO_ALLOC_HOOKS)
#if defined(__GLIBC__)
extern "C" {
void* __libc_malloc(size_t);
void* __libc_calloc(size_t, size_t);
void* __libc_realloc(void*, size_t);
void __libc_free(void*);
void* __libc_memalign(size_t, size_t);
void* malloc(size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_malloc(n); }
void* calloc(size_t a, size_t b) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_calloc(a, b); }
void* realloc(void* p, size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_realloc(p, n); }
void free(void* p) { if (p && g_count.load(std::memory_order_relaxed)) ++g_allocs; __libc_free(p); }
void* memalign(size_t a, size_t n) { if (g_count.load(std::memory_order_relaxed)) ++g_allocs; return __libc_memalign(a, n); }
void* aligned_alloc(size_t a, size_t n) { return memalign(a, n); }
int posix_memalign(void** p, size_t a, size_t n) { *p = memalign(a, n); return *p ? 0 : 12; }
}
#define DK_RAW_ALLOC(n) __libc_malloc(n)
#define DK_RAW_ALIGNED(a, n) __libc_memalign(a, n)
#define DK_RAW_FREE(p) __libc_free(p)
#else
#define DK_RAW_ALLOC(n) std::malloc(n)
#define DK_RAW_ALIGNED(a, n) std::aligned_alloc(a, ((n) + (a) - 1) / (a) * (a))
#define DK_RAW_FREE(p) std::free(p)
#endif
static void* dk_new(size_t n) {
    if (g_count.load(std::memory_order_relaxed)) ++g_allocs;
    void* p = DK_RAW_ALLOC(n ? n : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
static void* dk_new_al(size_t n, std::align_val_t a) {
    if (g_count.load(std::memory_order_relaxed)) ++g_allocs;
    void* p = DK_RAW_ALIGNED(size_t(a), n ? n : 1);
    if (!p) throw std::bad_alloc();
    return p;
}
static void dk_del(void* p) {
    if (p && g_count.load(std::memory_order_relaxed)) ++g_allocs;
    DK_RAW_FREE(p);
}
void* operator new(size_t n) { return dk_new(n); }
void* operator new[](size_t n) { return dk_new(n); }
void* operator new(size_t n, const std::nothrow_t&) noexcept { try { return dk_new(n); } catch (...) { return nullptr; } }
void* operator new[](size_t n, const std::nothrow_t&) noexcept { try { return dk_new(n); } catch (...) { return nullptr; } }
void* operator new(size_t n, std::align_val_t a) { return dk_new_al(n, a); }
void* operator new[](size_t n, std::align_val_t a) { return dk_new_al(n, a); }
void operator delete(void* p) noexcept { dk_del(p); }
void operator delete[](void* p) noexcept { dk_del(p); }
void operator delete(void* p, size_t) noexcept { dk_del(p); }
void operator delete[](void* p, size_t) noexcept { dk_del(p); }
void operator delete(void* p, std::align_val_t) noexcept { dk_del(p); }
void operator delete[](void* p, std::align_val_t) noexcept { dk_del(p); }
void operator delete(void* p, size_t, std::align_val_t) noexcept { dk_del(p); }
void operator delete[](void* p, size_t, std::align_val_t) noexcept { dk_del(p); }
static constexpr bool ALLOC_HOOKS = true;
#else
static constexpr bool ALLOC_HOOKS = false;
#endif

struct CountAllocs {          // counts allocations in its scope (single-threaded sections only)
    long start;
    CountAllocs() : start(g_allocs.load()) { g_count = true; }
    ~CountAllocs() { g_count = false; }
    long n() const { return g_allocs.load() - start; }
};

#endif
