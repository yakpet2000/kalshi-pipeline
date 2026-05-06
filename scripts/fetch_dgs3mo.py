"""Fetch FRED DGS3MO (3-month Treasury Bill rate) and cache as CSV.

Per `notes/simulator-design.md` §3.4 the simulator caches DGS3MO at
`/tmp/dgs3mo.csv` and forward-fills weekends/holidays. This script
populates that cache.

Note on URL: simulator-design.md §3.4 cites
https://fred.stlouisfed.org/series/DGS3MO as the source. That URL is
the human-facing page (HTML); the corresponding CSV download endpoint
is https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO. We hit
the CSV endpoint here and save the raw response unchanged for
auditability.

Usage:
    /Users/peteryakovlev/projects/kalshi-pipeline/.venv/bin/python \\
        scripts/fetch_dgs3mo.py

No CLI args. No DB access. No authentication required (FRED CSV is
public).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import structlog

FRED_DGS3MO_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO"
CACHE_PATH = Path("/tmp/dgs3mo.csv")
HTTP_TIMEOUT_SECONDS = 30.0

ENV = os.environ.get("ENV", "dev")
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if ENV == "dev"
        else structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()


def fetch_and_cache() -> Path:
    log.info("fetching_fred_dgs3mo", url=FRED_DGS3MO_CSV_URL)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        r = client.get(FRED_DGS3MO_CSV_URL)
        r.raise_for_status()
    body = r.text
    CACHE_PATH.write_text(body)
    line_count = body.count("\n")
    first_data_line = ""
    last_data_line = ""
    for line in body.splitlines()[1:]:
        if line.strip():
            if not first_data_line:
                first_data_line = line
            last_data_line = line
    log.info(
        "wrote_cache",
        path=str(CACHE_PATH),
        line_count=line_count,
        first_data_line=first_data_line,
        last_data_line=last_data_line,
    )
    return CACHE_PATH


def main() -> int:
    fetch_and_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
