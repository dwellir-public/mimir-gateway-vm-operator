"""Provider-side helpers for prometheus remote-write publication."""

import json

LEGACY_METADATA_KEYS = ("tenant-id", "application", "model", "model_uuid")


class RemoteWriteProvider:
    """Publish remote-write endpoint data for related consumers."""

    def __init__(self, charm):
        self._charm = charm

    def publish(self, *, relation_urls: dict[int, str]) -> None:
        """Publish relation-specific gateway write URLs on provider relations."""
        for relation in self._charm.model.relations.get("receive-remote-write", []):
            for key in LEGACY_METADATA_KEYS:
                relation.data[self._charm.app].pop(key, None)
            url = relation_urls.get(relation.id)
            if url is None:
                relation.data[self._charm.unit].pop("remote_write", None)
                continue
            relation.data[self._charm.unit]["remote_write"] = json.dumps({"url": url})

    def clear(self) -> None:
        """Clear published remote-write endpoint data from all relations."""
        for relation in self._charm.model.relations.get("receive-remote-write", []):
            for key in LEGACY_METADATA_KEYS:
                relation.data[self._charm.app].pop(key, None)
            relation.data[self._charm.unit].pop("remote_write", None)
