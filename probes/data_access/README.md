# Phase 0A probe — data-access feasibility

Throwaway probe code. **Not** Phase 1 infrastructure: no database, no
services, no dependencies beyond the Python standard library. Kept in the
repo only so the Phase 0A result is reproducible and auditable.

## Files

| File | Purpose |
|---|---|
| `field_requirements.py` | Single source of truth for what ViralLab needs from a niche-discovery provider. Criticality levels, field aliases, per-field validators. |
| `score_provider.py` | Scores a raw provider sample against those requirements and prints the Phase 0A gate verdict. |
| `fixture_good.json` | Synthetic sample that should **pass**. Harness self-test. |
| `fixture_bad.json` | Synthetic sample that should **fail** — date-rounded timestamps, half of them carrying the scrape date. Negative control. |

## Usage

```bash
python score_provider.py <sample.json> --provider "<name>"
```

Accepts a JSON array, JSONL, or an object wrapping a `results`/`items`/
`data` array. Exit code **0 = gate passed, 1 = failed, 2 = unreadable
input**.

## Self-test

Run both fixtures after any change. The good one must exit 0, the bad one
must exit 1:

```bash
python score_provider.py fixture_good.json   # expect exit 0
python score_provider.py fixture_bad.json    # expect exit 1
```

The negative control is not decoration. Writing it caught two real detection
gaps in the first version of the scorer: a provider where only *half* the
timestamps were contaminated with the scrape date passed the distinctness
check, and a sample with **zero** videos old enough for a fixed-age snapshot
passed silently. Both are now hard failures.

## What the scorer checks

**Field presence**, by criticality:
- `BLOCKING` — gate fails below 100%. Without these the data cannot be used
  for labeling or modeling at any price. `posted_at` is the one most likely
  to be quietly absent or fake.
- `IMPORTANT` — counted toward the ≥80% weighted completeness threshold.
- `NICE` — reported only.
- `CAPABILITY` — `media_url`, reported separately and **never** folded into
  the score. It does not affect Tier 1 but decides whether Tier 2/3
  multimodal analysis of third-party videos is possible at all.

**Timestamp plausibility** — because a field that merely parses is not good
enough:
- all values parse, none future-dated
- values vary, and **no single value dominates** (>20% share ⇒ scrape-time
  contamination)
- **time-of-day preserved** — a provider that rounds to the date destroys
  the posting-time features the project brief asks for, while still looking
  like a valid timestamp
- the window spans enough days for chronological validation
- at least some videos are ≥35 days old, i.e. eligible for Strategy A
  mature snapshots (plan §5.4)

## Deliberate non-checks

`media_url` aliases **exclude** page links such as `share_url`,
`webVideoUrl` and `embed_link`. A page link is not downloadable media;
conflating the two is precisely the error corrected in plan §2.1, so the
scorer reports 0% media availability for a provider that returns only links.

## Adding a provider

Add aliases to `field_requirements.py` rather than renaming the provider's
output, so the raw sample stays byte-for-byte auditable against what was
actually returned.
