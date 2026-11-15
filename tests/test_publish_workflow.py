"""The publish workflow asks the index for the package as the Python it supports sees it."""

import re

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-pypi.yml"


def _verify_index_script():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["verify-index"]["steps"]
    return "\n".join(step.get("run", "") for step in steps)


def _pip_downloads(script):
    # A command continued with a trailing backslash is one command.
    joined = re.sub(r"\\\n\s*", " ", script)
    return [line for line in joined.splitlines() if "pip download" in line]


def test_verify_index_downloads_the_package():
    assert len(_pip_downloads(_verify_index_script())) == 1


def test_the_download_names_the_python_of_the_matrix():
    # The runner's own python3 is older than the package's Requires-Python, so pip
    # would skip the release unless it is told which Python to select for.
    (download,) = _pip_downloads(_verify_index_script())
    assert '--python-version "$PY_VERSION"' in download


def test_the_matrix_python_is_the_one_the_package_requires():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["verify-index"]
    assert job["env"]["PY_VERSION"] == "${{ matrix.python }}"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    required = re.search(r'requires-python = ">=(\d+\.\d+)"', pyproject)
    assert required is not None
    assert job["strategy"]["matrix"]["python"] == [required.group(1)]
