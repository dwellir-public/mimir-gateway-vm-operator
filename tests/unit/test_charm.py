import json
from dataclasses import replace
from pathlib import Path

import yaml
from ops import testing
from ops.testing import PeerRelation, Relation

from charm import MimirGatewayVmCharm
from rule_bridge import CACHE_KEY, CACHE_VALUE_LIMIT, MAX_SOURCE_RELATIONS

REPOSITORY_ROOT = Path(__file__).parents[2]

META = {
    "name": "mimir-gateway-vm",
    "requires": {
        "backend": {"interface": "mimir_gateway_backend"},
        "mimir-alert-rules": {"interface": "prometheus_remote_write"},
    },
    "peers": {"gateway-peers": {"interface": "mimir_gateway_peers"}},
    "provides": {
        "receive-remote-write": {"interface": "prometheus_remote_write"},
        "grafana-source": {"interface": "grafana_datasource"},
    },
}

ACTION_META = {
    "show-gateway-routes": {
        "description": "Show published shared frontend URLs and backend route state."
    }
}


def _context() -> testing.Context:
    return testing.Context(MimirGatewayVmCharm, meta=META, actions=ACTION_META)


def _backend_relation():
    return Relation(
        "backend",
        interface="mimir_gateway_backend",
        remote_app_name="mimir-vm",
        remote_app_data={"urls": '["http://10.0.0.10:9009"]'},
    )


def _remote_write_relation():
    return Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
    )


def _rule_destination_relation():
    return Relation(
        "mimir-alert-rules",
        interface="prometheus_remote_write",
        remote_app_name="mimir-vm",
    )


def _peer_relation():
    return PeerRelation("gateway-peers", interface="mimir_gateway_peers")


def test_actions_are_declared_only_in_unified_charmcraft_manifest():
    manifest = yaml.safe_load((REPOSITORY_ROOT / "charmcraft.yaml").read_text())

    assert not (REPOSITORY_ROOT / "actions.yaml").exists()
    assert manifest["actions"]["show-gateway-routes"]["description"].startswith(
        "Show shared frontend URLs"
    )


def test_start_renders_shared_dynamic_configs(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    written = {}
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr(
        "charm.traefik.write_dynamic_config",
        lambda filename, content: written.setdefault(filename, content) or True,
    )
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    same_model_relation = _remote_write_relation()
    second_relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="otel",
    )
    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, same_model_relation, second_relation]),
    )
    assert calls == []
    assert len(written) == 2
    assert all("PathPrefix(`/api/v1/push`)" in content for content in written.values())
    assert all("PathPrefix(`/prometheus/api/v1`)" in content for content in written.values())
    assert all(
        "PathPrefix(`/prometheus/config/v1/rules`) && Method(`GET`)" in content
        for content in written.values()
    )
    assert all("X-Scope-OrgID" not in content for content in written.values())
    assert all("/tenants/" not in content for content in written.values())
    assert state.unit_status.name == "active"


def test_remote_write_relation_publishes_shared_gateway_url(monkeypatch):
    ctx = _context()

    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    backend = _backend_relation()
    relation = _remote_write_relation()
    state = ctx.run(ctx.on.start(), testing.State(relations=[backend, relation], leader=True))
    relation_out = state.get_relation(relation.id)
    assert (
        relation_out.local_unit_data["remote_write"]
        == '{"url": "http://10.0.0.20:80/api/v1/push"}'
    )


def test_remote_write_relation_clears_legacy_gateway_metadata(monkeypatch):
    ctx = _context()

    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    backend = _backend_relation()
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        local_app_data={
            "tenant-id": "legacy-tenant",
            "application": "legacy-app",
            "model": "legacy-model",
            "model_uuid": "legacy-uuid",
        },
    )
    state = ctx.run(ctx.on.start(), testing.State(relations=[backend, relation], leader=True))
    relation_out = state.get_relation(relation.id)
    assert relation_out.local_app_data == {}
    assert (
        relation_out.local_unit_data["remote_write"]
        == '{"url": "http://10.0.0.20:80/api/v1/push"}'
    )


