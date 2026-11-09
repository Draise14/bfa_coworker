# Coworker security model

Security 1 (#345). Every way into Coworker, every place it keeps something
sensitive, what protects it today, and what is missing. Line numbers are for
the commit that adds this file; re-check them when you change the code.

## The boundary

Coworker runs Python in Blender for several clients. The line we defend is
**your own user account on your own computer**:

- **Defended:** web pages open in your browser (they can send requests to
  `127.0.0.1`), other machines on your network, other user accounts on the
  same computer, text in scenes and downloads that tries to steer the model
  (prompt injection), and tampered downloads.
- **Not defended (accepted):** a program already running as you. It can read
  the token file, edit your Blender config or replace the add-on. Nothing
  inside Coworker can stop that, so we don't pretend to.

## What is verified

- **The bridge** (`mcp_to_blender_server`) binds `prefs.host`, which defaults to
  `localhost` (`addon/bfa_coworker/mcp_to_blender_server.py:44`, bind at `:1595`).
  It speaks NUL-terminated JSON over raw TCP. A browser's HTTP request starts with
  `POST / HTTP/1.1`, fails `json.loads` (`:1227`) and never runs. So browsers
  can't drive the bridge; local processes can.
- **The MCP HTTP server** binds `--host`, which defaults to `127.0.0.1`
  (`mcp/blmcp/__init__.py:45`). The add-on's own launch passes no `--host`
  (`addon/bfa_coworker/agent_controller.py:2979`), so Local, Remote and the
  tool listing only ever listen on loopback. **Network mode** (External Harness >
  MCP Server > Network) binds `prefs.mcp_server_host`
  (`addon/bfa_coworker/operators_agent.py:1169`). It defaults to `127.0.0.1`,
  but you can set `0.0.0.0` or a LAN address. The preferences show a warning
  when you do (`addon/bfa_coworker/preferences.py:2287`). So "loopback only" is
  true unless you choose otherwise.
- **External Harness and External Agent use stdio, not HTTP.** The client
  config runs the launcher with `--transport stdio`
  (`addon/bfa_coworker/agent_controller.py:3500-3531`), and the ACP agent gets
  the same entry (`addon/bfa_coworker/ui_chat.py:3846`). Only Local, Remote, the
  llama.cpp web UI and Network-mode clients use the HTTP endpoint.
- **The session token is not in the client's config file** (the issue said it
  was). The config carries only `BFACW_HOST` / `BFACW_PORT`
  (`agent_controller.py:3512-3514`). The launcher reads the token from
  `~/.cache/bfa_coworker/harness/install.json` (`agent_controller.py:3433-3434`,
  written at `:3460-3462`).
- **The bundled llama-server binds `127.0.0.1`** (`addon/bfa_coworker/llm_manager.py:3200`)
  and runs without `--api-key`.

## HTTP hardening (done in this PR)

`mcp/blmcp/__init__.py:99` `_configure_http` now:

