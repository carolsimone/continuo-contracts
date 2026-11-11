import importlib.resources
import re

import pytest
import yaml

from continuo_contracts.errors import FingerprintsFileError
from continuo_contracts.fingerprints import (
    Entry,
    When,
    _StrictLoader,
    hashed_config_denylist,
    load,
    parse_fingerprints,
    projection_keys,
    render_in,
    render_markdown,
)

# A backslash at the end of a line joins it to the next, so a long flow mapping
# stays one line of YAML.
MINIMAL = """
version: 1
lists:
  hashed_config_denylist: ["meta", "tags"]
  projection_keys: ["schema", "table"]
families:
  every:
    - {id: "provenance", field: "Image tag", source: "the release", fingerprint: "neither", \
why: "provenance", inputs: ["release.image_tag"]}
  dbt-model:
    - {id: "raw_code", field: "SQL | text", source: "`raw_code`", fingerprint: "content", \
parts: ["source"], why: "source part", inputs: ["raw_code"]}
    - {id: "other_config", field: "Every other config key", source: "`config`", \
fingerprint: "content", parts: ["config"], why: "hashed_config", inputs: ["config.*"], \
rule: "outside_denylist"}
    - {id: "tags", field: "`tags`", source: "node `tags`", fingerprint: "contract", why: "tags", \
inputs: ["config.tags"]}
    - {id: "meta_other", field: "Every other `meta` key", source: "`config.meta`", \
fingerprint: "neither", why: "no field holds it", inputs: ["config.meta"], \
when: {condition: "sql_reads", fingerprint: "content", parts: ["compiled"]}}
    - {id: "declared_columns", field: "Declared columns", source: "`schema.yml`", \
fingerprint: "contract", why: "properties", inputs: ["contract.columns"], \
when: {condition: "enforced", fingerprint: "both", parts: ["config"]}}
    - {id: "image_tag", field: "Image tag", source: "the release", fingerprint: "neither", \
why: "provenance", inputs: ["release.image_tag"]}
  dbt-seed: []
  dbt-snapshot: []
  dbt-test:
    - {id: "severity", field: "`severity`", source: "`config`", fingerprint: "both", \
parts: ["config"], why: "lower-cased", inputs: ["config.severity"]}
  python-node:
    - {id: "assets", field: "`assets`", source: "authored", fingerprint: "both", \
parts: ["config", "shared"], why: "projection and shared part", inputs: ["projection.assets"]}
  python-csv: []
  python-api: []
"""
FP = parse_fingerprints(MINIMAL)


@pytest.mark.parametrize(("family", "name", "expected"), [
    ("dbt-model", "raw_code", "raw_code"),
    ("dbt-model", "release.image_tag", "image_tag"),        # the family before `every`
    ("dbt-seed", "release.image_tag", "provenance"),        # `every` for the rest
    ("dbt-model", "config.persist_docs", "other_config"),   # the rule
    ("dbt-model", "config.metadata", "other_config"),       # only starts like a denylisted key
    ("dbt-model", "config.contract.enforced", "other_config"),
    ("dbt-model", "config.tags", "tags"),
    ("dbt-model", "config.meta", "meta_other"),
    ("dbt-model", "config.meta.owner", None),               # a denylisted key never takes the rule
    ("dbt-seed", "config.persist_docs", None),              # no rule entry in that family
    ("python-node", "config.persist_docs", None),           # the rule is for dbt only
    ("python-node", "projection.assets", "assets"),
    ("dbt-model", "nothing.here", None),
])
def test_classify(family, name, expected):
    entry = FP.classify(family, name)
    assert (entry.id if entry else None) == expected


def test_classify_refuses_a_name_that_is_not_a_family():
    for family in ("every", "python"):
        with pytest.raises(ValueError):
            FP.classify(family, "raw_code")


def test_unclassified_lists_the_unknown_names_sorted():
    assert FP.unclassified("dbt-model", ["raw_code", "zz", "aa"]) == ["aa", "zz"]


