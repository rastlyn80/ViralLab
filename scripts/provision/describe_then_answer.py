"""Does describing the frame first make the model actually look at it?

## The question this answers

The vision stack asks a captioning model for bare booleans under a
grammar that forces `true` or `false`. On `piano_or_keyboard_visible`
it answers yes on 79 of 80 publications, its confidence is identical
when right and wrong, and it self-flagged none of its 78 errors. The
diagnosis in M3 was that the model never evaluates the question: a
forced boolean carries no gradation, so there is nothing to calibrate
and nothing to inspect.

The owner's proposal is the obvious alternative and deserves a real
test rather than an opinion: **have the model describe the scene in
detail, and take the answers from the description.** If the failure is
that the model pattern-matches the genre - "a pianist's video, so
there is a piano" - then forcing it to write down what is actually in
the frame first might break the shortcut.

## What is varied, and what is held fixed

Two factors, tested separately, because conflating them would answer
neither:

* **prompt** - `bare` (the committed arm, booleans only) against
  `describe_first` (a required per-frame description, then the same
  booleans, with the model told the description is the evidence).
* **model** - `gemma-3-4b` (committed, 2.5 GB) against
  `qwen2.5-vl-7b` (4.7 GB, measured at 7634 MiB peak in Phase 0B, so
  it fits the 8 GB card with nothing else resident).

Frames, framing plan, temperature and scoring are identical across
all four cells. The frames are the ones the published benchmark used.

## What counts as a result

The headline is not accuracy. It is **whether the model ever says
no.** A cell that answers yes on 39 of 40 own publications has not
improved, whatever its accuracy, because the own cohort genuinely
contains six publications with no keyboard in shot. So the first
number reported is the yes-rate, and only then the confusion matrix
against the labels.

Own-account publications only: 40 of them, free, because competitor
media was deleted under ADR 0009 and re-acquiring it to answer a
yes/no question about model behaviour would be paying for precision
this experiment does not need.

    python describe_then_answer.py --frames own40.json \\
        --arms bare,describe_first --model gemma
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import urllib.error                                        # noqa: E402
import urllib.request                                      # noqa: E402

from server.ai import local_vlm                            # noqa: E402
from server.ai.base import AnalysisRequest                 # noqa: E402

#: The five the holdout exists to judge, plus the two already verified
#: as a control: if a prompt change breaks `face_visible`, that is a
#: cost and the experiment should show it rather than hide it.
FIELDS = ("piano_or_keyboard_visible", "hands_visible",
          "sheet_music_visible", "face_visible", "text_overlay_present")

MODELS = {
    "gemma": ("gemma-3-4b-it-Q4_K_M.gguf", "mmproj-model-f16.gguf",
              "gemma-3-4b-it-Q4_K_M"),
    "qwen": ("Qwen2.5-VL-7B-Instruct-Q4_K_M.gguf",
             "mmproj-Qwen2.5-VL-7B-f16.gguf", "Qwen2.5-VL-7B-Instruct-Q4"),
}

_RULES = """
A frame shows the thing only if it is ACTUALLY VISIBLE in that frame.
Do not infer it from the kind of video this appears to be. A video of
a pianist very often contains frames with no keyboard in them at all -
a face, an audience, a title card, a wide shot of a stage - and for
those frames the honest answer is false.

"piano_or_keyboard_visible" means THE KEYS ARE VISIBLE. A piano seen
from the side, from behind, or with the lid open and no keys in shot
is false. The instrument being present is not the question.

"text_overlay_present" means text the creator added on top of the
video, not printed text already in the scene such as sheet music.
""".strip()

BARE = """You are analysing the opening frames of a short video.

%s

Answer with JSON only, exactly these keys, each a boolean:
%s
""" % (_RULES, "\n".join(f"  {f}" for f in FIELDS))

DESCRIBE_FIRST = """You are analysing the opening frames of a short video.

Work in two steps.

STEP 1. For each frame in order, write one or two sentences saying
what is literally in it: the subject, what fills the frame, what is in
the background, and anything written on screen. Describe only what you
can see. If you cannot tell, say so.

STEP 2. Answer the questions below USING ONLY YOUR OWN DESCRIPTIONS
from step 1. If a thing does not appear in any of your descriptions,
the answer is false. Do not add anything in step 2 that you did not
write in step 1.