def test_remote_write_relations_publish_same_shared_gateway_url(monkeypatch):
    ctx = _context()

    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    backend = _backend_relation()
    relation_one = _remote_write_relation()
    relation_two = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="otel",
    )

    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, relation_one, relation_two], leader=True),
    )

    relation_one_out = state.get_relation(relation_one.id)
    relation_two_out = state.get_relation(relation_two.id)
    assert (
        relation_one_out.local_unit_data["remote_write"]
        == '{"url": "http://10.0.0.20:80/api/v1/push"}'
    )
    assert (
        relation_two_out.local_unit_data["remote_write"]
        == '{"url": "http://10.0.0.20:80/api/v1/push"}'
    )


def test_grafana_source_relation_publishes_prometheus_url(monkeypatch):
    ctx = _context()
    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    backend = _backend_relation()
    grafana = Relation(
        "grafana-source",
        interface="grafana_datasource",
        remote_app_name="grafana",
    )
    relation = _remote_write_relation()
    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, grafana, relation], leader=True),
    )
    relation_out = state.get_relation(grafana.id)
    assert relation_out.local_unit_data["grafana_source_host"] == "http://10.0.0.20:80/prometheus"
    source_data = json.loads(relation_out.local_app_data["grafana_source_data"])
    assert source_data["application"] == "mimir-gateway-vm"
    assert source_data["type"] == "prometheus"
    assert source_data["model"]
    assert source_data["model_uuid"]
    assert source_data["extra_fields"] == {
        "manageAlerts": True,
        "prometheusType": "Mimir",
    }
    assert source_data["secure_extra_fields"] is None


def test_grafana_source_relation_keeps_shared_url_when_multiple_consumers_exist(monkeypatch):
    ctx = _context()
    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    backend = _backend_relation()
    grafana = Relation(
        "grafana-source",
        interface="grafana_datasource",
        remote_app_name="grafana",
    )
    relation = _remote_write_relation()
    cross_model_relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="otel",
    )
    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, grafana, relation, cross_model_relation], leader=True),
    )
    relation_out = state.get_relation(grafana.id)
    assert relation_out.local_unit_data["grafana_source_host"] == "http://10.0.0.20:80/prometheus"


def test_configure_writes_distinct_dynamic_file_per_relation(monkeypatch, tmp_path):
    written = {}

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr(
        "charm.traefik.write_dynamic_config",
        lambda filename, content: written.setdefault(filename, content) or True,
    )
    monkeypatch.setattr("charm.traefik.start", lambda: None)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    ctx = _context()
    backend = _backend_relation()
    rel1 = _remote_write_relation()
    rel2 = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="otel",
    )
    ctx.run(ctx.on.start(), testing.State(relations=[backend, rel1, rel2]))
    assert set(written) == {f"relation-{rel1.id}.yml", f"relation-{rel2.id}.yml"}
    assert "PathPrefix(`/api/v1/push`)" in written[f"relation-{rel1.id}.yml"]
    assert "PathPrefix(`/prometheus/api/v1`)" in written[f"relation-{rel1.id}.yml"]
    assert (
        "PathPrefix(`/prometheus/config/v1/rules`) && Method(`GET`)"
        in written[f"relation-{rel1.id}.yml"]
    )
    assert "X-Scope-OrgID" not in written[f"relation-{rel1.id}.yml"]
    assert "/tenants/" not in written[f"relation-{rel1.id}.yml"]
    assert "PathPrefix(`/api/v1/push`)" in written[f"relation-{rel2.id}.yml"]
    assert "PathPrefix(`/prometheus/api/v1`)" in written[f"relation-{rel2.id}.yml"]
    assert (
        "PathPrefix(`/prometheus/config/v1/rules`) && Method(`GET`)"
        in written[f"relation-{rel2.id}.yml"]
    )
    assert "X-Scope-OrgID" not in written[f"relation-{rel2.id}.yml"]
    assert "/tenants/" not in written[f"relation-{rel2.id}.yml"]


