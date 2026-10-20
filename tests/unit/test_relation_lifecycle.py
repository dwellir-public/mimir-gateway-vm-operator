"""Regression coverage for remote-write relations disappearing during other hooks."""

import json
from dataclasses import replace
from pathlib import Path

import ops
import pytest
import yaml
from ops import testing
from ops.model import RelationData, RelationDataContent

from charm import MimirGatewayVmCharm
from rule_bridge import CACHE_KEY


def _rules(name):
    """Return distinguishable rules for checking ownership and retention."""
    return json.dumps({"groups": [{"name": name, "rules": [{"alert": name, "expr": "up"}]}]})


@pytest.fixture
def context(monkeypatch):
    """Exercise real charm events without changing a host workload."""
    monkeypatch.setattr(MimirGatewayVmCharm, "_configure", lambda *_args: True)
    monkeypatch.setattr("charm.traefik.get_version", lambda: None)
    monkeypatch.setattr("charm.traefik.is_active", lambda: True)
    manifest = yaml.safe_load((Path(__file__).parents[2] / "charmcraft.yaml").read_text())
    return testing.Context(
        MimirGatewayVmCharm,
        meta={key: manifest[key] for key in ("name", "requires", "provides", "peers")},
        config=manifest["config"],
        actions=manifest["actions"],
    )


def _sources():
    """Model the healthy event source and stale source from the incident."""
    return [
        testing.Relation(
            "receive-remote-write",
            id=relation_id,
            remote_app_name=name,
            remote_app_data={"alert_rules": _rules(name)},
        )
        for relation_id, name in [(39, "Healthy"), (104, "Stale")]
    ]


@pytest.mark.parametrize("event_source_id", [39, 104])
@pytest.mark.parametrize("valid_cache", [True, False])
def test_unreadable_source_retains_rules_while_healthy_source_updates(
    context, monkeypatch, event_source_id, valid_cache
):
    """A stale source must neither crash another hook nor erase its accepted rules."""
    healthy, stale = _sources()
    destination = testing.Relation("mimir-alert-rules", remote_app_name="mimir")
    peer = testing.PeerRelation("gateway-peers")
    backend = testing.Relation(
        "backend", remote_app_name="mimir", remote_app_data={"urls": '["http://mimir:9009"]'}
    )
    initial = context.run(
        context.on.relation_changed(healthy),
        testing.State(
            relations=[healthy, stale, destination, peer, backend],
            leader=True,
            unit_status=testing.ActiveStatus("gateway ready"),
        ),
    )
    healthy = replace(
        initial.get_relation(healthy.id), remote_app_data={"alert_rules": _rules("Updated")}
    )
    relations = [healthy, *[r for r in initial.relations if r.id != healthy.id]]
    if not valid_cache:
        relations = [
            replace(r, local_app_data={CACHE_KEY: "corrupt"}) if r.id == peer.id else r
            for r in relations
        ]
    original = RelationData.__getitem__

    def inaccessible(data, entity):
        if data.relation.id == stale.id:
            raise ops.ModelError("ERROR permission denied")
        return original(data, entity)

    monkeypatch.setattr(RelationData, "__getitem__", inaccessible)
    result = context.run(
        context.on.relation_changed(next(r for r in relations if r.id == event_source_id)),
        replace(initial, relations=relations),
    )
    groups = json.loads(result.get_relation(destination.id).local_app_data["alert_rules"])[
        "groups"
    ]
    assert {group["name"] for group in groups} == {
        "Updated" if valid_cache else "Healthy",
        "Stale",
    }
    assert result.unit_status.name == "waiting"
    assert "unavailable=(104,)" in result.unit_status.message

    # Once Juju confirms removal, stale ownership is withdrawn and status recovers.
    recovered = context.run(
        context.on.update_status(),
        replace(result, relations=[r for r in result.relations if r.id != stale.id]),
    )
    groups = json.loads(recovered.get_relation(destination.id).local_app_data["alert_rules"])[
        "groups"
    ]
    assert [group["name"] for group in groups] == ["Updated"]
    assert recovered.unit_status.name == "active"


