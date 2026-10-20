# 1.1.40 manual test: the Coworker editor and the compact sidebar chat

About 20 minutes. Build from main, install, restart Bforartists. Tick each line; anything odd →
a comment on #26 with a screenshot.

## A. Find it

- [x] Any area's **Editor Type** menu lists **Coworker** (wizard icon) after the node editors.
- [x] 3D View sidebar (N) → **Coworker** tab shows the compact chat: top bar (status, Start / Stop,
      open-editor and settings buttons, Coworker | Ask), the conversation, the input at the bottom.
- [x] The top bar's open-editor button opens the Coworker editor in a new window.
- [x] Preferences > Coworker > Chat Display > **Sidebar Chat = Classic Panels** brings back the old tab;
      Compact Chat switches back.

## B. The editor (Local LLM or Remote API)

- [x] **Header:** Chat, View, Coworker | Ask, mode, status, Start / Stop, settings. No node menus.
- [x] **Toolshelf (T):** icon buttons only (new thread, @, render, screen, thumbnails, plan, rules, log).
- [x] **Sidebar (N):** Coworker controls with the image socket, Session (Context, Memory & Checkpoints),
      Queue, Status & Diagnostics. No node tabs.
- [x] Start Coworker from the header. Click the input, type, **Enter** sends; **Shift+Enter** adds a line.
- [x] While it works: the bottom reads "Thinking ⠦ | 12s", the Workshop header spins, Stop works,
      and typing + Enter (or Queue) queues the next message.
- [ ] Type `@` + a few letters: the list shows filter buttons and type icons; the filters work;
      Up / Down / Tab / Enter / click insert; Esc closes the list (second Esc leaves the box).
- [X] While typing, node / viewport shortcuts don't fire (try G, N, Shift+A).
- [x] Copy on a code block and on a reply; a link opens the browser.
- [x] Wheel scrolls; the scrollbar fades in near the right edge, widens on hover, drags; Ctrl+wheel zooms;
      View > Zoom / Jump to Newest work.
- [x] Attach an image (sidebar socket, Render or Screen): its thumbnail shows in the input box; after
      sending it shows in your message; click it → Image Editor window; View > Thumbnails S / M / L.
- [x] **Edit** opens a native text field; IME input (if you use one) comes back into the box.
- [x] Theme: switch to another theme, the chat follows (colours, roundness, font size, UI scale).
- [x] Open another .blend: the editor still takes clicks and keys.
- [x] A normal Shader / Geometry Nodes editor still looks and works as before.

## C. External Harness

- [x] Switch to External Harness (stop Coworker first; switching while running is refused).
- [x] Editor and sidebar show the **External Harness card**; no input box.
- [x] Start Bridge from the card; Copy Config; paste into your client; the client's tools work.
- [x] Stop Bridge from the header or the card.

## D. Same conversation everywhere

- [x] A message sent from the editor shows in the sidebar chat (and in Classic Panels mode).
- [x] A half-typed draft shows in both the editor and the sidebar.
- [x] New Thread (toolshelf / Chat menu) clears both.

## E. Disable / enable

- [x] Disable the add-on: node editors are back to normal, no errors in the console. Enable again: all back.