%s

Reply with JSON only:
{
  "frame_descriptions": ["...", "..."],
  %s
}
""" % (_RULES, ",\n  ".join(f'"{f}": true or false' for f in FIELDS))

ARMS = {"bare": BARE, "describe_first": DESCRIBE_FIRST}


def load_frames(path: Path) -> dict[int, list[str]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return {int(v["video_id"]): v["frames"] for v in d["videos"]}


def _as_data_url(frame) -> str:
    if isinstance(frame, dict):
        frame = (frame.get("jpeg_b64") or frame.get("b64")
                 or frame.get("data") or frame.get("image"))
    if isinstance(frame, bytes):
        frame = base64.b64encode(frame).decode()
    if isinstance(frame, str) and frame.startswith("data:"):
        return frame
    return f"data:image/jpeg;base64,{frame}"


def ask(prompt: str, frames: list, *, timeout: float = 240.0) -> str:
    content = [{"type": "text", "text": prompt}]
    for f in frames[:local_vlm.MAX_IMAGES]:
        content.append({"type": "image_url",
                        "image_url": {"url": _as_data_url(f)}})
    body = json.dumps({
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1, "max_tokens": 900,
    }).encode()
    req = urllib.request.Request(
        f"{local_vlm.BASE_URL}/v1/chat/completions", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"]["content"]


def parse(text: str) -> dict:
    """Pull the JSON object out of whatever the model wrapped it in."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        # Models truncate. Take what parses field by field.
        got = {}
        for f in FIELDS:
            hit = re.search(rf'"{f}"\s*:\s*(true|false)', text)
            if hit:
                got[f] = hit.group(1) == "true"
        if "frame_descriptions" in text:
            got["frame_descriptions"] = re.findall(
                r'"([^"]{40,})"', text)[:8]
        return got


def run_cloud_arm(arm: str, frames: dict[int, list], labels: dict,
                  provider, budget) -> dict:
    """The same two prompts, sent to a paid model.

    Every call is reserved against the ledger before it is made and
    settled with the token counts the API itself returns, so the cost
    reported here is measured rather than estimated and the cap is
    enforced by the same machinery that governs media acquisition. A
    refused reservation stops the run; it is never worked around.
    """
    import base64

    prompt = ARMS[arm]
    preds, raw, failures = {}, {}, []
    spent = 0.0
    t0 = time.time()
    for vid, fr in sorted(frames.items()):
        blobs = []
        for f in fr[:4]:
            b64 = f.get("jpeg_b64") if isinstance(f, dict) else f
            blobs.append((float(f.get("at_s", 0.0)) if isinstance(f, dict)
                          else 0.0, base64.b64decode(b64)))
        req = AnalysisRequest(task="describe_then_answer", schema={},
                              prompt=prompt, frames=blobs, video_id=vid,
                              max_output_tokens=900)
        est = provider.estimate_usd(req)
        res = budget(est, f"describe_then_answer:{provider.name}:"
                          f"{arm}:{vid}")
        if res is None:
            failures.append({"video_id": vid, "why": "budget refused"})
            break
        try:
            out = provider.analyse(req)
        except Exception as exc:               # noqa: BLE001 - recorded
            res("failed", 0.0)
            failures.append({"video_id": vid, "why": str(exc)[:160]})
            continue
        actual = (out.usage.actual_usd if out.usage else None) or est
        res("ok", actual)
        spent += actual
        got = parse(out.text or "")
        if not got:
            failures.append({"video_id": vid, "why": "no JSON in reply"})
            continue
        preds[vid] = {f: got.get(f) for f in FIELDS}
        raw[vid] = {"descriptions": got.get("frame_descriptions"),
                    "in_tok": out.usage.input_tokens if out.usage else None,
                    "out_tok": out.usage.output_tokens if out.usage else None}
    return {"arm": arm, "seconds": round(time.time() - t0, 1),
            "answered": len(preds), "failures": failures,
            "billed_usd": round(spent, 4),
            "predictions": preds, "raw": raw}


