"""M8: a chat that cannot invent a number, enforced rather than asked.

## The shape of the problem

A language model over a research database is a machine for producing
confident sentences, and this project's whole value is that its
sentences are earned. So the design assumes the model will try to
help by filling gaps, and makes that impossible rather than
discouraged.

Three structural choices do the work:

**The model never sees the database.** It sees whatever the five
read-only tools below return, and nothing else. There is no SQL
path, no table list, no schema in the prompt. A question the tools
cannot answer is a question that gets "I don't have that", which is
the correct answer and the one a model is least inclined to give.

**Every number in the answer is checked against the tool results.**
Not "the model was asked to cite" - the finished text is scanned, and
any figure that does not appear in something a tool actually returned
is flagged, logged, and shown to the reader as unverified. This is
the one guarantee that does not depend on the model cooperating.

**The refusal rules are code, not prose.** The system prompt states
them because that makes compliance likely; `review()` enforces them
because prompts are not a security boundary. Causal language about
anything below `Validated`, virality promises, and answers about
withheld measurements are caught after generation whatever the
prompt said.

## What it may never do

* **Claim causation below `Validated`.** Nothing in this project is
  Validated, because only the M9 pre-registered experiment can
  produce that grade. So the honest position today is that *nothing*
  here causes anything, and the enforcement is absolute rather than
  conditional.
* **Promise virality.** In any phrasing, including hedged ones.
* **Answer from a withheld measurement.** `shot_distance` and
  `performer_visible` are withheld because they were measured and
  failed; a chat that quietly used them would undo that decision.
* **Identify a person.** Out of scope, and at least one provider
  refuses it outright anyway.

## Cost

Through the existing provider layer under the `strategy` category,
which ships at $0.00 and stays there until the owner raises it in the
dashboard. Building this authorises nothing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.ai import vision_questions as vq
from server.db.models import Creator, Video
from server.features import catalogue as cat
from server.features import lexicon as lex
from server.features import visual_structure as vs

logger = logging.getLogger("virallab.ai.strategist")

MODEL_TASK = "strategist_v1"

#: Everything in this project is Observed, Hypothesised or Associated.
#: Nothing is Validated, so the causal gate is currently total - and
#: it is written as a comparison rather than a constant so that it
#: opens by itself on the day M9 produces one.
CAUSAL_OK_AT = "Validated"

#: Phrases that assert causation. Matched case-insensitively against
#: the finished answer. Deliberately broad: a false positive costs a
#: caveat, a false negative costs the project its credibility.
CAUSAL_PATTERNS = (
    r"\bcause[sd]?\b", r"\bcausing\b", r"\bbecause of\b",
    r"\bdrives?\b", r"\bdriving\b", r"\bleads? to\b", r"\bresults? in\b",
    r"\bmakes? (?:people|viewers|them)\b", r"\bdue to\b",
    r"\bresponsible for\b", r"\bthe reason (?:why|that|is)\b",
    r"\bwill (?:increase|improve|boost|reduce|lower)\b",
    r"\bif you .{0,40}\byou(?:'ll| will)\b",
    # Added when the generator was built. "X makes videos perform
    # better" is the phrasing a creative tool reaches for first, and
    # nothing above caught it: `makes?` was bounded to people,
    # viewers and them. Kept narrow on purpose - "makes you feel" is
    # a legitimate hook about music, not a claim about reach, and
    # has to keep passing.
    r"\bmakes? .{0,40}?\b(?:perform|do) (?:better|worse)\b",
    r"\bhelps? (?:your |the )?(?:videos?|reels?|posts?|content)\b",
    r"\b(?:boosts?|boosting|increases?|increasing) (?:your |the )?"
    r"(?:views?|reach|engagement|performance|watch[- ]time)\b",
    r"\bthe key to (?:getting|more|better|a)\b",
    # "Fewer emoji performs better" - seen in a live suggestion. It
    # reads as a report of the measurement and is not one: the brief
    # says fewer emoji WENT WITH doing better on both accounts,
    # which is a statement about videos that exist. This phrasing
    # turns it into a rule. Deliberately narrow so the brief's own
    # wording - "went with doing better" - keeps passing.
    r"\b(?:performs?|perform|works?|does|do) better\b",
)

#: Promises about reach. Also deliberately broad.
VIRALITY_PATTERNS = (
    r"\bgo viral\b", r"\bgoes viral\b", r"\bgoing viral\b",
    r"\bvirality\b", r"\bguarantee", r"\bblow up\b",
    r"\bmillions of views\b", r"\bsure[- ]fire\b",
)

#: Asking who somebody is.
IDENTITY_PATTERNS = (
    r"\bwho is (?:the|that|this) (?:person|man|woman|performer|pianist)\b",
    r"\bidentify (?:the|this|that) (?:person|face|performer)\b",
    r"\bwhat(?:'s| is) (?:his|her|their) name\b",
)


def jsonable(obj: Any) -> Any:
    """Tool output a JSONB column and a JSON body both accept.

    `cohort.compare` returns dates in its composition, and the first
    live run of the cohort question died with "Object of type
    datetime is not JSON serializable" - after the model had been
    called and the turn billed. Coerced once here rather than at each
    boundary, so the model and the stored log see the same bytes and
    a logged answer can be replayed against the evidence it had.
    """
    from datetime import date, datetime
    from decimal import Decimal

    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    return str(obj)


@dataclass
class ToolCall:
    name: str
    arguments: dict
    result: Any = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {"name": self.name, "arguments": self.arguments,
                "result": self.result, "error": self.error}


@dataclass
class Violation:
    kind: str
    detail: str
    excerpt: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail,
                "excerpt": self.excerpt}


@dataclass
class Answer:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    unverified_numbers: list[str] = field(default_factory=list)
    blocked: bool = False
    refusal: str | None = None

    def as_dict(self) -> dict:
        return {
            "text": self.refusal if self.blocked else self.text,
            "blocked": self.blocked,
            "refusal": self.refusal,
            "tool_calls": [t.as_dict() for t in self.tool_calls],
            "violations": [v.as_dict() for v in self.violations],
            "unverified_numbers": list(self.unverified_numbers),
        }


# ---------------------------------------------------------------------------
# the five tools
# ---------------------------------------------------------------------------

def _creator_report(session: Session, *, creator_id: int,
                    **_: Any) -> dict:
    from server.research import report as rep
    r = rep.build(session, creator_id)
    return {
        "creator_id": creator_id,
        "sections": [
            {"key": s.key, "title": s.title, "basis": s.basis,
             "grade": getattr(s, "grade", None),
             "unavailable_reason": s.unavailable_reason,
             "rows": (s.rows[:12] if getattr(s, "rows", None) else [])}
            for s in r.sections],
        "limitations": list(r.data_limitations or []),
    }


def _compare(session: Session, *, collection_id: int,
             **_: Any) -> dict:
    from server.research import cohort as co
    c = co.compare(session, collection_id)
    return {
        "collection": c.collection_name,
        "composition": c.composition,
        "meets_minimum": c.meets_minimum,
        "differences": [
            {"feature": d.feature, "display_name": d.display_name,
             "plain": d.plain, "grade": d.grade,
             # `cohort_median_of_medians`, never a pooled median.
             # M6 exists because pooling lets the creator who posts
             # most speak for the cohort, and the shorter attribute
             # name does not exist for exactly that reason.
             "you": d.owner_median,
             "them": d.cohort_median_of_medians,
             "q_value": d.q_value, "n_creators": d.cohort_members}
            for d in c.differences],
        "limitations": list(c.limitations or []),
    }


def _publication(session: Session, *, video_id: int, **_: Any) -> dict:
    from server.collectors import permalink
    from server.db.models import VideoFeature

    v = session.get(Video, video_id)
    if v is None:
        return {"error": f"no publication {video_id}"}
    c = session.get(Creator, v.creator_id)
    rows = session.scalars(select(VideoFeature).where(
        VideoFeature.video_id == video_id,
        VideoFeature.status == "ok")).all()
    out = []
    for r in rows:
        name = r.feature
        # An UNVERIFIED measurement is not served, at the tool
        # boundary rather than in the prompt: the model cannot
        # decline to use what it was never given.
        #
        # An `indicative` one is served. It is barred from statistics,
        # not from description, and the first live run showed the
        # cost of conflating those - "is sheet music visible in
        # publication 287?" came back "not measured", which is false.
        short = name[len("vision_"):] if name.startswith("vision_") else name
        fq = vq.by_name(short)
        if (name in vs.WITHHELD_VISION and name not in vs.VISION_FEATURES
                and (fq is None or fq.reliability == "unverified")):
            continue
        value = (r.value_bool if r.value_bool is not None else
                 r.value_num if r.value_num is not None else
                 r.value_text if r.value_text is not None else r.value_json)
        out.append({"feature": name, "value": value,
                    "display_name": lex.display_name(name),
                    "method_version": r.method_version,
                    "reliability": (r.params or {}).get("reliability")})
    return {
        "video_id": video_id, "platform": v.platform,
        "caption": (v.caption or "")[:300],
        "posted_at": v.posted_at.isoformat() if v.posted_at else None,
        "duration_s": v.duration_s,
        "permalink": permalink.for_video(v, c),
        "measurements": out,
    }


def _resolve(name: str, known) -> str | None:
    """A name the model typed, mapped to a name the table stores.

    A model asked about "edge density" types "edge density", and the
    column holds `edge_density`. The first live run lost two of ten
    questions to exactly that, and reported "no such measurement" -
    which is worse than an error, because it reads like a fact about
    the data.
    """
    if name in known:
        return name
    squashed = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    for candidate in (squashed, f"vision_{squashed}",
                      squashed.removeprefix("vision_")):
        if candidate in known:
            return candidate
    # Last resort: a unique substring match in either direction.
    hits = [k for k in known
            if squashed and (squashed in k or k in squashed)]
    return hits[0] if len(hits) == 1 else None


def _feature_lookup(session: Session, *, name: str, **_: Any) -> dict:
    entries = {e.spec.name: e for e in cat.build(session)}
    resolved = _resolve(name, entries)
    e = entries.get(resolved) if resolved else None
    if e is None:
        squashed = re.sub(r"[^a-z0-9]+", "_", (name or "").lower())
        close = [n for n in entries
                 if any(w and w in n for w in squashed.split("_"))][:8]
        return {"error": f"no feature named {name!r}",
                "did_you_mean": close,
                "note": ("Feature names use underscores. Try one of the "
                         "suggestions, or ask for the catalogue.")}
    q = vq.by_name(name[len("vision_"):] if name.startswith("vision_")
                   else name)
    return {
        "feature": name,
        "display_name": lex.display_name(name),
        "describes": e.spec.describes,
        "grade": e.grade,
        "grade_reason": e.grade_reason,
        "coverage": e.coverage,
        "withheld": e.spec.withheld,
        "measurement_reliability": q.reliability if q else None,
        "may_support_a_claim": bool(q and q.reliability == "verified"),
    }


def _evidence_for(session: Session, *, feature: str,
                  outcome: str | None = None, **_: Any) -> dict:
    from server.db.models import FeatureEvidence

    known = {f for f in session.scalars(
        select(FeatureEvidence.feature).distinct()).all()}
    feature = _resolve(feature, known) or feature
    q = select(FeatureEvidence).where(FeatureEvidence.feature == feature)
    if outcome:
        # "skip rate" is not `reels_skip_rate`, and filtering on the
        # mismatch returned "no evidence" for a relationship that
        # exists - the worst possible failure for this tool, because
        # absence of evidence is exactly what it is asked to report.
        outcomes = {o for o in session.scalars(
            select(FeatureEvidence.outcome).distinct()).all()}
        resolved = _resolve(outcome, outcomes)
        if resolved:
            q = q.where(FeatureEvidence.outcome == resolved)
    rows = session.scalars(q).all()
    if not rows:
        return {"feature": feature, "outcome": outcome, "rows": [],
                "note": "no stored evidence for this relationship"}
    from server.research import insights as ins
    return {"feature": feature, "outcome": outcome, "rows": [
        {"outcome": r.outcome, "population": r.population, "n": r.n,
         "rho": r.rho, "partial_rho": r.partial_rho,
         "q_value": r.q_value, "controls": list(r.controls or []),
         "grade_of_this_row": ins.row_status(r),
         "unavailable_n": r.unavailable_n}
        for r in rows]}


TOOLS: dict[str, Callable[..., Any]] = {
    "creator_report": _creator_report,
    "compare": _compare,
    "publication": _publication,
    "feature_lookup": _feature_lookup,
    "evidence_for": _evidence_for,
}

TOOL_SCHEMAS = [
    {"name": "creator_report",
     "description": ("The full report for one creator: every section, "
                     "its basis and its grade."),
     "input_schema": {"type": "object", "required": ["creator_id"],
                      "properties": {"creator_id": {"type": "integer"}}}},
    {"name": "compare",
     "description": ("How the owner differs from a saved collection of "
                     "creators. Every difference is Observed and "
                     "nothing more, because no outcome was measured on "
                     "the other creators."),
     "input_schema": {"type": "object", "required": ["collection_id"],
                      "properties": {
                          "collection_id": {"type": "integer"}}}},
    {"name": "publication",
     "description": ("One publication: its caption, its link and every "
                     "measurement stored for it. Withheld measurements "
                     "are not returned at all."),
     "input_schema": {"type": "object", "required": ["video_id"],
                      "properties": {"video_id": {"type": "integer"}}}},
    {"name": "feature_lookup",
     "description": ("What one measurement is, how far it is trusted, "
                     "and how many publications carry it."),
     "input_schema": {"type": "object", "required": ["name"],
                      "properties": {"name": {"type": "string"}}}},
    {"name": "evidence_for",
     "description": ("The stored statistical evidence for a "
                     "feature-outcome relationship, with its n, its "
                     "corrected q-value and the grade that earns."),
     "input_schema": {"type": "object", "required": ["feature"],
                      "properties": {"feature": {"type": "string"},
                                     "outcome": {"type": "string"}}}},
]


SYSTEM = """You are ViralLab's analyst. You answer the owner's questions
about his own social-media data, and you answer them only from the
tools.

