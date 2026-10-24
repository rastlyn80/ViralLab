#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/virallab"
set -a; . ./.env; set +a
mkdir -p media logs
# Bind to the tailnet address only: 0.0.0.0 would also expose the API on the
# home LAN, and the worker reaches us over Tailscale.
BIND_IP="$(tailscale ip -4 2>/dev/null | head -1 || echo 127.0.0.1)"
echo "binding to $BIND_IP"
pkill -f 'uvicorn server.api.app' 2>/dev/null || true
sleep 1
nohup .venv/bin/python -m uvicorn server.api.app:app \
  --host "$BIND_IP" --port 8443 \
  --ssl-keyfile certs/server.key --ssl-certfile certs/server.crt \
  > logs/api.log 2>&1 &
sleep 4
echo "--- listening? ---"
ss -ltnp 2>/dev/null | grep 8443 || echo "NOT listening"
echo "--- local TLS health check ---"
curl -sk --max-time 10 "https://$BIND_IP:8443/health" || echo "health check failed"
echo
tail -3 logs/api.log
