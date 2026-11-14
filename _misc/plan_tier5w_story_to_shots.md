# Tier 5 — Story to Shots: from a written idea to a timeline to 3D scenes

**Status**: 📝 Plan for later (2026-11-14). Nothing here is scheduled. It starts after the
Tier 4 Text Editor work (Text Editor 1–4, Tools 2) and the Sequencer core have shipped.
**Clean room**: our own design from the need. Fountain is an open plain-text format; we
write our own reader and writer from its public syntax description, and copy no code,
prompts or text from other add-ons or apps (`plan_tier4s_skills_cleanroom.md` §2).

## 1. The idea in one line

An artist writes an idea in the Text Editor. Coworker helps turn it into a script, the
script into shots, the shots into a timeline, and the timeline into 3D scenes with
cameras: **1D text → 2D timeline → 3D scenes**, each step a button, each step editable,
each step one undo.

It serves three kinds of work with one core:

| Work | 1D (text) | 2D | 3D |
|---|---|---|---|
| **Animation / film** | Fountain script | Sequencer animatic: one strip per shot, dialogue as captions | One scene per location, one camera per shot, cameras bound to markers |
| **Storyboarding** | Script with shot notes | A board: one panel per shot, with its text | Optional: a blocked-out scene behind each panel |
| **Comic layout** | Script with page and panel notes | Pages with panel frames and lettering boxes | Optional: a camera per panel for reference renders |

## 2. Why this fits Coworker, and where the limits are

- **It is the wand idea applied to writing.** Rule 1 of `plan_tier5c_artist_automation.md`
  holds: the tools are deterministic and run without a model (parse, break down, build the
  timeline, set up scenes). The model is used where writing needs it: idea → outline,
  outline → script, "suggest shots for this scene", "rewrite the selection".
