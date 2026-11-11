"""Take everything off this machine before it is wiped. Runs ON Ubuntu.

The owner is replacing Ubuntu with another operating system on the
box that hosts the server, the database and his own media. A wipe
cannot be undone, so this errs towards copying too much.

## What it produces

One dated directory holding, for each thing backed up, an archive
and its SHA-256. Nothing is deleted and nothing is moved: the
machine is left exactly as it was, so a failed transfer costs a
retry rather than the data.

    backup-20261111/
      virallab.dump          pg_dump custom format, restorable
                             into any PostgreSQL >= the source
      globals.sql            roles and their passwords, because a
                             restored dump with no role to own it
                             is not a restore
      virallab.tar.gz        media, .env, certs, config, logs
      Phonefarm.tar.gz       source only
      auto-poster.tar.gz     source only
      system/                the systemd unit, the launcher, the
                             crontab - the parts that live outside
                             the project directory and are the
                             easiest to forget
      MANIFEST.txt           every file, its size and its checksum

## What is deliberately left out

`.venv`, `venv` and `node_modules`. They are rebuildable, they are
the bulk of two of these directories, and - the real reason - they
hold compiled binaries built against Ubuntu's libraries, which
will not run on the new system anyway. A backup that restores
something broken is worse than one that restores nothing.

`media_transient` is excluded too: it is the six-hour quarantine
for other people's video (ADR 0009), it is empty between runs, and
restoring it would resurrect files the project promises to delete.

**`.env` IS included and holds live credentials** - the Apify
token, the Anthropic key, the database password, the interface
password. The archive is as sensitive as the machine was.

    python backup_machine.py --out /home/rastlyn/backup-20261111
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path("/home/rastlyn")

#: Directory -> what to leave out of its archive.
PROJECTS: dict[str, tuple[str, ...]] = {
    "virallab": (".venv", "media_transient", "__pycache__",
                 ".pytest_cache", ".git"),
    "Phonefarm": ("venv", ".venv", "__pycache__", "node_modules",
                  ".git"),
    "auto-poster": ("node_modules", ".venv", "venv", "__pycache__",
                    ".git"),
}

#: Databases to dump. `virallab_test` and `virallab_mig` are
#: scratch and are skipped; the real one is not.
DATABASES = ("virallab",)

#: Files outside any project directory that the server needs to
#: work and that nobody remembers until it does not.
SYSTEM_FILES = (
    "/etc/systemd/system/virallab-api.service",
    "/home/rastlyn/virallab/run_api_svc.sh",
)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          check=False, **kw)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def db_url() -> str:
    """The URL psql understands, from the one SQLAlchemy uses."""
    raw = os.environ["VIRALLAB_DB_URL"]
    return raw.replace("postgresql+psycopg://", "postgresql://")


def dump_database(out: Path, name: str) -> list[Path]:
    made = []
    url = db_url()
    target = out / f"{name}.dump"
    # Custom format: compressed, and restorable selectively into a
    # different PostgreSQL. A plain SQL file would also work and
    # could not be restored table by table when something goes
    # wrong halfway.
    proc = run(["pg_dump", "--format=custom", "--no-owner",
                "--no-privileges", "--file", str(target), url])
    if proc.returncode != 0:
        raise SystemExit(f"pg_dump failed: {proc.stderr[:400]}")
    made.append(target)

    # Roles and passwords. A dump restored into a server with no
    # `virallab` role leaves every object ownerless and the app
    # unable to log in, which looks like data loss and is not.
    globals_file = out / "globals.sql"
    proc = run(["sudo", "-n", "-u", "postgres",
                "pg_dumpall", "--globals-only"])
    if proc.returncode == 0 and proc.stdout.strip():
        globals_file.write_text(proc.stdout, encoding="utf-8")
        made.append(globals_file)
    else:
        # Not fatal: the role can be recreated by hand from the
        # password in .env, which is also in this backup. Said
        # out loud rather than failing silently.
        (out / "globals.MISSING.txt").write_text(
            "pg_dumpall --globals-only could not run without a "
            "password for sudo.\n\n"
            "Recreate the role by hand on the new machine:\n"
            "  create role virallab with login password '<the "
            "password in .env>';\n"
            "  create database virallab owner virallab;\n"
            f"\nstderr: {proc.stderr[:300]}\n", encoding="utf-8")
        made.append(out / "globals.MISSING.txt")
    return made


def archive(out: Path, name: str, exclude: tuple[str, ...]) -> Path:
    src = HOME / name
    target = out / f"{name}.tar.gz"
    cmd = ["tar", "-czf", str(target), "-C", str(HOME)]
    for pattern in exclude:
        cmd += ["--exclude", pattern]
    cmd.append(name)
    proc = run(cmd)
    # tar exits 1 for "file changed as we read it", which happens
    # on a live machine and does not corrupt the archive. Only a
    # 2 is fatal.
    if proc.returncode > 1:
        raise SystemExit(f"tar failed for {name}: {proc.stderr[:400]}")
    if proc.returncode == 1:
        print(f"  note: {name} had files change while reading "
              f"(harmless)", flush=True)
    return target


def system_bits(out: Path) -> list[Path]:
    where = out / "system"
    where.mkdir(exist_ok=True)
    made = []
    for path in SYSTEM_FILES:
        p = Path(path)
        if p.exists():
            dest = where / p.name
            shutil.copy2(p, dest)
            made.append(dest)
    crontab = run(["crontab", "-l"])
    if crontab.returncode == 0:
        dest = where / "crontab.txt"
        dest.write_text(crontab.stdout, encoding="utf-8")
        made.append(dest)
    return made


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="",
                    help="comma-separated project names; default all")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    wanted = ([n.strip() for n in args.only.split(",") if n.strip()]
              or list(PROJECTS))

    made: list[Path] = []
    print("database", flush=True)
    for name in DATABASES:
        made += dump_database(out, name)
        print(f"  {name}", flush=True)

    for name in wanted:
        if name not in PROJECTS:
            raise SystemExit(f"unknown project {name!r}")
        if not (HOME / name).is_dir():
            print(f"  skipped {name}: not on this machine", flush=True)
            continue
        print(f"archiving {name}", flush=True)
        made.append(archive(out, name, PROJECTS[name]))

    print("system files", flush=True)
    made += system_bits(out)

    lines = [
        "ViralLab machine backup",
        f"taken {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
        f"from {os.uname().nodename}",
        "",
        "SENSITIVE: virallab.tar.gz contains .env, which holds live",
        "API keys and passwords. Treat this directory like the",
        "machine it came from.",
        "",
    ]
    total = 0
    for path in sorted(made):
        size = path.stat().st_size
        total += size
        lines.append(f"{sha256(path)}  {size:>13,}  "
                     f"{path.relative_to(out)}")
    lines += ["", f"{len(made)} files, {total / 1e9:.2f} GB"]
    (out / "MANIFEST.txt").write_text("\n".join(lines) + "\n",
                                      encoding="utf-8")

    print(json.dumps({
        "out": str(out), "files": len(made),
        "bytes": total, "gb": round(total / 1e9, 2),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
