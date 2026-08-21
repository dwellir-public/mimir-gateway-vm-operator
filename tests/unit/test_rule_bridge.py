import json

import pytest

from rule_bridge import (
    RELATION_VALUE_LIMIT,
    InvalidRuleDocumentError,
    merge_rule_groups,
    parse_rule_groups,
    serialize_rule_groups,
)


def _group(name: str, expression: str = "up == 0") -> dict:
    return {
        "name": name,
        "interval": "1m",
        "labels": {"owner": "principal"},
        "rules": [
            {
                "alert": "ReferenceMetricMissing",
                "expr": expression,
                "for": "5m",
                "labels": {"severity": "critical"},
                "annotations": {"summary": "missing"},
            }
        ],
    }


def test_parse_preserves_valid_group_content_exactly():
    group = _group("principal_metrics_deadbeef")

    assert parse_rule_groups(json.dumps({"groups": [group]})) == [group]


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        '{"groups":{},"secret":"must-not-log"}',
        '{"groups":[null]}',
        '{"groups":[{"name":"","rules":[]}]}',
        '{"groups":[{"name":"valid","rules":{}}]}',
        '{"groups":[{"name":"valid","rules":[null]}]}',
        '{"groups":[],"groups":[{"name":"duplicate","rules":[]}]}',
        '{"groups":[{"name":"valid","rules":[{"expr":NaN}]}]}',
    ],
)
def test_parse_rejects_malformed_or_ambiguous_documents(raw):
    with pytest.raises(InvalidRuleDocumentError):
        parse_rule_groups(raw)


def test_parse_rejects_relation_value_at_ceiling():
    with pytest.raises(InvalidRuleDocumentError, match="size"):
        parse_rule_groups(" " * RELATION_VALUE_LIMIT)


@pytest.mark.parametrize(
    "raw",
    [
        '{"groups":' + "[" * 2_000 + "]" * 2_000 + "}",
        '{"groups":[],"invalid":"\ud800"}',
    ],
    ids=["deeply-nested", "non-utf8-surrogate"],
)
def test_parse_safely_rejects_recursive_or_non_utf8_text(raw):
    with pytest.raises(InvalidRuleDocumentError):
        parse_rule_groups(raw)


def test_merge_orders_by_relation_id_then_group_name_without_rewriting():
    group_z = _group("z", "vector(3)")
    group_b = _group("b", "vector(2)")
    group_a = _group("a", "vector(1)")

    merged = merge_rule_groups({9: [group_z], 3: [group_b, group_a]})

    assert merged == [group_a, group_b, group_z]


def test_serialize_is_compact_sorted_and_round_trips():
    groups = [_group("principal")]

    rendered = serialize_rule_groups(groups)

    assert '": ' not in rendered
    assert '", "' not in rendered
    assert json.loads(rendered) == {"groups": groups}


def test_serialize_rejects_aggregate_at_ceiling():
    groups = [_group("principal", "x" * RELATION_VALUE_LIMIT)]

    with pytest.raises(InvalidRuleDocumentError, match="size"):
        serialize_rule_groups(groups)
