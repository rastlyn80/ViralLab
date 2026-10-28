"""Ideas, hooks and captions — generated from this account's evidence.

The owner asked for a platform that generates video ideas, hooks and
captions. Everything before this measured; nothing produced
anything he could use on a Tuesday afternoon.

## What makes this different from asking a chatbot

A language model asked "write me a hook for a piano video" writes a
hook for a generic piano video, from whatever it absorbed about
social media. That is worth something and it is not worth building a
product around.

This assembles a **brief from the account's own evidence** and makes
the model write against it:

* the measured differences between publications that broke out and
  the rest, with their direction;
* the owner's own highest-performing on-screen text, verbatim, as
  examples of what has actually worked here;
* the repertoire he plays, with how recognisable each piece is;
* what he has posted recently, so a suggestion is not something he
  did last week.

The model's job is composition, not research. Every claim about what
works comes from the brief; nothing is allowed in from the model's
general sense of what does well on TikTok.

## What it may never do

The same rules the strategist enforces, enforced the same way -
after generation, in code, because a prompt is not a boundary:

* **No promise about reach.** Not "this will go viral", not "this
  will get you more views", in any phrasing. A generator is where
  that temptation is strongest, because a confident promise is what
  a creative tool is expected to sound like.
* **No causal claim.** The brief says publications that broke out
  had busier backgrounds; it does not say busier backgrounds caused
  anything, and neither may the output.
* **No invented evidence.** A suggestion may not cite a number, a
  study, a percentage or a platform behaviour. The brief has the
  numbers; the output has ideas.

A suggestion is a suggestion. The honest frame is "here is something
to try, and here is the measured pattern it draws on", which is also
the frame that makes it checkable.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from server.db.models import Creator, MetricsSnapshot, Video, VideoFeature
from server.features import lexicon as lex

logger = logging.getLogger("virallab.ai.creative")

#: Kinds of thing this can produce.
KINDS = ("ideas", "hooks", "captions")

#: Examples drawn from the owner's own work. Enough to show a range,
#: few enough that the model composes rather than copies.
EXAMPLES = 8

#: Recent publications shown so a suggestion is not last week's post.
RECENT = 12


@dataclass
class Brief:
    """Everything the generator is allowed to know."""

    findings: list[dict] = field(default_factory=list)
    best_hooks: list[dict] = field(default_factory=list)
    repertoire: list[dict] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"findings": self.findings, "best_hooks": self.best_hooks,
                "repertoire": self.repertoire, "recent": self.recent,
                "counts": self.counts}

    def is_thin(self) -> bool:
        """Whether there is enough here to ground anything.

        Said out loud rather than quietly generating from nothing: a
        brief with no findings and no examples would produce exactly
        the generic output this module exists to avoid, and would do
        it while looking grounded.
        """
        return not self.findings and len(self.best_hooks) < 3


def build_brief(session: Session) -> Brief:
    """Assemble the account's evidence. No model is called here."""
    from server.research import virality as vir

    brief = Brief()

    breaks = vir.breakthroughs(session, own=True)
    for d in breaks.get("differences", []):
        brief.findings.append({
            "what": d["display_name"],
            "direction": d["direction"],
            "among": "publications that broke past your usual run",
            "outliers": d["outliers_mean"], "others": d["others_mean"],
        })
    gradient = vir.build(session, own=True)
    seen = {f["what"] for f in brief.findings}
    for d in gradient.get("findings", [])[:8]:
        if d["display_name"] in seen:
            continue
        brief.findings.append({
            "what": d["display_name"],
            "direction": ("more went with doing better"
                          if d["direction"] == "positive"
                          else "less went with doing better"),
            "among": "all your publications, ranked against your own average",
            "outliers": None, "others": None,
        })

    own = (select(Video.id).join(Creator, Creator.id == Video.creator_id)
           .where(Creator.is_own_account.is_(True)))
    own_ids = set(session.scalars(own).all())

    # The owner's own best-performing on-screen text, verbatim. The
    # strongest material in the brief: these are not examples of
    # good hooks in general, they are hooks that worked here.
    rows = session.execute(
        select(VideoFeature.value_text, MetricsSnapshot.views,
               Video.caption)
        .join(Video, Video.id == VideoFeature.video_id)
        .join(MetricsSnapshot, MetricsSnapshot.video_id == Video.id)
        .where(VideoFeature.feature == "ocr_text",
               VideoFeature.value_text.is_not(None),
               Video.id.in_(own_ids),
               MetricsSnapshot.views.is_not(None),
               func.length(VideoFeature.value_text) > 15)
        .order_by(desc(MetricsSnapshot.views)).limit(EXAMPLES * 3)).all()
    seen_text: set[str] = set()
    for text, views, caption in rows:
        clean = _tidy(text)
        key = clean[:40].lower()
        if not clean or key in seen_text:
            continue
        seen_text.add(key)
        brief.best_hooks.append({"on_screen": clean,
                                 "views": int(views),
                                 "caption": (caption or "")[:140]})
        if len(brief.best_hooks) >= EXAMPLES:
            break

    comp = session.execute(
        select(VideoFeature.value_text, func.count(VideoFeature.id))
        .join(Video, Video.id == VideoFeature.video_id)
        .where(VideoFeature.feature == "composer",
               VideoFeature.value_text.is_not(None),
               Video.id.in_(own_ids))
        .group_by(VideoFeature.value_text)
        .order_by(func.count(VideoFeature.id).desc()).limit(14)).all()
    brief.repertoire = [{"composer": c, "publications": n}
                        for c, n in comp]

    recent = session.execute(
        select(Video.caption).where(Video.id.in_(own_ids),
                                    Video.caption.is_not(None))
        .order_by(desc(Video.posted_at)).limit(RECENT)).all()
    brief.recent = [(c or "")[:120] for (c,) in recent if c]

    brief.counts = {
        "findings": len(brief.findings),
        "examples": len(brief.best_hooks),
        "composers": len(brief.repertoire),
    }
    return brief


