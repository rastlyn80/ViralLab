"""A link to the post, or an honest absence. Never a guess.

The rule this file enforces is narrow and matters more than it looks:
**a URL that 404s is worse than no URL at all.** A reader who clicks a
dead link concludes the post was deleted; a reader who sees "no link,
and here is why" learns something true about our data.

Two real cases drive it. The owner's TikTok creator row carries the
API's `open_id` rather than a username, so a derived URL would fail
for all 235 of their TikTok publications. And Instagram permalinks
carry a shortcode that cannot be computed from the media id in either
direction, so one that was never collected cannot be reconstructed.
"""

from __future__ import annotations

import pytest

from server.collectors import permalink as pl
from server.db.models import Creator, Video


def _creator(handle, platform="tiktok", own=False):
    return Creator(platform=platform, handle=handle,
                   is_own_account=own, source="own_api")


def _video(platform="tiktok", external_id="123", permalink=None):
    v = Video(platform=platform, external_id=external_id, source="own_api",
              creator_id=1)
    v.permalink = permalink
    return v


def test_a_tiktok_url_is_derived_from_handle_and_id():
    url = pl.for_video(_video(), _creator("tranquilstreamsmusic"))
    assert url == ("https://www.tiktok.com/@tranquilstreamsmusic"
                   "/video/123")


def test_an_open_id_handle_produces_no_link_and_says_why():
    """The measured case. The owner's TikTok row holds
    `-000lRCwi-AFr6BTkIsmkCkFPfgeI8Izqw5p`, which is the API's
    `open_id`; a URL built from it 404s on every one of their videos.
    """
    c = _creator("-000lRCwi-AFr6BTkIsmkCkFPfgeI8Izqw5p", own=True)
    v = _video()
    assert pl.for_video(v, c) is None
    why = pl.why_missing(v, c)
    assert "internal account id" in why
    assert "would not resolve" in why


@pytest.mark.parametrize("handle", [
    "a",                                   # too short
    "x" * 25,                              # too long
    "has-a-hyphen",                        # hyphens are not valid
    "has space",
    "emoji\U0001f600",
])
def test_an_implausible_handle_never_becomes_a_url(handle):
    assert pl.for_video(_video(), _creator(handle)) is None


@pytest.mark.parametrize("handle", ["pianza88", "keys.falling",
                                    "music_with_hazem", "ab"])
def test_a_plausible_handle_does(handle):
    assert pl.for_video(_video(), _creator(handle))


def test_instagram_is_never_derived():
    """There is no public mapping from a media id to a shortcode, so a
    computed Instagram URL would be a guess that fails."""
    v = _video(platform="instagram", external_id="17900000000000000")
    assert pl.for_video(v, _creator("gabriele_baldocci", "instagram",
                                    own=True)) is None


def test_a_stored_permalink_is_used_as_given():
    v = _video(platform="instagram",
               permalink="https://www.instagram.com/reel/DcnrY7QjZku/")
    assert pl.for_video(v, _creator("gabriele_baldocci", "instagram")) == (
        "https://www.instagram.com/reel/DcnrY7QjZku/")


def test_a_stored_permalink_beats_a_derivable_one():
    """What the platform said wins over our reconstruction of it."""
    v = _video(permalink="https://www.tiktok.com/@renamed/video/999")
    assert pl.for_video(v, _creator("oldhandle")) == (
        "https://www.tiktok.com/@renamed/video/999")


def test_a_missing_instagram_link_explains_how_to_get_one():
    v = _video(platform="instagram")
    why = pl.why_missing(v, _creator("someone", "instagram"))
    assert "shortcode" in why
    assert "collector" in why


def test_no_external_id_means_no_link():
    v = _video(external_id="")
    assert pl.for_video(v, _creator("goodhandle")) is None
    assert pl.why_missing(v, _creator("goodhandle")) == pl.NO_EXTERNAL_ID


def test_a_creator_we_never_resolved_is_handled_not_crashed():
    assert pl.for_video(_video(), None) is None


def test_an_unknown_platform_is_refused_rather_than_guessed():
    v = _video(platform="youtube")
    assert pl.for_video(v, _creator("someone", "youtube")) is None
    assert pl.why_missing(v, _creator("someone", "youtube")) == pl.UNSUPPORTED