- **Most of the pipeline already exists or is planned.** Sequencer tools (#304), caption
  strips (#376), text linked to a timeline (#498 Edit by Transcript), the wand framework
  (Wand 0, #331), helper chats (#479), the Text Editor panels (#313–#316).
- **Limits of Blender's Text Editor** (to confirm in the spike, §8):
  - No custom syntax colouring and no inline widgets from Python. So annotations are
    **plain text in the script** (Fountain already has notes, sections and synopses), and
    structure is shown in a **sidebar outline panel**, not as coloured text.
  - No change event per keystroke; "live" means a timer that looks for changes, as in #498.
- **What a model cannot be trusted with**: counting, timing and ids. Durations, shot
  numbers and the link between a line and a strip are computed, never generated.

## 3. One text format: Fountain plus our notes

Fountain is plain text that reads like a script. It already carries what we need:

| Need | Fountain | Example |
|---|---|---|
| Structure (acts, sequences) | Sections | `# Act One`, `## The Market` |
| What a scene is about | Synopsis | `= Mira finds the map.` |
| A scene | Scene heading | `INT. WORKSHOP - NIGHT` |
| Who speaks | Character, dialogue, parenthetical | `MIRA` / `(whispering)` / `It moves.` |
| Free annotation | Note | `[[ make this colder ]]` |
| Title, author, format | Title page keys | `Title: The Map` |

Our additions live **inside notes**, so the file stays valid Fountain everywhere:

- **Shot notes:** `[[shot: WS, 4s, low angle, slow push in]]` under the action it covers.
- **Layout notes** (comic): `[[page: 3]]`, `[[panel: wide, 1/3 page]]`.
- **Ids:** `[[id: s012]]`, written by Coworker once a shot exists, so text and timeline
  can find each other after edits. Ids are the only thing the user should not type.
- **Ideas:** any other note is the artist's annotation, and is what the model reads when
  asked to develop a passage.

A user who has never seen Fountain starts with prose. **Format as Script** converts it.

## 4. The story document

One schema, `bfa_coworker.story/1`, is the hub. Text, timeline and scenes are views of it.

```
story
  title, format (film | board | comic), fps, aspect
  sequences[]            from Fountain sections
    scenes[]             from scene headings: location, interior/exterior, time of day
      synopsis, characters[], props[]
      beats[]            action and dialogue blocks, in order, each with its source lines
      shots[]            id, size (WS/MS/CU…), angle, move, duration, beats covered,
                         dialogue covered, notes; for comics also page and panel
```

- Built by a **bpy-free core** (parser, breakdown, estimator, differ), tested like
  `transcripts.py` and the node-code format.
- Stored next to the `.blend` and referenced from the text data-block; the Fountain text
  stays the source the user edits.
- **Durations are estimated, then owned by the user**: dialogue by words per second,
  action by sentence count, both adjustable; a shot note with a time wins.

## 5. The steps (each a wand in the Text Editor, each also a tool for the chat)

| # | Step | Kind | What it does |
|---|---|---|---|
| 1 | **Develop Idea** | model | Notes and loose prose → a short outline (sections and synopses). Offers three directions as suggestion buttons. |
| 2 | **Format as Script** | model, checked | Prose or outline → Fountain. The parser validates the result; what does not parse is flagged, not silently fixed. |
| 3 | **Breakdown** | deterministic | Scenes, locations, characters, props, estimated length. Shown in the outline panel; exports as CSV. |
| 4 | **Suggest Shots** | model, checked | Proposes shot notes per scene from the action and dialogue. Inserted as notes the user edits. Numbers and durations are computed. |
| 5 | **Build Timeline** | deterministic | Shots → Sequencer: one placeholder strip per shot (colour or scene strip), named by id, grouped by scene on channels, dialogue as caption strips (#376), markers at scene starts. |
| 6 | **Set Up Scenes** | deterministic | One Blender scene per location; one camera per shot with the size / angle / move as starting values; cameras bound to timeline markers; scene strips replace the placeholders. |
| 7 | **Block Out** | model + tools | Per scene, simple stand-ins for characters and props, placed with the layout tools of `plan_tier5c` §2.2. Good work for helper chats: one per location. |
| 8 | **Sync** | deterministic | Text changed → timeline follows (a cut line removes its strip, a moved scene moves its strips); timeline changed → shot notes update. Same diff approach as #498. |

Rules for every step:
- **Preview → apply → one undo.** Each lists what it will create or change first.
- **Nothing is overwritten by guessing.** A shot the user has worked on (keyed camera,
  replaced strip) is marked as theirs; Sync asks before touching it.
- **One implementation, three doors:** Text Editor wand, chat tool, and later a wizard
  (Tier 5 "wizard spells") that walks the steps in order.

## 6. Boards and comic pages (the 2D that is not a timeline)

- **Storyboard:** a board scene with one frame per shot in a grid, the shot's text under
  it, and a Grease Pencil layer to draw on. "Build Timeline" can use the drawn panels as
  image strips, which gives an animatic.
- **Comic:** pages at a chosen size with panel frames from the layout notes (rows, wide,
  tall, splash), gutters, and lettering boxes filled from the dialogue. Reading order
  follows the script. Export per page as image or PDF.
- **Reference renders:** with step 6 done, each panel or board frame can show its
  camera's view of the blocked-out scene.
- **Generated images for panels are Tier 6**, not here: this plan makes the frames, the
  text and the cameras; `plan_tier6_moodboard_storyboarding.md` fills frames with
  generated or painted images and owns the Moodboard editor. The two meet at the shot id.

## 7. Engines and helpers

- Steps 3, 5, 6 and 8 need no model and work with Coworker stopped.
- Model steps go scene by scene to stay inside small context windows: a 14B Local model
  gets one scene and the outline, never the whole script.
- With helper chats (#479), scenes are developed side by side on online engines and one
  after another on Local: one interface, two schedules.

## 8. Phases

| Phase | Content | Size | Needs |
|---|---|---|---|
| **Story 0: spike** | What the Text Editor allows from Python (selection, cursor line, change detection, gutter or sidebar markers); scene strips, camera-to-marker binding and marker tools on 5.2.4, 5.4 and Bforartists; Fountain syntax notes and a sample set of scripts. | S | Text Editor 1 |
| **Story 1: Fountain core** | bpy-free parser / printer / story schema, round-trip tests; outline panel in the Text Editor sidebar; Breakdown; open and save `.fountain`. | M | Story 0 |
| **Story 2: writing wands** | Develop Idea, Format as Script, rewrite the selection as script, with the parser as the check. | M | Story 1, Text Editor 2 |
| **Story 3: shots** | Shot notes, Suggest Shots, the duration estimator, shot list export (CSV). | M | Story 1 |
| **Story 4: timeline** | Build Timeline and Sync text ↔ timeline. | M | Story 3, #304, #376, #498 |
| **Story 5: 3D** | Set Up Scenes, cameras per shot, markers; Block Out with helper chats. | L | Story 4, `plan_tier5c` §2.2 |
| **Story 6: boards and pages** | Storyboard board and comic page layout, lettering boxes, export. | L | Story 3 |
| **Story 7: wizard** | One guided run from idea to scenes, as a wizard spell. | S | Story 5, Spell Book |

Story 1 to 4 are the useful core: a writer gets from an idea to an animatic. Story 5 is
what makes it a Blender feature rather than a script tool.

## 9. Open questions

1. **Tier.** By the rule "lowest tier whose foundation it needs" this is Tier 5: it needs
   the Tier 4 Text Editor and Sequencer work and no generation. Story 0 and 1 could be
   pulled into late Tier 4 if the Text Editor work lands early.
2. **One file or many.** A long script as one text data-block, or one per sequence?
3. **Scene per location or scene per shot.** Per location keeps sets shared; per shot is
   simpler to sync. The spike should try both on a 30-shot sample.
4. **Comic page model.** Camera frames in a 2D scene, Grease Pencil, or image strips on a
   page-sized Sequencer? Decide in Story 0 from what exports cleanly.
5. **Other script formats.** Final Draft (`.fdx`) import later, if asked for; Fountain
   stays the working format.
6. **Bforartists parity.** Nothing here may depend on a Bforartists-only change; the
   Text Editor and Sequencer APIs used must exist in Blender too.

## 10. Not in this plan

- Generating images, video or voices for shots (Tier 6).
- A full screenwriting app: revision colours, production reports, scheduling, budgets.
- Editing footage by transcript (that is #498), though Sync shares its core.
