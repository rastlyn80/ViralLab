"""Sync server-side source to the Ubuntu box and run migrations.

Deliberately not a git clone: the repository is private and putting a GitHub
credential on the server would be a new secret to manage for no benefit. This
copies only what the server half actually runs.

    python scripts/deploy.py            # sync + migrate
    python scripts/deploy.py --no-migrate
"""

from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from remote import connect, env, run  # noqa: E402

REMOTE_ROOT = "/home/rastlyn/virallab"

#: Server-side only. The GPU worker's own deps and the probes stay local.
INCLUDE_DIRS = ["server", "shared", "config", "tests"]
INCLUDE_FILES = ["alembic.ini", "pytest.ini", "requirements-server.txt"]
EXCLUDE_PARTS = {"__pycache__", ".pytest_cache", "node_modules"}


def walk(root: Path):
    for p in sorted(root.rglob("*")):
        if any(part in EXCLUDE_PARTS for part in p.parts):
            continue
        if p.is_file() and p.suffix not in (".pyc",):
            yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-migrate", action="store_true")
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
        sent = 0
        for d in INCLUDE_DIRS:
            src = local_root / d
            if not src.is_dir():
                continue
            for f in walk(src):
                rel = f.relative_to(local_root).as_posix()
                mkdirs(f"{REMOTE_ROOT}/{Path(rel).parent.as_posix()}")
                sftp.put(str(f), f"{REMOTE_ROOT}/{rel}")
                sent += 1
        for name in INCLUDE_FILES:
            f = local_root / name
            if f.is_file():
                sftp.put(str(f), f"{REMOTE_ROOT}/{name}")
                sent += 1
        print(f"synced {sent} files -> {REMOTE_ROOT}")

        # Server-side .env, written with restrictive permissions. Only the
        # values the server half needs; the worker's own secrets stay local.
        db_url = (f"postgresql+psycopg://virallab:{e['SERVER_DB_PASSWORD']}"
                  f"@127.0.0.1:5432/virallab")
        envfile = "\n".join([
            f"VIRALLAB_DB_URL={db_url}",
            f"VIRALLAB_MEDIA_SECRET={e['SERVER_MEDIA_SECRET']}",
            f"VIRALLAB_WORKER_TOKENS=gpu-1:{e['SERVER_WORKER_TOKEN']}",
            f"VIRALLAB_PUBLIC_BASE_URL=https://{e['UBUNTU_HOST']}:8443",
            f"VIRALLAB_MEDIA_ROOT={REMOTE_ROOT}/media",
            "VIRALLAB_MEDIA_TTL=900",
            "",
        ])
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
        rc, out, err = run(
            c, f"{venv}/bin/pip install -q -r {REMOTE_ROOT}/requirements-server.txt",
            timeout=900)
        print("deps installed" if rc == 0 else (err or out))

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