def test_moves_reads_the_parts_and_the_widening():
    assert FP.classify("dbt-model", "contract.columns").moves("config")
    assert not FP.classify("dbt-model", "config.tags").moves("config")


def _e(family, fingerprint, parts=(), when=None):
    return Entry(family=family, id="x", field="f", source="s", fingerprint=fingerprint, parts=parts,
                 why="w", inputs=(), when=when, rule=None)


@pytest.mark.parametrize(("entry", "cell"), [
    (_e("dbt-model", "content", ("source",)), "content (source)"),
    (_e("python-node", "both", ("config", "shared")), "both (config, shared)"),
    (_e("dbt-model", "contract"), "contract"),
    (_e("dbt-model", "neither"), "neither"),
    (_e("dbt-model", "contract", when=When("sql_reads", "both", ("compiled",))),
     "contract; both (compiled) while SQL reads it"),
    (_e("dbt-model", "neither", when=When("enforced", "content", ("config",))),
     "neither; content (config) under an enforced dbt contract"),
    (_e("python-node", "contract", when=When("upsert", "both", ("config",))),
     "contract; both (config) on an `upsert` node"),
    (_e("dbt-test", "both", ("config",)), "content (test node: config), contract (tested node)"),
    (_e("dbt-test", "content", ("identity",)), "content (test node: identity)"),
    (_e("dbt-test", "contract"), "contract (tested node)"),
    (_e("dbt-test", "neither", when=When("sql_reads", "content", ("source",))),
     "neither; content (test node: source) while SQL reads it"),
])
def test_render_in(entry, cell):
    assert render_in(entry) == cell


def test_render_markdown():
    md = render_markdown(FP)
    assert md.index("## Every family") < md.index("## dbt model") < md.index("## python-api")
    assert "| SQL \\| text | `raw_code` | content (source) | source part | `raw_code` |" in md
    assert md.endswith("|\n")
    assert "The *Part, or why neither* column names the part" in md
    assert "`contract_hash`, the fingerprint of a node's contract" in md
    assert "`ci.source_hash` (the digest of the node's script bytes" in md


def _with(mutate):
    doc = yaml.safe_load(MINIMAL)
    mutate(doc)
    return yaml.safe_dump(doc, sort_keys=False)


def _model(i):
    return lambda d: d["families"]["dbt-model"][i]


REFUSALS = [
    ("version", lambda d: d.update(version=2)),
    ("unknown", lambda d: d.update(extra=1)),
    ("families", lambda d: d["families"].pop("dbt-test")),
    ("duplicate id", lambda d: d["families"]["dbt-model"].append(
        {**_model(0)(d), "inputs": ["x"]})),
    ("duplicate input", lambda d: d["families"]["dbt-model"].append(
        {**_model(0)(d), "id": "raw_code_2"})),
    ("parts", lambda d: _model(0)(d).pop("parts")),
    ("parts", lambda d: _model(2)(d).update(parts=["config"])),
    ("part", lambda d: _model(0)(d).update(parts=["compile"])),
    ("fingerprint", lambda d: _model(0)(d).update(fingerprint="code")),
    ("condition", lambda d: _model(3)(d)["when"].update(condition="always")),
    ("when", lambda d: _model(3)(d)["when"].update(fingerprint="both")),
    ("rule", lambda d: d["families"]["python-node"][0].update(rule="outside_denylist")),
    ("input", lambda d: _model(0)(d).update(inputs=["Raw Code"])),
    ("config.*", lambda d: d["families"]["python-node"][0].update(inputs=["config.*"])),
    ("duplicate", lambda d: d["lists"]["hashed_config_denylist"].append("meta")),
]


@pytest.mark.parametrize(("needle", "mutate"), REFUSALS)
def test_a_malformed_file_is_refused(needle, mutate):
    with pytest.raises(FingerprintsFileError, match=re.escape(needle)):
        parse_fingerprints(_with(mutate))