def test_show_gateway_routes_action_reports_shared_urls(monkeypatch):
    ctx = _context()
    captured = {}

    monkeypatch.setattr(
        "charm.ops.ActionEvent.set_results",
        lambda _event, results: captured.update(results),
    )
    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._external_url_base",
        lambda _self: "http://10.0.0.20:80",
    )
    backend = _backend_relation()
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-vm",
    )
    ctx.run(ctx.on.action("show-gateway-routes"), testing.State(relations=[backend, relation]))
    mappings = json.loads(captured["mappings"])
    assert mappings == [
        {
            "backend-urls": ["http://10.0.0.10:9009"],
            "query-url": "http://10.0.0.20:80/prometheus",
            "relation-id": relation.id,
            "remote-app": "alloy-vm",
            "route-file": f"relation-{relation.id}.yml",
            "route-name": f"relation-{relation.id}",
            "write-url": "http://10.0.0.20:80/api/v1/push",
        }
    ]


def test_install_installs_traefik(monkeypatch):
    ctx = _context()
    called = {}

    monkeypatch.setattr("charm.traefik.install", lambda: called.setdefault("install", True))

    state = ctx.run(ctx.on.install(), testing.State())
    assert called["install"] is True
    assert state.unit_status.name == "maintenance"


def test_start_starts_traefik_once_when_backend_present(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: False)
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.get_version", lambda: "3.6.2")
    monkeypatch.setattr("charm.traefik.is_active", lambda: False)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    state = ctx.run(ctx.on.start(), testing.State(relations=[backend]))
    assert calls == ["start"]
    assert state.workload_version == "3.6.2"
    assert state.unit_status.name == "waiting"


def test_start_without_backend_does_not_double_start(monkeypatch):
    ctx = _context()
    calls = []

    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.get_version", lambda: "3.6.2")
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    state = ctx.run(ctx.on.start(), testing.State())
    assert calls == []
    assert state.workload_version == "3.6.2"
    assert state.unit_status.name == "waiting"


def test_invalid_backend_urls_json_does_not_crash_and_waits(monkeypatch):
    ctx = _context()
    backend = Relation(
        "backend",
        interface="mimir_gateway_backend",
        remote_app_name="mimir-vm",
        remote_app_data={"urls": "{not-json"},
    )
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.update_status(), testing.State(relations=[backend]))
    assert state.unit_status.name == "waiting"


def test_empty_backend_urls_do_not_report_active(monkeypatch):
    ctx = _context()
    backend = Relation(
        "backend",
        interface="mimir_gateway_backend",
        remote_app_name="mimir-vm",
        remote_app_data={"urls": "[]"},
    )
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.update_status(), testing.State(relations=[backend]))
    assert state.unit_status.name == "waiting"


def test_start_sets_durable_status_when_config_write_fails(monkeypatch):
    ctx = _context()

    monkeypatch.setattr(
        "charm.traefik.ensure_directories",
        lambda: (_ for _ in ()).throw(OSError("disk full")),
    )

    state = ctx.run(ctx.on.start(), testing.State(relations=[_backend_relation()]))
    assert state.unit_status.name == "blocked"
    assert state.unit_status.message == "Config failed: disk full"


def test_update_status_waits_when_service_inactive(monkeypatch):
    ctx = _context()

    monkeypatch.setattr("charm.traefik.is_active", lambda: False)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.update_status(), testing.State())
    assert state.unit_status.name == "waiting"


def test_backend_relation_changed_reconciles_gateway(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    relation = _remote_write_relation()

    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: "3.6.2")
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    state = ctx.run(ctx.on.relation_changed(backend), testing.State(relations=[backend, relation]))
    assert state.workload_version == "3.6.2"
    assert state.unit_status.name == "active"
    assert state.unit_status.message == "gateway ready: 1 active backend, 1 consumer served"


def test_remote_write_relation_changed_with_consumer_metadata_preserves_status(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": '{"groups": []}'},
    )
    configure_calls = []

    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._configure",
        lambda _self, _urls: configure_calls.append(_urls) or True,
    )
    monkeypatch.setattr("charm.traefik.get_version", lambda: "3.6.2")
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.MimirGatewayVmCharm._publish_consumer_data", lambda _self: None)

    state = ctx.run(
        ctx.on.relation_changed(relation),
        testing.State(
            relations=[backend, relation],
            unit_status=testing.ActiveStatus("gateway ready: 1 active backend, 1 consumer served"),
        ),
    )

    assert configure_calls == []
    assert state.workload_version == "3.6.2"
    assert state.unit_status.name == "active"
    assert state.unit_status.message == "gateway ready: 1 active backend, 1 consumer served"


