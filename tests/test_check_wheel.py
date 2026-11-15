import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_wheel.py"
spec = importlib.util.spec_from_file_location("check_wheel", SCRIPT)
check_wheel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_wheel)

METADATA = (
    "Metadata-Version: 2.4\n"
    "Name: continuo-contracts\n"
    "Version: 0.1.0\n"
    "Requires-Python: >=3.12\n"
    "License-Expression: Apache-2.0\n"
    "Requires-Dist: jsonschema>=4.26\n"
    "Requires-Dist: PyYAML>=6.0\n"
)
ENTRY_POINTS = "[console_scripts]\ncontinuo-contracts = continuo_contracts.cli:main\n"
INFO = "continuo_contracts-0.1.0.dist-info"


def _wheel(
    tmp_path, name="continuo_contracts-0.1.0-py3-none-any.whl", skip=(), entry_points=ENTRY_POINTS
):
    """Write a wheel with every required member. `entry_points` is the text of its
    `entry_points.txt`, or `None` for a wheel without one."""
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member in check_wheel.REQUIRED_MEMBERS:
            if member not in skip:
                zf.writestr(member, "x")
        zf.writestr(f"{INFO}/METADATA", METADATA)
        zf.writestr(f"{INFO}/licenses/LICENSE", "x")
        zf.writestr(f"{INFO}/licenses/NOTICE", "x")
        if entry_points is not None:
            zf.writestr(f"{INFO}/entry_points.txt", entry_points)
    return path


def test_a_complete_wheel_has_no_problems(tmp_path):
    assert check_wheel.wheel_problems(_wheel(tmp_path)) == []


def test_a_wheel_without_its_data_files_is_refused(tmp_path):
    data = (
        "continuo_contracts/fingerprints.yaml",
        "continuo_contracts/odcs-json-schema-v3.2.0.json",
        "continuo_contracts/vectors/canonical_json.json",
        "continuo_contracts/vectors/contracts.json",
        "continuo_contracts/vectors/hashing.json",
        "continuo_contracts/vectors/projection.json",
        "continuo_contracts/vectors/sql_types.json",
        "continuo_contracts/vectors/strict_yaml.json",
    )
    problems = check_wheel.wheel_problems(_wheel(tmp_path, skip=data))
    assert all(any(member in p for p in problems) for member in data)


def _is_package_content(relative):
    """Say whether the file at `relative`, a path inside the package, is part of the package.

    Bytecode and anything hidden (a name that starts with a dot, such as the `.DS_Store` a file
    manager writes) is the machine's own output and is not part of the package.
    """
    return (
        "__pycache__" not in relative.parts
        and relative.suffix != ".pyc"
        and not any(part.startswith(".") for part in relative.parts)
    )


def _package_files(package_dir):
    """Return the check's member name of every file under `package_dir`, bytecode and hidden
    files excluded."""
    return {
        f"{check_wheel.PACKAGE}/{path.relative_to(package_dir).as_posix()}"
        for path in package_dir.rglob("*")
        if path.is_file() and _is_package_content(path.relative_to(package_dir))
    }


def test_the_required_members_are_the_files_of_the_package_tree():
    package_dir = SCRIPT.parents[1] / check_wheel.PACKAGE
    listed = list(check_wheel.REQUIRED_MEMBERS)
    on_disk = _package_files(package_dir)
    assert sorted(on_disk - set(listed)) == [], "files of the package that REQUIRED_MEMBERS omits"
    assert sorted(set(listed) - on_disk) == [], "REQUIRED_MEMBERS entries with no file"
    assert len(listed) == len(set(listed)), "REQUIRED_MEMBERS lists a member twice"


def test_a_platform_wheel_is_refused(tmp_path):
    problems = check_wheel.wheel_problems(
        _wheel(tmp_path, name="continuo_contracts-0.1.0-cp312-cp312-linux_x86_64.whl")
    )
    assert any("py3-none-any" in p for p in problems)


def test_a_wheel_without_entry_points_is_refused(tmp_path):
    problems = check_wheel.wheel_problems(_wheel(tmp_path, entry_points=None))
    assert any("missing member" in p and "entry_points.txt" in p for p in problems)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "[console_scripts]\n",
        "[console_scripts]\nother = continuo_contracts.cli:main\n",
        "[gui_scripts]\ncontinuo-contracts = continuo_contracts.cli:main\n",
        "[console_scripts]\ncontinuo-contracts = continuo_contracts.cli:run\n",
        "[console_scripts]\ncontinuo-contracts = continuo_contracts:main\n",
        "[console_scripts]\nContinuo-Contracts = continuo_contracts.cli:main\n",
        "continuo-contracts = continuo_contracts.cli:main\n",
    ],
)
def test_a_wheel_without_the_console_script_is_refused(tmp_path, text):
    problems = check_wheel.wheel_problems(_wheel(tmp_path, entry_points=text))
    assert [p for p in problems if "entry_points.txt" in p], problems


def test_the_console_script_may_sit_beside_others(tmp_path):
    text = ENTRY_POINTS + "other = other.module:main\n[gui_scripts]\ng = g:main\n"
    assert check_wheel.wheel_problems(_wheel(tmp_path, entry_points=text)) == []
