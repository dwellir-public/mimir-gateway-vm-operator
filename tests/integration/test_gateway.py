#!/usr/bin/env python3
# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.

"""Integration coverage for the Mimir gateway VM charm."""

from __future__ import annotations

import json
import pathlib

import jubilant


def _gateway_is_waiting_for_backend(status: jubilant.Status) -> bool:
    """Report whether the gateway unit is waiting specifically on backend relation data."""
    units = status.get_units("mimir-gateway-vm")
    if not units:
        return False
    unit = next(iter(units.values()))
    return (
        unit.workload_status.current == "waiting"
        and unit.workload_status.message == "waiting for backend relation data"
    )


def test_gateway_action_reports_shared_mapping(
    charm: pathlib.Path,
    mimir_charm: pathlib.Path,
    alloy_charm: pathlib.Path,
    juju: jubilant.Juju,
):
    """Deploy the gateway and assert the mapping action reports shared URLs."""
    juju.deploy(mimir_charm.resolve(), app="mimir-vm")
    juju.deploy(charm.resolve(), app="mimir-gateway-vm")
    juju.wait(lambda status: _gateway_is_waiting_for_backend(status), timeout=1200)

    juju.integrate("mimir-gateway-vm:backend", "mimir-vm:backend")
    juju.wait(
        lambda status: jubilant.all_active(status, "mimir-vm", "mimir-gateway-vm"),
        timeout=1200,
    )

    juju.deploy(alloy_charm.resolve(), app="alloy-vm")
    juju.wait(lambda status: jubilant.all_active(status, "alloy-vm"), timeout=1200)
    juju.integrate("alloy-vm:send-remote-write", "mimir-gateway-vm:receive-remote-write")
    juju.wait(
        lambda status: jubilant.all_active(status, "mimir-vm", "mimir-gateway-vm", "alloy-vm"),
        timeout=1200,
    )

    result = juju.run(
        "mimir-gateway-vm/0",
        "show-gateway-routes",
        wait=300,
    )
    result.raise_on_failure()

    mappings = json.loads(result.results["mappings"])
    assert len(mappings) == 1
    assert mappings[0]["remote-app"] == "alloy-vm"
    assert mappings[0]["backend-urls"] == ["http://10.0.0.10:9009"]
    assert mappings[0]["route-file"] == f"relation-{mappings[0]['relation-id']}.yml"
    assert mappings[0]["route-name"] == f"relation-{mappings[0]['relation-id']}"
    assert mappings[0]["write-url"].endswith("/api/v1/push")
    assert mappings[0]["query-url"].endswith("/prometheus")
