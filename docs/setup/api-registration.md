# Registering the TikTok and Meta apps

One-time setup so ViralLab can read **your own** accounts. Verified against
the platforms' own documentation on 2026-10-24.

**Neither platform requires app review for this.** Review exists for apps
that serve *other people's* accounts. Yours only ever reads your own, so both
stay in their respective development/sandbox modes.

Budget ~15 minutes each. Do them in either order.

---

## A. Meta / Instagram — the easier one

**Prerequisites**

1. Your Instagram account must be a **Professional** account (Business or
   Creator). Personal accounts have *zero* API access.
   Instagram app → Settings → Account type and tools → Switch to professional.
2. It must be **linked to a Facebook Page**. ViralLab uses the Facebook Login
   path because Hashtag Search and Business Discovery exist only there
   (ADR 0001). A Page with no followers is fine — it exists to carry the
   permission, not to be used.
   Instagram → Settings → Linked accounts → Facebook.

**Steps**

1. Go to **developers.facebook.com** → log in → **My Apps** → **Create App**.
2. Use case: choose **Other** → app type **Business**.
3. Name it anything (`ViralLab`). Leave it in **Development** mode — that is
   what avoids app review.
4. In the left sidebar: **Add Product** → **Instagram** → set it up.
5. **App roles** → confirm your own account is listed as **Administrator**.
   In Development mode the app can only touch accounts holding a role, which
   is precisely why no review is needed.
6. **App settings → Basic**: copy the **App ID** and **App Secret**.

That is all I need from Meta: **App ID + App Secret**. I derive the
long-lived Page token from there (it does not expire — ADR 0005).

---

## B. TikTok

**Steps**

1. Go to **developers.tiktok.com** → log in with your TikTok account →
   **Manage apps** → **Create an app**.
2. Add these products to the app:
   - **Login Kit** (the OAuth flow)
   - **Display API** (reading your video list and its metrics)
3. Request these scopes:
   `user.info.basic`, `user.info.stats`, `video.list`
4. **Register a redirect URI.** See the note below if you want to avoid
   hosting anything — the short version is that any URI works, because the
   authorization code is readable from your browser's address bar.
5. **Add a sandbox** (Sandbox settings → Add account under *Target users*),
   and add your own TikTok account. Up to 10 accounts; results appear within
   an hour. A sandbox is explicitly *"a restricted environment that allows
   you to try out integrations without having to submit your app for
   review."*
6. Copy the **Client key** and **Client secret**.

That is all I need from TikTok: **Client key + Client secret**, plus the
redirect URI you registered.

### One honest uncertainty

TikTok documents that sandbox mode excludes the **Content Posting API** (for
public videos) and the **Data Portability API**. It does **not** state
whether the **Display API** works in a sandbox — the exclusion list simply
does not mention it `[PRIMARY, by omission]`.

So: the sandbox path probably works for reading your own video list, but it
is not documented either way and we will find out on the first real call. If
it turns out production access is required, the fallback is TikTok's app
audit — a demo video, a privacy policy URL, a terms URL and a specific
use-case description, typically 1–3 weeks with one rejection round
`[SECONDARY]`. I would tell you before starting that, not after.

### The redirect URI, without hosting anything

A redirect URI only has to receive a browser redirect carrying a `code`
parameter. It does **not** have to be a working server:

- Register something stable, e.g.
  `https://gabry-ubuntu.tailc6fc62.ts.net:8443/oauth/tiktok/callback`
- Visit the consent URL in a browser. After you approve, TikTok redirects.
- Even if the page fails to load, **the full URL in the address bar contains
  `?code=…`**. Copy that whole URL and hand it over.

The code is single-use and expires in minutes, so this is a reasonable
one-time method. If the Tailscale URL is awkward, `http://localhost:8000/callback`
is equally fine for the same reason.

---

## What to send, and how

| Platform | What I need |
|---|---|
| Meta | App ID, App Secret |
| TikTok | Client key, Client secret, the redirect URI you registered |

These are **app** credentials, not account passwords — they identify the
software, and alone they cannot read anything. The account authorization is a
separate step that only you can perform, in your own browser.

They are still secrets: they go straight into the gitignored `.env`, same as
every other credential here. If you would rather not paste them into this
conversation, put them in `C:\Coding Projects\ViralLab\.env` yourself as:

```
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=...
META_APP_ID=...
META_APP_SECRET=...
```

and just tell me they are there.

---

## What happens next

1. I generate the TikTok consent URL; **you** open it and approve. You paste
   back the redirected URL, I exchange the code, and the token store takes
   over (rotation-aware refresh, ADR 0005).
2. For Meta I walk you through generating a short-lived token in the Graph
   API Explorer, then exchange it for a long-lived User token and derive the
   non-expiring Page token automatically.
3. Historical backfill of both accounts, then the daily schedule.

**No authorization starts without your explicit go-ahead**, and the consent
step is yours to click either way — I cannot perform it for you.

### One thing worth knowing before you start

Instagram retains insights for **about two years** `[PRIMARY, ADR 0001]`, on a
rolling window. Anything older than that is already unrecoverable, and more
of it falls off the edge every day. That is the one argument for doing this
sooner rather than later — the TikTok side has no such limit.
