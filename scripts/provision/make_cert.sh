#!/usr/bin/env bash
# Self-signed TLS cert for the ViralLab API, pinned by the worker.
# SAN covers the tailnet hostname and IP so either form validates.
set -euo pipefail
D="$HOME/virallab/certs"; mkdir -p "$D"; cd "$D"
HOST="gabry-ubuntu.tailc6fc62.ts.net"
TSIP="$(tailscale ip -4 2>/dev/null | head -1 || echo 127.0.0.1)"
if [ -f server.crt ] && openssl x509 -checkend 2592000 -noout -in server.crt 2>/dev/null; then
  echo "existing cert still valid for >30 days"
else
  openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
    -keyout server.key -out server.crt \
    -subj "/CN=$HOST/O=ViralLab" \
    -addext "subjectAltName=DNS:$HOST,IP:$TSIP,IP:127.0.0.1" \
    -addext "keyUsage=digitalSignature,keyEncipherment" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null
  chmod 600 server.key; chmod 644 server.crt
  echo "generated new cert"
fi
openssl x509 -in server.crt -noout -subject -enddate -ext subjectAltName
echo "FINGERPRINT: $(openssl x509 -in server.crt -noout -fingerprint -sha256 | cut -d= -f2)"
