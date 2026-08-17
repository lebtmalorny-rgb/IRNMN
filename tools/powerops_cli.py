#!/usr/bin/env python3
"""Validate and prepare the Kolla-Ansible PowerOps extension."""

import argparse
import configparser
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
import zipfile

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
    "derived_image_build",
    "derived_image_publish",
    "ironic_api_failover",
    "ironic_conductor_failover",
    "redfish_power",
    "ipmi_power",
    "masakari_fencing_and_evacuation",
    "mistral_planned_workflows",
)
IMAGE_SERVICES = (
    "mistral-api",
    "mistral-engine",
    "mistral-executor",
    "masakari-engine",
)
POWEROPS_ACTIONS = (
    "powerops.acquire_host_lock",
    "powerops.refresh_host_lock",
    "powerops.release_host_lock",
    "powerops.resolve_host",
    "powerops.set_nova_service",
    "powerops.set_masakari_maintenance",
    "powerops.drain_host",
    "powerops.assert_host_empty",
    "powerops.ironic_power",
    "powerops.wait_power",
    "powerops.wait_nova_service",
    "powerops.verify_host_return",
    "powerops.power_status",
    "powerops.audit_event",
    "powerops.fail_safe_host",
)
POWEROPS_WORKFLOWS = (
    "planned_power_off",
    "planned_reboot",
    "power_on_and_return",
    "host_power_status",
)
PLUGIN_BUILDS = (
    {
        "package": "plugins/mistral_power_actions",
        "wheel": "openstack_power_actions-*.whl",
        "context": "docker/powerops/mistral",
        "entry_group": "mistral.actions",
        "entry_name": "powerops.acquire_host_lock",
    },
    {
        "package": "plugins/masakari_ironic_fence",
        "wheel": "masakari_ironic_fence-*.whl",
        "context": "docker/powerops/masakari",
        "entry_group": "masakari.task_flow.tasks",
        "entry_name": "ironic_fence",
    },
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
        "automation": {
            "action_count": len(POWEROPS_ACTIONS),
            "workflow_count": len(POWEROPS_WORKFLOWS),
        },
        "live_validation": {name: not_run for name in LIVE_CHECKS},
    }


def _image_mappings(data):
    base = data.get("powerops_base_images")
    derived = data.get("powerops_derived_images")
    if not isinstance(base, dict) or not isinstance(derived, dict):
        raise ValueError(
            "powerops_base_images and powerops_derived_images must be mappings"
        )
    for service in IMAGE_SERVICES:
        if not base.get(service):
            raise ValueError("missing base image for {}".format(service))
        if not derived.get(service):
            raise ValueError("missing derived image for {}".format(service))
    return base, derived


def _run(runner, command, root=None):
    kwargs = {"check": True}
    if root is not None:
        kwargs["cwd"] = str(root)
    return runner(command, **kwargs)


def _built_wheel(root, build):
    candidates = list((root / build["package"] / "dist").glob(build["wheel"]))
    if not candidates:
        raise ValueError("wheel was not produced for {}".format(build["package"]))
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))


def _verify_wheel_entrypoint(wheel, group, entry_name):
    with zipfile.ZipFile(wheel) as archive:
        matches = [
            name for name in archive.namelist() if name.endswith("/entry_points.txt")
        ]
        if len(matches) != 1:
            raise ValueError("wheel must contain exactly one entry_points.txt")
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(archive.read(matches[0]).decode("utf-8"))
    if not parser.has_option(group, entry_name):
        raise ValueError(
            "wheel {} is missing entry point {}:{}".format(
                wheel.name, group, entry_name
            )
        )


def build_images(data, runner=subprocess.run, root=ROOT):
    """Build plugin wheels and four derived service images without publishing."""
    root = Path(root).resolve()
    base, derived = _image_mappings(data)
    engine = data.get("kolla_container_engine", "podman")
    wheel_by_kind = {}
    for build in PLUGIN_BUILDS:
        package = root / build["package"]
        _run(
            runner,
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                str(package),
            ],
            root=root,
        )
        wheel = _built_wheel(root, build)
        _verify_wheel_entrypoint(
            wheel, build["entry_group"], build["entry_name"]
        )
        context = root / build["context"]
        destination = context / "dist" / wheel.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(wheel, destination)
        wheel_by_kind["masakari" if "masakari" in build["package"] else "mistral"] = (
            destination
        )

    for service in IMAGE_SERVICES:
        kind = "masakari" if service == "masakari-engine" else "mistral"
        if kind not in wheel_by_kind:
            raise ValueError("missing {} plugin wheel".format(kind))
        context = root / "docker/powerops" / kind
        _run(
            runner,
            [
                engine,
                "build",
                "--build-arg",
                "BASE_IMAGE={}".format(base[service]),
                "--tag",
                derived[service],
                str(context),
            ],
            root=root,
        )
    return {"built_images": [derived[service] for service in IMAGE_SERVICES]}


def _registry_host(image):
    if "/" not in image:
        raise ValueError("derived image must include an explicit registry host")
    return image.split("/", 1)[0]


def publish_images(data, confirm_registry, runner=subprocess.run, root=ROOT):
    """Verify and publish existing derived images after exact registry confirmation."""
    _, derived = _image_mappings(data)
    engine = data.get("kolla_container_engine", "podman")
    images = [derived[service] for service in IMAGE_SERVICES]
    registries = {_registry_host(image) for image in images}
    if registries != {confirm_registry}:
        raise ValueError(
            "registry confirmation does not match derived image registry"
        )
    for image in images:
        _run(runner, [engine, "image", "inspect", image], root=root)
    for image in images:
        _run(runner, [engine, "push", image], root=root)
    return {"published_images": images}


def _validate_command(args):
    configdir = Path(args.configdir).resolve()
    inventory_path = Path(args.inventory or configdir / "inventory").resolve()
    report_path = Path(args.report).resolve()
    data = load_config(configdir / "globals.yml")
    inventory_text = inventory_path.read_text()
    errors = validate_config(data, inventory_text)
    report = build_report(data, errors)
    engine = data.get("kolla_container_engine", "podman")
    if shutil.which(engine) is None:
        report["live_validation"]["derived_image_build"] = (
            "not_run: configured container engine is unavailable"
        )
        report["live_validation"]["derived_image_publish"] = (
            "not_run: derived images were not built"
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 2 if errors else 0


def _build_images_command(args):
    data = load_config(Path(args.configdir).resolve() / "globals.yml")
    result = build_images(data)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _publish_images_command(args):
    data = load_config(Path(args.configdir).resolve() / "globals.yml")
    result = publish_images(data, args.confirm_registry)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


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
    build_images_parser = subparsers.add_parser(
        "build-images", help="build local PowerOps service images"
    )
    build_images_parser.add_argument(
        "--configdir", default=str(ROOT / "etc/kolla")
    )
    build_images_parser.set_defaults(handler=_build_images_command)
    publish_images_parser = subparsers.add_parser(
        "publish-images", help="publish already-built PowerOps service images"
    )
    publish_images_parser.add_argument(
        "--configdir", default=str(ROOT / "etc/kolla")
    )
    publish_images_parser.add_argument("--confirm-registry", required=True)
    publish_images_parser.set_defaults(handler=_publish_images_command)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return args.handler(args)
    except (
        OSError,
        ValueError,
        subprocess.CalledProcessError,
        yaml.YAMLError,
        zipfile.BadZipFile,
    ) as exc:
        print("powerops: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
