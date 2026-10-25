#!/usr/bin/env bash
# Launcher for the systemd unit. Resolves the bind address itself rather
# than trusting an environment variable.
#
# Why this exists: the unit previously had `--host ${VIRALLAB_BIND_IP}`, and
# when deploy.py regenerated .env without that key the variable expanded to
# nothing - so uvicorn fell back to its default and bound 0.0.0.0, exposing
# the API on the home LAN. CLAUDE.md 4.6 requires it to be LAN-bound and
# never publicly reachable, so an empty variable must fail CLOSED.
#
# Order of preference:
#   1. VIRALLAB_BIND_IP, if it is set AND is not a wildcard
#   2. the Tailscale address, which is the intended interface
#   3. 127.0.0.1 - useless to the worker, but safe, and loud in the log
#
# 0.0.0.0 is never chosen, and is rejected even if explicitly configured.
set -euo pipefail
cd /home/rastlyn/virallab
set -a; . ./.env; set +a
mkdir -p media logs

BIND="${VIRALLAB_BIND_IP:-}"
case "$BIND" in
  ""|"0.0.0.0"|"::"|"*")
    BIND="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    ;;
esac
if [ -z "$BIND" ]; then
  echo "WARNING: no Tailscale address and no usable VIRALLAB_BIND_IP;" \
       "binding 127.0.0.1 only. The GPU worker will not be able to reach" \
       "this API until the address is resolvable." >&2
  BIND="127.0.0.1"
fi
echo "binding $BIND:8443"

exec .venv/bin/python -m uvicorn server.api.app:app \
  --host "$BIND" --port 8443 \
  --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt
