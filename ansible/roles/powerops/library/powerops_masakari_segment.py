#!/usr/bin/python
"""Idempotently reconcile one Masakari segment and declared compute hosts."""

from copy import deepcopy

from ansible.module_utils.basic import AnsibleModule
import openstack.connection


DOCUMENTATION = r"""
---
module: powerops_masakari_segment
short_description: Reconcile a Masakari segment without deleting records
description:
  - Creates or updates one COMPUTE segment and its declared hosts.
  - Extra records are reported and never deleted.
options:
  segment:
    description: Declarative segment and host specification.
    type: dict
    required: true
  auth:
    description: OpenStack service authentication mapping.
    type: dict
    required: true
  region_name:
    description: OpenStack region containing the Masakari endpoint.
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
    """A fail-closed segment mapping or API state error."""


def _value(resource, name, default=None):
    if isinstance(resource, dict):
        return resource.get(name, default)
    return getattr(resource, name, default)


def normalize_segment_spec(raw):
    if not isinstance(raw, dict):
        raise ReconciliationError("segment specification must be a mapping")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ReconciliationError("segment name is required")
    service_type = str(raw.get("service_type") or "").upper()
    if service_type != "COMPUTE":
        raise ReconciliationError("segment service_type must be COMPUTE")
    recovery_method = str(raw.get("recovery_method") or "").lower()
    if recovery_method not in {"auto", "reserved_host"}:
        raise ReconciliationError(
            "recovery_method must be auto or reserved_host"
        )

    hosts = []
    names = set()
    for raw_host in raw.get("hosts") or []:
        if not isinstance(raw_host, dict):
            raise ReconciliationError("every segment host must be a mapping")
        host_name = str(raw_host.get("name") or "").strip()
        if not host_name:
            raise ReconciliationError("segment host name is required")
        if host_name in names:
            raise ReconciliationError(
                "duplicate desired Masakari host {}".format(host_name)
            )
        names.add(host_name)
        host_type = str(raw_host.get("type") or "COMPUTE").upper()
        if host_type != "COMPUTE":
            raise ReconciliationError("host type must be COMPUTE")
        control_attributes = str(
            raw_host.get("control_attributes") or ""
        ).strip()
        if not control_attributes:
            raise ReconciliationError("host control_attributes are required")
        hosts.append(
            {
                "name": host_name,
                "type": "COMPUTE",
                "control_attributes": control_attributes,
                "reserved": bool(raw_host.get("reserved", False)),
                "on_maintenance": bool(
                    raw_host.get("on_maintenance", False)
                ),
            }
        )
    if not hosts:
        raise ReconciliationError("at least one segment host is required")
    return {
        "name": name,
        "service_type": "COMPUTE",
        "recovery_method": recovery_method,
        "description": str(raw.get("description") or ""),
        "hosts": hosts,
    }


def reconcile_segment(client, raw_spec, check_mode=False):
    spec = normalize_segment_spec(raw_spec)
    matches = [
        segment
        for segment in client.segments()
        if _value(segment, "name") == spec["name"]
    ]
    if len(matches) > 1:
        raise ReconciliationError(
            "expected exactly one Masakari segment named {}".format(
                spec["name"]
            )
        )
    if matches and _value(matches[0], "service_type") != "COMPUTE":
        raise ReconciliationError(
            "existing segment service_type is not COMPUTE"
        )

    if not matches and check_mode:
        return {
            "changed": True,
            "segment": spec["name"],
            "segment_uuid": None,
            "hosts_changed": len(spec["hosts"]),
            "extra_hosts": [],
        }

    changed = not matches
    if matches:
        segment = matches[0]
    else:
        segment = client.create_segment(
            name=spec["name"],
            service_type="COMPUTE",
            recovery_method=spec["recovery_method"],
            description=spec["description"],
        )

    segment_updates = {}
    for field in ("recovery_method", "description"):
        if _value(segment, field) != spec[field]:
            segment_updates[field] = spec[field]
    if segment_updates:
        changed = True
        if not check_mode:
            segment = client.update_segment(segment, **segment_updates)

    existing_hosts = list(client.hosts(segment))
    existing_by_name = {}
    for host in existing_hosts:
        name = _value(host, "name")
        if name in existing_by_name:
            raise ReconciliationError(
                "duplicate existing Masakari host {}".format(name)
            )
        existing_by_name[name] = host

    hosts_changed = 0
    desired_names = {host["name"] for host in spec["hosts"]}
    for desired in spec["hosts"]:
        host = existing_by_name.get(desired["name"])
        attrs = {
            key: deepcopy(desired[key])
            for key in (
                "name",
                "type",
                "control_attributes",
                "reserved",
                "on_maintenance",
            )
        }
        if host is None:
            changed = True
            hosts_changed += 1
            if not check_mode:
                client.create_host(segment, **attrs)
            continue
        updates = {
            key: value
            for key, value in attrs.items()
            if key != "name" and _value(host, key) != value
        }
        if updates:
            changed = True
            hosts_changed += 1
            if not check_mode:
                client.update_host(host, _value(segment, "id"), **updates)

    extra_hosts = sorted(set(existing_by_name) - desired_names)
    return {
        "changed": changed,
        "segment": spec["name"],
        "segment_uuid": _value(segment, "uuid", _value(segment, "id")),
        "hosts_changed": hosts_changed,
        "extra_hosts": extra_hosts,
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
            "segment": {"type": "dict", "required": True},
            "auth": {"type": "dict", "required": True, "no_log": True},
            "region_name": {"type": "str", "default": "RegionOne"},
            "interface": {"type": "str", "default": "internal"},
            "validate_certs": {"type": "bool", "default": True},
            "cacert": {"type": "path", "required": False},
        },
        supports_check_mode=True,
    )
    try:
        result = reconcile_segment(
            _connection(module).instance_ha,
            module.params["segment"],
            check_mode=module.check_mode,
        )
        module.exit_json(**result)
    except Exception as exc:
        module.fail_json(msg=str(exc))


if __name__ == "__main__":
    main()
