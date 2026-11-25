"""The fingerprint field declaration: reader, classifier and renderer of `fingerprints.yaml`.

`fingerprints.yaml` says which fingerprint each input of a node moves, so the producers, the
topology-controller and the guards that check them agree on it. It holds `version: 1` and:

- `lists.hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config.
- `lists.projection_keys`: the keys of the Python code projection, in projection order.
- `lists.contract_hash_exclusions`: the root keys of the stored form that `contract_hash` leaves
  out.
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
- `rule`: marks the one entry of a family that classifies every input no entry names:
  `outside_denylist` in a dbt family, for every `config` key outside
  `lists.hashed_config_denylist` (`inputs: ["config.*"]`, `fingerprint: content`, `parts:
  [config]`), and `standard_field` in a Python family, for every standard ODCS field of the stored
  form (`inputs: ["odcs.*"]`, `fingerprint: contract`, no `when`).

An input name is `config.<key>` for a dbt config key, `projection.<key>` for a key of the Python
code projection, one of `FIXED_INPUT_NAMES`, or `odcs.<path>` for a field of a contract's stored
form: `odcs` and then the ODCS names of the path as written (camelCase allowed), with the array
indexes dropped, such as `odcs.team.name` and `odcs.schema.properties.logicalType`. A `continuo`
custom property is `odcs.custom.<property>` on the contract and `odcs.schema.custom.<property>` on
the schema object, and `classify` leaves such a path to its entries: the `standard_field` rule never
claims one. A dbt family has no such rule, so its entries name every `odcs.<path>` that
`contract_hash` hashes (it leaves out the keys of `contract_hash_exclusions`).

`parse_fingerprints` refuses a file that is not in this form with a `FingerprintsFileError` that
names the location. A mapping that writes a key twice is refused too, naming the key and its line,
so a second `fingerprint:` in an entry cannot silently replace the first. A key that a `<<` merge
brings in twice, or that a merge brings in and the mapping writes too, is named with the line of
the merge. `render_markdown` writes the file as the tables of `docs/fingerprints.md`.
"""

import functools
import importlib.resources
import re

from collections.abc import Hashable, Iterable, Mapping
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
# The input names an entry may list that are written out in full. The rest of the closed
# vocabulary is the pattern `config.<key>` (and `config.*`) for a dbt config key,
# `projection.<key>` for a key of the Python code projection and `odcs.<path>` (and `odcs.*`) for a
# field of a contract's stored form.
FIXED_INPUT_NAMES = (
    "raw_code",
    "compiled_code",
    "seed_bytes",
    "seed_checksum",
    "relation.schema",
    "relation.alias",
    "contract.columns",
    "constraints",
    "macros",
    "functions",
    "unit_tests",
    "on_run_start",
    "on_run_end",
    "project_flags",
    "generate_schema_name",
    "generate_alias_name",
    "ci.source_hash",
    "ci.shared_code_hash",
)

_VERSION = 1
_FINGERPRINTS = ("content", "contract", "both", "neither")
_PARTS = ("source", "shared", "config", "compiled", "identity")
_CONDITIONS = ("enforced", "upsert", "sql_reads")
_WIDENS_TO = {"contract": "both", "neither": "content"}
_OUTSIDE_DENYLIST = "outside_denylist"
_STANDARD_FIELD = "standard_field"
# The segment that marks a `continuo` custom property in an `odcs.<path>` name.
_CUSTOM_SEGMENT = "custom"

_ID = re.compile(r"[a-z][a-z0-9_]*")
_INPUT = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_ODCS_INPUT = re.compile(r"odcs(\.([A-Za-z][A-Za-z0-9]*|\*))+")
_KEY_NAME = re.compile(r"[^\s.]+")


@dataclass(frozen=True)
class _Rule:
    """What an entry with a `rule` looks like.

    It says where the entry may sit, the fingerprint and parts it has and the one
    input it names, which stands for every input that no entry names.
    """

    families: tuple[str, ...]
    where: str
    fingerprint: str
    parts: tuple[str, ...]
    name: str


_RULES = {
    _OUTSIDE_DENYLIST: _Rule(
        DBT_FAMILIES, "a dbt family", "content", ("config",), "config.*"
    ),
    _STANDARD_FIELD: _Rule(
        PYTHON_FAMILIES, "a Python family", "contract", (), "odcs.*"
    ),
}


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
        return part in self.parts or (
            self.when is not None and part in self.when.parts
        )


