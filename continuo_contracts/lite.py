"""The producer's lite check of a dbt project: what a manifest says about its contracts.

`check_manifest` takes the `manifest.json` that `dbt parse` writes and does for each model, seed
and snapshot what continuo's release does with it: it leaves out the nodes `dbt.skip_reason`
skips, writes the contract of each node it keeps (`dbt.generate`), checks it
(`validate.validate_dbt_contract`) and hashes its stored form (`contract.normalise_dbt` and
`contract.contract_hash`). It also compares the project of each node it keeps with the declared
project, the project the release names, as the release does, and the columns a seed declares
with the header of its CSV file, which `read_header` gives. The result is a `LiteReport`.

Given an `Ontology`, the check also reads the bindings of the contracts against it, with the
rules that need no other project (`ontology_check.PER_PROJECT_RULES`). It never runs the rules
that read another project, `access`, `order` and `drift`: the lite check sees one project.

The check reads no file, runs no program, logs nothing and prints nothing: the caller reads the
manifest, the ontology file and the CSV headers and prints the report. It is a lite check because
the release does more than it does (`RELEASE_ONLY`), so a report with no error does not guarantee
that the release creates the project's contracts (`BANNER`).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts import contract, dbt, ontology_check, validate
from continuo_contracts.errors import DbtNodeError, HashInputError
from continuo_contracts.ontology import Ontology, resolve

BANNER: Final = (
    "LITE CHECK. Passing it does not guarantee that continuo will create this "
    "project's contracts at release time."
)

# What only the release does, as `(what, why it can differ)`.
RELEASE_ONLY: Final[tuple[tuple[str, str], ...]] = (
    (
        "dbt runs in the team's image",
        "continuo compiles the project in the team's own image, and CI can run "
        "another dbt version.",
    ),
    (
        "the whole estate is parsed",
        "the estate-wide checks, such as duplicate relations and the ontology "
        "rules `access`, `order` and `drift`, need the contracts of every "
        "project, and the lite check sees one project.",
    ),
    (
        "the tables a node reads",
        "continuo adds the tables that a node's compiled SQL names to the tables "
        "it declares, and `dbt parse` compiles nothing, so a contract of the lite "
        "check lists only the declared ones.",
    ),
    (
        "the install's library version",
        "the install runs the continuo-contracts version that its chart release "
        "names, and CI can pin another.",
    ),
    (
        "install settings",
        "`servers` and absolute links exist only in published files.",
    ),
    (
        "the install's ontology",
        "the release checks bindings against the ontology the install runs; the "
        "lite check reads the file --ontology names, which can differ, and "
        "without --ontology it checks no binding.",
    ),
)

_INFO: Final = "info"
_ERROR: Final = "error"

_NO_OWNER, _LOCAL_STUB, _INSTALLED_PACKAGE = dbt.SKIP_REASONS

# What the check says about a node that `dbt.skip_reason` leaves out, by reason.
_SKIPS: Final = MappingProxyType(
    {
        _NO_OWNER: "no owner, continuo skips it",
        _LOCAL_STUB: "local stub, continuo skips it",
        _INSTALLED_PACKAGE: "from an installed package and has no tag, continuo skips it",
    }
)
_DORMANT: Final = "no tag: a dormant node, it runs only by hand"
# What the check says about a kept node whose project is not the declared project, which the
# release rejects whatever the node's contract.
_OTHER_PROJECT: Final = (
    "the node's project is {}, the declared project is {}: the release rejects a manifest "
    "that holds a node of another project as invalid_artifact"
)
# What the check says instead about a kept node with no project (no `fqn`, an empty one, or one
# whose first name is empty), which the release cannot read or finds of another project.
_NO_PROJECT: Final = (
    "the node has no project, the declared project is {}: the release rejects the manifest "
    "as invalid_artifact"
)
_ALL_SKIPPED: Final = (
    "every node of the project is skipped: the release rejects it as invalid_artifact "
    "(manifest contains no model/seed nodes)"
)
# What the check says about each of two or more kept nodes whose contracts have one id, which the
# release rejects whatever the nodes' projects, and which the bundle of contracts cannot hold.
_SHARED_ID: Final = "contract id {} is also the id of {}: the release rejects it as duplicate_table"
_UNREADABLE: Final = "the CSV file cannot be read, so its header is not checked against the declared columns"
# What the check says about a kept seed of an installed package, whose CSV file is not in the
# project.
_INSTALLED_SEED: Final = (
    "a seed of an installed package: its CSV header is not checked"
)
# What the check says, once and for the whole project, about an ontology file with an error of its
# own: the file is checked alone by `continuo-contracts ontology check`, so no contract is read
# against it. `{}` is the number of errors.
_BROKEN_ONTOLOGY: Final = "the ontology file has {} error(s): run continuo-contracts ontology check <file>"


@dataclass(frozen=True)
class Line:
    """One thing the lite check says about a node.

    `level` is `info` for a node the check leaves out, a dormant node, a seed of an installed
    package or a project the ontology does not declare, `warning` for something the ontology
    check finds that does not fail the check, and `error` for a problem that fails the check.
    `node_id` is the node's key in the manifest, such as `model.shop.orders`, or the empty string
    for a line about the whole project. `message` does not repeat the node.
    """

    level: str
    node_id: str
    message: str


@dataclass(frozen=True)
class LiteReport:
    """What the lite check found in a manifest.

    `project` is the project the gates compare each node with, `dbt.manifest_project` of the
    manifest. `lines` are the lines of the check, by node id and, for one node, in the order
    `check_manifest` describes, with the line about the whole project last. `contracts` maps the
    id of each contract that was generated, has no issue, can be stored and is the contract of no
    other node to its `contract_hash`. A node whose contract has an issue has no entry, an id
    that two or more nodes' contracts have has none, and a seed whose header differs from its
    declared columns has one. So a consumer counts the nodes by `lines`, not by `contracts`.
    """

    project: str
    lines: tuple[Line, ...]
    contracts: Mapping[str, str]

    @property
    def ok(self) -> bool:
        """Whether no line is an `error`."""
        return all(line.level != _ERROR for line in self.lines)


def _refusal(node_id: str, error: DbtNodeError) -> Line:
    """Return the error line for a node that the library cannot read.

    The message of the error starts with the node's id, which the line holds already.
    """
    return Line(_ERROR, node_id, str(error).removeprefix(f"{node_id}: "))


def _header_lines(
    node_id: str,
    node: Mapping[str, Any],
    document: Mapping[str, Any],
    read_header: Callable[[Mapping[str, Any]], Sequence[str] | None],
) -> list[Line]:
    """Return the error lines of a seed whose CSV header and declared columns differ."""
    header = read_header(node)
    if header is None:
        return [Line(_ERROR, node_id, _UNREADABLE)]
    declared = [
        column["name"] for column in document["schema"][0].get("properties", [])
    ]
    return [
        Line(_ERROR, node_id, message)
        for message in dbt.seed_header_issues(declared, header)
    ]


def _is_installed(node: Mapping[str, Any], manifest: Mapping[str, Any]) -> bool:
    """Say whether `node` comes from a package the project installs.

    It does when the manifest names its project and the node's `package_name` is another one; a
    node with no `package_name` is the project's.
    """
    project = (manifest.get("metadata") or {}).get("project_name")
    package = node.get("package_name")
    return bool(project) and package is not None and package != project


def _check_kept_node(
    node_id: str,
    node: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    declared_project: str,
    read_header: Callable[[Mapping[str, Any]], Sequence[str] | None],
) -> tuple[list[Line], str | None, str | None, dict[str, Any] | None]:
    """Return what the check finds in a node that continuo keeps.

    The result is the node's lines, the id of its contract, its hash and its stored form. The id
    is `None` unless the contract is generated, and the hash and the stored form are `None`
    unless the contract is generated, has no validation issue and can be stored.
    """
    lines: list[Line] = []
    # The gates have read the node's `fqn`, so its project can be read.
    project = dbt.node_project(node)
    if declared_project and project != declared_project:
        message = (
            _OTHER_PROJECT.format(project, declared_project)
            if project
            else _NO_PROJECT.format(declared_project)
        )
        lines.append(Line(_ERROR, node_id, message))
    try:
        if not dbt.schedule(node):
            lines.append(Line(_INFO, node_id, _DORMANT))
        generated = dbt.generate(node, tests, manifest=manifest)
    except DbtNodeError as error:
        return [*lines, _refusal(node_id, error)], None, None, None
    document = generated.document
    issues = validate.validate_dbt_contract(document)
    lines.extend(Line(_ERROR, node_id, str(issue)) for issue in issues)
    digest: str | None = None
    stored: dict[str, Any] | None = None
    if not issues:
        try:
            stored = contract.normalise_dbt(document, reads=[])
        except HashInputError as error:
            lines.append(Line(_ERROR, node_id, str(error)))
        else:
            digest = contract.contract_hash(stored)
    if node.get("resource_type") == "seed":
        if _is_installed(node, manifest):
            lines.append(Line(_INFO, node_id, _INSTALLED_SEED))
        else:
            lines.extend(_header_lines(node_id, node, document, read_header))
    return lines, document["id"], digest, stored


def _ontology_lines(
    onto: Ontology, stored: Mapping[str, Mapping[str, Any]]
) -> tuple[dict[str, list[Line]], list[Line]]:
    """Return the lines of the ontology check by node id, and the lines of the whole project.

    `stored` maps a node id to the stored form of its contract. An ontology file with an error of
    its own gives the one error line of the whole project and no other: `ontology_check.check`
    reads contracts against a file that `ontology_findings` accepts. Otherwise each contract is
    resolved against the ontology, and `check` runs over them with the rules that need no other
    project. The severity of a finding is the level of its line: a finding about a contract is a
    line of its node, and any other finding, the one about a project the ontology does not
    declare, is a line of the whole project.
    """
    broken = sum(
        finding.severity == _ERROR
        for finding in ontology_check.ontology_findings(onto)
    )
    if broken:
        return {}, [Line(_ERROR, "", _BROKEN_ONTOLOGY.format(broken))]
    node_of = {contract["id"]: node_id for node_id, contract in stored.items()}
    findings = ontology_check.check(
        onto,
        [resolve(contract, onto) for contract in stored.values()],
        rules=ontology_check.PER_PROJECT_RULES,
    )
    by_node: dict[str, list[Line]] = {}
    whole_project: list[Line] = []
    for finding in findings:
        if finding.contract_id is None:
            whole_project.append(Line(finding.severity, "", finding.message))
        else:
            node_id = node_of[finding.contract_id]
            by_node.setdefault(node_id, []).append(
                Line(finding.severity, node_id, finding.message)
            )
    return by_node, whole_project


def check_manifest(
    manifest: Mapping[str, Any],
    *,
    declared_project: str,
    read_header: Callable[[Mapping[str, Any]], Sequence[str] | None],
    ontology: Ontology | None = None,
) -> LiteReport:
    """Return the lite check's report of the dbt `manifest`.

    `manifest` is a `manifest.json` as `json.load` returns it. `declared_project` is the project
    the release names: the release compares the project of every node it keeps with it, and it
    is used as the project when the manifest's `metadata.project_name` is empty, as in
    `dbt.manifest_project`. The empty string declares no project, and then no node's project is
    compared with it, as the release compares none when its request declares no project.
    `read_header` gives the column names in the first row of a seed's CSV file, from the seed's
    manifest node, and `None` when the file cannot be read; it is called once for each seed of
    the project that gets a contract, and an exception it raises is not caught. `ontology` is
    the ontology to read the bindings of the contracts against, or `None`, the default, to read
    none: the check then reads the shape of the bindings, as `validate` does, and nothing else of
    them, and its report is the one it gives without the parameter.

    The models, seeds and snapshots of the manifest are taken by node id, in code point order,
    whatever the order of the manifest. For each node, in this order:

    1. A node that `dbt.skip_reason` leaves out gives one `info` line that says why, and nothing
       else. A node whose gate cannot read a field (`DbtNodeError`) gives one `error` line instead.
    2. When `declared_project` is not empty, a kept node whose project, `dbt.node_project`, is
       another gives an `error` line, `the node's project is <project>, the declared project is
       <declared>: the release rejects a manifest that holds a node of another project as
       invalid_artifact`, as the first line of the node, whatever its contract. A kept node with
       no project, whose `fqn` is missing or empty or starts with an empty name, gives `the node
       has no project, the declared project is <declared>: the release rejects the manifest as
       invalid_artifact` there instead. A skipped node is not compared, since the release never
       reads it.
    3. A kept node with no tag, a dormant node, gives an `info` line.
    4. `dbt.generate` writes the contract. A field it cannot read (`DbtNodeError`) gives one
       `error` line, and the node has no more lines.
    5. Each issue of `validate.validate_dbt_contract` gives an `error` line, `<path>: <message>`.
    6. A contract with no issue has its stored form, which `contract.normalise_dbt` gives with no
       reads from compiled SQL, hashed with `contract.contract_hash`, and `contracts` maps the
       contract's id to the hash. A contract with an issue has no hash, as only a valid contract
       gets one. A declared read that is not a table gives an `error` line and no hash.
    7. For a kept seed of the project, `read_header` gives its header. `None` gives an `error`
       line. Otherwise each column the header and the declared columns disagree on gives an
       `error` line, as `dbt.seed_header_issues` words it. The declared columns are the names of
       the contract's properties, in order. A kept seed of an installed package, one whose
       `package_name` is not the manifest's `metadata.project_name`, has its CSV file outside the
       project: it gets an `info` line that says its header is not checked, and `read_header` is
       not called for it. A seed with no `package_name`, or a manifest that names no project,
       counts as the project's.
    8. A generated contract whose id is the id of another node's generated contract, such as two
       names that differ only in case, or a project's model and a tagged package model of one
       name and schema with other aliases, gives an `error` line on each of those nodes,
       `contract id <id> is also the id of <the other node ids, in id order>: the release rejects
       it as duplicate_table`, and the id has no entry in `contracts`. The release rejects such a
       manifest whatever the projects, and its bundle of contracts holds none of them.
    9. With an `ontology`, a contract is read against it when it has a hash (steps 4 to 6), is
       the contract of no other node (step 8) and, when `declared_project` is not empty, is of
       that project. It is stored with its declared reads alone, since the check has no compiled
       SQL, and resolved against the ontology (`ontology.resolve`), and `ontology_check.check`
       runs over these contracts with `PER_PROJECT_RULES`, so no rule reads another project. An
       error finding gives an `error` line, a warning a `warning` line and an information
       finding an `info` line, each with the finding's message, after the lines of steps 1 to 8
       of the node of its contract. The one finding that has no contract is about a project the
       ontology does not declare, `not declared in the ontology: none of this project's
       contracts is checked`: it gives an `info` line of the whole project, after the lines of
       the nodes. A contract of such a project that writes bindings gives a `warning` line,
       `writes bindings, but its project is not declared in the ontology: they are kept as
       written and not checked`.
    10. An ontology with an error of its own, which `ontology_check.ontology_findings` reports,
        gives instead of step 9 the one `error` line of the whole project `the ontology file has
        <n> error(s): run continuo-contracts ontology check <file>`, with the number of errors,
        and no contract is read against it. The line comes after the lines of the nodes and
        before the line about skipped nodes. Neither step changes `contracts`, which holds the
        hashes of the stored forms, never those of the resolved contracts.

    The message of an `error` line from a `DbtNodeError` does not repeat the node id. When every
    model, seed and snapshot is skipped, or there is none, the last line is an `error` that the
    release rejects the project. A node whose gate cannot read it is not counted as skipped.

    A `metadata` or a `nodes` of the manifest, or a field of a test that `dbt.tests_by_node`
    reads, in a shape the library cannot read raises `DbtNodeError`, since it is no node's to
    report.
    """
    project = dbt.manifest_project(manifest, declared_project)
    tests = dbt.tests_by_node(manifest)
    nodes = manifest.get("nodes") or {}
    found: dict[str, list[Line]] = {}
    claims: dict[str, list[tuple[str, str | None]]] = {}
    # The stored form of each contract that the ontology is read against, by node id: kept only
    # when there is an ontology, so a check without one lets each form go once it is hashed.
    stored_forms: dict[str, dict[str, Any]] = {}
    unskipped = 0
    for node_id in sorted(nodes):
        node = nodes[node_id]
        if node.get("resource_type") not in dbt.RESOURCE_TYPES:
            continue
        try:
            reason = dbt.skip_reason(node, project=project)
        except DbtNodeError as error:
            unskipped += 1
            found[node_id] = [_refusal(node_id, error)]
            continue
        if reason is not None:
            found[node_id] = [Line(_INFO, node_id, _SKIPS[reason])]
            continue
        unskipped += 1
        found[node_id], contract_id, digest, stored = _check_kept_node(
            node_id,
            node,
            tests.get(node_id, []),
            manifest,
            declared_project,
            read_header,
        )
        if contract_id is not None:
            claims.setdefault(contract_id, []).append((node_id, digest))
        if (
            ontology is not None
            and stored is not None
            and (
                not declared_project or dbt.node_project(node) == declared_project
            )
        ):
            stored_forms[node_id] = stored
    contracts: dict[str, str] = {}
    for contract_id, claimed in claims.items():
        if len(claimed) == 1:
            if claimed[0][1] is not None:
                contracts[contract_id] = claimed[0][1]
            continue
        for node_id, _ in claimed:
            others = ", ".join(other for other, _ in claimed if other != node_id)
            found[node_id].append(
                Line(_ERROR, node_id, _SHARED_ID.format(contract_id, others))
            )
    project_lines: list[Line] = []
    if ontology is not None:
        by_node, project_lines = _ontology_lines(
            ontology,
            {
                node_id: stored
                for node_id, stored in stored_forms.items()
                if len(claims[stored["id"]]) == 1
            },
        )
        for node_id, node_lines in by_node.items():
            found[node_id].extend(node_lines)
    lines = [line for node_lines in found.values() for line in node_lines]
    lines.extend(project_lines)
    if not unskipped:
        lines.append(Line(_ERROR, "", _ALL_SKIPPED))
    return LiteReport(project, tuple(lines), contracts)
