import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKBOOK = ROOT / "mistral/workbooks/power-ops.yaml"
GLOBALS = ROOT / "etc/kolla/globals.yml"
DEFAULTS = ROOT / "ansible/roles/powerops/defaults/main.yml"

REQUIRED_ARTIFACTS = {
    ".gitignore",
    "ansible/powerops.yml",
    "ansible/roles/powerops/defaults/main.yml",
    "ansible/roles/powerops/library/powerops_ironic_node.py",
    "ansible/roles/powerops/library/powerops_masakari_segment.py",
    "ansible/roles/powerops/library/powerops_mistral_workbook.py",
    "ansible/roles/powerops/tasks/main.yml",
    "ansible/roles/powerops/tasks/preflight.yml",
    "ansible/roles/powerops/tasks/reconcile_ironic.yml",
    "ansible/roles/powerops/tasks/reconcile_masakari.yml",
    "ansible/roles/powerops/tasks/register_mistral.yml",
    "ansible/roles/powerops/tasks/validate.yml",
    "ansible/site.yml",
    "docker/powerops/masakari/Containerfile",
    "docker/powerops/mistral/Containerfile",
    "docs/powerops/POWEROPS-INSTALL.md",
    "docs/powerops/ironic-ha-power-workflows.png",
    "docs/powerops/ironic-ha-power-workflows.svg",
    "etc/kolla/config/ironic.conf",
    "etc/kolla/config/masakari/masakari-engine.conf",
    "etc/kolla/config/mistral/mistral-executor.conf",
    "etc/kolla/globals-pvs-fragment.yml",
    "etc/kolla/globals.yml",
    "etc/kolla/inventory",
    "mistral/workbooks/power-ops.yaml",
    "plugins/masakari_ironic_fence/pyproject.toml",
    "plugins/masakari_ironic_fence/src/masakari_ironic_fence/__init__.py",
    "plugins/masakari_ironic_fence/src/masakari_ironic_fence/config.py",
    "plugins/masakari_ironic_fence/src/masakari_ironic_fence/task.py",
    "plugins/masakari_ironic_fence/tests/conftest.py",
    "plugins/masakari_ironic_fence/tests/test_task.py",
    "plugins/mistral_power_actions/pyproject.toml",
    "plugins/mistral_power_actions/src/openstack_power_actions/__init__.py",
    "plugins/mistral_power_actions/src/openstack_power_actions/actions.py",
    "plugins/mistral_power_actions/src/openstack_power_actions/clients.py",
    "plugins/mistral_power_actions/src/openstack_power_actions/locks.py",
    "plugins/mistral_power_actions/src/openstack_power_actions/operations.py",
    "plugins/mistral_power_actions/tests/conftest.py",
    "plugins/mistral_power_actions/tests/test_actions.py",
    "plugins/mistral_power_actions/tests/test_clients.py",
    "plugins/mistral_power_actions/tests/test_locks.py",
    "plugins/mistral_power_actions/tests/test_operations.py",
    "reports/powerops-validation.json",
    "tests/powerops/test_artifact_contract.py",
    "tests/powerops/test_config_contract.py",
    "tests/powerops/test_containerfiles.py",
    "tests/powerops/test_docs.py",
    "tests/powerops/test_ironic_module.py",
    "tests/powerops/test_masakari_module.py",
    "tests/powerops/test_mistral_module.py",
    "tests/powerops/test_mistral_workbook.py",
    "tests/powerops/test_overrides.py",
    "tests/powerops/test_powerops_cli.py",
    "tests/powerops/test_python39_syntax.py",
    "tests/powerops/test_role_contract.py",
    "tests/powerops/test_safety_invariants.py",
    "tests/powerops/test_source_contract.py",
    "tools/powerops",
    "tools/powerops_cli.py",
}


def _entry_point_names(path, group):
    text = path.read_text()
    marker = '[project.entry-points."{}"]'.format(group)
    section = text.split(marker, 1)[1].split("\n[", 1)[0]
    return {
        match.group(1)
        for match in re.finditer(r'^"([^"]+)"\s*=', section, re.MULTILINE)
    }


