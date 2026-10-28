"""One call per video, forty-odd answers, built from the question set.

## Why this exists

The committed vision stage asks a 4B captioning model for six
booleans and gets two usable ones. The 2026-10-27 benchmarks settled
why: it answers from the genre rather than the frame, and no prompt
fixes that. A frontier model does not have the failure, and on the
owner's own review of the hardest twenty-one labels it sided with the
owner against the answer key three times out of five.

The second thing those benchmarks settled is cheaper: **asking is
nearly free and checking is expensive.** One request carrying forty
questions costs barely more than one carrying six, because the images
dominate the input and the extra answers are a few hundred output
tokens. Haiku 4.5 and Sonnet tied at nine errors each over 40
publications on the owner-corrected labels, so this uses Haiku at a
quarter of the price.

## The prompt is generated, never written twice

`vision_questions.ALL` is the single list of what may be asked. The
schema, the prompt and the parser are all derived from it, so adding
a question is a one-line change there and nothing here needs
touching. A hand-written prompt beside a declared question list is
two sources of truth, and the one that runs is whichever the caller
happened to use.

## Describe first, then answer

The owner proposed it and the measurement supports it, conditionally:
on Gemma it changed nothing, and on a model strong enough to use its
own description it rescued on-screen-text recall from 0.53 to 0.89.
So every request asks for per-frame descriptions first and requires
the answers to follow from them.

The descriptions are kept. They are the most human-readable thing
this pipeline produces, and a reader who disagrees with an answer can
see what the model thought it was looking at.

## What this does NOT do

It does not promote anything. Every field it writes carries the
reliability grade `vision_questions` gives it, and only `verified`
fields may enter a statistic. Running a better model over more
publications produces better *descriptions*; it produces no new
claims until a prospective holdout says so.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from server.ai import vision_questions as vq

logger = logging.getLogger("virallab.ai.wide_vision")

#: Bumped whenever the question set or the prompt changes in a way
#: that makes old rows non-comparable. Stored on every feature row.
METHOD_VERSION = "hook-vision-wide-haiku45-v1"

#: Questions a vision-language model can answer from frames. The
#: deterministic ones are arithmetic and already computed elsewhere;
#: asking a model for them would be slower and worse.
ASKABLE = tuple(q for q in vq.ALL
                if q.method in ("detector", "vlm")
                and "Not built" not in q.basis)

#: How many frame descriptions to ask for. Matches the frame plan.
MAX_FRAMES = 6


def _answer_shape(q: vq.Question) -> str:
    if q.answer == "boolean":
        return "true or false"
    if q.answer == "enum":
        return " | ".join(q.values)
    if q.answer == "count":
        return "a whole number"
    if q.answer == "number":
        return "a number"
    return "a short phrase, or null"


def schema() -> dict:
    """JSON schema for the reply, derived from the question set."""
    props: dict[str, Any] = {
        "frame_descriptions": {
            "type": "array", "maxItems": MAX_FRAMES,
            "items": {"type": "string", "maxLength": 400},
        },
    }
    for q in ASKABLE:
        if q.answer == "boolean":
            props[q.name] = {"type": "boolean"}
        elif q.answer == "enum":
            props[q.name] = {"type": "string", "enum": list(q.values)}
        elif q.answer in ("count", "number"):
            props[q.name] = {"type": "number"}
        else:
            props[q.name] = {"type": ["string", "null"], "maxLength": 200}
    return {"type": "object", "additionalProperties": False,
            "required": ["frame_descriptions"], "properties": props}


#: The rules that cost the project the most when they were implicit.
#: Each one is here because a measurement or the owner's review showed
#: it being got wrong, not because it seemed sensible.
RULES = """
ANSWER FROM THE FRAMES, NOT FROM THE GENRE. These are a pianist's
videos, so most of them contain a piano - and many individual frames
do not. A frame showing only a face, an audience, a title card or an
empty stage contains no keyboard, and the honest answer is false. A
small model failed this project by answering "yes, piano" on 79 of 80
publications regardless of the pixels.

A FIELD IS TRUE IF THE THING APPEARS IN ANY OF THE FRAMES YOU ARE
SHOWN. Not only the first. This is the rule the labels were made
under.

"piano_or_keyboard_visible" MEANS THE KEYS ARE VISIBLE. A piano seen
side-on, from behind, or with the lid open and no keys in shot is
false. The instrument being present is not the question.

"face_visible" means a recognisable human face. The back of a head
does not count, and a face too small to resolve does not count.
A painted or photographed face - a portrait, a reproduction on a
screen - is not a person in the scene, and is false.

