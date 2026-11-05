"""Brownfield support: import an existing repository into a run, and diff the result against it."""

from __future__ import annotations

import difflib
from pathlib import Path

from agentforge.tools.sandbox import MAX_FILE_BYTES, Sandbox, is_secret_path

IGNORED_DIRS = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache", "runs"}
)
IGNORED_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3", ".pyc", ".log"})


def import_repo(source: Path, *targets: Sandbox) -> list[str]:
    """Copy text files from `source` into each target sandbox. Secrets, binaries and caches are skipped."""
    if not source.is_dir():
        raise FileNotFoundError(f"Repository not found: {source}")
    imported = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if (
            path.is_symlink()
            or not path.is_file()
            or any(part in IGNORED_DIRS for part in rel.parts)
            or path.suffix in IGNORED_SUFFIXES
            or is_secret_path(rel)
            or path.stat().st_size > MAX_FILE_BYTES
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary file
        for target in targets:
            target.write_text(rel.as_posix(), text)
        imported.append(rel.as_posix())
    return imported


def _is_diffable(path: str) -> bool:
    return Path(path).suffix not in IGNORED_SUFFIXES


def unified_diff(before: Sandbox, after: Sandbox) -> str:
    paths = sorted({p for p in [*before.list_files(), *after.list_files()] if _is_diffable(p)})
    chunks: list[str] = []
    for path in paths:
        old = before.read_text(path).splitlines(keepends=True) if before.exists(path) else []
        new = after.read_text(path).splitlines(keepends=True) if after.exists(path) else []
        if old != new:
            chunks.extend(
                difflib.unified_diff(
                    old, new, fromfile=f"a/{path}" if old else "/dev/null", tofile=f"b/{path}" if new else "/dev/null"
                )
            )
    return "".join(chunks)


def diff_stats(diff: str) -> dict[str, tuple[int, int]]:
    """Per-file (added, removed) line counts from a unified diff."""
    stats: dict[str, tuple[int, int]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[4:].removeprefix("b/")
            if current == "/dev/null":
                current = None
        elif line.startswith("--- "):
            current = line[4:].removeprefix("a/")
        elif current and line.startswith("+") and not line.startswith("+++"):
            added, removed = stats.get(current, (0, 0))
            stats[current] = (added + 1, removed)
        elif current and line.startswith("-") and not line.startswith("---"):
            added, removed = stats.get(current, (0, 0))
            stats[current] = (added, removed + 1)
    return stats
