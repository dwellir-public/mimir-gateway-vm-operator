# Mimir Gateway VM Operator

`mimir-gateway-vm` is a Traefik-based machine charm that fronts `mimir-vm`
with relation-scoped multitenancy. It exposes a Prometheus remote-write entry
point and a Grafana datasource/query endpoint while injecting
`X-Scope-OrgID` per consumer relation.

## Tenant Routing Contract

Tenant identity is chosen in this order:

- explicit `tenant-id` from the related application databag
- derived `<app-name>-<short-model-uuid>` when a remote model UUID is available
- derived `<app-name>` for same-model relations

Examples:

- same-model relation: `alloy-vm`
- cross-model relation: `alloy-vm-f794060e`

The gateway publishes a tenant-specific write URL on the existing
`prometheus_remote_write` relation:

- write URL: `/tenants/<tenant-id>/api/v1/push`

The tenant-specific query URL:

- query URL: `/tenants/<tenant-id>/prometheus`

is reported by the `show-relation-tenants` action and is published on the
`grafana-source` relation only when exactly one tenant is currently served.

For operational inspection, `show-relation-tenants` also reports:

- `tenant-source`: `explicit` or `derived`
- `remote-app`
- `remote-model-uuid`
- `relation-id`
- `route-name`
- `route-file`

Each tenant gets its own Traefik route and the gateway strips the
`/tenants/<tenant-id>` prefix before forwarding the request upstream.

## Integrations

- `backend` (required): `mimir_gateway_backend`
- `receive-remote-write` (provided): `prometheus_remote_write`
- `grafana-source` (provided): `grafana_datasource`

## Configuration

The charm metadata currently declares these options:

- `external-url`
- `traefik-port`
- `log-level`

Current implementation note: the gateway still derives its published base URL
from the backend binding address and currently renders Traefik with fixed
listener `:80` and log level `INFO`.

## Development

- [Architecture](ARCHITECTURE.md)
- [Developer workflow](DEVELOPING.md)
- [Contribution guide](CONTRIBUTING.md)

## Local validation model

Primary test model:

- `localhost-localhost:admin/charmhub-stack-r2-20260317-193315`

Typical validation:

```bash
charmcraft pack
juju deploy ./mimir-gateway-vm_amd64.charm mimir-gateway-vm
juju integrate mimir-gateway-vm:backend mimir-vm:<backend-endpoint>
juju integrate alloy-vm:send-remote-write mimir-gateway-vm:receive-remote-write
juju run mimir-gateway-vm/leader show-relation-tenants
```
