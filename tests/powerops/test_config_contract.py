from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def load_globals():
    return yaml.safe_load((ROOT / "etc/kolla/globals.yml").read_text())


def _section(text, name):
    marker = "[{}]".format(name)
    return text.split(marker, 1)[1].split("[", 1)[0]


def _hosts(text, name):
    return [
        line.split()[0]
        for line in _section(text, name).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_required_services_and_powerops_are_enabled():
    data = load_globals()
    assert data["enable_ironic"] == "yes"
    assert data["enable_masakari"] == "yes"
    assert data["enable_mistral"] == "yes"
    assert data["enable_powerops"] == "yes"


def test_redfish_and_ipmi_examples_have_required_fields():
    nodes = load_globals()["powerops_ironic_nodes"]
    assert {node["driver"] for node in nodes} == {"redfish", "ipmi"}
    redfish = next(node for node in nodes if node["driver"] == "redfish")
    ipmi = next(node for node in nodes if node["driver"] == "ipmi")
    assert redfish["driver_info"]["redfish_system_id"].startswith("/redfish/")
    assert (
        redfish["driver_info"]["redfish_password"]
        == "example-only-redfish-password"
    )
    assert ipmi["driver_info"]["ipmi_password"] == "example-only-ipmi-password"
    assert all(node["network_interface"] == "noop" for node in nodes)
    assert all(node["desired_provision_state"] == "manageable" for node in nodes)


def test_inventory_has_three_controls_and_power_only_ironic_groups():
    text = (ROOT / "etc/kolla/inventory").read_text()
    assert _hosts(text, "control") == [
        "controller-01",
        "controller-02",
        "controller-03",
    ]
    assert _hosts(text, "compute") == ["compute-01", "compute-02"]
    assert "[ironic-api:children]\ncontrol" in text
    assert "[ironic-conductor:children]\ncontrol" in text
    assert _hosts(text, "nova-compute-ironic") == []
    assert _hosts(text, "ironic-inspector") == []
    assert _hosts(text, "ironic-tftp") == []
    assert _hosts(text, "ironic-http") == []


def test_pvs_fragment_is_registry_only_and_contains_no_bmc_data():
    data = yaml.safe_load((ROOT / "etc/kolla/globals-pvs-fragment.yml").read_text())
    assert data["kolla_base_distro"] == "sberlinux"
    assert data["docker_registry"] == "registry.example.invalid:5000"
    assert "powerops_ironic_nodes" not in data
    assert "password" not in yaml.safe_dump(data).lower()
