# Architecture

## Overview

`mimir-gateway-vm` is a Traefik-based machine charm that fronts `mimir-vm`.
The charm owns exactly one workload: a Traefik service managed through a
systemd unit. It publishes tenant-scoped remote-write and Grafana datasource
entry points and injects `X-Scope-OrgID` toward the backend.

## Workload Boundary

The charm manages one directly downloaded Traefik binary and its rendered
configuration. Backend Mimir nodes are modeled through a Juju integration
rather than free-form host config.

## Runtime

`src/charm.py` owns Juju orchestration and relation handling. Workload
installation, config-file writes, and service lifecycle are isolated in
Juju-independent helper modules so they can be unit tested directly.

## Integrations

- `backend`: supplies one or more Mimir backend URLs
- `receive-remote-write`: receives per-relation write consumers
- `grafana-source`: publishes the gateway query endpoint

## Configuration Flow

The operator may set `external-url`, `traefik-port`, and `log-level`. The charm
renders static Traefik configuration plus relation-scoped dynamic route files.

## Upgrade And Recovery

The workload distribution class is direct artifact download. Charm upgrades may
update orchestration logic independently of the installed Traefik version.
Workload upgrade and recovery behavior will be implemented through explicit
download, render, and restart flows.
