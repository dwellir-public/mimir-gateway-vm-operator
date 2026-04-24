# Tenant-Specific Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `mimir-gateway-vm` multi-tenant routing by publishing tenant-specific frontend URLs on the existing `prometheus_remote_write` relation and routing each tenant on its own path prefix.

**Architecture:** Keep the single `prometheus_remote_write` relation and the explicit-or-derived tenant-id model, but stop publishing one shared URL to every consumer. Instead, publish `/tenants/<tenant-id>/...` URLs per relation, update Traefik config rendering to match and strip tenant-specific prefixes, and verify end-to-end that multiple relations remain independently addressable.

**Tech Stack:** Python `ops`, Juju relation databags, Traefik file-provider config, `pytest`, `ops.testing`, `tox`, `uv`, `charmcraft`.

---

## File Structure

- Modify: `mimir-gateway-vm-operator/src/tenancy.py`
  Purpose: keep explicit-or-derived tenant-id computation and normalization as the source of truth for route identity.
- Modify: `mimir-gateway-vm-operator/src/config_builder.py`
  Purpose: render tenant-specific Traefik path rules and strip-prefix middleware before forwarding upstream.
- Modify: `mimir-gateway-vm-operator/src/charm.py`
  Purpose: publish relation-specific write/query URLs, use tenant-specific paths in action output, and keep status/reconcile behavior aligned.
- Modify: `mimir-gateway-vm-operator/src/remote_write.py`
  Purpose: continue publishing remote-write data, now with per-relation tenant-specific URLs.
- Modify: `mimir-gateway-vm-operator/tests/unit/test_tenancy.py`
  Purpose: preserve direct tenant-id derivation coverage.
- Modify: `mimir-gateway-vm-operator/tests/unit/test_config_builder.py`
  Purpose: assert tenant-specific routing rules and prefix stripping in rendered Traefik config.
- Modify: `mimir-gateway-vm-operator/tests/unit/test_charm.py`
  Purpose: assert per-relation published URLs, distinct route configs, and durable status behavior.
- Modify: `mimir-gateway-vm-operator/tests/integration/test_gateway.py`
  Purpose: assert that the live mapping action returns tenant-specific frontend URLs rather than a shared URL.
- Modify: `mimir-gateway-vm-operator/README.md`
  Purpose: document tenant-id priority and the tenant-specific published paths.

## Task 1: Keep Tenant Identity Helper Stable

**Files:**
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/tests/unit/test_tenancy.py`
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/src/tenancy.py`

- [ ] **Step 1: Write the failing tenancy tests**

```python
def test_explicit_tenant_id_wins():
    assert effective_tenant_id(
        relation_app_data={"tenant-id": "team-a"},
        remote_app_name="alloy-vm",
        remote_model_uuid="1234abcd",
    ) == "team-a"


def test_same_model_fallback_uses_app_name():
    assert effective_tenant_id(
        relation_app_data={},
        remote_app_name="alloy-vm",
        remote_model_uuid="",
    ) == "alloy-vm"


def test_cross_model_fallback_uses_model_uuid_and_app_name():
    assert effective_tenant_id(
        relation_app_data={},
        remote_app_name="opentelemetry-collector",
        remote_model_uuid="f794060e-b2d7-43ba-81d5-1a028c1c748d",
    ) == "f794060e-b2d7-43ba-81d5-1a028c1c748d-opentelemetry-collector"
```

- [ ] **Step 2: Run the tenancy tests to verify they fail**

Run: `uv run pytest tests/unit/test_tenancy.py -v`
Expected: FAIL if the helper still uses `relation_id` or old fallback behavior.

- [ ] **Step 3: Write the minimal tenancy implementation**

```python
def effective_tenant_id(
    *,
    relation_app_data: dict[str, str],
    remote_app_name: str,
    remote_model_uuid: str,
) -> str:
    explicit = str(relation_app_data.get("tenant-id", "")).strip()
    if explicit:
        return explicit
    return fallback_tenant_id(
        remote_app_name=remote_app_name,
        remote_model_uuid=remote_model_uuid,
    )
```

- [ ] **Step 4: Run the tenancy tests to verify they pass**

Run: `uv run pytest tests/unit/test_tenancy.py -v`
Expected: PASS with explicit, same-model, and cross-model fallback coverage green.

- [ ] **Step 5: Commit**

```bash
git -C /home/erik/Loki-project/mimir-gateway-vm-operator add src/tenancy.py tests/unit/test_tenancy.py
git -C /home/erik/Loki-project/mimir-gateway-vm-operator commit -m "feat: derive gateway tenant ids from relation metadata"
```

## Task 2: Render Tenant-Specific Traefik Routes