def run_arm(name: str, frames: dict[int, list], labels: dict) -> dict:
    prompt = ARMS[name]
    preds, raw, failures = {}, {}, []
    t0 = time.time()
    for vid, fr in sorted(frames.items()):
        try:
            text = ask(prompt, fr)
        except Exception as exc:                 # noqa: BLE001 - recorded
            failures.append({"video_id": vid, "why": str(exc)[:160]})
            continue
        got = parse(text)
        if not got:
            failures.append({"video_id": vid, "why": "no JSON in reply"})
            continue
        preds[vid] = {f: got.get(f) for f in FIELDS}
        raw[vid] = {"descriptions": got.get("frame_descriptions"),
                    "chars": len(text)}
    return {"arm": name, "seconds": round(time.time() - t0, 1),
            "answered": len(preds), "failures": failures,
            "predictions": preds, "raw": raw}


def score(preds: dict, labels: dict) -> dict:
    out = {}
    for f in FIELDS:
        tp = tn = fp = fn = unk = 0
        for vid, p in preds.items():
            truth = labels.get(vid, {}).get(f)
            got = p.get(f)
            if truth is None or got is None:
                unk += 1
                continue
            if got and truth:
                tp += 1
            elif got and not truth:
                fp += 1
            elif not got and truth:
                fn += 1
            else:
                tn += 1
        n = tp + tn + fp + fn
        pos = tp + fn
        yes = tp + fp
        base = max(pos, n - pos) / n if n else 0.0
        out[f] = {
            "n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "unscored": unk,
            # The first number that matters: a model that always says
            # yes has not improved however good its accuracy looks.
            "says_yes": yes,
            "says_yes_rate": round(yes / n, 3) if n else None,
            "true_positive_rate_in_set": round(pos / n, 3) if n else None,
            "specificity": round(tn / (tn + fp), 3) if (tn + fp) else None,
            "recall": round(tp / pos, 3) if pos else None,
            "accuracy": round((tp + tn) / n, 3) if n else None,
            "baseline": round(base, 3),
            "lift": round((tp + tn) / n - base, 3) if n else None,
            "negatives_available": tn + fp,
        }
    return out


