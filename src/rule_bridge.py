"""Bounded desired-state bridging for Prometheus alert-rule relations."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from remote_write import ALERT_RULES_KEY

logger = logging.getLogger(__name__)

RELATION_VALUE_LIMIT = 60 * 1024
CACHE_KEY = "_mimir_rule_bridge_state_v1"
CACHE_VERSION = 1
CACHE_VALUE_LIMIT = 60 * 1024
CACHE_DECODED_LIMIT = 2 * 1024 * 1024
MAX_SOURCE_RELATIONS = 32
MAX_DEPTH = 32
MAX_NODES = 10_000
MAX_GROUP_NAME_BYTES = 512


class InvalidRuleDocumentError(ValueError):
    """Report a structurally invalid rule document without retaining its content."""


class InvalidRuleCacheError(ValueError):
    """Report invalid bounded bridge cache state without exposing its content."""


@dataclass(frozen=True)
class RuleBridgeResult:
    """Describe accepted bridge state needed for charm status reconciliation."""

    accepted_has_rules: bool
    destination_present: bool


@dataclass(frozen=True)
class _RuleCache:
    """Hold per-relation snapshots and the last accepted rendered aggregate."""

    snapshots: dict[int, list[dict[str, Any]]]
    accepted: str


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRuleDocumentError("document contains a duplicate object key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    """Reject non-standard JSON constants such as NaN and Infinity."""
    raise InvalidRuleDocumentError("document contains a non-finite number")


def _validate_scalar(value: Any) -> None:
    """Accept JSON scalar values while rejecting non-finite or unsupported values."""
    if isinstance(value, float) and not math.isfinite(value):
        raise InvalidRuleDocumentError("document contains a non-finite number")
    if not isinstance(value, (str, int, float, bool, type(None))):
        raise InvalidRuleDocumentError("document contains an unsupported value")


def _validate_tree(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    """Reject excessively deep or large JSON structures using a bounded tree walk."""
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > MAX_NODES:
        raise InvalidRuleDocumentError("document has too many values")
    if depth > MAX_DEPTH:
        raise InvalidRuleDocumentError("document is too deeply nested")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidRuleDocumentError("object key is not a string")
            _validate_tree(item, depth=depth + 1, nodes=nodes)
    elif isinstance(value, list):
        for item in value:
            _validate_tree(item, depth=depth + 1, nodes=nodes)
    else:
        _validate_scalar(value)


def _validate_group(group: Any) -> dict[str, Any]:
    """Validate one alert-rule group while preserving every supplied field."""
    if not isinstance(group, dict):
        raise InvalidRuleDocumentError("group is not an object")
    name = group.get("name")
    rules = group.get("rules")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.encode("utf-8")) > MAX_GROUP_NAME_BYTES
        or not name.isprintable()
    ):
        raise InvalidRuleDocumentError("group name is invalid")
    if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
        raise InvalidRuleDocumentError("group rules is not a list of objects")
    return group


def parse_rule_groups(raw: str) -> list[dict[str, Any]]:
    """Parse one bounded standard remote-write alert-rule value into rule groups."""
    if not isinstance(raw, str):
        raise InvalidRuleDocumentError("relation value is not text")
    try:
        raw_size = len(raw.encode("utf-8"))
    except UnicodeError as exc:
        raise InvalidRuleDocumentError("relation value is not valid UTF-8 text") from exc
    if raw_size >= RELATION_VALUE_LIMIT:
        raise InvalidRuleDocumentError("relation value exceeds the safe size limit")
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise InvalidRuleDocumentError("relation value is not valid bounded JSON") from exc
    _validate_tree(document)
    if not isinstance(document, dict) or set(document) != {"groups"}:
        raise InvalidRuleDocumentError("rule document must contain only groups")
    groups = document["groups"]
    if not isinstance(groups, list):
        raise InvalidRuleDocumentError("groups is not a list")
    return [_validate_group(group) for group in groups]


def merge_rule_groups(snapshots: Mapping[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge relation snapshots deterministically by relation ID and group name."""
    merged: list[dict[str, Any]] = []
    for relation_id in sorted(snapshots):
        merged.extend(sorted(snapshots[relation_id], key=lambda group: str(group["name"])))
    return merged


