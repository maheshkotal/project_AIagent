"""Executed in a subprocess inside the workspace: prints the generated app's OpenAPI document as JSON.

Kept out of the agentforge process so that importing untrusted generated code cannot affect it.
"""

from __future__ import annotations

import json
import sys
import tempfile


def main() -> int:
    from app.main import create_app  # resolved from the workspace via PYTHONPATH

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(f"sqlite:///{tmp}/probe.db")
        json.dump(app.openapi(), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
