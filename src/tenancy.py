"""Tenant-id mapping helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass

TENANT_PATH_PREFIX = "/tenants/"
_MIMIR_TENANT_ID_PATTERN = re.compile(r"^[0-9A-Za-z!_\-.*'()]+$")


@dataclass(frozen=True)
class TenantIdentity:
    """Resolved tenant identity plus the source used to derive it."""

    tenant_id: str
    source: str


def _normalize_tenant_component(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def validate_tenant_id(tenant_id: str) -> str:
    """Validate a tenant id against Mimir's accepted identifier rules."""
    if len(tenant_id) > 150 or tenant_id in {".", "..", "__mimir_cluster"}:
        raise ValueError("invalid tenant-id provided in relation data")
    if not _MIMIR_TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise ValueError("invalid tenant-id provided in relation data")
    return tenant_id


def tenant_path_prefix(*, tenant_id: str) -> str:
    """Return the routing path prefix for a validated tenant id."""
    return f"{TENANT_PATH_PREFIX}{validate_tenant_id(tenant_id)}"


def _short_model_uuid(remote_model_uuid: str) -> str:
    normalized = _normalize_tenant_component(remote_model_uuid).replace("-", "")
    return normalized[:8]


def fallback_tenant_id(*, remote_app_name: str, remote_model_uuid: str) -> str:
    """Derive the fallback tenant id from relation metadata."""
    base = remote_app_name
    short_model_uuid = _short_model_uuid(remote_model_uuid)
    if short_model_uuid:
        base = f"{remote_app_name}-{short_model_uuid}"
    tenant_id = _normalize_tenant_component(base)
    if not tenant_id:
        raise ValueError("unable to derive a valid tenant id from relation metadata")
    return validate_tenant_id(tenant_id)


def _explicit_tenant_id(value: str) -> str:
    tenant_id = value.strip()
    if not tenant_id:
        return ""
    if tenant_id != value:
        raise ValueError("invalid tenant-id provided in relation data")
    return validate_tenant_id(tenant_id)


def effective_tenant_id(
    *,
    relation_app_data: dict[str, str],
    remote_app_name: str,
    remote_model_uuid: str,
) -> str:
    """Return the explicit tenant id when present, else the metadata fallback."""
    return resolve_tenant_identity(
        relation_app_data=relation_app_data,
        remote_app_name=remote_app_name,
        remote_model_uuid=remote_model_uuid,
    ).tenant_id


def resolve_tenant_identity(
    *,
    relation_app_data: dict[str, str],
    remote_app_name: str,
    remote_model_uuid: str,
) -> TenantIdentity:
    """Resolve the effective tenant id and record whether it was explicit or derived."""
    explicit = _explicit_tenant_id(str(relation_app_data.get("tenant-id", "")))
    if explicit:
        return TenantIdentity(tenant_id=explicit, source="explicit")
    return TenantIdentity(
        tenant_id=fallback_tenant_id(
            remote_app_name=remote_app_name,
            remote_model_uuid=remote_model_uuid,
        ),
        source="derived",
    )
