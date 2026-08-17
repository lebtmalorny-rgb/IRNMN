#!/usr/bin/python
"""Reconcile one Mistral DSL v2 workbook through its stable REST API."""

import hashlib
from pathlib import Path
from urllib import parse

from ansible.module_utils.basic import AnsibleModule
import openstack.connection
import yaml


DOCUMENTATION = r"""
---
module: powerops_mistral_workbook
short_description: Validate and reconcile one Mistral DSL v2 workbook
description:
  - Validates and creates or updates one Mistral DSL v2 workbook.
  - Required custom actions must exist before the workbook is changed.
options:
  path:
    description: Local path to the workbook YAML definition.
    type: path
    required: true
  required_actions:
    description: Action names which must exist in the Mistral API.
    type: list
    elements: str
  auth:
    description: OpenStack service authentication mapping.
    type: dict
    required: true
  region_name:
    description: OpenStack region containing the Mistral endpoint.
    type: str
    default: RegionOne
  interface:
    description: OpenStack endpoint interface.
    type: str
    default: internal
  validate_certs:
    description: Verify TLS certificates for OpenStack API requests.
    type: bool
    default: true
  cacert:
    description: Optional CA certificate bundle path.
    type: path
"""


class ReconciliationError(RuntimeError):
    """A fail-closed workbook, action or API state error."""


def _value(resource, name, default=None):
    if isinstance(resource, dict):
        return resource.get(name, default)
    return getattr(resource, name, default)


def normalize_workbook_yaml(definition):
    """Return name, canonical DSL v2 YAML and its stable SHA-256 hash."""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ReconciliationError("invalid workbook YAML: {}".format(exc))
    if not isinstance(data, dict):
        raise ReconciliationError("workbook document must be a mapping")
    if str(data.get("version")) != "2.0":
        raise ReconciliationError("workbook must use Mistral DSL v2")
    name = str(data.get("name") or "").strip()
    workflows = data.get("workflows")
    if not name or not isinstance(workflows, dict) or not workflows:
        raise ReconciliationError(
            "workbook name and non-empty workflows mapping are required"
        )
    canonical = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=True,
    )
    return {
        "name": name,
        "definition": canonical,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def reconcile_workbook(
    client,
    path,
    check_mode=False,
    required_actions=None,
):
    desired = normalize_workbook_yaml(Path(path).read_text())
    if required_actions:
        available = set(client.list_action_names())
        missing = sorted(set(required_actions) - available)
        if missing:
            raise ReconciliationError(
                "required Mistral actions are missing: {}".format(
                    ", ".join(missing)
                )
            )

    matches = [
        workbook
        for workbook in client.list_workbooks()
        if _value(workbook, "name") == desired["name"]
    ]
    if len(matches) > 1:
        raise ReconciliationError(
            "expected exactly one Mistral workbook named {}".format(
                desired["name"]
            )
        )
    if matches:
        current = matches[0]
        definition = _value(current, "definition")
        if not definition:
            current = client.get_workbook(desired["name"])
            definition = _value(current, "definition")
        if not definition:
            raise ReconciliationError(
                "Mistral API did not return the existing workbook definition"
            )
        current_hash = normalize_workbook_yaml(definition)["sha256"]
        changed = current_hash != desired["sha256"]
        if changed and not check_mode:
            client.update_workbook(desired["definition"])
    else:
        changed = True
        if not check_mode:
            client.create_workbook(desired["definition"])
    return {
        "changed": changed,
        "workbook": desired["name"],
        "definition_sha256": desired["sha256"],
    }


class MistralRestClient:
    """Small REST adapter matching python-mistralclient stable/2025.1."""

    def __init__(self, connection, region_name, interface):
        self.session = connection.session
        endpoint = self.session.get_endpoint(
            service_type="workflowv2",
            region_name=region_name,
            interface=interface,
        )
        if not endpoint:
            raise ReconciliationError("Mistral workflowv2 endpoint was not found")
        self.endpoint = endpoint.rstrip("/")

    @staticmethod
    def _json(response):
        return response.json()

    def list_workbooks(self):
        response = self.session.get(self.endpoint + "/workbooks")
        return self._json(response).get("workbooks", [])

    def get_workbook(self, name):
        url = self.endpoint + "/workbooks/" + parse.quote(name, safe="")
        return self._json(self.session.get(url))

    def create_workbook(self, definition):
        response = self.session.post(
            self.endpoint + "/workbooks",
            params={"scope": "private"},
            data=definition,
            headers={"content-type": "text/plain"},
        )
        return self._json(response)

    def update_workbook(self, definition):
        response = self.session.put(
            self.endpoint + "/workbooks",
            params={"scope": "private"},
            data=definition,
            headers={"content-type": "text/plain"},
        )
        return self._json(response)

    def list_action_names(self):
        response = self.session.get(self.endpoint + "/actions")
        return {
            str(_value(action, "name"))
            for action in self._json(response).get("actions", [])
        }


def _connection(module):
    verify = (
        module.params["cacert"]
        if module.params["validate_certs"] and module.params["cacert"]
        else module.params["validate_certs"]
    )
    return openstack.connection.Connection(
        **dict(module.params["auth"]),
        region_name=module.params["region_name"],
        interface=module.params["interface"],
        verify=verify,
    )


def main():
    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "required": True},
            "required_actions": {
                "type": "list",
                "elements": "str",
                "default": [],
            },
            "auth": {"type": "dict", "required": True, "no_log": True},
            "region_name": {"type": "str", "default": "RegionOne"},
            "interface": {"type": "str", "default": "internal"},
            "validate_certs": {"type": "bool", "default": True},
            "cacert": {"type": "path", "required": False},
        },
        supports_check_mode=True,
    )
    try:
        connection = _connection(module)
        client = MistralRestClient(
            connection,
            module.params["region_name"],
            module.params["interface"],
        )
        result = reconcile_workbook(
            client,
            module.params["path"],
            check_mode=module.check_mode,
            required_actions=module.params["required_actions"],
        )
        module.exit_json(**result)
    except Exception as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
