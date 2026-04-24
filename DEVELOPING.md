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
