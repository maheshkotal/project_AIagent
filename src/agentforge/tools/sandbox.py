"""Filesystem sandbox: agents may only read/write inside a run's workspace directory."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 512 * 1024

# Files that must never be sent to an LLM or written by an agent.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "id_rsa*",
    "id_ed25519*",
    "credentials*.json",
    "secrets.*",
)


class SandboxViolation(Exception):
    pass


def is_secret_path(path: str | Path) -> bool:
    name = PurePosixPath(str(path)).name
    return any(fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


class Sandbox:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def resolve(self, relative: str | Path) -> Path:
        """Map a workspace-relative path to an absolute path, rejecting anything that escapes the root."""
        rel = Path(relative)
        if rel.is_absolute():
            raise SandboxViolation(f"Absolute paths are not allowed: {relative}")
        candidate = (self.root / rel).resolve()  # resolves '..' and symlinks
        if candidate == self.root or not candidate.is_relative_to(self.root):
            raise SandboxViolation(f"Path escapes the workspace: {relative}")
        return candidate

    def write_text(self, relative: str | Path, content: str) -> Path:
        if is_secret_path(relative):
            raise SandboxViolation(f"Refusing to write secret-like file: {relative}")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise SandboxViolation(f"File exceeds {MAX_FILE_BYTES} bytes: {relative}")
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def read_text(self, relative: str | Path) -> str:
        if is_secret_path(relative):
            raise SandboxViolation(f"Refusing to read secret-like file: {relative}")
        return self.resolve(relative).read_text(encoding="utf-8")

    def exists(self, relative: str | Path) -> bool:
        return self.resolve(relative).exists()

    def list_files(self) -> list[str]:
        return sorted(
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and not is_secret_path(p) and "__pycache__" not in p.parts
        )
