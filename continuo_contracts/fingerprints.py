"""The fingerprint field declaration: reader, classifier and renderer of `fingerprints.yaml`.

`fingerprints.yaml` says which fingerprint each input of a node moves, so the producers, the
topology service and the guards that check them agree on it. It holds `version: 1` and:

- `lists.hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config.
- `lists.projection_keys`: the keys of the Python code projection, in projection order.
- `families`: one list of entries for each of the eight sections, `every` (what every family
  shares) and the seven node families in `FAMILIES`.

An entry classifies the `inputs` it names:

- `id`: a name that is unique in its section.
- `field`, `source`, `why`: the "Field or input", "Source" and "Part, or why neither" cells of
  the rendered table.
- `fingerprint`: `content` (moves `content_hash`), `contract` (moves `contract_hash`), `both`
  or `neither`.
- `parts`: the parts of `content_hash` the input moves, among `source`, `shared`, `config`,
  `compiled` and `identity`. Required, and only allowed, when `fingerprint` is `content` or
  `both`.
- `when`: on a `contract` or `neither` entry, what the input moves in addition while a condition
  holds (`enforced`, `upsert` or `sql_reads`): a `contract` entry widens to `both`, a `neither`
  entry to `content`.
- `inputs`: the input names the entry classifies, unique across the section.
- `rule`: `outside_denylist` marks the one entry of a dbt family that classifies every `config`
  key outside `lists.hashed_config_denylist`. It has `inputs: ["config.*"]`, `fingerprint:
  content` and `parts: [config]`.

`parse_fingerprints` refuses a file that is not in this form with a `FingerprintsFileError` that
names the location. `render_markdown` writes the file as the tables of `docs/fingerprints.md`.
"""

import functools
import importlib.resources
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import yaml

from continuo_contracts.errors import FingerprintsFileError

FAMILIES = (
    "dbt-model",
    "dbt-seed",
    "dbt-snapshot",
    "dbt-test",
    "python-node",
    "python-csv",
    "python-api",
)
DBT_FAMILIES = FAMILIES[:4]
PYTHON_FAMILIES = FAMILIES[4:]
EVERY = "every"
SECTIONS = (EVERY, *FAMILIES)

_VERSION = 1
_FINGERPRINTS = ("content", "contract", "both", "neither")
_PARTS = ("source", "shared", "config", "compiled", "identity")
_CONDITIONS = ("enforced", "upsert", "sql_reads")
_WIDENS_TO = {"contract": "both", "neither": "content"}
_RULE = "outside_denylist"
_CONFIG_RULE_INPUT = "config.*"

_ID = re.compile(r"[a-z][a-z0-9_]*")
_INPUT = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_KEY_NAME = re.compile(r"[^\s.]+")


@dataclass(frozen=True)
class When:
    """What an input moves in addition to its entry's fingerprint while a condition holds."""

    condition: str
    fingerprint: str
    parts: tuple[str, ...]


@dataclass(frozen=True)
class Entry:
    """One row of a section: the fingerprint that the entry's `inputs` move."""

    family: str
    id: str
    field: str
    source: str
    fingerprint: str
    parts: tuple[str, ...]
    why: str
    inputs: tuple[str, ...]
    when: When | None
    rule: str | None

    def moves(self, part: str) -> bool:
        """Return whether the entry moves `part` of `content_hash`, also under its `when`."""
        return part in self.parts or (self.when is not None and part in self.when.parts)


@dataclass(frozen=True)
class Fingerprints:
    """A checked fingerprints file."""

    version: int
    hashed_config_denylist: frozenset[str]
    projection_keys: tuple[str, ...]
    sections: Mapping[str, tuple[Entry, ...]]

    def entries(self, section: str) -> tuple[Entry, ...]:
        """Return the entries of `section`, one of `SECTIONS`, in file order."""
        if section not in SECTIONS:
            raise ValueError(f"unknown section {section!r}")
        return self.sections[section]

    def classify(self, family: str, name: str) -> Entry | None:
        """Return the entry that classifies the input `name` of a node of `family`, or `None`.

        The entries of the family come before those of `every`. In a dbt family a `config.<key>`
        that no entry names, and whose key is not in the denylist, belongs to the family's rule
        entry. Raises `ValueError` when `family` is not one of `FAMILIES`.
        """
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}")
        for section in (family, EVERY):
            for entry in self.sections[section]:
                if name in entry.inputs:
                    return entry
        if family in DBT_FAMILIES and name.startswith("config."):
            key = name.split(".")[1]
            if key and key not in self.hashed_config_denylist:
                return next((e for e in self.sections[family] if e.rule == _RULE), None)
        return None

    def unclassified(self, family: str, names: Iterable[str]) -> list[str]:
        """Return the distinct `names` that no entry classifies, sorted.

        Raises `ValueError` when `family` is not one of `FAMILIES`.
        """
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}")
        return sorted({name for name in names if self.classify(family, name) is None})