@dataclass(frozen=True)
class Fingerprints:
    """A checked fingerprints file."""

    version: int
    hashed_config_denylist: frozenset[str]
    projection_keys: tuple[str, ...]
    contract_hash_exclusions: frozenset[str]
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
        entry. In a Python family an `odcs.<path>` that no entry names belongs to the family's
        rule entry unless it is a `continuo` custom property (a path with a `custom` segment),
        which only an entry can classify. Raises `ValueError` when `family` is not one of
        `FAMILIES`.
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
                return self._rule_entry(family, _OUTSIDE_DENYLIST)
        if (
            family in PYTHON_FAMILIES
            and name.startswith("odcs.")
            and _CUSTOM_SEGMENT not in name.split(".")
        ):
            return self._rule_entry(family, _STANDARD_FIELD)
        return None

    def _rule_entry(self, family: str, rule: str) -> Entry | None:
        return next((e for e in self.sections[family] if e.rule == rule), None)

    def unclassified(self, family: str, names: Iterable[str]) -> list[str]:
        """Return the distinct `names` that no entry classifies, sorted.

        Raises `ValueError` when `family` is not one of `FAMILIES`.
        """
        if family not in FAMILIES:
            raise ValueError(f"unknown family {family!r}")
        return sorted(
            {name for name in names if self.classify(family, name) is None}
        )


# The tag PyYAML gives the `<<` key of a merge.
_MERGE_TAG = "tag:yaml.org,2002:merge"


class _StrictLoader(yaml.SafeLoader):
    """A `SafeLoader` that refuses a mapping in which a key occurs twice.

    PyYAML keeps the last of two equal keys, so a repeated `fingerprint:` would change the
    classification of an entry with no error. A key that a `<<` merge brings in counts as written
    in the mapping it lands in. The key node that a merge brings in sits in the mapping that is
    merged, not at the merge, so a duplicate that involves one is reported at the `<<` key that
    brings it in.
    """

    def construct_mapping(
        self, node: yaml.MappingNode, deep: bool = False
    ) -> dict[Hashable, Any]:
        # A scalar tagged `!!map` also arrives here, and the base class refuses it.
        if isinstance(node, yaml.MappingNode):
            merges = [
                (key, merged)
                for key, merged in node.value
                if key.tag == _MERGE_TAG
            ]
            self.flatten_mapping(node)
            merged_by: dict[int, list[yaml.Node]] = {}
            for merge_key, merged in merges:
                for key_node in _merged_key_nodes(merged):
                    merged_by.setdefault(id(key_node), []).append(merge_key)
            first: dict[Hashable, yaml.Node] = {}
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=True)
                if not isinstance(key, Hashable):
                    continue
                if key in first:
                    raise _duplicate_key(
                        key,
                        first[key],
                        key_node,
                        [
                            *merged_by.get(id(first[key]), ()),
                            *merged_by.get(id(key_node), ()),
                        ],
                    )
                first[key] = key_node
        return super().construct_mapping(node, deep=deep)


def _merged_key_nodes(merged: yaml.Node) -> list[yaml.Node]:
    """Return the key nodes of the mapping, or of each mapping of the sequence, a `<<` merges.

    `flatten_mapping` has already flattened every one of them, so the list holds the keys they
    bring in as well as their own.
    """
    mappings = merged.value if isinstance(merged, yaml.SequenceNode) else [merged]
    return [key for mapping in mappings for key, _ in mapping.value]


def _duplicate_key(
    key: Hashable,
    first: yaml.Node,
    repeat: yaml.Node,
    merge_keys: list[yaml.Node],
) -> yaml.constructor.ConstructorError:
    """Build the error for `key` written at `first` and again at `repeat`.

    `merge_keys` are the `<<` keys that brought in either occurrence, empty when the mapping
    writes both. A key that a merge brings in names the line of the merge, not the line where the
    merged mapping wrote the key.
    """
    if not merge_keys:
        return yaml.constructor.ConstructorError(
            None,
            None,
            f"duplicate key {key!r}, first written at line {first.start_mark.line + 1}",
            repeat.start_mark,
        )
    by_line = {
        merge_key.start_mark.line + 1: merge_key for merge_key in merge_keys
    }
    lines = sorted(by_line)
    if len(lines) == 1:
        where = f"the merge at line {lines[0]}"
    else:
        where = (
            "the merges at lines "
            + ", ".join(map(str, lines[:-1]))
            + f" and {lines[-1]}"
        )
    return yaml.constructor.ConstructorError(
        None,
        None,
        f"duplicate key {key!r}, brought in through {where}",
        by_line[lines[0]].start_mark,
    )


