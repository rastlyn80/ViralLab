"""What the platform can run for itself, and nothing else.

The owner's objection: "the platform should be the place of
research, not coding sessions." The analysis already lived in the
product; what fed it did not. Every refresh needed somebody with a
terminal, which made him dependent on me for the thing he had asked
to own.

## Why a frozen list rather than a command

An endpoint that runs a command supplied by its caller is a remote
shell, whatever else it is called. So the API accepts a **key from
this module** and a handful of **numbers with bounds**, and builds
the command line here. There is no path from a request to an
argument this file did not write.

## What each entry has to say for itself

Written for the owner, not for an operator:

* `title` and `what_it_does` in plain English, no column names;
* `costs_money` and `cost_note`, because a button that silently
  spends is the worst button in the product;
* `how_long`, because "run this" and "run this for four hours" are
  different decisions;
* `needs` - what has to be true first, so a failure is predictable
  rather than a surprise.

Params are declared with bounds and the API refuses anything else.
A number from a browser is not trusted because it came from our own
page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Where the scripts live on the server. Operations run from the
#: repository root so relative paths behave as they do by hand.
ROOT = "/home/rastlyn/virallab"
PYTHON = f"{ROOT}/.venv/bin/python"


@dataclass(frozen=True)
class Param:
    """One number the owner may choose, with the bounds it must obey."""

    key: str
    label: str
    default: int
    minimum: int
    maximum: int
    help: str = ""

    def clean(self, value) -> int:
        """The value to use. Out-of-range is clamped, not refused.

        Clamping rather than erroring because every bound here is a
        politeness or spending limit, and the right response to
        "collect 100000" is to collect the maximum and say so, not
        to show the owner a validation error about a field he had no
        reason to think was bounded.
        """
        try:
            n = int(value)
        except (TypeError, ValueError):
            return self.default
        return max(self.minimum, min(self.maximum, n))


@dataclass(frozen=True)
class Operation:
    key: str
    title: str
    what_it_does: str
    #: `argv` built from the params. Returns a list, never a string:
    #: nothing is passed through a shell, so nothing can be injected
    #: into one.
    build: object
    how_long: str
    costs_money: bool = False
    cost_note: str = ""
    needs: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    #: Shown after it finishes, pointing at where the result shows up.
    look_at: str = ""


def _script(name: str) -> str:
    return f"{ROOT}/scripts/provision/{name}"


CATALOGUE: tuple[Operation, ...] = (
    Operation(
        key="collect_own",
        title="Update my own videos",
        what_it_does=(
            "Fetches the latest view counts and any new posts from "
            "your own TikTok and Instagram accounts."),
        how_long="a few minutes",
        needs=("Your accounts stay connected on the Connections page.",),
        look_at="My videos",
        build=lambda p: [PYTHON, _script("backfill_surface_metrics.py"),
                         "--yes"],
    ),
    Operation(
        key="find_creators",
        title="Find more creators like you",
        what_it_does=(
            "Searches for classical piano creators we are not "
            "already following, and adds them to the list we study."),
        how_long="about a minute per search",
        costs_money=True,
        cost_note="Roughly $0.03 a search, from the discovery budget.",
        needs=("A discovery budget with room left.",),
        look_at="Creators we study",
        params=(
            Param("limit", "How many creators to look for", 20, 5, 100),
        ),
        build=lambda p: [PYTHON, _script("discover_niche.py"),
                         "--limit", str(p["limit"]), "--yes"],
    ),
    Operation(
        key="deepen_creators",
        title="Collect more of each creator's back catalogue",
        what_it_does=(
            "A creator with one video tells us nothing - we need "
            "enough of their posts to see which ones did unusually "
            "well for them. This fills in their recent catalogue."),
        how_long="around a minute per creator",
        costs_money=True,
        cost_note=(
            "Measured at about $0.05 a creator for 30 posts, from "
            "the discovery budget."),
        look_at="What goes with more views",
        params=(
            Param("creators", "How many creators", 25, 5, 100),
            Param("per_creator", "Posts each", 30, 10, 50),
            Param("max_usd", "Stop after spending (dollars)", 5, 1, 20),
        ),
        build=lambda p: [PYTHON, _script("build_cohort.py"),
                         "--creators", str(p["creators"]),
                         "--per-creator", str(p["per_creator"]),
                         "--max-usd", str(p["max_usd"]), "--yes"],
    ),
    Operation(
        key="read_captions",
        title="Read every caption and posting time",
        what_it_does=(
            "Works out the hashtags, emoji, caption shape, composer, "
            "piece, posting hour and posting rhythm for every "
            "publication we hold - yours and theirs."),
        how_long="under a minute",
        look_at="Everything we can measure",
        build=lambda p: [PYTHON, _script("extract_publication_meta.py"),
                         "--yes"],
    ),
    Operation(
        key="read_repertoire",
        title="Work out what is being played, and how often",
        what_it_does=(
            "Composer, period, how recognisable the piece is, whether "
            "it is your own composition, and how it fits the run of "
            "posts around it."),
        how_long="under a minute",
        look_at="Everything we can measure",
        build=lambda p: [PYTHON, _script("extract_context.py"), "--yes"],
    ),
    Operation(
        key="read_hooks",
        title="Classify the opening text",
        what_it_does=(
            "Looks at the words on screen and says what kind of "
            "opening it is - a question, a story, a POV, a plain "
            "label - for anything we have read text from."),
        how_long="seconds",
        look_at="How my openings look",
        build=lambda p: [PYTHON, _script("extract_hooktext.py")],
    ),
    Operation(
        key="watch_competitors",
        title="Watch other creators' videos",
        what_it_does=(
            "Downloads other creators' public videos one at a time, "
            "measures the on-screen text, background and sound, then "
            "deletes the file. Nothing is kept."),
        how_long="about twenty minutes per 40 videos",
        look_at="What goes with more views",
        params=(
            Param("cycles", "Rounds", 5, 1, 20),
            Param("batch", "Videos per round", 40, 5, 60),
        ),
        build=lambda p: ["bash", _script("competitor_visual_pass.sh"),
                         str(p["cycles"]), str(p["batch"]), "0"],
    ),
    Operation(
        key="watch_competitors_closely",
        title="Watch other creators' videos, and look closely",
        what_it_does=(
            "The same, plus asking a model 31 questions about each "
            "one - camera angle, framing, lighting, who is in shot, "
            "whether there is sheet music. This is the only way to "
            "compare how they shoot with how you shoot."),
        how_long="about twenty-five minutes per 40 videos",
        costs_money=True,
        cost_note=(
            "Measured at $0.008 a video. 40 videos is about 32 "
            "cents. Comes out of the vision budget."),
        look_at="What goes with more views",
        params=(
            Param("cycles", "Rounds", 5, 1, 20),
            Param("batch", "Videos per round", 40, 5, 60),
            Param("usd_per_round", "Stop each round after (cents)",
                  40, 5, 100),
        ),
        build=lambda p: ["bash", _script("competitor_visual_pass.sh"),
                         str(p["cycles"]), str(p["batch"]),
                         f"{p['usd_per_round'] / 100:.2f}"],
    ),
    Operation(
        key="look_at_my_videos",
        title="Look closely at my own videos",
        what_it_does=(
            "Asks the same 31 questions about your own publications, "
            "so there is something to compare other creators with."),
        how_long="about seven seconds a video",
        costs_money=True,
        cost_note="Measured at $0.008 a video, from the vision budget.",
        look_at="What my videos look like",
        params=(
            Param("limit", "How many videos", 50, 5, 500),
            Param("max_usd", "Stop after spending (dollars)", 2, 1, 10),
        ),
        build=lambda p: [PYTHON, _script("describe_library.py"),
                         "--limit", str(p["limit"]),
                         "--max-usd", str(p["max_usd"]), "--yes"],
    ),
)

BY_KEY = {op.key: op for op in CATALOGUE}


def get(key: str) -> Operation:
    """The operation with this key, or a refusal.

    `KeyError` rather than a lookup returning None, so a caller
    cannot accidentally proceed with nothing.
    """
    if key not in BY_KEY:
        raise KeyError(f"no operation called {key!r}")
    return BY_KEY[key]


def clean_params(key: str, raw: dict | None) -> dict:
    """The parameters this operation will actually run with.

    Only declared keys survive, each clamped to its bounds. A value
    the catalogue does not declare is dropped rather than passed
    on, so an unexpected field in a request cannot become an
    argument.
    """
    op = get(key)
    raw = raw or {}
    return {p.key: p.clean(raw.get(p.key, p.default)) for p in op.params}


def argv(key: str, params: dict) -> list[str]:
    """The command line, built here and nowhere else."""
    return list(get(key).build(clean_params(key, params)))


def as_dict(op: Operation) -> dict:
    return {
        "key": op.key, "title": op.title,
        "what_it_does": op.what_it_does,
        "how_long": op.how_long,
        "costs_money": op.costs_money,
        "cost_note": op.cost_note,
        "needs": list(op.needs),
        "look_at": op.look_at,
        "params": [{"key": p.key, "label": p.label,
                    "default": p.default, "minimum": p.minimum,
                    "maximum": p.maximum, "help": p.help}
                   for p in op.params],
    }
