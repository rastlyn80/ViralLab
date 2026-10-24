# ADR 0005 — OAuth token handling for TikTok and Instagram

**Date:** 2026-10-24
**Status:** Accepted
**Driver:** Phase 2. Verified against primary sources before writing any
integration code, per CLAUDE.md §2.

## Verified mechanics

### TikTok Display API `[PRIMARY]`

| | |
|---|---|
| Token endpoint | `POST https://open.tiktokapis.com/v2/oauth/token/` (`application/x-www-form-urlencoded`) |
| Authorization code exchange | `client_key`, `client_secret`, `code` (**URL-decoded**), `grant_type=authorization_code`, `redirect_uri` |
| Refresh | `client_key`, `client_secret`, `grant_type=refresh_token`, `refresh_token` |
| Access token lifetime | **24 hours** |
| Refresh token lifetime | **365 days** |
| Revoke | `POST https://open.tiktokapis.com/v2/oauth/revoke/` |

([OAuth token management](https://developers.tiktok.com/doc/oauth-user-access-token-management))

**The refresh token rotates.** TikTok's documentation is explicit: *"The
returned `refresh_token` may be different than the one passed in the payload.
You must use the newly-returned token if the value is different than the
previous one."*

This is the single most important correctness detail in Phase 2. A refresh
implementation that persists only the new *access* token and keeps the old
refresh token will work for exactly one cycle and then fail permanently,
24 hours later, with no obvious cause. The token store therefore writes both
values in one transaction on every refresh.

### Instagram via the Facebook Login path `[PRIMARY]`

ADR 0001 committed the project to the **Facebook Login** path, because
Hashtag Search and Business Discovery exist only there. That choice changes
the token mechanics, and the difference is easy to get wrong:

| Path | Refresh mechanism |
|---|---|
| Instagram Login (**not us**) | `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token` → 60 more days |
| **Facebook Login (us)** | exchange for a long-lived **User** token, then derive a **Page** token from it |

- Long-lived User token: `GET https://graph.facebook.com/{version}/oauth/access_token?grant_type=fb_exchange_token&client_id=…&client_secret=…&fb_exchange_token=…` → **60 days**
- Page token: `GET /{app-scoped-user-id}/accounts?access_token={long-lived-user-token}` → **long-lived Page access tokens do not expire** and are invalidated only under specific conditions.

([Get Long-Lived Tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/))

**Correction to the plan.** `DEVELOPMENT_PLAN.md` §7 Phase 2 says *"IG's
60-day token must be refreshed before expiry"*. That is true of the **User**
token, but the credential ViralLab actually uses for media and insights is
the **Page** token, which does not expire. The 60-day refresh matters only
for re-deriving a Page token later.

## Decision

1. **One `oauth_tokens` table**, keyed by `(platform, account_ref)`, holding
   access token, refresh token, expiry, scope and the derived Page id. Tokens
   live in the database rather than `.env` because they rotate at runtime;
   `.env` holds only the app credentials, which do not.
2. **Refresh is transactional and rotation-aware.** Access and refresh tokens
   are written together, under a row lock, and the refresh token is replaced
   whenever the response differs from what was sent.
3. **Refresh early, not on failure.** TikTok's access token lasts 24 h and is
   refreshed when less than 6 hours remain, so a failed refresh has three
   more attempts before anything breaks. Refresh-on-401 alone would make
   every expiry a visible outage.
4. **Tokens are never logged.** Error paths log the platform, the HTTP status
   and the account reference — never the token or the app secret.
5. **A missing or expired credential is a deferral, not a crash.** The
   collector records the condition and the scheduler retries later, matching
   how the GPU worker treats insufficient VRAM.

## Consequences

- The TikTok refresh token is the long-lived credential (365 days). If it is
  lost, the account owner must re-authorise interactively; there is no
  server-side recovery. It is worth backing up.
- Instagram's non-expiring Page token means the common failure mode is not
  expiry but **invalidation** (password change, permission revocation, app
  review change). The collector must treat a sudden 190-series error as
  "re-authorisation required" rather than retrying indefinitely.
- Both flows need a one-time interactive authorisation by the account owner,
  which is the Phase 2 approval gate.
