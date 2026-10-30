# ADR 0011 — Observation at scale carries the product; the experiment stops being the gate

**Date:** 2026-10-30
**Status:** Accepted (owner's decision)
**Changes:** CLAUDE.md §8 ("The MVP ends with Phase 8, the live
own-account experiment"), BLUEPRINT §3 M9 and §7 Sequencing

## Context

The plan made one pre-registered own-account experiment the end of
the MVP, on the reasoning that a suite recommending without having
tested a recommendation is the failure mode the project was built
to avoid. That reasoning is sound and the conclusion no longer
follows, for two reasons that only became visible once the data
existed.

**The experiment is underpowered and everyone knew it.** It needs
22 publications at a measured 4.84 a month — about four and a half
months — and view counts on this account vary by an order of
magnitude between two videos for reasons nothing in the dataset
can see. BLUEPRINT M9 hedges this as "with its stated power
limits". The honest translation is that the most likely outcome is
"inconclusive", four months from now, having constrained what the
owner posts in the meantime.

**The observational side got strong while nobody was looking.** It
now holds 6,174 competitor publications across 208 creators with
deep enough catalogues, every video scored against its own
creator's distribution, log age partialled out, and
Benjamini-Hochberg across the family. Four measurements now hold
**independently on the owner's account and on 208 other pianists**:
naming the composer, naming the form, hashtag count, and repeating
the kind of piece played recently.

Replication across independent subjects is evidence. Epidemiology
is built on it; nobody randomises smoking. One underpowered
experiment on a single account is not obviously better evidence
than a pattern that reproduces across two hundred independent
creators, and it is four months slower.

The owner's position, in his words: *"The suite needs to be based
on scientific and observational data, not on what happens to me."*

## Decision

1. **The experiment is no longer the MVP gate.** It stays built,
   locked and running. It is completed when it is completed. The
   product is not blocked on it and the owner's posting is not
   constrained by it.

2. **The observational bar rises to compensate.** A measurement may
   be promoted beyond `Observed` only if it **replicates on
   independent creators** — it must hold on the owner's account and
   on the competitor corpus, in the same direction. A pattern seen
   on one account is a description of that account.

3. **Natural experiments are added**, as the nearest thing to
   control that observation allows: matched pairs of the owner's
   own publications, alike on the things that are not the question
   and far apart on outcome. The owner asked for this months ago in
   his own words — *"similar and draws no traffic"*.

4. **The ceiling is unchanged and must stay stated.** No amount of
   observational data separates "videos naming the composer did
   better" from "the videos where he bothered naming the composer
   were the ones he put more care into". More creators measure that
   confound more precisely; they do not remove it. Nothing here may
   be called causal, and the grade `Validated` remains reachable
   only through the experiment.

## Consequences

**What improves.** The product ships on evidence it already has,
and the strongest claim it makes — four findings replicated across
208 independent creators — is a claim it can actually defend. The
owner posts what he wants to post.

**What we give up.** The one route to a causal claim gets slower
and less certain, because an experiment nobody is waiting for is an
experiment that is easy to never finish. Guarded by keeping it
locked and running rather than cancelling it.

**What this is not.** Not permission to describe a correlation as a
cause, not permission to promote a finding seen on one account, and
not a reason to stop collecting. The opposite: the whole argument
rests on scale, so thin data is now the binding constraint on what
the product may say.

## Alternatives considered

**Keep the gate.** Rejected: it makes the product's usefulness
contingent on an outcome that is most likely to be inconclusive,
and it puts a four-month hold on a suite that is already producing
replicated findings.

**Run a bigger experiment.** Powering it properly needs hundreds of
publications from one account. Not available at 4.84 a month, and
not made available by wanting it.

**Cancel the experiment.** Rejected. It is the only instrument that
can ever produce a causal claim, it is already built and locked,
and it costs nothing to let it run.
