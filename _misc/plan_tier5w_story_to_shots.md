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
- **Limits of Blender's Text Editor** (to confirm in the spike, §9):
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
- **Everything lives in the `.blend`.** The Fountain text is a text data-block and stays
  the source the user edits. The story document is stored in the file too (a second,
  hidden text data-block), and what the breakdown finds becomes real data in the file: the
  Story Library of §5. No side files; one `.blend` carries the whole story. Export
  (`.fountain`, shot list as CSV) is an extra, never the storage.
- **Durations are estimated, then owned by the user**: dialogue by words per second,
  action by sentence count, both adjustable; a shot note with a time wins.

## 5. The Story Library: the breakdown as things in the file

A breakdown shown as a table is something to read. A breakdown kept as collections is
something to work with. So step 3 does not fill a table; it builds a library in the file.

```
Story                         one collection, kept out of renders, colour-tagged
  Characters
    MIRA                      one collection per character, holding its stand-in
    OLD TOMAS
  Props
    MAP
    LANTERN
  Sets
    WORKSHOP                  one collection per location: floor, walls, big pieces
    MARKET
```

- **One collection per element, with a stand-in inside.** Each has a label (its name,
  shown in the viewport) and carries what the script says about it: its story id, the
  scenes it appears in, how many lines it speaks, the notes written about it.
- **Scenes use instances of these collections.** Replace the stand-in inside `MIRA` with
  the real character once, and every scene and shot shows it. The library is the one
  place to go from grey box to final asset.
- **Where a stand-in comes from, in this order:**
  1. what the user picked for that element;
  2. an asset with a matching name or tag in the asset libraries the user has set up;
  3. the **blockout kit** shipped with the add-on;
  4. a plain box of a sensible size, as the last resort.
- **The blockout kit** is one small `.blend` in the add-on: person (adult, child),
  animal, table, chair, door, window, wall, floor, stairs, vehicle, tree, rock, generic
  box and cylinder, all at real-world size. Pieces are appended from it, never generated,
  so the same proxy is the same mesh in every file and no new meshes pile up.
- **Grey boxing with colour coding.** Sets and props are grey. Each character gets one
  colour from a fixed palette, and that colour is reused wherever the character shows up:
  the stand-in (object colour, so it reads in solid view without materials), the
  collection's colour tag, its dialogue captions on the timeline. Timeline strips are
  colour-tagged by scene.
- **The outline panel reads from the library.** The Text Editor sidebar lists scenes,
  characters, props and sets with counts; clicking one selects its collection. A table
  export (CSV) stays available for people who need a sheet.
- **Updates, never rebuilds.** Running the breakdown again adds what is new and marks
  what is no longer in the script as unused; it never deletes a collection the user has
  put work into.

## 6. The steps (each a wand in the Text Editor, each also a tool for the chat)

