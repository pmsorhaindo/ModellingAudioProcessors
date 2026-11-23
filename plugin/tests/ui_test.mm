/* ui_test.mm -- the web editor in a test host window, through the built plugins (#105 key-focus spike).
 *
 * Not a DAW: a plain NSWindow whose content view stands in for the host (it records the key events that reach it).
 * The editors are the real ones, created the way a host creates them:
 *   VST 3  the .vst3 module (SDK hosting classes) -> IEditController::createView("editor") -> attached(NSView)
 *   AU     the installed component -> kAudioUnitProperty_CocoaUI -> the bundle's factory class -> NSView
 * Events are synthetic NSEvents sent through -[NSWindow sendEvent:] (and -performKeyEquivalent:). The window is
 * shown without activating the app, so nothing is taken from the user's keyboard focus.
 *
 * Checks
 *   1. page served from memory (document.title), host -> page parameter display, page drag -> host edit gesture
 *   2. per focus mode 0..3: after clicking the knob, a space bar press reaches the host view or not; Cmd-Z is or is
 *      not claimed by the web view; typing, arrows and Esc in the text field; focus handed back on Esc and on a click
 *      elsewhere. Modes 0..2 are reported (the baseline, to record which mitigations are needed); mode 3, the default,
 *      must pass every check.
 *   3. resize (page request -> host), and 20 open / close cycles: every WKWebView and editor view is deallocated
 *      (weak references), resident memory growth reported
 *
 *   map_ui_test [path.vst3] [--au] [--cycles 20]
 */
#import <AudioToolbox/AUCocoaUIView.h>
#import <AudioToolbox/AudioToolbox.h>
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

#include "map_identity.h"
#include "map_params.h"

#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/module.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"

#include <mach/mach.h>
#include <objc/runtime.h>

#include <cstdio>
#include <cstdlib>
#include <functional>
#include <string>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;

// ---- the stand-in host view -----------------------------------------------------------------------------------

@interface MapUITestHostView : NSView {
@public
    int keyDowns, keyUps;
    std::string lastChars;
}
@end
@implementation MapUITestHostView
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)acceptsFirstMouse:(NSEvent*)e { return YES; }   // the test app is never active
- (BOOL)isFlipped { return YES; }
- (void)keyDown:(NSEvent*)e { ++keyDowns; lastChars = [[e characters] UTF8String] ?: ""; }
- (void)keyUp:(NSEvent*)e { ++keyUps; }
- (void)flagsChanged:(NSEvent*)e {}
- (void)mouseDown:(NSEvent*)e { [[self window] makeFirstResponder:self]; }
@end

namespace {

int g_fail = 0;
int g_cycles = 20;
void check(bool ok, const char* what) {
    std::printf("  %s  %s\n", ok ? "pass" : "FAIL", what);
    if (!ok) ++g_fail;
}
void note(bool ok, const char* what) { std::printf("  %s  %s\n", ok ? "yes " : "no  ", what); }

void spin(double s) { [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:s]]; }

bool wait_for(const std::function<bool()>& f, double timeout = 3.0) {
    NSDate* end = [NSDate dateWithTimeIntervalSinceNow:timeout];
    while ([end timeIntervalSinceNow] > 0) {
        if (f()) return true;
        spin(0.02);
    }
    return f();
}

WKWebView* find_webview(NSView* v) {
    if ([v isKindOfClass:[WKWebView class]]) return (WKWebView*)v;
    for (NSView* s in [v subviews])
        if (WKWebView* w = find_webview(s)) return w;
    return nil;
}

std::string js(WKWebView* wk, const std::string& code) {
    __block bool done_ = false;
    __block std::string out;
    bool* done = &done_;
    [wk evaluateJavaScript:[NSString stringWithUTF8String:code.c_str()]
         completionHandler:^(id r, NSError* err) {
             if (err) out = std::string("ERR ") + [[err description] UTF8String];
             else if (r) out = [[NSString stringWithFormat:@"%@", r] UTF8String];
             done_ = true;
         }];
    wait_for([&] { return *done; });
    return out;
}

