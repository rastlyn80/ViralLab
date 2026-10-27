#!/usr/bin/env bash
# Validate a2f49c81b73e (feature_evidence) on the scratch database:
# empty -> head -> downgrade -1 -> head, which is how every migration in
# this project has been checked. Runs ON Ubuntu.
#
# It uses virallab_test, which the suite recreates from Base.metadata on
# every run, so wiping it costs nothing. Do not run this while a suite
# is running: both would be pointing at the same database.
set -e
cd /home/rastlyn/virallab
set -a; . ./.env; set +a

TESTURL="${VIRALLAB_DB_URL%/virallab}/virallab_test"

sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='virallab_test'" | grep -q 1 \
  || sudo -u postgres createdb -O virallab virallab_test
sudo -u postgres psql -d virallab_test -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO virallab;" >/dev/null
sudo -u postgres psql -d virallab_test -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null

export VIRALLAB_DB_URL="$TESTURL"

echo "=== empty -> head ==="
./.venv/bin/python -m alembic upgrade head 2>&1 | tail -2
echo "--- feature_evidence present (expect 1) ---"
sudo -u postgres psql -d virallab_test -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name='feature_evidence'"
echo "--- its constraints ---"
sudo -u postgres psql -d virallab_test -tAc "select conname from pg_constraint where conrelid='feature_evidence'::regclass order by 1" | tr '\n' ' '; echo
echo "--- its indexes ---"
sudo -u postgres psql -d virallab_test -tAc "select indexname from pg_indexes where tablename='feature_evidence' order by 1" | tr '\n' ' '; echo

echo "=== head -> downgrade -1 ==="
./.venv/bin/python -m alembic downgrade -1 2>&1 | tail -2
echo "--- feature_evidence gone (expect 0) ---"
sudo -u postgres psql -d virallab_test -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name='feature_evidence'"

echo "=== downgrade -> head ==="
./.venv/bin/python -m alembic upgrade head 2>&1 | tail -2
echo "--- back again (expect 1) ---"
sudo -u postgres psql -d virallab_test -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name='feature_evidence'"

echo "=== the uniqueness constraint actually bites ==="
sudo -u postgres psql -d virallab_test -tAc "
insert into feature_evidence (feature, method_version, outcome, population, n, analysis)
values ('f','mv','o','p',10,'a');" >/dev/null
if sudo -u postgres psql -d virallab_test -tAc "
insert into feature_evidence (feature, method_version, outcome, population, n, analysis)
values ('f','mv','o','p',99,'a');" >/dev/null 2>&1; then
  echo "FAIL: a duplicate finding was accepted"
  exit 1
else
  echo "ok: a second row for the same finding is refused"
fi

echo "=== the check constraints bite ==="
if sudo -u postgres psql -d virallab_test -tAc "
insert into feature_evidence (feature, method_version, outcome, population, n, analysis, rho)
values ('g','mv','o','p',10,'a',2.0);" >/dev/null 2>&1; then
  echo "FAIL: rho=2.0 was accepted"
  exit 1
else
  echo "ok: rho outside [-1,1] is refused"
fi
if sudo -u postgres psql -d virallab_test -tAc "
insert into feature_evidence (feature, method_version, outcome, population, n, analysis, direction)
values ('h','mv','o','p',10,'a','sideways');" >/dev/null 2>&1; then
  echo "FAIL: an unknown direction was accepted"
  exit 1
else
  echo "ok: an unknown direction is refused"
fi

sudo -u postgres psql -d virallab_test -c "delete from feature_evidence" >/dev/null
echo "=== all checks passed ==="
