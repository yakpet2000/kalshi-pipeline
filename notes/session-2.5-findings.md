# Session 2.5 findings

Notes captured during session 2.5 (deploy to remote server) that should carry forward to session 3 (query layer).

## Deployment summary

The pipeline now runs on a Hetzner Linux VPS at 204.168.161.112 (hostname cloudcode-bot, Ubuntu 24.04 LTS). Cron fires the collector every 15 minutes, appending JSON logs to /var/log/kalshi-pipeline.log. The original local-Mac install is no longer the production data source — it is now just a dev environment for code changes. The server has its own Postgres database (kalshi_pipeline owned by role kalshi) and its own snapshots — no shared DB between Mac and server.

## Server architecture

- OS: Ubuntu 24.04 LTS, kernel 6.8.0-106 (pending kernel upgrade deferred).
- Postgres: 16.13, installed via apt. Localhost-only TCP. Auto-starts on boot.
- Python: 3.12.3 system, venv at /root/projects/kalshi-pipeline/.venv/.
- Other tenants: telegram bots and a daily journal generator. Pipeline deployed alongside without disturbing them.
- 3.7 GB RAM, no swap. Comfortably within budget for our load.

## Auth setup

- SSH access to server: from Windows works, from Mac currently falls through to password despite identical key. Deferred — not blocking deployment work which happens on the server itself.
- GitHub deploy key on server: /root/.ssh/id_ed25519_kalshi (read-only). Aliased in /root/.ssh/config so git@github.com auto-uses it.
- DB credentials: hardcoded in server .env at /root/projects/kalshi-pipeline/.env, mode 600 root-only. Acceptable because Postgres binds localhost only and shell access is gated by SSH.

## Cron details

The crontab entry runs every 15 minutes. It cd's into /root/projects/kalshi-pipeline first, then runs .venv/bin/python -m kalshi_pipeline collect, appending stdout and stderr to /var/log/kalshi-pipeline.log.

The cd is mandatory — collector reads tracked_markets.yml and .env from the current working directory. Without cd, cron runs in /root/ and the collector exits with FileNotFoundError.

Append (>>) not overwrite (>) so the log is monotonic. Will need rotation eventually.

## Cadence and bucket alignment

Cron fires at :00, :15, :30, :45 of each hour. The collector's floor_to_15min(datetime.now(UTC)) produces buckets at the same boundaries. So each cron run lands in its own dedicated bucket with no overlap between runs.

Verified: first cron run fired at 19:00:02 UTC, all 9 tickers fetched, 9 rows inserted into bucket 2026-05-01 19:00:00+00. Distinct from the manual run earlier in bucket 2026-05-01 18:45:00+00.

## Logging in prod vs dev

ENV=prod on server selects structlog.processors.JSONRenderer. Each log line is one JSON object with keys event, ticker, outcome, latency_ms, timestamp, level. Greppable, parseable, machine-readable.

ENV=dev on Mac selects structlog.dev.ConsoleRenderer (colored, key-value). Both paths verified end-to-end.

## Open questions for session 3

### Querying server data from Mac

Session 3 builds the query layer. The data of interest now lives on the server, not the Mac. Two viable paths:

1. SSH tunnel: forward server's Postgres port to Mac's localhost so query code can connect locally.
2. Run queries on the server itself, ssh in to use psql interactively.

Path 1 is more convenient for Python query code. Path 2 is more secure (no port forwarding). Decide in session 3.

### Log rotation

The log file will grow unbounded — about 1.7 KB per run, 96 runs/day, ~60 MB/year. Not urgent at our scale, but worth a logrotate config eventually. Session 4 polish or later.

### Mac SSH key issue (deferred)

Mac's SSH to the server falls through to password despite the same ed25519 private key working from Windows. Keys verified identical via public-key derivation. Server's authorized_keys accepts the key from Windows. Mac-specific issue (possibly macOS Keychain interference, possibly something else). Not blocking session 2.5 deploy. Revisit before session 3 if SSH tunnel approach is chosen.

### previous_* field semantics (still pending from session 2)

Now that data is being collected continuously, session 3 has the raw material to analyze whether previous_*_dollars represents prior tick, prior day close, or scheduled reset. Just look at consecutive rows for any single ticker.

### Bot tenant safety

Server runs other Python processes. So far they coexist fine. If pipeline ever grows resource demands (larger ticker list, denser polling), monitor top for impact on the bot processes.
