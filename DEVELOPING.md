# Developing

## Local workflow

```bash
uv sync --group dev
tox -e format
tox -e lint
tox -e unit
tox -e integration
charmcraft pack
```

## Fast iteration

```bash
uv run pytest tests/unit -v
uv run pytest tests/integration -v -s
uv run ruff check src tests
uv run pyright
```

## Integration test artifacts

The integration test deploys local charm artifacts for:

- `mimir-gateway-vm`
- `mimir-vm`
- `alloy-vm`

By default it looks for:

- `./mimir-gateway-vm_amd64.charm`
- `../mimir-vm-operator/mimir-vm_amd64.charm`
- `../alloy-vm-operator/alloy-vm_ubuntu@24.04-amd64.charm`

Override those paths with `CHARM_PATH`, `MIMIR_CHARM_PATH`, or `ALLOY_CHARM_PATH`.

## Reference repos

- `/home/erik/Loki-project/loki-loadbalancer-vm-operator`
- `/home/erik/Loki-project/mimir-vm-operator`

## Primary validation model

- `localhost-localhost:admin/charmhub-stack-r2-20260317-193315`

## Resume after reboot

```bash
cd /home/erik/Loki-project/mimir-gateway-vm-operator
git status --short
juju status -m localhost-localhost:charmhub-stack-r2-20260317-193315 mimir-vm mimir-gateway-vm
```

If the repo does not exist yet:

```bash
cd /home/erik/Loki-project
git clone git@github.com:dwellir-public/mimir-gateway-vm-operator.git
```


## Dependency ownership and BOMs

`pyproject.toml` and `uv.lock` define Python dependencies. `dependencies/upstreams.json`
records upstream ownership; `dependencies/vendored.json` pins the exact source,
commit, hash, license and LIB metadata of each shipped library. Locally modified
libraries identify their downstream source and upstream owner explicitly. An
upstream catalog entry alone does not install a dependency.

Review dependency updates together with their locks, source pins and compatibility
tests. Do not replace a locally patched library without reviewing its documented
changes. Run these checks from this repository:

```bash
uv lock --check
uv run tox -e provenance
python3 tools/dependency_bom.py --verify-upstream
python3 tools/dependency_bom.py --output build/development.cdx.json
# Set CHARM_PATH and CHARM_BASE to the artifact and base actually built.
python3 tools/dependency_bom.py --artifact "$CHARM_PATH" --base "$CHARM_BASE" \
  --arch amd64 --output build/runtime.cdx.json
```

The development CycloneDX BOM describes the locked development/test dependency
graph. The runtime BOM checks installed distribution versions and vendored bytes
against the built archive and records its checksum, source revision and input
hashes. These BOMs do not cover OS packages, downloaded workload binaries or
transitive build-tool environments. CI verifies provenance and keeps generated
BOMs as artifacts rather than source files.

Rule compression uses Canonical's public `cosl` API. Rule acceptance, retention
and size policy belong to this charm; no shared Dwellir transport package or
cross-repository source synchronization is required.
