"""The adherence check, and the ways it could flatter the owner.

A page that tells a creator he is doing everything right is the
easiest page in this project to write and the least useful. These
tests pin the three places it would happen:

* a finding every recent publication already clears being reported
  as a pass rather than as spent;
* a skewed average contradicting the count it sits next to;
* an unmeasured publication being counted as a failure.

The arithmetic helpers are tested directly rather than through the
database, because what is being checked is the judgement, not the
query.
"""

from __future__ import annotations

from server.research import adherence as adh


# ---------------------------------------------------------------------------
# the summary must not disagree with the count beside it
# ---------------------------------------------------------------------------

def test_the_typical_value_is_the_median_not_the_mean():
    """A long tail must not drag the summary across the line.

    The live case: colour intensity reported an average of 0.326
    beside "7 of 9 below 0.300", which reads as a contradiction
    whether or not it is one.
    """
    skewed = sorted([0.20, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26, 0.90, 0.95])
    typical = adh._median(skewed)
    assert typical == 0.24
    below = sum(1 for v in skewed if v <= 0.30)
    # The median and the count agree, which is the whole point.
    assert (typical <= 0.30) is (below > len(skewed) / 2)


def test_the_median_handles_an_even_count_and_an_empty_one():
    assert adh._median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert adh._median([7.0]) == 7.0
    assert adh._median([]) is None


# ---------------------------------------------------------------------------
# a finding nothing falls short of is spent, not passed
# ---------------------------------------------------------------------------

def test_a_finding_everything_clears_no_longer_separates():
    """Three of this account's four findings are in this state."""
    # Breakouts averaged 0.167, everything else 0.137; recent work
    # sits at 0.22-0.24, past both.
    recent = sorted([0.21, 0.22, 0.23, 0.24, 0.25])
    assert adh._separates(recent, 0.1671, 0.1369, True) is False


def test_a_finding_recent_work_straddles_still_separates():
    recent = sorted([0.10, 0.12, 0.16, 0.19, 0.25])
    assert adh._separates(recent, 0.1671, 0.1369, True) is True


def test_a_finding_everything_fails_still_separates():
    """Spent means "no longer a question", not "unanimous"."""
    recent = sorted([0.01, 0.02, 0.03])
    assert adh._separates(recent, 0.1671, 0.1369, True) is True


def test_separation_works_in_the_lower_direction_too():
    # Breakouts had LESS of this: 0.269 against 0.332.
    way_below = sorted([0.05, 0.06, 0.07])
    assert adh._separates(way_below, 0.2692, 0.3317, False) is False
    straddling = sorted([0.22, 0.29, 0.31, 0.36])
    assert adh._separates(straddling, 0.2692, 0.3317, False) is True


def test_nothing_measured_does_not_separate():
    assert adh._separates([], 0.2, 0.1, True) is False


# ---------------------------------------------------------------------------
# the headline
# ---------------------------------------------------------------------------

def _f(name, verdict="following", separates=True):
    return {"display_name": name, "verdict": verdict,
            "separates": separates}


def test_the_headline_leads_with_what_is_being_ignored():
    findings = [_f("Colour intensity", "not following"),
                _f("Overall visual detail")]
    out = adh._headline(findings, [findings[0]], [], 12)
    assert "go against" in out
    assert "colour intensity" in out


def test_the_headline_refuses_to_call_a_spent_finding_a_pass():
    """The live result, and the one most tempting to misreport.

    Three of four findings cleared by everything is not "broadly in
    line" - it means the findings have stopped being the question.
    """
    findings = [_f("A"), _f("B", separates=False),
                _f("C", separates=False), _f("D", separates=False)]
    settled = [f for f in findings if not f["separates"]]
    out = adh._headline(findings, [], settled, 12)
    assert "no longer separate" in out
    assert "broadly in line" not in out
    # The one that still discriminates is named as the live question.
    assert "1 still tells them apart" in out


def test_everything_spent_says_so_without_claiming_success():
    findings = [_f("A", separates=False), _f("B", separates=False)]
    out = adh._headline(findings, [], findings, 12)
    assert "not a pass mark" in out
    assert "has not been measured yet" in out


def test_nothing_to_check_says_nothing_to_check():
    out = adh._headline([], [], [], 12)
    assert "Nothing to check yet" in out


# ---------------------------------------------------------------------------
# the verdict thresholds
# ---------------------------------------------------------------------------

def test_too_few_measured_withholds_a_verdict():
    """Three videos agreeing is not a habit."""
    assert adh._verdict(3, 3) == "not enough measured"
    assert adh._verdict(0, adh.MIN_MEASURED - 1) == "not enough measured"


def test_the_verdicts_land_where_the_thresholds_say():
    assert adh._verdict(9, 9) == "following"
    assert adh._verdict(7, 10) == "following"
    assert adh._verdict(5, 10) == "mixed"
    assert adh._verdict(3, 10) == "not following"
    assert adh._verdict(0, 9) == "not following"


def test_plain_english_marks_a_spent_finding_rather_than_praising_it():
    d = {"feature": "colourfulness", "direction": "higher"}
    spent = adh._plain(d, 9, 9, 12, separates=False)
    assert "no longer tells them apart" in spent
    live = adh._plain(d, 7, 9, 12, separates=True)
    assert "7 of your last 9" in live


def test_plain_english_says_unmeasured_rather_than_failing():
    d = {"feature": "colourfulness", "direction": "higher"}
    out = adh._plain(d, 0, 0, 12, separates=False)
    assert "None of your last 12 have been measured" in out
