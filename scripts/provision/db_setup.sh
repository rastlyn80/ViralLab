#!/usr/bin/env bash
set -euo pipefail
DBPW='vWma4wmOIuOIraG0MClI4T20wFwTVJfa'
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='virallab') THEN
    CREATE ROLE virallab LOGIN PASSWORD '$DBPW';
  END IF;
END \$\$;
SQL
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='virallab'" | grep -q 1; then
  sudo -u postgres createdb -O virallab virallab
fi
sudo -u postgres psql -d virallab -v ON_ERROR_STOP=1   -c 'CREATE EXTENSION IF NOT EXISTS vector;'   -c 'GRANT ALL ON SCHEMA public TO virallab;' >/dev/null
echo "--- extensions ---"
sudo -u postgres psql -d virallab -tAc "SELECT extname||' '||extversion FROM pg_extension ORDER BY 1;"
echo "--- connectivity as virallab ---"
PGPASSWORD="$DBPW" psql -h 127.0.0.1 -U virallab -d virallab -tAc "SELECT 'connected as '||current_user||' to '||current_database();"