You have no access to any database. If the tools do not return
something, you do not know it, and the correct answer is to say so.
Never estimate, never fill a gap from general knowledge about social
media, and never carry a number from one answer to the next - look it
up again.

EVERY NUMBER YOU STATE MUST COME FROM A TOOL RESULT IN THIS
CONVERSATION. Numbers are checked against the tool results after you
answer, and any figure that is not in them is shown to the reader as
unverified. This is checked in code; it is not a matter of trust.

Rules you must not break:

1. NOTHING IN THIS PROJECT HAS BEEN SHOWN TO CAUSE ANYTHING. Every
   finding is a correlation or a plain observation. Never say a
   feature causes, drives, leads to, improves or is responsible for
   an outcome. Say "tends to go with", "is associated with", "videos
   with more X also tended to have less Y". Only a grade of
   Validated would permit causal language, and nothing holds that
   grade.

2. Never promise or imply reach. No "this will go viral", no "this
   will get you more views", in any phrasing including hedged ones.
   You may say what an experiment would have to show.

3. If a measurement is withheld or unavailable, say so plainly. Do
   not substitute a related one without saying you have.

4. Never identify a person, and never speculate about who appears in
   a video.

5. Always give the grade of anything you report - Observed,
   Hypothesised or Associated - and say what it means in one clause.