def test_remote_write_rule_change_is_bridged_without_reconfiguring_traefik(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    rules = {
        "groups": [
            {
                "name": "principal_metrics_deadbeef",
                "rules": [{"alert": "ReferenceMetricMissing", "expr": "up == 0"}],
            }
        ]
    }
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    configure_calls = []

    monkeypatch.setattr(
        "charm.MimirGatewayVmCharm._configure",
        lambda _self, _urls: configure_calls.append(_urls) or True,
    )
    monkeypatch.setattr("charm.traefik.get_version", lambda: "3.6.2")

    state = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(
            relations=[backend, source, destination],
            leader=True,
            unit_status=testing.ActiveStatus("gateway ready: 1 active backend, 1 consumer served"),
        ),
    )

    assert configure_calls == []
    destination_out = state.get_relation(destination.id)
    assert json.loads(destination_out.local_app_data["alert_rules"]) == rules


def test_start_recovers_and_publishes_existing_rule_relations(monkeypatch):
    ctx = _context()
    rules = {
        "groups": [{"name": "recovered", "rules": [{"alert": "Recovered", "expr": "up == 0"}]}]
    }
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    state = ctx.run(
        ctx.on.start(),
        testing.State(
            relations=[_backend_relation(), source, destination],
            leader=True,
        ),
    )

    assert json.loads(state.get_relation(destination.id).local_app_data["alert_rules"]) == rules


def test_malformed_update_retains_relation_snapshot_and_applies_other_valid_updates(monkeypatch):
    ctx = _context()
    first = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-one",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
            )
        },
    )
    second = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-two",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "two", "rules": [{"alert": "Two", "expr": "up"}]}]}
            )
        },
    )
    destination = _rule_destination_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    initial = ctx.run(
        ctx.on.relation_changed(first),
        testing.State(relations=[first, second, destination, peers], leader=True),
    )
    invalid_first = replace(
        initial.get_relation(first.id), remote_app_data={"alert_rules": "private-invalid-body"}
    )
    updated_second = replace(
        initial.get_relation(second.id),
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "two-updated", "rules": [{"alert": "Two", "expr": "up"}]}]}
            )
        },
    )
    next_state = replace(
        initial,
        relations=[
            invalid_first,
            updated_second,
            initial.get_relation(destination.id),
            initial.get_relation(peers.id),
        ],
        leader=True,
        stored_states=[],
    )

    result = ctx.run(ctx.on.relation_changed(updated_second), next_state)

    assert [
        group["name"]
        for group in json.loads(result.get_relation(destination.id).local_app_data["alert_rules"])[
            "groups"
        ]
    ] == ["one", "two-updated"]


def test_first_seen_malformed_source_does_not_freeze_unrelated_valid_rules(monkeypatch):
    ctx = _context()
    malformed = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="bad-alloy",
        remote_app_data={"alert_rules": "private-invalid-body"},
    )
    valid_rules = {"groups": [{"name": "valid", "rules": [{"alert": "Valid", "expr": "up"}]}]}
    valid = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="good-alloy",
        remote_app_data={"alert_rules": json.dumps(valid_rules)},
    )
    destination = replace(
        _rule_destination_relation(),
        local_app_data={"alert_rules": '{"groups":[{"name":"old","rules":[]}]}'},
    )
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(valid),
        testing.State(relations=[malformed, valid, destination, _peer_relation()], leader=True),
    )

    assert (
        json.loads(state.get_relation(destination.id).local_app_data["alert_rules"]) == valid_rules
    )


