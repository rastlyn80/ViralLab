"""Restart the API and leave exactly one running. Runs from Windows.

Written after the third time a restart left two uvicorns alive.
That is not cosmetic: each one runs the operation scheduler, so two
processes started the same nightly job within four seconds of each
other, and each failed bind ran the lifespan - which reconciles
operations and so marked a healthy running collection as
"Interrupted".

The sequence that actually works, rather than the one that usually
does:

1. kill, and **wait for the process to be gone**, not for a sleep
   to elapse;
2. **wait for the port to be released**, which happens after the
   process exits, not with it;
3. start one;
4. **verify exactly one is listening**, and say so.

    python scripts/provision/restart_api.py
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import remote  # noqa: E402

PATTERN = "[u]vicorn server.api.app"
START = (
    "cd /home/rastlyn/virallab && set -a && . ./.env && set +a && "
    "setsid nohup .venv/bin/python -m uvicorn server.api.app:app "
    "--host gabry-ubuntu.tailc6fc62.ts.net --port 8443 "
    "--ssl-keyfile certs/server.key --ssl-certfile certs/server.crt "
    ">> logs/api.log 2>&1 < /dev/null &"
)


#: Count the PYTHON process, not the shell that launched it.
#:
#: `pgrep -f` matches the whole command line, and the `bash -c ...`
#: wrapper that starts uvicorn contains the same words - so a
#: healthy single server counts as two and this script reported a
#: clean restart as a failure. Matching command lines that BEGIN
#: with a python path counts servers.
_COUNT = (r"ps -eo args= | grep -cE "
          r"'^[^ ]*python[0-9.]* -m uvicorn server\.api\.app'")


def count(client, pattern: str = PATTERN) -> int:
    _, out, _ = client.exec_command(_COUNT)
    try:
        return int(out.read().decode().strip() or 0)
    except ValueError:
        return 0


def listeners(client) -> int:
    _, out, _ = client.exec_command(
        "ss -lntp 2>/dev/null | grep -c ':8443 '")
    try:
        return int(out.read().decode().strip() or 0)
    except ValueError:
        return 0


def wait_for(fn, want: int, *, seconds: int = 30) -> int:
    deadline = time.time() + seconds
    value = fn()
    while value != want and time.time() < deadline:
        time.sleep(1.5)
        value = fn()
    return value


def main() -> int:
    client = remote.connect()

    client.exec_command(f"pkill -f '{PATTERN}'")
    remaining = wait_for(lambda: count(client), 0)
    if remaining:
        print(f"forcing: {remaining} still alive after SIGTERM")
        client.exec_command(f"pkill -9 -f '{PATTERN}'")
        remaining = wait_for(lambda: count(client), 0)
        if remaining:
            print(f"FAILED: {remaining} uvicorn(s) will not die")
            return 1

    # The port outlives the process by a moment. Starting into a
    # held port is what produced the bind-error restart loop.
    free = wait_for(lambda: listeners(client), 0, seconds=30)
    if free:
        print("FAILED: port 8443 is still held by something else")
        return 1

    client.exec_command(START)
    # Wait for the LISTENER, not the process. uvicorn exists for a
    # few seconds before it binds, so checking the process first
    # and the port immediately after reported a healthy restart as
    # a failure - which is the kind of false alarm that teaches you
    # to ignore the check.
    listening = wait_for(lambda: listeners(client), 1, seconds=45)
    running = count(client)

    print(f"uvicorn processes: {running}; listeners on 8443: {listening}")
    if running != 1 or listening != 1:
        print("FAILED: expected exactly one of each")
        return 1
    print("API restarted cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
