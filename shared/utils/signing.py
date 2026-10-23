"""HMAC-signed, short-lived, single-video media URLs.

Media is never exposed through a blanket static mount (DEVELOPMENT_PLAN.md
4.2). Each URL grants one worker access to one video for a bounded window,
so a leaked link expires on its own and cannot be walked to other videos.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode

DEFAULT_TTL_SECONDS = 900


def _payload(video_id: int, expires_at: int) -> bytes:
    return f"{video_id}:{expires_at}".encode()


def sign_media_url(base_url: str, video_id: int, secret: str, *,
                   ttl_seconds: int = DEFAULT_TTL_SECONDS,
                   now: int | None = None) -> str:
    """Build a signed URL for exactly one video."""
    expires_at = int(now or time.time()) + ttl_seconds
    sig = hmac.new(secret.encode(), _payload(video_id, expires_at),
                   hashlib.sha256).hexdigest()
    query = urlencode({"expires": expires_at, "sig": sig})
    return f"{base_url.rstrip('/')}/media/{video_id}?{query}"


def verify_media_signature(video_id: int, expires: int, sig: str,
                           secret: str, *, now: int | None = None) -> None:
    """Raise ValueError unless the signature is valid and unexpired.

    The signature covers the video id, so a valid signature for one video
    cannot be replayed against another.
    """
    now = int(now or time.time())
    if expires < now:
        raise ValueError("signature expired")
    expected = hmac.new(secret.encode(), _payload(video_id, expires),
                        hashlib.sha256).hexdigest()
    # Constant-time: a timing oracle here would leak the signature.
    if not hmac.compare_digest(expected, sig):
        raise ValueError("bad signature")
