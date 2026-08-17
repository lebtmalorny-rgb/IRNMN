from types import SimpleNamespace

import pytest

from openstack_power_actions.operations import (
    HostNotEmpty,
    HostReturnNotSafe,
    MappingError,
    PowerOperations,
    PowerTimeout,
    UnsupportedInstancePolicy,
)


class FakeBaremetal:
    def __init__(self):
        self.node = SimpleNamespace(
            id="node-1",
            name="compute-01",
            power_state="power on",
            target_power_state=None,
            last_error=None,
        )
        self.states = []
        self.read_count = 0
        self.requests = []

    def nodes(self, details=True):
        assert details is True
        return [self.node]

    def get_node(self, node):
        assert node.id == "node-1"
        if self.states:
            state = self.states.pop(0)
            if isinstance(state, dict):
                for key, value in state.items():
                    setattr(self.node, key, value)
            else:
                self.node.power_state = state
        self.read_count += 1
        return self.node

    def set_node_power_state(self, node, target, wait=False):
        assert wait is False
        self.requests.append((node.id, target))
        self.node.target_power_state = target
        return self.node


class FakeCompute:
    def __init__(self):
        self.service = SimpleNamespace(
            id="service-1", host="compute-01", binary="nova-compute",
            status="enabled", state="up"
        )
        self.servers_now = []
        self.server_snapshots = []
        self.disabled = []
        self.enabled = []
        self.live_migrations = []
        self.stops = []

    def services(self, **filters):
        assert filters["host"] == "compute-01"
        return [self.service]

    def disable_service(self, service, disabled_reason=None):
        self.disabled.append((service.id, disabled_reason))
        service.status = "disabled"
        return service

    def enable_service(self, service):
        self.enabled.append(service.id)
        service.status = "enabled"
        return service

    def servers(self, **filters):
        assert filters["all_projects"] is True
        if self.server_snapshots:
            self.servers_now = self.server_snapshots.pop(0)
        return list(self.servers_now)

    def live_migrate_server(self, server, **kwargs):
        self.live_migrations.append(server.id)

    def stop_server(self, server):
        self.stops.append(server.id)


class FakeMasakari:
    def __init__(self):
        self.segment = SimpleNamespace(id="segment-1", name="powerops-compute")
        self.host = SimpleNamespace(
            id="masakari-host-1", name="compute-01", on_maintenance=False
        )

    def segments(self):
        return [self.segment]

    def hosts(self, segment):
        assert segment.id == "segment-1"
        return [self.host]

    def update_host(self, host, segment, **attrs):
        assert segment.id == "segment-1"
        for key, value in attrs.items():
            setattr(host, key, value)
        return host


class FakeNetwork:
    def __init__(self):
        self.items = []

    def agents(self, **filters):
        assert filters["host"] == "compute-01"
        return self.items


@pytest.fixture
def connection():
    return SimpleNamespace(
        baremetal=FakeBaremetal(),
        compute=FakeCompute(),
        instance_ha=FakeMasakari(),
        network=FakeNetwork(),
    )


def server(server_id, host="compute-01", status="ACTIVE"):
    return SimpleNamespace(
        id=server_id, name=server_id, status=status, hypervisor_hostname=host
    )


def test_wait_power_requires_consecutive_off_observations(connection):
    connection.baremetal.states = [
        "power on", "power off", "power on", "power off", "power off", "power off"
    ]
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.wait_power(
        "compute-01", "power off", timeout=30, interval=1, stable_observations=3
    )
    assert result["power_state"] == "power off"
    assert connection.baremetal.read_count == 6


def test_wait_power_fails_on_last_error(connection):
    connection.baremetal.states = [
        {"power_state": "power on", "last_error": "BMC unreachable"}
    ]
    operations = PowerOperations(connection, sleep=lambda _: None)
    with pytest.raises(PowerTimeout, match="BMC reported an error"):
        operations.wait_power("compute-01", "power off", 10, 1, 2)


