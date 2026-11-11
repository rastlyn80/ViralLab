# Putting it all back after the wipe

**Backup taken 11 November 2026.** Verified: every file's checksum
matched after the transfer, and the database dump was restored into
a scratch copy and compared table by table — 38 tables, every row
count identical to the live one.

**Where it is:** `D:\ViralLab-backup-20261111` on the Windows
machine. 1.71 GB.

```
virallab.dump        the database — 10,326 videos, 454,446
                     measurements, your 225 labels, the spending
                     ledger, everything
globals.sql          the database roles and their passwords
virallab.tar.gz      1.65 GB — your 483 video files, .env,
                     certificates, config, logs
Phonefarm.tar.gz     source only
auto-poster.tar.gz   source only
system/              the service definition, its launcher, the
                     crontab
MANIFEST.txt         checksums
```

> **This directory is as sensitive as the machine was.** `.env` is
> inside `virallab.tar.gz` and holds the live Apify token, the
> Anthropic key, the database password and the interface password.

**Not backed up, by your decision:** `leon` and its five cron jobs,
the `statuvox_marketing` database, `Downloads`, and the Claude Code
history on that machine.

---

## The one thing that will save you an afternoon

**Create the user `rastlyn` on the new system, with the same home
directory `/home/rastlyn`.**

93 places across 65 files have `/home/rastlyn` written into them —
the service definition, every provisioning script, the launcher.
Matching the username makes all of that work untouched. A different
username means finding and changing all 93.

---

## 1. Before you wipe — the last check

Run this on the Ubuntu machine and make sure it says nothing is
missing. If it does, stop and tell me.

```bash
ssh rastlyn@gabry-ubuntu.tailc6fc62.ts.net "cd /home/rastlyn/backup-20261111 && sha256sum -c <(awk 'NF==3 && length($1)==64 {print $1\"  \"$3}' MANIFEST.txt)"
```

Nothing else needs doing on that machine. The backup is already on
Windows and verified; the copy still on Ubuntu is a spare.

## 2. On the new system — install what it needs

Arch package names; adjust if the new system is something else.

```bash
sudo pacman -S --needed postgresql python python-pip ffmpeg yt-dlp tailscale git base-devel
```

PostgreSQL must be **18 or newer** — the dump came from 18.6 and
will not load into anything older.

```bash
sudo -u postgres initdb -D /var/lib/postgres/data
sudo systemctl enable --now postgresql tailscaled
sudo tailscale up
```

## 3. Put the files back

```bash
sudo useradd -m -s /bin/bash rastlyn && sudo passwd rastlyn
```

Then, as `rastlyn`, with the backup copied across from Windows:

```bash
cd /home/rastlyn && tar -xzf virallab.tar.gz && tar -xzf Phonefarm.tar.gz && tar -xzf auto-poster.tar.gz
```

## 4. Put the database back

`globals.sql` recreates the roles **with their original
passwords**, which is what lets the app log in without editing
anything.

```bash
sudo -u postgres psql -f /home/rastlyn/globals.sql
```

```bash
sudo -u postgres createdb -O virallab virallab && pg_restore --no-owner --no-privileges -d "postgresql://virallab@127.0.0.1:5432/virallab" /home/rastlyn/virallab.dump
```

Expect one harmless complaint about the `vector` extension. Nothing
currently uses it — I checked, there are no vector columns — but
the project plans to, so install it when convenient:

```bash
sudo pacman -S --needed pgvector && sudo -u postgres psql -d virallab -c "create extension if not exists vector"
```

Check it landed:

```bash
psql "postgresql://virallab@127.0.0.1:5432/virallab" -c "select (select count(*) from videos) as videos, (select count(*) from video_features) as measurements, (select count(*) from label_reviews) as your_labels"
```

**It should say 10,326 videos, 454,446 measurements, 225 labels.**
If those numbers match, nothing was lost.

## 5. Rebuild the environment and start the server

```bash
cd /home/rastlyn/virallab && python -m venv .venv && .venv/bin/pip install -r requirements-server.txt
```

```bash
sudo cp /home/rastlyn/backup-20261111/system/virallab-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now virallab-api
```

The certificates came back in `virallab.tar.gz`, so the Windows
worker's pinned authority still matches and needs no change.

## 6. Check it from Windows

```bash
cd "C:\Coding Projects\ViralLab" && .venv312/Scripts/python.exe scripts/provision/restart_api.py
```

Then open <https://gabry-ubuntu.tailc6fc62.ts.net:8443>. If the
machine's Tailscale name changed, update `UBUNTU_HOST` and
`VIRALLAB_SERVER_URL` in both `.env` files.

## 7. The crontab

`system/crontab.txt` is saved but **every entry in it is for
`leon`**, which you chose not to keep. Restore it only if you
change your mind about leon; otherwise ignore the file.

---

## If something is wrong

The backup is not consumed by restoring it — the files on Windows
stay exactly as they are, and the dump can be restored again as
many times as needed. The code itself is in GitHub on
`m1-measurement-integrity`, so a broken checkout is a `git clone`
away and loses nothing.

The one genuinely irreplaceable thing in here is the database:
nine months of collection, the measurements derived from videos
that other people may since have deleted, and the 225 labels you
wrote by hand. That is what `virallab.dump` holds, and it has been
restored once already to prove it works.
