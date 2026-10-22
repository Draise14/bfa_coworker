## 🎉 What's New

**Coworker 1.1.40 is the biggest release yet.** The chat has moved out of a small panel into its own **Coworker editor**, and a compact copy sits in **every editor's sidebar**. A new mode, **External Agent**, lets the agent app you already use (Claude Code, OpenCode, Codex, Gemini, Copilot) do the work through Coworker's Blender tools. Right-click almost anything to **Explain** or **Translate** it. And every engine now shares the same plan, questions, permissions, history and one-click Revert.

![The Coworker editor](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/coworker_editor.png)

---

## ✨ Highlight Features

### 💬 **The Coworker Editor** (#26)
- Pick **Coworker** in any area's *Editor Type* menu, or press the editor button on the sidebar chat. It opens a full-size chat drawn in your theme's colours, fonts and Bforartists' own icons.
- It works like a chat app:
  - the newest message is at the bottom, with the input pinned under it;
  - **Enter sends** and **Shift+Enter** adds a line;
  - type **@** to mention objects, materials, collections, node groups, worlds or actions;
  - Ctrl+wheel zooms.
- Replies render **markdown**: headings, lists, tables, and code with a **Copy** button. The **Workshop** folds the tool steps away, so you see the answer first.
- **Images:**
  - drag an image onto the chat, or attach the render or a part of the screen;
  - your message shows them as thumbnails;
  - pin an image to keep it for every message.
- **Typing in another script:** **Edit** opens a native text field for Chinese, Japanese or Korean input.
- **Open it in its own window** to keep the chat beside your work.

### 🧭 **The Coworker Tab in Every Editor** (#291)
- **Where:** the compact chat, with Queue, Plan, History and Status, is in the sidebar of:
  - the **3D View** and **Node editors** (Shader, Geometry, Compositor, Texture);
  - the **Image / UV**, **Sequencer**, **Movie Clip**, **Dope Sheet**, **Graph**, **NLA**, **Text** and **Spreadsheet** editors.
