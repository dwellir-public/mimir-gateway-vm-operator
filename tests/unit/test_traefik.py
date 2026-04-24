import traefik


def test_write_static_config_updates_file(tmp_path, monkeypatch):
    path = tmp_path / "traefik.yml"
    monkeypatch.setattr(traefik, "TRAEFIK_STATIC_CONFIG_PATH", path)
    assert traefik.write_static_config("entryPoints:\n") is True
    assert traefik.write_static_config("entryPoints:\n") is False


def test_write_dynamic_config_creates_relation_file(tmp_path, monkeypatch):
    dynamic_dir = tmp_path / "dynamic"
    dynamic_dir.mkdir()
    monkeypatch.setattr(traefik, "TRAEFIK_DYNAMIC_DIR", dynamic_dir)
    assert traefik.write_dynamic_config("relation-7.yml", "http:\n") is True
    assert (dynamic_dir / "relation-7.yml").exists()


def test_write_systemd_unit_updates_file(tmp_path, monkeypatch):
    path = tmp_path / "traefik.service"
    monkeypatch.setattr(traefik, "TRAEFIK_SYSTEMD_UNIT_PATH", path)
    assert traefik.write_systemd_unit("[Unit]\n") is True
    assert traefik.write_systemd_unit("[Unit]\n") is False


def test_prune_dynamic_configs_removes_unmanaged_files(tmp_path, monkeypatch):
    dynamic_dir = tmp_path / "dynamic"
    dynamic_dir.mkdir()
    keep = dynamic_dir / "keep.yml"
    prune = dynamic_dir / "prune.yml"
    keep.write_text("keep", encoding="utf-8")
    prune.write_text("prune", encoding="utf-8")
    monkeypatch.setattr(traefik, "TRAEFIK_DYNAMIC_DIR", dynamic_dir)

    assert traefik.prune_dynamic_configs(keep={"keep.yml"}) is True
    assert keep.exists()
    assert not prune.exists()
