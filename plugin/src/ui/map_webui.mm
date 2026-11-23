/* map_webui.mm -- WebEditor (map_webui.h): choc's WKWebView in an NSView, with the #105 key-focus policy.
 *
 * Objective-C++, manual reference counting (no ARC), main thread only except WebEditor::set_param().
 *
 * Key focus. choc creates its WKWebView subclass at run time (choc_WebView.h, "CHOCWebView_" + a unique suffix), one
 * class per binary. We add or replace these methods on that class, once, and look up the per-view state through an
 * associated object (nil once the WebEditor is gone, and then every method falls back to WebKit's behaviour):
 *   keyDown: keyUp: flagsChanged:     (a) no page text input focused -> straight to the next responder (the host)
 *   performKeyEquivalent:             (a) no page text input focused -> NO, so Cmd shortcuts reach the host's menus;
 *                                         otherwise choc's clipboard handling (Cmd C / X / V / Z / A)
 *   acceptsFirstMouse:                (b) YES: the first click on an inactive window acts
 *   acceptsFirstResponder / becomeFirstResponder
 *                                     (b) NO unless a page text input is focused
 *   resignFirstResponder              (c) if a text input had focus, blur it in the page
 * and (c): the page reports text-input focus (gyre_textFocus); the editor then makes the web view first responder,
 * and on blur hands first responder back to whatever had it before (or to the window).
 */
#import <Cocoa/Cocoa.h>

#include "map_webui.h"

#include "choc/gui/choc_WebView.h"

#include <objc/message.h>
#include <objc/runtime.h>

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>

namespace mapvs::ui {

namespace {
// process-wide, so the spike's choice survives closing and reopening the editor
std::atomic<int> g_focus_mode{int(kDefaultFocusMode)};
const char kStateKey = 0;           // associated-object key on the WKWebView
}

struct WebEditor::Impl {
    Callbacks cb;
    std::string format;
    NSView* container = nil;        // retained
    std::unique_ptr<choc::ui::WebView> web;
    NSView* wk = nil;               // the WKWebView (owned by `web`)

    // key-focus state (main thread)
    FocusMode mode = kDefaultFocusMode;
    bool text_focus = false;
    NSResponder* saved_responder = nil;   // retained while the page has text focus
    std::uint64_t forwarded = 0, forwarded_sent = ~std::uint64_t(0);

    // host -> page (set_param: any thread; tick: main thread)
    std::atomic<double> norm[kMaxParams];
    std::atomic<std::uint32_t> dirty{0};
    dispatch_source_t timer = nullptr;

