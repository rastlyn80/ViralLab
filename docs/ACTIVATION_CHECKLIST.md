# Activating paid competitor discovery

Everything below is for the owner to do. Nothing here has been done, and no
paid call has ever been made by this system.

Verified 2026-10-26 against commit `8b29db6` plus Phase 6A.

---

## Before you start

**Current state:** `spending.monthly_cap_usd` is `$0.00`,
`providers_authorised` is empty, `APIFY_TOKEN` is unset. Every discovery
path therefore resolves to the fixture adapter and **refuses rather than
attempts**. Month-to-date spend is `$0.00` and the `budget_reservations`
table is empty.

You can stop at any step. Until step 3 is complete, nothing can spend.

---

## 1. Create the Apify account and token

1. Sign up at <https://apify.com>. The free tier includes a monthly usage
   allowance; the plan below costs well under it.
2. Go to **Settings → Integrations → API tokens** and create a token.
3. Copy it. You will paste it in step 3 and never need to see it again.

**Do not paste the token into a chat, a commit, a config file that is
tracked by git, or a URL.** `.env` is gitignored and is the only place it
belongs. The client sends it as an `Authorization: Bearer` header — never
as a URL parameter — because the provider's own documentation warns that
URLs end up in browser history and server logs.

## 2. Decide the cap

Recommended first cap: **`$5.00`**.

The planned first experiment is `$0.42` (five operations), so `$5.00`
leaves room to repeat it and to research a handful of discovered creators
without a second authorisation round. The cap is a hard ceiling: a
reservation that would exceed it is refused, not negotiated.

## 3. Configure, on the Ubuntu server

```bash
cd /home/rastlyn/virallab

# a) authorise the provider and set the cap
nano config/settings.yaml
```

```yaml
spending:
  monthly_cap_usd: 5.00
  providers_authorised:
    - apify
```

```bash
# b) add the token to the gitignored .env (0600, never committed)
printf '\nAPIFY_TOKEN=%s\n' 'paste-your-token-here' >> .env
chmod 600 .env

# c) restart so the process picks both up
sudo systemctl restart virallab-api
```

**Both conditions are required.** `active_provider()` returns `fixture`
unless the provider is authorised in settings **and** the token is present,
so a token alone or a cap alone changes nothing. That is deliberate: either
one on its own would let a request reach a paid endpoint you had not
approved.

## 4. Confirm it took effect

Open the interface → **Discover creators**. The banner at the top should
change from the fixture warning to a live-provider line showing the cap and
what remains. The search form will show *"This provider charges. You will
be shown the exact estimate and asked to confirm before anything runs."*

Check `/api/research/state` reports `fixture_mode: false` and
`active_provider: "apify"`.

If it still says fixture, the most likely cause is a typo in the YAML or a
missing restart. The server logs a warning naming the environment variable
when the provider is authorised but the token is absent.

## 5. Run the first experiment

Follow `docs/LIVE_VALIDATION_PLAN.md` in order. Operations 1–3 are
`$0.05` each; stop after Operation 1 and look at what came back before
paying for more.

Every paid search now requires an explicit confirmation that states the
provider, the query, the item cap, the estimated cost and the remaining
budget. Cancelling costs nothing. If an identical search ran recently you
are offered the stored results for free first.

---

## What protects you once it is live

| protection | behaviour |
|---|---|
| **Cap** | A reservation that would exceed `monthly_cap_usd` is refused. Spending more needs a new, explicit change. |
| **Atomic reservation** | Budget is reserved under a per-provider-month advisory lock and **committed before the provider request starts**, so a crash mid-call leaves the money held rather than silently released. Verified by a test that fails if the commit is moved. |
| **In-flight guard** | Two requests for the same query cannot both start a paid run. |
| **Reuse window** | An identical search within 6 hours returns the stored result for free unless you tick "re-run". |
| **Estimate is free** | Pricing a search never calls the provider. |
| **Item cap** | Hard-capped at 500 items per run regardless of what is requested. |
| **Run timeout** | A provider run that overruns is aborted rather than billing indefinitely. |
| **Actual cost recorded** | What gets stored is what the provider billed, not the estimate. |

---

## Prices, verified

`[PRIMARY]` Checked 2026-10-26 against the actors' own Apify store pages.

| actor | listed price | code uses | status |
|---|---|---|---|
| `apidojo/tiktok-scraper` | **$0.30 / 1,000 posts**, no platform fee | $0.30 / 1k | ✅ matches; 12,914 users, 99.9% success |
| `apify/instagram-hashtag-scraper` | **$1.90–$2.60 / 1,000** (tier-dependent) | $2.56 / 1k | ✅ within band, conservative end; 139,179 users, 100% success |
| `apify/instagram-reel-scraper` | same family | $2.56 / 1k | assumed same band `[SECONDARY]` |
| `clockworks/free-tiktok-scraper` | — | $3.00 / 1k | implemented for redundancy, **not the default** |

Two capability claims the plan depends on were re-confirmed on the same
pages:

- `[PRIMARY]` the Instagram hashtag actor returns **no follower counts** —
  "extracts creator info (username + profile ID)" with no account-level
  follower data. This is why Instagram candidates show *unknown* rather
  than a number.
- `[PRIMARY]` it returns **recent posts only**, not full history. This is
  why hashtag discovery finds creators and never measures them.

If the Instagram tier price is $2.60 rather than $2.56, the estimate
under-reserves by 1.6%, which the 1.25× safety factor on every reservation
absorbs comfortably.

---

## Turning it off

Set `monthly_cap_usd: 0.00` **or** remove `apify` from
`providers_authorised` **or** delete `APIFY_TOKEN`, then restart. Any one of
the three returns the system to fixture mode. Collected data is kept.
