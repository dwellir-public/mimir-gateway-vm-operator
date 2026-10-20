"""Exercise teardown against an explicitly authorized retained nonproduction model."""

import hashlib
import json
import os
import pathlib
import re
import urllib.error
import urllib.request
import uuid
import zipfile

import jubilant
import pytest


def _runtime(juju, unit):
    """Record the workload identity so a restart cannot hide behind active status."""
    return juju.ssh(
        unit,
        "sudo systemctl show traefik.service",
        "--property=MainPID,ExecMainStartTimestamp,NRestarts,ActiveState",
    )


def _rule_names(url):
    """Read evaluated groups from the real Mimir ruler through the gateway."""
    with urllib.request.urlopen(f"{url}/api/v1/rules", timeout=5) as response:
        document = json.load(response)
    assert document["status"] == "success"
    return {group["name"] for group in document["data"]["groups"]}


def _wait_rules(juju, gateway, url, expected, absent=()):
    """Wait for ruler convergence while failing immediately on a gateway hook error."""

    def ready(status):
        if not jubilant.all_active(status, gateway):
            return False
        try:
            names = _rule_names(url)
        except (urllib.error.URLError, TimeoutError):
            return False
        return set(expected) <= names and not set(absent) & names

    juju.wait(
        ready,
        error=lambda status: jubilant.any_error(status, gateway),
        delay=2,
        timeout=180,
        successes=3,
    )


def test_retained_gateway_cross_model_teardown(charm: pathlib.Path):
    """Keep existing traffic/rules while removing and recreating a cross-model consumer."""
    model = os.environ.get("TEST_MODEL", "")
    model_uuid = os.environ.get("TEST_MODEL_UUID", "")
    fixture_path = pathlib.Path(os.environ.get("RULE_SOURCE_CHARM_PATH", "missing.charm"))
    if os.environ.get("ALLOW_RETAINED_MODEL_TESTS") != "1":
        pytest.skip("Set ALLOW_RETAINED_MODEL_TESTS=1 for an authorized nonproduction model")
    if ":" not in model or "/" not in model or not model_uuid or not fixture_path.is_file():
        pytest.fail(
            "Set controller-qualified TEST_MODEL, TEST_MODEL_UUID and RULE_SOURCE_CHARM_PATH"
        )
    gateway = os.environ.get("GATEWAY_APP", "mimir-gateway-vm")
    if not re.fullmatch(r"[a-z][a-z0-9-]*", gateway):
        pytest.fail("GATEWAY_APP must be a Juju application name")
    juju = jubilant.Juju(model=model)
    identity = json.loads(juju.cli("show-model", model, "--format=json", include_model=False))
    assert len(identity) == 1
    assert next(iter(identity.values()))["model-uuid"] == model_uuid
    controller, qualified_model = model.split(":", 1)
    owner, model_name = qualified_model.split("/", 1)
    status = juju.status()
    assert jubilant.all_active(status, gateway)
    existing_apps = set(status.apps)
    unit = next(iter(status.apps[gateway].units))
    machine = status.apps[gateway].units[unit].machine
    other_machine = next((key for key in status.machines if key != machine), None)
    assert other_machine is not None, "A second existing machine is needed for scale-down coverage"
    runtime = _runtime(juju, unit)
    assert "ActiveState=active" in runtime
    mappings = json.loads(juju.run(unit, "show-gateway-routes").results["mappings"])
    assert mappings, "Keep at least one existing consumer to verify its rules survive"
    existing_ids = {mapping["relation-id"] for mapping in mappings}
    url = mappings[0]["query-url"]
    existing_rules = _rule_names(url)
    with zipfile.ZipFile(charm) as archive:
        expected_digest = hashlib.sha256(archive.read("src/charm.py")).hexdigest()
    juju.refresh(gateway, path=charm.resolve())

    def upgraded(current):
        if not jubilant.all_active(current, gateway) or not jubilant.all_agents_idle(
            current, gateway
        ):
            return False
        deployed = juju.ssh(
            unit,
            "sudo sha256sum",
            f"/var/lib/juju/agents/unit-{unit.replace('/', '-')}/charm/src/charm.py",
        )
        return deployed.split()[0] == expected_digest

    juju.wait(upgraded, error=lambda state: jubilant.any_error(state, gateway), timeout=600)
    assert _runtime(juju, unit) == runtime, "Charm refresh restarted Traefik"
    suffix = uuid.uuid4().hex[:8]
    healthy = f"lifecycle-healthy-{suffix}"
    remote = f"lifecycle-remote-{suffix}"
    created = False
    try:
        juju.deploy(
            fixture_path.resolve(),
            app=healthy,
            to=machine,
            config={"rule-name": healthy},
            base="ubuntu@24.04",
        )
        created = True
        juju.integrate(f"{healthy}:send-remote-write", f"{gateway}:receive-remote-write")
        juju.wait(lambda state: jubilant.all_active(state, healthy, gateway), timeout=600)
        _wait_rules(juju, gateway, url, existing_rules | {healthy})
        juju.add_unit(healthy, to=other_machine)
        juju.wait(
            lambda state: (
                len(state.apps[healthy].units) == 2
                and jubilant.all_active(state, healthy, gateway)
            ),
            timeout=600,
        )
        departing = max(juju.status().apps[healthy].units)
        juju.remove_unit(departing)
        juju.wait(
            lambda state: (
                len(state.apps[healthy].units) == 1
                and jubilant.all_active(state, healthy, gateway)
            ),
            timeout=180,
        )
        _wait_rules(juju, gateway, url, existing_rules | {healthy})

        # A fresh client model reproduces the CMR removal path from the incident.
        with jubilant.temp_model(
            controller=controller, config={"update-status-hook-interval": "1m"}
        ) as client:
            print(f"Temporary consumer model: {client.model}")
            client.deploy(
                fixture_path.resolve(),
                app=remote,
                config={"rule-name": remote},
                base="ubuntu@24.04",
                constraints={"mem": "512M", "cores": "1"},
            )
            client.wait(lambda state: jubilant.all_active(state, remote), timeout=900)
            for generation in range(2):
                client.consume(
                    f"{model_name}.{gateway}", alias="gateway", controller=controller, owner=owner
                )
                client.integrate(f"{remote}:send-remote-write", "gateway:receive-remote-write")
                _wait_rules(juju, gateway, url, existing_rules | {healthy, remote})
                client.cli("remove-saas", "gateway")
                # Queue another consumer change while the gateway processes removal.
                juju.config(healthy, {"generation": generation + 1})
                _wait_rules(juju, gateway, url, existing_rules | {healthy}, absent={remote})
                print(f"Cross-model remove/reconnect cycle {generation + 1}: passed")
        current = json.loads(juju.run(unit, "show-gateway-routes").results["mappings"])
        assert existing_ids <= {mapping["relation-id"] for mapping in current}
        assert _runtime(juju, unit) == runtime, "Relation lifecycle restarted Traefik"
    finally:
        if created:
            juju.remove_application(healthy)
            juju.wait(lambda state: healthy not in state.apps, timeout=300)
    _wait_rules(juju, gateway, url, existing_rules, absent={healthy, remote})
    assert set(juju.status().apps) == existing_apps
    assert _runtime(juju, unit) == runtime
