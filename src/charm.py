#!/usr/bin/env python3
# Copyright 2026 Erik Lönroth
# See LICENSE file for licensing details.

"""Charm the Mimir gateway VM workload."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TypeGuard

import ops
from charms.prometheus_k8s.v1.prometheus_remote_write import PrometheusRemoteWriteConsumer

import traefik
from config_builder import (
    format_backend_url,
    render_dynamic_config,
    render_static_config,
    render_systemd_unit,
)
from remote_write import RemoteWriteProvider
from rule_bridge import PrometheusRuleBridge

logger = logging.getLogger(__name__)
RULE_DESTINATION_WAITING_MESSAGE = "waiting for Mimir alert-rule destination"


@dataclass(frozen=True)
class BackendState:
    """Normalized backend relation data."""

    urls: list[str]


class MimirGatewayVmCharm(ops.CharmBase):
    """Machine charm for a Traefik-based Mimir gateway."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.remote_write_provider = RemoteWriteProvider(self)
        self.remote_write_consumer = PrometheusRemoteWriteConsumer(
            self,
            relation_name="mimir-alert-rules",
            peer_relation_name="gateway-peers",
            forward_alert_rules=False,
        )
        self.rule_bridge = PrometheusRuleBridge(self)
        framework.observe(self.on.install, self._on_install)
        framework.observe(self.on.start, self._on_start)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.update_status, self._on_update_status)
        framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        framework.observe(self.on.leader_elected, self._on_leader_elected)
        for event in (
            self.on["mimir-alert-rules"].relation_created,
            self.on["mimir-alert-rules"].relation_joined,
            self.on["mimir-alert-rules"].relation_changed,
            self.on["mimir-alert-rules"].relation_departed,
            self.on["mimir-alert-rules"].relation_broken,
        ):
            framework.observe(event, self._on_rule_destination_changed)
        framework.observe(self.on.backend_relation_created, self._on_relation_event)
        framework.observe(self.on.backend_relation_joined, self._on_relation_event)
        framework.observe(self.on.backend_relation_changed, self._on_relation_event)
        framework.observe(self.on.backend_relation_broken, self._on_relation_event)
        framework.observe(self.on.receive_remote_write_relation_created, self._on_relation_event)
        framework.observe(self.on.receive_remote_write_relation_joined, self._on_relation_event)
        framework.observe(self.on.receive_remote_write_relation_changed, self._on_relation_event)
        framework.observe(self.on.receive_remote_write_relation_departed, self._on_relation_event)
        framework.observe(self.on.receive_remote_write_relation_broken, self._on_relation_event)
        framework.observe(self.on.grafana_source_relation_created, self._on_relation_event)
        framework.observe(self.on.grafana_source_relation_joined, self._on_relation_event)
        framework.observe(self.on.grafana_source_relation_changed, self._on_relation_event)
        framework.observe(self.on.grafana_source_relation_departed, self._on_relation_event)
        framework.observe(self.on.grafana_source_relation_broken, self._on_relation_event)
        framework.observe(
            self.on.show_gateway_routes_action,
            self._on_show_gateway_routes_action,
        )

    def _on_install(self, event: ops.InstallEvent) -> None:
        self.unit.status = ops.MaintenanceStatus("installing gateway")
        try:
            traefik.install()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to install Traefik")
            self.unit.status = ops.BlockedStatus(f"Install failed: {exc}")

    def _on_start(self, event: ops.StartEvent) -> None:
        try:
            backend = self._backend_state()
            self.unit.status = ops.MaintenanceStatus("starting gateway")
            backend_urls = backend.urls if backend is not None else []
            if not self._configure(backend_urls):
                return

            self._set_workload_version()
            self._publish_consumer_data()
            self._refresh_status()
        finally:
            self._reconcile_rules()

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        self._reconcile()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        self._set_workload_version()
        self._refresh_status()
        self._reconcile_rules()

    def _on_upgrade_charm(self, event: ops.UpgradeCharmEvent) -> None:
        self._reconcile()

    def _on_leader_elected(self, event: ops.LeaderElectedEvent) -> None:
        """Republish the complete rule snapshot after leadership changes."""
        self._reconcile_rules()

    def _on_rule_destination_changed(self, event: ops.RelationEvent) -> None:
        """Publish bridge state after the standard consumer finishes its relation handler."""
        excluded_destination_id = (
            event.relation.id if isinstance(event, ops.RelationBrokenEvent) else None
        )
        self._reconcile_rules(excluded_destination_id=excluded_destination_id)

    def _on_relation_event(self, event: ops.EventBase) -> None:
        if self._is_remote_write_relation_changed(event):
            self._on_remote_write_relation_changed(event)
            return
        excluded_rule_relation_id = (
            event.relation.id
            if isinstance(event, ops.RelationBrokenEvent)
            and event.relation.name == "receive-remote-write"
            else None
        )
        self._reconcile(excluded_rule_relation_id=excluded_rule_relation_id)

    def _is_remote_write_relation_changed(
        self, event: ops.EventBase
    ) -> TypeGuard[ops.RelationChangedEvent]:
        """Return whether the event is consumer data churn on remote-write."""
        return (
            isinstance(event, ops.RelationChangedEvent)
            and event.relation.name == "receive-remote-write"
        )

    def _on_remote_write_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Log remote-write consumer data changes without changing workload status."""
        self._set_workload_version()
        app_name = event.app.name if event.app is not None else ""
        unit_name = event.unit.name if event.unit is not None else ""
        app_keys = sorted(event.relation.data[event.app].keys()) if event.app is not None else []
        unit_keys = (
            sorted(event.relation.data[event.unit].keys()) if event.unit is not None else []
        )
        logger.info(
            "Ignoring remote-write consumer data change for relation %s: "
            "remote_app=%s remote_unit=%s app_data_keys=%s unit_data_keys=%s",
            event.relation.id,
            app_name,
            unit_name,
            app_keys,
            unit_keys,
        )
        self._reconcile_rules()

    def _reconcile(self, *, excluded_rule_relation_id: int | None = None) -> None:
        """Converge gateway routing, published endpoints, rules, and workload status."""
        try:
            backend = self._backend_state()
            self.unit.status = ops.MaintenanceStatus("configuring gateway")
            backend_urls = backend.urls if backend is not None else []
            if not self._configure(backend_urls):
                return
            self._set_workload_version()
            self._publish_consumer_data()
            self._refresh_status()
        finally:
            self._reconcile_rules(excluded_relation_id=excluded_rule_relation_id)

    def _reconcile_rules(
        self,
        *,
        excluded_relation_id: int | None = None,
        excluded_destination_id: int | None = None,
    ) -> None:
        """Converge bridged rules and apply their narrow status overlay."""
        result = self.rule_bridge.reconcile(
            excluded_relation_id=excluded_relation_id,
            excluded_destination_id=excluded_destination_id,
        )
        waiting_for_destination = result.accepted_has_rules and not result.destination_present
        if waiting_for_destination and isinstance(self.unit.status, ops.ActiveStatus):
            self.unit.status = ops.WaitingStatus(RULE_DESTINATION_WAITING_MESSAGE)
        elif (
            not waiting_for_destination
            and isinstance(self.unit.status, ops.WaitingStatus)
            and self.unit.status.message == RULE_DESTINATION_WAITING_MESSAGE
        ):
            self._refresh_status()

    def _backend_state(self) -> BackendState | None:
        relation = self.model.get_relation("backend")
        if relation is None or relation.app is None:
            return None
        raw_urls = str(relation.data[relation.app].get("urls", "")).strip()
        if not raw_urls:
            return None
        try:
            urls = json.loads(raw_urls)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(urls, list) or not all(isinstance(item, str) for item in urls):
            return None
        if not urls:
            return None
        return BackendState(urls=urls)

    def _remote_write_relations(self) -> list[ops.Relation]:
        return list(self.model.relations.get("receive-remote-write", []))

    def _relation_route_name(self, relation: ops.Relation) -> str:
        return f"relation-{relation.id}"

    def _relation_route_file(self, relation: ops.Relation) -> str:
        return f"{self._relation_route_name(relation)}.yml"

    def _relation_write_url(self, relation: ops.Relation) -> str:
        return f"{self._external_url_base()}/api/v1/push"

    def _relation_query_url(self, relation: ops.Relation) -> str:
        return f"{self._external_url_base()}/prometheus"

    def _on_show_gateway_routes_action(self, event: ops.ActionEvent) -> None:
        backend_state = self._backend_state()
        backend_urls = backend_state.urls if backend_state is not None else []
        mappings = []
        for relation in self._remote_write_relations():
            remote_app_name = relation.app.name if relation.app else ""
            mappings.append(
                {
                    "backend-urls": backend_urls,
                    "relation-id": relation.id,
                    "remote-app": remote_app_name,
                    "route-file": self._relation_route_file(relation),
                    "route-name": self._relation_route_name(relation),
                    "write-url": self._relation_write_url(relation),
                    "query-url": self._relation_query_url(relation),
                }
            )
        event.set_results(
            {"mappings": json.dumps(mappings, sort_keys=True, separators=(",", ":"))}
        )

    def _configure(self, backend_urls: list[str]) -> bool:
        """Render Traefik config and only restart for non-hot-reloadable changes."""
        try:
            traefik.ensure_directories()
            static_changed = traefik.write_static_config(render_static_config(entrypoint_port=80))
            unit_changed = traefik.write_systemd_unit(render_systemd_unit())
            rendered = self._render_relation_dynamic_configs(backend_urls)
            traefik.prune_dynamic_configs(keep=set(rendered))
            dynamic_changed = False
            for filename, content in rendered.items():
                dynamic_changed = (
                    traefik.write_dynamic_config(filename, content) or dynamic_changed
                )
            if unit_changed:
                traefik.daemon_reload()
                traefik.enable()
            service_active = traefik.is_active()
            if service_active:
                # Traefik's file provider hot-reloads dynamic route fragments in place.
                if static_changed or unit_changed:
                    traefik.restart()
            else:
                traefik.start()
        except OSError as exc:
            logger.warning("Unable to write Traefik config in current environment")
            self.unit.status = ops.BlockedStatus(f"Config failed: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to configure Traefik")
            self.unit.status = ops.BlockedStatus(f"Config failed: {exc}")
            return False
        return True

    def _render_relation_dynamic_configs(self, backend_urls: list[str]) -> dict[str, str]:
        rendered: dict[str, str] = {}
        for relation in self._remote_write_relations():
            filename = self._relation_route_file(relation)
            rendered[filename] = render_dynamic_config(
                route_name=self._relation_route_name(relation),
                backend_urls=backend_urls,
            )
        return rendered

    def _publish_consumer_data(self) -> None:
        relation_urls = {
            relation.id: self._relation_write_url(relation)
            for relation in self._remote_write_relations()
        }
        self.remote_write_provider.publish(relation_urls=relation_urls)
        query_url = (
            self._relation_query_url(self._remote_write_relations()[0]) if relation_urls else None
        )
        for relation in self.model.relations.get("grafana-source", []):
            if query_url is None:
                relation.data[self.unit].pop("grafana_source_host", None)
            else:
                relation.data[self.unit]["grafana_source_host"] = query_url

    def _set_workload_version(self) -> None:
        version = traefik.get_version()
        if version:
            self.unit.set_workload_version(version)

    def _refresh_status(self) -> None:
        """Update unit status from service health and currently related routing state."""
        if not traefik.is_active():
            self.unit.status = ops.WaitingStatus("Traefik service not running")
            return
        backend = self._backend_state()
        if backend is None:
            self.unit.status = ops.WaitingStatus("waiting for backend relation data")
            return
        backend_count = len(backend.urls)
        tenant_count = len(self._remote_write_relations())
        self.unit.status = ops.ActiveStatus(
            "gateway ready: "
            f"{backend_count} active {self._pluralize('backend', backend_count)}, "
            f"{tenant_count} {self._pluralize('consumer', tenant_count)} served"
        )

    def _pluralize(self, noun: str, count: int) -> str:
        """Return a singular or plural noun form using a simple count-based rule."""
        return noun if count == 1 else f"{noun}s"

    def _external_url_base(self) -> str:
        binding = self.model.get_binding("backend")
        if binding is None:
            return format_backend_url(scheme="http", host="127.0.0.1", port=80)
        network = binding.network
        if network is None:
            return format_backend_url(scheme="http", host="127.0.0.1", port=80)
        address = network.bind_address
        return format_backend_url(scheme="http", host=str(address), port=80)


if __name__ == "__main__":  # pragma: nocover
    ops.main(MimirGatewayVmCharm)
