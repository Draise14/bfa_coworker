# Manual test: polish round (#212-#221)

About 20 minutes. Goes with `manual_test_tier4_agents_engines.md` (section M there is the round
before). Build from main, install, restart Bforartists. Tick each line; anything odd → a comment
on the issue, with a screenshot. Review shots for each PR are on the `review-shots` branch.

## Layout

- [ ] Coworker editor sidebar: **Coworker, Queue, Plan, History**, then **Status & Diagnostics**
      (closed; Session is inside it). No Actions panel. (#212)
- [ ] 3D View sidebar (compact chat): **Queue** (open), **Plan** (closed), **the chat**, History and
      Status (closed), and **Coworker** (closed) at the bottom with the mode tabs, engine and Start. (#212)
- [ ] No Image box in either Coworker panel. (#213)

## Images

- [ ] Editor input row, left: **image / render / screen** icons. The image icon opens a popover: pick
      any image in the file, File..., Render, Screen, Send Once, the preview. (#213)
- [ ] Sidebar chat: a **column of icons** left of the messages: New Thread, @ Mention, Revert (when
      there is one) or Stop (while running), Open Editor; then Image, Render, Screen. Each works. (#214)
- [ ] Floating Coworker editor (Open Editor): click into the input, then **drag a PNG from Explorer**
      onto the chat: it attaches (tray above the input). Also with the input not focused. (#215)
- [ ] **Attach Screen** from the floating editor: that window shows the hovered editor tinted with a
      border, at once; no copy in the main window. Click: that editor is attached. Again from the main
      window: drag a part; Enter / Space / Shift / Esc as before. (#217)

## Chat

- [ ] While a turn runs, the floating Workshop button is just **arrow + Workshop icon**. (#216)
- [ ] A long job (50+ steps) with the Workshop open: scrolling and streaming stay smooth. (#218)
- [ ] Ask for something that ends with "Shall I ...?" or offers options ("Next, I could: 1. ... 2. ..."):
      **buttons** under the reply; a click sends it as your next message. None while an approval card
      shows. A plain "I made these:" list gives none. (#220)

## External Agent

- [ ] Claude Code connected: no separate Mode row (Model and Effort stay). **Plan** in the chat → the
      agent plans (its plan mode); **Coworker** → it works normally again. (#221)
- [ ] An agent with more modes (OpenCode, if it has several): its Mode row shows; picking its plan mode
      switches the chat to Plan. (#221)

## Your taskbar

- [ ] Did the Windows taskbar misbehave today? (My own test runs are now paced.)

## Still open

- #219 Orientation tools (place / align by bounding boxes): a task for later; add your examples there.
