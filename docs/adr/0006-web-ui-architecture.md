# ADR 0006 — Web UI architecture: typed JSON API + no-build ES-module SPA

**Date:** 2026-10-24
**Status:** accepted
**Context:** Phase 3 begins the first unified graphical interface
(`docs/PRODUCT_VISION.md` §3.I). Until now every interface has been a
separate command-line script or a throwaway local page, which the vision
document explicitly forbids continuing.

## Decision

1. **Backend:** a typed JSON API under `/api/*` in the existing FastAPI
   process, with Pydantic response models. No new service (CLAUDE.md §6:
   the target topology is three long-running processes).
2. **Frontend:** a single-page application of plain ES modules served
   statically from `webui/`, with **no build step, no bundler and no
   `node_modules`**.
3. **Typed contract:** the Pydantic models are the single source of truth.
   FastAPI publishes them as OpenAPI at `/openapi.json`, and
   `scripts/gen_ui_types.py` generates `webui/types.js` JSDoc typedefs from
   that schema, so editors type-check the frontend against the real
   contract without a compiler in the loop.
4. **Auth:** a signed, HttpOnly, SameSite=Strict session cookie obtained by
   posting a password. Mutations additionally require a header that a
   cross-site form cannot set.

## Why not React + Vite + TypeScript

That was the default expectation and it was rejected on measured grounds,
not taste.

- **The server has 184 MiB of free RAM** (7.2 GiB total, 2.5 GiB used,
  4 cores). Node 24 is installed, but running a Vite build there is
  unreasonable next to Postgres, the API, the collector and the reaper.
- **So the build would have to happen on Windows and ship a `dist/`.**
  Deployment today is `scripts/deploy.py` copying source directories over
  SFTP. Introducing a build artefact means either committing `dist/` (a
  generated tree in version control) or adding a build-then-deploy step that
  fails whenever the Windows box is off — and the Windows box is
  *deliberately* intermittent.
- **~200 MB of `node_modules` for a six-view internal tool** is exactly the
  dependency weight CLAUDE.md §6 tells us to justify or avoid. There is one
  user, on a tailnet, on a current browser.
- The user's own constraint: *"Do not introduce a complicated frontend
  build/deployment process unnecessarily."*

## Why this still counts as a maintainable modern architecture

The requirement was clean API/frontend separation, typed data contracts,
reusable components and straightforward deployment. All four hold:

| requirement | how it is met |
|---|---|
| clean separation | the frontend is a static asset; it talks to `/api/*` over JSON and shares no code or templates with the server |
| typed data contracts | Pydantic models → OpenAPI → generated JSDoc typedefs; the contract is checked at the boundary, not hand-written twice |
| reusable components | `webui/components.js` exports composable render functions over a shared design-token stylesheet |
| straightforward deployment | copy files; `deploy.py` already does exactly this, and a reload needs no build |

Browser features relied on are all baseline for years: ES modules, `fetch`,
template literals, CSS custom properties, `<dialog>`.

## Consequences

- **Accepted cost:** no JSX, no compile-time type errors in the frontend,
  and manual DOM updates. For six read-mostly views this is a smaller cost
  than the toolchain it avoids.
- **Charts** (creator timelines, Phase 5) will be drawn with inline SVG, or
  a single CDN-hosted library if one becomes necessary. Neither needs a
  bundler.
- **Revisit when** any of these becomes true: more than one concurrent user,
  genuinely complex client state, a second client (mobile app), or a view
  that cannot be built without a component framework. The API is already the
  seam, so swapping the frontend later touches nothing on the server.

## The local-media problem, and what we do about it

The unified UI runs on **Ubuntu**, which holds the authoritative database
and the preserved Instagram media. Local master files live on **`D:` on
Windows** and are deliberately never transferred.

So the server cannot play a local master. The matching-review view therefore:

- always plays the **Instagram** clip, via the existing HMAC-signed
  `/media/{video_id}` endpoint — no file path is ever exposed to the browser;
- shows the master's metadata and the full evidence, which is enough to
  decide most pairs;
- **opportunistically** embeds the master from a local bridge on
  `127.0.0.1` when the user happens to be at the Windows machine, and says
  plainly when it is unavailable rather than showing a dead player.

`scripts/review_ui.py` is reduced to that bridge role, which is how it gets
absorbed rather than duplicated.