Write plainly, in short paragraphs, for a concert pianist rather than
a statistician. Prefer "about a third of your videos" to "33.3%" when
precision is not the point, but give the exact figure when it is."""


# ---------------------------------------------------------------------------
# enforcement
# ---------------------------------------------------------------------------

def _numbers(text: str) -> list[str]:
    """Numeric claims in the answer, normalised for comparison.

    Years, small counts written as words, and list markers are not
    claims. The filter is deliberately conservative about what counts
    as a number so the check stays about figures a reader would act
    on.
    """
    out = []
    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)\s*%?", text):
        raw = m.group(1)
        try:
            val = float(raw)
        except ValueError:
            continue
        if 1900 <= val <= 2100 and "." not in raw:
            continue                      # a year
        if val <= 2 and "." not in raw:
            continue                      # "one of", "two reasons"
        out.append(raw)
    return out


def _flatten(obj: Any) -> set[str]:
    """Every number anywhere in the tool results, as text."""
    seen: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            seen.add(f"{x:g}")
            # A negative correlation reads "-0.31" in the tool result
            # and "0.31" in a sentence that already says "negative".
            # Without the unsigned form every rho in the project
            # would be flagged as invented.
            seen.add(f"{abs(x):g}")
            seen.add(f"{abs(float(x)):.2f}")
            seen.add(f"{round(float(x)):g}")
            seen.add(f"{float(x):.1f}")
            seen.add(f"{float(x):.2f}")
            seen.add(f"{float(x) * 100:g}")
            seen.add(f"{round(float(x) * 100):g}")
            return
        if isinstance(x, str):
            for m in re.finditer(r"\d+(?:\.\d+)?", x):
                seen.add(m.group(0))
            return
        if isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
            return
        if isinstance(x, (list, tuple, set)):
            for v in x:
                walk(v)

    walk(obj)
    return seen


def unverified_numbers(text: str, tool_calls: list[ToolCall]) -> list[str]:
    """Figures in the answer that no tool returned.

    The guarantee that does not depend on the model cooperating. A
    model instructed to cite will usually cite; this catches the time
    it does not, and the time it cites something it rounded into
    existence.
    """
    available: set[str] = set()
    for t in tool_calls:
        available |= _flatten(t.result)
    out = []
    for n in _numbers(text):
        if n in available:
            continue
        # Accept a rounded restatement of something returned.
        try:
            v = float(n)
        except ValueError:
            continue
        if any(abs(v - float(a)) < 0.051 for a in available
               if _isnum(a)):
            continue
        out.append(n)
    return sorted(set(out), key=out.index)


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


#: Words that turn a causal verb into a denial of causation. The
#: correct answer in this project says "this is not evidence of
#: cause" constantly, and a matcher that blocked those would leave
#: the assistant unable to state its own limits - which is the one
#: thing it most needs to say.
DENIALS = ("not ", "never ", "no evidence", "cannot", "can't", "isn't",
           "is not", "does not", "doesn't", "rather than", "nothing ",
           "without ", "no one", "would have to")


def _is_denial(low: str, start: int, end: int) -> bool:
    window = low[max(0, start - 70):end + 40]
    return any(d in window for d in DENIALS)


def review(text: str, tool_calls: list[ToolCall]) -> list[Violation]:
    """What the finished answer did wrong, whatever the prompt said.

    Prompts are not a security boundary. Everything the system prompt
    asks for is checked here, after generation, against the text that
    would otherwise reach the owner.
    """
    out: list[Violation] = []
    low = text.lower()

    for pat in CAUSAL_PATTERNS:
        for m in re.finditer(pat, low):
            if _is_denial(low, m.start(), m.end()):
                continue
            out.append(Violation(
                "causal_language",
                "Nothing in this project holds the Validated grade, so no "
                "causal claim is permitted. Only the pre-registered "
                "experiment of M9 could license one.",
                text[max(0, m.start() - 60):m.end() + 60]))
            break
        else:
            continue
        break

    for pat in VIRALITY_PATTERNS:
        m = re.search(pat, low)
        if m:
            out.append(Violation(
                "virality_promise",
                "Reach is never promised or implied, in any phrasing.",
                text[max(0, m.start() - 60):m.end() + 60]))
            break

    for pat in IDENTITY_PATTERNS:
        m = re.search(pat, low)
        if m:
            out.append(Violation(
                "identification",
                "Identifying a person from a frame is out of scope.",
                text[max(0, m.start() - 60):m.end() + 60]))
            break

    for feature in vs.WITHHELD_VISION:
        short = (feature[len("vision_"):] if feature.startswith("vision_")
                 else feature)
        fq = vq.by_name(short)
        if fq is not None and fq.reliability != "unverified":
            continue          # indicative: showable, just never counted
        pretty = lex.display_name(feature).lower()
        if feature in low or (len(pretty) > 8 and pretty in low):
            # Mentioning it is fine if the answer says it is withheld.
            if not re.search(r"withheld|unavailable|not (?:trusted|"
                             r"measured|reliable)|cannot", low):
                out.append(Violation(
                    "withheld_measurement",
                    f"{feature} was measured and failed, so it may only "
                    f"be mentioned as unavailable.",
                    feature))
                break
    return out


def precheck(question: str) -> str | None:
    """A refusal decided before any money is spent.

    Three question shapes get the same answer every time and do not
    need a model to produce it. Refusing here also means the trap
    questions in the acceptance test cost nothing to run.
    """
    low = (question or "").lower()
    if any(re.search(p, low) for p in VIRALITY_PATTERNS):
        return ("I can't answer that. Nothing in this project predicts "
                "reach, and anything that sounded like a prediction "
                "would be invented. What I can tell you is which of "
                "your openings differ from your usual ones, and what an "
                "experiment would have to show before anyone could "
                "claim a difference mattered.")
    if any(re.search(p, low) for p in IDENTITY_PATTERNS):
        return ("I can't identify people in your videos - it's out of "
                "scope for this project, and the model I'd be asking "
                "refuses it as well. I can tell you whether a face is "
                "visible, which is a different question.")
    if re.search(r"\bwhy did .{0,40}\b(succeed|do well|work|perform)",
                 low) or re.search(r"\bwhy (?:was|is) .{0,30}successful",
                                   low):
        return ("I can't tell you why a video did well. Nothing here "
                "has established that anything causes anything - every "
                "finding is a correlation measured across your "
                "publications, and a single video has no counterfactual "
                "to compare against. I can show you how that video "
                "differs from your usual ones, which is a description "
                "and not an explanation.")
    return None


def withheld_question(question: str) -> str | None:
    """Asking about a measurement that was measured and FAILED.

    Narrower than it first was, and the first live run is why. It
    blocked "is sheet music visible in publication 287?" - a
    description question about one video, answerable from data that
    exists, about a field graded `indicative`, meaning shown as a
    reading and never counted. Refusing that was the enforcement
    being wrong rather than strict, and an assistant that refuses
    answerable questions teaches the owner to stop asking.

    So the gate is `unverified` only: a field nobody may even see.
    An `indicative` field is answerable and the per-publication tool
    returns it. What neither may do is support a statistic, and that
    is enforced where statistics are made.
    """
    low = (question or "").lower()
    for feature in vs.WITHHELD_VISION:
        short = feature[len("vision_"):] if feature.startswith("vision_") \
            else feature
        fq = vq.by_name(short)
        if fq is not None and fq.reliability != "unverified":
            continue
        words = [w for w in short.split("_") if len(w) > 3]
        if words and all(w in low for w in words):
            q = vq.by_name(short)
            why = (q.basis if q else "") or vs.WITHHELD_VISION[feature]
            return (f"That measurement is withheld, so I can't answer "
                    f"from it.\n\n{why}\n\nIt is still collected and you "
                    f"can see it on a video's own page, but nothing is "
                    f"allowed to build an answer on it.")
    return None


# ---------------------------------------------------------------------------
# the conversation
# ---------------------------------------------------------------------------

#: A runaway tool loop is the one way a chat window spends without
#: anyone deciding to. Each round trip is reserved separately, so the
#: budget stops it too, but a hard count is the cheaper guard.
MAX_TOOL_ROUNDS = 6


def _tool_result_blocks(calls: list[ToolCall]) -> list[dict]:
    return [{"type": "tool_result", "tool_use_id": c.arguments.pop("_id"),
             "content": json.dumps(c.error and {"error": c.error}
                                   or c.result, default=str)[:12000]}
            for c in calls]


def context_block(session: Session) -> str:
    """The ids the tools need, which the model has no way to guess.

    BLUEPRINT M8 describes "an evidence context assembled by
    ViralLab", and this is the smallest useful one. Two of ten
    questions in the first live run called no tool at all and asked
    the owner for a collection id, because nothing had told it that
    collections have ids, or what the measurements are called.

    **Names and ids only.** No measurements, no findings, no counts -
    those come from the tools, so that every figure in an answer has
    a tool result behind it and the number check stays meaningful.
    """
    from server.db.models import CreatorCollection

    own = session.execute(
        select(Creator.id, Creator.handle, Creator.platform)
        .where(Creator.is_own_account.is_(True))).all()
    colls = session.execute(
        select(CreatorCollection.id, CreatorCollection.name)).all()
    features = sorted({e.spec.name for e in cat.build(session)})
    lines = ["These are the ids and names the tools take. Nothing else "
             "about them is given here - look anything up with a tool.",
             ""]
    if own:
        lines.append("The owner's own accounts (creator_id, handle, "
                     "platform):")
        lines += [f"  {i}  {h}  {p}" for i, h, p in own]
    if colls:
        lines.append("Saved collections (collection_id, name):")
        lines += [f"  {i}  {n}" for i, n in colls]
    lines += ["", "Measurement names, which use underscores: "
              + ", ".join(features)]
    return "\n".join(lines)


def ask(session: Session, question: str, *, provider,
        reserve: Callable[[float, str], Callable[[str, float], None] | None],
        max_rounds: int = MAX_TOOL_ROUNDS) -> Answer:
    """One question, answered only from the tools, then checked.

    `reserve` is injected rather than imported so the caller owns the
    transaction and the test suite can run the whole loop without a
    ledger. It takes an estimate and an idempotency key and returns a
    settle function, or None if the budget refuses - in which case the
    conversation stops rather than continuing unledgered.
    """
    refusal = precheck(question) or withheld_question(question)
    if refusal:
        # Decided without a model, so the trap questions cost nothing.
        return Answer(text="", blocked=True, refusal=refusal)

    try:
        preamble = context_block(session) if session is not None else ""
    except Exception:                      # noqa: BLE001 - not fatal
        logger.exception("context block unavailable; asking without it")
        preamble = ""
    messages: list[dict] = [{"role": "user", "content": (
        f"{preamble}\n\n---\n\n{question}" if preamble else question)}]
    calls: list[ToolCall] = []
    text = ""

    for _round in range(max_rounds):
        body = {
            "model": provider.MODEL,
            "max_tokens": 1500,
            "system": SYSTEM,
            "tools": TOOL_SCHEMAS,
            "messages": messages,
        }
        est = round(8000 / 1e6 * provider.USD_PER_MTOK_IN
                    + 1500 / 1e6 * provider.USD_PER_MTOK_OUT, 6)
        settle = reserve(est, f"strategist:{abs(hash(question)) % 10**12}:"
                              f"{_round}")
        if settle is None:
            return Answer(text="", tool_calls=calls, blocked=True,
                          refusal=("The strategy budget would not cover "
                                   "this answer, so nothing was asked. "
                                   "Raise the `strategy` cap to continue."))
        headers = {"x-api-key": _key(provider),
                   "anthropic-version": provider.API_VERSION,
                   "content-type": "application/json"}
        try:
            data = provider._request_with_retries(
                provider.URL, headers, body, 120.0)
        except Exception as exc:               # noqa: BLE001 - reported
            settle("failed", 0.0)
            logger.exception("strategist call failed")
            return Answer(text="", tool_calls=calls, blocked=True,
                          refusal=f"The model could not be reached: {exc}")

        usage = data.get("usage") or {}
        actual = round(
            (usage.get("input_tokens") or 0) / 1e6 * provider.USD_PER_MTOK_IN
            + (usage.get("output_tokens") or 0) / 1e6
            * provider.USD_PER_MTOK_OUT, 6)
        settle("ok", actual)

        blocks = data.get("content") or []
        text = "".join(b.get("text") or "" for b in blocks
                       if b.get("type") == "text")
        wants = [b for b in blocks if b.get("type") == "tool_use"]
        if not wants:
            break

        messages.append({"role": "assistant", "content": blocks})
        round_calls: list[ToolCall] = []
        for b in wants:
            name = b.get("name") or ""
            args = dict(b.get("input") or {})
            call = ToolCall(name=name, arguments={**args, "_id": b.get("id")})
            fn = TOOLS.get(name)
            if fn is None:
                call.error = f"no tool named {name!r}"
            else:
                try:
                    call.result = jsonable(fn(session, **args))
                except Exception as exc:       # noqa: BLE001 - reported
                    call.error = str(exc)[:300]
                    logger.warning("tool %s failed: %s", name, exc)
            round_calls.append(call)
        messages.append({"role": "user",
                         "content": _tool_result_blocks(round_calls)})
        calls += round_calls

    violations = review(text, calls)
    unverified = unverified_numbers(text, calls)
    blocked = any(v.kind in ("causal_language", "virality_promise",
                             "identification", "withheld_measurement")
                  for v in violations)
    refusal_text = None
    if blocked:
        kinds = ", ".join(sorted({v.kind for v in violations}))
        refusal_text = (
            "I drafted an answer and then refused it, because it broke a "
            f"rule this project does not bend ({kinds}). That is a check "
            "on my own output rather than on your question, and the "
            "draft is logged so the failure can be looked at. Ask me "
            "again and I will try to answer it without the claim that "
            "tripped it.")
    return Answer(text=text, tool_calls=calls, violations=violations,
                  unverified_numbers=unverified, blocked=blocked,
                  refusal=refusal_text)


def _key(provider) -> str:
    from server.ai.base import credential
    return credential(provider.env_var) or ""
