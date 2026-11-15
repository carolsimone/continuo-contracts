"""The files whose bytes are pinned are marked so that no checkout rewrites their line endings."""

import shutil
import subprocess

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "continuo_contracts"


def _pinned_files():
    return sorted(
        [
            *PACKAGE.glob("*.json"),
            *(PACKAGE / "vectors").glob("*.json"),
            *(
                path
                for path in (ROOT / "tests" / "fixtures").rglob("*")
                if path.is_file()
            ),
        ]
    )


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


@pytest.fixture(scope="module")
def in_a_checkout():
    if (
        shutil.which("git") is None
        or _git("rev-parse", "--is-inside-work-tree").returncode != 0
    ):
        pytest.skip("the check reads git's attributes and runs in a git checkout")


def test_there_are_pinned_files_to_check():
    names = {path.name for path in _pinned_files()}
    assert {
        "odcs-json-schema-v3.2.0.json",
        "contracts.json",
        "py_daily_kpis.odcs.yaml",
    } <= names


def test_a_pinned_file_has_its_line_endings_left_alone(in_a_checkout):
    files = [path.relative_to(ROOT).as_posix() for path in _pinned_files()]
    done = _git("check-attr", "text", "--", *files)
    assert done.returncode == 0, done.stderr
    states = {
        line.split(": ", 2)[0]: line.split(": ", 2)[2]
        for line in done.stdout.splitlines()
    }
    assert states == dict.fromkeys(files, "unset")
