## 🛠️ What's Fixed

### 🔌 **Claude Code Connects in External Harness Mode** (#98)
- **One click:** pick the *Claude Code* preset in Preferences → Advanced → MCP Server and press **Add to Claude Code** -- the server is written into `~/.claude.json`, then restart Claude Code and check `/mcp`
- Works for the Claude Code CLI **and** the VS Code / JetBrains extensions -- no `claude` command needed
- Your other Claude Code settings are kept, and the previous file is backed up as `.claude.json.bfacw.bak`
- The copied config can also be saved as `.mcp.json` in a project folder
- Before, the instructions pointed at Claude Desktop's config file, which Claude Code never reads

### 🔄 **Automatic Updates Find New Versions**
- The *Bforartists Coworker* extension repository was publishing an empty list, so **Get Extensions** never offered an update. It now lists 1.1.38.
