"""RSA-PSS auth helpers for authenticated Kalshi API calls.

Mirrors the RSA-PSS pattern in scripts/discover_universe.py:62-106.
Per notes/simulator-design.md §2, the simulator authenticates calls
to the Kalshi candlesticks endpoint via this pattern.

Note on the spec citation: simulator-design.md §2 references
scripts/build_test_b_universe.py as a source of the same pattern.
That script actually uses the public unauthenticated endpoint
(candle-data-probe.md §1 confirmed candlesticks works without auth).
The RSA-PSS pattern lives in scripts/discover_universe.py only; this
module is the canonical reusable home for it inside simulator/.

Sign (timestamp_ms_str + METHOD + full_path) with
RSA-PSS / SHA256 / MGF1 / salt_length=DIGEST_LENGTH.
full_path includes /trade-api/v2 and excludes the query string.
"""
from __future__ import annotations

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def load_private_key(key_path: str | Path):
    """Load an RSA private key from a PEM file. Raises if the file is
    missing or the key cannot be parsed."""
    pem = Path(key_path).read_bytes()
    return serialization.load_pem_private_key(pem, password=None)


def sign_request(private_key, method: str, full_path: str) -> dict[str, str]:
    """Return the two signed headers (TIMESTAMP, SIGNATURE) for a
    Kalshi authenticated request. `full_path` is the request path
    including /trade-api/v2 prefix and excluding the query string."""
    timestamp = str(int(time.time() * 1000))
    msg = (timestamp + method.upper() + full_path).encode()
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


def auth_headers(key_id: str, private_key, method: str,
                 full_path: str) -> dict[str, str]:
    """Return the full set of headers for an authenticated Kalshi
    request: KALSHI-ACCESS-KEY plus the two signed headers."""
    return {"KALSHI-ACCESS-KEY": key_id,
            **sign_request(private_key, method, full_path)}
