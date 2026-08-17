import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "ansible/roles/powerops/library/powerops_masakari_segment.py"
)


def load_module():
    spec = importlib.util.spec_from_file_location(
        "powerops_masakari_segment", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def segment_spec():
    return {
        "name": "powerops-compute",
        "service_type": "COMPUTE",
        "recovery_method": "auto",
        "description": "PowerOps compute hosts",
        "hosts": [
            {
                "name": "compute-01",
                "type": "COMPUTE",
                "control_attributes": "SSH",
                "reserved": False,
                "on_maintenance": True,
            },
            {
                "name": "compute-02",
                "type": "COMPUTE",
                "control_attributes": "SSH",
                "reserved": False,
                "on_maintenance": True,
            },
        ],
    }


class FakeMasakari:
    def __init__(self):
        self.segment_items = []
        self.host_items = {}
        self.calls = []

    @property
    def segment_count(self):
        return len(self.segment_items)

    @property
    def host_names(self):
        return {
            host.name
            for hosts in self.host_items.values()
            for host in hosts
        }

    def segments(self):
        return list(self.segment_items)

    def create_segment(self, **attrs):
        self.calls.append(("create_segment", dict(attrs)))
        segment = SimpleNamespace(
            id="segment-1", uuid="segment-1", **attrs
        )
        self.segment_items.append(segment)
        self.host_items[segment.id] = []
        return segment

    def update_segment(self, segment, **attrs):
        self.calls.append(("update_segment", segment.id, dict(attrs)))
        for key, value in attrs.items():
            setattr(segment, key, value)
        return segment

    def hosts(self, segment):
        return list(self.host_items[segment.id])

    def create_host(self, segment, **attrs):
        self.calls.append(("create_host", segment.id, dict(attrs)))
        host = SimpleNamespace(
            id="host-{}".format(len(self.host_items[segment.id]) + 1),
            **attrs,
        )
        self.host_items[segment.id].append(host)
        return host

    def update_host(self, host, segment, **attrs):
        self.calls.append(("update_host", host.id, segment, dict(attrs)))
        for key, value in attrs.items():
            setattr(host, key, value)
        return host

    def add_host(self, name):
        segment = self.segment_items[0]
        self.host_items[segment.id].append(
            SimpleNamespace(
                id="host-extra",
                name=name,
                type="COMPUTE",
                control_attributes="SSH",
                reserved=False,
                on_maintenance=True,
            )
        )


def test_reconcile_segment_and_hosts_is_idempotent(segment_spec):
    module = load_module()
    client = FakeMasakari()

    first = module.reconcile_segment(client, segment_spec)
    second = module.reconcile_segment(client, segment_spec)

    assert first["changed"] is True
    assert second["changed"] is False
    assert client.segment_count == 1
    assert client.host_names == {"compute-01", "compute-02"}


def test_removed_host_is_reported_not_deleted(segment_spec):
    module = load_module()
    client = FakeMasakari()
    module.reconcile_segment(client, segment_spec)
    client.add_host("compute-retired")

    result = module.reconcile_segment(client, segment_spec)

    assert result["extra_hosts"] == ["compute-retired"]
    assert "compute-retired" in client.host_names
    assert all("delete" not in call[0] for call in client.calls)


def test_wrong_service_type_fails_without_update(segment_spec):
    module = load_module()
    client = FakeMasakari()
    segment = client.create_segment(
        name="powerops-compute",
        service_type="CONTROLLER",
        recovery_method="auto",
        description="wrong",
    )
    assert segment.service_type == "CONTROLLER"

    with pytest.raises(module.ReconciliationError, match="service_type"):
        module.reconcile_segment(client, segment_spec)

    assert all(call[0] != "update_segment" for call in client.calls)


def test_check_mode_does_not_create_records(segment_spec):
    module = load_module()
    client = FakeMasakari()

    result = module.reconcile_segment(client, segment_spec, check_mode=True)

    assert result["changed"] is True
    assert result["segment_uuid"] is None
    assert client.segment_count == 0
    assert client.calls == []
