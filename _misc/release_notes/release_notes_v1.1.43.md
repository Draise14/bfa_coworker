## 🧰 What's New

**Coworker 1.1.43 is a fixes-and-polish release.** It works in Blender now, not only in Bforartists, and Claude Code gets Coworker's tools on a fresh install. Each .blend brings back its own chats and tabs when you open it. And the chat behaves more like the apps you know: paste a screenshot with Ctrl V, select text with the mouse and copy it, and right-click for the chat's own menu.

---

## ✨ Highlight Features

### 📋 **Paste an Image into the Chat** (#450)
- **Ctrl V** (Cmd V) in the chat's input attaches a copied image to the chat you're typing in, in the Coworker editor and in the sidebar chat.
- **What you can paste:** a screenshot, a picture copied from a browser or an image editor, an image file copied in Explorer, or the copied path of an image file. The path isn't typed into your message.
- **Text still pastes as text.** If the clipboard holds both text and a picture (like cells from a spreadsheet), Ctrl V pastes the text. **Paste Image** takes the picture: it's in the attach button's menu, the Chat menu, the right-click menu and the editor's Attach tools.
- To read the picture, Coworker briefly turns an editor into an Image Editor and switches it straight back. It never uses your 3D View, so a rendered viewport doesn't restart.

### 🖱️ **Select and Copy Text with the Mouse** (#451, #455)
- **In your message:** drag to select, double-click to select a word. A single click still just places the cursor.
- **In the conversation:** drag across lines, lists, code and even several messages; the chat scrolls with the wheel while you drag. Double-click selects a word, and the live Thinking box can be selected too.
- **Ctrl C** (Cmd C) copies the selection while the mouse is over the chat. You get the text as it reads on screen, and API keys show as `***`. Anywhere else Ctrl C works as before, so it still copies objects in the 3D View.
- **Esc** or a click elsewhere clears the selection. The right-click menu has **Copy Selection**.

### 📎 **The Chat's Own Right-Click Menu** (#453)
- Right-click in the chat no longer opens the Node Editor's menu. It opens Coworker's:
  - **on a message:** Copy Message, Copy Code (on a code block) and Revert (on the newest reply);
  - **on your input:** Cut, Copy, Paste and Select All;
  - **always:** Attach Image, Capture Editor, Capture Render, New Chat, Stop (while Coworker works), **Copy Whole Chat** (this conversation, without the tool steps), History and Preferences.
- Right-click outside the chat works as before.

### 🗂️ **Each .blend Brings Its Chats Back** (#431)
- **Open a file and its conversation is there**, with its Goal & Plan and memory, so Coworker continues where you left off. Its tabs come back too, and each area shows the tab it had.
- **Switching files keeps chats apart.** Before, opening a second file in External Agent kept the first file's chat on screen and could save it into the second file's chat.
- **External Agent** picks up each chat's own conversation with the agent, and each tab keeps its own.
- **Save As keeps your chat**, under the new file name.
- **History by file:** your files, newest first, each with an **Open** button and its chats under it. Chats from files you never saved sit under *Not saved in a file*. Search finds chat titles and file names.

---

## 🎨 What's Changed

