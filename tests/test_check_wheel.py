import importlib.util
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_wheel.py"
spec = importlib.util.spec_from_file_location("check_wheel", SCRIPT)
check_wheel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_wheel)

METADATA = "Metadata-Version: 2.4\nName: continuo-contracts\nVersion: 0.1.0\nRequires-Python: >=3.12\nLicense-Expression: Apache-2.0\nRequires-Dist: PyYAML>=6.0\n"
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
    data = ("continuo_contracts/fingerprints.yaml", "continuo_contracts/vectors/canonical_json.json",
            "continuo_contracts/vectors/hashing.json", "continuo_contracts/vectors/projection.json",
            "continuo_contracts/vectors/sql_types.json")
    problems = check_wheel.wheel_problems(_wheel(tmp_path, skip=data))
    assert all(any(member in p for p in problems) for member in data)


def test_a_platform_wheel_is_refused(tmp_path):
    problems = check_wheel.wheel_problems(_wheel(tmp_path, name="continuo_contracts-0.1.0-cp312-cp312-linux_x86_64.whl"))
    assert any("py3-none-any" in p for p in problems)
