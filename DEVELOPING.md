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

The unit tox environment installs the runtime and unit dependency group from
`uv.lock`, so rule-compression checks use the same library versions as the built
charm. Update dependency declarations and the lock together when testing newer
libraries.

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

## Retained-model lifecycle regression

The lifecycle test refreshes an existing gateway with a built local artifact.
It requires an explicitly authorized nonproduction model, its UUID, and the
opt-in below. It preserves existing applications and integrations, and leaves
the gateway running the tested artifact. It creates a workload-free source on
two existing machines and a temporary consumer model on the same controller.
Cleanup removes only those test resources; it never destroys the retained model.

Build both artifacts, then supply the target identity from `juju show-model`:

```bash
charmcraft pack --use-lxd
charmcraft pack --project-dir tests/integration/charms/rule-source --use-lxd

TEST_MODEL=<controller>:<owner>/<test-model> \
TEST_MODEL_UUID=<model-uuid> \
ALLOW_RETAINED_MODEL_TESTS=1 \
CHARM_PATH=./mimir-gateway-vm_amd64.charm \
RULE_SOURCE_CHARM_PATH=./tests/integration/charms/rule-source/gateway-lifecycle-source_amd64.charm \
uv run pytest tests/integration/test_relation_lifecycle.py -v -s
```

Set `GATEWAY_APP` if the application has a different name; its existing offer
must use the same name. The gateway needs a backend, an alert-rule destination,
an existing consumer, and an accessible Mimir ruler API. The test verifies rules
through that API, removes one unit from a two-unit consumer, and removes and
recreates a cross-model consumer while another source updates. It also checks
that the Traefik process and start timestamp survive refresh and relation churn.
Other integration tests retain their existing temporary-model workflow.

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
