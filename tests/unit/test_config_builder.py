import pytest

from config_builder import format_backend_url, render_dynamic_config, render_static_config


def test_render_static_config_includes_entrypoint():
    rendered = render_static_config(entrypoint_port=80)
    assert 'address: ":80"' in rendered


def test_render_dynamic_config_includes_shared_push_and_query_routes():
    rendered = render_dynamic_config(
        route_name="shared",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert rendered.splitlines()[1:8] == [
        "  routers:",
        "    shared-push:",
        "      entryPoints:",
        "        - web",
        '      rule: "PathPrefix(`/api/v1/push`)"',
        "      service: shared",
        "    shared-query:",
    ]
    assert '      rule: "PathPrefix(`/prometheus`)"' in rendered
    assert "X-Scope-OrgID" not in rendered


def test_render_dynamic_config_uses_shared_non_tenant_paths():
    rendered = render_dynamic_config(
        route_name="shared",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert 'rule: "PathPrefix(`/api/v1/push`)"' in rendered
    assert 'rule: "PathPrefix(`/prometheus`)"' in rendered
    assert "/tenants/" not in rendered


def test_render_dynamic_config_does_not_render_tenant_strip_prefix():
    rendered = render_dynamic_config(
        route_name="shared",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert "stripPrefix" not in rendered


def test_render_dynamic_config_rejects_empty_backend_urls():
    with pytest.raises(ValueError, match="backend_urls"):
        render_dynamic_config(
            route_name="shared",
            backend_urls=[],
        )


def test_render_dynamic_config_rejects_blank_backend_url_entries():
    with pytest.raises(ValueError, match="backend_urls"):
        render_dynamic_config(
            route_name="shared",
            backend_urls=["http://10.0.0.10:9009", ""],
        )


def test_render_dynamic_config_renders_multiple_backends():
    rendered = render_dynamic_config(
        route_name="shared",
        backend_urls=["http://10.0.0.10:9009", "http://10.0.0.11:9009"],
    )
    assert [line for line in rendered.splitlines() if '          - url: "' in line] == [
        '          - url: "http://10.0.0.10:9009"',
        '          - url: "http://10.0.0.11:9009"',
    ]


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("route_name", {"route_name": 'relation-7"\n  bad:'}),
        ("backend_urls", {"backend_urls": ["http://10.0.0.10:9009\nbad"]}),
    ],
)
def test_render_dynamic_config_rejects_invalid_interpolated_values(field_name, kwargs):
    call_kwargs = {
        "route_name": "shared",
        "backend_urls": ["http://10.0.0.10:9009"],
    }
    call_kwargs.update(kwargs)
    with pytest.raises(ValueError, match=field_name):
        render_dynamic_config(**call_kwargs)


def test_format_backend_url_brackets_ipv6():
    assert (
        format_backend_url(scheme="http", host="2001:db8::1", port=9009)
        == "http://[2001:db8::1]:9009"
    )
