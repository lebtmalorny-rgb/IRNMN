from types import SimpleNamespace

import pytest

from masakari import exception
from masakari_ironic_fence import config
from masakari_ironic_fence.task import IronicFenceTask


class FakeBaremetal:
    def __init__(self):
        self.node = SimpleNamespace(
            id="node-1",
            name="compute-01",
            power_state="power on",
            target_power_state=None,
            last_error=None,
        )
        self.nodes_result = [self.node]
        self.states = []
        self.poll_count = 0
        self.requested_targets = []

    def nodes(self, details=True):
        assert details is True
        return list(self.nodes_result)

    def get_node(self, node):
        assert node.id == "node-1"
        if self.states:
            state = self.states.pop(0)
            if isinstance(state, dict):
                for key, value in state.items():
                    setattr(self.node, key, value)
            else:
                self.node.power_state = state
        self.poll_count += 1
        return self.node

    def set_node_power_state(self, node, target, wait=False):
        assert node.id == "node-1"
        assert wait is False
        self.requested_targets.append(target)
        self.node.target_power_state = target


@pytest.fixture
def ironic():
    return FakeBaremetal()


@pytest.fixture
def task(ironic):
    conf = SimpleNamespace(
        powerops_ironic=SimpleNamespace(
            power_timeout=10,
            poll_interval=1,
            stable_off_observations=2,
        )
    )
    connection = SimpleNamespace(baremetal=ironic)
    return IronicFenceTask(
        context=object(),
        novaclient=object(),
        connection_factory=lambda: connection,
        sleep=lambda _: None,
        conf=conf,
    )


def test_transient_power_off_resets_stability_counter(task, ironic):
    ironic.states = ["power off", "power on", "power off", "power off"]
    result = task.execute("compute-01")
    assert result == {
        "host": "compute-01",
        "node_uuid": "node-1",
        "power_state": "power off",
        "stable_observations": 2,
    }
    assert ironic.poll_count == 4
    assert ironic.requested_targets == ["power off"]


def test_timeout_blocks_recovery(task, ironic):
    ironic.states = ["power on"] * 20
    with pytest.raises(exception.HostRecoveryFailureException, match="not confirmed"):
        task.execute("compute-01")


def test_revert_never_sends_power_on(task, ironic):
    task.revert("compute-01")
    assert ironic.requested_targets == []
    assert ironic.poll_count == 0


@pytest.mark.parametrize("count", (0, 2))
def test_zero_or_duplicate_exact_node_match_blocks_recovery(task, ironic, count):
    ironic.nodes_result = [ironic.node] * count
    with pytest.raises(exception.HostRecoveryFailureException, match="exactly one"):
        task.execute("compute-01")
    assert ironic.requested_targets == []


def test_last_error_blocks_recovery_without_exposing_driver_info(task, ironic):
    ironic.states = [
        {
            "power_state": "power on",
            "target_power_state": "power off",
            "last_error": "BMC password example-only-redfish-password failed",
        }
    ]
    with pytest.raises(exception.HostRecoveryFailureException) as caught:
        task.execute("compute-01")
    assert "example-only-redfish-password" not in str(caught.value)


def test_conflicting_target_blocks_recovery(task, ironic):
    ironic.node.target_power_state = "power on"
    with pytest.raises(exception.HostRecoveryFailureException, match="conflicting"):
        task.execute("compute-01")
    assert ironic.requested_targets == []


def test_constructor_matches_masakari_entrypoint_contract(task):
    assert task.requires == {"host_name"}
    assert task.provides == {"ironic_fence_result"}


def test_connection_uses_masakari_privileged_service_credentials(monkeypatch):
    captured = {}
    conf = SimpleNamespace(
        os_privileged_user_auth_url="https://keystone.internal/v3",
        os_privileged_user_name="nova",
        os_privileged_user_password="secret",
        os_privileged_user_tenant="service",
        os_user_domain_name="Default",
        os_project_domain_name="Default",
        os_region_name="RegionOne",
        nova_api_insecure=False,
        nova_ca_certificates_file="/etc/ssl/certs/openstack.pem",
    )

    monkeypatch.setattr(
        config.openstack.connection,
        "Connection",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    config.connection_from_conf(conf)

    assert captured == {
        "auth_url": "https://keystone.internal/v3",
        "username": "nova",
        "password": "secret",
        "project_name": "service",
        "user_domain_name": "Default",
        "project_domain_name": "Default",
        "region_name": "RegionOne",
        "interface": "internal",
        "verify": "/etc/ssl/certs/openstack.pem",
    }
