"""The import graph of CONTRIBUTING.md, checked mechanically.

import-linter (`uv run lint-imports`) enforces the contracts in `pyproject.toml`. These
tests cover what it cannot say by itself:

- no contract carries a baseline of ignored imports;
- the layers name every module of the package once, and are exhaustive, so a new module
  cannot sit outside the graph;
- each third-party confinement names every module but the ones that may use the library,
  so a new module cannot import the library unnoticed;
- nothing in the package imports `scripts` or `tests`, which import-linter cannot see;
- the contracts fail when an import crosses them, so they cannot silently stop working.
"""

import ast
import re
import shutil
import subprocess
import sys
import tomllib

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "continuo_contracts"
PYPROJECT = ROOT / "pyproject.toml"

# The only modules that may import each confined library. A new module that needs one is
# named here and left out of the contract's `source_modules`, both on purpose.
CONFINED = {
    "jsonschema": {"validate"},
    "yaml": {"fingerprints", "yaml_strict"},
    "logging": {"cli"},
}


def _contracts() -> list[dict]:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return config["tool"]["importlinter"]["contracts"]


def _modules(package: Path) -> set[str]:
    """The names of the modules directly under `package`."""
    return {
        path.stem for path in package.glob("*.py") if path.name != "__init__.py"
    }


def _layer_modules(layer: str) -> list[str]:
    """The modules of one layer: `"a | b"` is `["a", "b"]`, `":"` joins them too."""
    return [name for name in re.split(r"\s*[|:]\s*", layer) if name]


def _imported_names(path: Path) -> set[str]:
    """The top-level names of the modules `path` imports with an absolute import."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_contract_ignores_an_import() -> None:
    """The graph in CONTRIBUTING.md is the spec: a baseline of exceptions cannot creep in."""
    with_baseline = [c["name"] for c in _contracts() if "ignore_imports" in c]

    assert with_baseline == []


def test_the_layers_name_every_module_of_the_package_once() -> None:
    [graph] = [c for c in _contracts() if c["type"] == "layers"]
    named = [m for layer in graph["layers"] for m in _layer_modules(layer)]

    assert graph["exhaustive"] is True
    assert graph["containers"] == ["continuo_contracts"]
    assert sorted(named) == sorted(_modules(PACKAGE)), (
        "a new module goes in a layer of the `layers` contract in pyproject.toml"
    )


def test_cli_is_the_top_layer_and_the_bottom_layer_imports_nothing() -> None:
    [graph] = [c for c in _contracts() if c["type"] == "layers"]

    assert graph["layers"][0] == "cli"
    assert graph["layers"][-1] == "errors | vocabulary"


@pytest.mark.parametrize("library", CONFINED)
def test_each_confined_library_is_forbidden_to_every_other_module(
    library: str,
) -> None:
    [contract] = [
        c for c in _contracts() if c.get("forbidden_modules") == [library]
    ]
    named = {
        name.removeprefix("continuo_contracts.")
        for name in contract["source_modules"]
    }

    assert contract["type"] == "forbidden"
    assert named == _modules(PACKAGE) - CONFINED[library], (
        f"every module but {sorted(CONFINED[library])} is a source module of the "
        f"contract that forbids `{library}`"
    )


def test_the_modules_that_use_a_confined_library_do_import_it() -> None:
    """An allowance that outlives its use would let a later import in unnoticed."""
    for library, users in CONFINED.items():
        for user in users:
            assert library in _imported_names(PACKAGE / f"{user}.py")


def test_no_module_of_the_package_imports_scripts_or_tests() -> None:
    offenders = {
        path.name: found
        for path in sorted(PACKAGE.glob("*.py"))
        if (found := _imported_names(path) & {"scripts", "tests"})
    }

    assert offenders == {}


def test_a_planted_import_of_scripts_or_tests_is_found(tmp_path: Path) -> None:
    """The control for the test above: it finds an import, in each form."""
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import os\nimport scripts.check_wheel\nfrom tests import helper\n",
        encoding="utf-8",
    )

    assert _imported_names(planted) & {"scripts", "tests"} == {"scripts", "tests"}


def _copy_package(root: Path) -> Path:
    """Copy the modules of the package into `root`, so a test can change them freely."""
    copy = root / "continuo_contracts"
    shutil.copytree(
        PACKAGE,
        copy,
        ignore=lambda _folder, names: [
            name for name in names if not name.endswith(".py")
        ],
    )
    return copy


def _lint_imports(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the venv's lint-imports on the package copy in `root`, with the real contracts.

    lint-imports puts its working directory first on the import path, so the copy wins
    over the installed package.
    """
    shutil.copy(PYPROJECT, root / "pyproject.toml")
    executable = Path(sys.executable).with_name("lint-imports")
    return subprocess.run(
        [str(executable), "--config", str(root / "pyproject.toml")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_contracts_pass_on_an_unchanged_package_copy(tmp_path: Path) -> None:
    """The control for the planted offences below: only a planted import breaks one."""
    _copy_package(tmp_path)

    result = _lint_imports(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 kept, 0 broken" in result.stdout, result.stdout


# Each offence: the module it is added to, the code, the contract it breaks, and the edge
# import-linter names.
OFFENCES = {
    "a library module imports cli": (
        "hashing",
        "from continuo_contracts import cli\n",
        "import-graph",
        "continuo_contracts.hashing is not allowed to import",
    ),
    "the bottom layer imports a higher one": (
        "errors",
        "from continuo_contracts import canonical\n",
        "import-graph",
        "continuo_contracts.errors is not allowed to import",
    ),
    "the bottom layer's modules import each other": (
        "vocabulary",
        "from continuo_contracts import errors\n",
        "import-graph",
        "continuo_contracts.vocabulary is not allowed to import",
    ),
    "two modules of one layer import each other": (
        "canonical",
        "from continuo_contracts import hashing\n",
        "import-graph",
        "continuo_contracts.canonical is not allowed to import",
    ),
    "jsonschema in a module that may not use it": (
        "canonical",
        "import jsonschema\n",
        "jsonschema-is-confined-to-validate",
        "continuo_contracts.canonical is not allowed to import jsonschema",
    ),
    "yaml in a module that may not use it": (
        "python",
        "import yaml\n",
        "yaml-is-confined-to-fingerprints-and-yaml-strict",
        "continuo_contracts.python is not allowed to import yaml",
    ),
    "logging in a library module": (
        "validate",
        "import logging\n",
        "only-cli-logs",
        "continuo_contracts.validate is not allowed to import logging",
    ),
}


@pytest.mark.parametrize("offence", OFFENCES)
def test_a_planted_import_breaks_its_contract(
    offence: str, tmp_path: Path
) -> None:
    module, code, contract, edge = OFFENCES[offence]
    copy = _copy_package(tmp_path)
    with (copy / f"{module}.py").open("a", encoding="utf-8") as out:
        out.write(f"\n{code}")

    result = _lint_imports(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(rf"{contract}\s+BROKEN", result.stdout), result.stdout
    # import-linter wraps a long line, so compare with the whitespace collapsed.
    assert edge in " ".join(result.stdout.split()), result.stdout


def test_a_module_that_no_layer_names_breaks_the_layers(tmp_path: Path) -> None:
    copy = _copy_package(tmp_path)
    (copy / "extra.py").write_text('"""Planted."""\n', encoding="utf-8")

    result = _lint_imports(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert re.search(r"import-graph\s+BROKEN", result.stdout), result.stdout
    assert "continuo_contracts.extra" in result.stdout, result.stdout
