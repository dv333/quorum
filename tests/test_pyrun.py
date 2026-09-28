from pathlib import Path

import pytest

from backend import pyrun

OUTSIDE = str(Path.home() / "quorum-check.txt")  # the real home: a program's own ~ is its temporary folder

sandboxed = pytest.mark.skipif(pyrun.sandbox() is None, reason="no sandbox on this computer")


def test_programs_are_found_after_an_at_python_line():
    text = "Check:\n@Python:\n```python\nprint(1)\n```\nand\n@python\n```py\nprint(2)\n```\n```python\nprint(3)\n```"
    assert pyrun.parse_requests(text) == ["print(1)", "print(2)"]  # a block without @Python isn't run
    assert pyrun.parse_requests("```python\nprint(1)\n```") == []


def test_text_written_after_the_program_is_dropped():
    text = "Let me check.\n@Python:\n```python\nprint(6 * 7)\n```\nIt's 40.\nSTANCE: AGREE"
    assert pyrun.before_output(text) == "Let me check.\n@Python:\n```python\nprint(6 * 7)\n```"
    assert pyrun.before_output("no program") == "no program"


def test_a_traceback_is_cut_to_the_line_and_the_error():
    tb = 'Traceback:\n  File "/tmp/x/guard.py", line 22\n  File "check.py", line 3, in <module>\n    x[3]\nIndexError: nope'
    assert pyrun.error_summary(tb) == 'File "check.py", line 3, in <module>\nIndexError: nope'


def test_checks_are_off_when_turned_off(monkeypatch):
    monkeypatch.setenv("QUORUM_PYTHON", "off")
    assert pyrun.sandbox() is None


@sandboxed
async def test_a_program_prints_its_result():
    r = await pyrun.run(
        "import itertools, math\nprint(sum(1 for p in itertools.permutations(range(5))), math.comb(9, 5))"
    )
    assert r.ok and r.output == "120 126"


@sandboxed
@pytest.mark.parametrize(
    "code",
    [
        "import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=2)",
        "import subprocess\nsubprocess.run(['ls'])",
        "import os\nos.system('ls')",
        "open('x.txt', 'w').write('x')",
        f"import os\nos.open({OUTSIDE!r}, os.O_CREAT | os.O_WRONLY)",
    ],
)
async def test_a_program_cant_reach_the_network_write_files_or_start_programs(code):
    r = await pyrun.run(code)
    assert not r.ok and "Error" in r.output
    assert not Path(OUTSIDE).exists()


@sandboxed
async def test_a_program_that_runs_too_long_is_stopped(monkeypatch):
    monkeypatch.setattr(pyrun, "TIMEOUT_S", 1)
    r = await pyrun.run("while True:\n    pass")
    assert not r.ok and "Stopped" in r.output
