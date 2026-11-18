# Tier 4v, part 2 — Highlights and Suggest Edits (design)

**Status**: 📝 Proposal (2026-11-18), for the maintainer to accept or change. No code yet.
**Covers**: Video 8, Highlights (#571) and Video 5, Suggest Edits (#378). They share one
pipeline, so they are designed together.
**Builds on**: local transcription (#375), Remove Silences (#331, #539), Make Vertical
(#333) and Reframe layouts (#606), Auto Captions (#376, #594), Edit by Transcript (#498).
**Clean room**: our own design. No prompt, text or scoring rule is taken from another
tool or service.

## 1. What the artist gets

Two buttons in the Sequencer's Coworker tab, and the same through the chat.

| Button | You have | You get |
|---|---|---|
| **Find Highlights** | A long edit: a podcast, tutorial, talk or showcase | A short list of pieces under 60 seconds, each with a title, its length and one line saying why it was picked. **Preview** plays it in the long edit. **Make Short** builds it as its own scene, already cut, vertical and captioned. The long edit is never changed. |
| **Suggest Edits** | Any edit, long or short | Up to five concrete edits (tighten these pauses, drop this retake, punch in here, add a hook title, this caption style, this vertical layout). Each one has **Apply**, which is one undo. |

Rules that hold for both:

- **Nothing is built or changed until you press a button on a suggestion.**
- **Every result is ordinary Sequencer data**: scenes, strips, keys. You can fix any of it
  by hand, or with Reframe, Edit by Transcript and the caption tools.
- **It says why, never "viral".** A pick comes with its reason and its scores, not a promise.
- **Local keeps everything on the machine.** With a Remote engine the dialog says that the
  transcript text is sent to that service. No frames or audio are sent in the first version.

## 2. The idea that makes it robust: the model picks sentences, the code does the rest

A language model is good at judging which part of a talk is interesting. It is bad at
timestamps, and a small Local model is worse. So the model never sees or returns a time.

```mermaid
flowchart TD
    A["Long edit"] --> B["Reader: the words as they play now, grouped into numbered sentences S1..Sn"]
    B --> C["Windows: overlapping stretches of sentences that fit the engine's context"]
    C --> D["Find: per window, the model names candidate ranges by sentence number"]
    D --> E["Check in code: numbers exist, length is 15 to 60 s, starts and ends on a sentence"]
    E --> F["Rank: one request with all candidates' text, scored per criterion"]
    F --> G["Picks list on the scene: title, range, reason, scores"]
    G --> H["Chat card, Coworker tab, optional timeline markers"]
    H -->|Preview| I["Play that range in the long edit"]
    H -->|Make Short| J["New scene, trimmed, then the polish chain"]
```

- The **reader** turns the transcript into sentences with numbers. Each sentence knows its
  start and end frame on the timeline as it plays today, after every cut and move.
- The numbers are only the handle for one conversation with the model. What is **stored**
  for a pick is the media file and the transcript's word positions (3.6), because a single
  cut renumbers every later sentence.
- The model answers with numbers only: "S214 to S231". The code turns that into frames,
  measures the length, and rejects anything that is too long, too short or not there.
- A wrong answer costs one retry of one window. It cannot produce a cut in the wrong place.

## 3. The pieces

### 3.1 Reader (new, no Blender needed for its core)
Today's `transcript_edit.timeline_words` is not enough: it reads one media file at a time,
only top-level strips, and Edit by Transcript refuses the cases it cannot map. Highlights
needs a reader for the **whole scene**, and it is the first thing to build:

- It walks every sound-carrying strip of the edit and places each transcript word where it
  plays now (word, start frame, end frame, media file, word position in that file's
  transcript, and the speaker track if the captions were made per speaker).
- **Several media files**: words from all of them are merged by time.
- **The same speech twice at once** (two microphones, camera sound plus recorder): where two
  files speak over the same frames, one is kept, the one with the higher word confidence,
  and the result says so. Otherwise the sentences would come out interleaved.
- **A clip used twice**: both uses are kept, each as its own sentences, since each plays.
- **Muted strips and muted channels** are skipped.
- **Retimed strips** (speed changes): the first version skips them and reports how much of
  the edit that was, as the captions do today. Mapping words through a speed change is its
  own task.
- **Inside meta strips**: skipped in the first version, and reported.
- Sentences: the lines Edit by Transcript already makes (`transcript_edit.render`: sentence
  punctuation, or a pause of 0.8 s), so Suggest Edits can hand a sentence straight to the
  transcript editor's cut. A sentence longer than 20 s is split at its longest pause.
- Per sentence, cheap facts the code can count: seconds, words per second, filler words,
  pause before it, question mark, a number in it, speaker change. These go to the model as
  short hints and are used for the value-density score, so the model does not have to guess them.
- No transcript yet: the button offers to transcribe first (the existing wand). Parts of the
  edit without speech are skipped and the result says how much was skipped.

### 3.2 Windows
- A one-hour talk is about 9 000 words, about 12 000 tokens. That fits a 32K Local context
  in one request only on paper: with the instructions and room for the answer it is tight,
  and the 8K fallback context cannot do it at all.
- So the size of a window follows the engine. The real context size is read from the
  running server (the Local preset can be anything from 4K to 128K; 32K is the default).
  A window is at most about 2 400 words (16 minutes of speech), and smaller when the
  context is: instructions, the sentence numbers and hints (about as many tokens again as
  the words) and room for the answer must fit with a margin. On a 32K context a one-hour
  talk is four or five requests; on 8K about ten.
- Windows overlap by one minute, so a piece that crosses a border is seen whole in one of them.
- The **rank** request is sized the same way: when the candidates do not fit one request
  (small contexts, many candidates), they are ranked in groups and the group winners once more.
- Each request is made directly by the wand (the way Follow Subject asks today), not through
  the agent's chat, so the chat's history and tool list are not in it. To verify in the
  build: on a Local server with one slot, a wand request may push the chat's cached prompt
  out, which costs the chat one full re-read afterwards (the subject of #546 and #590).
- Thinking is switched off for these requests where the engine allows it: the answers are
  short lists, and thinking would use the room the transcript needs.

### 3.3 Asking for a checked answer (new shared helper)
There is no such helper today; Follow Subject parses its one number by hand. Highlights,
Suggest Edits and later wands need a real one:

- `ask_json(messages, schema)`: sends the request with the schema where the engine's server
  can enforce it, and otherwise asks for JSON only. Two things to settle first, in the
  build: our request function cannot pass extra fields today (`llm_transport`), so it needs
  that; and whether the llama.cpp build we ship enforces a schema on the chat endpoint has
  to be probed, not assumed.
- Every answer is checked against the schema in our code, whatever the server promised.
- One retry, with the error named, when the check fails. After that the window is skipped
  and counted in the result ("1 of 5 parts could not be read").
- **External Agent and External Harness engines**: the add-on cannot send a direct request there. The button then
  hands the job to the agent as a prepared message, and the agent uses the same chat tools
  (3.7). The checks and the picks list are the same code either way.

### 3.4 Find, then rank (two passes)
- **Find** (per window): "name up to three ranges that would stand alone as a short".
  Answer per range: first sentence, last sentence, a five-word label. Nothing else.
  Keeping this answer tiny is what makes a small Local model reliable at it.
- **Check** (code): see section 2. Overlapping candidates from neighbouring windows are
  merged. A range a little over 60 s is trimmed back to the last sentence that fits, if it
  still ends on a full stop.
- **Rank** (one request): the text of all surviving candidates, usually 10 to 20 pieces of
  up to 150 words. The model scores each on the criteria (3.5), writes a title, the hook
  line and one sentence of reason. The total is added up in code from those scores and the
  weights of the chosen kind of video, so the order can be explained and tuned.
- One pass would be cheaper, but it asks a small model to read 16 minutes, choose, score
  and write titles in one answer. Two passes keep each answer short and checkable.

### 3.5 What makes a piece worth a short
Each criterion is scored 0 to 3 by the model, except the last, which the code counts.

| Criterion | The question asked |
|---|---|
| Hook | Do the first three seconds make you want the rest: a question, a bold claim, a number, a surprise? |
| Stands alone | Does it make sense without the minute before it? No "as I said", no "this" pointing at nothing. |
| Complete | Is there a setup and a payoff, ending on a finished sentence? |
| One idea | Is it a single tip, story or opinion? |
| Energy | Is there laughter, disagreement, excitement, a strong opinion (from the words; from the sound later)? |
| Teases the long video | Does it open a topic the full video answers? |
| Value density | Few fillers and pauses, quotable lines (counted by the code). |

The weights depend on the **kind of video**, chosen in the dialog (or "Auto", decided in
the rank request from the first minutes):

- **Podcast or interview**: an exchange between speakers, a story. Stands alone and Energy weigh most.
- **Tutorial**: one tip with a result. Complete and One idea weigh most.
- **Talk or lecture**: a claim and its proof. Hook and Complete weigh most.
- **Showcase or trailer**: little speech; see 3.9.

The criteria, the weights and the two prompts live in one editable skill chapter
(`skills/video_highlights.md`), so a channel can tune them without touching code.

### 3.6 The picks list: one store, three views
- The picks are stored on the scene as one custom property holding JSON. Per pick: the
  media file's key and the first and last word position in its transcript (this is the
  identity, it does not change when the edit is cut), the frames it had when found, a
  signature of the timeline at that moment (as Edit by Transcript keeps), title, hook,
  reason, scores, state. They survive saving and reopening.
- **Chat card**: a new card kind with one row per pick (title, length, reason) and the
  buttons Preview, Make Short, Dismiss. Today a new card needs its own branch in the chat
  code; this adds a small general "list card" that Suggest Edits uses too.
- **Coworker tab**: the same list as a panel, so it is there after the chat has moved on.
- **Timeline markers** (a switch, off by default): one marker pair per pick on the long
  edit, removable in one click. Markers are the only thing Highlights ever adds to the long edit.
- **Preview** sets the preview range to the pick, plays, and puts the range back afterwards.
- If the long edit is cut after the picks were made, the signature no longer matches. The
  picks are then placed again from their words: still whole and in order means new frames;
  partly cut away means "changed, check it"; gone means "no longer in the edit". Preview and
  Make Short always check this first, the way Apply in Edit by Transcript refuses stale text.

### 3.7 Make Short
- A copy of the scene (the method Make Vertical uses, with the fix that keeps a copy on
  save; that function gets a name and tag of its own instead of the fixed " Vertical"),
  then a **trim made for this job**. Remove Silences' cutter cannot be used: it refuses the
  whole edit when any strip it cannot cut lies in a removed stretch, and here nearly the
  whole edit is removed. The trim:
  - deletes strips wholly outside the range, whatever their kind, with their keys;
  - trims strips that cross a border, and refuses, naming the strip, only when such a strip
    cannot be trimmed (a locked strip, a transition whose inputs fall outside);
  - puts the borders in the pause before the first and after the last word, not on the word;
  - moves the rest to frame 1, sets the frame range, moves or drops markers, and moves the
    captions' stored word times with them;
  - clears what the copy must not inherit (the picks list, a vertical tag).
- Named `Short 01: <title>`, tagged with the scene and words it came from.
- A copy and trim, not "a new scene with only some strips": effects, channels, colour and
  audio settings come along for free, and strips only point at media, so the file barely
  grows. The build issue measures it on an edit of 500 strips before settling this.
- The transcript comes along by itself, because it is stored per media file, not per scene.
- **Polish chain**, each step optional and each an existing wand:
  1. Remove Silences, with a tighter setting than for a long edit.
  2. Make Vertical inside the short (not a second copy), in the aspect chosen (9:16, 1:1 or
     keep), with the layout the kind of video suggests (Reframe, #606, to change it).
  3. Auto Captions, in the style last used.
  4. A hook title over the first three seconds, from the pick's hook line.
- One undo removes the whole short. That holds because the three wands push no undo of
  their own when called from one operator, with two conditions: the transcript must exist
  before (transcribing runs as its own job with its own undo), and a tracked layout is not
  part of the chain (Follow Subject works in the background and pushes its own). The steps
  are reported in one line each.
- Rendering is left to the artist in the first version (see section 6).

### 3.8 Chat tools
- `suggest_highlights(kind, max_seconds, count)`: starts the pipeline as a background job,
  like a render, and answers at once; the agent polls it with the existing job status tool.
  A run takes minutes, which a single tool call must not block.
- `read_edit_sentences(first, count)` and `propose_highlights(picks)`: the same pipeline in
  two steps, for an agent that reads the sentences itself (the External Agent path). The
  proposals go through the same checks.
- `make_short(pick, steps)`.
- So *"find me three shorts in this podcast and build the best one"* works in the chat.

### 3.9 Without speech
Films, music videos and showcases say little, so the transcript cannot find their moments.
The first version says so and stops ("not enough speech to pick from"). A later version
looks at the footage with the vision model through contact sheets; it is listed in section 6.

## 4. Suggest Edits on the same pipeline (#378)

Suggest Edits is the same machine pointed at a different question. It uses the reader, the
checked answer, the list card and "apply as one undo".

- The model chooses from a **fixed menu of edit kinds**, and points at sentences or words by
  number. It never writes code or frame numbers. Each kind maps to one existing tool.

| Edit kind | What the model gives | What applies it | Exists today? |
|---|---|---|---|
| Tighten pauses | Nothing | Remove Silences on the edit | Yes |
| Tighten one stretch | A stretch of sentences | Remove Silences limited to a frame range | No: it takes strips, not a range |
| Drop a retake, a false start or filler | The sentences or words to drop | Edit by Transcript | Yes |
| Caption style and motion | A style and animation from the list | Auto Captions | Yes |
| Vertical layout | A layout from the list | Reframe | In work (#606) |
| Punch in for emphasis | One sentence | A short scale key on the picture strip | No tool yet |
| Hook title | The line | A text strip over the first seconds | No tool yet (also needed by Make Short) |
| Chapters | A sentence and a title per chapter | Timeline markers | No (the open box of #542) |

The first version of Suggest Edits ships the kinds marked Yes plus the hook title, which
Make Short needs anyway. The others join the menu as their tools arrive.

#378 also planned a contact sheet of the footage for the model. That is left out of the
first version on purpose: text only, so nothing but the transcript leaves the machine on a
Remote engine and a Local model without vision can do it. It returns with the vision path.

- Up to five suggestions, the recommended ones marked. Each row: what, why, Preview where
  it makes sense, Apply. "Apply the recommended ones" applies them in a safe order (cuts
  first, then layout, then captions), still one undo each.
- A suggestion that no longer fits after an earlier one was applied is checked again before
  it runs, and greyed out if its sentences are gone.
- On a fresh short from Make Short this is the natural next click: the short is built, and
  Suggest Edits proposes how to make it better.

## 5. How we know the picks are good, without private footage

- **A written test transcript**: a made-up one-hour "podcast" and a made-up tutorial, written
  by us, with strong moments planted at known places (a clear question and answer, a story
  with a punchline, a tip with a result) and weak stretches around them (small talk,
  half sentences, references to earlier parts).
- **Unit tests** use a stand-in model with fixed answers: they test the reader, the windows,
  the checks, the merge, the ranking sum and the placing after cuts. They need no model.
- **A model check outside the unit suite**: the real Local model (and a Remote one when
  asked) on the written transcripts. Target: at least four of the six planted moments are in
  the top five, and no pick starts or ends in the middle of a sentence (the code guarantees
  the second). This is run when the prompts or criteria change, and its numbers go into the PR.
- **Hand test** on real footage by the maintainer, as with the other video tools.

## 6. Decisions asked of the maintainer

| # | Question | Proposal |
|---|---|---|
| 1 | One pass or two? | Two: find per window, then rank once. |
| 2 | Where do picks show? | Stored on the scene; shown as a chat card and in the Coworker tab; timeline markers as a switch. |
| 3 | How is the short's scene made? | Copy the scene and trim. |
| 4 | Can a short join two places (setup at 12:10, payoff at 14:30)? | Not in the first version. The stored pick already allows a list of ranges, so it can come later without a new format. |
| 5 | Presets | Length 15 / 30 / 60 s (default 60), count 3 to 10 (default 5), kind of video (Auto), aspect 9:16 / 1:1 / keep (default 9:16), polish steps on by default. |
| 6 | Video without speech | First version stops with a clear message. Vision path later. |
| 7 | Rendering the shorts | Left to the artist first. A "Render all shorts" queue later. |
| 8 | What a Remote engine receives | Transcript text only, said in the dialog. |
| 9 | Which release | Not 1.1.44, which is in hand testing. Proposed as the main feature of 1.1.45. |

## 7. Build plan (issues to file once this is accepted)

| Step | Issue to file | Size | Needs | Done when |
|---|---|---|---|---|
| H1 | Scene reader: every speaking strip, merged, numbered sentences, windows; `read_edit_sentences` | L | — | On made-up edits (two media files, two microphones, a clip used twice, a muted and a retimed strip) the sentences are in playing order, none doubled, and the skipped parts are reported. |
| H2 | `ask_json`: schema-checked answers on Local and Remote, one retry; request function passes extra fields; schema support of the shipped llama.cpp probed | M | — | A fake server and the real Local server both return checked answers; a wrong answer is retried once and then reported. |
| H3 | Find and rank, the criteria chapter, the written test transcripts (with made-up word times) and the model check; time measured on the reference card | M | H1, H2 | At least four of six planted moments in the top five on the written transcripts; time per hour of talk stated. |
| H4a | Picks store on the scene, re-placing after cuts, Preview, markers, Coworker tab panel | M | H1 | Picks survive save and reopen; after a cut they are placed again or marked changed or gone. |
| H4b | List card in the chat (rows with their own buttons), used by Highlights and Suggest Edits | M | H4a | The card shows the stored picks and its buttons act on the right pick in every chat layout. |
| H5 | Make Short: named scene copy, the trim, the hook title tool, the polish chain, one undo | L | H4a | A pick becomes a short scene on edits with metas, effects, keys and word-motion captions; one undo removes it; the long edit is byte-for-byte unchanged. |
| H6 | Chat tools as a background job, and the External Agent and Harness path | M | H3, H5 | *"Find three shorts and build the best one"* works on Local, Remote and External Agent. |
| S1 | Suggest Edits (#378, rescoped onto H1, H2, H4b): the menu, checks, Apply | M | H1, H2, H4b; #606 for layouts | On a made-up clip it proposes at least three edits that apply cleanly, each one undo. |

H1 and H2 can start together; H3 and H4a follow in parallel; then H4b and H5; then H6 and S1.

Later, Tier 5: a short joined from two places; the vision path for footage without speech;
energy from the sound (laughter, loudness); words through speed changes and inside metas;
a render queue for all shorts; picks tuned per platform (length and safe areas); punch-in
and chapters in the Suggest Edits menu.

## 7a. The two answers the model gives (sketch; the wording of the prompts is settled in H3)

Find, per window:
```json
{"candidates": [{"first": 214, "last": 231, "label": "why we dropped the old rig"}]}
```
`first` and `last` are sentence numbers shown in that window; at most three candidates;
`label` at most eight words. Anything else in the answer is ignored.

Rank, per group of candidates (each shown with a letter and its text):
```json
{"picks": [{"id": "C", "title": "The rig we threw away", "hook": "We deleted six months of work.",
            "reason": "A clear story with a payoff that needs no context.",
            "scores": {"hook": 3, "alone": 3, "complete": 2, "one_idea": 3, "energy": 2, "tease": 1}}],
 "kind": "podcast"}
```
Scores are whole numbers 0 to 3; `kind` is only read when the dialog says Auto. Titles and
hooks are cut to a fixed length in code. The prompts state the criteria of 3.5 in our own
words and are stored in the skill chapter.

## 8. Risks

- **A small Local model picks dull pieces.** The checks guarantee well-formed picks, not
  interesting ones. The written test transcripts measure this before any real footage, and
  the criteria chapter is there to tune. If a model scores below the target, the dialog can
  recommend a stronger engine for this wand.
- **Time on Local.** Four or five window requests and one rank request for an hour of talk.
  To be measured in H3 on the reference card (RTX 3080) and stated in the dialog, with a
  progress line and a working Stop.
- **Bad transcripts.** Wrong words and missing punctuation hurt sentence borders. The pause
  rule covers missing punctuation; a transcript with very low word confidence gets a warning
  before the run.
- **Long edits with many cuts.** Sentences are built from the words as they play now, so a
  heavily cut edit gives short, choppy sentences. That is the edit's real state; the reader
  reports the number of sentences so the artist sees it.
