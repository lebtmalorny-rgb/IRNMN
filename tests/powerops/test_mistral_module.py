import importlib.util
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT / "ansible/roles/powerops/library/powerops_mistral_workbook.py"
)
WORKBOOK = ROOT / "mistral/workbooks/power-ops.yaml"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "powerops_mistral_workbook", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMistral:
    def __init__(self):
        self.definitions = {}
        self.calls = []
        self.actions = {
            "powerops.acquire_host_lock",
            "powerops.power_status",
        }

    def list_workbooks(self):
        return [
            {"name": name, "definition": definition}
            for name, definition in self.definitions.items()
        ]

    def get_workbook(self, name):
        return {"name": name, "definition": self.definitions[name]}

    def create_workbook(self, definition):
        self.calls.append(("create", definition))
        name = yaml.safe_load(definition)["name"]
        self.definitions[name] = definition

    def update_workbook(self, definition):
        self.calls.append(("update", definition))
        name = yaml.safe_load(definition)["name"]
        self.definitions[name] = definition

    def list_action_names(self):
        return set(self.actions)


def test_create_and_replay_are_hash_idempotent():
    module = load_module()
    client = FakeMistral()

    first = module.reconcile_workbook(client, WORKBOOK)
    second = module.reconcile_workbook(client, WORKBOOK)

    assert first["changed"] is True
    assert second["changed"] is False
    assert first["definition_sha256"] == second["definition_sha256"]
    assert [call[0] for call in client.calls] == ["create"]


def test_definition_change_updates_only_target_workbook(tmp_path):
    module = load_module()
    client = FakeMistral()
    client.definitions["unrelated"] = "version: '2.0'\nname: unrelated\nworkflows: {}\n"
    module.reconcile_workbook(client, WORKBOOK)
    changed = yaml.safe_load(WORKBOOK.read_text())
    changed["workflows"]["host_power_status"]["description"] = "changed"
    path = tmp_path / "power-ops.yaml"
    path.write_text(yaml.safe_dump(changed, sort_keys=False))

    result = module.reconcile_workbook(client, path)

    assert result["changed"] is True
    assert client.definitions["unrelated"].startswith("version:")
    assert [call[0] for call in client.calls] == ["create", "update"]


def test_invalid_dsl_fails_before_api_calls(tmp_path):
    module = load_module()
    client = FakeMistral()
    path = tmp_path / "invalid.yaml"
    path.write_text("version: '1.0'\nname: bad\nworkflows: {}\n")

    with pytest.raises(module.ReconciliationError, match="DSL v2"):
        module.reconcile_workbook(client, path)

    assert client.calls == []


def test_missing_required_action_blocks_workbook_mutation():
    module = load_module()
    client = FakeMistral()

    with pytest.raises(module.ReconciliationError, match="powerops.missing"):
        module.reconcile_workbook(
            client,
            WORKBOOK,
            required_actions=["powerops.acquire_host_lock", "powerops.missing"],
        )

    assert client.calls == []


def test_check_mode_does_not_create_workbook():
    module = load_module()
    client = FakeMistral()

    result = module.reconcile_workbook(client, WORKBOOK, check_mode=True)

    assert result["changed"] is True
    assert client.definitions == {}
    assert client.calls == []