def test_the_packaged_file_holds_the_two_lists():
    assert hashed_config_denylist() == frozenset(
        {"meta", "docs", "description", "grants", "tags", "access", "group", "freshness"})
    assert projection_keys() == ("schema", "table", "kind", "script", "reads", "output_columns",
                                 "extra_columns", "config", "secret_ref", "mode_type",
                                 "primary_key", "assets")


def _second_rule(d):
    d["families"]["dbt-model"].append(
        {**_model(1)(d), "id": "other_config_2", "inputs": ["config.*"]})


MORE_REFUSALS = [
    ("rule", _second_rule),
    ("rule", lambda d: _model(1)(d).update(fingerprint="both", parts=["config"])),
    ("rule", lambda d: _model(1)(d).update(parts=["source"])),
    ("rule", lambda d: _model(1)(d).update(inputs=["config.*", "config.x"])),
    ("rule", lambda d: d["families"]["every"][0].update(rule="outside_denylist")),
    ("rule", lambda d: _model(1)(d).update(rule="inside_denylist")),
    ("when", lambda d: _model(1)(d).update(when=_model(3)(d)["when"])),
    ("version", lambda d: d.update(version=True)),
    ("key name", lambda d: d["lists"]["projection_keys"].append("a.b")),
    ("missing", lambda d: _model(0)(d).pop("why")),
]


@pytest.mark.parametrize(("needle", "mutate"), MORE_REFUSALS)
def test_a_malformed_rule_or_value_is_refused(needle, mutate):
    with pytest.raises(FingerprintsFileError, match=re.escape(needle)):
        parse_fingerprints(_with(mutate))


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "version: [1\n"])
def test_a_text_that_is_not_a_fingerprints_mapping_is_refused(text):
    with pytest.raises(FingerprintsFileError):
        parse_fingerprints(text)


def test_a_cell_stays_on_one_line():
    doc = yaml.safe_load(MINIMAL)
    doc["families"]["dbt-model"][0]["why"] = "source\npart"
    md = render_markdown(parse_fingerprints(yaml.safe_dump(doc, sort_keys=False)))
    assert "| source part |" in md


def _repeat(old, new):
    """Return MINIMAL with its text `old` once replaced by `new`."""
    assert MINIMAL.count(old) == 1
    return MINIMAL.replace(old, new, 1)


def _line_of(text, needle):
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


REPEATED_KEYS = [
    ("an entry in flow style", "fingerprint",
     _repeat('parts: ["source"], why: "source part"',
             'parts: ["source"], fingerprint: "neither", why: "source part"'),
     'id: "raw_code"'),
    ("a section under families", "dbt-seed",
     _repeat("  dbt-snapshot: []\n", "  dbt-snapshot: []\n  dbt-seed: []\n"),
     "  dbt-seed: []"),
    ("a key under lists", "hashed_config_denylist",
     _repeat('  hashed_config_denylist: ["meta", "tags"]\n',
             '  hashed_config_denylist: ["meta", "tags"]\n  hashed_config_denylist: ["x"]\n'),
     "  hashed_config_denylist:"),
    ("a top-level key", "version", MINIMAL + "version: 1\n", "version: 1"),
]


@pytest.mark.parametrize(("where", "key", "text", "first"), REPEATED_KEYS,
                         ids=[case[0] for case in REPEATED_KEYS])
def test_a_repeated_key_is_refused_with_its_name_and_line(where, key, text, first):
    first_line = _line_of(text, first)
    repeated_line = len(text.splitlines()) if where == "a top-level key" else None
    with pytest.raises(FingerprintsFileError, match=re.escape(f"duplicate key {key!r}")) as caught:
        parse_fingerprints(text)
    message = str(caught.value)
    assert f"first written at line {first_line}" in message
    if repeated_line is not None:
        assert f"line {repeated_line}, column" in message


