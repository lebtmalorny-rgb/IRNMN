#!/usr/bin/python
"""Idempotently reconcile power-only Ironic Nodes and Ports."""

from copy import deepcopy
import re

from ansible.module_utils.basic import AnsibleModule
import openstack.connection


DOCUMENTATION = r"""
---
module: powerops_ironic_node
short_description: Reconcile a power-only Ironic Node and its declared Ports
options:
  node:
    type: dict
    required: true
  auth:
    type: dict
    required: true
  region_name:
    type: str
    default: RegionOne
  interface:
    type: str
    default: internal
  validate_certs:
    type: bool
    default: true
  cacert:
    type: path
  timeout:
    type: int
    default: 300
"""


MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)
REQUIRED_DRIVER_INFO = {
    "redfish": (
        "redfish_address",
        "redfish_system_id",
        "redfish_username",
        "redfish_password",
    ),
    "ipmi": (
        "ipmi_address",
        "ipmi_username",
        "ipmi_password",
    ),
}


class ReconciliationError(RuntimeError):
    """A fail-closed input, mapping or state error."""


def _value(resource, name, default=None):
    if isinstance(resource, dict):
        return resource.get(name, default)
    return getattr(resource, name, default)


def _secret_values(spec):
    info = spec.get("driver_info") if isinstance(spec, dict) else None
    if not isinstance(info, dict):
        return []
    return [
        str(value)
        for key, value in info.items()
        if "password" in str(key).lower() and value
    ]


def _redact(message, secrets):
    redacted = str(message)
    for secret in secrets:
        redacted = redacted.replace(secret, "***")
    return redacted


def normalize_spec(spec):
    """Validate and normalize one declarative power-only node mapping."""
    if not isinstance(spec, dict):
        raise ReconciliationError("node specification must be a mapping")
    name = str(spec.get("name") or "").strip()
    nova_hostname = str(spec.get("nova_hostname") or "").strip()
    driver = str(spec.get("driver") or "").strip().lower()
    if not name or not nova_hostname:
        raise ReconciliationError("name and nova_hostname are required")
    if driver not in REQUIRED_DRIVER_INFO:
        raise ReconciliationError("driver must be redfish or ipmi")
    if spec.get("network_interface") != "noop":
        raise ReconciliationError("network_interface must be noop")
    if spec.get("desired_provision_state") != "manageable":
        raise ReconciliationError("desired_provision_state must be manageable")

    driver_info = deepcopy(spec.get("driver_info") or {})
    for key in REQUIRED_DRIVER_INFO[driver]:
        if not driver_info.get(key):
            raise ReconciliationError("missing driver_info.{}".format(key))

    normalized_ports = []
    addresses = set()
    for raw_port in spec.get("ports") or []:
        if not isinstance(raw_port, dict):
            raise ReconciliationError("every port must be a mapping")
        address = str(raw_port.get("address") or "").lower()
        if not MAC.match(address):
            raise ReconciliationError("invalid port MAC: {}".format(address))
        if address in addresses:
            raise ReconciliationError("duplicate desired port MAC: {}".format(address))
        addresses.add(address)
        normalized_ports.append(
            {
                "address": address,
                "physical_network": raw_port.get("physical_network"),
            }
        )

    return {
        "name": name,
        "nova_hostname": nova_hostname,
        "driver": driver,
        "driver_info": driver_info,
        "network_interface": "noop",
        "desired_provision_state": "manageable",
        "ports": normalized_ports,
    }


def _driver_info_differs(current, desired):
    current = current or {}
    for key, desired_value in desired.items():
        current_value = current.get(key)
        if "password" in key.lower() and current_value in (None, "", "******"):
            continue
        if current_value != desired_value:
            return True
    return False


def reconcile_ports(connection, node, desired_ports, check_mode=False):
    """Create/update declared Ports and report extras without deleting them."""
    node_id = _value(node, "id")
    all_ports = list(connection.baremetal.ports(details=True))
    desired_addresses = {item["address"] for item in desired_ports}
    changed = 0

    for desired in desired_ports:
        matches = [
            port
            for port in all_ports
            if str(_value(port, "address", "")).lower() == desired["address"]
        ]
        if len(matches) > 1:
            raise ReconciliationError(
                "expected at most one Ironic Port with MAC {}".format(
                    desired["address"]
                )
            )
        if not matches:
            changed += 1
            if not check_mode:
                connection.baremetal.create_port(
                    node_id=node_id,
                    address=desired["address"],
                    physical_network=desired["physical_network"],
                )
            continue

        port = matches[0]
        if _value(port, "node_id") != node_id:
            raise ReconciliationError(
                "port {} belongs to another Ironic Node".format(
                    desired["address"]
                )
            )
        if _value(port, "physical_network") != desired["physical_network"]:
            changed += 1
            if not check_mode:
                connection.baremetal.update_port(
                    port, physical_network=desired["physical_network"]
                )

    extras = sorted(
        str(_value(port, "address")).lower()
        for port in all_ports
        if _value(port, "node_id") == node_id
        and str(_value(port, "address", "")).lower() not in desired_addresses
    )
    return {"ports_changed": changed, "extra_ports": extras}


