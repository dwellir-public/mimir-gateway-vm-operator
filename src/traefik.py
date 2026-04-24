"""Filesystem helpers for managing the Traefik workload."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path
from urllib.request import urlretrieve

from config_builder import (
    TRAEFIK_CONFIG_DIR,
    TRAEFIK_DYNAMIC_DIR,
    TRAEFIK_STATIC_CONFIG_PATH,
    TRAEFIK_SYSTEMD_UNIT_PATH,
)

TRAEFIK_BINARY_PATH = Path("/usr/local/bin/traefik")
TRAEFIK_VERSION = "3.6.2"
TRAEFIK_DOWNLOAD_URL = (
    "https://github.com/traefik/traefik/releases/download/"
    f"v{TRAEFIK_VERSION}/traefik_v{TRAEFIK_VERSION}_linux_amd64.tar.gz"
)


def _run(cmd: Iterable[str], *, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command with a noninteractive environment."""
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    return subprocess.run(
        list(cmd),
        check=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def install() -> None:
    """Install Traefik onto the machine."""
    _run(["apt-get", "update"])
    _run(["apt-get", "-y", "install", "ca-certificates"])
    ensure_directories()
    with tempfile.TemporaryDirectory(prefix="traefik-install-") as tmpdir:
        archive_path = Path(tmpdir) / "traefik.tar.gz"
        extracted_path = Path(tmpdir) / "extracted"
        extracted_path.mkdir()
        urlretrieve(TRAEFIK_DOWNLOAD_URL, archive_path)
        with tarfile.open(archive_path) as tar:
            tar.extractall(extracted_path, filter="data")
        binary = extracted_path / "traefik"
        shutil.copy2(binary, TRAEFIK_BINARY_PATH)
        TRAEFIK_BINARY_PATH.chmod(0o755)
    _run([str(TRAEFIK_BINARY_PATH), "version"])


def ensure_directories() -> None:
    """Ensure the Traefik config directories exist."""
    TRAEFIK_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TRAEFIK_DYNAMIC_DIR.mkdir(parents=True, exist_ok=True)


def write_static_config(content: str) -> bool:
    """Write the static Traefik config when it has changed."""
    current = (
        TRAEFIK_STATIC_CONFIG_PATH.read_text(encoding="utf-8")
        if TRAEFIK_STATIC_CONFIG_PATH.exists()
        else None
    )
    if current == content:
        return False
    TRAEFIK_STATIC_CONFIG_PATH.write_text(content, encoding="utf-8")
    return True


def write_dynamic_config(filename: str, content: str) -> bool:
    """Write one dynamic Traefik config fragment when it has changed."""
    path = TRAEFIK_DYNAMIC_DIR / filename
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def write_systemd_unit(content: str) -> bool:
    """Write the Traefik systemd unit when it has changed."""
    current = (
        TRAEFIK_SYSTEMD_UNIT_PATH.read_text(encoding="utf-8")
        if TRAEFIK_SYSTEMD_UNIT_PATH.exists()
        else None
    )
    if current == content:
        return False
    TRAEFIK_SYSTEMD_UNIT_PATH.write_text(content, encoding="utf-8")
    return True


def prune_dynamic_configs(*, keep: set[str]) -> bool:
    """Remove dynamic config fragments that are no longer desired."""
    changed = False
    if not TRAEFIK_DYNAMIC_DIR.exists():
        return False
    for path in TRAEFIK_DYNAMIC_DIR.glob("*.yml"):
        if path.name in keep:
            continue
        path.unlink()
        changed = True
    return changed


def daemon_reload() -> None:
    """Reload systemd manager configuration."""
    _run(["systemctl", "daemon-reload"])


def enable() -> None:
    """Enable the Traefik service."""
    _run(["systemctl", "enable", "traefik"])


def start() -> None:
    """Start the Traefik service."""
    _run(["systemctl", "start", "traefik"])


def restart() -> None:
    """Restart the Traefik service."""
    _run(["systemctl", "restart", "traefik"])


def is_active() -> bool:
    """Return whether the Traefik service is active."""
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", "traefik"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def get_version() -> str | None:
    """Return the installed Traefik version."""
    if not TRAEFIK_BINARY_PATH.exists():
        return None
    result = subprocess.run(
        [str(TRAEFIK_BINARY_PATH), "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("Version:"):
            return line.split(":", 1)[1].strip()
    return None