    bool at_least(FocusMode m) const { return int(mode) >= int(m); }
    void tick();
    void page_text_focus(bool focused);
    void lost_first_responder();
    void restore_responder();
};

// ---- the WKWebView methods (installed once on choc's class) ---------------------------------------------------

namespace {
WebEditor::Impl* state_of(id self) { return (WebEditor::Impl*)objc_getAssociatedObject(self, &kStateKey); }

Class g_wk_super = nil;             // WKWebView: the superclass of choc's class
IMP g_choc_key_equivalent = nullptr, g_choc_first_mouse = nullptr;

template <typename R, typename... A>
R call_super(id self, SEL sel, A... a) {
    return ((R(*)(id, SEL, A...))class_getMethodImplementation(g_wk_super, sel))(self, sel, a...);
}

bool forward_keys(WebEditor::Impl* s) { return s && s->at_least(FocusMode::kForward) && !s->text_focus; }
bool refuse_focus(WebEditor::Impl* s) { return s && s->at_least(FocusMode::kNonActivating) && !s->text_focus; }

void wv_keyDown(id self, SEL sel, NSEvent* e) {
    if (auto* s = state_of(self); forward_keys(s)) { ++s->forwarded; [[(NSView*)self nextResponder] keyDown:e]; return; }
    call_super<void>(self, sel, e);
}
void wv_keyUp(id self, SEL sel, NSEvent* e) {
    if (auto* s = state_of(self); forward_keys(s)) { ++s->forwarded; [[(NSView*)self nextResponder] keyUp:e]; return; }
    call_super<void>(self, sel, e);
}
void wv_flagsChanged(id self, SEL sel, NSEvent* e) {
    if (auto* s = state_of(self); forward_keys(s)) { [[(NSView*)self nextResponder] flagsChanged:e]; return; }
    call_super<void>(self, sel, e);
}
BOOL wv_performKeyEquivalent(id self, SEL sel, NSEvent* e) {
    if (forward_keys(state_of(self))) return NO;
    return ((BOOL(*)(id, SEL, NSEvent*))g_choc_key_equivalent)(self, sel, e);
}
BOOL wv_acceptsFirstMouse(id self, SEL sel, NSEvent* e) {
    if (auto* s = state_of(self); s && s->at_least(FocusMode::kNonActivating)) return YES;
    return ((BOOL(*)(id, SEL, NSEvent*))g_choc_first_mouse)(self, sel, e);
}
BOOL wv_acceptsFirstResponder(id self, SEL sel) {
    if (refuse_focus(state_of(self))) return NO;
    return call_super<BOOL>(self, sel);
}
BOOL wv_becomeFirstResponder(id self, SEL sel) {
    if (refuse_focus(state_of(self))) return NO;
    return call_super<BOOL>(self, sel);
}
BOOL wv_resignFirstResponder(id self, SEL sel) {
    const BOOL r = call_super<BOOL>(self, sel);
    if (r)
        if (auto* s = state_of(self)) s->lost_first_responder();
    return r;
}

void install_methods(Class cls) {
    static Class done = nil;
    if (done == cls) return;
    done = cls;
    g_wk_super = class_getSuperclass(cls);
    // choc adds these two itself; keep its versions to call through
    g_choc_key_equivalent = class_getMethodImplementation(cls, @selector(performKeyEquivalent:));
    g_choc_first_mouse = class_getMethodImplementation(cls, @selector(acceptsFirstMouse:));
    auto put = [cls](SEL sel, IMP imp) {      // same type encoding as NSView's method
        class_replaceMethod(cls, sel, imp, method_getTypeEncoding(class_getInstanceMethod([NSView class], sel)));
    };
    put(@selector(performKeyEquivalent:), (IMP)wv_performKeyEquivalent);
    put(@selector(acceptsFirstMouse:), (IMP)wv_acceptsFirstMouse);
    put(@selector(keyDown:), (IMP)wv_keyDown);
    put(@selector(keyUp:), (IMP)wv_keyUp);
    put(@selector(flagsChanged:), (IMP)wv_flagsChanged);
    put(@selector(acceptsFirstResponder), (IMP)wv_acceptsFirstResponder);
    put(@selector(becomeFirstResponder), (IMP)wv_becomeFirstResponder);
    put(@selector(resignFirstResponder), (IMP)wv_resignFirstResponder);
}

double arg_number(const choc::value::ValueView& args, uint32_t i, double fallback) {
    if (!args.isArray() || i >= args.size()) return fallback;
    const auto v = args[i];
    if (v.isFloat() || v.isInt()) return v.get<double>();
    if (v.isBool()) return v.getBool() ? 1.0 : 0.0;
    return fallback;
}

std::optional<choc::ui::WebView::Options::Resource> fetch(const std::string& path) {
    const std::string p = path.empty() || path == "/" ? std::string("/index.html") : path;
    for (const Resource* r = kResources; r->path; ++r)
        if (p == r->path)
            return choc::ui::WebView::Options::Resource(std::string_view((const char*)r->data, r->size), r->mime);
    return std::nullopt;
}
}  // namespace

// ---- Impl ---------------------------------------------------------------------------------------------------

void WebEditor::Impl::tick() {
    std::string js;
    const std::uint32_t d = dirty.exchange(0, std::memory_order_acquire);
    if (d) {
        js += "\"p\":[";
        bool first = true;
        for (int i = 0; i < kMaxParams; ++i) {
            if (!(d & (1u << i))) continue;
            char buf[64];
            std::snprintf(buf, sizeof buf, "%s[%d,%.17g]", first ? "" : ",", i, norm[i].load(std::memory_order_relaxed));
            js += buf;
            first = false;
        }
        js += "]";
    }
    if (forwarded != forwarded_sent) {
        forwarded_sent = forwarded;
        char buf[96];
        std::snprintf(buf, sizeof buf, "%s\"keys\":{\"forwarded\":%llu,\"mode\":%d,\"text\":%s}", js.empty() ? "" : ",",
                      (unsigned long long)forwarded, int(mode), text_focus ? "true" : "false");
        js += buf;
    }
    if (js.empty() || !web) return;
    web->evaluateJavascript("window.gyre && gyre.onHost({" + js + "})");
}

void WebEditor::Impl::page_text_focus(bool focused) {
    if (focused == text_focus) return;
    text_focus = focused;
    forwarded_sent = ~std::uint64_t(0);     // report the new state on the next tick
    if (!at_least(FocusMode::kTextFocus)) return;
    NSWindow* w = [wk window];
    if (!w) return;
    if (focused) {
        NSResponder* prev = [w firstResponder];
        if (prev != wk) {
            [saved_responder release];
            saved_responder = [prev retain];
            [w makeFirstResponder:wk];
        }
    } else if ([w firstResponder] == wk) {
        restore_responder();
    }
}

void WebEditor::Impl::restore_responder() {
    NSWindow* w = [wk window];
    NSResponder* to = saved_responder;
    // only a view still in this window (or the window itself) gets first responder back
    if (to && [to isKindOfClass:[NSView class]] && [(NSView*)to window] != w) to = nil;
    if (to && [to isKindOfClass:[NSWindow class]] && to != w) to = nil;
    if (w) [w makeFirstResponder:to];         // nil: the window itself
    [saved_responder release];
    saved_responder = nil;
}

void WebEditor::Impl::lost_first_responder() {
    // the user clicked elsewhere in the window (or the host moved focus): drop the page's text focus too
    if (!text_focus) return;
    text_focus = false;
    forwarded_sent = ~std::uint64_t(0);
    [saved_responder release];
    saved_responder = nil;
    if (web) web->evaluateJavascript("window.gyre && gyre.blurText && gyre.blurText()");
}

// ---- WebEditor ----------------------------------------------------------------------------------------------

WebEditor::WebEditor(Callbacks cb, int w, int h, const char* format) : impl_(std::make_unique<Impl>()) {
    Impl& s = *impl_;
    s.cb = std::move(cb);
    s.format = format ? format : "";
    s.mode = FocusMode(g_focus_mode.load(std::memory_order_relaxed));
    for (auto& n : s.norm) n.store(0.0, std::memory_order_relaxed);

    s.container = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, w, h)];
    [s.container setWantsLayer:YES];
    // the plate colour under the page, so opening does not flash white before the first paint
    s.container.layer.backgroundColor = [[NSColor colorWithCalibratedRed:0.07 green:0.07 blue:0.075 alpha:1] CGColor];

    choc::ui::WebView::Options opt;
    opt.acceptsFirstMouseClick = true;
    opt.transparentBackground = true;
    opt.enableDebugMode = true;     // v0.1 test builds: right-click > Inspect Element (Safari's inspector)
    opt.customSchemeURI = "gyre://ui/";
    opt.fetchResource = fetch;
    // the page never navigates away from its own resources
    opt.shouldAllowRequest = [](const choc::ui::WebView::Options::Request& r) {
        return r.uri.rfind("gyre://", 0) == 0;
    };
    s.web = std::make_unique<choc::ui::WebView>(opt);
    s.wk = (NSView*)s.web->getViewHandle();
    if (!s.wk) return;              // loadedOK() false: an empty plate
    install_methods([s.wk class]);
    objc_setAssociatedObject(s.wk, &kStateKey, (id)&s, OBJC_ASSOCIATION_ASSIGN);
    [s.wk setFrame:[s.container bounds]];
    [s.wk setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
    [s.container addSubview:s.wk];

    Impl* sp = &s;
    s.web->addInitScript("window.gyreHostFormat = \"" + s.format + "\";");
    s.web->bind("gyre_begin", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        const int id = int(arg_number(a, 0, -1));
        if (id >= 0 && id < kMaxParams && sp->cb.begin_edit) sp->cb.begin_edit(id);
        return {};
    });
    s.web->bind("gyre_set", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        const int id = int(arg_number(a, 0, -1));
        double n = arg_number(a, 1, -1.0);
        if (id < 0 || id >= kMaxParams || !(n >= 0.0 && n <= 1.0)) return {};   // rejects NaN and out-of-range
        if (sp->cb.perform_edit) sp->cb.perform_edit(id, n);
        return {};
    });
    s.web->bind("gyre_end", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        const int id = int(arg_number(a, 0, -1));
        if (id >= 0 && id < kMaxParams && sp->cb.end_edit) sp->cb.end_edit(id);
        return {};
    });
    s.web->bind("gyre_ready", [sp](const choc::value::ValueView&) -> choc::value::Value {
        if (sp->cb.get_norm)
            for (int i = 0; i < kMaxParams; ++i) sp->norm[i].store(sp->cb.get_norm(i), std::memory_order_relaxed);
        sp->dirty.fetch_or(~0u, std::memory_order_release);
        sp->forwarded_sent = ~std::uint64_t(0);
        return {};
    });
    s.web->bind("gyre_textFocus", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        sp->page_text_focus(arg_number(a, 0, 0.0) != 0.0);
        return {};
    });
    s.web->bind("gyre_focusMode", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        int m = int(arg_number(a, 0, int(kDefaultFocusMode)));
        m = m < 0 ? 0 : (m > 3 ? 3 : m);
        if (sp->text_focus && sp->at_least(FocusMode::kTextFocus) && FocusMode(m) < FocusMode::kTextFocus)
            sp->restore_responder();
        sp->mode = FocusMode(m);
        g_focus_mode.store(m, std::memory_order_relaxed);
        sp->forwarded_sent = ~std::uint64_t(0);
        return {};
    });
    s.web->bind("gyre_resize", [sp](const choc::value::ValueView& a) -> choc::value::Value {
        const int rw = int(arg_number(a, 0, 0)), rh = int(arg_number(a, 1, 0));
        if (rw < 100 || rh < 100 || rw > 4000 || rh > 4000) return choc::value::Value(false);
        return choc::value::Value(sp->cb.request_resize ? sp->cb.request_resize(rw, rh) : false);
    });

    // ~30 Hz: host -> page. The main queue also runs during mouse tracking, so drags keep updating.
    s.timer = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
    dispatch_source_set_timer(s.timer, dispatch_time(DISPATCH_TIME_NOW, 33 * NSEC_PER_MSEC), 33 * NSEC_PER_MSEC,
                              5 * NSEC_PER_MSEC);
    dispatch_source_set_event_handler(s.timer, ^{ sp->tick(); });
    dispatch_resume(s.timer);
}