def test_a_repeated_key_in_a_block_entry_is_refused_at_its_line():
    block = yaml.safe_dump(yaml.safe_load(MINIMAL), sort_keys=False)
    line = "    fingerprint: content\n"
    text = block.replace(line, line + "    fingerprint: neither\n", 1)
    assert text != block
    repeated = text[: text.index(line)].count("\n") + 2
    with pytest.raises(FingerprintsFileError, match="duplicate key 'fingerprint'") as caught:
        parse_fingerprints(text)
    assert f"first written at line {repeated - 1}" in str(caught.value)
    assert f"line {repeated}, column 5" in str(caught.value)


def test_a_second_fingerprint_in_a_packaged_entry_is_refused():
    shipped = importlib.resources.files("continuo_contracts").joinpath("fingerprints.yaml")
    text = shipped.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    seed = lines.index("  dbt-seed:\n")
    grants = next(i for i in range(seed, len(lines)) if lines[i] == '    - id: "grants"\n')
    fingerprint = next(i for i in range(grants, len(lines))
                       if lines[i].lstrip().startswith("fingerprint:"))
    lines.insert(fingerprint + 1, lines[fingerprint].replace('"contract"', '"neither"'))
    assert lines[fingerprint + 1] != lines[fingerprint]
    with pytest.raises(FingerprintsFileError, match="duplicate key 'fingerprint'"):
        parse_fingerprints("".join(lines))


def test_a_key_that_a_merge_brings_in_counts_as_written():
    text = "base: &base {k: 1}\nentry: {<<: *base, k: 2}\n"
    with pytest.raises(FingerprintsFileError, match="duplicate key 'k'"):
        parse_fingerprints(text)


# Each case is a name, a text that brings the key `k` in twice, and the lines of the `<<` keys
# that bring it in.
MERGED_KEYS = [
    ("a list of aliases in flow style",
     "a: &a {k: 1}\nb: &b {k: 2}\nz: {<<: [*a, *b]}\n", [3]),
    ("a list of aliases in block style",
     "a: &a {k: 1}\nb: &b {k: 2}\nz:\n  <<: [*a, *b]\n", [4]),
    ("a merge and then the key", "base: &base {k: 1}\nentry: {<<: *base, k: 2}\n", [2]),
    ("the key and then a merge", "base: &base {k: 1}\nentry:\n  k: 2\n  <<: *base\n", [4]),
    ("two merges", "a: &a {k: 1}\nb: &b {k: 2}\nz:\n  <<: *a\n  <<: *b\n", [4, 5]),
    ("a merged mapping that merges",
     "a: &a {k: 1}\nb: &b\n  <<: *a\nz:\n  <<: *b\n  k: 3\n", [5]),
]


@pytest.mark.parametrize(("text", "merge_lines"), [case[1:] for case in MERGED_KEYS],
                         ids=[case[0] for case in MERGED_KEYS])
def test_a_duplicate_key_through_a_merge_is_reported_at_the_line_of_the_merge(text, merge_lines):
    for line in merge_lines:
        assert "<<" in text.splitlines()[line - 1]
    with pytest.raises(FingerprintsFileError, match="duplicate key 'k'") as caught:
        parse_fingerprints(text)
    message = str(caught.value)
    reported = message[message.index("duplicate key"):]
    assert "first written" not in reported
    assert [int(n) for n in re.findall(r"\d+", reported.splitlines()[0])] == merge_lines
    assert f"line {merge_lines[0]}, column" in reported
    assert {int(n) for n in re.findall(r"\bline (\d+)", reported)} == {merge_lines[0]}


@pytest.mark.parametrize("text", ["? [a]\n: 1\n", "x: !!map 1\n"])
def test_a_key_or_tag_pyyaml_refuses_is_still_a_file_error(text):
    with pytest.raises(FingerprintsFileError, match="is not valid YAML"):
        parse_fingerprints(text)


def test_the_packaged_file_loads_under_the_strict_loader():
    shipped = importlib.resources.files("continuo_contracts").joinpath("fingerprints.yaml")
    text = shipped.read_text(encoding="utf-8")
    assert yaml.load(text, Loader=_StrictLoader) == yaml.safe_load(text)
    assert parse_fingerprints(text) == load()
