# Check your dbt project

How do I run the lite check in CI and fix what it reports? The [lite check](glossary.md#lite-check),
`continuo-contracts dbt check`, runs `dbt parse` and builds each [node](glossary.md#node)'s
[contract](glossary.md#contract). It prints what [continuo](glossary.md#continuo)'s
[release](glossary.md#release) step would reject, with no warehouse. Passing it does not guarantee
that the release accepts the project, because the lite check cannot see what only the release does:

- the project compiled with the release's own dbt and continuo-contracts versions;
- the tables each node's compiled SQL names, which the release adds to [`reads`](glossary.md#reads);
- the checks across the contracts of every service, such as for duplicate relations;
- `servers` and absolute links, which only the files the release publishes hold.

## Run it

It needs Python 3.14 or later and runs dbt 1.5 or later, installed with your adapter in their own
environment. Run `dbt deps` first if the project has a `packages.yml`. `PROJECT_DIR` is the project
folder, where it reads the [seed](glossary.md#dbt-words) CSV files, also with `--manifest`.

| Option | Meaning | Default |
|---|---|---|
| `--dbt PATH` | the dbt executable, a path or a name on `PATH` | `dbt` |
| `--profiles-dir DIR` | the folder of [`profiles.yml`](glossary.md#dbt-words) | `PROJECT_DIR` |
| `--target NAME` | the profile's target, passed to `dbt parse` | the profile's |
| `--manifest FILE` | check this [manifest](glossary.md#manifest), run no dbt | none |
| `--service SERVICE` | the [declared service](glossary.md#declared-service) | see below |

Always pass `--service`: your [service](glossary.md#service), the `name:` in `dbt_project.yml` with
`_` replaced by `-`. A [kept node](glossary.md#kept-node) of another service is an error, as in the
release. The default is `PROJECT_DIR`'s folder name changed the same way (for `.`, the current one).

## A CI job

`dbt parse` reads `profiles.yml` but never connects, so a profile of dummy values is enough. With
no profile in the repository, commit one as `ci/profiles.yml`, named as the `profile:` of
`dbt_project.yml`. Keep your real schema: each contract id starts with it.

```yaml
shop:
  target: ci
  outputs:
    ci: {type: postgres, host: localhost, port: 5432, user: ci, password: ci,
         dbname: shop, schema: analytics}
```

In a real profile, an `env_var('NAME')` without a default in `profiles.yml` gets
`continuo-lite-check` when `NAME` is unset. Give a numeric default to one read with `| as_number`.

```yaml
on: pull_request
jobs:
  lite-check:
    runs-on: ubuntu-latest
    env:
      DBT_CORE_VERSION: <your dbt-core version>
      DBT_ADAPTER_VERSION: <your adapter's version>
      CONTINUO_CONTRACTS_VERSION: <the version continuo's release uses>
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v7
      - run: |  # dbt in its own environment, then the packages of packages.yml
          uv venv --python 3.12 "$RUNNER_TEMP/dbt"
          uv pip install --python "$RUNNER_TEMP/dbt" dbt-core==${{ env.DBT_CORE_VERSION }} \
            dbt-postgres==${{ env.DBT_ADAPTER_VERSION }}
          if [ -f packages.yml ]; then "$RUNNER_TEMP/dbt/bin/dbt" deps; fi
      - run: >
          uvx --python 3.14 --from "continuo-contracts==${{ env.CONTINUO_CONTRACTS_VERSION }}"
          continuo-contracts dbt check . --dbt "$RUNNER_TEMP/dbt/bin/dbt" --profiles-dir ci
          --service shop
```

Replace the versions, `dbt-postgres` and `shop` with yours. Ask whoever runs your continuo which
continuo-contracts version its release uses ([stability](stability.md)). Any non-zero exit fails
the step, and a pipe into `grep` would hide it.

## Output and exit codes

Here `seeds/countries.csv` starts with `code,label` but `schema.yml` declares `code` and `name`. The
first `...` is the banner; the last is the list of what only the release does, then the banner
again.

```bash
$ continuo-contracts dbt check . --dbt "$RUNNER_TEMP/dbt/bin/dbt" --profiles-dir ci --service shop
...
ERROR seed.shop.countries: label is in the CSV header but not declared
ERROR seed.shop.countries: name is declared but not in the CSV header
...
```

A [passing run](walkthrough.md) prints the banners, the list, and an INFO line for each skipped
node, dormant node and installed package's seed. INFO lines never fail the job, under any option.

| Exit | Meaning |
|---|---|
| 0 | no ERROR line |
| 1 | an ERROR line, or `dbt parse exited with status <N>` and dbt's last 20 lines, no report |
| 2 | the lite check could not run: a usage error, no project folder or dbt, a manifest it cannot read, or `the manifest is not the shape of a dbt manifest: ...` |

Logs go to stderr. `--json` prints one document on stdout (`--help` shows its shape). Its
`contracts` maps the id of each valid contract no other node shares to its
[contract hash](glossary.md#contract-hash). The lite check compares no hash: the hashes are for
your own use, and the [field reference](fingerprints.md#dbt-model) says which edits move one.

`reads` is the list of tables a node reads: those declared in `meta.continuo.reads` plus those its
compiled SQL names. The library writes the declared ones and the release adds the others, which the
lite check cannot. So the release's hash differs from the lite check's when the SQL reads a table
`reads` does not declare.

## Messages

| Message | Level | Fix |
|---|---|---|
| `<column> is in the CSV header but not declared` | ERROR | Declare it in [`schema.yml`](glossary.md#dbt-words) or drop it from the CSV. A seed that declares no column gets this for every CSV column. |
| `<column> is declared but not in the CSV header` | ERROR | Add it to the CSV header or drop it from `schema.yml`. Names compare exactly: `ID` is not `id`. |
| `the CSV file cannot be read, so its header is not checked against the declared columns` | ERROR | Missing, outside the seed folders, not UTF-8, a first row over 1 MiB, a field over `csv`'s limit, or a delimiter `csv` refuses. |
| `the node's service is <service>, the declared service is <declared>: the release rejects a manifest that holds a node of another service as invalid_artifact` | ERROR | Pass the right `--service`. For a tagged model of an installed package, untag or disable it. |
| `the node has no service, the declared service is <declared>: the release rejects the manifest as invalid_artifact` | ERROR | The node's [`fqn`](glossary.md#dbt-words) is missing, empty, or starts with an empty name: check a manifest that `dbt parse` wrote. |
| `contract id <id> is also the id of <node ids>: the release rejects it as duplicate_table` | ERROR | Two nodes have one `<schema>.<table>`, such as names differing only in case. Rename one or change its schema: the alias is not in the id. |
| `no owner, continuo skips it` | INFO | Set `owner` in the node's [`meta`](glossary.md#dbt-words) to give it a contract. A skipped seed's CSV header is not checked. |
| `local stub, continuo skips it`, `from an installed package and has no tag, continuo skips it`, `a seed of an installed package: its CSV header is not checked` | INFO | Nothing to fix. The tag `local_stub` marks a node that stands in for a table another service owns. |
| `no tag: a dormant node, it runs only by hand` | INFO | Add a tag: the first tag is its [schedule](glossary.md#schedule). A seed with no tag is not dormant: it runs on the `seed` schedule. |
| `<path>: <message>` | ERROR | A contract issue, such as ``customProperties[3].value: `criticality` is 'HIGH', ...``: see the [reasons](validation.md#reasons). |
| `<field> must be <what>, got <type>` | ERROR | The library cannot read that field of the node, such as `config.meta.owner`. Fix it in `schema.yml` or the config. |
| `reads entry '<entry>': a table is written <schema>.<table>, ...` | ERROR | An entry of `meta.continuo.reads` is not a table: write `<schema>.<table>`. |
| `every node of the service is skipped: the release rejects it as invalid_artifact (manifest contains no model/seed nodes)` | ERROR | Every model, seed and [snapshot](glossary.md#dbt-words) is skipped, or none exists. The parenthesis counts kept nodes, snapshots included. Give one an owner. |