double resident_mb() {
    mach_task_basic_info info{};
    mach_msg_type_number_t n = MACH_TASK_BASIC_INFO_COUNT;
    task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&info, &n);
    return double(info.resident_size) / (1024.0 * 1024.0);
}

NSTimeInterval g_t = 1000.0;
NSInteger g_evn = 1;

// centre of a page element, in window coordinates
NSPoint element_point(WKWebView* wk, const char* selector) {
    const std::string r = js(wk, std::string("(() => { const b = document.querySelector('") + selector +
                                     "').getBoundingClientRect(); return (b.left + b.width / 2) + ',' + "
                                     "(b.top + b.height / 2); })()");
    double x = 0, y = 0;
    std::sscanf(r.c_str(), "%lf,%lf", &x, &y);
    NSPoint p = NSMakePoint(x, [wk isFlipped] ? y : [wk bounds].size.height - y);
    return [wk convertPoint:p toView:nil];
}

void mouse(NSWindow* w, NSEventType type, NSPoint p) {
    NSEvent* e = [NSEvent mouseEventWithType:type location:p modifierFlags:0 timestamp:(g_t += 0.05)
                                windowNumber:[w windowNumber] context:nil eventNumber:g_evn++ clickCount:1
                                    pressure:type == NSEventTypeLeftMouseUp ? 0.0f : 1.0f];
    [w sendEvent:e];
}
void click(NSWindow* w, NSPoint p) {
    mouse(w, NSEventTypeLeftMouseDown, p);
    spin(0.05);
    mouse(w, NSEventTypeLeftMouseUp, p);
    spin(0.25);
}
void drag(NSWindow* w, NSPoint from, double dy_up) {
    mouse(w, NSEventTypeLeftMouseDown, from);
    spin(0.05);
    for (int i = 1; i <= 10; ++i) {
        // window coordinates are y-up
        mouse(w, NSEventTypeLeftMouseDragged, NSMakePoint(from.x, from.y + dy_up * i / 10.0));
        spin(0.03);
    }
    mouse(w, NSEventTypeLeftMouseUp, NSMakePoint(from.x, from.y + dy_up));
    spin(0.3);
}

NSEvent* key_event(NSWindow* w, NSEventType type, NSString* chars, unsigned short code, NSEventModifierFlags mods = 0) {
    return [NSEvent keyEventWithType:type location:NSZeroPoint modifierFlags:mods timestamp:(g_t += 0.02)
                        windowNumber:[w windowNumber] context:nil characters:chars charactersIgnoringModifiers:chars
                           isARepeat:NO keyCode:code];
}
void key(NSWindow* w, NSString* chars, unsigned short code) {
    [w sendEvent:key_event(w, NSEventTypeKeyDown, chars, code)];
    spin(0.03);
    [w sendEvent:key_event(w, NSEventTypeKeyUp, chars, code)];
    spin(0.05);
}
NSString* str(unichar c) { return [NSString stringWithCharacters:&c length:1]; }

