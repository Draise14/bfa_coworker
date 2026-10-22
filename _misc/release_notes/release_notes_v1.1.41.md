## 🛟 Autosave Is Back

**Coworker 1.1.41 is a hotfix, and we recommend it for everyone on 1.1.40.** While Coworker was enabled, Bforartists' and Blender's own **autosave almost never ran**. If the program crashed, you could lose everything since you opened the file. This update fixes that.

---

## 🛠️ What's Fixed

### 💾 **Autosave works again while Coworker is enabled** (#370)
- **What went wrong:** Blender doesn't autosave while a *modal operator* is running, to avoid saving in the middle of an action. The new chat watches your mouse and keys through one of these, in every window, all the time. So autosave kept waiting, and in long sessions it never got a turn.
- **What changed:** about every 30 seconds, Coworker steps aside for a quarter of a second, and the waiting autosave goes through. It only does this when you haven't typed or clicked in the chat for a moment, so your typing never ends up as a shortcut in another editor.
- **Your settings still apply:** the interval in *Preferences → Save & Load → Auto Save* is used as before, and nothing changes if autosave is off.
- **Checked live:** with Coworker's chat running, the autosave file was written on time with this update and not at all without it.

> **Lost work in 1.1.40?** Look in **File → Recover → Auto Save…** first. Autosave files are in your temporary folder (on Windows, `%TEMP%`), named after your file. Copy the one you need somewhere safe before you start Bforartists again, because a new session can replace it.

---

## 📥 Install or Update

- **Already on 1.1.37 or newer?** Coworker updates itself: open **Preferences → Get Extensions** and press **Update**.
- **New install:**
  1. Download **`bfa_coworker_installer.zip`** below.
  2. Drag it into Bforartists / Blender.
  3. It adds the Coworker update repository and installs Coworker.
- **Manual:** install `bfa_coworker-1.1.41.zip` with *Preferences → Add-ons → Install from Disk*.

**Requires** Blender / Bforartists 5.1 or newer. Windows is tested; macOS and Linux are untested.

**Full list:** [CHANGELOG](https://github.com/Draise14/bfa_coworker/blob/main/CHANGELOG.md) · **Everything new in 1.1.40:** [v1.1.40 release](https://github.com/Draise14/bfa_coworker/releases/tag/v1.1.40) · **Docs:** [Wiki](https://github.com/Draise14/bfa_coworker/wiki)
