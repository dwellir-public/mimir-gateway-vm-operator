"""Measured 1,024-source envelope, independent of live Juju relation validation."""

import json
import uuid

import pytest
from cosl import LZMABase64


def _corpus(count=1024, rules=4):
    snapshots = {}
    for index in range(count):
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
            for number in range(rules)
        ]
    return snapshots


@pytest.mark.parametrize("count,rules", [(1024, 4), (1025, 1), (2048, 1)])
def test_source_corpus_preserves_all_groups_and_topology_through_wire_and_cache(count, rules):
    snapshots = _corpus(count, rules)
    if count == 2048:
        # Isolate count scaling from the separate per-value wire-size policy.
        snapshots = {
            i: [{"name": f"g-{i}", "rules": [{"alert": "Example", "expr": "up"}]}]
            for i in range(count)
        }
    groups = [group for values in snapshots.values() for group in values]
    raw = json.dumps({"groups": groups}, sort_keys=True)
    assert len(raw.encode()) > 60 * 1024
    packed = LZMABase64.compress(raw)
    assert len(packed) < 60 * 1024
    assert json.loads(LZMABase64.decompress(packed))["groups"] == groups
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


def test_high_entropy_2048_source_document_reports_wire_capacity():
    groups = [group for values in _corpus(2048, 1).values() for group in values]
    import rule_bridge as module

    packed = LZMABase64.compress(json.dumps({"groups": groups}, sort_keys=True))
    with pytest.raises(ValueError):
        module.parse_rule_groups(packed)