def test_overflow_publishes_prior_accepted_snapshot_to_new_destination(monkeypatch):
    ctx = _context()
    first_rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "x" * 35_000}]}]}
    first = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-one",
        remote_app_data={"alert_rules": json.dumps(first_rules)},
    )
    old_destination = _rule_destination_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    initial = ctx.run(
        ctx.on.relation_changed(first),
        testing.State(relations=[first, old_destination, peers], leader=True),
    )
    second = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-two",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "two", "rules": [{"alert": "Two", "expr": "y" * 35_000}]}]}
            )
        },
    )
    new_destination = _rule_destination_relation()
    next_state = replace(
        initial,
        relations=[
            initial.get_relation(first.id),
            second,
            initial.get_relation(old_destination.id),
            new_destination,
            initial.get_relation(peers.id),
        ],
        leader=True,
        stored_states=[],
    )

    result = ctx.run(ctx.on.relation_created(new_destination), next_state)

    assert (
        json.loads(result.get_relation(new_destination.id).local_app_data["alert_rules"])
        == first_rules
    )


def test_leader_elected_republishes_accepted_snapshot_after_malformed_update(monkeypatch):
    ctx = _context()
    rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    initial = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(relations=[source, destination, peers], leader=True),
    )
    malformed = replace(
        initial.get_relation(source.id), remote_app_data={"alert_rules": "malformed-private"}
    )
    cleared_destination = replace(
        initial.get_relation(destination.id), local_app_data={"alert_rules": "{}"}
    )
    failover = replace(
        initial,
        relations=[malformed, cleared_destination, initial.get_relation(peers.id)],
        leader=True,
        stored_states=[],
    )

    result = ctx.run(ctx.on.leader_elected(), failover)

    assert json.loads(result.get_relation(destination.id).local_app_data["alert_rules"]) == rules


def test_peer_cache_and_source_admission_are_deterministically_bounded(monkeypatch):
    ctx = _context()
    sources = [
        Relation(
            "receive-remote-write",
            interface="prometheus_remote_write",
            remote_app_name=f"alloy-{index}",
            remote_app_data={
                "alert_rules": json.dumps(
                    {
                        "groups": [
                            {
                                "name": f"group-{index:02d}",
                                "rules": [{"alert": f"Alert{index}", "expr": "up"}],
                            }
                        ]
                    }
                )
            },
        )
        for index in range(MAX_SOURCE_RELATIONS + 1)
    ]
    destination = _rule_destination_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(sources[-1]),
        testing.State(relations=[*sources, destination, peers], leader=True),
    )

    groups = json.loads(state.get_relation(destination.id).local_app_data["alert_rules"])["groups"]
    admitted = sorted(sources, key=lambda item: item.id)[:MAX_SOURCE_RELATIONS]
    admitted_names = {source.remote_app_name for source in admitted}
    assert len(groups) == MAX_SOURCE_RELATIONS
    assert {f"alloy-{int(group['name'].split('-')[1])}" for group in groups} == admitted_names
    encoded_cache = state.get_relation(peers.id).local_app_data[CACHE_KEY]
    assert len(encoded_cache.encode("utf-8")) < CACHE_VALUE_LIMIT


def test_peer_cache_persists_multiple_individually_bounded_rule_sources(monkeypatch):
    """Persist and replay sources whose combined tree exceeds one source's node bound."""
    ctx = _context()
    sources = [
        Relation(
            "receive-remote-write",
            interface="prometheus_remote_write",
            remote_app_name=f"alloy-{index}",
            remote_app_data={
                "alert_rules": json.dumps(
                    {"groups": [{"name": f"group-{index}", "rules": [{}] * 5_000}]}
                )
            },
        )
        for index in range(2)
    ]
    destination = _rule_destination_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(sources[-1]),
        testing.State(relations=[*sources, destination, peers], leader=True),
    )

    cached_peers = state.get_relation(peers.id)
    assert CACHE_KEY in cached_peers.local_app_data
    malformed_sources = [
        replace(
            state.get_relation(source.id),
            remote_app_data={"alert_rules": "not-json"},
        )
        for source in sources
    ]
    cleared_destination = replace(
        state.get_relation(destination.id), local_app_data={"alert_rules": "{}"}
    )
    replayed = ctx.run(
        ctx.on.leader_elected(),
        replace(
            state,
            relations=[*malformed_sources, cleared_destination, cached_peers],
            stored_states=[],
            leader=True,
        ),
    )

    published = replayed.get_relation(destination.id).local_app_data["alert_rules"]
    assert len(published.encode("utf-8")) < CACHE_VALUE_LIMIT
    assert [group["name"] for group in json.loads(published)["groups"]] == [
        "group-0",
        "group-1",
    ]