def parse_fingerprints(text: str) -> Fingerprints:
    """Read and check the YAML `text` of a fingerprints file.

    Raises `FingerprintsFileError` for a file that is not in the form this module describes.
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise _fail("fingerprints file", f"is not valid YAML: {error}") from error
    root = _mapping(document, "fingerprints file", ("version", "lists", "families"))
    version = root["version"]
    if type(version) is not int or version != _VERSION:
        raise _fail("version", f"must be the integer {_VERSION}, got {version!r}")
    lists = _mapping(root["lists"], "lists", ("hashed_config_denylist", "projection_keys"))
    denylist = _key_names(lists["hashed_config_denylist"], "lists.hashed_config_denylist")
    projection = _key_names(lists["projection_keys"], "lists.projection_keys")
    families = _mapping(root["families"], "families", SECTIONS)
    sections = {section: _section(section, families[section]) for section in SECTIONS}
    return Fingerprints(
        version=_VERSION,
        hashed_config_denylist=frozenset(denylist),
        projection_keys=projection,
        sections=MappingProxyType(sections),
    )


@functools.cache
def load() -> Fingerprints:
    """Return the packaged `fingerprints.yaml`, read and checked once."""
    resource = importlib.resources.files("continuo_contracts") / "fingerprints.yaml"
    return parse_fingerprints(resource.read_text(encoding="utf-8"))


def hashed_config_denylist() -> frozenset[str]:
    """Return the dbt `config` keys that stay out of the hashed config."""
    return load().hashed_config_denylist


def projection_keys() -> tuple[str, ...]:
    """Return the keys of the Python code projection, in projection order."""
    return load().projection_keys


def classify(family: str, name: str) -> Entry | None:
    """Return the packaged entry that classifies the input `name` of `family`, or `None`."""
    return load().classify(family, name)


def unclassified(family: str, names: Iterable[str]) -> list[str]:
    """Return the distinct `names` that no packaged entry classifies for `family`, sorted."""
    return load().unclassified(family, names)


_CONDITION_TEXT = {
    "enforced": "under an enforced dbt contract",
    "upsert": "on an `upsert` node",
    "sql_reads": "while SQL reads it",
}
_HEADINGS = {
    EVERY: "Every family",
    "dbt-model": "dbt model",
    "dbt-seed": "dbt seed",
    "dbt-snapshot": "dbt snapshot",
    "dbt-test": "dbt test",
    "python-node": "python-node",
    "python-csv": "python-csv",
    "python-api": "python-api",
}
_TABLE_HEADER = (
    "| Field or input | Source | In | Part, or why neither | Inputs |",
    "|---|---|---|---|---|",
)
_IN_MEANING = (
    "The *In* column names the fingerprint an input moves. `content` moves `content_hash`, the "
    "fingerprint of a node's code, and the parts in brackets are the pieces of `content_hash` "
    "that the input is folded into: `source` is the node's own code or data, `shared` is code "
    "the node uses that other nodes share, `config` is the hashed configuration, `compiled` is "
    "the compiled SQL, and `identity` means the value names the node, so a change makes a new "
    "node with fresh hashes. `contract` moves `contract_hash`. `both` moves both. `neither` "
    "moves neither, and the last column says why. A clause after a semicolon names what the "
    "input also moves while its condition holds. In a dbt test row, `test node:` names the part "
    "of the test node's own `content_hash`, and `tested node` says the field also moves the "
    "`contract_hash` of the node under test."
)
_NAME_MEANING = (
    "The names in the *Inputs* column are these:",
    "",
    "- dbt source part: `raw_code` (model, snapshot), `compiled_code` (model and snapshot: the "
    "compiled part; test: the source part), `seed_bytes` (the digest the compile extras carry "
    "for a seed), `seed_checksum` (dbt's checksum of a seed, used when a stored artifact has no "
    "compile extras).",
    "- dbt relation: `relation.schema`, `relation.alias`.",
    "- dbt config: `config.<key>` for a key of the hashed config, which `config.*` covers for "
    "every key outside the denylist, and `config.<key>` for each denylisted key.",
    "- dbt enforced contract: `contract.columns`, `constraints`.",
    "- dbt shared units: `macros`, `functions`, `unit_tests`, `on_run_start`, `on_run_end`, "
    "`project_flags`, `generate_schema_name`, `generate_alias_name`.",
    "- Python: `projection.<key>` for each key of the code projection, `ci.source_hash`, "
    "`ci.shared_code_hash`.",
)


def render_in(entry: Entry) -> str:
    """Return the "In" cell of `entry`: the fingerprint it moves, its parts and its `when`."""
    cell = _written(entry.family, entry.fingerprint, entry.parts)
    if entry.when is not None:
        when = _written(entry.family, entry.when.fingerprint, entry.when.parts)
        cell = f"{cell}; {when} {_CONDITION_TEXT[entry.when.condition]}"
    return cell


def render_markdown(fp: Fingerprints) -> str:
    """Return the Markdown of `docs/fingerprints.md` for `fp`, ending with one newline."""
    denylist = ", ".join(f"`{key}`" for key in sorted(fp.hashed_config_denylist))
    projection = ", ".join(f"`{key}`" for key in fp.projection_keys)
    lines = [
        "# Fingerprint field reference",
        "",
        "This file is generated from `continuo_contracts/fingerprints.yaml` by "
        "`scripts/render_fingerprints.py` and is not edited by hand.",
        "",
        _IN_MEANING,
        "",
        *_NAME_MEANING,
        "",
        "## Lists the hashing code applies",
        "",
        f"- `hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config: "
        f"{denylist}.",
        f"- `projection_keys`: the keys of the Python code projection, in projection order: "
        f"{projection}.",
        "",
    ]
    for section in SECTIONS:
        lines += [f"## {_HEADINGS[section]}", "", *_TABLE_HEADER]
        lines += [_row(entry) for entry in fp.entries(section)]
        lines.append("")
    return "\n".join(lines)


def _written(family: str, fingerprint: str, parts: tuple[str, ...]) -> str:
    listed = ", ".join(parts)
    if family == "dbt-test":
        if fingerprint == "content":
            return f"content (test node: {listed})"
        if fingerprint == "both":
            return f"content (test node: {listed}), contract (tested node)"
        if fingerprint == "contract":
            return "contract (tested node)"
        return fingerprint
    if fingerprint in ("content", "both"):
        return f"{fingerprint} ({listed})"
    return fingerprint


def _row(entry: Entry) -> str:
    inputs = ", ".join(f"`{name}`" for name in entry.inputs)
    cells = (entry.field, entry.source, render_in(entry), entry.why, inputs)
    return "|" + "".join(f" {_cell(cell)} |" if cell else " |" for cell in cells)


def _cell(text: str) -> str:
    """Write `text` as one table cell: on one line, with `|` escaped."""
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip().replace("|", "\\|")


def _fail(where: str, message: str) -> FingerprintsFileError:
    return FingerprintsFileError(f"{where}: {message}")


def _mapping(
    value: object,
    where: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return `value` when it is a mapping with every `required` key and no key but those named."""
    if not isinstance(value, dict):
        raise _fail(where, "expected a mapping")
    for key in value:
        if key not in required and key not in optional:
            raise _fail(where, f"unknown key {key!r}")
    for key in required:
        if key not in value:
            raise _fail(where, f"missing key {key!r}")
    return value


