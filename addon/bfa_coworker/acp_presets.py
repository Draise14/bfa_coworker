# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
External Agent presets and detection (Tier 4h, task "Agent 3").

Each :class:`AgentPreset` says how to start one ACP agent, how to install it,
and which environment switches keep it focused on Coworker's tools (the
"Only Coworker tools" option).  :func:`detect` checks whether an agent is
installed and which version, and returns a plain-language hint when not.

Windows: npm installs ``.cmd`` shims.  Running a shim goes through
``cmd.exe``, whose argument handling is unsafe and slow, so
:func:`resolve_command` reads the shim and launches what it points to
directly: the native ``.exe`` (OpenCode) or ``node <script.js>`` (adapters).

Package and command names were checked against the npm registry on
2026-10-19.  Free of ``bpy``; never raises from :func:`detect`.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = (
    "AgentPreset",
    "Detection",
    "PRESETS",
    "detect",
    "generic_preset",
    "launch_env",
    "preset_by_id",
    "resolve_command",
)


@dataclass(frozen=True)
class AgentPreset:
    """How to start, install and isolate one ACP agent."""

    id: str
    name: str
    command: str                       # executable name looked up on PATH
    args: tuple[str, ...] = ()         # fixed arguments
    acp_flag_candidates: tuple[str, ...] = ()  # pick the first one "--help" lists
    npm_package: str = ""
    install_hint: str = ""
    login_hint: str = ""
    docs_url: str = ""
    maturity: str = "preview"          # "supported" (tested) or "preview"
    needs_node: bool = False
    isolation_env: tuple[tuple[str, str], ...] = ()
    plan_tool: str = ""                # agent's todo tool, if it reports plans that way
    account: str = ""                  # whose login / subscription it uses (plain words)


PRESETS: tuple[AgentPreset, ...] = (
    AgentPreset(
        id="opencode", name="OpenCode", command="opencode", args=("acp",),
        npm_package="opencode-ai",
        install_hint="Install OpenCode: npm install -g opencode-ai (or see opencode.ai).",
        login_hint="Pick a model or provider in OpenCode once (run `opencode` in a terminal).",
        docs_url="https://opencode.ai/docs/",
        maturity="supported",
        # Measured in the ACP spike (#111): stops OpenCode loading the user's
        # other installed skills (privacy) and saves ~5K tokens per turn.
        isolation_env=(("OPENCODE_DISABLE_CLAUDE_CODE", "1"),
                       ("OPENCODE_DISABLE_EXTERNAL_SKILLS", "1"),
                       ("OPENCODE_DISABLE_AUTOUPDATE", "1")),
        plan_tool="todowrite",
        account="Uses the model you set up in OpenCode (any provider's API key, or a local model).",
    ),
    AgentPreset(
        id="claude_code", name="Claude Code", command="claude-agent-acp",
        npm_package="@agentclientprotocol/claude-agent-acp",
        install_hint=("Install the Claude Code ACP adapter: "
                      "npm install -g @agentclientprotocol/claude-agent-acp (needs Node.js)."),
        login_hint="Log in to Claude Code once (run `claude` in a terminal), or set ANTHROPIC_API_KEY.",
        docs_url="https://github.com/agentclientprotocol/claude-agent-acp",
        needs_node=True,
        account="Uses your Claude Code login: your Claude Pro / Max plan, or an Anthropic API key.",
    ),
    AgentPreset(
        id="codex", name="Codex CLI", command="codex-acp",
        npm_package="@agentclientprotocol/codex-acp",
        install_hint="Install the Codex ACP adapter: npm install -g @agentclientprotocol/codex-acp (needs Node.js).",
        login_hint="Log in to Codex once (run `codex` in a terminal), or set OPENAI_API_KEY.",
        docs_url="https://github.com/agentclientprotocol/codex-acp",
        needs_node=True,
        account="Uses your Codex login: your ChatGPT plan, or an OpenAI API key.",
    ),
    AgentPreset(
        id="gemini", name="Gemini CLI", command="gemini",
        acp_flag_candidates=("--acp", "--experimental-acp"),
        npm_package="@google/gemini-cli",
        install_hint="Install Gemini CLI: npm install -g @google/gemini-cli (needs Node.js).",
        login_hint="Log in to Gemini CLI once (run `gemini` in a terminal).",
        docs_url="https://github.com/google-gemini/gemini-cli",
        needs_node=True,
        account="Uses your Google login (Gemini), or a Gemini API key.",
    ),
    AgentPreset(
        id="copilot", name="GitHub Copilot CLI", command="copilot",
        acp_flag_candidates=("--acp",),
        npm_package="@github/copilot",
        install_hint="Install GitHub Copilot CLI: npm install -g @github/copilot (needs Node.js).",
        login_hint="Log in to Copilot CLI once (run `copilot` in a terminal).",
        docs_url="https://github.com/github/copilot-cli",
        needs_node=True,
        account="Uses your GitHub Copilot subscription.",
    ),
)


