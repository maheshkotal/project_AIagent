from __future__ import annotations

from agentforge.tools.checks import scrub
from agentforge.tools.sandbox import Sandbox


def test_scrub_removes_run_specific_noise(tmp_path) -> None:
    ws = Sandbox(tmp_path / "runs" / "20261105-120000-abc123" / "workspace")
    text = (
        f'File "{ws.root}/app/main.py", line 3\n'
        "generated xml file: /private/var/folders/xy/T/tmpab12/junit.xml\n"
        "E   AssertionError at 0x10a2b3c4d\n"
        "1 failed, 3 passed in 0.42s"
    )
    assert scrub(text, ws) == (
        'File "<workspace>/app/main.py", line 3\n'
        "generated xml file: <tmp>\n"
        "E   AssertionError at 0x...\n"
        "1 failed, 3 passed in <t>s"
    )
