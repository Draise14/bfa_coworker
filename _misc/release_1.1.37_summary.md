# 🚀 Bforartists Coworker v1.1.37 Release

## 🎉 What's New

This release brings **major UX improvements**, **power-user tools**, and **performance optimizations** that make the Coworker more capable and easier to use than ever!

---

## ✨ Highlight Features
### 🔒 **Download Safety** (Tier 3f)
- SHA-256 verification for every model download
- HTTP Range resume via .part files — interrupted downloads resume where they left off
- Atomic rename prevents corrupt partial files
- GPU auto-detection eliminates OOM crashes from wrong --n-gpu-layers
- Temperature auto-switches between Agent (0.2) and Ask (0.35) modes
- Custom model URL: paste any HuggingFace link to download
- Server port auto-selects when configured port is busy


### 🎯 **Message Queue System**
Never lose a message again! If the Coworker is busy processing, your message is automatically queued and processed when ready. See queue status with the new Queue UI.

### 🔍 **Multi-Category Mention System**
Mention anything in your scene! Objects, materials, collections, node groups, worlds, actions — all searchable with category filters. Just type `@` and start typing to filter.

### 📊 **Enhanced Diagnostics & Benchmarks**
- ⏱️ Timing measurements for all benchmark suites
- 📈 6 new editor benchmark suites  
- 💾 Results persistence with comparison support
- 🔧 Debug mode with configurable log levels

### 🎨 **Asset Browser Integration**
- 📚 Browse asset libraries from the agent
- 🔎 Search across libraries by name, type, or tags
- 🏷️ Read node group editor type (Geometry Nodes, Shader, Compositor)
- 📦 Type-aware asset loading

### 🖥️ **Modular Chat Interface**
- 💬 Clean main panel focused on chat
- 📊 Collapsible status sub-panel for diagnostics
- 🔄 Restart button for quick recovery
- ⚠️ Stop-during-thinking guard

### ⚡ **Performance & UX**
- 🎠 Thinking spinner animation
- 📉 Model loading progress bar
- 🛡️ Graceful shutdown with health indicators
- 🏷️ Collection color tag tool
- ⚙️ Conditional advanced settings per mode

---

## 🔧 Technical Highlights

| Feature | Description |
|---------|-------------|
| **MessageQueue** | Thread-safe queue with auto-dequeue |
| **MentionFilter** | Multi-category with text filtering |
| **AssetTags** | Node group editor type detection |
| **BenchmarkTiming** | Suite-level timing measurements |
| **SessionLogging** | Export/copy with versioned history |
| **HealthDots** | Real-time Bridge/MCP/LLM liveness |

---

## 📦 Installation

### From Extension Platform
1. Open Blender → Edit → Preferences → Add-ons
2. Search for "Coworker"
3. Click "Install from Disk"
4. Select the `.zip` file

### From Source
```bash
cd addon/bfa_coworker
python build_addon.py
# Install the generated .zip from Blender Preferences
```

---

## 🐛 Bug Fixes

- 📝 Multiline custom skills text editor
- 🔄 Auto-reset benchmark on completion
- 💾 Versioned session history (keeps last 10)
- 🛡️ Readonly detection for collection tools
- ⚠️ Stop-during-thinking guard prevents accidental stops

---

## 📚 Documentation

- 📖 Updated skills documentation for asset browser
- 🎯 Collection color tag usage examples
- 💡 Mention system category guide

---

## 🙏 Credits

Thank you to all contributors and testers who helped shape this release!

---

## 🔗 Links

- 📖 [Documentation](https://github.com/bforartists/bfa_coworker/wiki)
- 🐛 [Report Issues](https://github.com/bforartists/bfa_coworker/issues)
- 💬 [Discussions](https://github.com/bforartists/bfa_coworker/discussions)

---

**Full Changelog**: https://github.com/bforartists/bfa_coworker/compare/v1.1.36...v1.1.37