// ---- focus checks, one mode --------------------------------------------------------------------------------------
// returns the number of failed checks for this mode
int focus_checks(NSWindow* win, MapUITestHostView* hostv, WKWebView* wk, int mode) {
    std::printf(" focus mode %d\n", mode);
    js(wk, "gyre_focusMode(" + std::to_string(mode) + ")");
    js(wk, "document.getElementById('notes').value = ''");
    spin(0.1);
    [win makeFirstResponder:hostv];
    int fails = 0;
    auto expect = [&](bool ok, const char* what) {
        // mode 3 is the shipped default: its checks must pass. Lower modes are the baseline record.
        if (mode == 3) check(ok, what);
        else note(ok, what);
        fails += !ok;
    };

    // 1. click (and drag) a control, then the space bar
    drag(win, element_point(wk, "#knob"), 20);
    const bool wv_first = [win firstResponder] == wk;
    if (mode >= 2) expect(!wv_first, "web view did not become first responder on a knob drag");
    else note(wv_first, "web view became first responder on a knob drag");
    const int kd0 = hostv->keyDowns, ku0 = hostv->keyUps;
    key(win, @" ", 49);
    spin(0.3);   // WebKit's asynchronous re-dispatch of an unhandled key, if any
    expect(hostv->keyDowns == kd0 + 1 && hostv->keyUps == ku0 + 1,
           "space bar after a knob drag reached the host view (key down and key up)");
    key(win, @"a", 0);   // a computer-keyboard MIDI key in most hosts
    spin(0.3);
    expect(hostv->keyDowns == kd0 + 2 && hostv->keyUps == ku0 + 2, "letter key (MIDI keyboard) reached the host view");
    click(win, element_point(wk, "#bypass"));
    click(win, element_point(wk, "#bypass"));
    const int kd1 = hostv->keyDowns;
    key(win, @" ", 49);
    spin(0.3);
    expect(hostv->keyDowns == kd1 + 1, "space bar after clicking a button reached the host view");
    const std::string pressed = js(wk, "document.getElementById('bypass').classList.contains('on') ? 'on' : 'off'");
    expect(pressed == "off", "space bar did not press the page's button");
    NSEvent* cmdz = key_event(win, NSEventTypeKeyDown, @"z", 6, NSEventModifierFlagCommand);
    expect(![win performKeyEquivalent:cmdz], "Cmd-Z not claimed by the web view (left to the host's menus)");

    // 2. the text field
    click(win, element_point(wk, "#notes"));
    spin(0.2);
    expect([win firstResponder] == wk, "clicking the text field gave the web view key focus");
    key(win, @"a", 0);
    key(win, @"b", 11);
    key(win, str(NSLeftArrowFunctionKey), 123);
    key(win, @"X", 7);
    spin(0.2);
    const std::string v = js(wk, "document.getElementById('notes').value");
    expect(v == "aXb", ("typing and the left arrow edit the text field (got \"" + v + "\")").c_str());
    const int kd2 = hostv->keyDowns;
    key(win, @" ", 49);
    spin(0.2);
    expect(hostv->keyDowns == kd2, "space bar typed into the field went to the page, not the host");
    NSEvent* cmda = key_event(win, NSEventTypeKeyDown, @"a", 0, NSEventModifierFlagCommand);
    expect([win performKeyEquivalent:cmda], "Cmd-A in the text field is handled by the page (clipboard keys)");
    spin(0.1);
    // Esc: blur, and the host gets its keys back
    key(win, str(27), 53);
    spin(0.3);
    expect([win firstResponder] == hostv, "Esc in the text field handed first responder back to the host view");
    const int kd3 = hostv->keyDowns;
    key(win, @" ", 49);
    spin(0.3);
    expect(hostv->keyDowns == kd3 + 1, "space bar after Esc reached the host view");
    // click elsewhere in the page
    click(win, element_point(wk, "#notes"));
    click(win, element_point(wk, "#knob"));
    spin(0.3);
    expect([win firstResponder] != wk || mode < 2, "a click on the knob after the field took key focus back");
    const int kd4 = hostv->keyDowns;
    key(win, @" ", 49);
    spin(0.3);
    expect(hostv->keyDowns == kd4 + 1, "space bar after clicking away from the field reached the host view");
    // click on the host outside the editor
    click(win, element_point(wk, "#notes"));
    click(win, NSMakePoint(1600, 100));   // window coordinates (y up): the host area below the editor
    spin(0.3);
    const std::string active = js(wk, "document.activeElement ? document.activeElement.id : ''");
    if (getenv("MAP_UI_TEST_DEBUG"))
        std::printf("  debug first responder %s, active element \"%s\"\n",
                    [NSStringFromClass([[win firstResponder] class]) UTF8String], active.c_str());
    expect([win firstResponder] == hostv && active != "notes",
           "a click on the host outside the editor blurred the field and gave the host its keys");
    return fails;
}

