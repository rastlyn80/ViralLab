"""The manual cannot quietly fall behind the product.

The owner, after one explanation too many: *"what do you think I
can understand about this? I'm back to square one."* He has said a
version of that four times. The product had grown to two dozen
pages with no entry point, so finding the right one meant opening
three and guessing.

A guide written as prose solves that for a week. These tests are
what make it keep working: **a page with no entry fails the build,
and an entry pointing at no page fails the build.** Adding a
feature without explaining it is not possible without deleting a
test, which is a decision somebody has to make on purpose.

The jargon rules are here too. This is the page where plain
language matters most, because it is the one he opens when he is
already lost.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WEBUI = Path(__file__).resolve().parents[2] / "webui"
GUIDE = (WEBUI / "guide.js").read_text(encoding="utf-8")
APP = (WEBUI / "app.js").read_text(encoding="utf-8")

#: Routes that take an id, which the guide links to by their list
#: page instead. "#/video/41" is not a page you navigate to, it is
#: a thing you click through to.
TAKES_AN_ID = re.compile(r"\(\\d\+\)")

#: Pages that exist and are deliberately not in the guide, each
#: with the reason. An empty reason is not allowed: this is the
#: escape hatch, and an escape hatch nobody has to justify becomes
#: the place every page ends up.
NOT_IN_THE_GUIDE = {
    "/guide": "it is the guide",
    "/?": "the guide lists it as '#/'",
}


def routes() -> set[str]:
    """Every route the app can show, from the router itself."""
    found = set()
    for raw in re.findall(r"pattern:\s*/\^(.*?)\$/", APP):
        if TAKES_AN_ID.search(raw):
            continue
        found.add(raw.replace("\\/", "/"))
    return found


def guide_paths() -> set[str]:
    """Every page the guide links to, without its tab."""
    hrefs = re.findall(r'href: [\'"]#([^\'"]+)[\'"]', GUIDE)
    assert hrefs, "no links found in the guide; the pattern has drifted"
    return {h.split("?")[0].rstrip("/") or "/" for h in hrefs}


def test_every_page_in_the_product_is_explained():
    """The test that makes this a manual rather than a document.

    A page nobody wrote an entry for is a page he will never
    find, and the four-tabs redesign already showed what happens
    then: the chat he had asked for existed for weeks under
    "Everything else", which is the same as not existing.
    """
    missing = []
    for route in sorted(routes()):
        key = route.rstrip("/") or "/"
        if route in NOT_IN_THE_GUIDE or key in NOT_IN_THE_GUIDE:
            continue
        if key not in guide_paths() and key != "/":
            missing.append(route)
    assert not missing, (
        f"these pages exist and the guide does not mention them: "
        f"{missing}. Add an entry in webui/guide.js, or add it to "
        f"NOT_IN_THE_GUIDE with the reason.")


def test_every_entry_in_the_guide_points_at_a_real_page():
    """A manual that sends him to a blank screen is worse than
    none, because the first one teaches him not to trust it."""
    real = {r.rstrip("/") or "/" for r in routes()}
    real.add("/")
    dangling = sorted(p for p in guide_paths() if p not in real)
    assert not dangling, (
        f"the guide links to pages that do not exist: {dangling}")


def test_the_escape_hatch_has_to_give_a_reason():
    for path, why in NOT_IN_THE_GUIDE.items():
        assert why and len(why) > 5, path


def test_the_guide_is_reachable_without_knowing_where_it_is():
    """In the sidebar, not folded under "Everything else".

    The four-item sidebar rule was about findings pages whose
    names differed only by their verb. A guide is the opposite
    problem: it is what you open when you do not know what you
    are looking for, so hiding it defeats it.
    """
    assert "href: '#/guide'" in APP
    nav = APP[APP.index("const NAV = ["):APP.index("const MORE = [")]
    assert "'#/guide'" in nav


# ---------------------------------------------------------------------------
# the words
# ---------------------------------------------------------------------------

#: Words that mean something precise to a statistician and nothing
#: to a pianist. The guide is the last page that should use them.
JARGON = (
    "spearman", "mann-whitney", "p-value", "p value", "q-value",
    "q value", "percentile", "robust z", "z-score", "correlation",
    "regression", "benjamini", "covariate", "confound", "idempotent",
    "api", "endpoint", "schema", "json", "sql", "query", "upsert",
    "baseline", "partial", "fdr", "null hypothesis", "significance",
)


@pytest.mark.parametrize("word", JARGON)
def test_the_guide_uses_none_of_the_words_he_has_asked_me_to_drop(word):
    """He has objected to this four times, most recently about a
    paragraph explaining rank correlation on a circular variable.
    The guide is where that failure would cost the most."""
    body = GUIDE.lower()
    # Only the part he reads. The module's own comments may say
    # why a thing is done, in whatever words make that clear.
    visible = "\n".join(
        line for line in body.splitlines()
        if not line.strip().startswith(("*", "//", "/*", "#:")))
    assert word not in visible, (
        f"the guide says {word!r}, which is the kind of word that "
        f"sent him back to square one")


def test_the_guide_says_nothing_causes_anything():
    """Rule one of the project, and the guide is where someone
    reads the rules."""
    assert "causes" in GUIDE
    # The sentence wraps in the source, so match the halves.
    assert "tended to do" in GUIDE and "better" in GUIDE
    assert "experiment" in GUIDE


def test_the_guide_says_money_is_never_spent_without_a_press():
    assert "costs" in GUIDE.lower()
    assert "ceiling" in GUIDE.lower() or "cap" in GUIDE.lower()
