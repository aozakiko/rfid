"""Project verification helpers."""


def verify(*args, **kwargs):
    """Lazy wrapper so `python -m tools.verify` runs without import warnings."""
    from .verify import verify as _verify

    return _verify(*args, **kwargs)


def main(*args, **kwargs):
    from .verify import main as _main

    return _main(*args, **kwargs)


__all__ = ["main", "verify"]