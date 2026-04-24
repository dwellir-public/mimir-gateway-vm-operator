import json

from ops import testing
from ops.testing import Relation

from charm import MimirGatewayVmCharm

META = {
    "name": "mimir-gateway-vm",
    "requires": {"backend": {"interface": "mimir_gateway_backend"}},
    "provides": {
        "receive-remote-write": {"interface": "prometheus_remote_write"},
        "grafana-source": {"interface": "grafana_datasource"},
    },
}

ACTION_META = {
    "show-relation-tenants": {
        "description": "Show relation-scoped tenant mapping and published frontend URLs."
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


def test_start_renders_derived_tenant_ids_in_dynamic_configs(monkeypatch):
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

    same_model_relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy",
    )
    cross_model_relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="otel",
        remote_app_data={"model_uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d"},
    )
    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, same_model_relation, cross_model_relation]),
    )
    assert calls == []
    assert len(written) == 2
    assert any('X-Scope-OrgID: "alloy"' in content for content in written.values())
    assert any('X-Scope-OrgID: "otel-f794060e"' in content for content in written.values())
    assert state.unit_status.name == "active"


def test_remote_write_relation_publishes_tenant_specific_gateway_url(monkeypatch):
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
    assert relation_out.local_unit_data["remote_write"] == (
        '{"url": "http://10.0.0.20:80/tenants/alloy/api/v1/push"}'
    )


def test_remote_write_relations_publish_distinct_tenant_specific_gateway_urls(monkeypatch):
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
        remote_app_data={"model_uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d"},
    )

    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, relation_one, relation_two], leader=True),
    )

    relation_one_out = state.get_relation(relation_one.id)
    relation_two_out = state.get_relation(relation_two.id)
    assert relation_one_out.local_unit_data["remote_write"] == (
        '{"url": "http://10.0.0.20:80/tenants/alloy/api/v1/push"}'
    )
    assert relation_two_out.local_unit_data["remote_write"] == (
        '{"url": "http://10.0.0.20:80/tenants/otel-f794060e/api/v1/push"}'
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
    assert (
        relation_out.local_unit_data["grafana_source_host"]
        == "http://10.0.0.20:80/tenants/alloy/prometheus"
    )


def test_grafana_source_relation_is_cleared_when_multiple_tenants_exist(monkeypatch):
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
        remote_app_data={"model_uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d"},
    )
    state = ctx.run(
        ctx.on.start(),
        testing.State(relations=[backend, grafana, relation, cross_model_relation], leader=True),
    )
    relation_out = state.get_relation(grafana.id)
    assert "grafana_source_host" not in relation_out.local_unit_data


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
        remote_app_data={"model_uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d"},
    )
    ctx.run(ctx.on.start(), testing.State(relations=[backend, rel1, rel2]))
    assert set(written) == {f"relation-{rel1.id}.yml", f"relation-{rel2.id}.yml"}
    assert (
        "Path(`/tenants/alloy`) || PathPrefix(`/tenants/alloy/`)"
        in written[f"relation-{rel1.id}.yml"]
    )
    assert '          - "/tenants/alloy"' in written[f"relation-{rel1.id}.yml"]
    assert 'X-Scope-OrgID: "alloy"' in written[f"relation-{rel1.id}.yml"]
    assert (
        "Path(`/tenants/otel-f794060e`) || "
        "PathPrefix(`/tenants/otel-f794060e/`)" in written[f"relation-{rel2.id}.yml"]
    )
    assert '          - "/tenants/otel-f794060e"' in written[f"relation-{rel2.id}.yml"]
    assert 'X-Scope-OrgID: "otel-f794060e"' in written[f"relation-{rel2.id}.yml"]


def test_show_relation_tenants_action_reports_tenant_specific_urls(monkeypatch):
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
        remote_app_data={
            "model_uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d",
        },
    )
    ctx.run(ctx.on.action("show-relation-tenants"), testing.State(relations=[backend, relation]))
    mappings = json.loads(captured["mappings"])
    assert mappings == [
        {
            "query-url": ("http://10.0.0.20:80/tenants/alloy-vm-f794060e/prometheus"),
            "relation-id": relation.id,
            "remote-app": "alloy-vm",
            "remote-model-uuid": "f794060e-b2d7-43ba-81d5-1a028c1c748d",
            "route-file": f"relation-{relation.id}.yml",
            "route-name": f"relation-{relation.id}",
            "tenant-id": "alloy-vm-f794060e",
            "tenant-source": "derived",
            "write-url": ("http://10.0.0.20:80/tenants/alloy-vm-f794060e/api/v1/push"),
        }
    ]


def test_show_relation_tenants_action_reports_explicit_tenant_source(monkeypatch):
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
    relation = Relation(
        "receive-remote-write",
        interface="prometheus_remote_write",
        remote_app_name="alloy-vm",
        remote_app_data={"tenant-id": "team-a"},
    )

    ctx.run(ctx.on.action("show-relation-tenants"), testing.State(relations=[relation]))
    mappings = json.loads(captured["mappings"])
    assert mappings[0]["tenant-id"] == "team-a"
    assert mappings[0]["tenant-source"] == "explicit"
    assert mappings[0]["remote-model-uuid"] == ""


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
    assert state.unit_status.message == "gateway ready: 1 active backend, 1 tenant served"


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
