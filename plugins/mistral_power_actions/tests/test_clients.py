from types import SimpleNamespace

from openstack_power_actions import clients


def test_connection_uses_service_auth_and_powerops_endpoint_options(monkeypatch):
    captured = {}

    def fake_connection(**kwargs):
        captured.update(kwargs)
        return "connection"

    monkeypatch.setattr(clients.openstack.connection, "Connection", fake_connection)
    conf = SimpleNamespace(
        keystone_authtoken=SimpleNamespace(
            auth_url="https://keystone.example/v3",
            username="mistral",
            password="service-password",
            project_name="service",
            user_domain_name="Default",
            project_domain_name="Default",
        ),
        powerops=SimpleNamespace(
            region_name="RegionOne", interface="internal", verify=True
        ),
    )

    assert clients.connection_from_conf(conf) == "connection"
    assert captured == {
        "auth_url": "https://keystone.example/v3",
        "username": "mistral",
        "password": "service-password",
        "project_name": "service",
        "user_domain_name": "Default",
        "project_domain_name": "Default",
        "region_name": "RegionOne",
        "interface": "internal",
        "verify": True,
    }


def test_redis_url_is_registered_as_secret():
    option = next(item for item in clients.POWEROPS_OPTS if item.name == "redis_url")
    assert option.secret is True
    password = next(
        item for item in clients.POWEROPS_OPTS if item.name == "redis_password"
    )
    assert password.secret is True
    assert next(item for item in clients.POWEROPS_OPTS if item.name == "lock_ttl").default == 900