def test_full_powerops_artifact_manifest_exists():
    missing = sorted(path for path in REQUIRED_ARTIFACTS if not (ROOT / path).is_file())
    assert missing == []


def test_role_and_site_reference_the_shipped_artifacts():
    defaults = DEFAULTS.read_text()
    main = (ROOT / "ansible/roles/powerops/tasks/main.yml").read_text()
    site = (ROOT / "ansible/site.yml").read_text()

    assert "../../../mistral/workbooks/power-ops.yaml" in defaults
    for task_file in (
        "preflight.yml",
        "reconcile_ironic.yml",
        "reconcile_masakari.yml",
        "register_mistral.yml",
        "validate.yml",
    ):
        assert task_file in main
    assert "import_playbook: powerops.yml" in site


def test_image_tags_and_container_build_inputs_are_connected():
    globals_data = yaml.safe_load(GLOBALS.read_text())
    rendered_globals = GLOBALS.read_text()
    derived = globals_data["powerops_derived_images"]

    assert set(derived) == {
        "mistral-api",
        "mistral-engine",
        "mistral-executor",
        "masakari-engine",
    }
    for service, image in derived.items():
        assert image.startswith("registry.example.invalid:5000/")
        assert ":2025.1-" in image
        variable = service.replace("-", "_") + "_image_full"
        assert variable in rendered_globals
    for path in (
        ROOT / "docker/powerops/mistral/Containerfile",
        ROOT / "docker/powerops/masakari/Containerfile",
    ):
        text = path.read_text()
        assert "ARG BASE_IMAGE" in text
        assert "COPY dist/*.whl" in text


def test_workbook_actions_match_entry_points_and_role_contract():
    workbook = yaml.safe_load(WORKBOOK.read_text())
    workflows = set(workbook["workflows"])
    used_actions = {
        task["action"].split()[0]
        for workflow in workbook["workflows"].values()
        for task in workflow["tasks"].values()
        if "action" in task
    }
    entry_points = _entry_point_names(
        ROOT / "plugins/mistral_power_actions/pyproject.toml",
        "mistral.actions",
    )
    defaults = yaml.safe_load(DEFAULTS.read_text())

    assert workbook["name"] == "power_ops"
    assert workflows == {
        "planned_power_off",
        "planned_reboot",
        "power_on_and_return",
        "host_power_status",
    }
    assert used_actions <= entry_points
    assert set(defaults["powerops_required_actions"]) == entry_points
    assert len(entry_points) == 15


def test_diagrams_match_reviewed_signatures_and_runbook_is_russian():
    expected = {
        "docs/powerops/ironic-ha-power-workflows.svg": (
            "e40547ec39a98cf180d2f3555365d5536bd96b4fb0043344d5b32450dd1d2b3a"
        ),
        "docs/powerops/ironic-ha-power-workflows.png": (
            "8c214c5e3a210814e6f469a1e331a7e2efadbeb96aa99e2d47201c24e741fea2"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest

    runbook = (ROOT / "docs/powerops/POWEROPS-INSTALL.md").read_text()
    assert len(runbook.splitlines()) >= 1000
    assert len(re.findall(r"[А-Яа-яЁё]", runbook)) >= 5000
    assert "ZIP из корня workspace" in runbook


def test_generated_build_outputs_are_not_tracked():
    tracked = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    generated = [
        path
        for path in tracked
        if path.endswith(".whl")
        or "/build/" in path
        or ".egg-info/" in path
        or "__pycache__" in path
    ]
    assert generated == []


def test_validation_report_has_local_counts_and_no_credentials():
    report_path = ROOT / "reports/powerops-validation.json"
    report_text = report_path.read_text()
    report = json.loads(report_text)

    assert report["local_validation"] == {"errors": [], "status": "passed"}
    assert report["inventory"]["node_count"] == 2
    assert report["inventory"]["port_count"] == 2
    assert report["automation"]["action_count"] == 15
    assert report["automation"]["workflow_count"] == 4
    assert all(
        status.startswith("not_run:")
        for status in report["live_validation"].values()
    )
    assert "password" not in report_text.lower()
    assert "driver_info" not in report_text
