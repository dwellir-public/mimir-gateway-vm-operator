"""Provider-side helpers for prometheus remote-write publication."""

import json


class RemoteWriteProvider:
    """Publish remote-write endpoint data for related consumers."""

    def __init__(self, charm):
        self._charm = charm

    def publish(self, *, relation_urls: dict[int, str]) -> None:
        """Publish relation-specific gateway write URLs on provider relations."""
        for relation in self._charm.model.relations.get("receive-remote-write", []):
            url = relation_urls.get(relation.id)
            if url is None:
                relation.data[self._charm.unit].pop("remote_write", None)
                continue
            relation.data[self._charm.unit]["remote_write"] = json.dumps({"url": url})

    def clear(self) -> None:
        """Clear published remote-write endpoint data from all relations."""
        for relation in self._charm.model.relations.get("receive-remote-write", []):
            relation.data[self._charm.unit].pop("remote_write", None)
