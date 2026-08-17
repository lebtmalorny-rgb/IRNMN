from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ROLE = ROOT / "ansible/roles/powerops"


def _yaml(path):
    return yaml.safe_load(path.read_text())


def test_site_imports_powerops_after_all_required_service_plays():
    site = _yaml(ROOT / "ansible/site.yml")
    names = [item.get("name") for item in site if "name" in item]
    powerops_index = next(
        index
        for index, item in enumerate(site)
        if item.get("import_playbook") == "powerops.yml"
    )
    for name in ("Apply role ironic", "Apply role mistral", "Apply role masakari"):
        service_play = next(item for item in site if item.get("name") == name)
        assert site.index(service_play) < powerops_index
    assert names.index("Apply role ironic") < names.index("Apply role mistral")
    assert names.index("Apply role mistral") < names.index("Apply role masakari")


def test_powerops_play_targets_control_and_role():
    plays = _yaml(ROOT / "ansible/powerops.yml")
    assert len(plays) == 1
    play = plays[0]
    assert play["hosts"] == "control"
    assert play["gather_facts"] is False
    assert play["serial"] == "100%"
    assert any(role.get("role") == "powerops" for role in play["roles"])


def test_role_mutations_are_gated_to_deploy_and_reconfigure():
    main = (ROLE / "tasks/main.yml").read_text()
    assert "enable_powerops | bool" in main
    assert "kolla_action in ['deploy', 'reconfigure']" in main
    for task_file in (
        "preflight.yml",
        "reconcile_ironic.yml",
        "reconcile_masakari.yml",
        "register_mistral.yml",
        "validate.yml",
    ):
        assert task_file in main


def test_api_reconciliation_is_local_once_and_hides_node_secrets():
    ironic = (ROLE / "tasks/reconcile_ironic.yml").read_text()
    masakari = (ROLE / "tasks/reconcile_masakari.yml").read_text()
    mistral = (ROLE / "tasks/register_mistral.yml").read_text()
    for source in (ironic, masakari, mistral):
        assert "run_once: true" in source
        assert "delegate_to: localhost" in source
    assert "no_log: true" in ironic
    assert "powerops_ironic_node:" in ironic


def test_preflight_requires_services_and_loadable_entry_points():
    source = (ROLE / "tasks/preflight.yml").read_text()
    defaults = (ROLE / "defaults/main.yml").read_text()
    for flag in (
        "enable_ironic",
        "enable_masakari",
        "enable_mistral",
        "enable_powerops",
    ):
        assert flag in source
    assert "masakari.task_flow.tasks" in source
    assert "ironic_fence" in source
    assert "mistral.actions" in source
    assert "powerops.acquire_host_lock" in defaults


def test_role_has_no_delete_or_provisioning_command_paths():
    sources = "\n".join(
        path.read_text()
        for path in (ROLE / "tasks").glob("*.yml")
    ).lower()
    for forbidden in (
        "delete_host",
        "delete_segment",
        "delete_workbook",
        "set_node_provision_state",
        "clean_node",
        "inspect_node",
    ):
        assert forbidden not in sources