def _key_names(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _fail(where, "expected a non-empty list")
    seen: set[str] = set()
    for index, name in enumerate(value):
        if not isinstance(name, str) or not _KEY_NAME.fullmatch(name):
            raise _fail(f"{where}[{index}]", f"{name!r} is not a key name (no dot, no whitespace)")
        if name in seen:
            raise _fail(f"{where}[{index}]", f"duplicate name {name!r}")
        seen.add(name)
    return tuple(value)


def _section(section: str, raw: object) -> tuple[Entry, ...]:
    where = f"families.{section}"
    if not isinstance(raw, list):
        raise _fail(where, "expected a list of entries")
    entries: list[Entry] = []
    entry_by_id: dict[str, Entry] = {}
    entry_by_input: dict[str, Entry] = {}
    rule_entry: Entry | None = None
    for index, item in enumerate(raw):
        entry = _entry(section, f"{where}[{index}]", item)
        located = f"{where}[{index}] ({entry.id})"
        if entry.id in entry_by_id:
            raise _fail(located, f"duplicate id {entry.id!r} in {where}")
        if entry.rule is not None:
            if rule_entry is not None:
                raise _fail(located, f"rule: {where} already has the rule entry {rule_entry.id!r}")
            rule_entry = entry
        for name in entry.inputs:
            if name in entry_by_input:
                other = entry_by_input[name].id
                raise _fail(located, f"duplicate input {name!r}, also named by {other!r}")
        entry_by_id[entry.id] = entry
        entry_by_input.update({name: entry for name in entry.inputs})
        entries.append(entry)
    return tuple(entries)


def _entry(section: str, where: str, item: object) -> Entry:
    data = _mapping(
        item,
        where,
        ("id", "field", "source", "fingerprint", "why", "inputs"),
        ("parts", "when", "rule"),
    )
    entry_id = data["id"]
    if not isinstance(entry_id, str) or not _ID.fullmatch(entry_id):
        raise _fail(where, f"id: {entry_id!r} does not match {_ID.pattern}")
    where = f"{where} ({entry_id})"
    for key in ("field", "source", "why"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise _fail(where, f"{key}: expected a non-empty string")
    fingerprint = data["fingerprint"]
    if fingerprint not in _FINGERPRINTS:
        raise _fail(where, f"fingerprint: {fingerprint!r} is not one of {', '.join(_FINGERPRINTS)}")
    parts = _entry_parts(data, fingerprint, where)
    when = _when(data["when"], fingerprint, where) if "when" in data else None
    rule = data.get("rule")
    if "rule" in data:
        _check_rule(section, data, fingerprint, parts, where)
    inputs = _inputs(data["inputs"], rule is not None, where)
    return Entry(
        family=section,
        id=entry_id,
        field=data["field"],
        source=data["source"],
        fingerprint=fingerprint,
        parts=parts,
        why=data["why"],
        inputs=inputs,
        when=when,
        rule=rule,
    )


def _entry_parts(data: dict[str, Any], fingerprint: str, where: str) -> tuple[str, ...]:
    if fingerprint in ("content", "both"):
        if "parts" not in data:
            raise _fail(where, f"parts: required when the fingerprint is {fingerprint!r}")
        return _parts(data["parts"], where, "parts")
    if "parts" in data:
        raise _fail(where, f"parts: not allowed when the fingerprint is {fingerprint!r}")
    return ()


def _parts(value: object, where: str, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _fail(where, f"{label}: expected a non-empty list of parts")
    seen: set[str] = set()
    for index, part in enumerate(value):
        if part not in _PARTS:
            raise _fail(where, f"{label}[{index}]: {part!r} is not a part ({', '.join(_PARTS)})")
        if part in seen:
            raise _fail(where, f"{label}[{index}]: duplicate part {part!r}")
        seen.add(part)
    return tuple(value)


def _when(value: object, fingerprint: str, where: str) -> When:
    if fingerprint not in _WIDENS_TO:
        raise _fail(where, f"when: only on a contract or neither entry, not on {fingerprint!r}")
    data = _mapping(value, f"{where}: when", ("condition", "fingerprint", "parts"))
    condition = data["condition"]
    if condition not in _CONDITIONS:
        raise _fail(where, f"when: condition {condition!r} is not one of {', '.join(_CONDITIONS)}")
    widened = _WIDENS_TO[fingerprint]
    if data["fingerprint"] != widened:
        raise _fail(
            where,
            f"when: fingerprint must be {widened!r} on a {fingerprint!r} entry, "
            f"got {data['fingerprint']!r}",
        )
    return When(condition, widened, _parts(data["parts"], where, "when.parts"))


def _check_rule(
    section: str,
    data: dict[str, Any],
    fingerprint: str,
    parts: tuple[str, ...],
    where: str,
) -> None:
    if data["rule"] != _RULE:
        raise _fail(where, f"rule: {data['rule']!r} is not {_RULE!r}")
    if section not in DBT_FAMILIES:
        raise _fail(where, f"rule: {_RULE} is only for a dbt family, not for {section!r}")
    if fingerprint != "content" or parts != ("config",):
        raise _fail(where, f"rule: a {_RULE} entry has fingerprint 'content' and parts ['config']")
    if data["inputs"] != [_CONFIG_RULE_INPUT]:
        raise _fail(where, f"rule: a {_RULE} entry has inputs ['{_CONFIG_RULE_INPUT}']")


def _inputs(value: object, is_rule: bool, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(where, "inputs: expected a list")
    seen: set[str] = set()
    for index, name in enumerate(value):
        if name == _CONFIG_RULE_INPUT:
            if not is_rule:
                raise _fail(
                    where,
                    f"inputs[{index}]: {_CONFIG_RULE_INPUT} is only for an entry with rule {_RULE}",
                )
        elif not isinstance(name, str) or not _INPUT.fullmatch(name):
            raise _fail(
                where,
                f"inputs[{index}]: {name!r} is not an input name, which is lowercase words "
                "joined by dots",
            )
        if name in seen:
            raise _fail(where, f"inputs[{index}]: duplicate input {name!r}")
        seen.add(name)
    return tuple(value)
