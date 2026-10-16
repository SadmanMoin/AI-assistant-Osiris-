"""Filesystem checks for explicitly configured directories."""

from __future__ import annotations

from pathlib import Path


def is_within(path: Path, root: Path) -> bool:
    """True when `path` stays inside `root` after resolving symlinks."""
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def directory_roots(raw_paths: list[str]) -> list[Path]:
    roots = []
    for raw in raw_paths:
        if not raw or not str(raw).strip():
            continue
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            roots.append(candidate.resolve())
    return roots