| # | Step | Kind | What it does |
|---|---|---|---|
| 1 | **Develop Idea** | model | Notes and loose prose → a short outline (sections and synopses). Offers three directions as suggestion buttons. |
| 2 | **Format as Script** | model, checked | Prose or outline → Fountain. The parser validates the result; what does not parse is flagged, not silently fixed. |
| 3 | **Breakdown** | deterministic | Scenes, locations, characters, props, estimated length → the Story Library (§5): collections with labelled, colour-coded stand-ins, all in the file. |
| 4 | **Suggest Shots** | model, checked | Proposes shot notes per scene from the action and dialogue. Inserted as notes the user edits. Numbers and durations are computed. |
| 5 | **Build Timeline** | deterministic | Shots → Sequencer: one placeholder strip per shot (colour or scene strip), named by id, grouped by scene on channels, dialogue as caption strips (#376), markers at scene starts. |
| 6 | **Set Up Scenes** | deterministic, model optional | One operation that makes the story visible. Per location: a Blender scene with the set's instance; the characters and props of that scene as instances from the Story Library, placed in a readable default layout; one camera per shot with the size / angle / move as starting values, bound to timeline markers; scene strips replace the placeholders. With a model running, **Arrange** then places the stand-ins from the action text with the layout tools of `plan_tier5c` §2.2 (good work for helper chats: one per location). Without a model the default layout stands and the user moves things. |
| 7 | **Sync** | deterministic | Text changed → library, timeline and scenes follow (a cut line removes its strip, a moved scene moves its strips, a new character gets a collection); timeline changed → shot notes update. Same diff approach as #498. |

Set Up Scenes and Block Out were two steps in the first draft. They are one here: a scene
with cameras and nothing in it shows nothing, and the stand-ins already exist after step 3,
so placing them costs no extra step.

Rules for every step:
- **Preview → apply → one undo.** Each lists what it will create or change first.
- **Nothing is overwritten by guessing.** A shot the user has worked on (keyed camera,
  replaced strip, moved stand-in) is marked as theirs; Sync asks before touching it.
- **One implementation, three doors:** Text Editor wand, chat tool, and later a wizard
  (Tier 5 "wizard spells") that walks the steps in order.

## 7. Boards and comic pages (the 2D that is not a timeline)

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

## 8. Engines and helpers

- Steps 3, 5, 6 (without Arrange) and 7 need no model and work with Coworker stopped.
- Model steps go scene by scene to stay inside small context windows: a 14B Local model
  gets one scene and the outline, never the whole script.
- With helper chats (#479), scenes are developed side by side on online engines and one
  after another on Local: one interface, two schedules.

## 9. Phases

| Phase | Content | Size | Needs |
|---|---|---|---|
| **Story 0: spike** | What the Text Editor allows from Python (selection, cursor line, change detection, gutter or sidebar markers); scene strips, camera-to-marker binding and marker tools on 5.2.4, 5.4 and Bforartists; finding assets by name or tag in the user's asset libraries from Python; appending from a kit `.blend` and collection instances across scenes; Fountain syntax notes and a sample set of scripts. | S | Text Editor 1 |
| **Story 1: Fountain core and Story Library** | bpy-free parser / printer / story schema, round-trip tests; the story kept in the `.blend`; Breakdown into the Story Library with the blockout kit and colour coding; outline panel in the Text Editor sidebar; open and save `.fountain`. | M | Story 0 |
| **Story 2: writing wands** | Develop Idea, Format as Script, rewrite the selection as script, with the parser as the check. | M | Story 1, Text Editor 2 |
| **Story 3: shots** | Shot notes, Suggest Shots, the duration estimator, shot list export (CSV). | M | Story 1 |
| **Story 4: timeline** | Build Timeline and Sync text ↔ timeline. | M | Story 3, #304, #376, #498 |
| **Story 5: 3D** | Set Up Scenes: scenes, stand-ins placed from the Story Library, cameras per shot, markers; Arrange with helper chats. | L | Story 4, `plan_tier5c` §2.2 |
| **Story 6: boards and pages** | Storyboard board and comic page layout, lettering boxes, export. | L | Story 3 |
| **Story 7: wizard** | One guided run from idea to scenes, as a wizard spell. | S | Story 5, Spell Book |

Story 1 to 4 are the useful core: a writer gets from an idea to an animatic. Story 5 is
what makes it a Blender feature rather than a script tool.

## 10. Open questions

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
   Text Editor, Sequencer and asset APIs used must exist in Blender too.
7. **Matching assets.** How sure must a name or tag match be before a library asset is
   used as a stand-in without asking? Start strict (exact name or tag) and list the
   near matches in the preview.
8. **Kit size and style.** How many pieces the blockout kit needs before it helps more
   than it weighs, and whether users can add their own kit file.
9. **Long projects.** One `.blend` holds everything by default. For a feature-length
   script, sets may need to become linked files; out of scope until someone needs it.

## 11. Not in this plan

- Generating images, video or voices for shots (Tier 6).
- A full screenwriting app: revision colours, production reports, scheduling, budgets.
- Editing footage by transcript (that is #498), though Sync shares its core.