// ---- the window -------------------------------------------------------------------------------------------------
struct Window {
    NSWindow* win = nil;
    MapUITestHostView* host = nil;
    Window() {
        // larger than the editor, so the test can click the host outside it
        win = [[NSWindow alloc] initWithContentRect:NSMakeRect(40, 40, 1700, 900)
                                          styleMask:NSWindowStyleMaskTitled
                                            backing:NSBackingStoreBuffered defer:NO];
        [win setReleasedWhenClosed:NO];
        host = [[MapUITestHostView alloc] initWithFrame:NSMakeRect(0, 0, 1700, 900)];
        [win setContentView:host];
        [win orderFrontRegardless];   // visible, without activating the app
        [win makeKeyWindow];
        [win makeFirstResponder:host];
    }
    ~Window() {
        [win orderOut:nil];
        [win close];
        [host release];
        [win release];
    }
};

// ---- VST 3 -------------------------------------------------------------------------------------------------------
class TestHandler : public IComponentHandler {
public:
    int begins = 0, performs = 0, ends = 0;
    double last = -1;
    tresult PLUGIN_API beginEdit(Vst::ParamID) override { ++begins; return kResultOk; }
    tresult PLUGIN_API performEdit(Vst::ParamID, ParamValue v) override { ++performs; last = v; return kResultOk; }
    tresult PLUGIN_API endEdit(Vst::ParamID) override { ++ends; return kResultOk; }
    tresult PLUGIN_API restartComponent(int32) override { return kResultOk; }
    tresult PLUGIN_API queryInterface(const TUID, void**) override { return kNoInterface; }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }
};

class TestFrame : public IPlugFrame {
public:
    NSView* host = nil;
    int resizes = 0;
    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* r) override {
        ++resizes;
        return view->onSize(r);
    }
    tresult PLUGIN_API queryInterface(const TUID, void**) override { return kNoInterface; }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }
};