- **It knows where you are:** a message says which editor it came from and what that editor shows (the material's node tree and active node, the active strip, the image, the text and line).
- **Use Editor Context Only:** the editor icon on the chat's header keeps Coworker to what that editor shows, and the rest of the scene is left alone.
- **Classic panels:** *Preferences → Coworker → Chat Display → Sidebar Chat* switches back to them.

![The Coworker tab in the Node Editor](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/sidebar_node_editor.png)

### 🤝 **New Mode: External Agent** (#93)
- **Your agent, Coworker's tools:** chat in Coworker while an agent app on your computer does the work. **Claude Code** and **OpenCode** are tested live; **Codex**, **Gemini** and **Copilot** are in preview; any ACP agent works through *Custom Command*.
- **Setup you can follow:** pick the agent and press **Start**. Coworker shows its version, location and whose account it uses, and offers **Install / Update / Remove**. If something's missing, it tells you what.
- **Safe by default:** the agent may only use Coworker's tools; its own shell and file edits are refused.
- **The same chat as the other modes:**
  - Send, Queue, Stop, images and Ask / Plan mode all work;
  - the agent's thinking, tool steps (with the code it ran) and reply appear in the chat;
  - every reply is headed **Coworker**.
- **Approvals in the chat:** when the agent asks before a tool, a card says who wants to run what, with *Allow once*, *Allow this turn* or *Deny*.
- **Picks the agent's own settings:** its own **Mode / Model / Effort** dropdowns, and its own **/ commands** when you type `/`.
- **Resume:** reopen Bforartists, press Start, and the agent carries on the same conversation. *New Thread* starts a fresh one.
- **Hardened:**
  - the agent closes with Bforartists, even on a crash;
  - login errors say how to log in;
  - a crash shows the agent's last output;
  - a Stop it ignores closes it after 10 s.

![External Agent mode](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/external_agent.png)

### 🔍 **Explain This** (#290) and 🌐 **Translate** (#42)
- **Explain This:** right-click any setting or button, and Coworker explains what it does and when to use it (in Ask mode, so nothing changes).
  - In an editor's empty space, the right-click menu offers **Explain This Editor**.
  - On header menus and popovers, it captures the spot you clicked, circles it in red and asks about that.
- **Translate…** works on a text field, a file path, a 3D text object, a text strip or the Text Editor (the selection or the whole text).
  - Pick **From / To** (both remembered; ⇄ swaps them).
  - Coworker writes the translation back as one undo step. It works even in a text object's Edit mode.

| | |
|---|---|
| ![Explain This in the right-click menu](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/explain_this.png) | ![Translate's From / To dialog](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/translate_dialog.png) |

### 🔒 **Permissions: Strict / Permissive / Auto** (#292)

![An approval card in the sidebar chat](https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/permissions.png)

- **Where:** the lock button left of **Send**. It applies to every engine and switches at once, even while the agent runs.
- **Strict** (default) asks before every change, with **Allow once**, **Allow this turn** and **Deny**.
- **Permissive** asks only before saving or opening files, deleting, exporting and running programs.
- **Auto** never asks.

### 🗂️ **Chat History, Plan, Queue and Revert — on Every Engine**
- **History** (#156): your chats in one list, not tied to the .blend.
  - Open, pin, rename, delete and search.
  - **Open** loads the chat's .blend straight away. It asks to save only when you have unsaved changes.
- **Plan mode** (#194): next to *Coworker* and *Ask*. It makes a step plan with read-only tools, then **Carry Out the Plan** builds it on any engine. The **Plan panel** shows the goal and steps ticking off.
- **Auto mode** (#177): keeps working, overnight if you like, while the plan is on track. It stops on done, on limits, when it makes no progress, on errors or on Stop, and says why.
- **Queue cards** (#197): type ahead while Coworker works. Each waiting message is a card with Next, Edit and Delete.
- **Revert this turn:** every turn is one undo step, and the Undo button on the last reply takes back the whole turn.
- **Questions as buttons:**
  - Coworker asks with clickable choices; the green one is its recommendation.
  - A reply's closing question becomes **suggestion buttons**.

<img src="https://raw.githubusercontent.com/Draise14/bfa_coworker/main/docs/images/v1.1.40/history_plan_queue.png" alt="Queue, Plan and History in the Coworker editor's sidebar" width="250">

### 🎛️ **One Mode Switcher, One Model / Effort Block** (#192–#195)
- **Mode switcher:** Local, Remote and Agent side by side, then Harness. It sits at the top of the Coworker panel, in Preferences, and as a menu on the chat's mode icon.
- **Model / Effort:** the same block for every engine, in the same place.
- **A bottom toolbar on the sidebar chat** (#263): new thread, attach, images, plan and more as square icon buttons, with a tooltip on every button.
- **Attach Screen** (#200): click an editor or drag a rectangle, like the Asset Browser's thumbnail capture. It works in every window, and Attach Render works without a 3D View.

---

## ⚡ Under the Hood

- **One engine for every mode.** Local, Remote and External Agent run through the same engine, so they share the plan, questions, scene digest, Revert, permissions and benchmarks.
- **New tools for every engine:**
  - `ask_user`, a question with buttons;
  - `update_plan`;
  - `get_scene_digest`, a short scene summary that answers "unchanged" on repeat;
  - `read_tool_output`;
  - `load_tools`;
  - `get_attached_image`, which brings images into External Harness.
- **Tools on demand** for External Agent: the basic tools first and the specialised ones when it asks. That saves about **8K tokens per request**.
- **Short tool results:** a result over ~8,000 characters arrives as a short preview the agent can read on from.
- **External Harness activity view:** every tool call your outside app makes in Blender, live, with *Copy Log*.
- **Benchmarks on every engine**, compared side by side.
- **Faster drawing:** a long Workshop draws only the rows in view, about 10× faster per frame.

## 🔐 Security

- **Web pages can no longer run code in Blender** (#123). Every request to Coworker's local servers must carry a private session token that only your own programs can read. Coworker, its MCP server and the External Harness launcher send it for you, so existing configs keep working.
- **Risky steps ask first:** with **Permissive** permissions, code that would save, open or quit, delete or move files, export or run a program asks you first, on every engine.

## 🛠️ What's Fixed

- **External Agent after re-enabling the add-on:** its tools reach Blender again. Before, the chat said *Connected* while every scene tool failed (#352).
- **Node tools on Blender 5.x** read the compositor correctly, and a read never creates a node tree any more.
- **`list_blender_templates` works** in every setup.
- **Quitting no longer wipes the saved chat**, in every mode.
- **Benchmark results are saved again.**
- **Revert** undoes the whole turn, even when its tools made undo steps of their own.
- **Status & Diagnostics** shows the real mode (e.g. *External Agent (Claude Code)*).
- **The sidebar chat:**
  - its scrollbar is no longer see-through over the viewport;
  - it doesn't blip away while you scroll;
  - it doesn't draw over its top bar.
- **Dropping images** works while the chat input has focus, and the Attach Screen veil shows only in its own window.
- **Every chat button has a tooltip;** icon buttons are square; joined rows draw one line between them.

---

## 📥 Install or Update

- **Already on 1.1.37 or newer?** Coworker updates itself: open **Preferences → Get Extensions** and press **Update**.
- **New install:**
  1. Download **`bfa_coworker_installer.zip`** below.
  2. Drag it into Bforartists / Blender.
  3. It adds the Coworker update repository and installs Coworker.
- **Manual:** install `bfa_coworker-1.1.40.zip` with *Preferences → Add-ons → Install from Disk*.

> **Trying External Agent?** Choose *Agent* in the mode switcher, pick Claude Code or OpenCode and press **Start**. Coworker tells you if the agent still needs to be installed or logged in. Guide: [External Agent](https://github.com/Draise14/bfa_coworker/wiki/External-Agent).

**Requires** Blender / Bforartists 5.1 or newer. Windows is tested; macOS and Linux are untested.

**Full list:** [CHANGELOG](https://github.com/Draise14/bfa_coworker/blob/main/CHANGELOG.md) · **Docs:** [Wiki](https://github.com/Draise14/bfa_coworker/wiki)

## 🔭 Next

Threads per editor, so a scene build, a node tidy and a strip translation can run side by side. Also the Text Editor as a coding IDE, and the rest of Tier 4's quick wins. Follow along in [#97](https://github.com/Draise14/bfa_coworker/issues/97).
