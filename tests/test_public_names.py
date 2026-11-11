"""Every public name of the package has a line in the README section of its module.

A public name is a module-level name without a leading underscore that the module defines (a
class, a function or a constant, not an import). The README section of the module ends with a
`### Public names` list whose items each start with one such name in backticks. The item of a
class also names, in backticks, the public fields and methods of that class.
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
MODULES = ("canonical", "hashing", "python", "fingerprints", "errors")
LISTING = "### Public names\n"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _assigned_names(node: ast.stmt) -> list[str]:
    if isinstance(node, ast.Assign):
        return [target.id for target in node.targets if isinstance(target, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _public(names: list[str]) -> list[str]:
    return [name for name in names if not name.startswith("_")]


def _defined(module: str) -> dict[str, set[str]]:
    """Map each public name that `module` defines to the public members of that name."""
    source = inspect.getsource(importlib.import_module(f"continuo_contracts.{module}"))
    defined: dict[str, set[str]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef):
            members: list[str] = []
            for member in node.body:
                if isinstance(member, ast.FunctionDef):
                    members.append(member.name)
                members += _assigned_names(member)
            defined[node.name] = set(_public(members))
        elif isinstance(node, ast.FunctionDef):
            defined[node.name] = set()
        else:
            defined.update({name: set() for name in _assigned_names(node)})
    return {name: members for name, members in defined.items() if not name.startswith("_")}


def _listed(module: str) -> list[tuple[str, str]]:
    """Return the items of the `### Public names` list in the README section of `module`, as
    (name, text of the item)."""
    sections = {
        part.split("\n", 1)[0].strip(): part
        for part in re.split(r"^## ", README.read_text(encoding="utf-8"), flags=re.MULTILINE)
    }
    assert module in sections, f"the README has no `## {module}` section"
    assert LISTING in sections[module], f"the README section {module} has no `{LISTING.strip()}`"
    listing = sections[module].split(LISTING, 1)[1]
    items = []
    for chunk in re.split(r"^(?=- )", listing, flags=re.MULTILINE):
        named = re.match(r"- `([A-Za-z_][A-Za-z0-9_]*)`", chunk)
        if named:
            items.append((named.group(1), chunk))
    return items


def _backticked_words(text: str) -> set[str]:
    return {word for span in re.findall(r"`([^`]*)`", text) for word in IDENTIFIER.findall(span)}


def test_every_module_of_the_package_is_checked():
    on_disk = sorted(
        path.stem for path in (ROOT / "continuo_contracts").glob("*.py") if path.stem != "__init__"
    )
    assert on_disk == sorted(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_the_readme_lists_each_public_name_once_and_nothing_else(module):
    defined = set(_defined(module))
    listed = [name for name, _ in _listed(module)]
    assert sorted(defined - set(listed)) == [], "public names without a README line"
    assert sorted(set(listed) - defined) == [], "README lines for names the module does not define"
    assert sorted({name for name in listed if listed.count(name) > 1}) == [], "listed twice"


@pytest.mark.parametrize("module", MODULES)
def test_the_readme_item_of_a_class_names_its_fields_and_methods(module):
    items = dict(_listed(module))
    for name, members in _defined(module).items():
        if name in items:
            missing = sorted(members - _backticked_words(items[name]))
            assert missing == [], f"{module}.{name}: members the README item does not name"


def test_the_scan_sees_every_field_of_entry():
    ten_fields = set("family id field source fingerprint parts why inputs when rule".split())
    assert ten_fields <= _defined("fingerprints")["Entry"]
    assert ten_fields <= _backticked_words(dict(_listed("fingerprints"))["Entry"])
