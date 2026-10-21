# Manual test: External Agent finished, the shared engine tools, and the GUI fixes

About an hour; each section stands on its own. Build from main, install, restart Bforartists.
You need Claude Code with its ACP adapter (or OpenCode) logged in. Tick each line; anything odd
→ a comment on the issue in brackets, with a screenshot.

# What's left (round 3)

Everything still open, in one place: the lines you hadn't ticked below (rewritten where the feature
changed since), the polish round #212-#221 (was `manual_test_tier4_polish_212_221.md`, now merged
here), and your round-2 notes (#233-#244). Review shots for each PR: the `review-shots` branch.
Your ticked sections A-N stay below as the record.

## R1. Layout and controls

- [x] Editor sidebar: **Coworker, Queue, Plan, History**, then **Status & Diagnostics** (closed; Session
      is inside it). 3D View sitdebar (compact chat): **Queue**, **Plan** (closed), **the chat**, History and
      Status (closed), **Coworker** (closed) at the bottom. No Actions panel. (#212)
- [x] Each control once: Start / Stop and the status are only in the header (editor) or the top bar
      (sidebar chat); the mode row has no settings gear. External Harness: client + **Copy Config** in the
      panel, **Copy Log / Configure** on the card, **Start Bridge** in the header. (#234)
- [x] Hover each mode tab (Local / Remote / Agent / Harness): one line, then what that mode does --
      External Agent included. (#233; was A.1)
- [x] Sidebar chat: the mode icon in its top bar (and the editor header's mode label) opens the mode tabs
      plus Model / Effort, same short-hand as the Coworker panel, and All Settings. (#235)

## R2. Images

- [x] Editor input row, left: **image / render / screen** icons; the image icon opens the picker popover
      (any image, File..., Render, Screen, Send Once, preview). No Image box in the Coworker panel. (#213)
- [x] Sidebar chat: the **icon column** left of the messages (New Thread, @ Mention, Revert / Stop, Open
      Editor; Image, Render, Screen). (#214)
- [x] Floating Coworker editor: click into the input, then **drag a PNG from Explorer** onto the chat:
      it attaches. (#215 -- needs your real drag)
- [x] **Attach Screen** from the floating editor: that window tints the hovered editor at once, no copy in
      the main window. (#217)

## R3. Chat

- [x] The floating Workshop button: **arrow + Workshop icon** only. (#216)
- [x] A 50+ step job with the Workshop open stays smooth. (#218) Tool results show a few lines, then
      **Show all**. (#236)
- [x] Hover the chat's icon buttons (Undo, Copy, the input row, the column, the top bar): a tooltip. (#237)
      Every icon-only button is square. (#238)
- [x] A reply ending "Shall I ...?" or offering options: **buttons** under it; a click sends it. (#220)
- [ ] History: the mode's **icon** where the chat ran (name in the tooltip), and **Open** for the chat's
      .blend: Blender asks to save, opens it, and the same chat continues there. (#239)
- [x] Queue a message with an image: its card shows the **image icon**, not the word. (#249)

## R4. External Agent (was B.6, C.1, C.3, D.1)

- [x] Claude Code connected: no separate Mode row; Model and Effort stay. Chat **Plan** → it plans;
      **Coworker** → it works. Stop + Start: Model / Effort still set. (#221; was B.6)
- [ ] **Revert** on the last reply undoes the whole turn, also a big one (assets, node wiring); Edit >
      Undo History shows "Before Claude Code's turn (n)". (#242; was D.1)
- [x] Plan mode: after the plan, a card: **Start the Plan** / **Keep refining**. (#241; was C.3) _failed, only shows one button, not two_
- [ ] **Permissions** button left of Send opens a dropdown: **Strict / Permissive / Auto** (lock / unlocked /
      car icons), switching at once, also while the agent runs. Strict: a card before each change -- **Allow
      once** (that step), **Allow this turn** (every tool until your next message), **Deny**. Permissive:
      only saving / opening files, deleting, exporting, running programs ask. Auto: nothing asks.

## R5. External Harness

- [x] The list "What your client did in Blender" scrolls with the wheel. (#243)

## R5b. Refinement pass (#265-#283)

- [ ] Image popover: picker, a small preview (not super tall), Send Once; no File / Render / Screen row.
      Editor toolshelf Attach: just its three buttons. (#265)
- [ ] From the **floating** Coworker window: **Attach Screen** veils every window; click the main window's
      3D View: that view is attached. **Attach Render** works there too, and with no 3D View open at all
      it renders the scene camera. (#266, #267)
- [ ] Hover any button in the chats -- cards, Show all, links, the grip, the scrollbar, the @ list --
      a tooltip. (#268)
- [ ] The editor's image buttons: one joined row of squares; joined rows everywhere draw one line
      between buttons, not two. (#269, #283)
- [ ] A closing question gives **two** buttons: "A, or B?" both halves; else Yes and "No, something
      else..." (puts "No, " in the input). (#270)
- [ ] While the agent runs, the lock button switches Ask / Risky / Auto at once. (#271)
- [ ] Plan > **Edit**: the text opens at the top with the goal and steps; change a step, it shows in the
      Plan panel. (#272)
- [ ] History: your older chats (e.g. "Coworker test - hallway") have the **Open** icon again. (#273)
- [ ] The chat's scrollbar always shows faintly; Ctrl+wheel zoom doesn't make it fatter. (#279)

## R6. Still open from before

- [ ] (Optional, was E.2) Log out of Claude Code, Start: "Claude Code isn't logged in" + how to log in. (#153)
- [ ] (Was I.3) Preferences > Coworker > **Advanced** > turn on **Debug / Diagnostics**; at the bottom of
      Preferences, **Diagnostics** > *Quick challenges* > **Sanity**: click its steps on Local, switch to
      External Agent and click them again, then **Compare Results**: both engines side by side in Window >
      Toggle System Console. (#165)
- [ ] `make wiki` (once your wiki edits are committed): the **External Agent** page is there. (#163)
- [ ] #219 (orientation tools) and #244 (scale vs dimensions): add examples of misplaced objects to #219.

# Earlier rounds (your record)

## A. Setup (External Agent)

- [x] Preferences > Coworker > **Operating Mode = External Agent**: one sentence says what happens;
      pick **Claude Code**: its version, location (Open Folder), whose account it uses, the login hint. (#145)
- [x] Hover the agent picker and each button (Install / Update / Remove / Open Folder): the tooltips
      say plainly what they do.
- [x] Options there: *Only Coworker Tools*, *Ask Before Blender Tools Too*, *Load Tools When Needed*
      (on). Each tooltip makes sense.
- [x] Press **Start** (chat header or sidebar): "Connected to Claude Code ...". (#139)

## B. Chatting with your own agent

- [x] Ask for a small scene ("a low-poly campfire: ground, logs, fire"). The reply streams; the
      Workshop shows its tool steps; the reply is headed **Claude Code**, not Coworker. (#164)
- [x] The **Plan** panel (sidebar, under Queue) fills from the agent's plan and ticks off. (#147)
- [x] While it runs: the live **Thinking** box, then the **Workshop** under it, then the streaming
      reply -- in that order. (#178)
- [x] The floating **Thinking** button (bottom right of the chat) folds / unfolds the live Thinking box;
      folded, the line says "thinking hidden". (#178)
- [x] Queue a second message while it works; it runs after. Stop stops it.
- [ x Attach an image (drag one onto the chat): it shows in your message, the agent can see it. (#141)
- [ ] The **Mode / Model / Effort** dropdowns under "ready": switch Mode to Plan, then back. Model shows
      your plan's models. Stop + Start: your pick is still set. (#157)
- [x] Type **/** at the start of the input: Claude Code's own commands, with their descriptions, not
      overlapping. Tab inserts one. (#163)

## C. The agent asks, you answer

- [x] Turn on *Ask Before Blender Tools Too*, Start, ask for a cube: a card above the input says
      "Claude Code wants to run execute_blender_code" with the code -- **Allow once** runs it. (#149)
- [x] Next time pick **Always**: it doesn't ask again for that tool until you Stop. **Deny**: it's told no
      and carries on. Turn the option off again.
- [x] Ask "ask me which colour the cube should be, with buttons": a card with the choices -- click one,
      the agent uses it. Typing an answer and Enter works too. (#160)

## D. Revert, resume, New Thread

- [ ] After a turn that changed the scene, the **Undo** icon on the last reply reverts the whole turn
      in one step. Change something yourself after a turn: Revert is gone (Edit > Undo History shows
      "Before Claude Code's turn" / "Claude Code's turn"). (#160)
- [x] Quit Bforartists, reopen, Start: the chat is back and "continuing your last conversation"; ask
      "what did we just build?" -- it knows. (#151)
- [x] **New Thread**: the chat clears and the agent starts fresh (ask again: it doesn't know).

## E. When things go wrong

- [x] Kill Bforartists from Task Manager while the agent is connected; reopen: no leftover node /
      claude processes in Task Manager. (#153)
- [ ] (Optional) Log out of Claude Code, Start: "Claude Code isn't logged in" + how to log in. (#153)

## F. Workshop

- [x] Open a finished turn's Workshop: a long Thinking entry ends with **Show all (N more ...)**;
      it expands in place, **Show less** folds it. Same for a long tool result (> 14 lines). (#169)

## G. Sidebar and editor layout

- [x] 3D View sidebar, Coworker tab: **Chat, Actions, Queue, Plan** (open), then History, Session and Status
      (closed). (#171, #201)
- [x] Coworker editor sidebar: Coworker (settings + just an image picker and preview), **Actions, Queue,
      Plan**, then History, Session and Status (closed). (#171, #201)
- [x] Make the 3D View short (drag the timeline up) so the sidebar is shorter than the chat panel:
      the chat's top bar and the input stay visible, the messages scroll between them, nothing draws over
      the top bar. Very short: the Coworker | Ask row disappears first. (#182)

## H. External Harness

- [x] Switch to External Harness, Start Bridge, use your client (Claude Desktop...): every tool call it
      makes shows under the card ("What your client did in Blender"), errors in red; **Copy Log**
      copies the list. (#162)

## I. Every engine

- [x] Local or Remote: a long tool result (ask for the full node tree of a big material) comes back short
      with a note to read the rest; the model can call read_tool_output. (#159)
- [x] Local: Revert and the Plan panel work like on the agent.
- [ ] Settings > Diagnostics: run **quick_sanity** on External Agent and on Local; **Compare** prints both
      engines side by side in the console (Window > Toggle System Console). (#165)

## J. Chat history (any engine)

- [x] First start: the **History** panel (sidebar and editor, under Plan, closed) lists your old chats
      (they moved in from the per-.blend files; nothing lost). (#156)
- [x] **New Thread** starts a new chat; the old one stays in History. Click an old one: its messages,
      Plan and memory come back.
- [x] On External Agent: chat A "remember ember", New Thread, chat B "remember frost", open A from
      History, ask which word: **ember** (the agent continued A's own conversation).
- [x] Pin (stays on top), Rename (keeps your name), Delete (asks first), Search.
- [x] Open another .blend: it continues the chat you last used in that file.

## K. Auto mode (overnight runs)

- [x] Ask for a 3-step job that sets a plan; turn on **Auto** in the Plan panel (limits under it:
      8 h / 30 turns by default, Preferences). (#177)
- [x] After your turn ends, Auto sends "[Auto mode] Continue with the plan ..." itself until the plan is
      done, then the Plan panel says why it stopped.
- [x] Press **Stop** during an Auto turn: it stops after that turn, and says so.
- [x] (Local) a timeout doesn't end the run: it continues (up to 3 in a row).
- [x] While Auto runs, the agent's tool requests and questions don't wait for you (refused tools stay
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

- [x] Mode row at the top of the Coworker panel: **Local, Remote, Agent** together, a gap, then **Harness**
      (also in Preferences, full names). Narrow sidebar: only the active one is named. Click the mode
      icon in the chat's top bar: the same choice as a menu. Locked while running. (#192, #193)
- [x] Every mode: a **Model / Effort** block before Start (External Agent: Mode / Model / Effort as
      buttons when there are 4 or fewer choices, else a dropdown; Remote: the model list after
      Refresh). (#195)
- [x] **Coworker | Ask | Plan** with icons, in the chat and in the sidebar chat. Plan: ask for a
      3-object scene; it writes a plan and changes nothing. **Carry Out the Plan** (Plan panel) builds
      it. (#194)
- [x] Plan on one engine, switch engine (e.g. External Agent -> Local), Carry Out: the plan is still
      there and the other engine does it. (#194)
- [x] The round button floating over a running turn says **Workshop** and folds the Workshop;
      Thinking always shows. (#196)
- [x] Queue: send 3 messages while a turn runs. One card each (number, mode, time, the text);
      **up** = next, **pencil** = back into the input, **X** = delete; **Send Next Now** when idle,
      **Clear All**. (#197)
- [x] Sidebar chat taller than the sidebar: scroll the sidebar so the chat's top and bottom are both
      out of view: it stays drawn, no blip (also on a squeezed monitor). (#198)
- [x] Editor toolshelf **Attach**: the image picker (browse any image in the file), a preview when
      wide. (#199)
- [x] External Harness: drag a PNG onto the Coworker editor: it shows under the harness card ("Your
      client can look at it"). In your client: "look at the image I attached in Blender": it calls
      **get_attached_image** and describes it. (#199)
- [x] **Attach Screen**: the screen gets a light veil. Hover an editor -> click: that editor is
      attached. Again, drag a rectangle: that part. Again, **Enter**: the same part; **Space** held
      moves it; **Shift** square; **Esc** cancels. The status bar lists the keys. (#200)
- [x] **Actions** panel (3D View sidebar and editor sidebar): New Thread, @ Mention, Revert / Stop,
      Image / Render / Screen, Goal & Plan, Rules, Copy / Export Log. Wide sidebar: several columns.
      External Harness: the chat buttons are gone. (#201)
- [x] (Note) Did the Windows taskbar misbehave while testing? Say so (I now pace my own test runs). No, it only failed when developing from VS Code.

## N. After testing

- [ ] `make wiki` (once your wiki edits are committed): the new **External Agent** page is there. (#163)