def run_cloud(args) -> int:
    """A paid run, with two independent brakes.

    `--max-usd` is this script's own ceiling, checked before every
    call. The budget ledger's monthly cap and per-category allowance
    are checked by `budget.reserve` underneath it. Neither is
    bypassable from here and the lower of the two binds.
    """
    import os

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from server import budget as bud
    from server.ai.providers import (AnthropicSonnetVisionProvider,
                                     AnthropicVisionProvider)

    provider = (AnthropicSonnetVisionProvider()
                if args.model == "claude-sonnet" else
                AnthropicVisionProvider())
    ok, why = provider.available()
    if not ok:
        print(f"cannot run: {why}")
        return 1

    url = os.environ.get("VIRALLAB_DB_URL")
    if not url:
        print("VIRALLAB_DB_URL is not set; the spend ledger lives there "
              "and an unledgered paid run is not allowed")
        return 1
    engine = create_engine(url, future=True)
    Session = sessionmaker(engine, expire_on_commit=False, future=True)

    spent = {"usd": 0.0}

    def budget(estimate: float, key: str):
        """Reserve, then hand back a settle function. None = refused.

        The idempotency key is required by `reserve` and is the thing
        that stops a re-run paying twice for the same publication and
        arm. It is derived from both, never from a counter.
        """
        if spent["usd"] + estimate > args.max_usd:
            print(f"stopping: --max-usd {args.max_usd:.2f} reached "
                  f"(spent {spent['usd']:.4f})")
            return None
        with Session() as s:
            try:
                r = bud.reserve(s, provider=provider.name,
                                estimated_usd=estimate,
                                idempotency_key=key,
                                category="cloud_vision",
                                detail={"script": "describe_then_answer"})
                s.commit()
            except Exception as exc:          # noqa: BLE001 - reported
                print(f"budget refused: {exc}")
                return None
        rid = r.id

        def settle(outcome: str, actual: float):
            with Session() as s2:
                if outcome == "ok":
                    bud.settle(s2, rid, actual_usd=actual)
                    spent["usd"] += actual
                else:
                    bud.release(s2, rid, reason=outcome)
                s2.commit()
        return settle

    frames = load_frames(Path(args.frames))
    labels = {int(k): v for k, v in
              json.loads(Path(args.labels).read_text(
                  encoding="utf-8"))["labels"].items()}
    print(f"{len(frames)} publications, {provider.name} "
          f"({provider.MODEL}), ceiling ${args.max_usd:.2f}")

    results = {}
    for arm in args.arms.split(","):
        r = run_cloud_arm(arm, frames, labels, provider, budget)
        r["scores"] = score(r["predictions"], labels)
        results[arm] = r
        print(f"\n=== {arm}  {r['seconds']}s  answered "
              f"{r['answered']}/{len(frames)}  failures "
              f"{len(r['failures'])}  billed ${r['billed_usd']}")
        for f, sc in r["scores"].items():
            print(f"  {f:28s} says yes {sc['says_yes']:>2}/{sc['n']:<3} "
                  f"spec {sc['specificity'] if sc['specificity'] is not None else '-':>5} "
                  f"recall {sc['recall'] if sc['recall'] is not None else '-':>5} "
                  f"lift {sc['lift'] if sc['lift'] is not None else '-':>6}")

    print(f"\ntotal billed: ${spent['usd']:.4f}")
    if args.out:
        Path(args.out).write_text(json.dumps(
            {"model": provider.MODEL, "provider": provider.name,
             "billed_usd": round(spent["usd"], 4),
             "arms": {k: {kk: vv for kk, vv in v.items()
                          if kk != "predictions"}
                      | {"predictions": {str(a): b for a, b in
                                         v["predictions"].items()}}
                      for k, v in results.items()}},
            indent=1), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--labels", default="tests/fixtures/vision/"
                                        "holdout_labels_own.json")
    ap.add_argument("--arms", default="bare,describe_first")
    ap.add_argument("--model", default="gemma",
                    choices=sorted(MODELS) + ["claude-sonnet", "claude-haiku"])
    ap.add_argument("--max-usd", type=float, default=0.80,
                    help="hard ceiling for a cloud run. The ledger cap "
                         "still applies on top of this; whichever is "
                         "lower wins.")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.model.startswith("claude-"):
        return run_cloud(args)

    model_file, mmproj_file, model_name = MODELS[args.model]
    local_vlm.MODEL = local_vlm.ROOT / "models" / model_file
    local_vlm.MMPROJ = local_vlm.ROOT / "models" / mmproj_file
    if not local_vlm.MODEL.exists():
        print(f"missing {local_vlm.MODEL}")
        return 1
    # Qwen's vision tower needs the room; the committed 4096 truncates
    # four frames plus a description prompt.
    local_vlm.CTX_SIZE = 8192

    ok, why = local_vlm.start_server(wait_s=300.0)
    if not ok:
        print(f"llama-server did not come up: {why}")
        return 1

    frames = load_frames(Path(args.frames))
    labels = {int(k): v for k, v in
              json.loads(Path(args.labels).read_text(
                  encoding="utf-8"))["labels"].items()}
    print(f"{len(frames)} publications, model {model_name}")

    results = {}
    for arm in args.arms.split(","):
        r = run_arm(arm, frames, labels)
        r["scores"] = score(r["predictions"], labels)
        results[arm] = r
        print(f"\n=== {arm}  {r['seconds']}s  "
              f"answered {r['answered']}/{len(frames)}  "
              f"failures {len(r['failures'])}")
        print(f"  {'field':28s} {'says yes':>9s} {'truth yes':>10s} "
              f"{'spec':>6s} {'recall':>7s} {'lift':>7s}")
        for f, s in r["scores"].items():
            print(f"  {f:28s} {str(s['says_yes'])+'/'+str(s['n']):>9s} "
                  f"{str(round(s['true_positive_rate_in_set']*s['n']) if s['true_positive_rate_in_set'] is not None else '?')+'/'+str(s['n']):>10s} "
                  f"{s['specificity'] if s['specificity'] is not None else '-':>6} "
                  f"{s['recall'] if s['recall'] is not None else '-':>7} "
                  f"{s['lift'] if s['lift'] is not None else '-':>7}")

    if args.out:
        payload = {"model": model_name, "arms": {
            k: {kk: vv for kk, vv in v.items() if kk != "predictions"}
            | {"predictions": {str(a): b for a, b in
                               v["predictions"].items()}}
            for k, v in results.items()}}
        Path(args.out).write_text(json.dumps(payload, indent=1),
                                  encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
