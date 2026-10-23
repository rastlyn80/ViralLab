# Phase 0A — Provider Terms of Service Review (gate condition 4)

**Status: COMPLETE.** Reviewed 2026-10-23 against primary sources.
**Verdict: compatible with the §2.3 operating policy — but one assumption in
the plan was wrong and has been corrected.**

Not legal advice. This is an internal compliance read by a non-lawyer; §2.3's
legal footing still rests on `[SECONDARY]` case summaries.

---

## 1. The correction: Apify does **not** absorb legal risk

`DEVELOPMENT_PLAN.md` §2.3 item 4 previously said to prefer established
vendors over bespoke scrapers partly for **"risk transfer"** — the idea being
that the vendor carries its own ToS exposure.

**Apify's terms say the opposite, explicitly** `[PRIMARY]`:

> *"You are solely responsible for the legality, accuracy, quality,
> appropriateness, and use of all Customer Data."*

> *"Should you use the Services or Actors to extract Customer Data from
> unauthorized sources, you shall be responsible for compensating any
> damages."*

> *"We are not responsible for your privacy obligations or data security
> practices."*

Liability is contractually pushed **onto the customer**, including an
indemnity-flavoured obligation to compensate damages. There is no risk
transfer to be had.

**The rationale in §2.3 is corrected**: the remaining valid reasons to prefer
a vendor over a bespoke scraper are **lower maintenance burden and better
reliability**, not legal risk transfer. The legal exposure is the same as
scraping directly — it simply arrives with better uptime.

([Apify General Terms](https://docs.apify.com/legal/general-terms-and-conditions))

---

## 2. What we may do with the data

**Favourable, and clearer than expected** `[PRIMARY]`:

> *"you retain all right, title, and interest in and to your Customer Data."*

Apify takes only *"a limited, non-exclusive, revocable, worldwide,
royalty-free right to transmit, display, reproduce, or copy Customer Data to
enable us to provide and improve Services."*

**Apify imposes no resale or redistribution restriction of its own.** The
constraints that matter are therefore external — the source platforms' terms,
GDPR, and §2.3's self-imposed policy — not Apify's.

This means §2.3's "internal research use only" rule is **our** choice, not a
contractual requirement. It should stay in force anyway: it is what keeps the
project inside the defensible region of the case law, and it is the condition
under which the legal posture was assessed.

---

## 3. Gaps — what the terms do *not* cover

Three silences, each worth recording rather than glossing:

1. **No robots.txt or third-party-ToS clause.** Apify's terms are silent on
   whether actors must respect a target site's terms. No assurance either
   way.
2. **The Acceptable Use Policy does not address scraping at all.** It covers
   fraud, impersonation, platform abuse and a general "contravene applicable
   laws" clause — nothing on personal data, GDPR, login-walled content, or
   target-site terms.
   ([Acceptable Use Policy](https://docs.apify.com/legal/acceptable-use-policy))
3. **Actor behaviour is not contractually guaranteed.** `§2.3` of the plan
   rests on the premise that collection touches only *logged-out public
   pages without circumventing technical controls*. Apify does not warrant
   this, and actors are third-party code.

   The best available evidence is the actor's own documentation: `apidojo`
   states it accesses **public TikTok data only**, without login, describing
   it as *"public surface data"* and noting it works *"without needing
   proxies"* — the absence of proxy rotation is mild corroboration that it is
   not fighting anti-bot measures. This is **`[SECONDARY]`, vendor
   self-description**, and that limitation is recorded rather than treated as
   verification.
   ([actor page](https://apify.com/apidojo/tiktok-scraper))

**Mitigation:** none of these gaps is disqualifying under §2.3, because the
policy's own constraints (public data, no circumvention, rate limiting,
internal use, no redistributable creator database) are enforced on *our*
side regardless of what the vendor promises. But the project is carrying this
risk itself, with no vendor backstop. That should be a conscious position,
and it is now written down as one.

---

## 4. `apidojo/tiktok-scraper` tier limits — verified, not purchased

Requested verification before any subscription. **No purchase was made.**

| | Free | Paid (Starter+) |
|---|---|---|
| Items per run | **10** `[PRIMARY, actor docs + run log]` | **no per-run item cap stated** |
| Runs per month | **5** | not limited at this tier |
| Price per 1,000 results | **$0.30** | **$0.30** (same) |
| Media (`video.url`) | included | included |

The free-tier limit was confirmed twice: the actor's own page states *"Limited
to 5 runs per month, each capped at 10 items"*, and the run log emitted
`Users with the Free Plan can retrieve a maximum of 10 items.` **Two of the
five monthly free runs have been used.**

### Apify Starter plan `[PRIMARY]`

| Term | Detail |
|---|---|
| Price | **$19/month** ($17/mo billed annually) |
| Included usage | $19 of platform usage |
| Credit rollover | **None** — *"unused usage credits are not rolled over… they expire at the end of the billing cycle"* |
| Overage | Billed on the next invoice, with notification |
| Concurrent runs | 32 (vs 5 on Free) |
| **Cancellation** | **Self-service, any time**, via billing settings |
| Cancellation timing | *"your subscription will last until the end of the current billing cycle"* — no immediate cut-off, no early-termination fee |
| **Refunds** | **None on voluntary cancellation.** Pro-rata refund of unused prepaid fees only where Apify materially fails to provide the service (their §15.5.2) |

([pricing](https://apify.com/pricing), [general terms](https://docs.apify.com/legal/general-terms-and-conditions))

**Assessment of the owner's stated preference (Apidojo Starter).** The
preference is well-founded on the measured evidence: exact view counts versus
`clockworks`' ~3-significant-figure rounding, media URLs included rather than
billed separately, and **10× cheaper per result** ($0.30 vs $3.00/1k).

Commitment risk is low: cancel any time, self-service, worst case one unused
month at $19. The one trap is that **credits do not roll over** — a month
with no collection wastes the $19. So the sensible pattern is to subscribe
for the months when bulk collection actually happens and cancel in between,
rather than holding it continuously.

Break-even against `clockworks` is ~7,000 videos in a single month. The MVP
corpus target is 1,000–5,000 (§7 Phase 3), which falls just below — so for a
single collection month the cost is close either way ($20.50 vs $15.00), and
the real argument for Starter is **data quality, not price**: exact counts
and bundled media. That is a defensible reason to pay $19 once.

---

## 5. Gate condition 4 verdict

✅ **Satisfied.** Terms reviewed against §2.3; compatible, with the risk
posture now accurately described rather than assumed. One plan assumption
corrected (§1).

**Bulk third-party collection is unblocked** from a terms standpoint,
subject to the owner's provider decision and to §2.3's constraints continuing
to be enforced on our side.

### Standing conditions for any collection

1. Public, logged-out data only. No credentialed access, no circumvention.
2. Internal research use only; no redistributable creator database.
3. Rate-limit conservatively.
4. Re-read these terms before any commercialisation, and get actual legal
   advice at that point — the indemnity language in §1 makes this
   non-optional.
5. Re-review if Apify's terms, an actor's behaviour, or the case law changes.
