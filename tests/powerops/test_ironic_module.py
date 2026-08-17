import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "ansible/roles/powerops/library/powerops_ironic_node.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location("powerops_ironic_node", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def redfish_spec():
    return {
        "name": "compute-01",
        "nova_hostname": "compute-01",
        "driver": "redfish",
        "network_interface": "noop",
        "desired_provision_state": "manageable",
        "driver_info": {
            "redfish_address": "https://192.0.2.101",
            "redfish_system_id": "/redfish/v1/Systems/1",
            "redfish_username": "powerops",
            "redfish_password": "example-only-redfish-password",
            "redfish_verify_ca": False,
        },
        "ports": [
            {
                "address": "02:00:00:00:01:01",
                "physical_network": None,
            }
        ],
    }


class FakeBaremetal:
    def __init__(self):
        self.node_items = []
        self.port_items = []
        self.created_nodes = []
        self.updated_nodes = []
        self.created_ports = []
        self.updated_ports = []
        self.provision_targets = []

    def nodes(self, details=True):
        assert details is True
        return list(self.node_items)

    def create_node(self, **attrs):
        self.created_nodes.append(dict(attrs))
        node = SimpleNamespace(id="node-1", provision_state="enroll", **attrs)
        self.node_items.append(node)
        return node

    def update_node(self, node, **attrs):
        self.updated_nodes.append((node.id, dict(attrs)))
        for key, value in attrs.items():
            setattr(node, key, value)
        return node

    def set_node_provision_state(self, node, target, wait=False, timeout=None):
        self.provision_targets.append((node.name, target, wait, timeout))
        node.provision_state = "manageable"
        return node

    def get_node(self, node):
        return node

    def ports(self, details=True):
        assert details is True
        return list(self.port_items)

    def create_port(self, **attrs):
        self.created_ports.append(dict(attrs))
        port = SimpleNamespace(id="port-{}".format(len(self.port_items) + 1), **attrs)
        self.port_items.append(port)
        return port

    def update_port(self, port, **attrs):
        self.updated_ports.append((port.id, dict(attrs)))
        for key, value in attrs.items():
            setattr(port, key, value)
        return port

    def seed_extra_port(self, address, node_id="node-1"):
        self.port_items.append(
            SimpleNamespace(
                id="port-extra",
                address=address,
                node_id=node_id,
                physical_network=None,
            )
        )


@pytest.fixture
def fake_connection():
    return SimpleNamespace(baremetal=FakeBaremetal())


def test_create_stops_in_manageable(fake_connection, redfish_spec):
    module = load_module()

    result = module.reconcile_node(fake_connection, redfish_spec)

    baremetal = fake_connection.baremetal
    assert result["changed"] is True
    assert result["provision_state"] == "manageable"
    assert baremetal.created_nodes[0]["name"] == "compute-01"
    assert baremetal.provision_targets == [("compute-01", "manage", True, 300)]
    assert all(
        "provide" not in str(call).lower()
        for call in baremetal.provision_targets
    )


def test_second_run_is_idempotent(fake_connection, redfish_spec):
    module = load_module()
    module.reconcile_node(fake_connection, redfish_spec)

    result = module.reconcile_node(fake_connection, redfish_spec)

    assert result["changed"] is False
    assert result["node"] == "compute-01"
    assert result["ports_changed"] == 0
    assert len(fake_connection.baremetal.provision_targets) == 1


def test_removed_port_is_reported_but_never_deleted(fake_connection, redfish_spec):
    module = load_module()
    module.reconcile_node(fake_connection, redfish_spec)
    fake_connection.baremetal.seed_extra_port("02:00:00:00:01:ff")

    result = module.reconcile_node(fake_connection, redfish_spec)

    assert result["changed"] is False
    assert result["extra_ports"] == ["02:00:00:00:01:ff"]
    assert not hasattr(fake_connection.baremetal, "deleted_ports")


def test_duplicate_exact_node_names_fail_closed(fake_connection, redfish_spec):
    module = load_module()
    node = SimpleNamespace(name="compute-01", id="node-1")
    fake_connection.baremetal.node_items = [node, node]

    with pytest.raises(module.ReconciliationError, match="exactly one"):
        module.reconcile_node(fake_connection, redfish_spec)


def test_duplicate_mac_owned_by_another_node_fails(fake_connection, redfish_spec):
    module = load_module()
    fake_connection.baremetal.node_items = [
        SimpleNamespace(
            id="node-1",
            name="compute-01",
            driver="redfish",
            driver_info=redfish_spec["driver_info"],
            network_interface="noop",
            extra={"nova_hostname": "compute-01"},
            provision_state="manageable",
        )
    ]
    fake_connection.baremetal.seed_extra_port(
        "02:00:00:00:01:01", node_id="another-node"
    )

    with pytest.raises(module.ReconciliationError, match="another Ironic Node"):
        module.reconcile_node(fake_connection, redfish_spec)


def test_mac_conflict_is_detected_before_missing_node_is_created(
    fake_connection, redfish_spec
):
    module = load_module()
    fake_connection.baremetal.seed_extra_port(
        "02:00:00:00:01:01", node_id="another-node"
    )

    with pytest.raises(module.ReconciliationError, match="another Ironic Node"):
        module.reconcile_node(fake_connection, redfish_spec)

    assert fake_connection.baremetal.created_nodes == []


def test_check_mode_reports_without_mutating(fake_connection, redfish_spec):
    module = load_module()

    result = module.reconcile_node(
        fake_connection, redfish_spec, check_mode=True
    )

    assert result["changed"] is True
    assert result["node_uuid"] is None
    assert fake_connection.baremetal.created_nodes == []
    assert fake_connection.baremetal.created_ports == []
    assert fake_connection.baremetal.provision_targets == []


def test_normalize_accepts_ipmi_and_lowercases_macs(redfish_spec):
    module = load_module()
    spec = dict(redfish_spec)
    spec["driver"] = "ipmi"
    spec["driver_info"] = {
        "ipmi_address": "192.0.2.102",
        "ipmi_username": "powerops",
        "ipmi_password": "ipmi-secret",
    }
    spec["ports"] = [{"address": "02:AA:BB:CC:DD:EE"}]

    normalized = module.normalize_spec(spec)

    assert normalized["driver"] == "ipmi"
    assert normalized["ports"][0]["address"] == "02:aa:bb:cc:dd:ee"


def test_forbidden_existing_state_is_never_changed(fake_connection, redfish_spec):
    module = load_module()
    fake_connection.baremetal.node_items = [
        SimpleNamespace(
            id="node-1",
            name="compute-01",
            driver="redfish",
            driver_info=redfish_spec["driver_info"],
            network_interface="noop",
            extra={"nova_hostname": "compute-01"},
            provision_state="available",
        )
    ]

    with pytest.raises(module.ReconciliationError, match="available"):
        module.reconcile_node(fake_connection, redfish_spec)

    assert fake_connection.baremetal.provision_targets == []


def test_sdk_exception_is_redacted(fake_connection, redfish_spec):
    module = load_module()
    password = redfish_spec["driver_info"]["redfish_password"]

    def fail_create(**attrs):
        raise RuntimeError("BMC rejected password {}".format(password))

    fake_connection.baremetal.create_node = fail_create
    with pytest.raises(module.ReconciliationError) as caught:
        module.reconcile_node(fake_connection, redfish_spec)

    assert password not in str(caught.value)
    assert "***" in str(caught.value)
