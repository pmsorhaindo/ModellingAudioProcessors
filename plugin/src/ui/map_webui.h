/* map_webui.h -- the plugin's web editor: a choc WebView in an NSView, shared by the VST 3 and AU views (#105).
 *
 * Plain C++ interface (no Objective-C here), implemented in map_webui.mm. Both formats parent the same NSView:
 *   VST 3   IPlugView::attached(parent) adds view() to the host's NSView (map_vst3.cpp)
 *   AU      the AUCocoaUIBase factory returns view() to the host (au/map_au_view.mm)
 *
 * Threads
 *   Everything here runs on the main (message) thread, except set_param(), which may be called from any thread:
 *   it only stores a relaxed atomic and a dirty flag. A ~30 Hz main-thread timer pushes the dirty values to the
 *   page in one evaluateJavascript call. Nothing here touches the audio thread or the engine.
 *
 * Page -> host. The page calls the bound functions (choc WebView::bind), which arrive on the main thread:
 *   gyre_begin(id) / gyre_set(id, normalized) / gyre_end(id)   one edit gesture, sent to the host through
 *                                                              Callbacks (VST 3 begin/perform/endEdit,
 *                                                              AU AUParameterSet + gesture events)
 *   gyre_ready()                                               page loaded: resend every parameter
 *   gyre_textFocus(bool)                                       a text input took or lost focus (key-focus policy)
 *   gyre_focusMode(int)                                        key-focus spike: pick the mitigation level
 *   gyre_resize(w, h)                                          the page asks for a new editor size
 *
 * Keyboard focus (the #105 spike). A focused WKWebView can swallow the host's keys (space bar, computer-keyboard
 * MIDI). FocusMode selects the mitigations from the #105 plan; each level includes the ones before it:
 *   kStock          choc's WKWebView as it is
 *   kForward        (a) key events the page does not need (no text input focused) go to the next responder,
 *                       never to WebKit; Cmd shortcuts go to the host's menus
 *   kNonActivating  (b) + the web view refuses first responder on clicks; the first click acts
 *   kTextFocus      (c) + the web view takes key focus only while a page text input has focus, and hands it back
 *                       to the previous first responder on blur
 */
#ifndef MAP_WEBUI_H
#define MAP_WEBUI_H

#include <cstddef>
#include <functional>
#include <memory>
#include <string>

namespace mapvs::ui {

// one file served to the page from memory (choc fetchResource; no file:// URLs)
struct Resource {
    const char* path;               // URL path, starting with "/"
    const char* mime;
    const unsigned char* data;
    std::size_t size;
};
// the embedded page files (generated at build time from plugin/ui/, see CMakeLists.txt); a null path ends the list
extern const Resource kResources[];

enum class FocusMode : int { kStock = 0, kForward = 1, kNonActivating = 2, kTextFocus = 3 };
inline constexpr FocusMode kDefaultFocusMode = FocusMode::kTextFocus;

struct Callbacks {                  // all called on the main thread
    std::function<void(int id)> begin_edit;
    std::function<void(int id, double norm)> perform_edit;
    std::function<void(int id)> end_edit;
    std::function<double(int id)> get_norm;         // current normalized value, for gyre_ready()
    // the page asks for a new size (logical points); return true if the host accepted it. The host then calls
    // set_size() (VST 3 onSize) or the view frame changes (AU).
    std::function<bool(int w, int h)> request_resize;
};

class WebEditor {
public:
    // Creates the NSView (and the WKWebView inside it) at w x h points. format names the host format for the page
    // ("VST3" or "AU"). The page is served from kResources at "/".
    WebEditor(Callbacks cb, int w, int h, const char* format);
    ~WebEditor();
    WebEditor(const WebEditor&) = delete;
    WebEditor& operator=(const WebEditor&) = delete;

    void* view() const;             // NSView*, owned by this object (retained until the destructor)
    void attach(void* parent);      // add view() to a parent NSView* (VST 3 attached); the destructor removes it
    void set_size(int w, int h);    // resize the view (VST 3 onSize)
    void set_param(int id, double norm) noexcept;   // any thread: host automation / state recall -> page
    static constexpr int kMaxParams = 32;

    struct Impl;                    // public name, private definition (map_webui.mm)

private:
    std::unique_ptr<Impl> impl_;
};

}  // namespace mapvs::ui
#endif
