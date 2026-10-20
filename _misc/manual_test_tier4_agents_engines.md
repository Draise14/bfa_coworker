# Manual test: External Agent finished, the shared engine tools, and the GUI fixes

About an hour; each section stands on its own. Build from main, install, restart Bforartists.
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

- [ ] 3D View sidebar, Coworker tab: **Chat, Actions, Queue, Plan** (open), then History, Session and Status
      (closed). (#171, #201)
- [ ] Coworker editor sidebar: Coworker (settings + just an image picker and preview), **Actions, Queue,
      Plan**, then History, Session and Status (closed). (#171, #201)
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

## J. Chat history (any engine)

- [ ] First start: the **History** panel (sidebar and editor, under Plan, closed) lists your old chats
      (they moved in from the per-.blend files; nothing lost). (#156)
- [ ] **New Thread** starts a new chat; the old one stays in History. Click an old one: its messages,
      Plan and memory come back.
- [ ] On External Agent: chat A "remember ember", New Thread, chat B "remember frost", open A from
      History, ask which word: **ember** (the agent continued A's own conversation).
- [ ] Pin (stays on top), Rename (keeps your name), Delete (asks first), Search.
- [ ] Open another .blend: it continues the chat you last used in that file.

## K. Auto mode (overnight runs)

- [ ] Ask for a 3-step job that sets a plan; turn on **Auto** in the Plan panel (limits under it:
      8 h / 30 turns by default, Preferences). (#177)
- [ ] After your turn ends, Auto sends "[Auto mode] Continue with the plan ..." itself until the plan is
      done, then the Plan panel says why it stopped.
- [ ] Press **Stop** during an Auto turn: it stops after that turn, and says so.
- [ ] (Local) a timeout doesn't end the run: it continues (up to 3 in a row).
- [ ] While Auto runs, the agent's tool requests and questions don't wait for you (refused tools stay
      refused).

## L. Ask before risky steps (off by default)

- [ ] Settings > Advanced > Bridge Server > **Ask Before Risky Steps** on. Ask Coworker (any engine) to
      "save a copy of this file to C:/temp/test.blend": a card asks first; **Allow once** saves it. (#166)
- [ ] Ask it to delete a file / export an OBJ: the card says so; **Deny** -> the model says you declined
      and doesn't retry.
- [ ] In External Harness mode the card shows at the bottom of the chat (under the harness card).
- [ ] With Auto on, a risky step is declined at once (no waiting overnight).
- [ ] Decide: should it be on by default?

## M. Your feedback round (#192-#201)

- [ ] Mode row at the top of the Coworker panel: **Local, Remote, Agent** together, a gap, then **Harness**
      (also in Preferences, full names). Narrow sidebar: only the active one is named. Click the mode
      icon in the chat's top bar: the same choice as a menu. Locked while running. (#192, #193)
- [ ] Every mode: a **Model / Effort** block before Start (External Agent: Mode / Model / Effort as
      buttons when there are 4 or fewer choices, else a dropdown; Remote: the model list after
      Refresh). (#195)
- [ ] **Coworker | Ask | Plan** with icons, in the chat and in the sidebar chat. Plan: ask for a
      3-object scene; it writes a plan and changes nothing. **Carry Out the Plan** (Plan panel) builds
      it. (#194)
- [ ] Plan on one engine, switch engine (e.g. External Agent -> Local), Carry Out: the plan is still
      there and the other engine does it. (#194)
- [ ] The round button floating over a running turn says **Workshop** and folds the Workshop;
      Thinking always shows. (#196)
- [ ] Queue: send 3 messages while a turn runs. One card each (number, mode, time, the text);
      **up** = next, **pencil** = back into the input, **X** = delete; **Send Next Now** when idle,
      **Clear All**. (#197)
- [ ] Sidebar chat taller than the sidebar: scroll the sidebar so the chat's top and bottom are both
      out of view: it stays drawn, no blip (also on a squeezed monitor). (#198)
- [ ] Editor toolshelf **Attach**: the image picker (browse any image in the file), a preview when
      wide. (#199)
- [ ] External Harness: drag a PNG onto the Coworker editor: it shows under the harness card ("Your
      client can look at it"). In your client: "look at the image I attached in Blender": it calls
      **get_attached_image** and describes it. (#199)
- [ ] **Attach Screen**: the screen gets a light veil. Hover an editor -> click: that editor is
      attached. Again, drag a rectangle: that part. Again, **Enter**: the same part; **Space** held
      moves it; **Shift** square; **Esc** cancels. The status bar lists the keys. (#200)
- [ ] **Actions** panel (3D View sidebar and editor sidebar): New Thread, @ Mention, Revert / Stop,
      Image / Render / Screen, Goal & Plan, Rules, Copy / Export Log. Wide sidebar: several columns.
      External Harness: the chat buttons are gone. (#201)
- [ ] (Note) Did the Windows taskbar misbehave while testing? Say so (I now pace my own test runs).

## N. After testing

- [ ] `make wiki` (once your wiki edits are committed): the new **External Agent** page is there. (#163)
