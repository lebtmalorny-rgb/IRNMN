import importlib.util
import json
from pathlib import Path
import zipfile

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


def image_data():
    data = valid_data()
    data["kolla_container_engine"] = "podman"
    data["powerops_base_images"] = {
        "mistral-api": "quay.example/mistral-api:2025.1",
        "mistral-engine": "quay.example/mistral-engine:2025.1",
        "mistral-executor": "quay.example/mistral-executor:2025.1",
        "masakari-engine": "quay.example/masakari-engine:2025.1",
    }
    data["powerops_derived_images"] = {
        service: "registry.example.invalid:5000/powerops/{}:v1".format(service)
        for service in data["powerops_base_images"]
    }
    return data


class RecordingRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))


def _wheel(path, entry_points):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("example-1.0.dist-info/entry_points.txt", entry_points)


def _fake_image_tree(tmp_path):
    mistral_wheel = (
        tmp_path
        / "plugins/mistral_power_actions/dist"
        / "openstack_power_actions-1.0-py3-none-any.whl"
    )
    masakari_wheel = (
        tmp_path
        / "plugins/masakari_ironic_fence/dist"
        / "masakari_ironic_fence-1.0-py3-none-any.whl"
    )
    _wheel(
        mistral_wheel,
        "[mistral.actions]\npowerops.acquire_host_lock = package:Action\n",
    )
    _wheel(
        masakari_wheel,
        "[masakari.task_flow.tasks]\nironic_fence = package:Task\n",
    )
    for kind in ("mistral", "masakari"):
        context = tmp_path / "docker/powerops" / kind
        context.mkdir(parents=True)
        (context / "Containerfile").write_text("ARG BASE_IMAGE\n")


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
    assert report["automation"]["action_count"] == 15
    assert report["automation"]["workflow_count"] == 4
    assert set(report["live_validation"].values()) == {
        "not_run: No target deployment was authorized"
    }


def test_validate_command_writes_redacted_report(tmp_path):
    module = load_module()
    module.shutil.which = lambda command: None
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
    assert json.loads(payload)["live_validation"]["derived_image_build"] == (
        "not_run: configured container engine is unavailable"
    )
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


def test_build_images_uses_configured_base_and_derived_tags(tmp_path):
    module = load_module()
    _fake_image_tree(tmp_path)
    runner = RecordingRunner()

    result = module.build_images(image_data(), runner=runner, root=tmp_path)

    commands = [command for command, _ in runner.calls]
    builds = [command for command in commands if command[:2] == ["podman", "build"]]
    assert len(builds) == 4
    for service, image in image_data()["powerops_derived_images"].items():
        command = next(item for item in builds if image in item)
        assert (
            "BASE_IMAGE=" + image_data()["powerops_base_images"][service]
            in command
        )
    assert result["built_images"] == list(
        image_data()["powerops_derived_images"].values()
    )


def test_build_images_never_pushes(tmp_path):
    module = load_module()
    _fake_image_tree(tmp_path)
    runner = RecordingRunner()

    module.build_images(image_data(), runner=runner, root=tmp_path)

    assert all(command[:2] != ["podman", "push"] for command, _ in runner.calls)


def test_publish_requires_exact_registry_confirmation():
    module = load_module()
    runner = RecordingRunner()

    with pytest.raises(ValueError, match="registry confirmation"):
        module.publish_images(
            image_data(),
            confirm_registry="wrong.example",
            runner=runner,
        )

    assert runner.calls == []


def test_publish_inspects_every_image_before_any_push():
    module = load_module()
    runner = RecordingRunner()

    result = module.publish_images(
        image_data(),
        confirm_registry="registry.example.invalid:5000",
        runner=runner,
    )

    commands = [command for command, _ in runner.calls]
    assert commands[:4] == [
        ["podman", "image", "inspect", image]
        for image in image_data()["powerops_derived_images"].values()
    ]
    assert commands[4:] == [
        ["podman", "push", image]
        for image in image_data()["powerops_derived_images"].values()
    ]
    assert result["published_images"] == list(
        image_data()["powerops_derived_images"].values()
    )
