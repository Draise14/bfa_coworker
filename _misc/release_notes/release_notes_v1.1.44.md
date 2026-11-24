## 🧰 What's New

**Coworker 1.1.44 brings video tools to the Sequencer, and chats that work side by side.** Transcribe a talking clip on your own computer, add styled and animated captions, cut out the silences, edit the video by editing its words, and turn a 16:9 edit into a 9:16 short. The tools sit in the Sequencer's own menus and toolshelf, and they work without starting the agent. Chat tabs can now run at the same time, a chat can hand parts of a job to helper chats, and the agent got safer around your own edits. Coworker needs Blender / Bforartists **5.2 or newer** from now on.

---

## ✨ Highlight Features

### 🎙️ **Transcribe Speech on Your Computer** (#375)
- **Preferences → Speech** (or **Set Up Speech...** in the Auto Captions dialog) downloads a speech model (tiny up to large-v3-turbo) once. It runs on your own machine; your audio doesn't leave it.
- **The agent can read what a clip says,** with the time of every word, and the result is kept next to your .blend so a second run is instant.
- **Long recordings work:** audio is cut into pieces at natural pauses, music and silence give no invented words, and the status line counts "piece 3 of 15". **Esc** stops it, and Stop in the chat stops the transcription it started. (#540, #522)
- WAV, FLAC, MP3, Ogg and AAC sound files all work, including 44.1 kHz stereo. (#520)
- Prefer an online model? **Use Remote for speech** uses your Remote API instead.

### 💬 **Auto Captions** (#376)
- **Strip → Coworker → Auto Captions...** turns a clip's words into caption strips, in four looks: **Clean**, **Bold Pop**, **Word-by-word highlight** and **Boxed**. One **Ctrl+Z** takes the whole run away.
- **No transcript yet? It transcribes first,** in the same click. (#516) It captions **every selected strip** and names any it had to skip, and a re-run keeps captions you edited by hand. (#538)
- **Put them where you want:** Bottom, Top or Middle with a margin, and a caption channel per speaker, with name labels, when you have one sound file per speaker. (#542)
- **Make them move:** each caption can fade, slide, pop or type out, and the words can pop, scale, rise or light up one by one as they are spoken. Pick how it eases (Ease In, Ease Out, Back, Bounce and more) and how fast. (#594, #605)
- **Make them yours:** letter case, font, bold, italic, size and colours. Change a caption's bold or font afterwards and its lit words follow. **Refit Word Highlights** (right-click) lays them out again. Every choice in the dialog has its own icon.
- **SRT in and out:** Strip → Captions → **Import SRT...** and **Export SRT...**. Right-click captions → **Translate Captions** translates many lines at once, keeping their style.

### ✂️ **Remove Silences** (#331)
- **Strip → Remove Silences** (also in the Coworker toolshelf tab) cuts the quiet parts of the selected sound strips and closes the gaps. It listens to all selected sounds and cuts only where all of them are quiet.
- **The whole edit stays together:** pictures, music, other tracks and captions are cut at the same places, and the scene's end follows. With a locked strip in the way, nothing changes and the strip is named. One **Ctrl+Z** undoes it all. (#539)
- The agent can run it too, and tells you how many seconds it removed.

### 📝 **Edit the Video by Editing Its Text** (#498)
- **Strip → Edit Transcript** opens the clip's words in the Text Editor, one line per sentence, each with a small time mark.
- **Delete a line and press Apply:** its picture and sound are cut out of the whole edit and the gap closes. Move a line and the clip moves with it. Fix a typo and only the caption changes. One **Ctrl+Z** brings it all back.
- **Nothing is cut by guessing.** A line Coworker can't match to your words is marked in red and left alone, and a cut of more than half the clip asks first.
- **Play from Line**, **Live** (apply as you type), **Make Captions** and **Remove Filler Words...** (um, uh) are in the same panel. (#593, #595)

### 📱 **Make Vertical and Reframe Layouts** (#333, #606)
- **Strip → Make Vertical** (also in the View menu and the Coworker toolshelf tab) turns your edit into a 9:16 scene with **Centre Crop** or **Blur Fill**, in 1080x1920, 720x1280 or 2160x3840. By default it works in a copy of your scene, and the copy now survives save and reopen. (#537)
- **Captions are laid out again** for the tall frame; a caption you moved by hand stays put. (#497)
- **Reframe layouts, one click each:** Full Frame, Blur Fill, Tracked, and three splits (half and half, two thirds on top, one third on top) that show two crops of the same shot, one above the other. **Swap** and **Reset Framing** correct a split. Each click is one undo step. The Reframe buttons are in the sidebar's Coworker tab, in the toolshelf grid and in the Preview.
- **Follow Subject** (off by default) keeps a person in frame, and needs a vision model.

### 🧙 **Chats Side by Side, and Helper Chats** (#359, #479)
- **Chat tabs work at the same time** on Remote API and External Agent (up to **Chats Running at Once**, 3 by default). **Local** takes turns, because one graphics card does one job at a time; **Local Side by Side** is an opt-in.
- **Two chats that want the same object ask first:** "The 3D chat is changing Cube. Wait for it, or go ahead anyway?" **Stop** ends one chat only; **Stop All Chats** (in the tab menu) ends all of them, helpers too.
- **Revert stays honest.** If another chat worked in between, you get **Revert Both Turns** or **Cancel**, never a quiet undo of someone else's work.
- **A chat can hand parts of a job to helper chats.** They open as tabs, and a **Helpers** card shows them. With **Magic Wording** on (the default) helpers read as wizards and the agent itself is "the Wizard"; turn it off in Preferences → Chat Display for the plain words. (#528, #608)

### 🌳 **Node Trees as Code** (#480)
- **Coworker reads a whole node tree** (Geometry Nodes, shader or compositor) as a short text, one line per node, and **builds or rebuilds a tree** from the same text. Changing one thing in a big tree no longer means rebuilding it node by node.

### 🛡️ **A Safer Agent**
- **The co-work lock no longer gets in the agent's way:** Apply Modifier and other operators work while Coworker works, and objects can't stay unselectable after a save or an undo. (#548)
- **Agent steps wait while you drag a slider or use a tool** like Grab, up to 8 seconds, then say "Not run ... Nothing was changed". While a render or bake runs, steps that would touch the scene are refused. (#576, #591)
- **One Ctrl+Z per chat turn,** and a turn that only looked leaves no undo step. (#501)
- **Cards say what they are about:** the approval card shows the tool and its arguments, nine more file-writing commands (UV layout export and others) ask first, and a harmless `objects.unlink` no longer shows the "delete files" card. (#500, #558, #549)
- **Keys stay hidden** in the Workshop, the job row, cards, helper chats, logs and the tool history. (#511, #528)
- **Permissions in External Harness mode:** the card has a **Permissions** button, and the sidebar and editor header have one too. (#385)

### 🖥️ **A More Reliable Local Engine**
- **The Local server starts the way you set it:** one slot, a context sized to the model and your free graphics memory (32K for the 27B), and your preferences (Quantize KV Cache, backend). The first message gets answered, in 44 s instead of failing. (#543)
- **A request is made to fit or not sent.** What was left out is listed in the chat, a request that can't fit says why, a stuck server is no longer waited on for 15 minutes, and cut-off replies no longer loop. (#544)
- **Local keeps its prompt cache,** so a plan update or a tab switch no longer makes the model read everything again. (#546)
- **Checkpoint summaries come back with text** and take seconds, not half a minute. (#545)
- **Context sizes start at 16K.** 4K and 8K can't hold the first message, so they are gone; a saved 4K or 8K becomes 16K. (#631)
- **Preset memory texts are right:** each model shows its real download size and the memory it needs. (#558)

### 🎨 **New Icons in the Bforartists Style** (#563)
- Coworker's icons are redrawn from Bforartists' own icons: filled, coloured and readable at menu size, in the dark and the light theme. The purple wand is the Coworker mark, and a small wand shows only on tools that need the agent (Translate, Explain, Tidy Nodes).
- **The Sequencer has a Coworker tab** in the toolshelf with icon buttons, and one **Video** panel in the sidebar. Every tool's tooltip ends with whether it works without the agent started. (#504, #607)

<!-- #637: confirm wording against its PR before release -->
### 🌐 **Read a Web Page** (#637)
- **Give the agent a link and it can read the page,** after you allow it on a card that shows the address. **Always** remembers the site until Blender restarts. Local addresses are refused.

---

## 🎨 What's Changed

- **The Classic chat is gone.** Every editor's Coworker tab and the Coworker editor show the same chat; the **Sidebar Chat** preference is removed. (#430)
- **Right-click a chat tab** for Rename..., Close, Close Other Tabs, Start This Chat Fresh, Stop, Copy Whole Chat and New Chat. A name you give stays after saving and reopening the file. (#573)
- **Five Video Sequencer tools for the agent:** list the timeline, list strips, read one strip, move or trim a strip, and look at a frame. (#304)
- **Preview renders are small and quick.** Every "look at the scene" render uses one capped profile; "Render the final image" gives full quality. Sequencer previews cap scene strips too, and a render you stop answers "stopped". (#524, #527)
- **The agent sees pictures** on Local and Remote: a screenshot or a render now reaches the model. (#487)
- **The chat input keeps the size you drag,** and double-clicking its top edge goes back to growing with the text. (#626)
- **The chat's scroll bar is easy to see** with the mouse away. (#627)
- **Your settings are saved when you quit,** also the ones set by buttons such as Set Context Size or Swap Languages. (#575)
- **Big tool calls arrive at once.** A large request, like building a node tree, used to take about 30 seconds just to be read; now it takes a fraction of a second. (#547)
- **The Coworker mode button keeps its ghost icon** with Magic Wording on. (#624)
- **Blender 5.2 is the oldest supported version.** (#515)

## 🛠️ What's Fixed

- **Auto Captions, Remove Silences and the agent's own strip code could crash Blender** with the preview showing. Strips are now removed safely. (#536, #558)
- **Edit Transcript crashed on Apply** after deleting part of a Word-by-word caption's line. It no longer does. (#593)
- **Asset tools:** node-group assets report their real inputs and outputs, "open the Asset Browser" works when none is open and leaves no stray workspace, catalog paths like `Props/Chairs` are found, and "list catalogs" answers with the library's real catalogs. (#535, #589, #602, #603)
- **A chat's question card shows only in its own tab,** also after the agent starts again. (#572)
- **All editors keeps its own chat** after save and reopen. (#579)
- **Restart Coworker** uses the same path as Start: your port offset, a true status, and the Local server back. Stopping the MCP server leaves no pipes or process behind. (#564, #502)
- **The first code step of a turn always takes its snapshot,** so Revert returns the scene exactly. (#505)
- **The compositor tree is read through its current API,** so "add a glare" no longer fails on newer builds. (#503)
- **The chat no longer loses a click or key** when the autosave runs. (#488) **Autosave is now written in a quiet moment** (no render, no agent step, no mouse button held, nothing typed for 2 seconds), and each write leaves a trace in `autosave_trace.txt`. This is a stability change, not a proven fix. (#638)
- **Changing the Operating Mode from a script no longer raises an error.** (#489)
- **Repeated transcriptions after a set-up change are no longer called a loop,** and Stop says "Transcription stopped." (#499)
- **The prompt's highlight ring stays on** while you drag its top edge. (#532)

## ⚠️ Known Limits

- **Open bugs that can touch you:**
  - **Crashes while the agent changes a modifier you are dragging, or after Paste Image (#576).** The agent now waits for your drag, but a crash isn't ruled out. If it happens, keep the crash log and `steps_*.txt` from `~/.cache/bfa_coworker/logs`.
  - **A crash in the autosave (#638).** The change above lowers the chance; it isn't proven to fix it. Keep the crash log and `autosave_trace.txt`.
  - **Local, unattended runs (#591):** a crash after a timed-out step and a render that never ends were reported; steps are refused during a render now, but this is not confirmed fixed.
  - **Make Vertical with a plain Centre Crop (#632)** can halve playback speed in the Sequencer.
  - **Local checkpoints (#545):** summaries return text now, but the model still re-reads the conversation after each checkpoint, and the step-thinking change ships switched off.
  - **Restart Coworker with a Local model (#564) and the Local 27B start (#543):** the fixes are in; a check with a real Local model is still open.
  - **Video and render follow-ups (#541, #527):** smaller findings from the real-footage test and render previews are still open.
- **Not yet tried by hand:** most video tools were checked live by the builders on Blender 5.2.4, Blender 5.4 and Bforartists 5.2.3, with generated clips and made-up speech. Please try them on real footage, and tell us what looks off (hand-test list: #561, 1.1.44 hand tests).
- **Chats side by side were tested with stand-in models,** not yet with real ones. OpenCode stays chained (one chat at a time) until it is measured.
- **Follow Subject** needs a vision model and wasn't tried on a real person. **Track All** is greyed out ("coming").
- **Speech set-up is Windows only** for now; on macOS and Linux use **Use Remote for speech**.
- **The 27B on a card that is nearly full** (partial GPU layers) wasn't tried with a model loaded.
- **Stop still doesn't cancel a running render or bake:** the **X** on the status bar does. This needs a small Bforartists API (#419).

---

## 📥 Install or Update

- **Already on 1.1.37 or newer?** Coworker updates itself: open **Preferences → Get Extensions** and press **Update**.
- **New install:**
  1. Download **`bfa_coworker_installer.zip`** below.
  2. Drag it into Bforartists / Blender.
  3. It adds the Coworker update repository and installs Coworker.
- **Manual:** install `bfa_coworker-1.1.44.zip` with *Preferences → Add-ons → Install from Disk*.

> **Try it:** open the Video Sequencer with a talking clip, set up speech in Preferences → Speech, select the clip and press **Strip → Coworker → Auto Captions...**. Then try **Remove Silences** and **Make Vertical**.

**Requires** Blender / Bforartists 5.2 or newer. Windows is tested; macOS and Linux are untested.

**Full list:** [CHANGELOG](https://github.com/Draise14/bfa_coworker/blob/main/CHANGELOG.md) · **Docs:** [Wiki](https://github.com/Draise14/bfa_coworker/wiki)

## 🔭 Next

**1.1.45: Highlights and a smarter Make Vertical.** Highlights finds the best short pieces (under 60 seconds) in a long video and builds each as its own scene, ready for export (design: #571, build tasks #610 to #616). **Make Vertical** learns to find faces, people and the pointer (#592). Suggest Edits (#378) is shelved for the later wizard work. Follow along in [#97](https://github.com/Draise14/bfa_coworker/issues/97).