def serialize_rule_groups(groups: list[dict[str, Any]]) -> str:
    """Serialize merged groups compactly and reject values unsafe for a Juju databag."""
    rendered = json.dumps({"groups": groups}, sort_keys=True, separators=(",", ":"))
    if len(rendered.encode("utf-8")) >= RELATION_VALUE_LIMIT:
        raise InvalidRuleDocumentError("merged rules exceed the safe size limit")
    return rendered


def _decompress_cache(encoded: str) -> bytes:
    """Decode one bounded base64/zlib cache value without permitting decompression bombs."""
    if not isinstance(encoded, str):
        raise InvalidRuleCacheError("cache value exceeds the safe size limit")
    try:
        encoded_size = len(encoded.encode("utf-8"))
    except UnicodeError as exc:
        raise InvalidRuleCacheError("cache value is not valid UTF-8 text") from exc
    if encoded_size >= CACHE_VALUE_LIMIT:
        raise InvalidRuleCacheError("cache value exceeds the safe size limit")
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidRuleCacheError("cache value is not valid base64") from exc
    decompressor = zlib.decompressobj()
    try:
        decoded = decompressor.decompress(compressed, CACHE_DECODED_LIMIT + 1)
    except zlib.error as exc:
        raise InvalidRuleCacheError("cache value is not valid compressed data") from exc
    if (
        len(decoded) > CACHE_DECODED_LIMIT
        or decompressor.unconsumed_tail
        or not decompressor.eof
        or decompressor.unused_data
    ):
        raise InvalidRuleCacheError("cache decoded content exceeds safe bounds")
    return decoded


