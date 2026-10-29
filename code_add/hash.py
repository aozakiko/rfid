"""
Compatibility shim: provide `simple_hash` used across protocols.
This duplicates the lightweight hash used in `base.py` so modules that
`from hash import simple_hash` can import it.
"""

import hashlib
from typing import Any


def simple_hash(val1: Any, val2: int) -> int:
    """Return a non-negative integer hash of the two inputs.

    This mirrors the implementation in `base.py` so both `from base import simple_hash`
    and `from hash import simple_hash` produce the same result.
    """
    payload = f"{val1}\x1f{val2}".encode("utf-8", errors="replace")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