WebEditor::~WebEditor() {
    Impl& s = *impl_;
    if (s.timer) {
        dispatch_source_cancel(s.timer);    // main thread: no handler is running, and none starts after this
        dispatch_release(s.timer);
    }
    if (s.wk) {
        if (s.text_focus) {
            NSWindow* w = [s.wk window];
            if (w && [w firstResponder] == s.wk) [w makeFirstResponder:nil];
        }
        objc_setAssociatedObject(s.wk, &kStateKey, nil, OBJC_ASSOCIATION_ASSIGN);
        [s.wk removeFromSuperview];
    }
    [s.saved_responder release];
    s.web.reset();
    [s.container removeFromSuperview];
    [s.container release];
}

void* WebEditor::view() const { return (void*)impl_->container; }

void WebEditor::attach(void* parent) {
    NSView* p = (NSView*)parent;
    [impl_->container setFrameOrigin:NSZeroPoint];
    [p addSubview:impl_->container];
}

void WebEditor::set_size(int w, int h) { [impl_->container setFrameSize:NSMakeSize(w, h)]; }

void WebEditor::set_param(int id, double norm) noexcept {
    if (id < 0 || id >= kMaxParams) return;
    norm = std::isfinite(norm) ? (norm < 0.0 ? 0.0 : (norm > 1.0 ? 1.0 : norm)) : 0.0;   // NaN / Inf never reach JSON
    impl_->norm[id].store(norm, std::memory_order_relaxed);
    impl_->dirty.fetch_or(1u << id, std::memory_order_release);
}

}  // namespace mapvs::ui
