"""Provider-side helpers for prometheus remote-write publication."""

import json
import logging

import ops

LEGACY_METADATA_KEYS = ("tenant-id", "application", "model", "model_uuid")
ALERT_RULES_KEY = "alert_rules"
logger = logging.getLogger(__name__)


class RemoteWriteProvider:
    """Publish remote-write endpoint data for related consumers."""

    def __init__(self, charm):
        self._charm = charm

    def publish(
        self, *, relation_urls: dict[int, str], relation: ops.Relation | None = None
    ) -> None:
        """Publish relation-specific gateway write URLs on provider relations."""
        relations = self._charm.model.relations.get("receive-remote-write", [])
        if relation is not None:
            selected = [current for current in relations if current.id == relation.id]
            relations = selected or relations
        for current in relations:
            if not current.active:
                continue
            try:
                self._publish_relation(current, relation_urls.get(current.id))
            except ops.ModelError as exc:
                logger.warning("Cannot publish remote-write source %s: %s", current.id, exc)

    def _publish_relation(self, relation: ops.Relation, url: str | None) -> None:
        """Update one source without letting its removal interrupt other consumers."""
        if self._charm.unit.is_leader():
            for key in LEGACY_METADATA_KEYS:
                relation.data[self._charm.app].pop(key, None)
        if url is None:
            relation.data[self._charm.unit].pop("remote_write", None)
        else:
            relation.data[self._charm.unit]["remote_write"] = json.dumps({"url": url})

    def clear(self) -> None:
        """Clear published remote-write endpoint data from all relations."""
        self.publish(relation_urls={})
