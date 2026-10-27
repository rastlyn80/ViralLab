"""Sync server-side source to the Ubuntu box and run migrations.

Deliberately not a git clone: the repository is private and putting a GitHub
credential on the server would be a new secret to manage for no benefit. This
copies only what the server half actually runs.

    python scripts/deploy.py            # sync + migrate
    python scripts/deploy.py --no-migrate
"""

from __future__ import annotations

import argparse
import secrets
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, env, run  # noqa: E402

REMOTE_ROOT = "/home/rastlyn/virallab"

#: Server-side only. The GPU worker's own deps and the probes stay local.
#: webui/ is included because the SPA has no build step: deploying the
#: interface is copying the files (ADR 0006).
INCLUDE_DIRS = ["server", "shared", "config", "tests", "webui"]
#: gpu_worker is NOT synced: it runs on Windows only.
INCLUDE_FILES = ["alembic.ini", "pytest.ini", "requirements-server.txt"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", "node_modules"}
#: Never deployed. `config/spending.local.yaml` is the operator's
#: runtime money authorisation for THAT machine; shipping it would
#: either wipe an approved cap or grant one nobody approved.
EXCLUDE_NAMES = {"spending.local.yaml"}


def walk(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in EXCLUDE_PARTS for part in p.parts):
            continue
        if p.name in EXCLUDE_NAMES:
            continue
        if p.is_file() and p.suffix not in (".pyc",):
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-migrate", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="upload every file and reinstall dependencies, "
                         "ignoring the size/mtime and requirements-hash "
                         "shortcuts")
    ap.add_argument("--test", action="store_true",
                    help="run the test suite on the server after deploying")
    args = ap.parse_args()

    local_root = Path(__file__).resolve().parent.parent
    e = env()
    c = connect()
    try:
        sftp = c.open_sftp()

        def mkdirs(remote_dir: str) -> None:
            parts, cur = remote_dir.strip("/").split("/"), ""
            for part in parts:
                cur = f"{cur}/{part}"
                try:
                    sftp.stat(cur)
                except FileNotFoundError:
                    sftp.mkdir(cur)

        mkdirs(REMOTE_ROOT)

        def changed(local_file: Path, remote_path: str) -> bool:
            """Upload only what differs.

            Pushing all 145 files for a one-line edit made every deploy a
            round of SFTP writes, and a deploy happens many times an hour
            during development. Size plus mtime is the cheap comparison
            rsync uses; it can miss a same-size same-mtime edit, which is
            why `--full` exists.
            """
            if args.full:
                return True
            try:
                st = sftp.stat(remote_path)
            except FileNotFoundError:
                return True
            local_st = local_file.stat()
            if st.st_size != local_st.st_size:
                return True
            # One second of slack: SFTP mtimes are whole seconds.
            return abs((st.st_mtime or 0) - local_st.st_mtime) > 1

        sent = skipped = 0
        for d in INCLUDE_DIRS:
            src = local_root / d
            if not src.is_dir():
                continue
            for f in walk(src):
                rel = f.relative_to(local_root).as_posix()
                remote = f"{REMOTE_ROOT}/{rel}"
                if not changed(f, remote):
                    skipped += 1
                    continue
                mkdirs(f"{REMOTE_ROOT}/{Path(rel).parent.as_posix()}")
                sftp.put(str(f), remote)
                # Carry the mtime across, or every file looks changed next
                # time and the comparison buys nothing.
                st = f.stat()
                sftp.utime(remote, (st.st_atime, st.st_mtime))
                sent += 1
        # The systemd service launcher lives next to the code it starts.
        # Shipped explicitly because scripts/ as a whole is not deployed.
        launcher = local_root / "scripts" / "provision" / "run_api_svc.sh"
        if launcher.is_file():
            sftp.put(str(launcher), f"{REMOTE_ROOT}/run_api_svc.sh")
            sent += 1
        for name in INCLUDE_FILES:
            f = local_root / name
            if f.is_file():
                sftp.put(str(f), f"{REMOTE_ROOT}/{name}")
                sent += 1
        print(f"synced {sent} changed file(s), skipped {skipped} unchanged -> {REMOTE_ROOT}")

        # Server-side .env, written with restrictive permissions. Only the
        # values the server half needs; the worker's own secrets stay local.
        db_url = (f"postgresql+psycopg://virallab:{e['SERVER_DB_PASSWORD']}"
                  f"@127.0.0.1:5432/virallab")
        # Swap only the trailing database name. A naive replace would also hit
        # the username in "://virallab:", which is exactly what it did once.
        head, _, _name = db_url.rpartition("/")
        test_db_url = f"{head}/virallab_test"
        # Web-UI password. Generated once if absent and appended to the
        # LOCAL .env (gitignored) rather than printed, so the credential
        # never appears in a transcript, a log or this tool's output.
        ui_password = e.get("SERVER_UI_PASSWORD", "").strip()
        if not ui_password:
            ui_password = secrets.token_urlsafe(18)
            with (local_root / ".env").open("a", encoding="utf-8") as fh:
                fh.write(f"\nSERVER_UI_PASSWORD={ui_password}\n")
            print("generated a web-UI password and appended it to .env as "
                  "SERVER_UI_PASSWORD (deliberately not printed here)")

        envfile = "\n".join([
            f"VIRALLAB_DB_URL={db_url}",
            # Separate disposable database: the suite truncates tables and
            # must never be able to reach the application database.
            f"VIRALLAB_TEST_DB_URL={test_db_url}",
            f"VIRALLAB_MEDIA_SECRET={e['SERVER_MEDIA_SECRET']}",
            # Without this the web UI is readable by anything that can reach
            # the tailnet address; app startup logs a warning if it is unset.
            f"VIRALLAB_UI_PASSWORD={ui_password}",
            f"VIRALLAB_WORKER_TOKENS=gpu-1:{e['SERVER_WORKER_TOKEN']}",
            f"VIRALLAB_PUBLIC_BASE_URL=https://{e['UBUNTU_HOST']}:8443",
            f"VIRALLAB_MEDIA_ROOT={REMOTE_ROOT}/media",
            # Competitor bytes, held for hours not forever. A SIBLING of
            # media/, never inside it: the signed-URL endpoint contains
            # paths to MEDIA_ROOT, so keeping quarantine outside means
            # that containment check refuses third-party media too,
            # independently of the is_own_account check in the frames
            # endpoint. `transient.assert_outside_media_root` fails loudly
            # if this is ever pointed somewhere inside.
            f"VIRALLAB_QUARANTINE_ROOT={REMOTE_ROOT}/media_transient",
            # The tailnet address. run_api_svc.sh re-derives this if it is
            # missing and refuses to bind 0.0.0.0 either way, but recording
            # it keeps the intended interface explicit.
            f"VIRALLAB_BIND_IP={e.get('UBUNTU_HOST','')}",
            "VIRALLAB_MEDIA_TTL=900",
            # Ubuntu is the authoritative collector: it needs the app
            # credentials to refresh the rotating TikTok token (ADR 0005).
            # Written via SFTP, so no secret ever reaches a shell command
            # line, a process list or shell history.
            f"TIKTOK_CLIENT_KEY={e.get('TIKTOK_CLIENT_KEY','')}",
            f"TIKTOK_CLIENT_SECRET={e.get('TIKTOK_CLIENT_SECRET','')}",
            f"TIKTOK_REDIRECT_URI={e.get('TIKTOK_REDIRECT_URI','')}",
            # Meta app credentials. The server is the authoritative
            # collector and needs these to refresh the Instagram token;
            # without them collection dies silently at the next refresh.
            f"META_APP_ID={e.get('META_APP_ID','')}",
            f"META_APP_SECRET={e.get('META_APP_SECRET','')}",
            # Third-party research provider. Only ever USED when the owner
            # has also authorised apify and a non-zero cap in the spending
            # override - a token alone buys nothing.
            f"APIFY_TOKEN={e.get('APIFY_TOKEN','')}",
            "VIRALLAB_COLLECTION_ENABLED=1",
            "",
        ])
        # Preserve keys this script does not manage.
        #
        # `.env` used to be written wholesale from the template above, which
        # silently deleted anything set another way - and the credentials
        # dashboard writes straight into this file. The owner pasted an
        # APIFY_TOKEN, the next deploy removed it, and research fell back to
        # fixture data with only a log line to say why.
        #
        # Deploy owns the template keys; everything else belongs to whoever
        # put it there and is carried across untouched.
        managed = {line.split("=", 1)[0] for line in envfile.splitlines()
                   if "=" in line and not line.lstrip().startswith("#")}
        preserved: list[str] = []
        try:
            with sftp.open(f"{REMOTE_ROOT}/.env", "r") as fh:
                existing = fh.read().decode("utf-8", errors="replace")
        except IOError:
            existing = ""
        for line in existing.splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key = line.split("=", 1)[0].strip()
            if key and key not in managed:
                preserved.append(line)
        if preserved:
            envfile = envfile.rstrip("\n") + "\n".join([
                "",
                "",
                "# Set outside deploy.py - by the credentials dashboard or by",
                "# hand - and preserved across deploys. deploy.py owns only",
                "# the keys above.",
            ] + preserved) + "\n"
            # Names only. The values are credentials.
            print(f"preserved {len(preserved)} externally-set key(s): "
                  f"{', '.join(sorted(l.split('=', 1)[0] for l in preserved))}")

        with sftp.open(f"{REMOTE_ROOT}/.env", "w") as fh:
            fh.write(envfile)
        sftp.chmod(f"{REMOTE_ROOT}/.env", stat.S_IRUSR | stat.S_IWUSR)
        sftp.close()
        print("wrote .env (0600)")

        venv = f"{REMOTE_ROOT}/.venv"
        rc, out, err = run(c, f"test -x {venv}/bin/python || python3 -m venv {venv}",
                           timeout=300)
        if rc:
            print(err or out, file=sys.stderr)
            return rc
        # pip was re-resolving the whole requirements file on every
        # deploy - tens of seconds for nothing, many times an hour. Only
        # run it when the requirements actually changed, tracked by a hash
        # stored next to the venv.
        import hashlib
        req_hash = hashlib.sha256(
            (local_root / "requirements-server.txt").read_bytes()).hexdigest()
        stamp = f"{REMOTE_ROOT}/.deps-hash"
        rc, current, _ = run(c, f"cat {stamp} 2>/dev/null || true", timeout=60)
        if args.full or current.strip() != req_hash:
            rc, out, err = run(
                c,
                f"{venv}/bin/pip install -q -r "
                f"{REMOTE_ROOT}/requirements-server.txt",
                timeout=900)
            if rc == 0:
                run(c, f"printf %s {req_hash} > {stamp}", timeout=60)
                print("deps installed")
            else:
                print(err or out)
        else:
            print("deps unchanged, skipped")

        if not args.no_migrate:
            rc, out, err = run(
                c, f"cd {REMOTE_ROOT} && set -a && . ./.env && set +a && "
                   f"{venv}/bin/alembic upgrade head 2>&1 | tail -3",
                timeout=600)
            print(out.strip() or err.strip())
            if rc:
                return rc

        if args.test:
            rc, out, err = run(
                c, f"cd {REMOTE_ROOT} && set -a && . ./.env && set +a && "
                   f"{venv}/bin/python -m pytest tests/ 2>&1 | tail -6",
                timeout=900)
            print(out.strip() or err.strip())
            return rc
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