def parse_fingerprints(text: str) -> Fingerprints:
    """Read and check the YAML `text` of a fingerprints file.

    Raises `FingerprintsFileError` for a file that is not in the form this module describes,
    including a mapping that writes a key twice.
    """
    try:
        document = yaml.load(text, Loader=_StrictLoader)
    except yaml.YAMLError as error:
        raise _fail("fingerprints file", f"is not valid YAML: {error}") from error
    root = _mapping(
        document, "fingerprints file", ("version", "lists", "families")
    )
    version = root["version"]
    if type(version) is not int or version != _VERSION:
        raise _fail("version", f"must be the integer {_VERSION}, got {version!r}")
    lists = _mapping(
        root["lists"],
        "lists",
        ("hashed_config_denylist", "projection_keys", "contract_hash_exclusions"),
    )
    denylist = _key_names(
        lists["hashed_config_denylist"], "lists.hashed_config_denylist"
    )
    projection = _key_names(lists["projection_keys"], "lists.projection_keys")
    exclusions = _key_names(
        lists["contract_hash_exclusions"], "lists.contract_hash_exclusions"
    )
    families = _mapping(root["families"], "families", SECTIONS)
    sections = {
        section: _section(section, families[section]) for section in SECTIONS
    }
    return Fingerprints(
        version=_VERSION,
        hashed_config_denylist=frozenset(denylist),
        projection_keys=projection,
        contract_hash_exclusions=frozenset(exclusions),
        sections=MappingProxyType(sections),
    )


@functools.cache
def load() -> Fingerprints:
    """Return the packaged `fingerprints.yaml`, read and checked once."""
    resource = (
        importlib.resources.files("continuo_contracts") / "fingerprints.yaml"
    )
    return parse_fingerprints(resource.read_text(encoding="utf-8"))


def hashed_config_denylist() -> frozenset[str]:
    """Return the dbt `config` keys that stay out of the hashed config."""
    return load().hashed_config_denylist


def projection_keys() -> tuple[str, ...]:
    """Return the keys of the Python code projection, in projection order."""
    return load().projection_keys


