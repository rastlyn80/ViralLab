"""Restart the API through systemd, and leave exactly one running.

Runs from Windows.

## What this got wrong for a fortnight

The server is a systemd unit — `virallab-api.service`, with
`Restart=always` and `RestartSec=10`. This script did not know
that. It killed the running python and started **its own** copy
with `setsid nohup`, which worked, in the sense that the page came
back.

systemd then spent the next two weeks trying to start the service
it owns, failing to bind a port held by a process it had never
heard of, waiting ten seconds, and trying again. **24,255 times.**

That would have been merely wasteful if uvicorn bound its port
before running the application lifespan. It does not. So every one
of those doomed processes ran the full startup — reconciled the
operations table, started the schedulers, and ticked the operation
schedule, which queued a `read_repertoire` run — and then died
about twelve seconds later without ever executing it.

The owner found it the way these things are always found: a button
greyed out as "Already running", behind **8,181 rows** of work that
never ran. Nothing was corrupted and nothing was spent. The
nightly measurement passes simply stopped happening on 1 November
and the history was too noisy to notice.

## So: ask systemd

`systemctl restart` stops the old process, waits for it, starts
one, and is the only thing that can do that without racing the
supervisor. The manual path is kept **only** for a machine with no
unit installed, and it says loudly which path it took — two ways
to start a server is how this happened, and the one thing worse
than the wrong default is a silent choice between them.

    python scripts/provision/restart_api.py
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import remote  # noqa: E402

UNIT = "virallab-api"

PATTERN = "[u]vicorn server.api.app"
#: The fallback, for a machine where the unit is not installed. It
#: is not the normal path and must not become one: a hand-started
#: server is invisible to the supervisor, which is the whole fault
#: described above.
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
#: wrapper that starts uvicorn contains the same words — so a
#: healthy single server counts as two and this script reported a
#: clean restart as a failure. Matching command lines that BEGIN
#: with a python path counts servers.
_COUNT = (r"ps -eo args= | grep -cE "
          r"'^[^ ]*python[0-9.]* -m uvicorn server\.api\.app'")


def ask(client, command: str) -> str:
    _, out, _ = client.exec_command(command)
    return out.read().decode(errors="replace").strip()


def ask_as_root(client, command: str) -> tuple[int, str]:
    """Run something that needs root, through `remote.run`.

    `sudo` on this machine wants a password, and a bare
    `exec_command("sudo systemctl restart ...")` has no terminal to
    ask on — so it fails, prints "interactive authentication is
    required" to stderr, and returns. This script read only stdout
    and announced a clean restart that had not happened.
    `remote.run` already knows how to feed the password in.
    """
    code, out, err = remote.run(client, command, sudo=True, timeout=180)
    return code, (out + err).strip()


def count(client) -> int:
    try:
        return int(ask(client, _COUNT) or 0)
    except ValueError:
        return 0


def listeners(client) -> int:
    try:
        return int(ask(client, "ss -lntp 2>/dev/null | grep -c ':8443 '")
                   or 0)
    except ValueError:
        return 0


def unit_exists(client) -> bool:
    """Whether systemd owns this service on this machine."""
    state = ask(client,
                f"systemctl show {UNIT} -p LoadState --value 2>/dev/null")
    return state == "loaded"


def restarts(client) -> int:
    try:
        return int(ask(client,
                       f"systemctl show {UNIT} -p NRestarts --value") or 0)
    except ValueError:
        return 0


def wait_for(fn, want: int, *, seconds: int = 30) -> int:
    deadline = time.time() + seconds
    value = fn()
    while value != want and time.time() < deadline:
        time.sleep(1.5)
        value = fn()
    return value


def through_systemd(client) -> int:
    before = restarts(client)
    print(f"restarting {UNIT} (systemd has restarted it "
          f"{before} times so far)")
    started_before = ask(
        client, f"systemctl show {UNIT} -p ExecMainStartTimestamp --value")
    code, said = ask_as_root(client, f"systemctl restart {UNIT}")
    if code != 0:
        print(f"FAILED: could not restart the unit: {said[:300]}")
        return 1

    listening = wait_for(lambda: listeners(client), 1, seconds=60)
    running = wait_for(lambda: count(client), 1, seconds=15)
    state = ask(client, f"systemctl show {UNIT} -p ActiveState --value")
    after = restarts(client)

    started_after = ask(
        client, f"systemctl show {UNIT} -p ExecMainStartTimestamp --value")

    print(f"uvicorn processes: {running}; listeners on 8443: "
          f"{listening}; unit is {state}")
    if started_after == started_before:
        # The reason this check exists: the first version used
        # `exec_command("sudo ...")`, which cannot answer a
        # password prompt, read only stdout, saw nothing, and
        # reported a clean restart of a process that had been
        # running for an hour. A restart that did not restart
        # anything is the one failure this script must never
        # report as success.
        print(f"FAILED: the unit never restarted — it has been up "
              f"since {started_before}. The new code is not running.")
        return 1
    if running != 1 or listening != 1 or state != "active":
        print("FAILED: expected one process, one listener, and an "
              "active unit")
        return 1
    # A unit that is restarting in a loop looks healthy for the
    # second you measure it. The restart counter does not.
    time.sleep(12)
    climbing = restarts(client) - after
    if climbing:
        print(f"FAILED: the unit restarted {climbing} more time(s) in "
              f"the last twelve seconds. Something else is holding "
              f"port 8443 — look for a server started by hand.")
        return 1
    print("API restarted cleanly")
    return 0


def by_hand(client) -> int:
    print(f"WARNING: no {UNIT} unit on this machine, so the server is "
          f"being started by hand. It will not come back after a "
          f"reboot and nothing will restart it if it dies.")
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
    if wait_for(lambda: listeners(client), 0, seconds=30):
        print("FAILED: port 8443 is still held by something else")
        return 1

    client.exec_command(START)
    # Wait for the LISTENER, not the process. uvicorn exists for a
    # few seconds before it binds, so checking the process first
    # and the port immediately after reported a healthy restart as
    # a failure — the kind of false alarm that teaches you to
    # ignore the check.
    listening = wait_for(lambda: listeners(client), 1, seconds=45)
    running = wait_for(lambda: count(client), 1, seconds=10)
    print(f"uvicorn processes: {running}; listeners on 8443: {listening}")
    if running != 1 or listening != 1:
        print("FAILED: expected exactly one of each")
        return 1
    print("API restarted cleanly (by hand)")
    return 0


def main() -> int:
    client = remote.connect()
    try:
        if unit_exists(client):
            return through_systemd(client)
        return by_hand(client)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
