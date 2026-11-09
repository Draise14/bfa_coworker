## 🧵 What's New

**Coworker 1.1.42 gives each editor its own conversation.** Ask about materials in the Shader Editor and about strips in the Sequencer, and the two chats stay apart, each with its own history, queue and Stop. Long renders and bakes no longer time out: Coworker starts them, shows a job row in the chat and checks back. And Coworker now steps in when the agent keeps repeating itself, and keeps your API keys out of every log and copy.

---

## ✨ Highlight Features

### 🧵 **Threads: Each Editor Has Its Own Conversation** (#357, #358)
- **How to use it:** click the editor's icon on the sidebar chat's header to turn on **Use Editor Context Only**. That editor's chat now shows **its own thread**, with its own history, Queue, Stop and status. Two 3D Views share one thread.
- **Nothing changes if you don't:** with it off, every sidebar shows the shared **All editors** thread, the single conversation you had before.
- **Thread tabs** on the sidebar chat's header:
  - switch between *All editors* and *this editor*;
  - a status icon shows when a thread is thinking, waiting for you or for its turn, or finished while you weren't looking;
  - **New Thread Here** (+) gives this area a fresh thread, **Switch Thread** continues a chat from History here, and **Open in Coworker Editor** shows the thread full size.
- **Threads panel** in the Coworker editor's toolshelf: every open thread with its editor icon, title and status. *Done* means it finished while you were elsewhere. Click one to show it.
- **One turn at a time:** Coworker works on one thread at once. A message from another editor waits in its own thread, and the chat tells you whose turn it is waiting for. Stop in one thread leaves the others alone.
- **Saved with your file:** each editor's thread is saved as its own chat in History. Reopen the .blend and each editor brings back its last chat.
- **Right-click knows where you are:** *Explain This* and *Translate…* run in the thread of the editor you right-clicked, and a translation is written back from there.
- **Undo says which thread:** a turn's undo step reads *Coworker (Node)'s turn*.
- **External Agent:** each thread gets its own conversation with the same agent app.

### ⏳ **Long Renders and Bakes Don't Time Out** (#309)
- **What changed:** a render or bake no longer has to finish inside one tool call. When one is still running after 45 seconds, Coworker hands it over as a **job**, and the agent checks on it with the new `get_job_status` tool until it is done.
- **You can see it:** the newest turn shows a **job row**, such as *Rendering 40% — Render big.png*, even with the Workshop folded. Under it sits Blender's own progress bar, with the real bake percentage and the **X** that cancels the job.
- **New bake tool:** ask Coworker to bake, and `bake_textures` checks first that each object has a UV map, a material and an active Image Texture node with an image. If one is missing, it tells you what to add. It switches to Cycles for the bake and back afterwards.
- **Safer renders:** Coworker no longer starts a second render while one is running, and an old output file can't pass for the new one.
- Works the same on every engine: Local, Remote, External Agent and External Harness.

### 🔁 **Coworker Asks When the Agent Repeats Itself** (#310)
- **The 3rd time** the agent makes exactly the same call, a card asks you: *Coworker wants to run … a 3rd time with the same input*. **Allow** lets it try again, **Deny** stops it.
- **The 4th time** is refused, and the agent is told why.
- **Normal work isn't flagged:** reads that change nothing get more room, and checking on a render or bake never counts.
- **Auto mode** declines the 3rd repeat at once, so an overnight run can't spin in place.

---

## 🔐 Security

- **Your keys stay out of logs, exports and copies** (#346). The Remote API key, the Hugging Face token, the generation API key and the session token show as `***` in the console, `coworker.log`, Export and Copy Session Log, Copy Error, the chat's Copy buttons and the activity view's Copy Log. Preferences says so under the API key. Files that hold a key are readable by you only. The settings backup still keeps your keys, because that is how they survive an update, but it never prints them.
- **Web pages can't talk to the MCP server through a disguised address** (#345). Its HTTP endpoint only answers requests addressed to your computer, from pages served on your computer. In Network mode your machine's address is allowed too, and `BFACW_ALLOWED_HOSTS` / `BFACW_ALLOWED_ORIGINS` add more.
- **The agent can't switch off the safety checks** (#347). A special comment in code used to skip the risky-step check and preflight. It now works only for Coworker's own tool code, never for code the agent writes.

## 🛠️ What's Fixed

- **Your settings come back after an update or reinstall** on Blender / Bforartists 5.x. Before, the backup was saved but never restored, so you were back to the defaults (API key, mode, ports). (#396, #400)
- **Microsoft Store Bforartists:** External Agent connects. It used to fail with *"cwd" does not exist*, because a Store copy keeps new files in its own package folder where other programs can't see them. *Add to <client>* now edits an existing config file in place. (#390)
- **The floating chat keeps working after Ctrl+Space** in the main window. Before, it stopped taking clicks and keys until you closed it. (#391)
- **Attach Screen and Auto mode's next step work while a window is maximized.** (#401)
- **Network mode starts quickly** even when your network's name lookup is slow. (#403)
- **Linux: Download llama-server works** (it gave HTTP 404) and the download runs. A custom llama-server path accepts the program, its folder or a llama.cpp `build/bin`, and says why when it can't use it. (#382)

## ⚠️ Known Limits

- **Stop doesn't cancel a running render or bake.** It ends the agent's wait; the **X** on the job row's bar, or **Esc**, cancels the job itself. Bforartists needs a small API for this (#419).
- **The draft, the attached image, the Goal & Plan panel, usage and Auto mode are still per window**, not per thread, and a maximized editor has no thread picker yet (#417).
- **Remote mode hasn't been checked live with threads** or with the loop guard yet. It runs the same code as Local, which was.
- **Threads on OpenCode** haven't been tried; Claude Code was checked live.
- **The Store fix and the Linux llama-server fix haven't been run on a real Store install or on Linux yet.** They were tested against the reporters' details and the real llama.cpp download. Please tell us how they work for you.

---

## 📥 Install or Update

- **Already on 1.1.37 or newer?** Coworker updates itself: open **Preferences → Get Extensions** and press **Update**.
- **New install:**
  1. Download **`bfa_coworker_installer.zip`** below.
  2. Drag it into Bforartists / Blender.
  3. It adds the Coworker update repository and installs Coworker.
- **Manual:** install `bfa_coworker-1.1.42.zip` with *Preferences → Add-ons → Install from Disk*.

> **Trying threads?** Open the sidebar chat in two editors (say, the 3D View and the Shader Editor), click each one's editor icon on the chat's header, and ask each its own question. Both show up in **History** as separate chats.

**Requires** Blender / Bforartists 5.1 or newer. Windows is tested; macOS and Linux are untested.

**Full list:** [CHANGELOG](https://github.com/Draise14/bfa_coworker/blob/main/CHANGELOG.md) · **Docs:** [Wiki](https://github.com/Draise14/bfa_coworker/wiki)

## 🔭 Next

**1.1.43: the Sequencer core.** Local transcription, Auto Captions, Remove Silences and Reframe for Vertical. After that: the draft, plan and usage move into each thread, and threads run side by side, so a scene build and a node tidy can work at the same time. Follow along in [#97](https://github.com/Draise14/bfa_coworker/issues/97).
