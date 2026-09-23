"""Measured 1,024-source envelope, independent of live Juju relation validation."""

import json
import uuid

from charms.dwellir_observability.v0 import alert_rule_transport as transport


def _corpus():
    snapshots = {}
    for index in range(1024):
        labels = {
            "juju_model": f"model-{index}",
            "juju_model_uuid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"model-{index}")),
            "juju_application": f"workload-{index}",
            "juju_unit": f"workload-{index}/0",
            "juju_charm": "reference",
        }
        snapshots[index] = [
            {
                "name": f"{labels['juju_model_uuid']}-workload-{index}-rule-{number}",
                "rules": [
                    {
                        "alert": f"WorkloadFault{number}",
                        "expr": "sum(rate(errors_total{"
                        + ",".join(f'{k}="{v}"' for k, v in labels.items())
                        + "}[5m])) > 0",
                        "for": "0s",
                        "labels": dict(labels, severity="warning"),
                        "annotations": {
                            "summary": "Workload reported an error",
                            "description": (
                                "Check the workload service and its upstream dependencies before "
                                "taking corrective action. Confirm fresh samples "
                                "and the exact Juju topology."
                            ),
                            "runbook_url": "https://example.invalid/test-only/runbook",
                        },
                    }
                ],
            }
            for number in range(4)
        ]
    return snapshots


def test_1024_source_corpus_preserves_all_groups_and_topology_through_wire_and_cache():
    snapshots = _corpus()
    groups = [group for values in snapshots.values() for group in values]
    raw = json.dumps({"groups": groups}, sort_keys=True)
    assert len(raw.encode()) > 2 * 1024 * 1024
    packed = transport.encode(raw, {transport.ENCODINGS_KEY: transport.ENCODINGS})
    assert len(packed) < 60 * 1024
    assert json.loads(transport.decode(packed))["groups"] == groups
    import rule_bridge as module

    accepted = module.serialize_rule_groups(module.merge_rule_groups(snapshots))
    cache = module._RuleCache(snapshots, accepted)
    encoded = module._encode_cache(cache)
    assert module._decode_cache(encoded) == cache
    assert module.parse_rule_groups(packed) == groups


def test_invalid_cache_and_malformed_source_never_withdraw_downstream_rules():
    from types import SimpleNamespace

    import rule_bridge as module

    app, remote = object(), object()
    source = SimpleNamespace(id=1, app=remote, data={remote: {"alert_rules": "broken"}, app: {}})
    accepted = '{"groups":[{"name":"existing","rules":[]}]}'
    destination = SimpleNamespace(
        id=2, app=remote, data={remote: {}, app: {"alert_rules": accepted}}
    )
    peer = SimpleNamespace(data={app: {module.CACHE_KEY: "corrupt-cache"}})
    charm = SimpleNamespace(
        app=app,
        unit=SimpleNamespace(is_leader=lambda: True),
        model=SimpleNamespace(
            get_relation=lambda _: peer,
            relations={"receive-remote-write": [source], "mimir-alert-rules": [destination]},
        ),
    )
    result = module.PrometheusRuleBridge(charm).reconcile()
    assert result.pending
    assert destination.data[app]["alert_rules"] == accepted
    assert peer.data[app][module.CACHE_KEY] == "corrupt-cache"
