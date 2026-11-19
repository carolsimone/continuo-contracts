"""Every link of the documentation names a file of the repository and a heading of it.

A relative target, or an absolute target on this repository's `main` branch, must name an
existing file and, after `#`, an existing heading of that file. Other absolute URLs are not
checked: the tests use no network.
"""

import re

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    *sorted((ROOT / "docs").glob("*.md")),
]
REPOSITORY = "https://github.com/carolsimone/continuo-contracts/blob/main/"
FENCE = re.compile(r"^ {0,3}(```|~~~)")
INLINE = re.compile(r"\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+\"[^\"]*\")?\s*$")
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def _lines_outside_fences(path: Path) -> list[tuple[int, str]]:
    lines, fenced = [], False
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            lines.append((number, line))
    return lines


def _anchors(path: Path) -> set[str]:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to `-`, and a
    repeated anchor gets `-1`, `-2` and so on."""
    seen: dict[str, int] = {}
    for _, line in _lines_outside_fences(path):
        heading = HEADING.match(line)
        if heading:
            words = re.sub(r"[^\w\- ]", "", heading.group(1).lower())
            slug = words.replace(" ", "-")
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            if count:
                seen[f"{slug}-{count}"] = 1
    return set(seen)


def _broken(page: Path, target: str) -> str | None:
    if target.startswith(REPOSITORY):
        file, _, anchor = target.removeprefix(REPOSITORY).partition("#")
        path = ROOT / file
    elif re.match(r"[a-z][a-z0-9+.-]*:", target):
        return None
    else:
        file, _, anchor = target.partition("#")
        path = (page.parent / file).resolve() if file else page
    if not path.is_file():
        return "no such file"
    if anchor and path.suffix == ".md" and anchor not in _anchors(path):
        return f"no heading #{anchor}"
    return None


@pytest.mark.parametrize("page", PAGES, ids=lambda page: page.name)
def test_every_link_names_an_existing_file_and_heading(page):
    broken = [
        f"line {number}: {target}: {problem}"
        for number, line in _lines_outside_fences(page)
        for target in INLINE.findall(line) + REFERENCE.findall(line)
        if (problem := _broken(page, target))
    ]
    assert broken == []
