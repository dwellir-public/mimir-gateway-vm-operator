import pytest

from tenancy import effective_tenant_id, fallback_tenant_id


def test_explicit_tenant_id_wins():
    assert (
        effective_tenant_id(
            relation_app_data={"tenant-id": "Team_A-1"},
            remote_app_name="alloy-vm",
            remote_model_uuid="1234abcd",
        )
        == "Team_A-1"
    )


def test_whitespace_only_explicit_tenant_id_falls_back():
    assert (
        effective_tenant_id(
            relation_app_data={"tenant-id": "   "},
            remote_app_name="alloy-vm",
            remote_model_uuid="",
        )
        == "alloy-vm"
    )


def test_explicit_tenant_id_with_surrounding_whitespace_is_rejected():
    with pytest.raises(ValueError, match="tenant-id"):
        effective_tenant_id(
            relation_app_data={"tenant-id": " Team A / Ops! "},
            remote_app_name="alloy-vm",
            remote_model_uuid="1234abcd",
        )


def test_explicit_tenant_id_rejects_reserved_value():
    with pytest.raises(ValueError, match="tenant-id"):
        effective_tenant_id(
            relation_app_data={"tenant-id": "__mimir_cluster"},
            remote_app_name="alloy-vm",
            remote_model_uuid="1234abcd",
        )


def test_explicit_tenant_id_rejects_overlong_value():
    with pytest.raises(ValueError, match="tenant-id"):
        effective_tenant_id(
            relation_app_data={"tenant-id": "a" * 151},
            remote_app_name="alloy-vm",
            remote_model_uuid="1234abcd",
        )


def test_explicit_tenant_id_rejects_invalid_characters():
    with pytest.raises(ValueError, match="tenant-id"):
        effective_tenant_id(
            relation_app_data={"tenant-id": "Team_A-1/ops"},
            remote_app_name="alloy-vm",
            remote_model_uuid="1234abcd",
        )


def test_same_model_fallback_uses_app_name():
    assert (
        effective_tenant_id(
            relation_app_data={},
            remote_app_name="alloy-vm",
            remote_model_uuid="",
        )
        == "alloy-vm"
    )


def test_cross_model_fallback_uses_app_name_and_short_model_uuid():
    assert (
        effective_tenant_id(
            relation_app_data={},
            remote_app_name="opentelemetry-collector",
            remote_model_uuid="f794060e-b2d7-43ba-81d5-1a028c1c748d",
        )
        == "opentelemetry-collector-f794060e"
    )


def test_fallback_normalizes_invalid_characters():
    assert (
        effective_tenant_id(
            relation_app_data={},
            remote_app_name="Telemetry Collector",
            remote_model_uuid="MODEL_UUID",
        )
        == "telemetry-collector-modeluui"
    )


def test_fallback_tenant_id_normalizes_app_name_when_model_uuid_missing():
    assert (
        fallback_tenant_id(remote_app_name="Telemetry Collector", remote_model_uuid="")
        == "telemetry-collector"
    )


def test_fallback_tenant_id_allows_150_character_tenant_ids():
    tenant_id = "a" * 150

    assert fallback_tenant_id(remote_app_name=tenant_id, remote_model_uuid="") == tenant_id


def test_fallback_tenant_id_rejects_overlong_tenant_ids():
    with pytest.raises(ValueError, match="invalid tenant-id"):
        fallback_tenant_id(remote_app_name="a" * 151, remote_model_uuid="")


def test_fallback_tenant_id_rejects_inputs_that_normalize_to_empty():
    with pytest.raises(ValueError, match="tenant id"):
        fallback_tenant_id(remote_app_name="!!!", remote_model_uuid="@@@")
