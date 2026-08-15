---
name: bfa-coworker-release-docs
description: "**WORKFLOW SKILL** — Update documentation as part of the bfa_coworker release process. Use when: preparing a new release, updating version numbers in docs, regenerating wiki after code changes, or writing release notes. DO NOT use for: making code changes, running tests, or building the addon."
---

# BFA Coworker Release Documentation — Agent Skill

## Overview

This skill covers the **documentation update workflow** that should happen
as part of every bfa_coworker release.

## Release Documentation Checklist

### Before Release

- [ ] Update `CHANGELOG.md` with all changes since last release
- [ ] Run `python _misc/generate_wiki.py --output-dir ../bfa_coworker.wiki`
- [ ] Verify all auto-generated tables match source (operator count, tool count, property count)
- [ ] Check for broken internal links in wiki pages
- [ ] Update version number references in wiki if needed
- [ ] Review screenshot placeholders — any new UI elements need new placeholders
- [ ] Update `README.md` if tool listing or features changed (run `make readme_update`)

### After Release

- [ ] Tag the wiki repo with the same version tag
- [ ] Update `blender_manifest.toml` version (triggers next cycle)
- [ ] Add new version section to wiki `CHANGELOG.md`
- [ ] Archive old screenshots if UI changed significantly

## Version Number Locations

| File | Field | Example |
|------|-------|---------|
| `addon/bfa_coworker/blender_manifest.toml` | `version` | `"1.1.37"` |
| `CHANGELOG.md` | Section header | `## [v1.1.37] - 2026-08-12` |
| Wiki `Home.md` | Version badge | Current version: **1.1.37** |
| Wiki `CHANGELOG.md` | Section header | `## [v1.1.37]` |

## Auto-Generated Content Verification

After running the generator, verify:

1. **Operator count**: `grep -c "bl_idname" API-Glossary/Operators-Reference.md` should match `__init__.py` `_classes` tuple length (29)
2. **MCP tool count**: Count tools in `API-Glossary/MCP-Tools-Reference.md` should be 24
3. **Preference count**: Count properties in `API-Glossary/Preferences-Reference.md` should be 50+
4. **Sidebar links**: Every `.md` file in the wiki should appear in `_Sidebar.md`
5. **No broken links**: All `[[...]]` references should resolve to existing pages

## Common Release Documentation Tasks

### Adding a New Operator
1. Add operator class to `operators_*.py`
2. Add to `__init__.py` `_classes` tuple
3. Run `make wiki` — operator auto-appears in Operators Reference
4. If operator adds new UI, add screenshot placeholder to relevant page

### Adding a New MCP Tool
1. Add tool module to `mcp/blmcp/tools/`
2. Run `make wiki` — tool auto-appears in MCP Tools Reference
3. Update `MCP-Tools.md` with use case description

### Adding a New Preference
1. Add property to `preferences.py`
2. Run `make wiki` — property auto-appears in Preferences Reference
3. Update `Configuration.md` if it changes the user workflow

### UI Redesign
1. Update all affected screenshot placeholders
2. Re-capture screenshots for affected pages
3. Update `Configuration.md` and `Chat-Interface.md` descriptions