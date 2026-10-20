# Manual test: External Agent finished, the shared engine tools, and the GUI fixes

About 45 minutes; each section stands on its own. Build from main, install, restart Bforartists.
You need Claude Code with its ACP adapter (or OpenCode) logged in. Tick each line; anything odd
→ a comment on the issue in brackets, with a screenshot.

## A. Setup (External Agent)

- [ ] Preferences > Coworker > **Operating Mode = External Agent**: one sentence says what happens;
      pick **Claude Code**: its version, location (Open Folder), whose account it uses, the login hint. (#145)
- [ ] Hover the agent picker and each button (Install / Update / Remove / Open Folder): the tooltips
      say plainly what they do.
- [ ] Options there: *Only Coworker Tools*, *Ask Before Blender Tools Too*, *Load Tools When Needed*
      (on). Each tooltip makes sense.
- [ ] Press **Start** (chat header or sidebar): "Connected to Claude Code ...". (#139)

## B. Chatting with your own agent

- [ ] Ask for a small scene ("a low-poly campfire: ground, logs, fire"). The reply streams; the
      Workshop shows its tool steps; the reply is headed **Claude Code**, not Coworker. (#164)
- [ ] The **Plan** panel (sidebar, under Queue) fills from the agent's plan and ticks off. (#147)
- [ ] While it runs: the live **Thinking** box, then the **Workshop** under it, then the streaming
      reply -- in that order. (#178)
- [ ] The floating **Thinking** button (bottom right of the chat) folds / unfolds the live Thinking box;
      folded, the line says "thinking hidden". (#178)
- [ ] Queue a second message while it works; it runs after. Stop stops it.
- [ ] Attach an image (drag one onto the chat): it shows in your message, the agent can see it. (#141)
- [ ] The **Mode / Model / Effort** dropdowns under "ready": switch Mode to Plan, then back. Model shows
      your plan's models. Stop + Start: your pick is still set. (#157)
- [ ] Type **/** at the start of the input: Claude Code's own commands, with their descriptions, not
      overlapping. Tab inserts one. (#163)

## C. The agent asks, you answer

- [ ] Turn on *Ask Before Blender Tools Too*, Start, ask for a cube: a card above the input says
      "Claude Code wants to run execute_blender_code" with the code -- **Allow once** runs it. (#149)
- [ ] Next time pick **Always**: it doesn't ask again for that tool until you Stop. **Deny**: it's told no
      and carries on. Turn the option off again.
- [ ] Ask "ask me which colour the cube should be, with buttons": a card with the choices -- click one,
      the agent uses it. Typing an answer and Enter works too. (#160)

## D. Revert, resume, New Thread

- [ ] After a turn that changed the scene, the **Undo** icon on the last reply reverts the whole turn
      in one step. Change something yourself after a turn: Revert is gone (Edit > Undo History shows
      "Before Claude Code's turn" / "Claude Code's turn"). (#160)
- [ ] Quit Bforartists, reopen, Start: the chat is back and "continuing your last conversation"; ask
      "what did we just build?" -- it knows. (#151)
- [ ] **New Thread**: the chat clears and the agent starts fresh (ask again: it doesn't know).

## E. When things go wrong

- [ ] Kill Bforartists from Task Manager while the agent is connected; reopen: no leftover node /
      claude processes in Task Manager. (#153)
- [ ] (Optional) Log out of Claude Code, Start: "Claude Code isn't logged in" + how to log in. (#153)

## F. Workshop

- [ ] Open a finished turn's Workshop: a long Thinking entry ends with **Show all (N more ...)**;
      it expands in place, **Show less** folds it. Same for a long tool result (> 14 lines). (#169)

## G. Sidebar and editor layout

- [ ] 3D View sidebar, Coworker tab: **Chat, Queue, Plan** (open), then Session and Status (closed). (#171)
- [ ] Coworker editor sidebar: Coworker (settings + just an image picker and preview), **Queue, Plan**,
      Session and Status (closed). (#171)
- [ ] Make the 3D View short (drag the timeline up) so the sidebar is shorter than the chat panel:
      the chat's top bar and the input stay visible, the messages scroll between them, nothing draws over
      the top bar. Very short: the Coworker | Ask row disappears first. (#182)

## H. External Harness

- [ ] Switch to External Harness, Start Bridge, use your client (Claude Desktop...): every tool call it
      makes shows under the card ("What your client did in Blender"), errors in red; **Copy Log**
      copies the list. (#162)

## I. Every engine

- [ ] Local or Remote: a long tool result (ask for the full node tree of a big material) comes back short
      with a note to read the rest; the model can call read_tool_output. (#159)
- [ ] Local: Revert and the Plan panel work like on the agent.
- [ ] Settings > Diagnostics: run **quick_sanity** on External Agent and on Local; **Compare** prints both
      engines side by side in the console (Window > Toggle System Console). (#165)

## J. After testing

- [ ] `make wiki` (once your wiki edits are committed): the new **External Agent** page is there. (#163)
