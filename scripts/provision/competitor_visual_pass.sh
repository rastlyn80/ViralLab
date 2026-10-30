#!/usr/bin/env bash
# Acquire, describe, analyse, delete. Repeat.
#
#   competitor_visual_pass.sh [cycles] [batch] [vision_usd_per_cycle]
#
# Cycles rather than one long pass, so no file sits anywhere near its
# six-hour deadline: N in, features out, bytes gone, next N.
#
# The free half - on-screen text, background complexity, audio - runs
# always and costs nothing (ADR 0010).
#
# The third argument turns on the PAID half: the 31 wide vision
# questions, which are the only way to measure camera angle, framing,
# lighting, who is in shot and whether there is sheet music. Measured
# at ~$0.008 a publication. Default 0, meaning off: this must be
# asked for, never arrived at by leaving a script running.
#
# Order matters. Vision runs BEFORE the deterministic pass, because
# that pass releases the file when it finishes and a released file
# cannot be described.
#
# If TikTok rate-limits, the acquire step stops that pass itself.
# This stops the whole run as well, because coming straight back with
# another batch a second later is the behaviour the per-pass stop was
# there to avoid.
set -uo pipefail

# Say so on the way out, whatever the reason.
#
# A pass stopped at cycle 12 of 20 and the log simply ended after
# the cycle header - indistinguishable from "still running" and
# from "finished quietly". The most likely cause was the kernel
# reclaiming memory, which leaves no message anywhere a normal user
# can read. An exit line costs nothing and turns the next silent
# death into a fact.
on_exit() {
  code=$?
  echo "=== exited with code $code at $(date -Is) ==="
  free -m | head -2
}
trap on_exit EXIT

cd /home/rastlyn/virallab
set -a; . ./.env; set +a

CYCLES="${1:-10}"
BATCH="${2:-40}"
VISION_USD="${3:-0}"

for i in $(seq 1 "$CYCLES"); do
  echo "=== cycle $i of $CYCLES ==="
  out="$(.venv/bin/python scripts/provision/acquire_competitor_free.py \
         --max "$BATCH" --yes 2>&1)"
  echo "$out" | tail -24

  # Paid, opt-in, and before the release.
  if [ "$VISION_USD" != "0" ]; then
    .venv/bin/python scripts/provision/describe_competitors.py \
        --max-usd "$VISION_USD" --yes 2>&1 | tail -10
  fi

  # Always analyse what did arrive, even if the pass stopped early -
  # otherwise those files just sit until the reaper takes them and
  # the fetch was wasted.
  .venv/bin/python scripts/provision/analyse_competitor_media.py 2>&1 | tail -14

  if echo "$out" | grep -q "rate-limited"; then
    echo "=== stopping: the platform asked us to slow down ==="
    break
  fi
  if echo "$out" | grep -q '"attempted": 0'; then
    echo "=== stopping: nothing left to fetch ==="
    break
  fi
done
echo "=== pass complete ==="