def test_corrupt_peer_cache_is_rebuilt_without_logging_its_content(monkeypatch, caplog):
    ctx = _context()
    private_cache = "private-corrupt-cache"
    rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    peers = PeerRelation(
        "gateway-peers",
        interface="mimir_gateway_peers",
        local_app_data={CACHE_KEY: private_cache},
    )
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(relations=[source, destination, peers], leader=True),
    )

    assert json.loads(state.get_relation(destination.id).local_app_data["alert_rules"]) == rules
    assert state.get_relation(peers.id).local_app_data[CACHE_KEY] != private_cache
    assert private_cache not in caplog.text


def test_downstream_broken_excludes_only_that_destination(monkeypatch):
    ctx = _context()
    rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    broken = _rule_destination_relation()
    remaining = _rule_destination_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_broken(broken),
        testing.State(
            relations=[source, broken, remaining, _peer_relation()],
            leader=True,
            unit_status=testing.ActiveStatus("gateway ready"),
        ),
    )

    assert json.loads(state.get_relation(remaining.id).local_app_data["alert_rules"]) == rules
    assert state.unit_status.name == "active"


def test_rules_without_destination_set_waiting_and_relation_restores_gateway_status(monkeypatch):
    ctx = _context()
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
            )
        },
    )
    backend = _backend_relation()
    peers = _peer_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    waiting = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(
            relations=[backend, source, peers],
            leader=True,
            unit_status=testing.ActiveStatus("gateway ready"),
        ),
    )
    assert waiting.unit_status.name == "waiting"
    assert waiting.unit_status.message == "waiting for Mimir alert-rule destination"

    destination = _rule_destination_relation()
    ready_input = replace(
        waiting,
        relations=[
            waiting.get_relation(backend.id),
            waiting.get_relation(source.id),
            waiting.get_relation(peers.id),
            destination,
        ],
        leader=True,
    )
    ready = ctx.run(ctx.on.relation_created(destination), ready_input)
    assert ready.unit_status.name == "active"


def test_rule_waiting_status_does_not_replace_blocked_or_unrelated_waiting(monkeypatch):
    ctx = _context()
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
            )
        },
    )
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    for durable_status in (
        testing.BlockedStatus("configuration failed"),
        testing.WaitingStatus("waiting for backend relation data"),
    ):
        state = ctx.run(
            ctx.on.relation_changed(source),
            testing.State(
                relations=[source, _peer_relation()],
                leader=True,
                unit_status=durable_status,
            ),
        )
        assert state.unit_status == durable_status


def test_non_leader_validates_but_does_not_publish_rule_data(monkeypatch):
    ctx = _context()
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
            )
        },
    )
    destination = _rule_destination_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(relations=[source, destination], leader=False),
    )

    assert "alert_rules" not in state.get_relation(destination.id).local_app_data


def test_source_relation_broken_withdraws_all_downstream_rules(monkeypatch):
    ctx = _context()
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={
            "alert_rules": json.dumps(
                {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
            )
        },
    )
    destination = _rule_destination_relation()
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)

    state = ctx.run(
        ctx.on.relation_broken(source),
        testing.State(relations=[_backend_relation(), source, destination], leader=True),
    )

    assert state.get_relation(destination.id).local_app_data["alert_rules"] == '{"groups":[]}'


def test_downstream_join_overwrites_standard_empty_rules_after_library_handler(monkeypatch):
    ctx = _context()
    rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_joined(destination),
        testing.State(relations=[source, destination], leader=True),
    )

    output = state.get_relation(destination.id).local_app_data
    assert json.loads(output["alert_rules"]) == rules
    assert {"application", "model", "model_uuid", "tenant-id"}.issubset(output)


def test_unknown_malformed_source_is_withdrawn_without_logging_its_body(monkeypatch, caplog):
    ctx = _context()
    private_body = "private-invalid-body"
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": private_body},
    )
    existing = '{"groups":[{"name":"existing","rules":[]}]}'
    destination = replace(_rule_destination_relation(), local_app_data={"alert_rules": existing})
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.relation_changed(source),
        testing.State(relations=[source, destination], leader=True),
    )

    assert state.get_relation(destination.id).local_app_data["alert_rules"] == '{"groups":[]}'
    assert private_body not in caplog.text


