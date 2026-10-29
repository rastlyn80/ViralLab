"""The guards that stop a small p-value on nothing becoming advice.

Two of them, both added after the analysis produced a number that
read as a finding and was not.

`TIME_PROXIES` exists because `publications_before_this` cleared
correction on both sides - positive on the owner's account, negative
on competitors'. A property of a video does not reverse; a count of
elapsed time does, depending on whether the account was growing.

`MIN_BOOLEAN_CASES` exists because `hook_is_what_if` and
`hook_names_celebrity` are each true on 3 publications out of 442,
and both came out at p = 0.011 against breaking out. "6% of
breakthroughs used a what-if hook" is two videos. A rank test cannot
tell that two is not a sample.
"""

from __future__ import annotations

import inspect

from server.research import virality as vir


def test_a_count_of_elapsed_time_is_not_a_content_finding():
    assert "publications_before_this" in vir.TIME_PROXIES
    assert "is_first_post" in vir.TIME_PROXIES
    src = inspect.getsource(vir._build)
    assert "account_trends" in src, (
        "time proxies must still be reported, just not as findings")


def test_time_proxies_are_excluded_from_breakthrough_differences():
    src = inspect.getsource(vir._breakthroughs)
    assert "TIME_PROXIES" in src


def test_a_rare_boolean_cannot_produce_a_difference():
    """The floor is on the rarer answer, not on the row count: a
    feature measured on 442 publications and true on 3 of them has
    442 rows and 3 cases.

    Checked by running the rule rather than by reading the source.
    The previous version of this test asserted that the string
    "min(trues, falses)" appeared in `breakthroughs`, and it passed
    for months while that line was **unreachable** - a guard above
    it dropped every boolean before it could be applied. A test that
    reads source proves the code is present; only running it proves
    the code runs.
    """
    assert vir.MIN_BOOLEAN_CASES >= 10
    rare_a = [1.0] * 2 + [0.0] * 30
    rare_b = [1.0] * 1 + [0.0] * 200
    assert vir.why_not_testable(rare_a, rare_b) != ""


def test_the_outlier_side_needs_cases_too():
    """A feature true on 40 publications overall but on almost none
    of the outliers still has nothing to say about outliers."""
    a = [1.0] * 2 + [0.0] * 40
    b = [1.0] * 40 + [0.0] * 40
    assert "breakthroughs" in vir.why_not_testable(a, b)


def test_a_healthy_boolean_is_not_caught_by_the_rare_one_guard():
    """The other half, which nothing checked and which was broken.

    Guarding against a rare boolean must not become guarding
    against every boolean. It did, and the cost was 43 of 103
    measurements never being tested at all.
    """
    a = [1.0] * 10 + [0.0] * 10
    b = [1.0] * 30 + [0.0] * 30
    assert vir.why_not_testable(a, b) == ""


def test_the_guards_are_documented_where_they_bite():
    """A numeric floor with no reason beside it gets raised by the
    next person who finds it inconvenient."""
    src = inspect.getsource(vir)
    assert "3 publications EACH out of 442" in src
    assert "two videos" in src


def test_the_gap_to_the_previous_post_is_not_a_content_finding():
    """A long gap means an older window to beat, not a better video.

    Breaking out means beating a trailing window of earlier posts.
    `days_since_previous_post` IS the age gap to the newest member
    of that window, so the longer it is, the staler the thing being
    beaten and the more the account has grown since. On the deep
    competitor corpus both gap measurements came out as the LARGEST
    differences - 20.9 days against 11.1 - and the advice they look
    like they give, post less often, is exactly what an artefact of
    the outlier definition would look like.
    """
    assert "days_since_previous_post" in vir.BASELINE_DISTANCE
    assert "gap_vs_usual" in vir.BASELINE_DISTANCE


def test_a_creators_habitual_cadence_is_not_excluded():
    """Only the distance to the window is confounded, not cadence.

    How often a creator posts in general is a property of the
    creator, not of the gap in front of one publication, and
    excluding it would throw away a real and actionable measurement.
    """
    assert "usual_gap_days" not in vir.BASELINE_DISTANCE


def test_the_confounded_measurements_are_still_reported():
    """Hiding a measurement makes the next person re-derive it."""
    src = inspect.getsource(vir._breakthroughs)
    assert "cadence_against_the_baseline" in src
    assert "cadence_warning" in src


# ---------------------------------------------------------------------------
# a question the project does not trust cannot become a finding
# ---------------------------------------------------------------------------

def test_only_verified_visual_questions_may_support_a_claim():
    """Phase 1 graded all 42 visual questions; nothing read the grade.

    `vision_questions.may_enter_statistics` allows the 9 graded
    `verified`. The analysis never consulted it, so an `indicative`
    answer could become a finding - and one had. "Sheet music
    visible" was reported as a breakthrough difference at
    q = 0.024, carried into the adherence check, and handed to the
    generator, on a question the project had already decided was
    not reliable enough to support a claim.
    """
    assert vir.may_be_a_finding("vision_face_visible"), (
        "face_visible is verified and must still count")
    for indicative in ("vision_sheet_music_visible", "vision_camera_angle",
                       "vision_framing", "vision_formality",
                       "vision_lighting"):
        assert not vir.may_be_a_finding(indicative), indicative


def test_a_deterministic_measurement_is_not_graded_by_this_gate():
    """Arithmetic on pixels is not a model answer.

    Whether an edge-density figure is MEANINGFUL is a separate
    argument from whether it is ACCURATE, and this gate is about
    the second one.
    """
    for deterministic in ("edge_density", "hashtag_count",
                          "ocr_word_count", "composer_named",
                          "posted_hour_utc"):
        assert vir.measurement_grade(deterministic) == "deterministic"
        assert vir.may_be_a_finding(deterministic)


def test_the_grade_matches_what_vision_questions_says():
    """One source of truth, not two that can drift."""
    from server.ai import vision_questions as vq

    for q in vq.ALL:
        feature = f"vision_{q.name}"
        assert vir.may_be_a_finding(feature) is \
            vq.may_enter_statistics(q.name), q.name


def test_a_held_back_measurement_is_still_reported():
    """Hiding it would make the next person measure it again.

    And the reason has to be the right one: these are held back
    because the QUESTION is unproven, not because the RESULT was
    weak, and those two deserve different responses from the owner.
    """
    src = inspect.getsource(vir._breakthroughs)
    assert "not_verified_enough" in src
    assert "not_verified_warning" in src
    build_src = inspect.getsource(vir._build)
    assert "not_verified_enough" in build_src
