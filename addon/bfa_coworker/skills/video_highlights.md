# Video Highlights: what makes a piece worth a short

This chapter is read by Find Highlights (`highlights.py`). It is NOT part of the always-loaded
skills, so it costs the chat nothing. Edit it to tune a channel: the code reads the headings and
the `name: value` lines below. If a part is missing or broken, Find Highlights says which and
stops; it never guesses.

A pick is explained by its reason and its scores. Nothing here promises that a short will
"go viral". It only says which stretches of a talk stand on their own.

## Criteria

Each is scored 0 to 3 by the model, except `density`, which the code counts from the words.

- hook: Do the first three seconds make you want the rest: a question, a bold claim, a number, a surprise?
- alone: Does it make sense without the minute before it? Nothing like "as I said" or a "this" that points at nothing.
- complete: Is there a setup and a payoff, ending on a finished thought?
- one_idea: Is it a single tip, story or opinion?
- energy: Is there laughter, disagreement, excitement or a strong opinion in the words?
- tease: Does it open a topic that the full video answers?
- density: Few filler words and pauses, short quotable lines. Counted by the code, never asked.

## Weights

How much each criterion counts, per kind of video. Whole numbers from 0 to 9. The total of a pick is
the weighted share of the best possible score, from 0 to 100.

### podcast

An exchange between speakers, or a story. Standing alone and energy count most.

- hook: 2
- alone: 3
- complete: 2
- one_idea: 1
- energy: 3
- tease: 1
- density: 1

### tutorial

One tip with a result. Complete and one idea count most.

- hook: 1
- alone: 2
- complete: 3
- one_idea: 3
- energy: 1
- tease: 1
- density: 2

### talk

A claim and its proof. Hook and complete count most.

- hook: 3
- alone: 2
- complete: 3
- one_idea: 2
- energy: 1
- tease: 1
- density: 1

## Find prompt

You are helping to pick short clips (at most {max_seconds} seconds, at least {min_seconds} seconds) from a longer video.
Below is part of its transcript. Each line starts with a sentence number such as S214, then the time and a few hints in brackets, then the sentence.

Name up to three stretches that would work as a clip on their own, someone who has not seen the rest understands it, and it ends when the thought ends.
A stretch is a run of whole sentences. Answer with the number of its first sentence, the number of its last sentence, and a label of at most eight words.
Use only the numbers shown. Never give times. Prefer a stretch that starts with a question, a claim or a story, and ends on its answer, result or punchline.
Skip small talk, greetings, housekeeping and stretches that need what came before. If nothing in this part is good, answer with an empty list.

## Rank prompt

You are helping to rank candidate clips from a longer video. Each clip has a letter and its text.
Score each clip from 0 to 3 on these criteria (0 = not at all, 3 = clearly):

{criteria}

Also write, for every clip: a title (at most eight words), a hook line (the line that would open the clip, at most fifteen words, in the speaker's own manner), and one sentence saying why this clip works or where it is weak. Be honest: a dull clip gets low scores.
Judge only the text you are shown. Do not promise reach or views.
{kind_line}
Answer for every clip by its letter.
