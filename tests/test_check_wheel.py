import importlib.util
import zipfile
from pathlib import Path

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
    "Requires-Dist: PyYAML>=6.0\n"
)
INFO = "continuo_contracts-0.1.0.dist-info"


def _wheel(tmp_path, name="continuo_contracts-0.1.0-py3-none-any.whl", skip=()):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        for member in check_wheel.REQUIRED_MEMBERS:
            if member not in skip:
                zf.writestr(member, "x")
        zf.writestr(f"{INFO}/METADATA", METADATA)
        zf.writestr(f"{INFO}/licenses/LICENSE", "x")
        zf.writestr(f"{INFO}/licenses/NOTICE", "x")
    return path


def test_a_complete_wheel_has_no_problems(tmp_path):
    assert check_wheel.wheel_problems(_wheel(tmp_path)) == []


def test_a_wheel_without_its_data_files_is_refused(tmp_path):
    data = (
        "continuo_contracts/fingerprints.yaml",
        "continuo_contracts/vectors/canonical_json.json",
        "continuo_contracts/vectors/hashing.json",
        "continuo_contracts/vectors/projection.json",
        "continuo_contracts/vectors/sql_types.json",
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
