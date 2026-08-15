---
name: bfa-coworker-screenshots
description: "**WORKFLOW SKILL** — Capture and manage screenshots for the bfa_coworker wiki documentation. Use when: taking screenshots for wiki pages, updating outdated screenshots, or creating annotated UI captures. DO NOT use for: general Blender rendering, image editing unrelated to documentation."
---

# BFA Coworker Screenshot Workflow — Agent Skill

## Overview

This skill covers the **screenshot workflow** for the bfa_coworker wiki documentation.
Screenshots are stored in the wiki repo and referenced from markdown pages.

## Screenshot Storage

```
bfa_coworker.wiki/
├── images/
│   ├── installation/       # Install & enable screenshots
│   ├── configuration/      # Preferences tabs
│   ├── chat-interface/     # Chat panel screenshots
│   ├── local-llm/          # Model download & setup
│   ├── remote-api/         # API configuration
│   └── architecture/       # Diagrams (if not Mermaid)
```

## Naming Convention

`{section}-{description}-{version}.png`

Examples:
- `configuration-local-llm-tab-v1.1.37.png`
- `chat-interface-agent-mode-v1.1.37.png`
- `installation-addon-enable-v1.1.37.png`

## Screenshot Checklist by Page

### Installation.md
- [ ] Blender Preferences → Add-ons → Install from Disk
- [ ] Add-on enabled in the list with checkbox
- [ ] Coworker panel appearing in 3D View sidebar

### Configuration.md
- [ ] Local LLM tab — full view with Operating Mode selector
- [ ] Local LLM tab — Model Presets section (Flagship, Mid-Range, Lightweight)
- [ ] Local LLM tab — Download progress bar active
- [ ] Remote API tab — Provider selector, API key field, model name
- [ ] Generative tab — Backend selector, Poly Haven test buttons
- [ ] Advanced tab — Port settings, skills, custom skills

### Chat-Interface.md
- [ ] Full chat panel in 3D View sidebar
- [ ] Agent mode vs Ask mode toggle
- [ ] @mention popup with object search
- [ ] Conversation history with tool results expanded
- [ ] Reasoning content collapsed/expanded
- [ ] Project rules text editor

### Local-LLM-Setup.md
- [ ] llama-server download button and progress
- [ ] Model download with progress bar
- [ ] "Already Downloaded" state
- [ ] Existing model scanner dropdown
- [ ] llama-server running status indicator

### Remote-API-Setup.md
- [ ] Provider selector with OpenRouter selected
- [ ] API key field (masked)
- [ ] "Test Connection" success result
- [ ] Saved provider profiles list
- [ ] Model browser button

### Troubleshooting.md
- [ ] Port check results (all green)
- [ ] Port check results (some red)
- [ ] Ping/Diagnose results
- [ ] Error message in chat panel
- [ ] Log file location

## Capture Settings

- **Resolution**: 1920x1080 or native — keep consistent
- **Format**: PNG (lossless)
- **Blender Theme**: Default Bforartists theme
- **UI Scale**: 1.0 (default)
- **Window**: Capture the full Blender window for context, crop to relevant area

## Annotation Guidelines

- Use red boxes/arrows to highlight key UI elements
- Number callouts (1, 2, 3...) matching the text description
- Keep annotations minimal — don't overcrowd
- Use consistent annotation style across all screenshots

## Updating Screenshots

When the addon UI changes:
1. Check `CHANGELOG.md` for UI-related changes
2. Identify affected wiki pages
3. Re-capture screenshots with updated version number in filename
4. Update image references in markdown
5. Remove old version screenshots