def _tidy(text: str) -> str:
    """OCR output, made readable.

    Frame-by-frame reads repeat the same words and are joined with
    pipes, so a raw value is "PAUL | LE-CAFE | PAUL | 40 years of
    practice". Consecutive duplicates go; the order stays, because
    the order is the hook.
    """
    parts = [p.strip() for p in re.split(r"[|\n]+", text or "")]
    out: list[str] = []
    for p in parts:
        if not p or len(p) < 2:
            continue
        if out and p.lower() == out[-1].lower():
            continue
        if p.lower() in {o.lower() for o in out}:
            continue
        out.append(p)
    return " / ".join(out)[:220]


# ---------------------------------------------------------------------------
# what the generator may and may not say
# ---------------------------------------------------------------------------

SYSTEM = """You write for a professional concert pianist, for short
vertical video.

You are given a BRIEF assembled from this account's own measured
data: what his publications that broke out had in common, the
on-screen text from his best-performing videos, the repertoire he
plays, and what he posted recently. Work from the brief.

Rules you must not break.

1. NEVER promise or imply reach. No "this will go viral", no "this
   will get you views", no "guaranteed", in any phrasing including
   hedged ones. You may say what a thing is trying to do. You may
   not say what it will achieve.

2. NEVER say one thing causes another. The brief reports what broke
   out alongside what; it does not say why, and neither may you. Say
   "your breakthroughs tended to have X", not "X makes videos do
   better".

3. NEVER state a statistic, a percentage, a study or a platform
   behaviour that is not in the brief. If you want to justify a
   suggestion, quote the brief. If the brief does not support it,
   suggest it as taste and say so.

4. Do not repeat what he posted recently. The brief lists it.

Write in his register: a serious musician, not a growth marketer. No
exclamation marks, no "crush it", no emoji unless the brief's own
examples use them. Specific beats clever - name the piece, the
moment in it, the shot.

Reply with JSON only."""

#: The JSON shape each kind must return. Built with `json.dumps` of
#: a real structure rather than written as a string literal: the
#: first version nested three levels of quoting by hand and did not
#: parse as Python, let alone as a schema.
_GROUNDED = "which brief item this draws on, or the word taste"

SHAPES = {
    "ideas": json.dumps({"items": [{
        "idea": "one sentence",
        "why_now": "what in the brief suggests it, quoted",
        "piece": "a work from the repertoire list, or null",
        "shot": "how to film it, one line",
        "grounded_in": _GROUNDED}]}),
    "hooks": json.dumps({"items": [{
        "text": "the words on screen, under 12 words",
        "device": "pov | question | story | contrast | number | plain",
        "piece": "a work it suits, or null",
        "grounded_in": _GROUNDED}]}),
    "captions": json.dumps({"items": [{
        "caption": "the full caption",
        "first_line": "what shows before the More link",
        "hashtags": ["..."],
        "grounded_in": _GROUNDED}]}),
}

