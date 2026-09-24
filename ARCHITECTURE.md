# Architecture

## Overview

`mimir-gateway-vm` is a Traefik-based machine charm that fronts `mimir-vm`.
The charm owns exactly one workload: a Traefik service managed through a
systemd unit. It publishes shared remote-write and Grafana datasource entry
points and load-balances requests toward the Mimir backend pool.

## Workload Boundary

The charm manages one directly downloaded Traefik binary and its rendered
configuration. Backend Mimir nodes are modeled through a Juju integration
rather than free-form host config.

## Runtime

`src/charm.py` owns Juju orchestration and relation handling. Workload
installation, config-file writes, and service lifecycle are isolated in
Juju-independent helper modules so they can be unit tested directly.

`src/rule_bridge.py` owns alert-source caches and downstream publication. The
reference-owned `alert_rule_transport` adapter negotiates JSON/LZMA encoding;
the separate `source_admission` helper preserves accepted source state within
the decoded-byte budget. Both are exact vendored copies pinned by commit and
hash in `dependencies/shared.json`. Canonical `cosl` supplies compression.
Neither helper owns Traefik lifecycle or backend reconciliation.

## Integrations

- `backend`: supplies one or more Mimir backend URLs
- `receive-remote-write`: receives write consumers and publishes the shared ingress URL
- `grafana-source`: publishes the shared gateway query endpoint

## Configuration Flow

The operator may set `external-url`, `traefik-port`, and `log-level`. The charm
renders static Traefik configuration plus relation-scoped dynamic route files.
Each route file publishes the same shared `/api/v1/push` and `/prometheus`
paths, but relation-scoped files keep ownership and pruning simple as consumer
relations are added or removed. Dynamic route files rely on Traefik's file
provider watch support for in-place reloads, so consumer relation churn does
not require restarting the Traefik service. Only static config or systemd unit
changes trigger a service restart.

## Upgrade And Recovery

The workload distribution class is direct artifact download. Charm upgrades may
update orchestration logic independently of the installed Traefik version.
Workload upgrade and recovery behavior will be implemented through explicit
download, render, and restart flows.


Follower rule readiness uses the leader-owned peer cache. A follower waits when
its current source candidate differs from the committed cache, and considers a
matching cache ready without attempting a write. Downstream publication remains
the leader's responsibility: Ops does not allow followers to read their own
application databag on non-peer relations. Follower readiness therefore does not
independently prove delivery to the ruler.

### Publication during source events

Source events advertise rule encodings only on the active source relation, while
rule admission still reads every source and reconciles the full accepted snapshot.
Endpoint publication can target an existing source when its published unit URL
already matches the current frontend. A missing or changed URL uses global
publication, as do configuration, backend/ingress and upgrade recovery paths.
Leadership still republishes capabilities and accepted rules globally. This
conservative fallback avoids adding frontend state; newly joined sources may
still require a full endpoint publication pass. Followers publish only their own
unit endpoints; application data remains leader-owned.
