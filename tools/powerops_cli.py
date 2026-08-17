#!/usr/bin/env python3
"""Validate and prepare the Kolla-Ansible PowerOps extension."""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
MAC = re.compile(r"^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$", re.IGNORECASE)
REQUIRED_SERVICES = (
    "enable_ironic",
    "enable_masakari",
    "enable_mistral",
    "enable_powerops",
)
LIVE_CHECKS = (
    "kolla_deploy",
    "ironic_api_failover",
    "ironic_conductor_failover",
    "redfish_power",
    "ipmi_power",
    "masakari_fencing_and_evacuation",
    "mistral_planned_workflows",
)


def load_config(path):
    """Load a YAML mapping from *path*."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError("configuration root must be a mapping")
    return data


def _inventory_section(inventory_text, name):
    marker = "[{}]".format(name)
    if marker not in inventory_text:
        return None
    return inventory_text.split(marker, 1)[1].split("[", 1)[0]


def _compute_hosts(inventory_text):
    section = _inventory_section(inventory_text, "compute")
    if section is None:
        return None
    return {
        line.split()[0]
        for line in section.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _required_driver_fields(driver):
    return {
        "redfish": (
            "redfish_address",
            "redfish_system_id",
            "redfish_username",
            "redfish_password",
        ),
        "ipmi": ("ipmi_address", "ipmi_username", "ipmi_password"),
    }.get(driver)


def validate_config(data, inventory_text):
    """Return all input contract violations without exposing credentials."""
    errors = []
    for key in REQUIRED_SERVICES:
        if data.get(key) != "yes":
            errors.append("{} must be yes".format(key))

    computes = _compute_hosts(inventory_text)
    if computes is None:
        errors.append("inventory is missing [compute] section")
        computes = set()

    nodes = data.get("powerops_ironic_nodes") or []
    if not isinstance(nodes, list):
        return errors + ["powerops_ironic_nodes must be a list"]

    names = set()
    macs = set()
    systems = set()
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            errors.append("every powerops_ironic_nodes item must be a mapping")
            continue
        node = raw_node
        name = str(node.get("name") or "<unnamed>")
        if name in names:
            errors.append("duplicate node name: {}".format(name))
        names.add(name)

        if node.get("nova_hostname") not in computes:
            errors.append("{}: nova_hostname is absent from [compute]".format(name))
        if node.get("network_interface") != "noop":
            errors.append("{}: network_interface must be noop".format(name))
        if node.get("desired_provision_state") != "manageable":
            errors.append(
                "{}: desired_provision_state must be manageable".format(name)
            )

        driver = node.get("driver")
        info = node.get("driver_info") or {}
        required = _required_driver_fields(driver)
        if required is None:
            errors.append("{}: driver must be redfish or ipmi".format(name))
        else:
            for key in required:
                if not info.get(key):
                    errors.append("{}: missing driver_info.{}".format(name, key))

        if driver == "redfish":
            identity = (
                info.get("redfish_address"),
                info.get("redfish_system_id"),
            )
            if identity in systems:
                errors.append(
                    "duplicate Redfish system: {} {}".format(
                        identity[0], identity[1]
                    )
                )
            systems.add(identity)

        for port in node.get("ports") or []:
            mac = str(port.get("address") or "").lower()
            if not MAC.match(mac):
                errors.append("{}: invalid port MAC: {}".format(name, mac))
            if mac in macs:
                errors.append("duplicate port MAC: {}".format(mac))
            macs.add(mac)
    return errors


def build_report(data, errors):
    """Create a deterministic report that contains no BMC attributes."""
    nodes = data.get("powerops_ironic_nodes") or []
    driver_counts = Counter(
        node.get("driver", "unknown") for node in nodes if isinstance(node, dict)
    )
    port_count = sum(
        len(node.get("ports") or []) for node in nodes if isinstance(node, dict)
    )
    not_run = "not_run: No target deployment was authorized"
    return {
        "local_validation": {
            "status": "failed" if errors else "passed",
            "errors": list(errors),
        },
        "inventory": {
            "node_count": len(nodes),
            "port_count": port_count,
            "driver_counts": dict(sorted(driver_counts.items())),
        },
        "live_validation": {name: not_run for name in LIVE_CHECKS},
    }


def _validate_command(args):
    configdir = Path(args.configdir).resolve()
    inventory_path = Path(args.inventory or configdir / "inventory").resolve()
    report_path = Path(args.report).resolve()
    data = load_config(configdir / "globals.yml")
    inventory_text = inventory_path.read_text()
    errors = validate_config(data, inventory_text)
    report = build_report(data, errors)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if errors else 0


def _parser():
    parser = argparse.ArgumentParser(prog="powerops")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate deployment inputs")
    validate.add_argument("--configdir", default=str(ROOT / "etc/kolla"))
    validate.add_argument("--inventory")
    validate.add_argument(
        "--report", default=str(ROOT / "reports/powerops-validation.json")
    )
    validate.set_defaults(handler=_validate_command)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print("powerops: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
