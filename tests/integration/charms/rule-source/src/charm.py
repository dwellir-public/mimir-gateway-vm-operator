#!/usr/bin/env python3
"""Publish synthetic rules for exercising a gateway's integration lifecycle."""

import json

import ops


class RuleSource(ops.CharmBase):
    """A workload-free remote-write consumer used only by integration tests."""

    def __init__(self, framework):
        super().__init__(framework)
        for event in (
            self.on.config_changed,
            self.on.leader_elected,
            self.on.send_remote_write_relation_created,
            self.on.send_remote_write_relation_joined,
        ):
            framework.observe(event, self._publish)

    def _publish(self, _event):
        """Publish one identifiable group, with a generation-dependent expression."""
        if self.unit.is_leader():
            group = {
                "name": str(self.config["rule-name"]),
                "rules": [
                    {
                        "alert": "GatewayLifecycleSynthetic",
                        "expr": f"vector({self.config['generation']}) < 0",
                    }
                ],
            }
            for relation in self.model.relations["send-remote-write"]:
                relation.data[self.app]["alert_rules"] = json.dumps({"groups": [group]})
        self.unit.status = ops.ActiveStatus("test rule source ready")


if __name__ == "__main__":
    ops.main(RuleSource)
