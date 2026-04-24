"""Traefik configuration rendering helpers for the Mimir gateway charm."""

import re
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from tenancy import tenant_path_prefix, validate_tenant_id

TRAEFIK_CONFIG_DIR = Path("/etc/traefik")
TRAEFIK_DYNAMIC_DIR = TRAEFIK_CONFIG_DIR / "dynamic"
TRAEFIK_STATIC_CONFIG_PATH = TRAEFIK_CONFIG_DIR / "traefik.yml"
TRAEFIK_SYSTEMD_UNIT_PATH = Path("/etc/systemd/system/traefik.service")
_ROUTE_NAME_PATTERN = re.compile(r"^[0-9A-Za-z-]+$")


def render_static_config(*, entrypoint_port: int) -> str:
    """Render the static Traefik configuration."""
    return "\n".join(
        [
            "entryPoints:",
            "  web:",
            f'    address: ":{entrypoint_port}"',
            "",
            "providers:",
            "  file:",
            f"    directory: {TRAEFIK_DYNAMIC_DIR}",
            "    watch: true",
            "",
            "api:",
            "  dashboard: false",
            "",
            "log:",
            "  level: INFO",
            "",
        ]
    )


def _validate_route_name(route_name: str) -> str:
    if not _ROUTE_NAME_PATTERN.fullmatch(route_name):
        raise ValueError("route_name must contain only letters, digits, and hyphens")
    return route_name


def _validated_tenant_id(tenant_id: str) -> str:
    try:
        return validate_tenant_id(tenant_id)
    except ValueError as exc:
        raise ValueError("tenant_id must be a valid tenant identifier") from exc


def _validate_backend_urls(backend_urls: list[str]) -> list[str]:
    if not backend_urls:
        raise ValueError("backend_urls must not be empty")

    validated_urls = []
    for backend_url in backend_urls:
        if not backend_url or backend_url != backend_url.strip():
            raise ValueError("backend_urls must contain non-empty absolute URLs")
        if any(char in backend_url for char in {'"', "'", "\n", "\r", "\t", " "}):
            raise ValueError("backend_urls must contain non-empty absolute URLs")

        parsed = urlparse(backend_url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("backend_urls must contain non-empty absolute URLs") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("backend_urls must contain non-empty absolute URLs")
        validated_urls.append(backend_url)
    return validated_urls


def _tenant_router_rule(*, path_prefix: str) -> str:
    """Match only the exact tenant root or paths beneath that tenant segment."""
    return f"Path(`{path_prefix}`) || PathPrefix(`{path_prefix}/`)"


def render_dynamic_config(
    *,
    route_name: str,
    tenant_id: str,
    backend_urls: list[str],
) -> str:
    """Render one relation-scoped dynamic Traefik route."""
    route_name = _validate_route_name(route_name)
    tenant_id = _validated_tenant_id(tenant_id)
    path_prefix = tenant_path_prefix(tenant_id=tenant_id)
    backend_urls = _validate_backend_urls(backend_urls)

    lines = [
        "http:",
        "  routers:",
        f"    {route_name}:",
        "      entryPoints:",
        "        - web",
        f'      rule: "{_tenant_router_rule(path_prefix=path_prefix)}"',
        f"      service: {route_name}",
        f"      middlewares: [{route_name}-strip, {route_name}-tenant]",
        "",
        "  middlewares:",
        f"    {route_name}-strip:",
        "      stripPrefix:",
        "        prefixes:",
        f'          - "{path_prefix}"',
        "",
        f"    {route_name}-tenant:",
        "      headers:",
        "        customRequestHeaders:",
        f'          X-Scope-OrgID: "{tenant_id}"',
        "",
        "  services:",
        f"    {route_name}:",
        "      loadBalancer:",
        "        passHostHeader: true",
        "        servers:",
    ]
    for url in backend_urls:
        lines.append(f'          - url: "{url}"')
    lines.append("")
    return "\n".join(lines)


def format_backend_url(*, scheme: str, host: str, port: int) -> str:
    """Build a backend URL, bracketing IPv6 literals when needed."""
    try:
        ip = ip_address(host)
    except ValueError:
        netloc = f"{host}:{port}"
    else:
        if ip.version == 6:
            netloc = f"[{ip.compressed}]:{port}"
        else:
            netloc = f"{ip.compressed}:{port}"
    return f"{scheme}://{netloc}"


def render_systemd_unit() -> str:
    """Render the Traefik systemd unit file."""
    return "\n".join(
        [
            "[Unit]",
            "Description=Traefik",
            "Documentation=https://doc.traefik.io/traefik/",
            "After=network-online.target",
            "Wants=network-online.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart=/usr/local/bin/traefik --configFile={TRAEFIK_STATIC_CONFIG_PATH}",
            "Restart=always",
            "RestartSec=2",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