def test_rule_lifecycle_converges_when_traefik_configuration_fails(monkeypatch):
    ctx = _context()
    rules = {"groups": [{"name": "one", "rules": [{"alert": "One", "expr": "up"}]}]}
    source = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
        remote_app_data={"alert_rules": json.dumps(rules)},
    )
    destination = _rule_destination_relation()
    monkeypatch.setattr("charm.MimirGatewayVmCharm._configure", lambda _self, _urls: False)

    state = ctx.run(
        ctx.on.relation_joined(source),
        testing.State(relations=[source, destination], leader=True),
    )

    assert json.loads(state.get_relation(destination.id).local_app_data["alert_rules"]) == rules


def test_config_changed_starts_traefik_when_service_inactive(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: True)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: True)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: False)
    monkeypatch.setattr("charm.traefik.daemon_reload", lambda: calls.append("daemon-reload"))
    monkeypatch.setattr("charm.traefik.enable", lambda: calls.append("enable"))
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.restart", lambda: calls.append("restart"))
    monkeypatch.setattr("charm.traefik.is_active", lambda: False)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.config_changed(), testing.State(relations=[backend]))
    assert calls == ["daemon-reload", "enable", "start"]
    assert state.unit_status.name == "waiting"


def test_config_changed_starts_inactive_traefik_even_without_file_changes(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: False)
    monkeypatch.setattr("charm.traefik.daemon_reload", lambda: calls.append("daemon-reload"))
    monkeypatch.setattr("charm.traefik.enable", lambda: calls.append("enable"))
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.restart", lambda: calls.append("restart"))
    monkeypatch.setattr("charm.traefik.is_active", lambda: False)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.config_changed(), testing.State(relations=[backend]))
    assert calls == ["start"]
    assert state.unit_status.name == "waiting"


def test_config_changed_does_not_restart_active_traefik_for_dynamic_config_updates(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    relation = _remote_write_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: True)
    monkeypatch.setattr("charm.traefik.daemon_reload", lambda: calls.append("daemon-reload"))
    monkeypatch.setattr("charm.traefik.enable", lambda: calls.append("enable"))
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.restart", lambda: calls.append("restart"))
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(
        ctx.on.config_changed(),
        testing.State(relations=[backend, relation], leader=True),
    )
    assert calls == []
    assert state.unit_status.name == "active"


def test_config_changed_does_not_restart_active_traefik_for_dynamic_config_prune(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: False)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: True)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: False)
    monkeypatch.setattr("charm.traefik.daemon_reload", lambda: calls.append("daemon-reload"))
    monkeypatch.setattr("charm.traefik.enable", lambda: calls.append("enable"))
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.restart", lambda: calls.append("restart"))
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.config_changed(), testing.State(relations=[backend]))
    assert calls == []
    assert state.unit_status.name == "active"


def test_config_changed_restarts_active_traefik_for_static_config_updates(monkeypatch):
    ctx = _context()
    backend = _backend_relation()
    calls = []

    monkeypatch.setattr("charm.traefik.ensure_directories", lambda: None)
    monkeypatch.setattr("charm.traefik.write_static_config", lambda _content: True)
    monkeypatch.setattr("charm.traefik.write_systemd_unit", lambda _content: False)
    monkeypatch.setattr("charm.traefik.prune_dynamic_configs", lambda keep: False)
    monkeypatch.setattr("charm.traefik.write_dynamic_config", lambda _filename, _content: False)
    monkeypatch.setattr("charm.traefik.daemon_reload", lambda: calls.append("daemon-reload"))
    monkeypatch.setattr("charm.traefik.enable", lambda: calls.append("enable"))
    monkeypatch.setattr("charm.traefik.start", lambda: calls.append("start"))
    monkeypatch.setattr("charm.traefik.restart", lambda: calls.append("restart"))
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)

    state = ctx.run(ctx.on.config_changed(), testing.State(relations=[backend]))
    assert calls == ["restart"]
    assert state.unit_status.name == "active"
