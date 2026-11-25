# Walkthrough

What happens to one dbt model, from [`schema.yml`](glossary.md#dbt-words) to its hash? The first
half runs the [lite check](glossary.md#lite-check) with no Python, the second does it in Python.

## From schema.yml to a check result

A dbt project named `finance` holds one model, `models/fx_transactions_eur.sql`, which is
`select 1 as transaction_id`, and this `models/schema.yml`:

```yaml
models:
  - name: fx_transactions_eur
    description: FX transactions restated in EUR.
    config:
      materialized: table
      tags: [daily]
      meta: {owner: finance, criticality: CORE}
    columns:
      - name: transaction_id
        data_tests: [unique, not_null]
```

Notice: the `owner` in [`meta`](glossary.md#dbt-words) gives the model a
[contract](glossary.md#contract), and its first tag is its [schedule](glossary.md#schedule).

`dbt parse` needs a [`profiles.yml`](glossary.md#dbt-words) but never connects, so the project keeps
a dummy one in `finance/ci/`, as the [CI job](check-your-project.md#a-ci-job) does:

```yaml
finance:
  target: ci
  outputs:
    ci: {type: postgres, host: localhost, port: 5432, user: ci, password: ci,
         dbname: finance, schema: analytics}
```

Notice: dbt gives the model the target's `schema`, `analytics`, which starts the contract id
`analytics.fx_transactions_eur`. By dbt's default rule, `schema: marts` gives `analytics_marts`.

From the folder that holds `finance/`, run the lite check. It runs `dbt parse` (dbt on your `PATH`,
or pass `--dbt`) and checks each [node](glossary.md#node). Long lines are cut at `...` here.

```bash
$ continuo-contracts dbt check finance --profiles-dir finance/ci --project finance; echo "exit $?"
INFO LITE CHECK. Passing it does not guarantee that continuo will create this project's ...
INFO What only the release does:
...
INFO LITE CHECK. Passing it does not guarantee that continuo will create this project's ...
exit 0
```

Notice: a passing run prints only the banner and, between the banners, six lines on what only
continuo's [release](glossary.md#release) does. Then set `criticality: HIGH` and run it again:

```bash
$ continuo-contracts dbt check finance --profiles-dir finance/ci --project finance; echo "exit $?"
INFO LITE CHECK. Passing it does not guarantee that continuo will create this project's ...
ERROR model.finance.fx_transactions_eur: customProperties[3].value: `criticality` is 'HIGH', ...
...
exit 1
```

Notice: an ERROR line names the node and the field of its contract, and fails the run.

| Output | Meaning |
|---|---|
| `LITE CHECK. ...` | the banner: passing does not guarantee that the release accepts the project |
| `ERROR model.finance.fx_transactions_eur:` | a problem on the node with this key in the manifest |
| `customProperties[3].value: ...` | where in the contract, then why ([below](#under-the-hood)) |

## Under the hood

In the [manifest](glossary.md#manifest) that `dbt parse` writes, `finance/target/manifest.json`,
each model, [seed, snapshot](glossary.md#dbt-words) and test is an entry of `nodes`. Below, the
model's entry and its two tests keep only the fields that decide the contract (`dbt.read_tree` lists
them). dbt wrote `primary_key` from the tests.

`generate` writes the [ODCS](glossary.md#odcs) contract, with continuo's facts as custom properties
and each test as a [quality rule](glossary.md#quality-rule). A **strict** test has severity `error`,
no `where` and dbt's default thresholds. A strict `not_null` or `unique` also sets `required` or
`unique`, and a strict `relationships` gives a relationship, whose target `generate` finds in
`manifest` ([dbt contracts](dbt-contracts.md#tests-and-quality-rules)).

```python
>>> from continuo_contracts import contract, dbt, validate
>>> node = {
...     "resource_type": "model", "name": "fx_transactions_eur", "schema": "analytics",
...     "fqn": ["finance", "fx_transactions_eur"], "tags": ["daily"],
...     "original_file_path": "models/fx_transactions_eur.sql", "primary_key": ["transaction_id"],
...     "description": "FX transactions restated in EUR.",
...     "config": {"materialized": "table", "meta": {"owner": "finance", "criticality": "CORE"}},
...     "columns": {"transaction_id": {"name": "transaction_id"}}}
>>> tests = [{"name": f"{k}_fx_transactions_eur_transaction_id", "test_metadata": {"name": k},
...           "column_name": "transaction_id", "depends_on": {"macros": [f"macro.dbt.test_{k}"]}}
...          for k in ("unique", "not_null")]
>>> doc = dbt.generate(node, tests, manifest={}).document
>>> doc["id"], {p["property"]: p["value"] for p in doc["customProperties"]}
('analytics.fx_transactions_eur', {'project': 'finance', 'kind': 'dbt-model', 'schedule': 'daily',
 'criticality': 'CORE', 'filePath': 'models/fx_transactions_eur.sql',
 'materialization': {'materialized': 'table'}})
>>> doc["schema"][0]["properties"][0]
{'name': 'transaction_id', 'primaryKey': True, 'required': True, 'unique': True,
 'quality': [{'id': 'not_null_fx_transactions_eur_transaction_id', 'metric': 'nullValues',
 'mustBe': 0}, {'id': 'unique_fx_transactions_eur_transaction_id', 'metric': 'duplicateValues',
 'mustBe': 0}]}
>>> validate.validate_dbt_contract(doc)
[]

```

The [contract hash](glossary.md#contract-hash) hashes the [stored form](glossary.md#stored-form):
the first hash below is the one `--json` prints under `contracts`.
[`run_facts`](glossary.md#run-facts) reads back what continuo runs the node with, the same from
the contract and its stored form. The failing run's issue points at item 3 of `customProperties`.

[`reads`](glossary.md#reads) is the list of tables a node reads: those declared in
`meta.continuo.reads` plus those its compiled SQL names. `generate` writes the declared ones and
`normalise_dbt` takes the others. continuo's release passes them, but the lite check cannot and
passes `reads=[]`. So the two hashes differ when the SQL reads a table `reads` does not declare.

```python
>>> stored = contract.normalise_dbt(doc, reads=[])
>>> contract.contract_hash(stored)
'sha256:273a56d5faf4d115663ab4b93ca976e2c132c3f49341b31189686c306ad3d6ca'
>>> contract.contract_hash(contract.normalise_dbt(doc, reads=["analytics.fx_rates"]))
'sha256:5b59551ffd0e397d857087850c89c0e66c22a25416d9e6279da96254650fcdf5'
>>> facts = dbt.run_facts(stored)
>>> facts.schedule, facts.criticality, facts.materialized, facts == dbt.run_facts(doc)
('daily', 'CORE', 'table', True)
>>> config = {"materialized": "table", "meta": {"owner": "finance", "criticality": "HIGH"}}
>>> high = dbt.generate(node | {"config": config}, tests, manifest={}).document
>>> [issue] = validate.validate_dbt_contract(high)
>>> issue.path, issue.reason, issue.message
('customProperties[3].value', 'value',
 "`criticality` is 'HIGH', it must be one of REGULATORY, CORE, SECONDARY")
>>> high["customProperties"][3]
{'vendor': 'continuo', 'property': 'criticality', 'value': 'HIGH'}

```
