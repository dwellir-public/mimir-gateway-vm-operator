# Mimir Gateway VM Operator

`mimir-gateway-vm` is a Traefik-based machine charm that fronts `mimir-vm`
as a stable shared ingress and load balancer. It exposes one shared
Prometheus remote-write entry point and one shared Grafana datasource/query
endpoint for a single-tenant Mimir deployment.

## Operating Model

This charm is not a tenant router.

The supported architecture is:

- one shared single-tenant Mimir deployment
- one shared write URL: `/api/v1/push`
- one shared query URL: `/prometheus`
- label-based partitioning inside Mimir rather than per-tenant routing

`mimir-gateway-vm` keeps a stable HTTP ingress in front of one or more Mimir
backends and load-balances requests across those backend URLs. It does not
derive tenant ids, inject `X-Scope-OrgID`, or publish tenant-specific paths.

For operational inspection, `show-gateway-routes` reports:

- `remote-app`
- `relation-id`
- `route-name`
- `route-file`
- `backend-urls`
- `write-url`
- `query-url`

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
juju run mimir-gateway-vm/leader show-gateway-routes
```
