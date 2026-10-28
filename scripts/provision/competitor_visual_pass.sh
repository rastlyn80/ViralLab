#!/usr/bin/env bash
# Acquire, analyse, delete. Repeat. Costs nothing (ADR 0010).
#
# Cycles rather than one long pass, so no file sits anywhere near its
# six-hour deadline: 40 in, features out, bytes gone, next 40.
#
# If TikTok rate-limits, `acquire_competitor_free.py` stops that pass
# itself. This stops the WHOLE run as well, because coming straight
# back with another batch a second later is the same behaviour the
# per-pass stop was there to avoid.
#
#   competitor_visual_pass.sh [cycles] [batch]
set -uo pipefail
cd /home/rastlyn/virallab
set -a; . ./.env; set +a

CYCLES="${1:-10}"
BATCH="${2:-40}"

for i in $(seq 1 "$CYCLES"); do
  echo "=== cycle $i of $CYCLES ==="
  out="$(.venv/bin/python scripts/provision/acquire_competitor_free.py \
         --max "$BATCH" --yes 2>&1)"
  echo "$out" | tail -24

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
