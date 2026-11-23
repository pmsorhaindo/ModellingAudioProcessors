# #105 keyboard-focus spike: choc WebView in Gyre's VST 3 and AU editors

Step 1 of the #105 UI plan (Michael, 22 Nov 2026: "try webview with choc first, and try and scope out to see if we
can handle keyboard focus appropriately early on to de risk the solution"). This is the go / no-go step before the full
v0.1 UI. **No-go** means a target host loses its keys and none of the mitigations fixes it. The fallback is then
VSTGUI, with the same design and parameter model.

The spike runs on the current 12AX7 plugin. The page (`ui/spike/index.html`, served from memory) has a Drive knob,
a Bypass button, a text field, the focus-mode selector (0–3), size buttons (75 / 100 / 150 / 200 %) and diagnostics:
the keys forwarded to the host, the keys the page itself saw, `document.hasFocus()`, the size and devicePixelRatio.

## Focus modes (cumulative; mode 3 is the default)

| mode | what the editor does |
|---|---|
| 0 stock | choc's WKWebView as it is |
| 1 (a) forward | with no page text input focused, `keyDown` / `keyUp` / `flagsChanged` go straight to the next responder (the host), never to WebKit, and `performKeyEquivalent` returns NO, so Cmd shortcuts reach the host's menus |
| 2 (b) non-activating | + the web view refuses first responder on clicks and drags; the first click on an inactive window acts |
| 3 (c) text focus only | + the page reports text-input focus over the bridge; only then is the web view made first responder, and on blur (Esc, a click elsewhere in the page or in the host) first responder goes back to whatever had it before |

## Automated results (test host, not a DAW)

`map_ui_test` (`tests/ui_test.mm`) loads the **built** plugins the way a host does: VST 3 through
`IEditController::createView` and `IPlugView::attached`, and the AU through `kAudioUnitProperty_CocoaUI` and the
bundle's factory class. It puts each editor in a plain NSWindow whose content view stands in for the host and counts
the keys that reach it. Events are synthetic NSEvents sent through `-[NSWindow sendEvent:]`. This tests the
responder-chain mechanism. It cannot show what a particular DAW does with the keys it gets back. Run on this Mac
(macOS 15.6.1, Apple silicon, Retina) on 23 Nov 2026, universal build: the arm64 slices, and the x86_64 slices under
Rosetta (`arch -x86_64 map_ui_test --au`), with the same results. "yes" means the behaviour held. Mode 3's rows are pass / fail
checks.