int run_vst3(const std::string& path) {
    std::printf("== VST 3 editor (%s)\n", path.c_str());
    std::string err;
    auto module = VST3::Hosting::Module::create(path, err);
    if (!module) { std::printf("  FAIL load: %s\n", err.c_str()); return 1; }
    HostApplication hostapp;
    PluginContextFactory::instance().setPluginContext(static_cast<IHostApplication*>(&hostapp));
    auto factory = module->getFactory();
    IPtr<PlugProvider> provider;
    for (auto& ci : factory.classInfos())
        if (ci.category() == kVstAudioEffectClass) { provider = owned(new PlugProvider(factory, ci, true)); break; }
    IComponent* comp = provider ? provider->getComponent() : nullptr;   // sets up component + controller
    IEditController* ctrl = comp ? provider->getController() : nullptr;
    if (!ctrl) { std::printf("  FAIL: no controller\n"); return 1; }
    TestHandler handler;
    ctrl->setComponentHandler(&handler);

    Window w;
    TestFrame frame;
    {
        IPtr<IPlugView> view = owned(ctrl->createView(ViewType::kEditor));
        check(view != nullptr, "createView(\"editor\")");
        if (!view) return 1;
        check(view->isPlatformTypeSupported(kPlatformTypeNSView) == kResultTrue, "NSView platform type");
        view->setFrame(&frame);
        ViewRect r{};
        view->getSize(&r);
        check(view->attached((__bridge void*)w.host, kPlatformTypeNSView) == kResultTrue, "attached");
        WKWebView* wk = find_webview(w.host);
        check(wk != nil, "a WKWebView is in the host view");
        if (!wk) return 1;
        check(wait_for([&] { return [[wk title] isEqualToString:@"Gyre focus spike"]; }),
              "page served from memory (document.title)");
        check(wait_for([&] { return js(wk, "window.gyreHostFormat") == "VST3"; }), "page sees format VST3");

        // host -> page
        ctrl->setParamNormalized(mapvs::kDrive, 0.5);
        check(wait_for([&] { return js(wk, "document.getElementById('kval').textContent") == "4.00×"; }),
              "host automation (setParamNormalized Drive 0.5) shown by the page (4.00×)");
        // page -> host
        const int b0 = handler.begins;
        drag(w.win, element_point(wk, "#knob"), 40);
        check(handler.begins == b0 + 1 && handler.ends == handler.begins && handler.performs > 0,
              "knob drag: one beginEdit / performEdit... / endEdit gesture");
        check(handler.last > 0.5 && std::abs(ctrl->getParamNormalized(mapvs::kDrive) - handler.last) < 1e-12,
              "knob drag raised Drive, and the controller holds the edited value");
        js(wk, "gyre_set(0, NaN); gyre_set(0, 7); gyre_set(99, 0.5); gyre_set(0, -1)");
        spin(0.2);
        check(std::abs(ctrl->getParamNormalized(mapvs::kDrive) - handler.last) < 1e-12,
              "NaN and out-of-range values from the page are ignored");

        // resize: the page asks, the host (frame) calls onSize
        const std::string ok = js(wk, "gyre_resize(1140, 600).then(r => window.__r = r); 0");
        spin(0.3);
        const std::string res = js(wk, "String(window.__r)");
        check(res == "true" && frame.resizes == 1 && NSWidth([find_webview(w.host) frame]) == 1140,
              "page resize request -> IPlugFrame::resizeView -> onSize (1140 x 600)");
        ViewRect big(0, 0, 5000, 5000);
        view->checkSizeConstraint(&big);
        check(big.getWidth() == 1520 && big.getHeight() == 800, "checkSizeConstraint clamps to 200 %");
        js(wk, "devicePixelRatio");
        std::printf("  info  devicePixelRatio %s (backing scale %.1f)\n", js(wk, "String(devicePixelRatio)").c_str(),
                    [w.win backingScaleFactor]);
        (void)ok;

        int fails3 = 0;
        for (int mode = 0; mode <= 3; ++mode) {
            const int f = focus_checks(w.win, w.host, wk, mode);
            if (mode == 3) fails3 = f;
            else std::printf("  -> mode %d: %d of the checks above did not hold\n", mode, f);
        }
        (void)fails3;
        js(wk, "gyre_focusMode(3)");
        view->removed();
    }

    // open / close cycles
    const double mb0 = resident_mb();
    int dealloced = 0;
    const int cycles = g_cycles;
    for (int i = 0; i < cycles; ++i) {
        if (i == cycles / 2) std::printf("  info  resident memory after %d cycles %.1f MB\n", i, resident_mb());
        id weak = nil;
        @autoreleasepool {
            IPtr<IPlugView> view = owned(ctrl->createView(ViewType::kEditor));
            view->attached((__bridge void*)w.host, kPlatformTypeNSView);
            WKWebView* wk = find_webview(w.host);
            objc_storeWeak(&weak, wk);
            wait_for([&] { return [[wk title] length] > 0; });
            view->removed();
            view = nullptr;
        }
        // outside the pool: the test's own autoreleased references (subviews arrays) are gone
        wait_for([&] { return objc_loadWeak(&weak) == nil; }, 2.0);
        dealloced += objc_loadWeak(&weak) == nil;
        objc_storeWeak(&weak, nil);
    }
    spin(0.5);
    check(dealloced == cycles, ("open / close x" + std::to_string(cycles) + ": every WKWebView deallocated (" +
                                std::to_string(dealloced) + ")").c_str());
    std::printf("  info  resident memory %.1f MB -> %.1f MB over %d cycles\n", mb0, resident_mb(), cycles);
    check([[w.host subviews] count] == 0, "no editor views left in the host view");
    ctrl->setComponentHandler(nullptr);
    provider = nullptr;
    PluginContextFactory::instance().setPluginContext(nullptr);
    return 0;
}

// ---- AU ----------------------------------------------------------------------------------------------------------
OSType fourcc(const char* s) {
    return OSType((std::uint32_t(std::uint8_t(s[0])) << 24) | (std::uint32_t(std::uint8_t(s[1])) << 16) |
                  (std::uint32_t(std::uint8_t(s[2])) << 8) | std::uint32_t(std::uint8_t(s[3])));
}

