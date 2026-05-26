# particle_art local scripts

`bin/pa` is the main CLI for everyday operations — no Claude needed.

## Setup

```bash
# Make sure it's executable (done once)
chmod +x bin/pa

# Optional: add to PATH for this shell session
export PATH="$PATH:$(pwd)/bin"
# Then call it as just: pa <command>
```

---

## Commands

### `view <code>` — open a piece in browser

Starts a local HTTP server (port 8787, auto-increments if busy) and opens the piece.

```bash
./bin/pa view ac2
./bin/pa view htb
```

### `gallery [page]` — open the gallery in browser

Opens `index.html` by default. Pass any other HTML page name to open that view.

```bash
./bin/pa gallery
./bin/pa gallery best.html
./bin/pa gallery tree.html
./bin/pa gallery compare.html
```

Available pages: `index.html`, `best.html`, `favorites.html`, `all.html`, `compare.html`, `tree.html`, `ideas.html`, `meta.html`, `versions.html`

### `serve [port]` — start HTTP server (foreground)

Keeps the server in the foreground. Use when you want to keep a terminal tab dedicated to serving.

```bash
./bin/pa serve
./bin/pa serve 9000
```

---

### `stats` — gallery health at a glance

```bash
./bin/pa stats
# pieces  : 1026
# thumbs  : 474 (46% coverage)
# queue   : 6 pending directives
# tick    : 2026-05-23T03:11:35  mvh → htb
```

### `log [N]` — recent mutations

Shows the last N entries from `scripts/mutation_log.jsonl` (default 10).

```bash
./bin/pa log
./bin/pa log 20
```

### `recent [N]` — most recently created pieces

Sorted by `created_at` descending. Useful after a cron tick to see what was just generated.

```bash
./bin/pa recent
./bin/pa recent 5
```

### `budget` — token spend this month

Reads `~/.particle_art_budget.json` and summarizes spend by provider for the current month.

```bash
./bin/pa budget
```

---

### `drop <code> [code ...]` — archive and mark pieces as dropped

Moves piece dirs to `pieces/archive/`, marks them `"dropped": true` in `lineage.json`, and commits. **Does not push** — run `git push` yourself when ready.

```bash
./bin/pa drop n5l
./bin/pa drop n5l x7q bad9 foo
```

### `mark <code> keep|drop ["note"]` — update preferences

Writes to `scripts/preferences.json`. The mutation worker reads this to weight parent selection.

```bash
./bin/pa mark zs4 keep
./bin/pa mark zs4 keep "Lorenz attractor — strong mathematical shape"
./bin/pa mark abc drop "noise blob, no form"
```

---

### `find` — search pieces

Filter by direction name (partial match), generation, or both. Excludes dropped pieces by default.

```bash
./bin/pa find --dir differential
./bin/pa find --gen 0
./bin/pa find --dir attractor --gen 2 --limit 30
./bin/pa find --dropped           # include dropped pieces
```

### `lineage <code>` — ancestor chain

Shows the full parent chain from a piece back to its gen-0 seed.

```bash
./bin/pa lineage htb
# htb  gen=3  2026-05-23  differential-growth-morphogene  ...
# mvh  gen=2  2026-05-10  ...
# 5xv  gen=1  ...
# 7ea  gen=0  ...
```

---

### `tick` — run one mutation manually

Calls `scripts/mutate.py` directly. Uses `.venv/bin/python3` if the venv exists.

```bash
./bin/pa tick
```

---

## Queue drain (parallel mutations)

### `drain` — run N parallel mutations from the feedback queue

Drains `iterate_when_chosen` with N mutation workers + 1 evaluator critic. Uses a
claim file (`.drain_claims.json`) for cross-session coordination — running the
command again in a new tab automatically picks the **next unclaimed** batch.

```bash
bash scripts/drain_queue.sh              # 4 mutators + 1 critic (default)
bash scripts/drain_queue.sh -n 3         # 3 mutators + 1 critic
bash scripts/drain_queue.sh -n 4 -c 0   # 4 mutators, skip critic
bash scripts/drain_queue.sh --status     # show claimed vs free, don't run
```

Each worker gets a dedicated `--parent <piece>` so there are no collisions.
Logs go to `.logs/mutate-<piece>-<time>.log` and `.logs/critic-<time>.log`.

### `wait_and_drain` — auto-resume after Claude Code hourly limit

When you hit the hourly limit, this waits N minutes then resumes the drain loop
automatically — no Claude Code session needed (Python workers are independent).

```bash
bash scripts/wait_and_drain.sh           # wait 60 min then resume (default)
bash scripts/wait_and_drain.sh 45        # wait 45 min
bash scripts/wait_and_drain.sh 0         # skip wait, drain now and loop
DRAIN_N=3 bash scripts/wait_and_drain.sh # use 3 workers instead of 4
```

The Stop hook (`on_stop_drain.sh`) fires automatically when a Claude session ends
and launches `wait_and_drain.sh` in the background if free pieces remain.

### Typical multi-tab workflow

```bash
# Tab 1 — first batch
bash scripts/drain_queue.sh -n 4
# → claims sav uag mi2 llr, spawns workers

# Tab 2 — second batch (while tab 1 is still running)
bash scripts/drain_queue.sh -n 4
# → claims td1 lk6 8i3 a2p (skips already-claimed ones)

# Tab 3 — check state at any time
bash scripts/drain_queue.sh --status
```

---

## Bulk drop workflow

```bash
# 1. Browse gallery locally
./bin/pa gallery

# 2. Note codes of pieces to cull

# 3. Drop them all at once
./bin/pa drop abc def ghi

# 4. Push when satisfied
git push
```

## Quick debug: piece not rendering?

```bash
# Check if the dir exists
ls pieces/abc/

# Open directly
./bin/pa view abc

# Look at the lineage entry
./bin/pa find --dir <direction>   # find similar pieces for comparison
./bin/pa lineage abc              # see where it came from
```
