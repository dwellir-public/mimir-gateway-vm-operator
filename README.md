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
- one shared query URL rooted at `/prometheus`
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
- `mimir-alert-rules` (required): `prometheus_remote_write`
- `grafana-source` (provided): `grafana_datasource`

`backend` is the Traefik data plane and supplies backend URLs. The gateway
publishes fixed `/api/v1/push` and `/prometheus` frontend paths on
`receive-remote-write` and `grafana-source` respectively.

On `grafana-source`, the gateway identifies the Prometheus datasource as
Mimir and enables data source-managed alert discovery. Grafana can therefore
list the charm-owned Mimir rules and their evaluated state under Alerting.
Query and evaluated-rule endpoints under `/prometheus/api/v1` retain their
standard methods. The ruler configuration endpoint under
`/prometheus/config/v1/rules` is exposed only for `GET`; `POST` and `DELETE`
are intentionally unmatched because `mimir-vm` is the sole writer for the
charm-owned ruler namespace.

Prometheus alert rules arriving from Alloy in `receive-remote-write`
application data are bridged independently to `mimir-alert-rules`; the
standard consumer is instantiated with automatic forwarding disabled so the
gateway owns the deterministic merge.

Direct-to-Mimir relations for either Alloy variant are:

```bash
juju relate alloy-vm:send-remote-write mimir-vm:receive-remote-write
juju relate alloy-sub:send-remote-write mimir-vm:receive-remote-write
```

With the gateway, choose the deployed Alloy variant and add both the rule and
data-plane relations:

```bash
juju relate alloy-vm:send-remote-write mimir-gateway-vm:receive-remote-write
juju relate alloy-sub:send-remote-write mimir-gateway-vm:receive-remote-write
juju relate mimir-gateway-vm:mimir-alert-rules mimir-vm:receive-remote-write
juju relate mimir-gateway-vm:backend mimir-vm:backend
```

The bridge preserves expressions, names, labels, and unchanged groups. It
orders multiple upstreams by relation ID and group name and publishes compact
complete desired state. Empty upstream state withdraws rules.

Malformed input is isolated per upstream relation. A bounded leader-shared
cache retains that relation's last valid snapshot, if present, and the last
accepted aggregate across leadership changes. The bridge admits at most 32
upstream relations and bounds JSON depth, nodes, group-name size, encoded Juju
values, and decoded cache size. If a new merge cannot fit, it republishes the
prior accepted aggregate rather than partially applying it.

Each standard `alert_rules` relation value must remain strictly below 60 KiB;
values at or above `60 * 1024` bytes are rejected. This gateway bound differs
from the canonical encoded `machine_observability` payload, where exactly
`60 * 1024` bytes is admitted and only larger values are rejected by Alloy.

When accepted non-empty rules exist without `mimir-alert-rules`, an otherwise
Active unit reports Waiting with `waiting for Mimir alert-rule destination`.
Telemetry routing continues, and an existing more important non-Active status
is not replaced by this rule-only Waiting state. Adding or removing the
destination relation converges publication and status, including over CMR.

Mimir authentication is disabled in the current machine stack, so the gateway
listener is a trusted-model-network boundary rather than a public
authentication boundary. Restrict network access to intended telemetry and
Grafana consumers. The read-only ruler route is defense in depth and does not
replace network segmentation or Grafana user-role controls.

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
juju integrate mimir-gateway-vm:backend mimir-vm:backend
juju integrate alloy-vm:send-remote-write mimir-gateway-vm:receive-remote-write
juju integrate mimir-gateway-vm:mimir-alert-rules mimir-vm:receive-remote-write
juju run mimir-gateway-vm/leader show-gateway-routes
```

For a multi-repository v3 upgrade, refresh the reference library first, then
both Alloy variants, then the Loki and Mimir gateways, and Grafana VM last.
Wait for relation convergence after each step.


## Alert rule delivery capacity

Rule admission has no fixed source-count cutoff. At least 1,024 is a validation
target, not an admission ceiling. Empty
telemetry relations do not consume slots. Existing sources retain their slots
when a new source would exceed capacity. Malformed or temporarily missing updates
retain their last valid rules; an explicit empty group list or relation removal
withdraws them. Rejected relation IDs and delivery counts appear in unit logs;
incomplete delivery is reported in workload status.

Receivers advertise `alert_rules_encodings` and accept legacy JSON plus Canonical's
LZMA/base64 format in `alert_rules`. Senders compress only for advertising peers.
There is no machine-observability schema change. The shared bounded adapter is
owned by `dwellir-observability-reference`; publish that library before releasing
consumer builds. Its candidate source is vendored byte-for-byte for coordinated
review and local testing.

The supported test corpus contains 1,024 sources and 4,096 groups/rules, including
all five Juju topology labels, expressions, and runbook annotations. It is about
3.4 MB as JSON and 54 KB after LZMA/base64. Every encoded relation value must remain
below 60 KiB; decoded rule documents are limited to 8 MiB and LZMA decoder memory to
64 MiB. These are finite limits, not a promise that arbitrary rule volumes will
fit. Low-compressibility or oversized updates are rejected without truncation.
Backend admission also limits total groups to 8,192 and total rules to 10,000.

Upgrade receivers before gateways and collectors. A JSON-only destination that
cannot fit an aggregate retains the previous accepted publication and reports
pending delivery. New readers accept previous cache formats; older 32-source
builds cannot read all new cache states or accept the larger volume. Roll back
using a compatible build; do not delete rules or downgrade blindly to make them
fit. Notifications require separately configured Alertmanager routing.

The capacity corpus tests are distinct from live relation-scale tests. The
coordinated reference repository's `tests/retained` suite targets dedicated local
applications and deliberately leaves applications, relations, and data in place.


### Upstream reuse and resource policies

The experimental transport uses Canonical `cosl` for LZMA/base64 encoding.
Local code retains strict decompression limits, negotiation integration and
last-known-good source ownership; these are separate from the codec. Runtime
packaging remains the existing vendored arrangement while the owner repository
investigates whether a separate package is justified.

Logical-source tests cover 1,025 realistic sources and 2,048 small sources through
wire and cache recovery. A 2,048-source corpus with distinct topology values
exceeds the existing 60 KiB wire policy and is explicitly rejected. This is an
application policy, not a verified Juju limit. No count cutoff is reintroduced
by cache readers. Memory, document-tree, group/rule and encoded-size policies
still constrain usable capacity; unlimited source volume is not promised.

Sources are decoded once before semantic validation. Removing the cumulative
decode cutoff avoids permanent starvation by relation ID, but parsing work still
scales with input volume per hook. Ruler writes retain resumable operation/time
budgets. Host/controller scale and malformed-input CPU budgets require measurement
before claiming production scale readiness; logical tests are not live relations.
