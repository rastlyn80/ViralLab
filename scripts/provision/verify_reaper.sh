#!/usr/bin/env bash
# Prove the BACKGROUND reaper runs: expire a lease, then wait without ever
# calling /admin/reap. If status returns to 'pending' on its own, the
# in-process loop is alive.
set -euo pipefail
cd "$HOME/virallab"; set -a; . ./.env; set +a
.venv/bin/python seed_roundtrip.py >/dev/null
.venv/bin/python sql.py "update analysis_batches set status='leased',
  lease_token='fake', leased_by='crashed-worker',
  lease_expires_at = now() - interval '5 minutes',
  last_heartbeat_at = now() - interval '5 minutes'" >/dev/null
echo "before: $(.venv/bin/python sql.py --query "select status from analysis_batches")"
for i in $(seq 1 10); do
  sleep 10
  S=$(.venv/bin/python sql.py --query "select status from analysis_batches")
  if [[ "$S" == *pending* ]]; then
    echo "after ${i}0s: $S  <- background reaper requeued it, no manual call"
    .venv/bin/python sql.py --query "select attempt_count, left(last_error,45) from analysis_batches"
    exit 0
  fi
done
echo "TIMEOUT after 100s: still $S - background reaper did NOT run"
exit 1