- **The chat input scrolls and resizes.** A long message no longer hides its first lines: the mouse wheel over the input scrolls it, and the cursor stays in view while you type. Drag the input's top edge (or press Ctrl+Up / Ctrl+Down) to make it taller or shorter. Coworker remembers the size, one for sidebars and one for the Coworker editor. (#389)
- **+ starts a chat for All editors.** In an editor's sidebar, the new button with the editor's icon starts a chat about that editor (*New Chat for This 3D Viewport*, *New Chat for This Shader Editor* and so on). (#466)
- **Start This Chat Fresh** in the tab menu (▾), the Chat menu and the right-click menu: the chat goes to History and its tab starts empty, in the same place. *New Thread* is now called **New Chat**. (#466)
- **The Coworker editor's header:** the chat tabs sit in the centre, and the S / M / L thumbnail buttons moved into a **Chat Display** dropdown. In a narrow editor everything still fits: the mode shows as icons and the status gets shorter, so Start / Stop and Preferences stay in view. (#467)
- **The Thinking box** shows headings, lists, bold and code properly, and is only as tall as its text, up to 8 lines with the newest at the bottom. It doesn't jump while Coworker thinks. (#452)

## 🛠️ What's Fixed

- **Coworker works in Blender.** It failed to turn on in Blender (*enum "WIZARD" not found*) because it used some icons that only Bforartists has. Those icons now have Blender stand-ins, and if the add-on ever fails to turn on, it no longer leaves broken panels behind. (#456)
- **Claude Code gets Coworker's tools on a fresh install.** In 1.1.42, External Agent could talk but had no tools, and External Harness showed *CONNECTION_CLOSED*, unless you had started Local first. Every mode now checks that the server's parts work and repairs them on the first start. *Check Status* and *Check Ports* also show the right things in External Agent mode. (#458)
- **Claude Code on Opus 5.5 stopped refusing Coworker's requests.** Coworker's instructions asked the model to "think aloud", which Claude now blocks. They now ask for a short line before and after each step instead, so you still follow the work. (#460)
- **Images go to the chat you attached them in.** Attaching from the file browser, Capture and Explain This Spot used to land in *All editors*. EXR, HDR, TGA and PSD images are attached as a PNG copy (EXR and TGA used to be refused). (#449)
- **Capture works every time and always closes.** A second capture no longer starts frozen on the last rectangle: the highlight follows the mouse, and **Enter** takes the last rectangle again. Opening a file during a capture no longer leaves the dark overlay up. (#454)
- **Two chat glitches are gone.** The messages could go blank for a moment while a reply came in (Local and Remote), and the sidebar chat could disappear after you changed Resolution Scale, moved to a monitor with another DPI or changed the chat's rows. If you still see a glitch, turn on **Chat Draw Trace** in Status & Diagnostics, press **Copy** and paste it into an issue. (#387)
- **Coworker can free its port on Windows.** It now sees a server left behind by a Blender that has closed, and frees that port instead of always moving to the next one. Another open Blender window's server is never touched. (#473)

## ⚠️ Known Limits

- **Not yet tried live on Bforartists:** the right-click menu, paste, text selection, the image and capture fixes, chats by file, the + button, the new header, the Thinking box, the resizable input and the two chat glitch fixes were checked live on Blender 5.2.4. Bforartists runs the same code, but please tell us if anything looks off there.
- **Paste and text selection haven't been tried on macOS or Linux** (Cmd V, Cmd C, Linux clipboards).
- **The Classic chat panel** doesn't get paste, text selection or the new Thinking box. It is removed in 1.1.44.
- **Claude Code fixes:** the tools fix was checked by starting Coworker's server the way Claude Code does, on Blender 5.2.4 and Bforartists 5.3, and the "think aloud" change by tests. A full Claude Code session wasn't run for either, and Claude's check isn't the same every time, so please report a blocked request if you see one.
- **The chat drawn over another sidebar tab** (reported in #387) couldn't be reproduced. The Chat Draw Trace will catch it if it happens to you.
- **The Windows port fix** was checked by tests, not in a live session with two Blender windows open.
- **A chat started in Local or Remote and continued in External Agent** starts the agent without that chat's earlier messages.
- **Stop still doesn't cancel a running render or bake:** the **X** on the status bar does. This needs a small Bforartists API (#419).

---

## 📥 Install or Update

- **Already on 1.1.37 or newer?** Coworker updates itself: open **Preferences → Get Extensions** and press **Update**.
- **New install:**
  1. Download **`bfa_coworker_installer.zip`** below.
  2. Drag it into Bforartists / Blender.
  3. It adds the Coworker update repository and installs Coworker.
- **Manual:** install `bfa_coworker-1.1.43.zip` with *Preferences → Add-ons → Install from Disk*.

> **Try it:** take a screenshot (Win+Shift+S), click in the chat's input and press **Ctrl V**. The picture goes with your next message. Then drag over a reply and press **Ctrl C**.

**Requires** Blender / Bforartists 5.1 or newer. Windows is tested; macOS and Linux are untested.

**Full list:** [CHANGELOG](https://github.com/Draise14/bfa_coworker/blob/main/CHANGELOG.md) · **Docs:** [Wiki](https://github.com/Draise14/bfa_coworker/wiki)

## 🔭 Next

**1.1.44: the Sequencer core.** First the Classic chat panel goes, so the chat is the same everywhere. Then local transcription, Auto Captions, Remove Silences and Reframe for Vertical. After that: conversations that run side by side, so a scene build and a node tidy can work at the same time. Follow along in [#97](https://github.com/Draise14/bfa_coworker/issues/97).