def test_inaccessible_capability_write_does_not_stop_other_sources(context, monkeypatch):
    """Global recovery must finish healthy publication despite a removed source bag."""
    healthy, stale = _sources()
    original = RelationDataContent.__setitem__

    def inaccessible(data, key, value):
        if data.relation.id == stale.id and key == "alert_rules_encodings":
            raise ops.ModelError("ERROR permission denied")
        return original(data, key, value)

    monkeypatch.setattr(RelationDataContent, "__setitem__", inaccessible)
    result = context.run(
        context.on.leader_elected(),
        testing.State(relations=[healthy, stale], leader=True, unit_status=testing.ActiveStatus()),
    )
    assert result.get_relation(healthy.id).local_app_data["alert_rules_encodings"]
    assert result.unit_status.name == "waiting"
    assert "unavailable=(104,)" in result.unit_status.message


@pytest.mark.parametrize("event_name", ["relation_departed", "relation_broken"])
def test_teardown_does_not_republish_endpoints(context, monkeypatch, event_name):
    """Teardown does not need a frontend address from its dying hook context."""
    healthy, stale = _sources()
    backend = testing.Relation(
        "backend", remote_app_name="mimir", remote_app_data={"urls": '["http://mimir:9009"]'}
    )

    def unexpected_address_lookup(_self):
        pytest.fail("Teardown must not look up or republish consumer endpoints")

    monkeypatch.setattr(MimirGatewayVmCharm, "_external_url_base", unexpected_address_lookup)
    event = (
        context.on.relation_departed(stale, remote_unit=0, departing_unit=0)
        if event_name == "relation_departed"
        else context.on.relation_broken(stale)
    )
    result = context.run(
        event,
        testing.State(relations=[backend, healthy, stale], leader=True),
    )
    assert result.unit_status.name == "waiting"  # Rules exist without a destination.


def test_one_departing_unit_keeps_relation_rules(context):
    """Scaling a consumer down must not withdraw its application-owned alert rules."""
    source = replace(_sources()[0], remote_units_data={0: {}, 1: {}})
    backend = testing.Relation(
        "backend", remote_app_name="mimir", remote_app_data={"urls": '["http://mimir:9009"]'}
    )
    destination = testing.Relation("mimir-alert-rules", remote_app_name="mimir")
    result = context.run(
        context.on.relation_departed(source, remote_unit=1, departing_unit=1),
        testing.State(relations=[backend, source, destination], leader=True),
    )
    assert json.loads(result.get_relation(destination.id).local_app_data["alert_rules"]) == (
        json.loads(_rules("Healthy"))
    )
    assert result.unit_status.name == "active"


def test_endpoint_publication_continues_past_inaccessible_source(context, monkeypatch):
    """A global refresh still repairs endpoints for surviving consumers."""
    healthy, stale = _sources()
    original = RelationData.__getitem__

    def inaccessible(data, entity):
        if data.relation.id == stale.id:
            raise ops.ModelError("ERROR permission denied")
        return original(data, entity)

    monkeypatch.setattr(RelationData, "__getitem__", inaccessible)
    monkeypatch.setattr(MimirGatewayVmCharm, "_external_url_base", lambda _: "http://gateway")
    backend = testing.Relation(
        "backend", remote_app_name="mimir", remote_app_data={"urls": '["http://mimir:9009"]'}
    )
    result = context.run(
        context.on.upgrade_charm(),
        testing.State(relations=[backend, healthy, stale], leader=True),
    )
    assert json.loads(result.get_relation(healthy.id).local_unit_data["remote_write"]) == {
        "url": "http://gateway/api/v1/push"
    }
    assert result.unit_status.name == "waiting"