def preset_by_id(preset_id: str) -> AgentPreset | None:
    return next((p for p in PRESETS if p.id == preset_id), None)


def generic_preset(command_line: str) -> AgentPreset:
    """A preset from a command line the user typed, e.g. ``"goose acp"``."""
    parts = shlex.split(command_line, posix=os.name != "nt")
    if not parts:
        raise ValueError("empty command line")
    return AgentPreset(id="generic", name="Custom agent", command=parts[0], args=tuple(parts[1:]),
                       install_hint="Check that the command works in a terminal.")


# -- Resolving the executable ---------------------------------------------------------

# npm .cmd shims reference their target relative to the shim's folder.
_SHIM_TARGET = re.compile(r'"%(?:~)?dp0%?\\([^"%]+)"', re.IGNORECASE)


def resolve_command(command: str, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """The argv prefix that runs *command*, or None when it isn't installed.

    On Windows an npm ``.cmd`` shim is replaced by its target: a native
    executable directly, a ``.js`` script through ``node``.
    """
    path = which(command)
    if not path:
        return None
    if Path(path).suffix.lower() not in (".cmd", ".bat"):
        return [path]
    shim = Path(path)
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [path]
    targets = [m.group(1) for m in _SHIM_TARGET.finditer(text)]
    targets = [t for t in targets if not t.lower().endswith("node.exe")]
    if not targets:
        return [path]
    target = (shim.parent / targets[-1].replace("\\", os.sep)).resolve()
    if not target.is_file():
        return [path]
    if target.suffix.lower() == ".exe":
        return [str(target)]
    local_node = shim.parent / "node.exe"
    node = str(local_node) if local_node.is_file() else which("node")
    if not node:
        return [path]  # let the shim report the missing Node.js
    return [node, str(target)]


# -- Detection ----------------------------------------------------------------------------

@dataclass
class Detection:
    """Whether a preset's agent can be started, and how."""

    preset_id: str
    installed: bool = False
    argv: list[str] = field(default_factory=list)   # full launch command
    version: str = ""
    problem: str = ""                               # plain language, empty when ready
    hint: str = ""

    @property
    def ready(self) -> bool:
        return self.installed and not self.problem


Run = Callable[..., subprocess.CompletedProcess]


def _run_text(argv: list[str], run: Run, timeout: float) -> tuple[int, str]:
    try:
        proc = run(argv, capture_output=True, timeout=timeout,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as ex:
        return -1, str(ex)
    out = (proc.stdout or b"") + b"\n" + (proc.stderr or b"")
    text = out.decode("utf-8", errors="replace") if isinstance(out, bytes) else str(out)
    return int(proc.returncode), text


def detect(preset: AgentPreset, which: Callable[[str], str | None] = shutil.which,
           run: Run = subprocess.run, timeout: float = 15.0) -> Detection:
    """Check *preset*: installed? version? ACP flag?  Never raises."""
    result = Detection(preset.id, hint=preset.install_hint)
    prefix = resolve_command(preset.command, which)
    if prefix is None:
        result.problem = "{:s} is not installed (no `{:s}` on PATH).".format(preset.name, preset.command)
        if preset.needs_node and which("node") is None:
            result.problem += " It also needs Node.js, which isn't installed."
        return result
    result.installed = True
    code, text = _run_text(prefix + ["--version"], run, timeout)
    if code == 0:
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        result.version = first[:80]
    args = list(preset.args)
    if preset.acp_flag_candidates:
        _, help_text = _run_text(prefix + ["--help"], run, timeout)
        flag = next((f for f in preset.acp_flag_candidates if f in help_text), "")
        if not flag:
            result.problem = ("This {:s} version doesn't list an ACP option ({:s}); "
                              "update it, or use a custom command.").format(
                                  preset.name, " / ".join(preset.acp_flag_candidates))
            return result
        args.append(flag)
    result.argv = prefix + args
    result.hint = preset.login_hint
    return result


def launch_env(preset: AgentPreset, only_coworker_tools: bool = True,
               extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment additions for launching *preset* (merged over os.environ by the client)."""
    env = dict(preset.isolation_env) if only_coworker_tools else {}
    env.update(extra or {})
    return env