"text_overlay_present" means text the creator added on top of the
video. Printed text already in the scene, such as sheet music or a
poster, is false.

Where you cannot tell, choose the value that claims least. Do not
guess to fill the field.
""".strip()


def prompt() -> str:
    """The full instruction, generated from the question set."""
    lines = [
        "You are looking at frames sampled from the opening of a short "
        "video by a classical pianist.",
        "",
        "STEP 1. For each frame in order, write one or two sentences "
        "saying what is literally in it: the subject, what fills the "
        "frame, what is behind it, and any text on screen. Describe "
        "only what you can see.",
        "",
        "STEP 2. Answer the questions below USING ONLY YOUR OWN "
        "DESCRIPTIONS from step 1. If a thing appears in none of your "
        "descriptions, the answer is false. Do not introduce anything "
        "in step 2 that you did not write in step 1.",
        "",
        RULES,
        "",
        "Reply with JSON only, with these keys:",
        '  "frame_descriptions": a list of one sentence per frame',
    ]
    for q in ASKABLE:
        lines.append(f'  "{q.name}": {_answer_shape(q)}   # {q.asks}')
    return "\n".join(lines)


def parse(text: str) -> dict:
    """Pull the answers out, tolerating whatever wraps them.

    Models truncate and they fence their JSON. A parser that only
    accepted a clean object threw away whole publications in the
    first benchmark run, which looked like refusals and was not.
    """
    out: dict[str, Any] = {}
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            out = json.loads(m.group(0))
        except json.JSONDecodeError:
            out = {}
    if out:
        return _coerce(out)

    # Field by field, for a truncated reply.
    salvaged: dict[str, Any] = {}
    for q in ASKABLE:
        if q.answer == "boolean":
            hit = re.search(rf'"{q.name}"\s*:\s*(true|false)', text or "")
            if hit:
                salvaged[q.name] = hit.group(1) == "true"
        elif q.answer == "enum":
            hit = re.search(rf'"{q.name}"\s*:\s*"([^"]+)"', text or "")
            if hit and hit.group(1) in q.values:
                salvaged[q.name] = hit.group(1)
        elif q.answer in ("count", "number"):
            hit = re.search(rf'"{q.name}"\s*:\s*(-?[0-9.]+)', text or "")
            if hit:
                try:
                    salvaged[q.name] = float(hit.group(1))
                except ValueError:
                    pass
    descs = re.findall(r'"([^"]{40,})"', text or "")
    if descs:
        salvaged["frame_descriptions"] = descs[:MAX_FRAMES]
    if salvaged:
        salvaged["_truncated"] = True
    return salvaged


def _coerce(raw: dict) -> dict:
    """Keep only declared keys, with values of the declared shape.

    A model that invents a field, or answers an enum with a word
    outside its vocabulary, must not have that stored: an unexpected
    value in `video_features` is indistinguishable from a measurement
    once it is in the table.
    """
    out: dict[str, Any] = {}
    descs = raw.get("frame_descriptions")
    if isinstance(descs, list):
        out["frame_descriptions"] = [str(d)[:400] for d in descs
                                     if isinstance(d, (str, int, float))
                                     ][:MAX_FRAMES]
    for q in ASKABLE:
        if q.name not in raw:
            continue
        v = raw[q.name]
        if q.answer == "boolean":
            if isinstance(v, bool):
                out[q.name] = v
        elif q.answer == "enum":
            if isinstance(v, str) and v in q.values:
                out[q.name] = v
            elif isinstance(v, str):
                logger.debug("%s answered %r, outside its vocabulary",
                             q.name, v)
        elif q.answer in ("count", "number"):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[q.name] = float(v)
        else:
            if v is None or isinstance(v, str):
                out[q.name] = (v or None) and str(v)[:200]
    return out


def feature_rows(parsed: dict) -> dict[str, Any]:
    """The values to store, keyed by the name used in the catalogue.

    Prefixed `vision_` to match `visual_structure.VISION_FEATURES`
    and the catalogue, so a wide run lands in the same namespace the
    rest of the system already reads.
    """
    rows: dict[str, Any] = {}
    for q in ASKABLE:
        if q.name in parsed:
            rows[f"vision_{q.name}"] = parsed[q.name]
    if parsed.get("frame_descriptions"):
        rows["vision_frame_descriptions"] = parsed["frame_descriptions"]
    return rows


def reliability_of(feature: str) -> str:
    """The grade a stored row inherits. Never upgraded by running."""
    name = feature[len("vision_"):] if feature.startswith("vision_") \
        else feature
    q = vq.by_name(name)
    return q.reliability if q else "unverified"
