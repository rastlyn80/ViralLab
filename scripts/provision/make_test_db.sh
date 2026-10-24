#!/usr/bin/env bash
set -euo pipefail
if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='virallab_test'" | grep -q 1; then
  echo "virallab_test already exists"
else
  sudo -u postgres createdb -O virallab virallab_test
  echo "created virallab_test"
fi
sudo -u postgres psql -d virallab_test -c 'CREATE EXTENSION IF NOT EXISTS vector;' >/dev/null
sudo -u postgres psql -d virallab_test -c 'GRANT ALL ON SCHEMA public TO virallab;' >/dev/null
sudo -u postgres psql -tAc "SELECT datname FROM pg_database WHERE datname LIKE 'virallab%' ORDER BY 1"
