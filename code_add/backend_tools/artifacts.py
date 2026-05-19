from __future__ import annotations

from pathlib import Path


def snapshot(project_root: Path) -> set[Path]:
    patterns = ["*.csv", "*.pdf", "*.png", "*.jpg", "*.jpeg", "*.txt", "*.json"]
    matched: list[Path] = []

    for pattern in patterns:
        matched.extend(project_root.glob(pattern))
        matched.extend((project_root / "Experiments").glob(pattern))

    return {p.resolve() for p in matched if p.exists()}


def diff(before: set[Path], after: set[Path], project_root: Path) -> list[Path]:
    new_files = sorted(after - before, key=lambda p: p.name.lower())
    rel: list[Path] = []

    for p in new_files:
        p = p.resolve()
        try:
            rel.append(p.relative_to(project_root.resolve()))
        except Exception:
            rel.append(p)

    return rel
