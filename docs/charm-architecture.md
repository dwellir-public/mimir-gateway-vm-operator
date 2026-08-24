# Mimir Gateway VM Architecture

Canonical workload architecture documentation lives in `ARCHITECTURE.md`.
This page records the alert-rule bridge added around that unchanged data plane.

## Independent planes

`backend` (`mimir_gateway_backend`) supplies Traefik targets. The gateway keeps
the fixed `/api/v1/push` write route and `/prometheus` datasource root and
publishes them through `receive-remote-write` and `grafana-source`.

The Grafana datasource application payload declares `manageAlerts: true` and
`prometheusType: Mimir` in its standard `extra_fields`. Grafana therefore
discovers the backend's data source-managed alert rules without copying them
into Grafana's alert-rule database. The gateway routes `/prometheus/api/v1`
for query and evaluated-rule traffic and routes only `GET` requests under
`/prometheus/config/v1/rules`. Configuration `POST` and `DELETE` calls are
left unmatched; the backing `mimir-vm` charm remains the sole namespace writer
through its direct backend-local Ruler API.

Alert rules use a separate standard `prometheus_remote_write` consumer relation
named `mimir-alert-rules`. Rules received from Alloy on the provided
`receive-remote-write` relation are merged and republished there. Automatic
forwarding in the standard consumer is disabled; the bridge remains the single
writer and does not alter rule expressions, names, or labels.

## Desired state and resilience

Upstreams are deterministically ordered by relation ID and group name. Their
application databags are complete desired state, so empty input and relation
removal withdraw ownership. Malformed first input is skipped; later malformed
input retains only that relation's LKG while unrelated valid changes proceed.

A compressed peer application databag stores bounded per-relation snapshots
and the last rendered aggregate for leader failover and upgrade replay. Limits
cover 32 source relations, relation value size, decoded cache size, tree depth,
node count, and group-name bytes. Aggregate overflow keeps the prior accepted
snapshot, including when a new destination appears.

Standard `alert_rules` values must be strictly below 60 KiB and are rejected at
or above `60 * 1024` bytes. This is intentionally different from the encoded
machine payload boundary, which admits exactly `60 * 1024` bytes.

Only the leader writes application data. Relation-created, changed, departed,
and broken events converge both normal and cross-model relations. Accepted
non-empty rules without a destination add a narrow Waiting status, but do not
override an existing non-Active gateway status or interrupt telemetry routing.

The shared listener has no charm-provided authentication because Mimir is
operated with authentication disabled inside a trusted Juju model network.
Deployments must segment that listener from untrusted clients. Read-only public
ruler routing reduces the write surface but is not an authentication mechanism.