def test_request_power_is_idempotent_when_target_is_already_set(connection):
    connection.baremetal.node.target_power_state = "power off"
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.request_power("compute-01", "power off")
    assert result["target_power_state"] == "power off"
    assert connection.baremetal.requests == []


def test_soft_power_off_is_idempotent_while_soft_target_is_pending(connection):
    connection.baremetal.node.target_power_state = "soft power off"
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.request_power("compute-01", "power off", soft=True)
    assert result["target_power_state"] == "soft power off"
    assert connection.baremetal.requests == []


def test_wait_off_accepts_soft_power_off_as_compatible_transition(connection):
    connection.baremetal.states = [
        {
            "power_state": "power on",
            "target_power_state": "soft power off",
            "last_error": None,
        },
        {"power_state": "power off", "target_power_state": None},
        {"power_state": "power off", "target_power_state": None},
    ]
    operations = PowerOperations(connection, sleep=lambda _: None)
    assert operations.wait_power("compute-01", "power off", 30, 1, 2)[
        "power_state"
    ] == "power off"


def test_resolve_host_rejects_duplicate_ironic_name(connection):
    connection.baremetal.nodes = lambda details=True: [
        connection.baremetal.node,
        SimpleNamespace(id="node-2", name="compute-01"),
    ]
    operations = PowerOperations(connection, sleep=lambda _: None)
    with pytest.raises(MappingError, match="exactly one Ironic Node"):
        operations.resolve_host("compute-01")


def test_stop_policy_waits_for_shutoff_without_migration(connection):
    active = server("server-1")
    stopped = server("server-1", status="SHUTOFF")
    connection.compute.server_snapshots = [[active], [stopped]]
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.drain_host("compute-01", "stop", timeout=30, interval=1)
    assert result["remaining_statuses"] == ["SHUTOFF"]
    assert connection.compute.stops == ["server-1"]
    assert connection.compute.live_migrations == []


def test_live_migrate_policy_waits_until_source_is_empty(connection):
    source = server("server-1")
    moved = server("server-1", host="compute-02")
    connection.compute.server_snapshots = [[source], [moved]]
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.drain_host(
        "compute-01", "live_migrate", timeout=30, interval=1
    )
    assert result["servers_on_source"] == []
    assert connection.compute.live_migrations == ["server-1"]


def test_unknown_drain_policy_never_changes_server(connection):
    connection.compute.servers_now = [server("server-1")]
    operations = PowerOperations(connection, sleep=lambda _: None)
    with pytest.raises(UnsupportedInstancePolicy):
        operations.drain_host("compute-01", "evacuate", timeout=30, interval=1)
    assert connection.compute.live_migrations == []
    assert connection.compute.stops == []


def test_assert_host_empty_can_explicitly_allow_only_shutoff(connection):
    connection.compute.servers_now = [server("server-1", status="SHUTOFF")]
    operations = PowerOperations(connection, sleep=lambda _: None)
    assert operations.assert_host_empty("compute-01", allow_shutoff=True)[
        "remaining_statuses"
    ] == ["SHUTOFF"]
    with pytest.raises(HostNotEmpty):
        operations.assert_host_empty("compute-01", allow_shutoff=False)


def test_verify_host_return_requires_stale_domain_confirmation(connection):
    operations = PowerOperations(connection, sleep=lambda _: None)
    with pytest.raises(HostReturnNotSafe, match="stale domain check is required"):
        operations.verify_host_return("compute-01", stale_domains_checked=False)


def test_fail_safe_leaves_nova_disabled_and_masakari_in_maintenance(connection):
    operations = PowerOperations(connection, sleep=lambda _: None)
    result = operations.fail_safe_host("compute-01")
    assert result == {
        "host": "compute-01",
        "nova_enabled": False,
        "on_maintenance": True,
    }
