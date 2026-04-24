from typing import Any, cast

import pytest

from config_builder import format_backend_url, render_dynamic_config, render_static_config


def test_render_static_config_includes_entrypoint():
    rendered = render_static_config(entrypoint_port=80)
    assert 'address: ":80"' in rendered


def test_render_dynamic_config_includes_header_middleware():
    rendered = render_dynamic_config(
        route_name="relation-7",
        tenant_id="relation-7",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert rendered.splitlines()[1:8] == [
        "  routers:",
        "    relation-7:",
        "      entryPoints:",
        "        - web",
        '      rule: "Path(`/tenants/relation-7`) || PathPrefix(`/tenants/relation-7/`)"',
        "      service: relation-7",
        "      middlewares: [relation-7-strip, relation-7-tenant]",
    ]
    assert (
        "    relation-7-tenant:\n"
        "      headers:\n"
        "        customRequestHeaders:\n"
        '          X-Scope-OrgID: "relation-7"'
    ) in rendered


def test_render_dynamic_config_derives_tenant_specific_path_prefix_when_omitted():
    rendered = render_dynamic_config(
        route_name="relation-7",
        tenant_id="relation-7",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert 'rule: "Path(`/tenants/relation-7`) || PathPrefix(`/tenants/relation-7/`)"' in rendered


def test_render_dynamic_config_bounds_router_rule_to_tenant_path_segment():
    rendered = render_dynamic_config(
        route_name="team",
        tenant_id="team",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert 'rule: "Path(`/tenants/team`) || PathPrefix(`/tenants/team/`)"' in rendered
    assert 'PathPrefix(`/tenants/team`)"' not in rendered


def test_render_dynamic_config_strips_tenant_prefix_before_forwarding():
    rendered = render_dynamic_config(
        route_name="relation-7",
        tenant_id="relation-7",
        backend_urls=["http://10.0.0.10:9009"],
    )
    assert "stripPrefix:" in rendered
    assert '          - "/tenants/relation-7"' in rendered


def test_render_dynamic_config_rejects_path_prefix_override():
    with pytest.raises(TypeError, match="path_prefix"):
        cast(Any, render_dynamic_config)(
            route_name="relation-7",
            tenant_id="relation-7",
            backend_urls=["http://10.0.0.10:9009"],
            path_prefix="/tenants/relation-7",
        )


def test_render_dynamic_config_rejects_empty_backend_urls():
    with pytest.raises(ValueError, match="backend_urls"):
        render_dynamic_config(
            route_name="relation-7",
            tenant_id="relation-7",
            backend_urls=[],
        )


def test_render_dynamic_config_rejects_blank_backend_url_entries():
    with pytest.raises(ValueError, match="backend_urls"):
        render_dynamic_config(
            route_name="relation-7",
            tenant_id="relation-7",
            backend_urls=["http://10.0.0.10:9009", ""],
        )


def test_render_dynamic_config_renders_multiple_backends():
    rendered = render_dynamic_config(
        route_name="relation-7",
        tenant_id="relation-7",
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
        ("tenant_id", {"tenant_id": 'tenant"\n  bad:'}),
        ("backend_urls", {"backend_urls": ["http://10.0.0.10:9009\nbad"]}),
    ],
)
def test_render_dynamic_config_rejects_invalid_interpolated_values(field_name, kwargs):
    call_kwargs = {
        "route_name": "relation-7",
        "tenant_id": "relation-7",
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
