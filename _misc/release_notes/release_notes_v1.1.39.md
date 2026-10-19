## 🛠️ What's Fixed

### 🔌 **External Harness: Connect Any Supported App in One Click** (#100)
- **Add to Claude Desktop** (or Cursor, Windsurf, Codex, OpenCode, Claude Code) in Preferences → Advanced → MCP Server writes the connection into that app's settings -- your other settings are kept and a backup is made
- **Configs keep working after an update or move.** Before, installing with the Coworker Installer could quietly break a working Claude Desktop setup; now the connection follows Coworker wherever it's installed
- **Test Config checks what your app will really run** and tells you if its entry is missing, out of date, or pointing at the wrong port
- Cursor, Windsurf, Cline, Codex and OpenCode were given a format they couldn't read -- each now gets exactly what its own documentation asks for. New: **VS Code (Copilot)**

> **Already set up a harness?** Open Preferences → Advanced → MCP Server, pick your app and press **Test Config**. If it reports an older config, press **Add to \<app\>** to update it (for Claude Desktop, quit it fully first).

### 🧱 **Fewer False Stops When an AI App Drives Blender** (#101)
- Working scripts are no longer refused for style reasons (the "add an existence check" and `active_object` stops) -- the advice comes back as a note while the script runs
- No more scripts silently "auto-fixed" into broken code for animation curves and smooth shading on Blender 5.x
- The pre-tested templates (`execute_blender_plan`) now actually work from Claude Desktop and other apps, and the app gets a short map of which tool to use for what

### 🌄 **Poly Haven Fixes** (#101)
- **HDRIs no longer overwrite another scene's world** -- the active scene gets its own world, and your previous world is kept
- **Textures with packed maps** no longer fail on Blender 5.x
- **Models arrive complete** with their geometry and textures

### ⏱️ **Faster Benchmarks** (#104)
- Diagnostics now has **6 core benchmarks** and **3 quick challenges** -- 31 steps instead of about 70, covering the same ground