**Files:**
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/tests/unit/test_config_builder.py`
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/src/config_builder.py`

- [ ] **Step 1: Write the failing config-builder tests**

```python
def test_render_dynamic_config_matches_tenant_specific_write_prefix():
    rendered = render_dynamic_config(
        route_name="tenant-a",
        tenant_id="tenant-a",
        backend_urls=["http://10.0.0.10:9009"],
        path_prefix="/tenants/tenant-a",
    )
    assert 'rule: "PathPrefix(`/tenants/tenant-a`)"' in rendered


def test_render_dynamic_config_strips_tenant_prefix_before_forwarding():
    rendered = render_dynamic_config(
        route_name="tenant-a",
        tenant_id="tenant-a",
        backend_urls=["http://10.0.0.10:9009"],
        path_prefix="/tenants/tenant-a",
    )
    assert "stripPrefix" in rendered
    assert '- "/tenants/tenant-a"' in rendered
```

- [ ] **Step 2: Run the config-builder tests to verify they fail**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: FAIL because `render_dynamic_config()` still hardcodes `PathPrefix(`/`)` and has no strip-prefix middleware.

- [ ] **Step 3: Write the minimal config-builder implementation**

```python
def render_dynamic_config(
    *,
    route_name: str,
    tenant_id: str,
    backend_urls: list[str],
    path_prefix: str,
) -> str:
    lines = [
        "http:",
        "  routers:",
        f"    {route_name}:",
        "      entryPoints:",
        "        - web",
        f'      rule: "PathPrefix(`{path_prefix}`)"',
        f"      service: {route_name}",
        f"      middlewares: [{route_name}-strip, {route_name}-tenant]",
        "",
        "  middlewares:",
        f"    {route_name}-strip:",
        "      stripPrefix:",
        "        prefixes:",
        f'          - "{path_prefix}"',
        f"    {route_name}-tenant:",
        "      headers:",
        "        customRequestHeaders:",
        f'          X-Scope-OrgID: "{tenant_id}"',
    ]
```

- [ ] **Step 4: Run the config-builder tests to verify they pass**

Run: `uv run pytest tests/unit/test_config_builder.py -v`
Expected: PASS with tenant-specific path matching and strip-prefix behavior verified.

- [ ] **Step 5: Commit**

```bash
git -C /home/erik/Loki-project/mimir-gateway-vm-operator add src/config_builder.py tests/unit/test_config_builder.py
git -C /home/erik/Loki-project/mimir-gateway-vm-operator commit -m "feat: render tenant-specific gateway routes"
```

## Task 3: Publish Per-Relation Tenant URLs From The Charm

**Files:**
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/tests/unit/test_charm.py`
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/src/charm.py`
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/src/remote_write.py`

- [ ] **Step 1: Write the failing charm tests**

```python
def test_remote_write_relation_publishes_tenant_specific_gateway_url(monkeypatch):
    ctx = _context()
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-vm",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._external_url_base", lambda _self: "http://10.0.0.20:80")
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    state = ctx.run(ctx.on.start(), testing.State(relations=[_backend_relation(), relation], leader=True))
    relation_out = state.get_relation(relation.id)
    assert relation_out.local_unit_data["remote_write"] == '{"url": "http://10.0.0.20:80/tenants/alloy-vm/api/v1/push"}'


def test_show_relation_tenants_action_reports_tenant_specific_urls(monkeypatch):
    ctx = _context()
    captured = {}
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-vm",
    )
    monkeypatch.setattr("charm.ops.ActionEvent.set_results", lambda _event, results: captured.update(results))
    monkeypatch.setattr("charm.MimirGatewayVmCharm._external_url_base", lambda _self: "http://10.0.0.20:80")

    ctx.run(ctx.on.action("show-relation-tenants"), testing.State(relations=[_backend_relation(), relation]))
    mappings = json.loads(captured["mappings"])
    assert mappings[0]["write-url"] == "http://10.0.0.20:80/tenants/alloy-vm/api/v1/push"
    assert mappings[0]["query-url"] == "http://10.0.0.20:80/tenants/alloy-vm/prometheus"
```

- [ ] **Step 2: Run the targeted charm tests to verify they fail**

Run: `uv run pytest tests/unit/test_charm.py::test_remote_write_relation_publishes_tenant_specific_gateway_url tests/unit/test_charm.py::test_show_relation_tenants_action_reports_tenant_specific_urls -v`
Expected: FAIL because the charm still publishes and reports one shared URL.

- [ ] **Step 3: Write the minimal charm implementation**

```python
def _tenant_path_prefix(self, tenant_id: str) -> str:
    return f"/tenants/{tenant_id}"