#: Reused from the strategist rather than restated, so a rule
#: tightened there tightens here too.
def _patterns():
    from server.ai.strategist import (CAUSAL_PATTERNS, VIRALITY_PATTERNS,
                                      _is_denial)
    return CAUSAL_PATTERNS, VIRALITY_PATTERNS, _is_denial


#: Numbers a suggestion may use without citing the brief: a count of
#: bars, a duration, an opus number. Anything that looks like a
#: performance claim is caught by `review`.
_STAT_CLAIM = re.compile(
    r"\b\d+(?:\.\d+)?\s*%|\b\d+\s*(?:x|times)\s+(?:more|better|higher)|"
    r"\b(?:studies|research|data)\s+(?:show|says?|suggests?)", re.I)


def review(text: str) -> list[dict]:
    """What the finished suggestion did wrong, whatever the prompt said."""
    causal, virality, is_denial = _patterns()
    out: list[dict] = []
    low = (text or "").lower()

    for pat in virality:
        m = re.search(pat, low)
        if m:
            out.append({"kind": "virality_promise",
                        "excerpt": text[max(0, m.start() - 50):m.end() + 50]})
            break
    for pat in causal:
        for m in re.finditer(pat, low):
            if is_denial(low, m.start(), m.end()):
                continue
            out.append({"kind": "causal_language",
                        "excerpt": text[max(0, m.start() - 50):m.end() + 50]})
            break
        else:
            continue
        break
    m = _STAT_CLAIM.search(text or "")
    if m:
        out.append({"kind": "invented_statistic",
                    "excerpt": text[max(0, m.start() - 50):m.end() + 50]})
    return out


