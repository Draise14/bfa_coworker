# Wand panels: one set of presentation rules (#610, Highlights W0)

**Status**: in force for every wand panel, dialog and menu entry from 1.1.45. The Highlights
panel (#571) is built to these rules from its first commit.
**Checked by**: `tests/test_wand_panel_rules_610.py` (the parts a script can check) and
`tests/test_wand_tooltips.py` (the tooltip line, #607).
**The bar**: useful, easy and amazing; the controls explain themselves where you meet them;
heavily iconised; Bforartists first, and just as usable in Blender; non-destructive where
possible; vertical output only for now.

## 1. Where things go

| What | Where | Why |
|---|---|---|
| The **action** (the thing the wand does) | A button in the panel or menu; **one main action per panel, first, large** (`scale_y` 1.4) | The eye lands on it first. |
| **Options** of the action | In the operator's dialog (`invoke_props_dialog`), grouped in boxes with a header each; **not** as loose controls in the panel | A panel stays a list of buttons; a dialog is where you decide. |
| **Results** (what was found, what will change) | In the panel, in a box above the buttons, one icon line per fact; long lists go to a card or a dialog | You read the result where you press the next button. |
| **What it did** (after running) | One status-bar report, one line, past tense, with the numbers: `Removed 14 silences (38.2 s)`. A warning when part was skipped, naming the part and the reason | The same shape for every wand, so the agent's chat can quote it. |
| A wand that **needs setup** | The button stays; its dialog says what to set up and has a button that opens it | Never a greyed-out wand with no reason. |

## 2. Buttons

1. **Order**: the main action first; what builds or adds next; what **cuts, removes or resets
   last** (separated by a divider in a menu). Inside the Sequencer's Video panel and Strip
   menu the order stays the pipeline order of the tools (clean, caption, reframe, edit).
2. **Wording**: a verb and its object, Title Case, no full stop (`Make Captions`,
   `Remove Silences`). A trailing `...` means a dialog opens first and **only** then.
   A toggle is named by what it switches on (`Live`, `Follow`), and shows pressed (`depress`).
3. **An icon on every button and every drop-down entry.** A wand with its own picture uses
   the real full-colour PNG from `icons/wand` (`wand/icons.py`, `icon_value`), with a Blender
   icon as the fallback when the picture cannot load. Every other button uses a Blender icon
   through `shared.icon()`. No monochrome stand-ins, no text-only buttons. Icon-only buttons
   (header, toolshelf) keep a tooltip that names them.
4. **Drop-down entries** (`EnumProperty` items) carry an icon in the fourth place of the
   tuple and a tooltip in the third. Layouts and styles use a picture that shows the result.
5. **Section headers** (a `label` that names a group of controls) carry an icon, and say what
   the group is for, not only what it is (`Style: how the captions look`). A label that only
   continues the line above it (a second line of a message) has none.
6. **Destructive** buttons are never the default of a dialog, never first, and always one undo.

## 3. The tooltip

Every wand operator's tooltip ends with the line about the agent (#607): `Works without the
agent started.`, or `...except <part>, which needs a vision model.`, or `Needs the agent
started: <why>.` Built only through `agent_line` / `model_line` / `needs_agent_line` in
`wand/spec.py`, so the wording stays one text. Tooltips of layout buttons are built the same
way.

## 4. Blender and Bforartists

| Same in both | May differ |
|---|---|
| The operators, the dialogs, the wording, the order, the icons (our own PNGs) | **Where** the entry is drawn: Bforartists' tabbed toolshelf (`show_toolshelf_tabs` on) has the Video panel and the Reframe buttons; Blender, and Bforartists with the tabs off, show them in the sidebar's Coworker tab. The Strip menu section is in both. |
| The tooltip line | Toolshelf buttons may be icon only (narrow); the sidebar shows icon and text |
| Fallback icons from Blender's own set | Nothing is built on a Bforartists-only icon or API; a missing one falls back through `shared.icon()` |

A Reframe preview drawn in the sidebar while the toolshelf shows the same PNGs leaves the
toolshelf's blank (measured, #606): the sidebar then uses the Blender fallback icons.

## 5. What this task changed (the three existing wands)

| Where | Before | After |
|---|---|---|
| Transcript panel (Text Editor sidebar) | `Wands` header with no icon; Remove Filler Words and Remove Silences above Make Captions | Header with the wand icon; Make Captions first, the two removals last |
| Reframe menu | `Reset Framing` in the middle, Follow Subject last | Reset Framing last, after a divider |
| Reframe sidebar box | `One picture` / `Split in two` headers without icons | Headers with icons |
| Reframe and Make Vertical drop-downs (resolution, mode, action, layout) | Entries without icons | Every entry has an icon |

Captions already followed the rules (icons on every style, animation, easing, speed, position,
case, lines and language entry; icons on every section header, #563, #605).