- turns on the MCP SDK's DNS-rebinding protection (`TransportSecuritySettings`,
  mcp 1.27 in `mcp/uv.lock`, 1.29 in the vendored deps). It allows the hosts
  `127.0.0.1:<port>`, `localhost:<port>` and `[::1]:<port>`, plus the bound
  address in Network mode (for `0.0.0.0`: this computer's name and addresses).
  See `mcp/blmcp/tools_helpers/http_auth.py:53`. A foreign `Host` gets **421**.
- allows a browser `Origin` only from this computer, on any port (the llama.cpp
  web UI is Coworker's on 8081+offset, or your own on 8080):
  `http_auth.py:46` (CORS) and `:82` (SDK check). A foreign `Origin` gets **403**,
  and its preflight gets **400** with no CORS headers. Requests without `Origin`
  (every non-browser client) are not affected.
- `BFACW_ALLOWED_HOSTS` / `BFACW_ALLOWED_ORIGINS` (comma separated, in Blender's
  environment) add more, for Network-mode setups the defaults don't cover.

Both checks run whether or not the token is required. Tests are in
`tests/test_http_hardening.py`.

## Entry points and sensitive places

| # | Entry point / sensitive place | Who can reach it | Protection today | Gap | Proposed task |
|---|---|---|---|---|---|
| 1 | **Bridge** TCP socket, `execute` requests (`mcp_to_blender_server.py:1210-1310`) | Any local process. Other machines if `prefs.host` is not loopback. Not browsers (see above). | Session token, `session_identity.check_token` (`mcp_to_blender_server.py:1243`, `session_identity.py:212`), compared with `hmac.compare_digest`. Per-window id refuses the wrong window (`:1238`, #85). 10 MiB request cap (`:171`). | (a) Setting `prefs.host` to `0.0.0.0` / a LAN IP has no warning (only the MCP server has one). The token then crosses the network in clear text. (b) *Require Session Token* can be switched off (`preferences.py:266`). | (a) **"Warn when the bridge binds a non-loopback address"**: Security 3. (b) Accepted, because it is an explicit opt-out whose description says what it costs. |
| 2 | Bridge `identify` request (`mcp_to_blender_server.py:1233`) | Any local process | None: answered before the token check | Leaks PID, app version and the open .blend's file name to any local process | Accepted, because a local process can already list windows and processes; the file name is low value. Revisit if it ever returns more. |
| 3 | **Risky-step check** at the bridge (`risky_steps.py:56-78`, called at `mcp_to_blender_server.py:1277-1290`) | Code from every engine (Local, Remote, External Agent, External Harness) | Asks before save / open / quit / delete / export / run a program. Default approval mode is Ask (`preferences.py:257`). Auto declines risky steps. | (a) **The skip marker can be spoofed.** Code that contains `# blmcp-toolcode-skip-preflight` skips the risky check (`risky_steps.py:58`), autofix and preflight (`mcp_to_blender_server.py:894,905`). `execute_blender_code` passes model code through as-is, so a model (or injected text) can switch the guard off. (b) Regexes are easy to get around (`getattr(os, "rem" + "ove")`, `exec`, `pathlib.Path.write_text`, `open(..., "w")`). | (a) **Done (#347):** the marker counts only as the first line of the code, and code from `execute_blender_code` / `execute_blender_plan` never honours it: the MCP server makes it inert and the bridge does again by tool name (`risky_steps.is_toolcode`, `untrusted`). (b) Accepted as a guard, not a sandbox (Security 3 says so). Security 3's path and network rules cover the common cases. |
| 4 | **`execute_blender_code_for_cli`** (`mcp/blmcp/tools/execute_blender_code.py:67`) | Any MCP client: stdio harness, ACP agent, HTTP clients | None of the bridge's checks. It runs `blender --background … --python-expr` from the MCP server process (`tools_helpers/blender_cli.py:70`). | Model code runs outside the bridge: no risky-step check, no approval card, no activity record | **"Route the CLI tool through the risky-step check (or ask before it runs)"**: Security 3. |
| 5 | **MCP HTTP endpoint** (`mcp/blmcp/__init__.py:99-142`) | Loopback by default. Network mode: whoever can reach the bound address. Browsers on this computer. | Token gate `TokenGateMiddleware` when `BFACW_TOKEN` is set (`http_auth.py:94`). **New:** Host and Origin checks, CORS limited to this computer (see above). | (a) Network mode: the token crosses the network in clear text (no TLS). (b) A wildcard bind only knows the names `socket` reports. A client using another name (mDNS, VPN address) gets 421 until it is added with `BFACW_ALLOWED_HOSTS`, and there is no preference for that. (c) CORS allows any loopback port, not just the llama.cpp UI's port. Pinning the port needs `agent_controller.py` to pass the LLM port. | (a) Accepted for now, because Network mode is opt-in and warned. Put "use an SSH tunnel or VPN" in the warning text: Security 3. (b) **"Network mode: allowed host names in Preferences"**: Security 3 (needs `agent_controller._build_mcp_env`). (c) Accepted, because a page on another local port is already a local program. Optional follow-up with (b). |
| 6 | **Session token file** `session_token` (`session_identity.py:160-199`) | Your account. Other accounts if file permissions allow it. | `%LOCALAPPDATA%` on Windows (private to your account). `chmod 600` elsewhere (`:192`). | The parent folder is created with default permissions (fine, since the file is 600) | Accepted. |
| 7 | **Harness launcher** `~/.cache/bfa_coworker/harness/` (`agent_controller.py:3398-3490`) | Your account. Other accounts on Linux/macOS (see gap). | Rewritten on every start | (a) `install.json` holds a **copy of the token** and is written with default permissions (`:3487`), not 600. On Linux/macOS with umask 022 and a traversable home, another account can read it. (b) `install.json`'s `pythonpath` decides what code the harness client runs. | (a) **"Write install.json owner-only (like session_token)"**: Security 2. (b) Accepted, because only your account can write it, and a program running as you is out of scope. |
| 8 | **External Harness client configs** (`harness_clients.py:191-227`) | Your account | No token in the file (see above). Merge keeps other keys. Unparseable files are never overwritten. Previous file kept as `.bfacw.bak` (`:167-173`). | The `.bfacw.bak` copy keeps any secrets the user's other MCP servers have in that file | Accepted, because it sits next to the original with the same permissions (`copy2`). |
| 9 | **External Agent (ACP)** process (`acp_client.py:125-145`, `engine_acp.py:466-510`) | The agent app you installed, with your login | No `fs` / `terminal` advertised (`acp_client.py:311-316`). *Only Coworker tools* refuses the agent's own tools when it asks (`engine_acp.py:484`) and hides modes that skip asking (`:52`, `:189`). Isolation env for OpenCode (`acp_presets.py:73-78`). | (a) **"Ours" is decided by the tool title**: `MCP_SERVER_NAME in title or "blender" in title.lower()` (`engine_acp.py:482`). An agent's own shell call whose title mentions "blender" (`blender --background …`) counts as a Coworker tool. It is then allowed without asking when *Ask before Coworker tools* is off (`:496`). (b) *Only Coworker tools* works only for tools the agent asks about. Tools it runs without asking (for example read-only file tools, depending on the agent's mode) are not stopped. Not checked per agent. (c) The agent inherits Blender's whole environment (`acp_client.py:130`). | (a) **"Identify Coworker tools by ACP tool kind / MCP server, not the title"**: Security 3. (b) Accepted, because it is the user's own agent with its own permission model. Say so in the option's tooltip (Security 3). (c) Accepted, because it is the user's own program. |
| 10 | **Prompt injection**: scene text, object names, asset descriptions, downloaded READMEs, web results | Anyone who made the .blend / asset / page | System prompt only. The risky-step check catches some outcomes. | Untrusted text reaches the model unmarked and can steer it. Combined with #3a it can also switch off the risky-step check. | Security 4 (#348) as filed. Make sure its "risky steps ask after untrusted content" also covers #3a. |
| 11 | **Downloads: llama-server** (`llm_manager.py:2702-2933`) | GitHub release assets for a pinned tag (`:156`), over HTTPS | Pinned release tag. Tar uses the `data` filter (`:2696`). `zipfile.extract` strips `..` and absolute paths (`:2847`). | No hash check on the archive or the `cudart` zip (`:2877-2880`). A replaced release asset, or a compromised account, runs as you. | **"Pin llama-server and cudart archives by SHA-256"**: Security 5 (#349). |
| 12 | **Downloads: models** (`llm_manager.py:2167-2340`) | Hugging Face, over HTTPS | `_verify_sha256` (`:2104`) runs when `expected_sha256` is set (`:2305`) | **No preset sets `expected_sha256`** (`:723` default `""`), so no model is checked today. GGUF is data, but llama.cpp has had parser CVEs. | **"Fill expected_sha256 for the curated presets"**: Security 5. |
| 13 | **Runtime `pip install`** of the MCP server's deps (`agent_controller.py:2301`, `build_addon.py:195,206`) | PyPI | Version range `mcp[cli]>=1.2.0,<2.0.0` | Unpinned, unhashed. Each machine gets whatever PyPI serves that day (vendored deps here: mcp 1.29.0). Also `TransportSecuritySettings` needs mcp ≥ 1.10, so the `>=1.2.0` floor is wrong. | **"Pin the vendored deps with hashes (a lock file)"**: Security 5. Raising the floor to `>=1.10` belongs in the same change (`agent_controller.py` is owned elsewhere this week). |
| 14 | **Extension repository** (`extension_repo.py:68`, `:116`) | `raw.githubusercontent.com/.../main/extensions/index.json` and the release zips | Blender checks `archive_hash` (sha256) from the listing. HTTPS. | Index and zip sit behind the same GitHub account. Whoever can push to `main` or upload a release can ship any code to every user's auto-update. No signature. | **"Signed releases / protected main for the extension listing"**: Security 5. |
| 15 | **Settings backup** `bfa_coworker_settings.json` in Blender's config folder (`settings_transfer.py:131-143`, `__init__.py:337-348`) | Your account. Other accounts if the folder is readable. | Written only on disable / quit. Restored once into a fresh install (`:156-170`). | (a) Plain JSON with `remote_api_key`, `hf_token` and the generation API key (`preferences.py:678,916,973`). Default file permissions. (b) A restored file also restores security switches (`require_session_token`, `approval_mode`, `mcp_server_host`). | (a) Security 2 (#346): keep the key on purpose, write the file owner-only, never log it. (b) Accepted, because only your account can write the file. |
| 16 | **Secrets in logs and exports** (console, log file, Copy Log / Copy Error, session export, `operations.jsonl` at `agent_controller.py:3816`) | Whoever you paste them to | `llm_transport` prints the URL only | Nothing redacts API keys, the HF token or `BFACW_TOKEN`. `operations.jsonl` keeps tool arguments (including code) in plain text without limit. | Security 2 (#346) as filed. Add `operations.jsonl` to its list. |
| 17 | **Bundled llama-server** HTTP (`llm_manager.py:3197-3204`) | Local processes. Browser pages too if llama-server's CORS is permissive (not re-checked here). | Loopback only | No `--api-key`: any page or local process can use the model. Whether `/slots` exposes prompt text is not checked. | **"Check what llama-server exposes; consider --api-key"**: Security 2 if prompts leak, otherwise accepted, because it is compute, not Blender access. |
| 18 | **Remote API key in transit** (`llm_transport.py:425,1111`, `llm_manager.py:1114`) | The provider URL you set | HTTPS by preset | `remote_api_url` accepts `http://` (useful for local OpenAI-compatible servers). A tampered backup (#15) could point it elsewhere. | Accepted, because the URL is your choice. Security 2's redaction keeps it out of logs. |

## Gaps to file

Security 2 (#346), secrets:

- Write `install.json` owner-only, like `session_token` (#7a).
- Settings backup owner-only, key kept on purpose, never logged (#15a, already in scope).
- Add `operations.jsonl` to the redaction list (#16).
- Check what llama-server exposes without `--api-key` (#17).

Security 3 (#347), code and file guard:

- ~~Only Coworker's own tool-code may carry `# blmcp-toolcode-skip-preflight` (#3a).~~ Done.
- Route `execute_blender_code_for_cli` through the risky-step check (#4).
- Identify Coworker tools in ACP permission requests by kind / server, not by title (#9a).
- Warn when the bridge binds a non-loopback address (#1a). Network-mode warning text: suggest a tunnel (#5a).
- Network mode: allowed host names in Preferences (#5b).

Security 4 (#348), untrusted content: as filed. Make sure it closes #3a's route too (#10).

Security 5 (#349), supply chain:

- SHA-256 pins for llama-server and cudart archives (#11).
- Fill `expected_sha256` for the curated model presets (#12).
- Lock the vendored Python deps with hashes, and raise the `mcp` floor to 1.10 (#13).
- Signed releases / protected `main` for the extension listing (#14).

Accepted: #1b, #2, #3b, #5a (for now), #5c, #6, #7b, #8, #9b, #9c, #15b, #18, with the reasons in the table.
