/* map_au_view.mm -- the AU's Cocoa view (#105): an AUCocoaUIBase factory and an NSView around the web editor.
 *
 * The host reads kAudioUnitProperty_CocoaUI (map_au.cpp), loads this bundle and asks the factory class for a view.
 * The view talks to the AU only through the public AudioUnit API, like any controller would:
 *   page edit      AUEventListenerNotify(begin gesture), AUParameterSet, AUEventListenerNotify(end gesture)
 *   host -> page   an AUEventListener on the main run loop (parameter value changes, host automation, preset
 *                  recall) -> WebEditor::set_param
 * Values on the AU are plain (map_params.h param_to_plain / param_to_norm); the page works in normalized values.
 *
 * Objective-C class names must be unique per build: two builds of this plugin in one host process (the AU and an
 * older or newer copy, or the AU beside its VST 3 build, which has no ObjC classes of its own) would otherwise share
 * one class, and the runtime would pick either. CMake sets MAP_OBJC_SUFFIX (version + a per-configure token).
 */
#import <AudioToolbox/AUCocoaUIView.h>
#import <AudioToolbox/AudioToolbox.h>
#import <Cocoa/Cocoa.h>

#include "../map_identity.h"
#include "../map_params.h"
#include "../ui/map_webui.h"

#include <memory>

#ifndef MAP_OBJC_SUFFIX
#error "MAP_OBJC_SUFFIX must be defined by the build (CMakeLists.txt)"
#endif
#define MAP_CAT2(a, b) a##b
#define MAP_CAT(a, b) MAP_CAT2(a, b)
#define MAP_AU_VIEW_FACTORY MAP_CAT(GyreAUViewFactory, MAP_OBJC_SUFFIX)
#define MAP_AU_VIEW MAP_CAT(GyreAUView, MAP_OBJC_SUFFIX)

namespace mapvs {

inline constexpr int kAUEditorW = 760, kAUEditorH = 400;

// owned by the view; main thread
class AUEditor {
public:
    AUEditor(AudioUnit au, NSView* outer) : au_(au), outer_(outer) {
        ui::Callbacks cb;
        cb.begin_edit = [this](int id) { gesture(id, kAudioUnitEvent_BeginParameterChangeGesture); };
        cb.end_edit = [this](int id) { gesture(id, kAudioUnitEvent_EndParameterChangeGesture); };
        cb.perform_edit = [this](int id, double n) {
            if (id < 0 || id >= kNumParams) return;
            const AudioUnitParameter p = param(id);
            AUParameterSet(listener_, this, &p, AudioUnitParameterValue(param_to_plain(std::uint32_t(id), n)), 0);
        };
        cb.get_norm = [this](int id) { return norm_of(id); };
        cb.request_resize = [this](int w, int h) {
            [outer_ setFrameSize:NSMakeSize(w, h)];     // the host follows the view's frame
            return true;
        };
        editor_ = std::make_unique<ui::WebEditor>(std::move(cb), kAUEditorW, kAUEditorH, "AU");
        NSView* v = (NSView*)editor_->view();
        [v setFrame:[outer bounds]];
        [v setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
        [outer addSubview:v];

        // 30 ms notification interval and granularity: coalesces automation bursts; delivered on the main run loop
        if (AUEventListenerCreate(&AUEditor::on_event, this, CFRunLoopGetMain(), kCFRunLoopCommonModes, 0.03f, 0.03f,
                                  &listener_) == noErr) {
            for (int id = 0; id < kNumParams; ++id) {
                AudioUnitEvent e{};
                e.mEventType = kAudioUnitEvent_ParameterValueChange;
                e.mArgument.mParameter = param(id);
                AUEventListenerAddEventType(listener_, this, &e);
            }
        }
    }
    ~AUEditor() {
        if (listener_) AUListenerDispose(listener_);   // synchronous: no callback after this returns
        editor_.reset();
    }

private:
    AudioUnitParameter param(int id) const {
        AudioUnitParameter p{};
        p.mAudioUnit = au_;
        p.mParameterID = AudioUnitParameterID(id);
        p.mScope = kAudioUnitScope_Global;
        p.mElement = 0;
        return p;
    }
    void gesture(int id, AudioUnitEventType type) {
        if (id < 0 || id >= kNumParams) return;
        AudioUnitEvent e{};
        e.mEventType = type;
        e.mArgument.mParameter = param(id);
        AUEventListenerNotify(listener_, this, &e);
    }
    double norm_of(int id) const {
        if (id < 0 || id >= kNumParams) return 0.0;
        AudioUnitParameterValue v = 0;
        if (AudioUnitGetParameter(au_, AudioUnitParameterID(id), kAudioUnitScope_Global, 0, &v) != noErr) return 0.0;
        return param_to_norm(std::uint32_t(id), double(v));
    }
    static void on_event(void* self, void*, const AudioUnitEvent* e, UInt64, AudioUnitParameterValue value) {
        auto* ed = static_cast<AUEditor*>(self);
        if (!e || e->mEventType != kAudioUnitEvent_ParameterValueChange || !ed->editor_) return;
        const AudioUnitParameterID id = e->mArgument.mParameter.mParameterID;
        if (id < AudioUnitParameterID(kNumParams)) ed->editor_->set_param(int(id), param_to_norm(id, double(value)));
    }

    AudioUnit au_;
    NSView* outer_;                                     // not retained: it owns us
    AUEventListenerRef listener_ = nullptr;
    std::unique_ptr<ui::WebEditor> editor_;
};

}  // namespace mapvs

@interface MAP_AU_VIEW : NSView {
    mapvs::AUEditor* editor_;
}
- (instancetype)initWithAudioUnit:(AudioUnit)au size:(NSSize)size;
@end

@implementation MAP_AU_VIEW
- (instancetype)initWithAudioUnit:(AudioUnit)au size:(NSSize)size {
    if ((self = [super initWithFrame:NSMakeRect(0, 0, size.width, size.height)]))
        editor_ = new mapvs::AUEditor(au, self);
    return self;
}
- (BOOL)isFlipped {
    return YES;
}
- (void)dealloc {
    delete editor_;
    editor_ = nullptr;
    [super dealloc];
}
@end

@interface MAP_AU_VIEW_FACTORY : NSObject <AUCocoaUIBase>
@end

@implementation MAP_AU_VIEW_FACTORY
- (unsigned)interfaceVersion {
    return 0;
}
- (NSString*)description {
    return [NSString stringWithUTF8String:MAP_PRODUCT_NAME];
}
- (NSView*)uiViewForAudioUnit:(AudioUnit)au withSize:(NSSize)preferred {
    (void)preferred;                                    // the editor has its own default size
    MAP_AU_VIEW* v = [[MAP_AU_VIEW alloc] initWithAudioUnit:au size:NSMakeSize(mapvs::kAUEditorW, mapvs::kAUEditorH)];
    return [v autorelease];
}
@end