def _decode_cache(encoded: str) -> _RuleCache:
    """Decode and structurally validate leader-shared bridge cache state."""
    try:
        document = json.loads(
            _decompress_cache(encoded),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        _validate_tree(document)
    except (InvalidRuleDocumentError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise InvalidRuleCacheError("cache content is not valid bounded JSON") from exc
    if not isinstance(document, dict) or set(document) != {"accepted", "relations", "version"}:
        raise InvalidRuleCacheError("cache structure is invalid")
    relations = document["relations"]
    if document["version"] != CACHE_VERSION or not isinstance(relations, dict):
        raise InvalidRuleCacheError("cache version or relation map is invalid")
    if len(relations) > MAX_SOURCE_RELATIONS:
        raise InvalidRuleCacheError("cache contains too many relations")
    snapshots: dict[int, list[dict[str, Any]]] = {}
    for raw_relation_id, groups in relations.items():
        if (
            not raw_relation_id.isdecimal()
            or len(raw_relation_id) > 20
            or str(int(raw_relation_id)) != raw_relation_id
        ):
            raise InvalidRuleCacheError("cache relation identifier is invalid")
        try:
            snapshots[int(raw_relation_id)] = parse_rule_groups(
                json.dumps({"groups": groups}, sort_keys=True, separators=(",", ":"))
            )
        except InvalidRuleDocumentError as exc:
            raise InvalidRuleCacheError("cache relation snapshot is invalid") from exc
    try:
        accepted = serialize_rule_groups(parse_rule_groups(document["accepted"]))
    except (InvalidRuleDocumentError, TypeError) as exc:
        raise InvalidRuleCacheError("cache accepted snapshot is invalid") from exc
    return _RuleCache(snapshots=snapshots, accepted=accepted)


def _encode_cache(cache: _RuleCache) -> str:
    """Serialize leader-shared cache state compactly within one Juju value."""
    document = {
        "accepted": cache.accepted,
        "relations": {str(key): value for key, value in sorted(cache.snapshots.items())},
        "version": CACHE_VERSION,
    }
    try:
        _validate_tree(document)
    except InvalidRuleDocumentError as exc:
        raise InvalidRuleCacheError("cache structure exceeds safe bounds") from exc
    raw = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > CACHE_DECODED_LIMIT:
        raise InvalidRuleCacheError("cache decoded content exceeds safe bounds")
    encoded = base64.b64encode(zlib.compress(raw, level=9)).decode("ascii")
    if len(encoded.encode("utf-8")) >= CACHE_VALUE_LIMIT:
        raise InvalidRuleCacheError("cache value exceeds the safe size limit")
    return encoded


class PrometheusRuleBridge:
    """Maintain bounded leader-shared state and publish its deterministic rule merge."""

    def __init__(self, charm: Any):
        """Bind the bridge to a charm while leaving parsing and merging independently testable."""
        self._charm = charm

    def _peer_relation(self) -> Any | None:
        """Return the single peer relation used for leader-shared rule state."""
        return self._charm.model.get_relation("gateway-peers")

    def _read_cache(self) -> _RuleCache:
        """Read the bounded peer application cache, failing closed on corruption."""
        peer = self._peer_relation()
        if peer is None:
            return _RuleCache(snapshots={}, accepted='{"groups":[]}')
        raw = peer.data[self._charm.app].get(CACHE_KEY, "")
        if not raw:
            return _RuleCache(snapshots={}, accepted='{"groups":[]}')
        try:
            return _decode_cache(raw)
        except InvalidRuleCacheError as exc:
            logger.warning("Ignoring invalid Mimir rule bridge cache: %s", exc)
            return _RuleCache(snapshots={}, accepted='{"groups":[]}')

    def _write_cache(self, cache: _RuleCache) -> bool:
        """Replace peer application cache when leader and within safe bounds."""
        peer = self._peer_relation()
        if peer is None or not self._charm.unit.is_leader():
            return peer is None
        try:
            encoded = _encode_cache(cache)
        except InvalidRuleCacheError as exc:
            logger.warning("Retaining previous Mimir rule bridge cache: %s", exc)
            return False
        peer.data[self._charm.app][CACHE_KEY] = encoded
        return True

    def reconcile(
        self,
        *,
        excluded_relation_id: int | None = None,
        excluded_destination_id: int | None = None,
    ) -> RuleBridgeResult:
        """Validate bounded upstream state and publish the leader-owned accepted aggregate."""
        previous = self._read_cache()
        current_relations = sorted(
            (
                relation
                for relation in self._charm.model.relations.get("receive-remote-write", [])
                if relation.id != excluded_relation_id and relation.app is not None
            ),
            key=lambda relation: relation.id,
        )
        if len(current_relations) > MAX_SOURCE_RELATIONS:
            logger.warning(
                "Only the first %s Mimir rule source relations are admitted; %s were present",
                MAX_SOURCE_RELATIONS,
                len(current_relations),
            )
        admitted = current_relations[:MAX_SOURCE_RELATIONS]
        admitted_ids = {relation.id for relation in admitted}
        snapshots = {
            relation_id: groups
            for relation_id, groups in previous.snapshots.items()
            if relation_id in admitted_ids
        }
        for relation in admitted:
            raw = relation.data[relation.app].get(ALERT_RULES_KEY, '{"groups":[]}')
            try:
                snapshots[relation.id] = parse_rule_groups(raw)
            except InvalidRuleDocumentError as exc:
                logger.warning(
                    "Retaining last valid Mimir rules for upstream relation %s when present: %s",
                    relation.id,
                    exc,
                )

        accepted = previous.accepted
        try:
            accepted = serialize_rule_groups(merge_rule_groups(snapshots))
        except InvalidRuleDocumentError as exc:
            logger.warning("Retaining last accepted downstream Mimir rule state: %s", exc)
        next_cache = _RuleCache(snapshots=snapshots, accepted=accepted)
        if not self._write_cache(next_cache):
            accepted = previous.accepted

        destinations = [
            relation
            for relation in self._charm.model.relations.get("mimir-alert-rules", [])
            if relation.id != excluded_destination_id
        ]
        if self._charm.unit.is_leader():
            for relation in destinations:
                relation.data[self._charm.app][ALERT_RULES_KEY] = accepted
        return RuleBridgeResult(
            accepted_has_rules=bool(parse_rule_groups(accepted)),
            destination_present=bool(destinations),
        )
