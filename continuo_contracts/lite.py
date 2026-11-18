"""The producer's lite check of a dbt service: what a manifest says about its contracts.

`check_manifest` takes the `manifest.json` that `dbt parse` writes and does for each model, seed
and snapshot what continuo's release does with it: it leaves out the nodes `dbt.skip_reason`
skips, writes the contract of each node it keeps (`dbt.generate`), checks it
(`validate.validate_dbt_contract`) and hashes its stored form (`contract.normalise_dbt` and
`contract.contract_hash`). It also compares the columns a seed declares with the header of its CSV
file, which `read_header` gives. The result is a `LiteReport`.

The check reads no file, runs no program, logs nothing and prints nothing: the caller reads the
manifest and the CSV headers and prints the report. It is a lite check because the release does
more than it does (`RELEASE_ONLY`), so a report with no error does not guarantee that the release
creates the service's contracts (`BANNER`).
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from continuo_contracts import contract, dbt, validate
from continuo_contracts.errors import DbtNodeError, HashInputError

BANNER: Final = (
    "LITE CHECK. Passing it does not guarantee that continuo will create this "
    "service's contracts at release time."
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
        "the estate-wide checks, such as duplicate relations and the ontology's "
        "rules across services, need the contracts of every service, and the "
        "lite check sees one service.",
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
        "the release checks the bindings of a contract against the ontology the "
        "install runs, and the lite check reads no ontology, so it checks no "
        "binding.",
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
_ALL_SKIPPED: Final = (
    "every node of the service is skipped: the release rejects it as invalid_artifact "
    "(manifest contains no model/seed nodes)"
)
_UNREADABLE: Final = "the CSV file cannot be read, so its header is not checked against the declared columns"


@dataclass(frozen=True)
class Line:
    """One thing the lite check says about a node.

    `level` is `info` for a node the check leaves out or a dormant node, and `error` for a
    problem that fails the check. `node_id` is the node's key in the manifest, such as
    `model.shop.orders`, or the empty string for a line about the whole service. `message` does
    not repeat the node.
    """

    level: str
    node_id: str
    message: str


@dataclass(frozen=True)
class LiteReport:
    """What the lite check found in a manifest.

    `service` is the service the gates compare each node with, `dbt.project_service` of the
    manifest. `lines` are the lines of the check, by node id and, for one node, in the order
    `check_manifest` describes, with the line about the whole service last. `contracts` maps the
    id of each contract that was generated, has no issue and can be stored to its
    `contract_hash`. A node whose contract has an issue has no entry, and a seed whose header
    differs from its declared columns has one.
    """

    service: str
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


def _check_kept_node(
    node_id: str,
    node: Mapping[str, Any],
    tests: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    read_header: Callable[[Mapping[str, Any]], Sequence[str] | None],
) -> tuple[list[Line], tuple[str, str] | None]:
    """Return the lines of a node that continuo keeps, and its contract id and hash.

    The hash is `None` unless the contract is generated, has no validation issue and can be
    stored.
    """
    lines: list[Line] = []
    try:
        if not dbt.schedule(node):
            lines.append(Line(_INFO, node_id, _DORMANT))
        generated = dbt.generate(node, tests, manifest=manifest)
    except DbtNodeError as error:
        return [*lines, _refusal(node_id, error)], None
    document = generated.document
    issues = validate.validate_dbt_contract(document)
    lines.extend(Line(_ERROR, node_id, str(issue)) for issue in issues)
    hashed: tuple[str, str] | None = None
    if not issues:
        try:
            stored = contract.normalise_dbt(document, reads=[])
        except HashInputError as error:
            lines.append(Line(_ERROR, node_id, str(error)))
        else:
            hashed = (document["id"], contract.contract_hash(stored))
    if node.get("resource_type") == "seed":
        lines.extend(_header_lines(node_id, node, document, read_header))
    return lines, hashed


def check_manifest(
    manifest: Mapping[str, Any],
    *,
    declared_service: str,
    read_header: Callable[[Mapping[str, Any]], Sequence[str] | None],
) -> LiteReport:
    """Return the lite check's report of the dbt `manifest`.

    `manifest` is a `manifest.json` as `json.load` returns it. `declared_service` is the service
    of the project when the manifest names none, as in `dbt.project_service`. `read_header` gives
    the column names in the first row of a seed's CSV file, from the seed's manifest node, and
    `None` when the file cannot be read; it is called once for each seed that gets a contract,
    and an exception it raises is not caught.

    The models, seeds and snapshots of the manifest are taken by node id, in code point order,
    whatever the order of the manifest. For each node, in this order:

    1. A node that `dbt.skip_reason` leaves out gives one `info` line that says why, and nothing
       else. A node whose gate cannot read a field (`DbtNodeError`) gives one `error` line instead.
    2. A kept node with no tag, a dormant node, gives an `info` line.
    3. `dbt.generate` writes the contract. A field it cannot read (`DbtNodeError`) gives one
       `error` line, and the node has no more lines.
    4. Each issue of `validate.validate_dbt_contract` gives an `error` line, `<path>: <message>`.
    5. A contract with no issue has its stored form, which `contract.normalise_dbt` gives with no
       reads from compiled SQL, hashed with `contract.contract_hash`, and `contracts` maps the
       contract's id to the hash. A contract with an issue has no hash, as only a valid contract
       gets one. A declared read that is not a table gives an `error` line and no hash.
    6. For a kept seed, `read_header` gives its header. `None` gives an `error` line. Otherwise
       each column the header and the declared columns disagree on gives an `error` line, as
       `dbt.seed_header_issues` words it. The declared columns are the names of the contract's
       properties, in order.

    The message of an `error` line from a `DbtNodeError` does not repeat the node id. When every
    model, seed and snapshot is skipped, or there is none, the last line is an `error` that the
    release rejects the service. A node whose gate cannot read it is not counted as skipped.
    """
    service = dbt.project_service(manifest, declared_service)
    tests = dbt.tests_by_node(manifest)
    nodes = manifest.get("nodes") or {}
    lines: list[Line] = []
    contracts: dict[str, str] = {}
    unskipped = 0
    for node_id in sorted(nodes):
        node = nodes[node_id]
        if node.get("resource_type") not in dbt.RESOURCE_TYPES:
            continue
        try:
            reason = dbt.skip_reason(node, project=service)
        except DbtNodeError as error:
            unskipped += 1
            lines.append(_refusal(node_id, error))
            continue
        if reason is not None:
            lines.append(Line(_INFO, node_id, _SKIPS[reason]))
            continue
        unskipped += 1
        found, hashed = _check_kept_node(
            node_id, node, tests.get(node_id, []), manifest, read_header
        )
        lines.extend(found)
        if hashed is not None:
            contracts[hashed[0]] = hashed[1]
    if not unskipped:
        lines.append(Line(_ERROR, "", _ALL_SKIPPED))
    return LiteReport(service, tuple(lines), contracts)
