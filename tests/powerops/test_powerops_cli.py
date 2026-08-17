import importlib.util
import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "powerops_cli", ROOT / "tools/powerops_cli.py"
)


def load_module():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def valid_data():
    return {
        "enable_ironic": "yes",
        "enable_masakari": "yes",
        "enable_mistral": "yes",
        "enable_powerops": "yes",
        "powerops_bmc_secrets_in_globals": True,
        "powerops_ironic_nodes": [
            {
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
                },
                "ports": [{"address": "02:00:00:00:01:01"}],
            }
        ],
    }


INVENTORY = """[control]
controller-01

[compute]
compute-01
"""


def test_duplicate_names_and_macs_are_rejected():
    module = load_module()
    data = valid_data()
    data["powerops_ironic_nodes"] *= 2
    errors = module.validate_config(data, INVENTORY)
    assert "duplicate node name: compute-01" in errors
    assert "duplicate port MAC: 02:00:00:00:01:01" in errors


def test_duplicate_redfish_system_is_rejected():
    module = load_module()
    data = valid_data()
    second = dict(data["powerops_ironic_nodes"][0])
    second["name"] = "compute-02"
    second["nova_hostname"] = "compute-02"
    second["ports"] = [{"address": "02:00:00:00:02:01"}]
    data["powerops_ironic_nodes"].append(second)
    errors = module.validate_config(data, INVENTORY + "compute-02\n")
    assert (
        "duplicate Redfish system: https://192.0.2.101 /redfish/v1/Systems/1"
        in errors
    )


def test_invalid_power_only_state_is_rejected():
    module = load_module()
    data = valid_data()
    data["powerops_ironic_nodes"][0]["desired_provision_state"] = "available"
    errors = module.validate_config(data, INVENTORY)
    assert "compute-01: desired_provision_state must be manageable" in errors


def test_unknown_compute_and_invalid_mac_are_rejected():
    module = load_module()
    data = valid_data()
    data["powerops_ironic_nodes"][0]["nova_hostname"] = "missing-compute"
    data["powerops_ironic_nodes"][0]["ports"][0]["address"] = "bad-mac"
    errors = module.validate_config(data, INVENTORY)
    assert "compute-01: nova_hostname is absent from [compute]" in errors
    assert "compute-01: invalid port MAC: bad-mac" in errors


def test_report_redacts_passwords_and_marks_live_checks_not_run():
    module = load_module()
    report = module.build_report(valid_data(), [])
    rendered = json.dumps(report)
    assert "example-only-redfish-password" not in rendered
    assert "driver_info" not in rendered
    assert report["local_validation"]["status"] == "passed"
    assert report["inventory"]["node_count"] == 1
    assert set(report["live_validation"].values()) == {
        "not_run: No target deployment was authorized"
    }


def test_validate_command_writes_redacted_report(tmp_path):
    module = load_module()
    configdir = tmp_path / "etc" / "kolla"
    configdir.mkdir(parents=True)
    (configdir / "globals.yml").write_text(yaml.safe_dump(valid_data()))
    inventory = configdir / "inventory"
    inventory.write_text(INVENTORY)
    report = tmp_path / "report.json"

    result = module.main(
        [
            "validate",
            "--configdir",
            str(configdir),
            "--inventory",
            str(inventory),
            "--report",
            str(report),
        ]
    )

    assert result == 0
    payload = report.read_text()
    assert json.loads(payload)["local_validation"]["status"] == "passed"
    assert "example-only-redfish-password" not in payload


def test_missing_compute_section_is_a_validation_error():
    module = load_module()
    errors = module.validate_config(valid_data(), "[control]\ncontroller-01\n")
    assert "inventory is missing [compute] section" in errors


@pytest.mark.parametrize(
    "service",
    ("enable_ironic", "enable_masakari", "enable_mistral", "enable_powerops"),
)
def test_each_required_service_must_be_enabled(service):
    module = load_module()
    data = valid_data()
    data[service] = "no"
    assert "{} must be yes".format(service) in module.validate_config(data, INVENTORY)
