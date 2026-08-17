from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKBOOK_PATH = ROOT / "mistral/workbooks/power-ops.yaml"


def load_workbook():
    return yaml.safe_load(WORKBOOK_PATH.read_text())


def test_workbook_defines_four_operator_workflows():
    workbook = load_workbook()
    assert workbook["version"] == "2.0"
    assert workbook["name"] == "power_ops"
    assert set(workbook["workflows"]) == {
        "planned_power_off",
        "planned_reboot",
        "power_on_and_return",
        "host_power_status",
    }


def test_planned_power_off_orders_safety_gates():
    tasks = load_workbook()["workflows"]["planned_power_off"]["tasks"]
    assert tasks["nova_disable"]["on-success"] == ["drain_instances"]
    assert tasks["drain_instances"]["on-success"] == ["refresh_lock"]
    assert tasks["refresh_lock"]["on-success"] == ["assert_host_safe"]
    assert tasks["assert_host_safe"]["on-success"] == ["request_off"]
    assert tasks["request_off"]["on-success"] == ["wait_off"]
    assert tasks["wait_off"]["on-success"] == ["audit_success"]


def test_every_mutating_task_fails_safe_before_releasing_lock():
    tasks = load_workbook()["workflows"]["planned_power_off"]["tasks"]
    for name in (
        "maintenance_on",
        "nova_disable",
        "drain_instances",
        "refresh_lock",
        "assert_host_safe",
        "request_off",
        "wait_off",
    ):
        assert tasks[name]["on-error"] == ["fail_safe_host"]
    assert tasks["fail_safe_host"]["on-complete"] == ["audit_failure"]
    assert tasks["audit_failure"]["on-complete"] == ["release_lock_error"]


def test_return_workflow_verifies_host_before_enabling_scheduler():
    tasks = load_workbook()["workflows"]["power_on_and_return"]["tasks"]
    assert tasks["wait_nova"]["on-success"] == ["verify_host_return"]
    assert tasks["verify_host_return"]["on-success"] == ["nova_enable"]
    inputs = load_workbook()["workflows"]["power_on_and_return"]["input"]
    assert {"stale_domains_checked": False} in inputs


def test_no_planned_workflow_calls_masakari_evacuation():
    rendered = yaml.safe_dump(load_workbook())
    assert "evacuat" not in rendered.lower()


def test_all_custom_actions_use_powerops_namespace():
    workbook = load_workbook()
    actions = {
        task["action"].split()[0]
        for workflow in workbook["workflows"].values()
        for task in workflow["tasks"].values()
        if "action" in task
    }
    assert all(name.startswith("powerops.") for name in actions)
    assert "powerops.wait_power" in actions
