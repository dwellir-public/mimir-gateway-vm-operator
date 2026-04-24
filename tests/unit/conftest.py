import pytest

import charm
import traefik


@pytest.fixture(autouse=True)
def isolate_traefik_side_effects(monkeypatch, tmp_path):
    """Prevent unit tests from touching the real host service or /etc paths."""
    config_dir = tmp_path / "etc-traefik"
    dynamic_dir = config_dir / "dynamic"
    static_config = config_dir / "traefik.yml"
    systemd_unit = tmp_path / "traefik.service"
    binary_path = tmp_path / "traefik"

    monkeypatch.setattr(traefik, "TRAEFIK_CONFIG_DIR", config_dir)
    monkeypatch.setattr(traefik, "TRAEFIK_DYNAMIC_DIR", dynamic_dir)
    monkeypatch.setattr(traefik, "TRAEFIK_STATIC_CONFIG_PATH", static_config)
    monkeypatch.setattr(traefik, "TRAEFIK_SYSTEMD_UNIT_PATH", systemd_unit)
    monkeypatch.setattr(traefik, "TRAEFIK_BINARY_PATH", binary_path)

    monkeypatch.setattr(charm.traefik, "install", lambda: None)
    monkeypatch.setattr(charm.traefik, "daemon_reload", lambda: None)
    monkeypatch.setattr(charm.traefik, "enable", lambda: None)
    monkeypatch.setattr(charm.traefik, "start", lambda: None)
    monkeypatch.setattr(charm.traefik, "restart", lambda: None)
    monkeypatch.setattr(charm.traefik, "is_active", lambda: False)
    monkeypatch.setattr(charm.traefik, "get_version", lambda: None)
