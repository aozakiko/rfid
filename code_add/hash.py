"""
Compatibility shim: provide `simple_hash` used across protocols.
This duplicates the lightweight hash used in `base.py` so modules that
`from hash import simple_hash` can import it.
"""

from typing import Any


def simple_hash(val1: Any, val2: int) -> int:
    """Return a non-negative integer hash of the two inputs.

    This mirrors the implementation in `base.py` so both `from base import simple_hash`
    and `from hash import simple_hash` produce the same result.
    """
    return abs(hash(str(val1) + str(val2)))