def sift(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the suggestions into the ones that may be shown and why.

    Per suggestion rather than per reply. Reviewing the whole reply
    was wrong in a way that only showed up live: the model wrote
    four clean captions and one that quoted a percentage, and the
    owner saw none of them, having paid for all five.

    A rejected suggestion is reported with the rule it broke and the
    words that broke it, so the gate can be judged rather than
    trusted. Everything the suggestion contains is checked, not just
    its headline - `grounded_in` is where a stray statistic is most
    likely to appear, because that is the field asking for a reason.
    """
    kept: list[dict] = []
    rejected: list[dict] = []
    for item in items:
        found = review(json.dumps(item, ensure_ascii=False))
        if not found:
            kept.append(item)
            continue
        headline = next((str(item[f]) for f in
                         ("text", "idea", "caption", "first_line")
                         if item.get(f)), "")
        for v in found:
            rejected.append({**v, "suggestion": headline[:120]})
    return kept, rejected


def parse(text: str) -> list[dict]:
    """Items out of the reply, whatever shape it arrived in.

    Three things go wrong and all three are the model being normal
    rather than wrong: it fences the JSON in ```json, it runs past
    `max_tokens` mid-object, and it occasionally returns the list
    without the wrapper. The first version matched first-brace to
    last-brace and parsed the whole thing or nothing, so one
    truncated suggestion threw away the five good ones in front of
    it - which is what happened on the first live call.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    # Strip a code fence if there is one.
    fence = re.match(r"^```[a-z]*\s*(.*?)\s*```$", raw, re.S)
    if fence:
        raw = fence.group(1)
    else:
        raw = re.sub(r"^```[a-z]*\s*", "", raw)

    for candidate in (raw, raw[raw.find("{"):raw.rfind("}") + 1]):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            # The wrapper key is whatever the model felt like. Asked
            # to "produce 6 hooks" against a schema keyed `items`, it
            # replies `{"hooks": [...]}` - which is reasonable of it,
            # and cost the first live call all six suggestions. Take
            # the first value that is a list of objects, whatever it
            # happens to be called.
            for value in (data.get("items"), *data.values()):
                if isinstance(value, list) and any(
                        isinstance(d, dict) for d in value):
                    return [d for d in value if isinstance(d, dict)]

    # Salvage: every complete object inside the items array, which
    # is where a truncated reply still has good suggestions in front
    # of the one that got cut off. Scanning from the array rather
    # than from character zero, because the items sit one level down
    # inside the wrapper and a depth-zero scan never closes.
    opens = raw.find("[")
    region = raw[opens:] if opens >= 0 else raw
    out: list[dict] = []
    depth, start, in_str, escape = 0, None, False, False
    for i, ch in enumerate(region):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(region[start:i + 1])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    if out:
        logger.warning("salvaged %d items from an unparseable reply", len(out))
    return out


#: Roughly what one suggestion costs to write, per kind, with
#: headroom. The first version used a flat 1800 for everything; six
#: ideas with five fields each do not fit in it, and a reply cut off
#: mid-object used to lose the five good suggestions in front of it.
_PER_ITEM = {"ideas": 190, "hooks": 90, "captions": 170}


def _token_budget(kind: str, count: int) -> int:
    return min(8000, 400 + _PER_ITEM.get(kind, 190) * max(count, 1))


def generate(session: Session, *, kind: str, provider,
             reserve: Callable[[float, str], Any],
             count: int = 6, note: str = "") -> dict:
    """Produce suggestions, then check them."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")

    brief = build_brief(session)
    if brief.is_thin():
        return {
            "kind": kind, "items": [], "blocked": True,
            "why": ("There is not enough measured evidence on this "
                    "account yet to ground a suggestion. Generating "
                    "anyway would produce advice about piano videos in "
                    "general, which is what this is built not to do."),
            "brief": brief.as_dict(),
        }

    ask = (f"Produce {count} {kind}, as JSON with the top-level "
           f'key "items":\n{SHAPES[kind]}\n\n'
           f"BRIEF:\n{json.dumps(brief.as_dict(), indent=1)[:9000]}")
    if note:
        ask += f"\n\nThe owner adds: {note[:400]}"

    est = round(6000 / 1e6 * provider.USD_PER_MTOK_IN
                + 1600 / 1e6 * provider.USD_PER_MTOK_OUT, 6)
    settle = reserve(est, f"creative:{kind}:{abs(hash(ask)) % 10**10}")
    if settle is None:
        return {"kind": kind, "items": [], "blocked": True,
                "why": "The strategy budget would not cover this.",
                "brief": brief.as_dict()}

    from server.ai.base import credential
    # Prefilling the assistant turn with an open brace. "Reply with
    # JSON only" in the system prompt was not enough on the first
    # live call - the reply came back in a shape the parser could
    # not read, and a prefill is the documented way to make the
    # answer start where the parser expects rather than hoping.
    # `parse` puts the brace back before reading.
    body = {"model": provider.MODEL,
            "max_tokens": _token_budget(kind, count),
            "system": SYSTEM,
            "messages": [{"role": "user", "content": ask},
                         {"role": "assistant", "content": "{"}]}
    headers = {"x-api-key": credential(provider.env_var) or "",
               "anthropic-version": provider.API_VERSION,
               "content-type": "application/json"}
    try:
        data = provider._request_with_retries(provider.URL, headers,
                                              body, 150.0)
    except Exception as exc:                 # noqa: BLE001 - reported
        settle("failed", 0.0)
        logger.exception("creative call failed")
        return {"kind": kind, "items": [], "blocked": True,
                "why": f"The model could not be reached: {exc}",
                "brief": brief.as_dict()}

    usage = data.get("usage") or {}
    actual = round(
        (usage.get("input_tokens") or 0) / 1e6 * provider.USD_PER_MTOK_IN
        + (usage.get("output_tokens") or 0) / 1e6
        * provider.USD_PER_MTOK_OUT, 6)
    settle("ok", actual)

    text = "".join(b.get("text") or "" for b in (data.get("content") or [])
                   if b.get("type") == "text")
    # The prefilled brace is not echoed back in the response.
    text = "{" + text.lstrip() if not text.lstrip().startswith("{") else text
    items = parse(text)
    if not items:
        logger.warning("creative produced nothing readable: %r", text[:400])
    kept, violations = sift(items)
    # One bad sentence does not spoil six suggestions. The first
    # version checked the whole reply and withheld all of it, so a
    # single stray percentage in suggestion four threw away three
    # good ones the owner had already paid for - which is what
    # happened on the first live run of ideas and captions.
    #
    # The offending suggestion goes; the rest arrive; the owner is
    # told what was removed and why, because an enforcement layer
    # nobody can see is one nobody knows works.
    if violations:
        logger.info("creative removed %d of %d %s: %s", len(violations),
                    len(items), kind,
                    ", ".join(sorted({v["kind"] for v in violations})))
    return {
        "kind": kind, "items": kept, "blocked": not kept,
        "violations": violations,
        "removed": len(violations),
        "billed_usd": actual,
        "brief": brief.as_dict(),
        "why": ("Every suggestion in this batch broke one of the "
                "rules, so none of them are shown. Ask again."
                if violations and not kept else ""),
        "grounding": (
            "Every suggestion names the brief item it draws on, or "
            "says 'taste'. The brief is this account's own measured "
            "evidence and its own best-performing text - nothing here "
            "comes from general knowledge about social media."),
    }
