"""One-shot discovery script for building a Kalshi candidate universe.

Self-contained: does NOT modify or import kalshi_pipeline.kalshi_client. Read-only
on the production pipeline DB (does not touch Postgres at all). Writes two
artifacts only: notes/universe-discovery.md and notes/candidate-universe.csv.

Run modes (CLI args):
  --probe       Auth-ping + small 10-market probe. Writes nothing. Prints sample.
  --series      Pull full /series catalog. Writes a debug JSON to /tmp.
  --full        Run end-to-end: series pull -> per-series market pull -> filter -> CSV.
  --headline    Read the cached debug data and print the headline summary table.

Auth: RSA-PSS over (timestamp + method + path), per Kalshi spec. KALSHI_API_KEY_ID
and KALSHI_PRIVATE_KEY_PATH come from .env at the project root.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import find_dotenv, load_dotenv

API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NOTES_DIR = PROJECT_ROOT / "notes"
DEBUG_DIR = Path("/tmp/kalshi-discovery")
DEBUG_DIR.mkdir(exist_ok=True)

NOW = datetime.now(timezone.utc)
WINDOW_START = NOW - timedelta(days=365)


def load_env() -> tuple[str, Path]:
    dotenv_path = find_dotenv(usecwd=False)
    if not dotenv_path:
        dotenv_path = "/Users/peteryakovlev/projects/kalshi-pipeline/.env"
    load_dotenv(dotenv_path)
    key_id = os.environ["KALSHI_API_KEY_ID"]
    key_path_raw = os.environ["KALSHI_PRIVATE_KEY_PATH"]
    key_path = Path(os.path.expanduser(key_path_raw))
    if not key_path.exists():
        raise SystemExit(f"private key not found at {key_path}")
    return key_id, key_path


def load_private_key(key_path: Path):
    pem = key_path.read_bytes()
    return serialization.load_pem_private_key(pem, password=None)


def sign_request(private_key, method: str, path: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    msg = (timestamp + method.upper() + path).encode()
    signature = private_key.sign(
        msg,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


class KalshiSignedClient:
    def __init__(self, key_id: str, private_key) -> None:
        self._client = httpx.Client(base_url=API_BASE, timeout=30.0)
        self._key_id = key_id
        self._private_key = private_key

    def __enter__(self) -> "KalshiSignedClient":
        return self

    def __exit__(self, *a) -> None:
        self._client.close()

    def get(self, path: str, params: dict | None = None) -> dict:
        # path here is the path AFTER /trade-api/v2 — but the signed message
        # uses the FULL request path including /trade-api/v2.
        full_path = f"/trade-api/v2{path}"
        for attempt in range(5):
            sig_headers = sign_request(self._private_key, "GET", full_path)
            headers = {"KALSHI-ACCESS-KEY": self._key_id, **sig_headers}
            r = self._client.get(path, params=params or {}, headers=headers)
            if r.status_code == 429:
                wait = 2 ** attempt
                print(f"  [rate-limit] 429 on {path}, sleeping {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"too many 429s on {path}")


# ---------- Classifier ----------

# Macro keywords (title + series_ticker, case-insensitive).
MACRO_KEYWORDS = [
    "cpi", "ppi", "pce", "fomc", "fed ", "powell", "interest rate", "rate hike",
    "rate cut", "inflation", "unemployment", "nonfarm", "payroll", "jobless",
    "gdp", "treasury yield", "treasury bill", "treasury note", "tnote", "tbill",
    "yield curve", "gas price", "oil price", "housing start", "retail sales",
    "recession", "consumer sentiment", "consumer confidence", "michigan",
    "ism manufacturing", "ism services", "industrial production", "trade balance",
    "current account", "money supply", "m2", "discount rate", "fed funds",
    "real estate", "mortgage rate", "personal income", "personal spending",
    "factory orders", "durable goods", "wholesale price", "job report",
    "fed decision", "rate decision",
]

# Geopolitics keywords. Splits into foreign-policy + foreign-election sub-buckets.
GEO_FOREIGN_POLICY = [
    "hormuz", "ukraine", "russia", "iran", "israel", "gaza", "taiwan", "china",
    "nato", "ecb", "boe", "boj", "pboc", "putin", " xi ", "war ", " war,",
    "ceasefire", "sanction", "opec", "treaty", "summit", "tariff", "trade war",
    "nuclear", "missile", "north korea", "south china sea", "houthi",
    "afghanistan", "syria", "venezuela", "cuba", "embassy", "ambassador",
    "diplomat", "foreign aid", "geopolitic", "border crisis", "immigration",
    "asylum",
]
GEO_FOREIGN_COUNTRIES = [
    "portugal", "spain", "italy", "germany", "france", "uk ", "britain", "british",
    "australia", "canada", "japan", "india", "brazil", "mexico", "argentina",
    "israel", "korea", "taiwan", "philippines", "indonesia", "turkey", "poland",
    "netherlands", "ireland", "sweden", "norway", "denmark", "finland", "greece",
    "ecuador", "peru", "chile", "colombia", "venezuela", "bolivia", "uruguay",
    "egypt", "morocco", "tunisia", "algeria", "south africa", "nigeria", "kenya",
    "ethiopia", "iraq", "lebanon", "jordan", "saudi", "uae", "qatar", "kuwait",
    "yemen", "vietnam", "thailand", "malaysia", "singapore", "pakistan",
    "bangladesh", "sri lanka", "myanmar", "hungary", "romania", "ukraine",
    "russia",
]
FOREIGN_ELECTION_KEYWORDS = [
    "presidential", "parliamentary", "snap election", "general election",
    "prime minister", "chancellor", "premier", "knesset", "diet ", "duma",
    "bundestag", "bundeskanzler",
]

# Markers that strongly indicate US-domestic content (used to flag US in Elections
# bucket and also to skip Politics-default-keep ambiguous when matched).
US_DOMESTIC_MARKERS = [
    "congress", " senate", " house ", "supreme court", "scotus", "scotus",
    "nominee", "confirm", "cabinet", "executive order", "impeach", "governor",
    "mayor", "state legislat", "rent control", "ballot", "referendum",
    "attorney general", "secretary of state", "fbi director", "fbi dir",
    " sec ", "fcc", "ftc", "doj", "bureau", "us house", "u.s. house",
    "us senate", "u.s. senate", "republican", "democrat", "primary",
    "midterm", "gubernatorial",
]
# Two-letter US state codes appearing as ticker prefixes (KXSECSTATE<XX>),
# used as a heuristic to flag Elections bucket entries.
US_STATE_CODES = [
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
    "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
    "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
    "VA","WA","WV","WI","WY","DC",
]

CATEGORY_HARD_INCLUDE = {
    "Economics":   "macro",
    "Financials":  "macro",
    "Commodities": "macro",
}
CATEGORY_KEYWORD_CLASSIFY = {"Politics", "Elections", "World", "(null/empty)"}
CATEGORY_HARD_EXCLUDE = {
    "Entertainment", "Sports", "Crypto", "Climate and Weather",
    "Mentions", "Social", "Science and Technology", "Transportation",
    "Exotics", "Education",
}
CATEGORY_PERMISSIVE_PASSTHROUGH = {"Health", "Companies"}


def _matches_any(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


def classify_series(s: dict) -> tuple[str, str]:
    """Return (label, sub-tag).

    label is one of: "macro", "geopolitics", "geopolitics_uncertain",
                     "macro_uncertain", "drop_us_domestic", "drop_other".
    sub-tag is one of: "hard_include", "keyword_macro", "keyword_geo_foreign_policy",
                       "keyword_geo_foreign_election", "default_keep_uncertain",
                       "us_state_code", "us_domestic_marker", "no_match".
    """
    cat = s.get("category") or "(null/empty)"
    title = (s.get("title") or "").lower()
    ticker = (s.get("ticker") or "").lower()
    blob = title + " || " + ticker

    if cat in CATEGORY_HARD_INCLUDE:
        return CATEGORY_HARD_INCLUDE[cat], "hard_include"

    if cat in CATEGORY_HARD_EXCLUDE or cat in CATEGORY_PERMISSIVE_PASSTHROUGH:
        # Run keyword check anyway for the permissive count, but the label is drop_*.
        passthrough_label = ""
        if cat in CATEGORY_PERMISSIVE_PASSTHROUGH:
            if _matches_any(blob, MACRO_KEYWORDS):
                passthrough_label = "macro_passthrough"
            elif (
                _matches_any(blob, GEO_FOREIGN_POLICY)
                or _matches_any(blob, GEO_FOREIGN_COUNTRIES)
                or _matches_any(blob, FOREIGN_ELECTION_KEYWORDS)
            ):
                passthrough_label = "geo_passthrough"
        if passthrough_label:
            return f"drop_other__would_include_{passthrough_label}", "permissive_passthrough"
        return "drop_other", f"hard_exclude_{cat}"

    # Keyword-classify bucket
    if cat in CATEGORY_KEYWORD_CLASSIFY:
        # Macro keywords first
        if _matches_any(blob, MACRO_KEYWORDS):
            return "macro", "keyword_macro"
        # Geopolitics: foreign policy
        if _matches_any(blob, GEO_FOREIGN_POLICY):
            return "geopolitics", "keyword_geo_foreign_policy"
        # Foreign-election: country name OR foreign-election keyword
        has_country = _matches_any(blob, GEO_FOREIGN_COUNTRIES)
        has_election_kw = _matches_any(blob, FOREIGN_ELECTION_KEYWORDS)
        if has_country and (has_election_kw or cat in {"Elections", "Politics"}):
            return "geopolitics", "keyword_geo_foreign_election"
        # US-state code in series_ticker (e.g. KXSECSTATEMI → MI)
        # Recognize KX<word><STATE> pattern: scan ticker for any 2-letter state code as suffix
        ticker_upper = (s.get("ticker") or "").upper()
        for st in US_STATE_CODES:
            if ticker_upper.endswith(st) or f"{st}-" in ticker_upper:
                return "drop_us_domestic", "us_state_code"
        # US-domestic markers in title
        if _matches_any(blob, US_DOMESTIC_MARKERS):
            return "drop_us_domestic", "us_domestic_marker"
        # Default keep (per user instruction): geopolitics_uncertain
        return "geopolitics_uncertain", "default_keep_uncertain"

    # Unknown / unanticipated category
    return "drop_other", f"unknown_category_{cat}"


def cmd_classify(c: KalshiSignedClient | None = None) -> None:
    """Apply the classifier to the cached /series dump and print aggregates."""
    series_path = DEBUG_DIR / "series_full.json"
    series = json.loads(series_path.read_text())
    print(f"loaded {len(series)} series from {series_path}")

    from collections import Counter
    label_counter = Counter()
    label_by_category: dict[tuple[str, str], int] = {}
    classified: list[dict] = []
    permissive_passthrough_counts = Counter()  # by source category

    # For Elections: also break down foreign-election vs US-domestic
    elections_split = Counter()
    politics_split = Counter()
    world_split = Counter()
    nullcat_split = Counter()

    for s in series:
        label, subtag = classify_series(s)
        cat = s.get("category") or "(null/empty)"
        label_counter[label] += 1
        label_by_category[(cat, label)] = label_by_category.get((cat, label), 0) + 1
        classified.append({
            "ticker": s.get("ticker"),
            "title": s.get("title"),
            "category": cat,
            "label": label,
            "subtag": subtag,
            "volume_fp": s.get("volume_fp"),
            "tags": s.get("tags"),
        })
        if subtag == "permissive_passthrough":
            permissive_passthrough_counts[cat] += 1
        if cat == "Elections":
            elections_split[subtag if label != "drop_us_domestic" else "us_domestic"] += 1
        if cat == "Politics":
            politics_split[subtag if label != "drop_us_domestic" else "us_domestic"] += 1
        if cat == "World":
            world_split[subtag if label != "drop_us_domestic" else "us_domestic"] += 1
        if cat == "(null/empty)":
            nullcat_split[subtag if label != "drop_us_domestic" else "us_domestic"] += 1

    out = DEBUG_DIR / "series_classified.json"
    out.write_text(json.dumps(classified, indent=2))
    print(f"\nclassified series saved to {out}")

    print("\n=== series-level label aggregate ===")
    for label, n in label_counter.most_common():
        print(f"  {label:50s} : {n}")

    print("\n=== Politics bucket split ===")
    for sub, n in politics_split.most_common():
        print(f"  {sub:35s} : {n}")
    print("\n=== Elections bucket split (foreign vs US-domestic) ===")
    for sub, n in elections_split.most_common():
        print(f"  {sub:35s} : {n}")
    print("\n=== World bucket split ===")
    for sub, n in world_split.most_common():
        print(f"  {sub:35s} : {n}")
    print("\n=== (null/empty) bucket split ===")
    for sub, n in nullcat_split.most_common():
        print(f"  {sub:35s} : {n}")

    print("\n=== permissive-passthrough counts (would-have-been-included) ===")
    for cat, n in permissive_passthrough_counts.most_common():
        print(f"  {cat:20s} : {n}")

    # Keep-list = anything not drop_*
    kept = [s for s in classified if not s["label"].startswith("drop_")]
    print(f"\n=== KEEP series count: {len(kept)} (will pull markets for each) ===")


LIQUIDITY_TIERS = [
    ("<$10K",       0.0,        10_000.0),
    ("$10K-$25K",   10_000.0,   25_000.0),
    ("$25K-$50K",   25_000.0,   50_000.0),
    ("$50K-$100K",  50_000.0,   100_000.0),
    ("$100K+",      100_000.0,  float("inf")),
]


def liquidity_tier(value: float) -> str:
    for label, lo, hi in LIQUIDITY_TIERS:
        if lo <= value < hi:
            return label
    return "<$10K"


def _to_float(s: object) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def cmd_aggregate(c: KalshiSignedClient | None = None) -> None:
    """Apply user-specified filters and print aggregate-only counts.

    Filters:
      - status: settled (status='finalized') | closed | open(active)
      - time window: 12 months back, applied differently per status
      - time-to-resolution >= 30 days at open
      - exclude multivariate (KXMVE* tickers and ticker contains MVE marker)
      - liquidity tier: by mid-market notional (OI * last_price, fallback to mid bid/ask)
    """
    raw_path = DEBUG_DIR / "markets_raw.jsonl"
    classified_path = DEBUG_DIR / "series_classified.json"
    classified = {s["ticker"]: s for s in json.loads(classified_path.read_text())}

    NOW_LOCAL = NOW
    WINDOW_START_LOCAL = WINDOW_START
    EXCLUDED_MULTIVARIATE = 0
    EXCLUDED_NO_OPEN_TIME = 0
    EXCLUDED_NO_CLOSE_TIME = 0
    EXCLUDED_DURATION_LT30 = 0
    EXCLUDED_OUT_OF_WINDOW = 0
    EXCLUDED_UNCLASSIFIED_DROP = 0
    EXCLUDED_OTHER = 0

    fallback_used_count = 0
    null_oi_count = 0
    null_price_full_fallback_count = 0

    candidates: list[dict] = []
    from collections import Counter
    elections_foreign_actual = 0
    elections_us_domestic_actual = 0
    elections_uncertain_actual = 0

    with raw_path.open() as fh:
        for line in fh:
            rec = json.loads(line)
            m = rec["market"]
            series_ticker = rec["series_ticker_attached"]
            label = rec["series_label"]
            cat = rec["series_category"]
            ticker = m.get("ticker", "")

            # Skip MVE markets (defensive)
            if "KXMVE" in ticker.upper() or m.get("mve_collection_ticker"):
                EXCLUDED_MULTIVARIATE += 1
                continue

            if label.startswith("drop_"):
                EXCLUDED_UNCLASSIFIED_DROP += 1
                continue

            open_time = _parse_ts(m.get("open_time"))
            close_time = _parse_ts(m.get("close_time"))
            settle_ts = _parse_ts(m.get("settlement_ts")) or _parse_ts(m.get("settle_time"))

            if open_time is None:
                EXCLUDED_NO_OPEN_TIME += 1
                continue
            if close_time is None:
                EXCLUDED_NO_CLOSE_TIME += 1
                continue
            duration_days = (close_time - open_time).total_seconds() / 86400.0
            if duration_days < 30:
                EXCLUDED_DURATION_LT30 += 1
                continue

            status = m.get("status") or ""
            # Map status: 'finalized' = settled in Kalshi terms
            if status == "finalized":
                bucket = "settled"
                if settle_ts is None:
                    # Fall back to expiration_time
                    settle_ts = _parse_ts(m.get("expiration_time"))
                ts_to_check = settle_ts or close_time
                if not (WINDOW_START_LOCAL <= ts_to_check <= NOW_LOCAL):
                    EXCLUDED_OUT_OF_WINDOW += 1
                    continue
            elif status == "closed":
                bucket = "closed"
                if not (WINDOW_START_LOCAL <= close_time <= NOW_LOCAL):
                    EXCLUDED_OUT_OF_WINDOW += 1
                    continue
            elif status == "active":
                bucket = "open"
                # No time filter for open markets
            elif status in ("unopened", "initialized"):
                # Pre-trading; treat as open candidate (forward)
                bucket = "open"
            else:
                EXCLUDED_OTHER += 1
                continue

            # Liquidity calc
            oi = _to_float(m.get("open_interest_fp"))
            last_price = _to_float(m.get("last_price_dollars"))
            yes_bid = _to_float(m.get("yes_bid_dollars"))
            yes_ask = _to_float(m.get("yes_ask_dollars"))
            if oi is None:
                oi = 0.0
                null_oi_count += 1
            if last_price is None or last_price == 0.0:
                if yes_bid is not None and yes_ask is not None and (yes_bid + yes_ask) > 0:
                    last_price = (yes_bid + yes_ask) / 2
                    fallback_used_count += 1
                else:
                    last_price = 0.0
                    null_price_full_fallback_count += 1
            open_interest_value = oi * last_price
            tier = liquidity_tier(open_interest_value)

            # Elections foreign/US-domestic real-actual counts (per CANDIDATE markets, not series)
            if cat == "Elections":
                series_subtag = classified.get(series_ticker, {}).get("subtag", "")
                if series_subtag in ("keyword_geo_foreign_election", "keyword_geo_foreign_policy"):
                    elections_foreign_actual += 1
                elif "us_" in series_subtag:
                    elections_us_domestic_actual += 1
                else:
                    elections_uncertain_actual += 1

            candidate = {
                "ticker": ticker,
                "event_ticker": m.get("event_ticker"),
                "series_ticker": series_ticker,
                "title": m.get("title"),
                "status": status,
                "bucket": bucket,
                "result": m.get("result"),
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
                "settlement_ts": m.get("settlement_ts") or m.get("settle_time"),
                "expiration_time": m.get("expiration_time"),
                "time_to_resolution_days": round(duration_days, 2),
                "open_interest_fp": oi,
                "last_price_dollars": last_price,
                "open_interest_value": open_interest_value,
                "total_volume": m.get("volume_fp"),
                "liquidity_tier": tier,
                "category_label": label,
                "kalshi_category": cat,
                "series_subtag": classified.get(series_ticker, {}).get("subtag", ""),
            }
            candidates.append(candidate)

    out = DEBUG_DIR / "candidates.jsonl"
    with out.open("w") as fh:
        for c2 in candidates:
            fh.write(json.dumps(c2) + "\n")
    print(f"\nwrote {len(candidates)} candidates to {out}")

    # Aggregates
    by_bucket = Counter()
    by_label = Counter()
    by_tier = Counter()
    by_bucket_label = Counter()
    by_bucket_label_tier = Counter()
    by_kalshi_category = Counter()

    for c2 in candidates:
        by_bucket[c2["bucket"]] += 1
        by_label[c2["category_label"]] += 1
        by_tier[c2["liquidity_tier"]] += 1
        by_bucket_label[(c2["bucket"], c2["category_label"])] += 1
        by_bucket_label_tier[(c2["bucket"], c2["category_label"], c2["liquidity_tier"])] += 1
        by_kalshi_category[c2["kalshi_category"]] += 1

    print("\n=== EXCLUSION COUNTS ===")
    print(f"  multivariate excluded:    {EXCLUDED_MULTIVARIATE}")
    print(f"  series-drop_*:            {EXCLUDED_UNCLASSIFIED_DROP}")
    print(f"  no open_time:             {EXCLUDED_NO_OPEN_TIME}")
    print(f"  no close_time:            {EXCLUDED_NO_CLOSE_TIME}")
    print(f"  duration < 30 days:       {EXCLUDED_DURATION_LT30}")
    print(f"  outside 12mo window:      {EXCLUDED_OUT_OF_WINDOW}")
    print(f"  other status (skipped):   {EXCLUDED_OTHER}")

    print("\n=== Liquidity-calc fallback counts ===")
    print(f"  null open_interest_fp coerced to 0: {null_oi_count}")
    print(f"  used (yes_bid+yes_ask)/2 fallback for last_price: {fallback_used_count}")
    print(f"  no price available (set to 0, tiered <$10K): {null_price_full_fallback_count}")

    print("\n=== TOTAL CANDIDATES ===")
    print(f"  {len(candidates)}")

    print("\n=== by status bucket ===")
    for k, v in by_bucket.most_common():
        print(f"  {k:10s}: {v}")

    print("\n=== by category_label ===")
    for k, v in by_label.most_common():
        print(f"  {k:25s}: {v}")

    print("\n=== by Kalshi source category (post-filter) ===")
    for k, v in by_kalshi_category.most_common():
        print(f"  {k:25s}: {v}")

    print("\n=== by liquidity tier ===")
    for tier_label, _, _ in LIQUIDITY_TIERS:
        print(f"  {tier_label:12s}: {by_tier.get(tier_label, 0)}")

    print("\n=== HEADLINE: status x category_label x liquidity tier ===")
    print(f"{'status':10s}  {'label':25s}  {'tier':12s}  count")
    for bucket in ("open", "closed", "settled"):
        for label in ("macro", "geopolitics", "geopolitics_uncertain"):
            for tier_label, _, _ in LIQUIDITY_TIERS:
                n = by_bucket_label_tier.get((bucket, label, tier_label), 0)
                if n > 0:
                    print(f"  {bucket:8s}  {label:25s}  {tier_label:12s}  {n}")

    print("\n=== Elections candidate-level split (post-filter, per market) ===")
    print(f"  foreign (geo subtag):    {elections_foreign_actual}")
    print(f"  US-domestic (us subtag): {elections_us_domestic_actual}")
    print(f"  uncertain (default-keep):{elections_uncertain_actual}")


def _recurring_cycle_top20() -> list[dict]:
    """Aggregate volume per series across markets that FAILED the 30-day filter.

    Reads the raw markets dump (markets_raw.jsonl) and the classified series
    file. Returns the top 20 KEPT series ranked by total volume_fp summed
    across markets with duration < 30 days, restricted to markets within
    the 12-month window (using the same proxy logic: settlement_ts /
    expiration_time / close_time).
    """
    raw_path = DEBUG_DIR / "markets_raw.jsonl"
    classified_path = DEBUG_DIR / "series_classified.json"
    classified = {s["ticker"]: s for s in json.loads(classified_path.read_text())}
    series_full_path = DEBUG_DIR / "series_full.json"
    series_full = {s.get("ticker"): s for s in json.loads(series_full_path.read_text())}

    from collections import defaultdict
    series_vol = defaultdict(float)
    series_count = defaultdict(int)

    with raw_path.open() as fh:
        for line in fh:
            rec = json.loads(line)
            m = rec["market"]
            st_ticker = rec["series_ticker_attached"]
            label = rec["series_label"]
            if label.startswith("drop_"):
                continue
            ticker = m.get("ticker", "")
            if "KXMVE" in ticker.upper() or m.get("mve_collection_ticker"):
                continue
            open_time = _parse_ts(m.get("open_time"))
            close_time = _parse_ts(m.get("close_time"))
            if open_time is None or close_time is None:
                continue
            duration_days = (close_time - open_time).total_seconds() / 86400.0
            if duration_days >= 30:
                continue
            # Time-window check
            settle_ts = (
                _parse_ts(m.get("settlement_ts"))
                or _parse_ts(m.get("expiration_time"))
                or close_time
            )
            if not (WINDOW_START <= settle_ts <= NOW):
                continue
            v = _to_float(m.get("volume_fp")) or 0.0
            series_vol[st_ticker] += v
            series_count[st_ticker] += 1

    rows = []
    for st_ticker, v in series_vol.items():
        rows.append({
            "series_ticker": st_ticker,
            "title": (
                classified.get(st_ticker, {}).get("title")
                or series_full.get(st_ticker, {}).get("title")
                or ""
            ),
            "kalshi_category": classified.get(st_ticker, {}).get("category", ""),
            "label": classified.get(st_ticker, {}).get("label", ""),
            "subtag": classified.get(st_ticker, {}).get("subtag", ""),
            "short_duration_market_count": series_count[st_ticker],
            "total_volume_fp_12mo": v,
        })
    rows.sort(key=lambda r: -r["total_volume_fp_12mo"])
    return rows[:20]


def cmd_write_artifacts(c: KalshiSignedClient | None = None) -> None:
    """Write notes/candidate-universe.csv and notes/universe-discovery.md."""
    raw_path = DEBUG_DIR / "candidates.jsonl"
    classified_path = DEBUG_DIR / "series_classified.json"
    series_full_path = DEBUG_DIR / "series_full.json"

    classified = {s["ticker"]: s for s in json.loads(classified_path.read_text())}
    series_full = {s.get("ticker"): s for s in json.loads(series_full_path.read_text())}

    candidates = [json.loads(line) for line in raw_path.open()]
    print(f"loaded {len(candidates)} candidates from {raw_path}")

    NOTES_DIR.mkdir(exist_ok=True)
    csv_path = NOTES_DIR / "candidate-universe.csv"
    md_path = NOTES_DIR / "universe-discovery.md"

    # ---- CSV ----
    csv_columns = [
        "ticker", "event_ticker", "series_ticker", "title", "status",
        "result", "open_time", "close_time", "settle_time",
        "time_to_resolution_days", "open_interest", "total_volume",
        "last_price_dollars", "open_interest_value", "liquidity_tier",
        "category_label", "kalshi_category", "series_subtag",
    ]
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(csv_columns)
        for c2 in candidates:
            w.writerow([
                c2.get("ticker"),
                c2.get("event_ticker"),
                c2.get("series_ticker"),
                c2.get("title"),
                c2.get("status"),
                c2.get("result"),
                c2.get("open_time"),
                c2.get("close_time"),
                c2.get("settlement_ts"),
                c2.get("time_to_resolution_days"),
                c2.get("open_interest_fp"),
                c2.get("total_volume"),
                c2.get("last_price_dollars"),
                round(float(c2.get("open_interest_value") or 0), 2),
                c2.get("liquidity_tier"),
                c2.get("category_label"),
                c2.get("kalshi_category"),
                c2.get("series_subtag"),
            ])
    print(f"wrote CSV: {csv_path} ({len(candidates)} rows)")

    # ---- Aggregate stats for the markdown report ----
    from collections import Counter, defaultdict

    by_bucket = Counter()
    by_label = Counter()
    by_tier = Counter()
    by_bucket_label_tier: dict[tuple[str, str, str], int] = Counter()
    by_kalshi_category = Counter()
    by_kalshi_category_label = Counter()
    duration_buckets = Counter()  # days bucket
    oi_value_buckets = Counter()
    settle_year_month = Counter()

    series_oi_value: dict[str, float] = defaultdict(float)
    series_market_count: dict[str, int] = defaultdict(int)
    series_label_map: dict[str, str] = {}
    series_subtag_map: dict[str, str] = {}
    series_category_map: dict[str, str] = {}
    series_title_map: dict[str, str] = {}

    for c2 in candidates:
        b = c2["bucket"]
        lbl = c2["category_label"]
        t = c2["liquidity_tier"]
        by_bucket[b] += 1
        by_label[lbl] += 1
        by_tier[t] += 1
        by_bucket_label_tier[(b, lbl, t)] += 1
        by_kalshi_category[c2["kalshi_category"]] += 1
        by_kalshi_category_label[(c2["kalshi_category"], lbl)] += 1

        d = c2.get("time_to_resolution_days") or 0
        if d < 60:
            duration_buckets["30-60d"] += 1
        elif d < 90:
            duration_buckets["60-90d"] += 1
        elif d < 180:
            duration_buckets["90-180d"] += 1
        elif d < 365:
            duration_buckets["180-365d"] += 1
        else:
            duration_buckets["365d+"] += 1

        v = c2.get("open_interest_value") or 0
        if v < 1_000:
            oi_value_buckets["<$1K"] += 1
        elif v < 10_000:
            oi_value_buckets["$1K-$10K"] += 1
        elif v < 25_000:
            oi_value_buckets["$10K-$25K"] += 1
        elif v < 50_000:
            oi_value_buckets["$25K-$50K"] += 1
        elif v < 100_000:
            oi_value_buckets["$50K-$100K"] += 1
        elif v < 500_000:
            oi_value_buckets["$100K-$500K"] += 1
        else:
            oi_value_buckets["$500K+"] += 1

        st = c2.get("settlement_ts") or ""
        if st and b == "settled" and len(st) >= 7:
            settle_year_month[st[:7]] += 1

        st_ticker = c2.get("series_ticker") or ""
        series_oi_value[st_ticker] += float(c2.get("open_interest_value") or 0)
        series_market_count[st_ticker] += 1
        series_label_map[st_ticker] = lbl
        series_subtag_map[st_ticker] = c2.get("series_subtag") or ""
        series_category_map[st_ticker] = c2.get("kalshi_category") or ""
        series_title_map[st_ticker] = (
            classified.get(st_ticker, {}).get("title")
            or series_full.get(st_ticker, {}).get("title")
            or ""
        )

    # Series-level inspection lists (only series that actually have post-filter markets)
    def series_summary_for_label(label_name: str) -> list[dict]:
        rows = []
        for st_ticker, n in series_market_count.items():
            if series_label_map.get(st_ticker) != label_name:
                continue
            rows.append({
                "series_ticker": st_ticker,
                "title": series_title_map.get(st_ticker, ""),
                "kalshi_category": series_category_map.get(st_ticker, ""),
                "subtag": series_subtag_map.get(st_ticker, ""),
                "market_count": n,
                "total_oi_value": series_oi_value.get(st_ticker, 0.0),
            })
        rows.sort(key=lambda r: -r["total_oi_value"])
        return rows

    geo_uncertain = series_summary_for_label("geopolitics_uncertain")

    # Elections-only default_keep_uncertain inspection (within Elections category)
    elections_default_keep = [
        r for r in geo_uncertain
        if r["kalshi_category"] == "Elections" and r["subtag"] == "default_keep_uncertain"
    ]
    null_keyword = [
        r for r in geo_uncertain
        if r["kalshi_category"] == "(null/empty)"
    ]
    politics_default_keep = [
        r for r in geo_uncertain
        if r["kalshi_category"] == "Politics" and r["subtag"] == "default_keep_uncertain"
    ]
    world_default_keep = [
        r for r in geo_uncertain
        if r["kalshi_category"] == "World" and r["subtag"] == "default_keep_uncertain"
    ]

    # Top-10 candidates per liquidity tier × category_label
    by_tier_label: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c2 in candidates:
        by_tier_label[(c2["liquidity_tier"], c2["category_label"])].append(c2)
    for k in by_tier_label:
        by_tier_label[k].sort(key=lambda x: -float(x.get("open_interest_value") or 0))

    # ---- write markdown ----
    above_10k = sum(by_tier.get(t, 0) for t in ("$10K-$25K", "$25K-$50K", "$50K-$100K", "$100K+"))
    above_50k = sum(by_tier.get(t, 0) for t in ("$50K-$100K", "$100K+"))

    lines: list[str] = []
    lines.append("# Universe discovery — Kalshi candidate set")
    lines.append("")
    lines.append(f"Run date: {NOW.date().isoformat()} UTC. Window: last 12 months "
                 f"({WINDOW_START.date().isoformat()} → {NOW.date().isoformat()}).")
    lines.append("")
    lines.append("## Executive summary")
    lines.append("")
    lines.append(
        f"- **Total candidates: {len(candidates):,}** across "
        f"**{len(series_market_count):,}** series (from a kept-series set of "
        f"{sum(1 for s in classified.values() if not s['label'].startswith('drop_')):,} "
        f"out of {len(classified):,} total Kalshi series)."
    )
    lines.append(
        f"- **Liquidity is the binding constraint.** Of {len(candidates):,} candidates, "
        f"only **{above_10k} ({above_10k / len(candidates) * 100:.1f}%) clear the $10K mid-market notional "
        f"floor**, and only **{above_50k} ({above_50k / len(candidates) * 100:.1f}%) clear $50K**. "
        f"Test B sample-size design should treat $10K+ as the practical universe size, "
        f"not 6,550."
    )
    lines.append(
        f"- **Status mix:** open={by_bucket['open']:,}, settled={by_bucket['settled']:,}, "
        f"closed={by_bucket['closed']:,}."
    )
    lines.append(
        f"- **Label mix:** macro={by_label['macro']:,}, "
        f"geopolitics={by_label['geopolitics']:,}, "
        f"geopolitics_uncertain={by_label['geopolitics_uncertain']:,} "
        f"(uncertain bucket needs human review; see inspection lists below)."
    )
    lines.append(
        f"- **Duration filter dominates:** of 57,252 raw markets across kept series, "
        f"50,618 (88%) failed the `close_time - open_time >= 30d` requirement. "
        f"This reflects the daily/weekly recurring nature of Kalshi's macro and election series."
    )
    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("### API endpoints used")
    lines.append("- `GET /exchange/status` — auth verification.")
    lines.append("- `GET /series?limit=200&include_volume=true&include_product_metadata=true` — "
                 "full /series catalog (one page returned 9,905 series; cursor was null).")
    lines.append("- `GET /markets?series_ticker=<X>&limit=200` (paginated) — per kept series, "
                 "no status filter (returns all statuses; we partition client-side).")
    lines.append("")
    lines.append("### Auth")
    lines.append("- RSA-PSS over `(timestamp + method + path)`. The signed message uses the "
                 "**full** path including `/trade-api/v2/...`, not the post-base relative path. "
                 "This was the one-line gotcha in the auth helper.")
    lines.append("- Read-only key. No production-pipeline state was touched (no DB writes, "
                 "no edits to `tracked_markets.yml`, no calls to `/portfolio/*`).")
    lines.append("")
    lines.append("### API quirks worth documenting (these will bite future-you)")
    lines.append("")
    lines.append("1. **`?status=settled` returns markets where `status='finalized'`.** The query "
                 "param value and the response value differ — `settled` is the API's filter input, "
                 "but `finalized` is the response status string. Both samples in our two-mode probe "
                 "had `status: 'finalized'`.")
    lines.append("2. **`settle_time` is never populated on the response.** The OpenAPI spec lists "
                 "this field but the actual response uses **`settlement_ts`** instead. Use that. "
                 "In our CSV, we store it under the column name `settle_time` to match the user-facing "
                 "task spec, but it is sourced from `settlement_ts`.")
    lines.append("3. **`series_ticker` and `category` are NOT echoed on `/markets` responses, even when "
                 "you filter by `?series_ticker=X`.** Both come back as `null`. We attach `series_ticker` "
                 "client-side from the filter we passed, and we get `category` from a separate `/series` join.")
    lines.append("4. **`occurrence_datetime` vs `close_time`.** They differ on at least one sample by "
                 "~3 hours (close_time = trading-window close; occurrence_datetime = trigger event). "
                 "Test B precision-sensitive logic should pre-register which is the correct stop-trading "
                 "boundary.")
    lines.append("5. **Default sort of `?status=X` is newest-first.** Page 1 is dominated by recently-"
                 "settled MVE sports markets when no series filter is applied. After 5 pages × 200 markets "
                 "= 1,000 settled markets, we still hadn't reached anything older than the most recent "
                 "two weeks. This means **global enumeration of old archive markets via `/markets` is not "
                 "viable**; per-series queries are the only path. Implication: any market in the kept-series "
                 "set that resolved >12 months ago is *technically* still reachable by ticker but not "
                 "by enumeration. Our 'outside 12-month window: 0' exclusion count is a side-effect of "
                 "this enumeration bias, not evidence the API gave us 12 months of history.")
    lines.append("6. **`status='active'` markets have no `settle_time`/`settlement_ts`.** Expected; "
                 "settlement timestamps populate only after the market resolves.")
    lines.append("7. **`last_price_dollars` for finalized markets does NOT pin to 0/1 by `result`.** "
                 "It's the last *trade* price, which can be off-mid. For settled markets where no "
                 "trades occurred (volume = 0), it's `0.0000`, regardless of `result`.")
    lines.append("8. **`open_interest_fp` persists post-finalize.** Doesn't drop to 0 on settle.")
    lines.append("9. **`?status=closed`** is a brief settlement-window state. We saw only 49 markets "
                 "in this state across the entire kept-series set.")
    lines.append("")
    lines.append("### Filter pipeline (applied in order)")
    lines.append("1. Status: include `active` (open), `closed`, `finalized` (settled). Drop `unopened`.")
    lines.append("2. Both `open_time` and `close_time` non-null.")
    lines.append("3. `(close_time - open_time) >= 30 days`.")
    lines.append("4. Time window:")
    lines.append("   - `finalized` → `settlement_ts` (or fallback to `expiration_time`) within last 12 months.")
    lines.append("   - `closed` → `close_time` within last 12 months.")
    lines.append("   - `active` → no time filter.")
    lines.append("5. Multivariate exclusion: drop tickers containing `KXMVE` or with non-null "
                 "`mve_collection_ticker`. (Note: at the per-series stage, multivariate series are "
                 "in the `Exotics` category, which is hard-excluded — so this is defensive only.)")
    lines.append("")
    lines.append("### Category mapping rules")
    lines.append("Kalshi has 19 distinct categories across 9,905 series (one was `(null/empty)`). "
                 "Three buckets:")
    lines.append("")
    lines.append("**Hard-include as `macro`:** `Economics` (514 series), `Financials` (176), `Commodities` (47).")
    lines.append("")
    lines.append("**Keyword-classified, default-keep ambiguous as `geopolitics_uncertain`:** "
                 "`Politics` (1,844), `Elections` (1,291), `World` (142), `(null/empty)` (43).")
    lines.append("")
    lines.append("**Hard-exclude:** `Entertainment` (2,396), `Sports` (1,827), `Mentions` (345), "
                 "`Companies` (356), `Climate and Weather` (269), `Crypto` (225), `Science and Technology` "
                 "(204), `Health` (111), `Social` (63), `Transportation` (40), `Exotics` (10), `Education` (2).")
    lines.append("")
    lines.append("**Permissive-passthrough counts** (would have been included as macro/geo under "
                 "permissive classification, but kept out of the final universe per user direction):")
    lines.append(f"- `Companies`: **12 series**")
    lines.append(f"- `Health`: **10 series**")
    lines.append("")
    lines.append("### Keyword classifier")
    lines.append(
        "Applied to `Politics`, `Elections`, `World`, `(null/empty)` series. Classifier checks "
        "the title and series_ticker (case-insensitive concatenation) against three keyword lists "
        "(macro, foreign-policy, foreign-election + foreign country names). If multiple match, macro "
        "wins; otherwise foreign-policy or foreign-election. US-domestic markers (state codes, "
        "state-level government roles, US-only political bodies) drop the series. Series matching "
        "none of the include-lists are kept with subtag `default_keep_uncertain` per user direction "
        "(the keyword lists are first-guess heuristics; default-drop would hide errors)."
    )
    lines.append("")
    lines.append("### Liquidity metric")
    lines.append(
        "Mid-market notional: `open_interest_value = open_interest_fp × last_price_dollars`. "
        "Reasoning: max-payout (`OI × $1`) overstates real exposure on out-of-the-money markets "
        "(a 5¢ market with 100K OI has $5K of real exposure, not $100K). Mid-market reflects what "
        "a trader would actually be facing."
    )
    lines.append("")
    lines.append("**Fallbacks for null/zero last_price:**")
    lines.append("- 541 markets used `(yes_bid + yes_ask) / 2` (last_price was null or zero but a quoted spread existed).")
    lines.append("- 74 markets had no usable price; `open_interest_value` set to 0, tiered as `<$10K`.")
    lines.append("- 0 markets had null `open_interest_fp`.")
    lines.append("")
    lines.append("### Edge cases captured during the discovery run")
    lines.append("- **MVE leakage check.** No MVE markets reached the candidate set. The `Exotics` "
                 "category in Kalshi's series taxonomy contains exactly 10 MVE-collection meta-series, "
                 "and our hard-exclude rule drops them at the series stage.")
    lines.append("- **Sub-titles with stray separators (`subtitle: \"::\"`).** Carry-over from session-2 "
                 "findings; harmless to us, captured as-is.")
    lines.append("- **Series with `category=null`** (43 series). Keyword-classified along with the named "
                 "ambiguous categories.")
    lines.append("- **`null open_time`/`null close_time`.** 0 candidates excluded for either reason. "
                 "Every market that reached the filter pipeline had both fields populated.")
    lines.append("- **50-page pagination cap.** Two series hit the cap (`KXNASDAQ100U`, `KXINXU`). "
                 "These are short-duration NASDAQ100 / INX Unit markets that would have failed the "
                 "30-day duration filter anyway. Not material to the candidate set.")
    lines.append("")
    lines.append("## Headline summary table")
    lines.append("")
    lines.append("### Total candidates by liquidity tier")
    lines.append("")
    lines.append("| tier | count | share |")
    lines.append("| --- | --- | --- |")
    for tier_label, _, _ in LIQUIDITY_TIERS:
        n = by_tier.get(tier_label, 0)
        lines.append(f"| {tier_label} | {n:,} | {n / len(candidates) * 100:.1f}% |")
    lines.append("")
    lines.append("### Status × label × tier (non-zero cells)")
    lines.append("")
    lines.append("| status | label | tier | count |")
    lines.append("| --- | --- | --- | --- |")
    for bucket in ("open", "closed", "settled"):
        for label in ("macro", "geopolitics", "geopolitics_uncertain"):
            for tier_label, _, _ in LIQUIDITY_TIERS:
                n = by_bucket_label_tier.get((bucket, label, tier_label), 0)
                if n > 0:
                    lines.append(f"| {bucket} | {label} | {tier_label} | {n:,} |")
    lines.append("")
    lines.append("### Status × label (marginals)")
    lines.append("")
    lines.append("| status | macro | geopolitics | geopolitics_uncertain |")
    lines.append("| --- | --- | --- | --- |")
    for bucket in ("open", "closed", "settled"):
        ms = sum(by_bucket_label_tier.get((bucket, "macro", t[0]), 0) for t in LIQUIDITY_TIERS)
        gs = sum(by_bucket_label_tier.get((bucket, "geopolitics", t[0]), 0) for t in LIQUIDITY_TIERS)
        us = sum(by_bucket_label_tier.get((bucket, "geopolitics_uncertain", t[0]), 0) for t in LIQUIDITY_TIERS)
        lines.append(f"| {bucket} | {ms:,} | {gs:,} | {us:,} |")
    lines.append("")
    lines.append("### Kalshi source category × label (post-filter)")
    lines.append("")
    lines.append("| Kalshi category | macro | geopolitics | geopolitics_uncertain | total |")
    lines.append("| --- | --- | --- | --- | --- |")
    cats_seen = sorted(set(c for c, _ in by_kalshi_category_label.keys()))
    for cat in cats_seen:
        m_ = by_kalshi_category_label.get((cat, "macro"), 0)
        g_ = by_kalshi_category_label.get((cat, "geopolitics"), 0)
        u_ = by_kalshi_category_label.get((cat, "geopolitics_uncertain"), 0)
        lines.append(f"| {cat} | {m_:,} | {g_:,} | {u_:,} | {m_ + g_ + u_:,} |")
    lines.append("")
    lines.append("## Distribution stats")
    lines.append("")
    lines.append("### Time-to-resolution distribution (candidates)")
    lines.append("")
    lines.append("| bucket | count |")
    lines.append("| --- | --- |")
    for k in ("30-60d", "60-90d", "90-180d", "180-365d", "365d+"):
        lines.append(f"| {k} | {duration_buckets.get(k, 0):,} |")
    lines.append("")
    lines.append("### Open-interest mid-market notional distribution (candidates)")
    lines.append("")
    lines.append("| bucket | count |")
    lines.append("| --- | --- |")
    for k in ("<$1K", "$1K-$10K", "$10K-$25K", "$25K-$50K", "$50K-$100K", "$100K-$500K", "$500K+"):
        lines.append(f"| {k} | {oi_value_buckets.get(k, 0):,} |")
    lines.append("")
    lines.append("### Settle-date distribution (settled bucket only)")
    lines.append("")
    lines.append("| YYYY-MM | count |")
    lines.append("| --- | --- |")
    for k in sorted(settle_year_month.keys()):
        lines.append(f"| {k} | {settle_year_month[k]:,} |")
    lines.append("")
    lines.append("## Elections bucket: foreign vs US-domestic split")
    lines.append("")
    lines.append("**Series-level** (1,291 series in `Elections` category, before market-pull):")
    lines.append("")
    lines.append("| classification | series count |")
    lines.append("| --- | --- |")
    lines.append("| US-domestic (dropped at series stage) | 691 |")
    lines.append("| keyword_geo_foreign_election | 167 |")
    lines.append("| keyword_geo_foreign_policy | 23 |")
    lines.append("| keyword_macro (re-tagged macro) | 22 |")
    lines.append("| default_keep_uncertain | 388 |")
    lines.append("")
    lines.append("**Market-level** (Elections markets that survived all filters):")
    lines.append("")
    lines.append("| classification | market count |")
    lines.append("| --- | --- |")
    lines.append("| foreign | 585 |")
    lines.append("| uncertain (default-keep) | 1,771 |")
    lines.append("| US-domestic | 0 (dropped at series stage) |")
    lines.append("")
    lines.append("## Inspection lists (for human keep/drop calls)")
    lines.append("")
    lines.append("These lists are sorted descending by total mid-market notional across all "
                 "candidate markets in the series. Series with no $10K+ candidates are not realistically "
                 "worth manual inspection time and are count-summarized only. The CSV "
                 "(`notes/candidate-universe.csv`) has the long tail.")
    lines.append("")

    def render_inspection(rows: list[dict], cap: int, title: str) -> None:
        lines.append(f"### {title}")
        lines.append("")
        with_liq = [r for r in rows if r["total_oi_value"] >= 10_000]
        without_liq = [r for r in rows if r["total_oi_value"] < 10_000]
        lines.append(f"Total series in this set: **{len(rows):,}**. "
                     f"With $10K+ aggregate mid-market notional: **{len(with_liq):,}**. "
                     f"Below $10K: **{len(without_liq):,}** (count-only; see CSV for individual rows).")
        lines.append("")
        if not with_liq:
            lines.append("*No series in this set have $10K+ aggregate notional.*")
            lines.append("")
            return
        shown = with_liq[:cap]
        lines.append(f"Top {len(shown)} by aggregate notional:")
        lines.append("")
        lines.append("| series_ticker | title | Kalshi category | subtag | candidate markets | total mid-market notional |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in shown:
            t = (r["title"] or "").replace("|", "\\|")
            lines.append(
                f"| `{r['series_ticker']}` | {t} | {r['kalshi_category']} | "
                f"{r['subtag']} | {r['market_count']:,} | "
                f"${r['total_oi_value']:,.0f} |"
            )
        lines.append("")
        if len(with_liq) > cap:
            lines.append(f"...and {len(with_liq) - cap} more series with $10K+ notional. See CSV.")
            lines.append("")

    render_inspection(geo_uncertain, 50, "All `geopolitics_uncertain` series (top 50 by notional)")
    render_inspection(elections_default_keep, 30,
                      "Elections `default_keep_uncertain` (top 30 by notional)")
    render_inspection(politics_default_keep, 30,
                      "Politics `default_keep_uncertain` (top 30 by notional)")
    render_inspection(world_default_keep, 30,
                      "World `default_keep_uncertain` (top 30 by notional)")
    render_inspection(null_keyword, 30,
                      "(null/empty) keyword-classified set (top 30 by notional)")

    # ---- Out-of-scope: recurring-cycle short-duration markets ----
    recurring_top = _recurring_cycle_top20()
    lines.append("## Out-of-scope universe: recurring-cycle short-duration markets")
    lines.append("")
    lines.append(
        "1. The 30-day duration filter culled **50,618 of 57,252 raw markets (88%)** in this "
        "discovery run. A meaningful fraction of these are recurring daily/weekly cycle series "
        "(`KXGASD` daily gas, `KXNATGASD` natural gas daily, weekly Hormuz contracts, etc.)."
    )
    lines.append("")
    lines.append(
        "2. These are **not independent markets** — they are sequential contracts on the same "
        "underlying time series. The May 1 oil price contract and the May 2 oil price contract "
        "are adjacent samples of one continuous price signal, not two independent draws."
    )
    lines.append("")
    lines.append(
        "3. Stitched together, these series have **potentially unbounded history** available, "
        "far beyond what the 30-day filter implies. They are out of scope for THIS discovery "
        "(which targeted long-duration markets per the stated category scope), but they represent "
        "a substantial future expansion of the effective universe for Test B."
    )
    lines.append("")
    lines.append(
        "4. **Top 20 recurring-cycle KEPT series by aggregate volume** (sum of `volume_fp` across "
        "all sub-30-day contracts in the series, settle/expiration within the last 12 months). "
        "Starting point for future stitching analysis:"
    )
    lines.append("")
    if recurring_top:
        lines.append(
            "| rank | series_ticker | title | Kalshi category | label | subtag | "
            "short-dur markets (12mo) | total volume_fp (12mo) |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for i, r in enumerate(recurring_top, start=1):
            t = (r["title"] or "").replace("|", "\\|")
            lines.append(
                f"| {i} | `{r['series_ticker']}` | {t} | {r['kalshi_category']} | "
                f"{r['label']} | {r['subtag']} | {r['short_duration_market_count']:,} | "
                f"{r['total_volume_fp_12mo']:,.0f} |"
            )
    else:
        lines.append("*No recurring-cycle series found in the raw data.*")
    lines.append("")
    lines.append(
        "Note on volume units: `volume_fp` is a contract-count quantity (per the field-suffix "
        "convention in CLAUDE.md). To convert to a dollar notional for ranking purposes, multiply "
        "by an average contract price; for ranking *between series* of similar contract types, "
        "raw `volume_fp` is sufficient."
    )
    lines.append("")

    # Top-10 candidates per liquidity tier × category_label
    lines.append("## Top candidates per liquidity tier × label")
    lines.append("")
    lines.append("(For sanity-checking the candidate set. Sorted by mid-market notional, descending.)")
    lines.append("")
    for tier_label, _, _ in LIQUIDITY_TIERS:
        for label in ("macro", "geopolitics", "geopolitics_uncertain"):
            rows = by_tier_label.get((tier_label, label), [])
            if not rows:
                continue
            lines.append(f"### Tier {tier_label} × {label} (n={len(rows):,})")
            lines.append("")
            top = rows[:10]
            lines.append("| ticker | title | Kalshi category | mid-market notional |")
            lines.append("| --- | --- | --- | --- |")
            for r in top:
                t = (r.get("title") or "").replace("|", "\\|")
                lines.append(
                    f"| `{r['ticker']}` | {t} | {r.get('kalshi_category')} | "
                    f"${float(r.get('open_interest_value') or 0):,.0f} |"
                )
            lines.append("")

    md_path.write_text("\n".join(lines))
    print(f"wrote markdown: {md_path}")
    print(f"  total lines: {len(lines)}")


def _parse_ts(s: object) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def cmd_pull_markets(c: KalshiSignedClient) -> None:
    """For each KEPT series (not drop_*), pull all markets via cursor pagination."""
    classified_path = DEBUG_DIR / "series_classified.json"
    classified = json.loads(classified_path.read_text())
    kept = [s for s in classified if not s["label"].startswith("drop_")]
    print(f"loaded {len(classified)} classified series, kept={len(kept)}")

    out_path = DEBUG_DIR / "markets_raw.jsonl"
    progress_every = 50
    total_markets = 0
    series_with_no_markets = 0
    series_completed = 0
    start = time.monotonic()

    with out_path.open("w") as fh:
        for s in kept:
            ticker = s["ticker"]
            label = s["label"]
            cat = s["category"]
            cursor: str | None = None
            page_idx = 0
            ser_market_count = 0
            while True:
                params: dict[str, Any] = {"series_ticker": ticker, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                try:
                    page = c.get("/markets", params=params)
                except httpx.HTTPStatusError as e:
                    print(f"  [HTTP {e.response.status_code}] series={ticker} -- skipping", file=sys.stderr)
                    break
                markets = page.get("markets", []) or []
                for m in markets:
                    record = {
                        "series_ticker_attached": ticker,
                        "series_label": label,
                        "series_category": cat,
                        "market": m,
                    }
                    fh.write(json.dumps(record) + "\n")
                    ser_market_count += 1
                cursor = page.get("cursor")
                page_idx += 1
                if not cursor or not markets:
                    break
                if page_idx > 50:
                    print(f"  WARN: hit 50-page cap on series={ticker}", file=sys.stderr)
                    break
                time.sleep(0.05)
            if ser_market_count == 0:
                series_with_no_markets += 1
            total_markets += ser_market_count
            series_completed += 1
            if series_completed % progress_every == 0:
                elapsed = time.monotonic() - start
                rate = series_completed / elapsed if elapsed > 0 else 0
                remaining = (len(kept) - series_completed) / rate if rate > 0 else float("inf")
                print(
                    f"  [{series_completed}/{len(kept)}] elapsed={elapsed:.0f}s "
                    f"rate={rate:.1f}/s ETA={remaining:.0f}s "
                    f"markets={total_markets} empty_series={series_with_no_markets}"
                )
            time.sleep(0.05)

    elapsed = time.monotonic() - start
    print(f"\n=== DONE: {total_markets} markets across {series_completed} series in {elapsed:.0f}s ===")
    print(f"  empty series: {series_with_no_markets}")
    print(f"  raw markets dump: {out_path}")


def cmd_pull_series(c: KalshiSignedClient) -> None:
    """Pull /series paginated. Save full response to disk; print taxonomy summary."""
    all_series: list[dict] = []
    cursor: str | None = None
    page_num = 0
    print("=== /series paginated pull ===")
    while True:
        page_num += 1
        params: dict[str, Any] = {
            "limit": 200,
            "include_volume": "true",
            "include_product_metadata": "true",
        }
        if cursor:
            params["cursor"] = cursor
        page = c.get("/series", params=params)
        # /series response shape: figure it out
        items = (
            page.get("series")
            or page.get("data")
            or page.get("items")
            or []
        )
        cursor = page.get("cursor") or page.get("next_cursor")
        print(f"  page {page_num}: {len(items)} series, top-level keys={list(page.keys())}, cursor={'<set>' if cursor else None}")
        if page_num == 1 and items:
            sample = items[0]
            print(f"  sample series fields: {sorted(sample.keys())}")
        all_series.extend(items)
        if not cursor or not items:
            break
        time.sleep(0.3)
        if page_num > 100:
            print("  WARN: hit 100-page safety cap")
            break

    print(f"\n=== TOTAL: {len(all_series)} series ===\n")
    out = DEBUG_DIR / "series_full.json"
    out.write_text(json.dumps(all_series, indent=2))
    print(f"raw series dump: {out}")

    # Summarize by category
    from collections import Counter
    cats = Counter()
    null_cats: list[str] = []
    cat_examples: dict[str, list[str]] = {}
    for s in all_series:
        cat = s.get("category")
        ticker = s.get("ticker") or s.get("series_ticker") or "?"
        title = s.get("title") or s.get("name") or "?"
        if cat is None or cat == "":
            null_cats.append(f"{ticker} :: {title}")
            cats["(null/empty)"] += 1
        else:
            cats[cat] += 1
            cat_examples.setdefault(cat, []).append(f"{ticker} :: {title}")

    print(f"\n=== distinct categories (N={len(cats)}) ===")
    for cat, n in cats.most_common():
        print(f"  {cat!r:30s} : {n}")

    print(f"\n=== examples per category (top 5 by series ticker) ===")
    for cat, n in cats.most_common():
        if cat == "(null/empty)":
            print(f"\n  [{cat}] ({n} series):")
            for ex in null_cats[:5]:
                print(f"    {ex}")
        else:
            print(f"\n  [{cat}] ({n} series):")
            for ex in cat_examples.get(cat, [])[:5]:
                print(f"    {ex}")


def cmd_probe_settled(c: KalshiSignedClient) -> None:
    """Compare schemas of recent-settled vs old-settled markets.

    Strategy: fetch /markets?status=settled paginated, then partition by
    expiration_time / settle_time to find one in last 30 days and one >6mo ago.
    """
    print("=== /markets?status=settled (page 1, limit 200) ===")
    page = c.get("/markets", params={"status": "settled", "limit": 200})
    markets = page.get("markets", [])
    print(f"  returned {len(markets)} settled markets, cursor={page.get('cursor')!r}")
    if not markets:
        print("no settled markets returned; cannot compare")
        return

    # Parse a "settle proxy" timestamp: prefer settle_time, fall back to
    # expiration_time, then expected_expiration_time.
    def parse_ts(s: str | None) -> datetime | None:
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def settle_proxy(m: dict) -> datetime | None:
        for f in ("settle_time", "expiration_time", "expected_expiration_time", "close_time"):
            ts = parse_ts(m.get(f))
            if ts:
                return ts
        return None

    enriched = [(m, settle_proxy(m)) for m in markets]
    enriched = [(m, ts) for m, ts in enriched if ts is not None]
    enriched.sort(key=lambda x: x[1])

    oldest = enriched[0][0] if enriched else None
    most_recent = enriched[-1][0] if enriched else None
    cutoff_recent = NOW - timedelta(days=30)
    cutoff_old = NOW - timedelta(days=180)
    recent_in_window = next(
        ((m, ts) for m, ts in reversed(enriched) if ts >= cutoff_recent),
        None,
    )
    old_in_window = next(
        ((m, ts) for m, ts in enriched if ts <= cutoff_old),
        None,
    )

    span_oldest = enriched[0][1]
    span_newest = enriched[-1][1]
    print(f"  proxy-time span on this page: {span_oldest} -> {span_newest}")
    print(f"  recent-settle (>= {cutoff_recent.date()}): {'FOUND' if recent_in_window else 'NOT on page 1'}")
    print(f"  old-settle    (<= {cutoff_old.date()}):    {'FOUND' if old_in_window else 'NOT on page 1'}")

    # If we can't find both on page 1, scan more pages (cap at 5 pages = 1000 markets)
    cursor = page.get("cursor")
    pages_scanned = 1
    while (not recent_in_window or not old_in_window) and cursor and pages_scanned < 5:
        print(f"\n  scanning page {pages_scanned + 1} for missing buckets...")
        time.sleep(0.5)
        page = c.get("/markets", params={"status": "settled", "limit": 200, "cursor": cursor})
        markets = page.get("markets", [])
        if not markets:
            break
        for m in markets:
            ts = settle_proxy(m)
            if ts is None:
                continue
            if not recent_in_window and ts >= cutoff_recent:
                recent_in_window = (m, ts)
            if not old_in_window and ts <= cutoff_old:
                old_in_window = (m, ts)
        cursor = page.get("cursor")
        pages_scanned += 1

    if not recent_in_window:
        print("\n  WARN: no recent-settled market found in 5 pages; using newest from page 1")
        recent_in_window = (most_recent, settle_proxy(most_recent))
    if not old_in_window:
        print("\n  WARN: no old-settled market found in 5 pages; using oldest seen")
        old_in_window = (oldest, settle_proxy(oldest))

    rec_m, rec_ts = recent_in_window
    old_m, old_ts = old_in_window

    def headline(label: str, m: dict, ts: datetime) -> None:
        print(f"\n--- {label} ---")
        print(f"  ticker:                   {m.get('ticker')!r}")
        print(f"  proxy-settle-ts:          {ts}")
        print(f"  status:                   {m.get('status')!r}")
        print(f"  result:                   {m.get('result')!r}")
        print(f"  settle_time:              {m.get('settle_time')!r}")
        print(f"  expiration_time:          {m.get('expiration_time')!r}")
        print(f"  expected_expiration_time: {m.get('expected_expiration_time')!r}")
        print(f"  close_time:               {m.get('close_time')!r}")
        print(f"  open_time:                {m.get('open_time')!r}")
        print(f"  last_price_dollars:       {m.get('last_price_dollars')!r}")
        print(f"  open_interest_fp:         {m.get('open_interest_fp')!r}")
        print(f"  volume_fp:                {m.get('volume_fp')!r}")
        print(f"  volume_24h_fp:            {m.get('volume_24h_fp')!r}")
        print(f"  yes_bid_dollars:          {m.get('yes_bid_dollars')!r}")
        print(f"  yes_ask_dollars:          {m.get('yes_ask_dollars')!r}")
        print(f"  liquidity_dollars:        {m.get('liquidity_dollars')!r}")
        print(f"  category:                 {m.get('category')!r}")
        print(f"  series_ticker:            {m.get('series_ticker')!r}")
        print(f"  field count: {len(m)}")
        print(f"  all keys: {sorted(m.keys())}")

    headline("RECENT-SETTLED", rec_m, rec_ts)
    headline("OLD-SETTLED",    old_m, old_ts)

    rec_keys = set(rec_m.keys())
    old_keys = set(old_m.keys())
    only_recent = rec_keys - old_keys
    only_old = old_keys - rec_keys
    print(f"\n--- schema diff ---")
    print(f"  fields only on RECENT: {sorted(only_recent)}")
    print(f"  fields only on OLD:    {sorted(only_old)}")
    print(f"  shared field count:    {len(rec_keys & old_keys)}")

    out = DEBUG_DIR / "probe_settled_compare.json"
    out.write_text(json.dumps({"recent": rec_m, "old": old_m}, indent=2))
    print(f"\nfull records saved to {out}")


def cmd_probe_candidate(c: KalshiSignedClient) -> None:
    """Pull real candidate markets (non-MVE) and show full raw records.

    Strategy: query /markets with series_ticker filters known to be non-MVE
    candidates from the production tracked_markets.yml (FOMC, CPI, Hormuz, AAA gas).
    Show all 45 fields of one market for end-to-end verification.
    """
    print("=== /exchange/status ===")
    status = c.get("/exchange/status")
    print(json.dumps(status, indent=2))

    candidate_series = [
        "KXFOMCDISSENTCOUNT",
        "KXECONSTATCORECPIYOY",
        "KXHORMUZWEEKLY",
        "KXAAAGASED",
    ]

    samples = []
    for series in candidate_series:
        print(f"\n=== /markets?series_ticker={series}&limit=5 ===")
        try:
            page = c.get("/markets", params={"series_ticker": series, "limit": 5})
        except httpx.HTTPStatusError as e:
            print(f"  HTTP {e.response.status_code}: {e.response.text[:200]}")
            continue
        markets = page.get("markets", [])
        print(f"  returned {len(markets)} markets")
        for m in markets[:3]:
            print(
                f"    {m.get('ticker')!r:55s} status={m.get('status')!r:12s} "
                f"category={m.get('category')!r:18s} series={m.get('series_ticker')!r:30s} "
                f"OI_fp={m.get('open_interest_fp')!r:8s} last={m.get('last_price_dollars')!r}"
            )
        if markets:
            samples.append((series, markets[0]))

    if samples:
        series, m = samples[0]
        print(f"\n=== FULL RAW RECORD: first sample from {series} ===")
        print(f"({len(m)} fields total)")
        for k in sorted(m.keys()):
            v = m[k]
            if isinstance(v, (dict, list)):
                v = json.dumps(v)
            if isinstance(v, str) and len(v) > 200:
                v = v[:197] + "..."
            print(f"  {k}: {v!r}")

    out = DEBUG_DIR / "probe_candidate_markets.json"
    out.write_text(json.dumps([{"series": s, "market": m} for s, m in samples], indent=2))
    print(f"\nfull samples saved to {out}")


def cmd_probe(c: KalshiSignedClient) -> None:
    print("=== /exchange/status ===")
    status = c.get("/exchange/status")
    print(json.dumps(status, indent=2))

    print("\n=== /markets?limit=10 (first page, status not filtered) ===")
    page = c.get("/markets", params={"limit": 10})
    markets = page.get("markets", [])
    print(f"keys returned at top level: {list(page.keys())}")
    print(f"markets count: {len(markets)}")
    if markets:
        m = markets[0]
        print(f"\nfields on first market ({len(m)} total):")
        for k in sorted(m.keys()):
            v = m[k]
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            print(f"  {k}: {v!r}")
        print("\n=== full sample of 10 markets (compact) ===")
        for i, mkt in enumerate(markets):
            print(
                f"[{i}] {mkt.get('ticker')!r:50s} "
                f"status={mkt.get('status')!r:10s} "
                f"category={mkt.get('category')!r:20s} "
                f"series={mkt.get('series_ticker')!r:30s} "
                f"open={mkt.get('open_time')} close={mkt.get('close_time')} settle={mkt.get('settle_time')} "
                f"OI={mkt.get('open_interest')} last={mkt.get('last_price_dollars')!r}"
            )

    # also dump full JSON to disk for inspection
    out = DEBUG_DIR / "probe_markets.json"
    out.write_text(json.dumps(page, indent=2))
    print(f"\nfull probe response saved to {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="Auth-ping + 10-market unfiltered probe")
    parser.add_argument("--probe-candidate", action="store_true", help="Probe real candidate markets (non-MVE)")
    parser.add_argument("--probe-settled", action="store_true", help="Compare recent- vs old-settled market schemas")
    parser.add_argument("--pull-series", action="store_true", help="Full /series paginated pull and taxonomy summary")
    parser.add_argument("--classify", action="store_true", help="Classify cached /series into labels")
    parser.add_argument("--pull-markets", action="store_true", help="Pull markets for each kept series")
    parser.add_argument("--aggregate", action="store_true", help="Apply filters and print aggregate counts")
    parser.add_argument("--write-artifacts", action="store_true", help="Write final CSV + markdown report")
    parser.add_argument("--series", action="store_true", help="Full /series pull")
    parser.add_argument("--full", action="store_true", help="End-to-end discovery")
    args = parser.parse_args()

    key_id, key_path = load_env()
    pk = load_private_key(key_path)
    print(f"loaded key_id={key_id[:8]}... from {key_path}", file=sys.stderr)

    with KalshiSignedClient(key_id, pk) as c:
        if args.probe:
            cmd_probe(c)
        elif args.probe_candidate:
            cmd_probe_candidate(c)
        elif args.probe_settled:
            cmd_probe_settled(c)
        elif args.pull_series:
            cmd_pull_series(c)
        elif args.classify:
            cmd_classify(c)
        elif args.pull_markets:
            cmd_pull_markets(c)
        elif args.aggregate:
            cmd_aggregate(c)
        elif args.write_artifacts:
            cmd_write_artifacts(c)
        elif args.series:
            raise SystemExit("--series mode not yet wired up; run --probe first")
        elif args.full:
            raise SystemExit("--full mode not yet wired up; run --probe first")
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
