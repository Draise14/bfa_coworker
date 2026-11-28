# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Written test transcripts for Highlights (#613), made up by us.  No real footage, no private data.

Two one-hour talks, each with three strong moments planted at known places and weak stretches
around them (small talk, half sentences, references to earlier parts, filler words):

* :func:`podcast`: a made-up podcast about building a small recording corner.  Strong moments:
  a clear question and answer ("never again"), a story with a punchline ("the cable"), a claim with
  its proof ("the cheap microphone").
* :func:`tutorial`: a made-up modelling and rendering tutorial.  Strong moments: a tip with a result
  ("loop cut"), a tip with a number ("render samples"), a mistake and its fix ("black normals").

Each returns ``{"words": [{"w", "start", "end"}], "planted": [{"label", "start", "end"}],
"minutes": float}`` with made-up word times (2.5 words a second, a short pause between sentences).
The weak stretches are built from templates and word lists with a fixed seed, so the same text
comes out every time.  Used by the unit tests (with a stand-in model) and by
``_misc/highlights_model_check.py`` (with a real one).
"""

__all__ = ("podcast", "tutorial")

import random
from typing import Any

WORD_STEP = 0.4                 # seconds from one word to the next: 2.5 words a second
WORD_LEN = 0.3
PAUSE = (0.5, 1.3)              # seconds between two sentences

_SMALL = [
    "Yeah, so, um, thanks for having me on, it is really good to be here.",
    "Well, let me think about that for a second, uh, it is a bit hard to say.",
    "So, um, as I said earlier, that is the thing from before, you know.",
    "And then, uh, we sort of, you know, we kind of went back and forth on it.",
    "Right, right, yeah, that makes sense, I think that is about right.",
    "Sorry, can you say that again, I think my connection dropped for a moment.",
    "I mean, it depends, it really depends on the day and on who you ask.",
    "Anyway, so, where were we, I lost my train of thought there.",
    "That reminds me of the other thing, but I will come back to that later.",
    "So the thing is, well, the thing is, um, how do I put this.",
    "Yeah, no, absolutely, and, uh, to be fair there are a lot of ways to look at it.",
    "Okay, so, moving on, let us talk about the next part, if that is fine with you.",
    "Hmm, that is a good question, honestly I would have to look it up.",
    "As we discussed before the break, this is more or less the same point.",
    "Thanks, thanks a lot, and, um, thank you to everyone who sent in questions.",
    "I think, um, we should probably, uh, take a quick look at the schedule.",
    "Like I said, it is one of those things where, you know, it just takes time.",
    "Mm, yes, I agree, and, well, that is kind of what I meant earlier.",
    "So, yeah, that is, uh, that is basically all I wanted to say about that.",
    "Let me just grab some water, sorry, one moment, okay, I am back.",
]

_PODCAST_A = ["the room", "the desk", "the shelf", "the window", "the carpet", "the door", "the lamp", "the chair",
              "the wall", "the corner", "the panel", "the stand"]
_PODCAST_B = ["a bit loud", "kind of small", "fairly new", "pretty old", "sort of grey", "rather plain",
              "a little cold", "quite heavy", "rather tidy", "mostly empty", "really average", "fairly quiet"]
_PODCAST_C = ["last spring", "a while ago", "on Tuesday", "for some time", "before the move", "after the holidays",
              "in the first week", "at the start", "over the summer", "most days", "now and then", "every so often"]
_PODCAST_T = [
    "I think {a} is {b}, and that was {c}, but, um, nobody really noticed.",
    "So {a} was {b} {c}, and, uh, we left it like that for a while.",
    "We talked about {a} {c}, which was {b}, I suppose, you know.",
    "Honestly {a} is {b}, and I do not have much more to say about it.",
    "People ask about {a}, which is {b}, and I tell them it depends.",
    "Well, {a} became {b} {c}, so we just, uh, kept going.",
    "It was {c} that {a} turned out {b}, if I remember it right.",
    "And then {a}, which is {b}, came up again {c}, as I said before.",
]

_TUT_A = ["the mesh", "the object", "the modifier", "the material", "the panel", "the viewport", "the collection",
          "the camera", "the light", "the node", "the layer", "the outliner"]
_TUT_B = ["on the left", "at the top", "in this menu", "over here", "under the tab", "next to it",
          "in the sidebar", "near the bottom", "in the header", "behind it", "to the right", "inside the group"]
_TUT_C = ["for now", "later on", "in a moment", "as before", "like earlier", "as usual", "first of all", "again",
          "just once", "at this point", "from here", "once more"]
_TUT_T = [
    "So, um, we have {a} {b}, and we will look at that {c}, okay.",
    "Now, uh, {a} is {b}, you can see it there, and we leave it {c}.",
    "Right, so this is {a} {b}, same as in the part before, {c}.",
    "I will not go into {a} {b}, that is, um, something for {c}.",
    "As I showed earlier, {a} sits {b}, so, yeah, we skip it {c}.",
    "Okay, let me click on {a}, it is {b}, and, uh, that is fine {c}.",
    "If you want, you can change {a} {b}, but it does not matter {c}.",
    "So, um, that was {a}, and the next thing is {b}, {c}, I think.",
]

_PODCAST_STRONG = [
    ("never again", [
        "What is the one thing you would never do again when building a studio?",
        "Buy the room before you listen to the room, that is the answer.",
        "I spent two thousand dollars on acoustic panels in my first month.",
        "They were beautiful, and they did nothing for the sound.",
        "Then a friend clapped his hands once in the middle of the floor.",
        "The echo came from the bare wall behind the desk, and a hundred dollar blanket fixed it.",
        "Ever since, I clap first, I measure second and I buy last.",
        "So the rule is simple: if you cannot hear the problem, you cannot pay to solve it."]),
    ("the cable", [
        "Here is the funniest thing that ever happened to us on a live show.",
        "Ten minutes before we went on, the mixer stopped sending sound to the stream.",
        "We swapped the cable, we swapped the laptop, we restarted everything twice.",
        "Nothing worked, and the clock kept going, and I could feel my hands shaking.",
        "Then my daughter walked in, pointed at the floor and asked why the plug was lying there.",
        "The whole time, the cable had been sitting beside the socket, not in it.",
        "I had pulled it out with my foot while checking the lights.",
        "We went live one minute late, and I told the audience the truth, and they loved it."]),
    ("the cheap microphone", [
        "Everybody says you need the expensive microphone, and I think that is wrong.",
        "Last year I recorded the same ten minutes with six different microphones.",
        "I asked forty people to pick the best one without knowing which was which.",
        "The nine hundred dollar microphone came fourth.",
        "The thirty dollar one came first, with twenty-two votes.",
        "The reason is the room, because every microphone hears the room before it hears you.",
        "So spend your first hundred dollars on the space and your voice, not on the label.",
        "That is my claim, and the numbers are in the show notes if you want to check them."]),
]

_TUTORIAL_STRONG = [
    ("loop cut", [
        "Here is a trick that saves you ten minutes on every model.",
        "Press Ctrl R, hover over the edge you want, and scroll the wheel to add more cuts.",
        "Now click once, then move the mouse and click again to place the loop exactly.",
        "Before, I used to cut the mesh by hand, vertex by vertex, and it was always crooked.",
        "With one loop cut the edges flow around the whole object, and the shading stays clean.",
        "You can see the result right here: the curve is smooth and nothing is pinched.",
        "So whenever you need detail in one place, add a loop and slide it.",
        "That one shortcut is worth learning today."]),
    ("render samples", [
        "Your render takes twelve minutes, and it can take ninety seconds.",
        "Open the render settings and set the samples to sixty-four instead of a thousand.",
        "Then switch on the denoiser, and look at the noise in the dark corners.",
        "On my test scene the twelve minute render dropped to ninety seconds.",
        "I put the two images side by side, and nobody I asked could tell which was which.",
        "The reason is that the denoiser cleans what extra samples would only slowly clean.",
        "So lower the samples, turn on the denoiser, and keep your evenings.",
        "That is the whole tip, and it works on almost every scene."]),
    ("black normals", [
        "If your object looks black in the viewport, do not delete it.",
        "The cause is almost always flipped normals, which means the faces point inward.",
        "Select everything, press Shift N, and Blender recalculates them to point outward.",
        "You can check it by turning on face orientation, where blue is outside and red is inside.",
        "Last week a student sent me a model with half of it red, and one key press fixed it.",
        "It happens after mirroring or after importing from another program.",
        "So the fix takes three seconds once you know what you are looking at.",
        "Remember: black means flipped, and Shift N makes it right."]),
]


def _weak(rng: random.Random, templates: list[str], a: list[str], b: list[str], c: list[str], n: int) -> list[str]:
    out: list[str] = []
    for _ in range(n):
        if rng.random() < 0.35:
            out.append(rng.choice(_SMALL))
        else:
            out.append(rng.choice(templates).format(a=rng.choice(a), b=rng.choice(b), c=rng.choice(c)))
    return out


def _words(sentences: list[tuple[str, str]], rng: random.Random) -> tuple[list[dict], list[dict]]:
    """Words with times, and the span in seconds of each planted label."""
    words: list[dict[str, Any]] = []
    spans: dict[str, list[float]] = {}
    t = 1.0
    for label, text in sentences:
        start = t
        for w in text.split():
            words.append({"w": w, "start": round(t, 3), "end": round(t + WORD_LEN, 3), "p": 0.9})
            t += WORD_STEP
        end = t - WORD_STEP + WORD_LEN
        if label:
            span = spans.setdefault(label, [start, end])
            span[1] = end
        t += rng.uniform(*PAUSE)
    planted = [{"label": k, "start": round(v[0], 2), "end": round(v[1], 2)} for k, v in spans.items()]
    return words, planted


def _build(seed: int, strong: list, at_minutes: list[float], templates: list[str], a: list[str],
           b: list[str], c: list[str], minutes: float) -> dict[str, Any]:
    rng = random.Random(seed)
    sentences: list[tuple[str, str]] = []
    placed = 0
    clock = 0.0
    target = [m * 60.0 for m in at_minutes]
    while clock < minutes * 60.0:
        if placed < len(strong) and clock >= target[placed]:
            label, lines = strong[placed]
            for line in lines:
                sentences.append((label, line))
                clock += len(line.split()) * WORD_STEP + 0.9
            placed += 1
            continue
        chunk = _weak(rng, templates, a, b, c, 1)
        sentences.append(("", chunk[0]))
        clock += len(chunk[0].split()) * WORD_STEP + 0.9
    words, planted = _words(sentences, rng)
    return {"words": words, "planted": planted, "minutes": round(words[-1]["end"] / 60.0, 1)}


def podcast() -> dict[str, Any]:
    """A made-up hour-long podcast, three planted moments at about minutes 9, 24 and 47."""
    return _build(613, _PODCAST_STRONG, [9.0, 24.0, 47.0], _PODCAST_T, _PODCAST_A, _PODCAST_B, _PODCAST_C, 60.0)


def tutorial() -> dict[str, Any]:
    """A made-up hour-long tutorial, three planted moments at about minutes 12, 29 and 51."""
    return _build(614, _TUTORIAL_STRONG, [12.0, 29.0, 51.0], _TUT_T, _TUT_A, _TUT_B, _TUT_C, 60.0)