struct Gestures {
    int begins = 0, ends = 0, changes = 0;
};
void gesture_cb(void* ref, void*, const AudioUnitEvent* e, UInt64, AudioUnitParameterValue) {
    auto* g = static_cast<Gestures*>(ref);
    if (e->mEventType == kAudioUnitEvent_BeginParameterChangeGesture) ++g->begins;
    if (e->mEventType == kAudioUnitEvent_EndParameterChangeGesture) ++g->ends;
    if (e->mEventType == kAudioUnitEvent_ParameterValueChange) ++g->changes;
}

NSView* au_view(AudioUnit au) {
    AudioUnitCocoaViewInfo info{};
    UInt32 size = sizeof info;
    if (AudioUnitGetProperty(au, kAudioUnitProperty_CocoaUI, kAudioUnitScope_Global, 0, &info, &size) != noErr)
        return nil;
    NSBundle* b = [NSBundle bundleWithURL:(__bridge NSURL*)info.mCocoaAUViewBundleLocation];
    Class cls = [b classNamed:(__bridge NSString*)info.mCocoaAUViewClass[0]];
    CFRelease(info.mCocoaAUViewBundleLocation);
    CFRelease(info.mCocoaAUViewClass[0]);
    if (!cls || ![cls conformsToProtocol:@protocol(AUCocoaUIBase)]) return nil;
    NSObject<AUCocoaUIBase>* f = [[cls alloc] init];
    NSView* v = [f uiViewForAudioUnit:au withSize:NSMakeSize(0, 0)];
    [v retain];
    [f release];
    return v;   // +1
}

