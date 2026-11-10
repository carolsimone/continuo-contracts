import re

import pytest
import yaml

from continuo_contracts.errors import FingerprintsFileError
from continuo_contracts.fingerprints import (
    Entry,
    When,
    hashed_config_denylist,
    parse_fingerprints,
    projection_keys,
    render_in,
    render_markdown,
)

MINIMAL = """
version: 1
lists:
  hashed_config_denylist: ["meta", "tags"]
  projection_keys: ["schema", "table"]
families:
  every:
    - {id: "provenance", field: "Image tag", source: "the release", fingerprint: "neither", why: "provenance", inputs: ["release.image_tag"]}
  dbt-model:
    - {id: "raw_code", field: "SQL | text", source: "`raw_code`", fingerprint: "content", parts: ["source"], why: "source part", inputs: ["raw_code"]}
    - {id: "other_config", field: "Every other config key", source: "`config`", fingerprint: "content", parts: ["config"], why: "hashed_config", inputs: ["config.*"], rule: "outside_denylist"}
    - {id: "tags", field: "`tags`", source: "node `tags`", fingerprint: "contract", why: "tags", inputs: ["config.tags"]}
    - {id: "meta_other", field: "Every other `meta` key", source: "`config.meta`", fingerprint: "neither", why: "no field holds it", inputs: ["config.meta"], when: {condition: "sql_reads", fingerprint: "content", parts: ["compiled"]}}
    - {id: "declared_columns", field: "Declared columns", source: "`schema.yml`", fingerprint: "contract", why: "properties", inputs: ["contract.columns"], when: {condition: "enforced", fingerprint: "both", parts: ["config"]}}
    - {id: "image_tag", field: "Image tag", source: "the release", fingerprint: "neither", why: "provenance", inputs: ["release.image_tag"]}
  dbt-seed: []
  dbt-snapshot: []
  dbt-test:
    - {id: "severity", field: "`severity`", source: "`config`", fingerprint: "both", parts: ["config"], why: "lower-cased", inputs: ["config.severity"]}
  python-node:
    - {id: "assets", field: "`assets`", source: "authored", fingerprint: "both", parts: ["config", "shared"], why: "projection and shared part", inputs: ["projection.assets"]}
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
    assert "the last column says why" not in md
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
    ("duplicate id", lambda d: d["families"]["dbt-model"].append({**_model(0)(d), "inputs": ["x"]})),
    ("duplicate input", lambda d: d["families"]["dbt-model"].append({**_model(0)(d), "id": "raw_code_2"})),
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
                                 "extra_columns", "config", "secret_ref", "mode_type", "primary_key", "assets")


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