def contract_hash_exclusions() -> frozenset[str]:
    """Return the root keys of the stored form that `contract_hash` leaves out."""
    return load().contract_hash_exclusions


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
    "node with fresh hashes. `contract` moves `contract_hash`, the fingerprint of a node's "
    "contract. `both` moves both. `neither` moves neither. The *Part, or why neither* column "
    "names the part for a `content` or `both` row, names the contract fields the input fills "
    "for a `contract` row, and gives the reason the input moves nothing for a `neither` row. "
    "A clause after a semicolon names what the input also moves "
    "while its condition holds. In a dbt test row, `test node:` names the part of the test "
    "node's own `content_hash`, and `tested node` says the field also moves the "
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
    "every key outside the denylist, and `config.<key>` for each denylisted key. "
    "`config.contract.enforced` is the `enforced` key of the dbt contract settings.",
    "- dbt enforced contract: `contract.columns`, `constraints`.",
    "- dbt shared units: `macros`, `functions`, `unit_tests`, `on_run_start`, `on_run_end`, "
    "`project_flags`, `generate_schema_name`, `generate_alias_name`.",
    "- Python: `projection.<key>` for each key of the code projection, `ci.source_hash` (the "
    "digest of the node's script bytes, computed by the producer's CI, and for a CSV node the "
    "digest of its URI), `ci.shared_code_hash` (the shared part for the in-repo files the "
    "script imports or lists as assets, folded path by path by `shared_hash`).",
    "- Contract: `odcs.<path>` for a field of the contract's stored form, written with "
    "its ODCS names as they are in the file and without array indexes, such as `odcs.team.name` "
    "or `odcs.schema.properties.logicalType`. A continuo custom property is "
    "`odcs.custom.<property>` on the contract (`odcs.custom.timeoutMinutes`) and "
    "`odcs.schema.custom.<property>` on the schema object (`odcs.schema.custom.reads`). "
    "`odcs.*` covers every standard ODCS field that no entry names, which a Python family "
    "classifies as contract, and no continuo custom property. A dbt family has no such "
    "rule: its entries name every `odcs.<path>` that `contract_hash` hashes (it leaves out the "
    "keys of `contract_hash_exclusions`).",
)
_TERMS_MEANING = (
    "## Terms",
    "",
    "- *re-baseline*: a continuo release of each project, run once after a change of the "
    "hashing code, that recomputes every node's stored hash with the new code and validates "
    "and reloads nothing.",
    "- *compile extras*: the files continuo's compile step writes beside dbt's manifest: seed "
    "bytes, seed headers and column types, and similar inputs a stored manifest lacks.",
    "- *code bundle*: the per-release file in which continuo stores each node's code.",
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
    exclusions = ", ".join(
        f"`{key}`" for key in sorted(fp.contract_hash_exclusions)
    )
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
        *_TERMS_MEANING,
        "",
        "## Lists the hashing code applies",
        "",
        f"- `hashed_config_denylist`: the dbt `config` keys that stay out of the hashed config: "
        f"{denylist}.",
        f"- `projection_keys`: the keys of the Python code projection, in projection order: "
        f"{projection}.",
        f"- `contract_hash_exclusions`: the root keys of the stored form that `contract_hash` "
        f"leaves out: {exclusions}.",
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
            raise _fail(
                f"{where}[{index}]",
                f"{name!r} is not a key name (no dot, no whitespace)",
            )
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
                raise _fail(
                    located,
                    f"rule: {where} already has the rule entry {rule_entry.id!r}",
                )
            rule_entry = entry
        for name in entry.inputs:
            if name in entry_by_input:
                other = entry_by_input[name].id
                raise _fail(
                    located, f"duplicate input {name!r}, also named by {other!r}"
                )
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
        raise _fail(
            where,
            f"fingerprint: {fingerprint!r} is not one of {', '.join(_FINGERPRINTS)}",
        )
    parts = _entry_parts(data, fingerprint, where)
    when = _when(data["when"], fingerprint, where) if "when" in data else None
    rule = data.get("rule")
    if "rule" in data:
        _check_rule(section, data, fingerprint, parts, where)
    inputs = _inputs(data["inputs"], rule, where)
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


def _entry_parts(
    data: dict[str, Any], fingerprint: str, where: str
) -> tuple[str, ...]:
    if fingerprint in ("content", "both"):
        if "parts" not in data:
            raise _fail(
                where, f"parts: required when the fingerprint is {fingerprint!r}"
            )
        return _parts(data["parts"], where, "parts")
    if "parts" in data:
        raise _fail(
            where, f"parts: not allowed when the fingerprint is {fingerprint!r}"
        )
    return ()


def _parts(value: object, where: str, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise _fail(where, f"{label}: expected a non-empty list of parts")
    seen: set[str] = set()
    for index, part in enumerate(value):
        if part not in _PARTS:
            raise _fail(
                where,
                f"{label}[{index}]: {part!r} is not a part ({', '.join(_PARTS)})",
            )
        if part in seen:
            raise _fail(where, f"{label}[{index}]: duplicate part {part!r}")
        seen.add(part)
    return tuple(value)


def _when(value: object, fingerprint: str, where: str) -> When:
    if fingerprint not in _WIDENS_TO:
        raise _fail(
            where,
            f"when: only on a contract or neither entry, not on {fingerprint!r}",
        )
    data = _mapping(
        value, f"{where}: when", ("condition", "fingerprint", "parts")
    )
    condition = data["condition"]
    if condition not in _CONDITIONS:
        raise _fail(
            where,
            f"when: condition {condition!r} is not one of {', '.join(_CONDITIONS)}",
        )
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
    rule = data["rule"]
    spec = _RULES.get(rule) if isinstance(rule, str) else None
    if spec is None:
        raise _fail(where, f"rule: {rule!r} is not one of {', '.join(_RULES)}")
    if section not in spec.families:
        raise _fail(
            where, f"rule: {rule} is only for {spec.where}, not for {section!r}"
        )
    if fingerprint != spec.fingerprint or parts != spec.parts:
        shape = f"fingerprint {spec.fingerprint!r}"
        if spec.parts:
            shape += f" and parts {list(spec.parts)}"
        raise _fail(where, f"rule: a {rule} entry has {shape}")
    if "when" in data:
        raise _fail(where, f"rule: a {rule} entry has no `when`")
    if data["inputs"] != [spec.name]:
        raise _fail(where, f"rule: a {rule} entry has inputs ['{spec.name}']")


def _inputs(value: object, rule: str | None, where: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail(where, "inputs: expected a list")
    seen: set[str] = set()
    for index, name in enumerate(value):
        stands_for_all = next(
            (r for r, spec in _RULES.items() if spec.name == name), None
        )
        if stands_for_all is not None:
            if rule != stands_for_all:
                raise _fail(
                    where,
                    f"inputs[{index}]: {name} is only for an entry with rule {stands_for_all}",
                )
        elif not _is_input_name(name):
            raise _fail(
                where,
                f"inputs[{index}]: {name!r} is not an input name, which is lowercase words "
                "joined by dots, or odcs and then ODCS names joined by dots",
            )
        if name in seen:
            raise _fail(where, f"inputs[{index}]: duplicate input {name!r}")
        seen.add(name)
    return tuple(value)


def _is_input_name(name: object) -> bool:
    """Say whether `name` is an input name other than a rule's.

    A name that starts with `odcs` is `odcs.<path>`, with no `*`, and every other
    name is lowercase words joined by dots.
    """
    if not isinstance(name, str):
        return False
    if name == "odcs" or name.startswith("odcs."):
        return _ODCS_INPUT.fullmatch(name) is not None and "*" not in name
    return _INPUT.fullmatch(name) is not None