int run_au() {
    std::printf("== AU editor (installed %s %s %s)\n", MAP_AU_TYPE, MAP_AU_SUBTYPE, MAP_AU_MANUFACTURER);
    AudioComponentDescription d{fourcc(MAP_AU_TYPE), fourcc(MAP_AU_SUBTYPE), fourcc(MAP_AU_MANUFACTURER), 0, 0};
    AudioComponent comp = AudioComponentFindNext(nullptr, &d);
    if (!comp) { std::printf("  FAIL: component not found (install it first: run_checks.sh --au)\n"); return 1; }
    AudioUnit au = nullptr;
    if (AudioComponentInstanceNew(comp, &au) != noErr) { std::printf("  FAIL: instance\n"); return 1; }
    AudioUnitCocoaViewInfo info{};
    UInt32 size = sizeof info;
    check(AudioUnitGetProperty(au, kAudioUnitProperty_CocoaUI, kAudioUnitScope_Global, 0, &info, &size) == noErr,
          "kAudioUnitProperty_CocoaUI");
    if (info.mCocoaAUViewClass[0]) {
        std::printf("  info  view factory class %s\n", [(__bridge NSString*)info.mCocoaAUViewClass[0] UTF8String]);
        CFRelease(info.mCocoaAUViewBundleLocation);
        CFRelease(info.mCocoaAUViewClass[0]);
    }
    Gestures g;
    AUEventListenerRef listener = nullptr;
    AUEventListenerCreate(gesture_cb, &g, CFRunLoopGetMain(), kCFRunLoopDefaultMode, 0.0f, 0.0f, &listener);
    for (AudioUnitEventType t : {kAudioUnitEvent_BeginParameterChangeGesture, kAudioUnitEvent_EndParameterChangeGesture,
                                 kAudioUnitEvent_ParameterValueChange}) {
        AudioUnitEvent e{};
        e.mEventType = t;
        e.mArgument.mParameter = {au, mapvs::kDrive, kAudioUnitScope_Global, 0};
        AUEventListenerAddEventType(listener, nullptr, &e);
    }

    Window w;
    {
        NSView* v = au_view(au);
        check(v != nil, "the bundle's factory class returned a view");
        if (!v) return 1;
        [w.host addSubview:v];
        WKWebView* wk = find_webview(v);
        check(wk != nil, "a WKWebView is in the view");
        if (!wk) return 1;
        check(wait_for([&] { return [[wk title] isEqualToString:@"Gyre focus spike"]; }),
              "page served from memory (document.title)");
        check(wait_for([&] { return js(wk, "window.gyreHostFormat") == "AU"; }), "page sees format AU");
        const AudioUnitParameter p{au, mapvs::kDrive, kAudioUnitScope_Global, 0};
        AUParameterSet(nullptr, nullptr, &p, 4.0f, 0);   // host automation
        check(wait_for([&] { return js(wk, "document.getElementById('kval').textContent") == "4.00×"; }),
              "host automation (AUParameterSet Drive 4) shown by the page (4.00×)");
        const int b0 = g.begins;
        drag(w.win, element_point(wk, "#knob"), 40);
        spin(0.2);
        AudioUnitParameterValue drive = 0;
        AudioUnitGetParameter(au, mapvs::kDrive, kAudioUnitScope_Global, 0, &drive);
        check(g.begins == b0 + 1 && g.ends == g.begins, "knob drag: one begin / end gesture");
        check(drive > 4.0f, "knob drag raised Drive on the AU");
        js(wk, "gyre_set(0, NaN); gyre_set(0, 7); gyre_set(0, -1)");
        spin(0.2);
        AudioUnitParameterValue drive2 = 0;
        AudioUnitGetParameter(au, mapvs::kDrive, kAudioUnitScope_Global, 0, &drive2);
        check(drive2 == drive, "NaN and out-of-range values from the page are ignored");
        js(wk, "gyre_resize(1140, 600)");
        spin(0.3);
        check(NSWidth([v frame]) == 1140 && NSHeight([v frame]) == 600, "page resize request -> view frame 1140 x 600");
        for (int mode = 0; mode <= 3; ++mode) {
            const int f = focus_checks(w.win, w.host, wk, mode);
            if (mode < 3) std::printf("  -> mode %d: %d of the checks above did not hold\n", mode, f);
        }
        js(wk, "gyre_focusMode(3)");
        [v removeFromSuperview];
        [v release];
    }
    const double mb0 = resident_mb();
    int dealloced = 0;
    const int cycles = g_cycles;
    for (int i = 0; i < cycles; ++i) {
        if (i == cycles / 2) std::printf("  info  resident memory after %d cycles %.1f MB\n", i, resident_mb());
        id weakv = nil, weakw = nil;
        @autoreleasepool {
            NSView* v = au_view(au);
            [w.host addSubview:v];
            WKWebView* wk = find_webview(v);
            objc_storeWeak(&weakv, v);
            objc_storeWeak(&weakw, wk);
            wait_for([&] { return [[wk title] length] > 0; });
            [v removeFromSuperview];
            [v release];
        }
        wait_for([&] { return objc_loadWeak(&weakv) == nil && objc_loadWeak(&weakw) == nil; }, 2.0);
        dealloced += objc_loadWeak(&weakv) == nil && objc_loadWeak(&weakw) == nil;
        objc_storeWeak(&weakv, nil);
        objc_storeWeak(&weakw, nil);
    }
    spin(0.5);
    check(dealloced == cycles, ("open / close x" + std::to_string(cycles) + ": every view and WKWebView deallocated (" +
                                std::to_string(dealloced) + ")").c_str());
    std::printf("  info  resident memory %.1f MB -> %.1f MB over %d cycles\n", mb0, resident_mb(), cycles);
    AUListenerDispose(listener);
    AudioComponentInstanceDispose(au);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    @autoreleasepool {
        std::string vst3 = MAP_TEST_VST3_PATH;
        bool au = false;
        for (int i = 1; i < argc; ++i) {
            if (std::string(argv[i]) == "--au") au = true;
            else if (std::string(argv[i]) == "--cycles" && i + 1 < argc) g_cycles = std::atoi(argv[++i]);
            else vst3 = argv[i];
        }
        std::setvbuf(stdout, nullptr, _IOLBF, 0);
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [NSApp finishLaunching];
        int r = run_vst3(vst3);
        if (au) r |= run_au();
        std::printf("%s (%d failed checks)\n", (r == 0 && g_fail == 0) ? "PASS" : "FAIL", g_fail);
        return (r == 0 && g_fail == 0) ? 0 : 1;
    }
}
