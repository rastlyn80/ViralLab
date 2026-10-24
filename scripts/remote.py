"""Run commands on the Ubuntu server over SSH.

Credentials come from the gitignored .env. Prefers key auth and falls back to
the password, so once the key is installed the password stops being used.

    python scripts/remote.py "uname -a"
    python scripts/remote.py --sudo "apt-get update"
    python scripts/remote.py --put local.py /remote/path.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / ".ssh" / "virallab_ed25519"


def env() -> dict[str, str]:
    out = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v
    return out


def connect() -> paramiko.SSHClient:
    e = env()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(hostname=e["UBUNTU_HOST"], username=e["UBUNTU_USER"],
                  timeout=30, look_for_keys=False, allow_agent=False)
    if KEY.exists():
        try:
            c.connect(pkey=paramiko.Ed25519Key.from_private_key_file(str(KEY)),
                      **kwargs)
            return c
        except paramiko.AuthenticationException:
            pass  # key not installed yet; fall back
    c.connect(password=e["UBUNTU_PASSWORD"], **kwargs)
    return c


def run(c: paramiko.SSHClient, cmd: str, *, sudo: bool = False,
        timeout: int = 600) -> tuple[int, str, str]:
    if sudo:
        # -S reads the password from stdin; -p '' keeps the prompt out of stderr.
        cmd = f"sudo -S -p '' bash -lc {shell_quote(cmd)}"
    stdin, out, err = c.exec_command(cmd, timeout=timeout, get_pty=False)
    if sudo:
        stdin.write(env()["UBUNTU_PASSWORD"] + "\n")
        stdin.flush()
    o, e = out.read().decode(errors="replace"), err.read().decode(errors="replace")
    return out.channel.recv_exit_status(), o, e


def shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?")
    ap.add_argument("--sudo", action="store_true")
    ap.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"))
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    c = connect()
    try:
        if args.put:
            sftp = c.open_sftp()
            sftp.put(args.put[0], args.put[1])
            sftp.close()
            print(f"uploaded {args.put[0]} -> {args.put[1]}")
            return 0
        if not args.command:
            ap.error("need a command or --put")
        rc, out, err = run(c, args.command, sudo=args.sudo,
                           timeout=args.timeout)
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        if err:
            print(err, end="" if err.endswith("\n") else "\n", file=sys.stderr)
        return rc
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
