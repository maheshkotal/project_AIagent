from __future__ import annotations

import pytest

from agentforge.tools.sandbox import Sandbox, SandboxViolation, is_secret_path


def test_writes_and_lists_files_inside_root(tmp_path) -> None:
    box = Sandbox(tmp_path / "ws")
    box.write_text("app/main.py", "print('hi')")
    assert box.read_text("app/main.py") == "print('hi')"
    assert box.list_files() == ["app/main.py"]


@pytest.mark.parametrize("path", ["../escape.py", "app/../../escape.py", "/etc/passwd", "."])
def test_rejects_paths_outside_root(tmp_path, path) -> None:
    box = Sandbox(tmp_path / "ws")
    with pytest.raises(SandboxViolation):
        box.write_text(path, "x")


def test_rejects_symlink_escape(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    box = Sandbox(tmp_path / "ws")
    (box.root / "link").symlink_to(outside)
    with pytest.raises(SandboxViolation):
        box.write_text("link/evil.py", "x")


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "id_rsa", "credentials-prod.json"])
def test_secret_files_are_blocked(tmp_path, name) -> None:
    assert is_secret_path(f"config/{name}")
    with pytest.raises(SandboxViolation):
        Sandbox(tmp_path).write_text(name, "secret")


def test_rejects_oversized_files(tmp_path) -> None:
    with pytest.raises(SandboxViolation):
        Sandbox(tmp_path).write_text("big.txt", "x" * (600 * 1024))