| check (both formats gave the same result) | mode 0 | mode 1 | mode 2 | mode 3 |
|---|---|---|---|---|
| web view does not become first responder on a knob drag | no | no | yes | pass |
| space bar after a knob drag reaches the host (down and up) | **no** | yes | yes | pass |
| a letter key (computer-keyboard MIDI) reaches the host (down and up) | **no** | yes | yes | pass |
| space bar after clicking a button reaches the host | yes | yes | yes | pass |
| space bar does not press the page's button | yes | yes | yes | pass |
| Cmd-Z is not claimed by the web view (left to the host's menus) | **no** | yes | yes | pass |
| clicking the text field gives the web view key focus | yes | yes | yes | pass |
| typing and the left arrow edit the field ("ab", ←, "X" → "aXb") | yes | yes | yes | pass |
| space typed into the field goes to the page, not the host | yes | yes | yes | pass |
| Cmd-A in the field is handled by the page | yes | yes | yes | pass |
| Esc in the field hands first responder back to the host | no | no | no | pass |
| space bar after Esc reaches the host | yes | yes | yes | pass |
| a click on the knob after the field takes key focus back | yes | yes | no | pass |
| space bar after clicking away from the field reaches the host | yes | yes | yes | pass |
| a click on the host outside the editor blurs the field and gives the host its keys | yes | yes | yes | pass |

So the classic failure shows up in the test host too: with the stock web view, the space bar, MIDI letter keys and
Cmd-Z are swallowed after a knob drag. Mitigation (a) fixes the keys. Without (c), the host still does not get first
responder back after Esc. Mode 3 passes every check in both formats.

Also checked by `map_ui_test`, both formats (all pass):
- the page is served from memory (`document.title`), and the page knows its format;
- host → page: VST 3 `setParamNormalized(Drive, 0.5)` and AU `AUParameterSet(Drive, 4)` show "4.00×" on the knob;
- page → host: a knob drag is one gesture (VST 3: one `beginEdit`, `performEdit`s, one `endEdit`; AU: one begin and
  one end gesture event), and it raises Drive in the controller / on the AU;
- `gyre_set` with NaN, 7, −1 or an unknown id is ignored;
- resize: the page asks for 1140 × 600, which goes through `IPlugFrame::resizeView` → `onSize` (VST 3) or the view
  frame (AU). `checkSizeConstraint` clamps to 200 %;
- HiDPI: devicePixelRatio 2 at backing scale 2;
- open / close ×20 (×120 once): every WKWebView and AU view is deallocated (weak references go nil), no editor view
  is left in the host view, and the count of WebContent processes on the machine is the same before and after.
  Resident memory of the test process: 63.0 → 80.6 → 83.5 MB (VST 3, after 0 / 60 / 120 cycles) and
  29.2 → 45.5 → 43.3 MB (AU). It levels off (WebKit's caches warming up), so there is no per-open leak.

Unchanged by the spike: the engine test, the smoothing test, the Steinberg validator (47 / 47, extensive 537 / 537),
the VST 3 and AU nulls (exact) and auval (`AU VALIDATION SUCCEEDED`). Logs are in the PR.

## DAW checklist (for Michael: not run by the agent)

The agent cannot drive a DAW's GUI, so the matrix below is empty until someone runs it. Ableton Live 11 and 12 are
installed on this Mac. Logic and Reaper are not, and GarageBand (AU) can stand in for Logic if needed.

**Setup.**
1. Build and install: `cmake -S plugin -B plugin/build && cmake --build plugin/build -j` then
   `plugin/tests/run_checks.sh plugin/build --au` (installs the AU to `~/Library/Audio/Plug-Ins/Components`), and
   `ditto plugin/build/VST3/Release/Gyre.vst3 ~/Library/Audio/Plug-Ins/VST3/Gyre.vst3` for the VST 3.
2. In Live: enable both AU and VST 3 plugins (Settings › Plug-ins), rescan, put **Gyre** on an audio track, and give
   a MIDI track an instrument so computer-keyboard MIDI (the `M` key toggle) makes sound. Open Gyre's window.
3. The editor starts in focus mode 3 (the candidate). The page shows "keys forwarded to host" and "keys seen by the
   page". Those two counters tell you whether a key went to the host or to the page.

**Checks per host × format.** Do them in mode 3 first. Then repeat checks 1–3 in modes 0, 1 and 2 (the buttons on
the page) to record which mitigations were needed. The mode is kept per host process.

1. **Space bar after a control.** Click the plugin window's title bar, then press space: the transport starts and
   stops. Drag the Drive knob, release, and press space at once: the transport toggles, and "keys seen by the page"
   does not go up. Click Bypass, then press space: the transport toggles and Bypass does not change.
2. **Host shortcuts.** With the plugin window focused, after a knob drag: Cmd-Z / Cmd-Shift-Z undo and redo the knob
   move in the host; Tab (Live: switch Session / Arrangement) and Cmd-S work.
3. **Computer-keyboard MIDI.** Arm the MIDI track and turn on computer MIDI keyboard. With the plugin window focused
   after a knob drag, play A S D F … and hold notes: every note starts and stops when the key goes down and up, with
   no stuck or late notes. Do the same with Shift held and with fast repeats.
4. **Text field.** Click the text field and type: the text appears, and the transport does **not** start on space.
   Check the arrows, Backspace, Cmd-A, Cmd-C, Cmd-V and Cmd-X (paste text in from another app too). The host's
   shortcuts do not fire while you type.
5. **Focus back to the host.** With the field focused: press Esc, then space: the transport toggles. Type in the field
   again, then click the knob: space goes to the host. Type again, then click the host's main window, then press
   space: the transport toggles.
6. **Close / reopen / resize.** Close and reopen the plugin window 10 times quickly, with a knob drag each time: no
   crash, the page comes back with the current Drive value, and automation of Drive (draw an envelope, play) moves
   the knob. Click the 75 / 150 / 200 % buttons: the window follows. If the host lets you drag the window corner
   (VST 3), the page follows that too.
7. **HiDPI.** On the Retina display the knob and text are sharp (devicePixelRatio 2 in the diagnostics). If you have
   a second, non-Retina display, move the window there and back.
8. **Session reload.** Save the set with Drive moved, close it and reopen it: the knob shows the saved value.

**Result matrix** (pass / fail / notes; record the lowest mode that passed checks 1–3):

| host | format | 1 space | 2 shortcuts | 3 MIDI keys | 4 text field | 5 focus back | 6 close / reopen / resize | 7 HiDPI | 8 reload | lowest mode needed |
|---|---|---|---|---|---|---|---|---|---|---|
| Live 12 | AU | | | | | | | | | |
| Live 12 | VST 3 | | | | | | | | | |
| Live 11 | AU | | | | | | | | | |
| Live 11 | VST 3 | | | | | | | | | |
| Logic (or GarageBand) | AU | | | | | | | | | |
| Reaper (if cheap) | VST 3 / AU | | | | | | | | | |

**Go / no-go.** Go if every row passes in mode 3. If a host loses its keys in mode 3, note which check failed and
what the two counters showed. That is a no-go for that host unless another mitigation fixes it. Do not switch
frameworks without Michael's decision.