def _preflight_port_ownership(connection, desired_ports, expected_node_id):
    all_ports = list(connection.baremetal.ports(details=True))
    for desired in desired_ports:
        matches = [
            port
            for port in all_ports
            if str(_value(port, "address", "")).lower() == desired["address"]
        ]
        if len(matches) > 1:
            raise ReconciliationError(
                "expected at most one Ironic Port with MAC {}".format(
                    desired["address"]
                )
            )
        if matches and _value(matches[0], "node_id") != expected_node_id:
            raise ReconciliationError(
                "port {} belongs to another Ironic Node".format(
                    desired["address"]
                )
            )


def _reconcile_node(connection, spec, check_mode, timeout):
    baremetal = connection.baremetal
    matches = [
        node
        for node in baremetal.nodes(details=True)
        if _value(node, "name") == spec["name"]
    ]
    if len(matches) > 1:
        raise ReconciliationError(
            "expected exactly one Ironic Node named {}".format(spec["name"])
        )

    created = not matches
    changed = created
    expected_node_id = None if created else _value(matches[0], "id")
    _preflight_port_ownership(
        connection, spec["ports"], expected_node_id
    )
    if created and check_mode:
        return {
            "changed": True,
            "node": spec["name"],
            "node_uuid": None,
            "provision_state": "absent",
            "ports_changed": len(spec["ports"]),
            "extra_ports": [],
        }

    if created:
        node = baremetal.create_node(
            name=spec["name"],
            driver=spec["driver"],
            driver_info=spec["driver_info"],
            network_interface="noop",
            extra={"nova_hostname": spec["nova_hostname"]},
        )
    else:
        node = matches[0]

    state = _value(node, "provision_state")
    if state not in ("enroll", "manageable"):
        raise ReconciliationError(
            "Ironic Node {} is in forbidden provision state {}".format(
                spec["name"], state
            )
        )

    updates = {}
    if _value(node, "driver") != spec["driver"]:
        updates["driver"] = spec["driver"]
    if _value(node, "network_interface") != "noop":
        updates["network_interface"] = "noop"
    current_info = deepcopy(_value(node, "driver_info", {}) or {})
    if _driver_info_differs(current_info, spec["driver_info"]):
        current_info.update(spec["driver_info"])
        updates["driver_info"] = current_info
    desired_extra = deepcopy(_value(node, "extra", {}) or {})
    if desired_extra.get("nova_hostname") != spec["nova_hostname"]:
        desired_extra["nova_hostname"] = spec["nova_hostname"]
        updates["extra"] = desired_extra
    if updates:
        changed = True
        if not check_mode:
            node = baremetal.update_node(node, **updates)

    if state == "enroll":
        changed = True
        if not check_mode:
            node = baremetal.set_node_provision_state(
                node, "manage", wait=True, timeout=timeout
            )
            node = baremetal.get_node(node)

    ports = reconcile_ports(
        connection, node, spec["ports"], check_mode=check_mode
    )
    changed = changed or ports["ports_changed"] > 0
    return {
        "changed": changed,
        "node": spec["name"],
        "node_uuid": _value(node, "id"),
        "provision_state": _value(node, "provision_state"),
        "ports_changed": ports["ports_changed"],
        "extra_ports": ports["extra_ports"],
    }


def reconcile_node(connection, raw_spec, check_mode=False, timeout=300):
    """Reconcile one Node and redact configured BMC secrets from failures."""
    secrets = _secret_values(raw_spec)
    try:
        spec = normalize_spec(raw_spec)
        return _reconcile_node(connection, spec, check_mode, timeout)
    except Exception as exc:
        message = _redact(str(exc), secrets)
        if isinstance(exc, ReconciliationError) and message == str(exc):
            raise
        raise ReconciliationError(message) from None


def _connection(module):
    auth = dict(module.params["auth"])
    verify = (
        module.params["cacert"]
        if module.params["validate_certs"] and module.params["cacert"]
        else module.params["validate_certs"]
    )
    return openstack.connection.Connection(
        **auth,
        region_name=module.params["region_name"],
        interface=module.params["interface"],
        verify=verify,
    )


def main():
    module = AnsibleModule(
        argument_spec={
            "node": {"type": "dict", "required": True, "no_log": True},
            "auth": {"type": "dict", "required": True, "no_log": True},
            "region_name": {"type": "str", "default": "RegionOne"},
            "interface": {"type": "str", "default": "internal"},
            "validate_certs": {"type": "bool", "default": True},
            "cacert": {"type": "path", "required": False},
            "timeout": {"type": "int", "default": 300},
        },
        supports_check_mode=True,
    )
    secrets = _secret_values(module.params["node"])
    try:
        result = reconcile_node(
            _connection(module),
            module.params["node"],
            check_mode=module.check_mode,
            timeout=module.params["timeout"],
        )
        module.exit_json(**result)
    except Exception as exc:
        module.fail_json(msg=_redact(str(exc), secrets))


if __name__ == "__main__":
    main()