def _relation_write_url(self, relation: ops.Relation) -> str:
    tenant_id = self._relation_tenant_id(relation)
    return self._external_url_base() + self._tenant_path_prefix(tenant_id) + "/api/v1/push"


def _relation_query_url(self, relation: ops.Relation) -> str:
    tenant_id = self._relation_tenant_id(relation)
    return self._external_url_base() + self._tenant_path_prefix(tenant_id) + "/prometheus"
```

Use those helpers in:

```python
_publish_consumer_data
_on_show_relation_tenants_action
_render_relation_dynamic_configs
```

Update route rendering call:

```python
render_dynamic_config(
    route_name=f"relation-{relation.id}",
    tenant_id=tenant_id,
    backend_urls=backend_urls,
    path_prefix=self._tenant_path_prefix(tenant_id),
)
```

- [ ] **Step 4: Run the charm unit tests to verify they pass**

Run: `uv run pytest tests/unit/test_charm.py -v`
Expected: PASS with per-relation URL publication and action output verified.

- [ ] **Step 5: Commit**

```bash
git -C /home/erik/Loki-project/mimir-gateway-vm-operator add src/charm.py src/remote_write.py tests/unit/test_charm.py
git -C /home/erik/Loki-project/mimir-gateway-vm-operator commit -m "feat: publish tenant-specific gateway urls"
```

## Task 4: Update Integration Coverage And Operator Docs

**Files:**
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/tests/integration/test_gateway.py`
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/README.md`

- [ ] **Step 1: Write the failing integration assertion**

```python
assert mappings[0]["tenant-id"] == "alloy-vm"
assert mappings[0]["write-url"].endswith("/tenants/alloy-vm/api/v1/push")
assert mappings[0]["query-url"].endswith("/tenants/alloy-vm/prometheus")
```

- [ ] **Step 2: Run the integration test to verify it fails**

Run: `uv run pytest tests/integration/test_gateway.py -v -s`
Expected: FAIL because the current action output still returns shared frontend URLs.

- [ ] **Step 3: Update the operator docs**

```md
Tenant selection priority:

1. `tenant-id` from the related remote-write consumer application databag
2. `<model-uuid>-<app>` derived fallback when remote model UUID is available
3. `<app>` derived fallback when no model UUID is available

Published frontend paths:

- write: `/tenants/<tenant-id>/api/v1/push`
- query: `/tenants/<tenant-id>/prometheus`
```

- [ ] **Step 4: Run full repo verification**

Run:

```bash
tox -e lint
tox -e unit
uv run pytest tests/integration/test_gateway.py -v -s
```

Expected:

- `lint`: PASS
- `unit`: PASS
- `integration`: PASS and `show-relation-tenants` reports tenant-specific URLs

- [ ] **Step 5: Commit**

```bash
git -C /home/erik/Loki-project/mimir-gateway-vm-operator add README.md tests/integration/test_gateway.py
git -C /home/erik/Loki-project/mimir-gateway-vm-operator commit -m "test: cover tenant-specific gateway routing"
```

## Task 5: Refresh The Local Validation Model

**Files:**
- Modify: `/home/erik/Loki-project/mimir-gateway-vm-operator/mimir-gateway-vm_amd64.charm`

- [ ] **Step 1: Build the updated charm**

Run: `charmcraft pack`
Expected: PASS and produce `mimir-gateway-vm_amd64.charm`.

- [ ] **Step 2: Refresh the live application**

Run:

```bash
juju refresh -m localhost-localhost:admin/charmhub-stack-r2-20260317-193315 \
  mimir-gateway-vm \
  --path /home/erik/Loki-project/mimir-gateway-vm-operator/mimir-gateway-vm_amd64.charm
```

Expected: local revision increments and the unit runs hooks successfully.

- [ ] **Step 3: Verify the live tenant mapping**

Run:

```bash
juju run -m localhost-localhost:admin/charmhub-stack-r2-20260317-193315 \
  mimir-gateway-vm/leader show-relation-tenants
juju status -m localhost-localhost:admin/charmhub-stack-r2-20260317-193315 mimir-gateway-vm
```

Expected:

- `show-relation-tenants` returns tenant-specific URLs like `/tenants/<tenant-id>/api/v1/push`
- gateway status stays `active`
- multiple relations no longer share one published write URL

- [ ] **Step 4: Commit**

```bash
git -C /home/erik/Loki-project/mimir-gateway-vm-operator add docs/superpowers/plans/2026-04-24-tenant-specific-routing.md
git -C /home/erik/Loki-project/mimir-gateway-vm-operator commit -m "docs: add tenant-specific routing implementation plan"
```
