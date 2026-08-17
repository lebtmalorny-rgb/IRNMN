"""Power-only OpenStack operations used by Mistral actions."""

import math
import time


class PowerOpsError(RuntimeError):
    """Base class for expected fail-closed operation failures."""


class MappingError(PowerOpsError):
    pass


class HostNotEmpty(PowerOpsError):
    pass


class PowerTimeout(PowerOpsError):
    pass


class UnsupportedPowerTarget(PowerOpsError):
    pass


class UnsupportedInstancePolicy(PowerOpsError):
    pass


class HostReturnNotSafe(PowerOpsError):
    pass


class PowerOperations:
    POWER_TARGETS = {
        "power on",
        "power off",
        "rebooting",
        "soft power off",
        "soft rebooting",
    }
    INSTANCE_POLICIES = {"require_empty", "live_migrate", "stop"}

    def __init__(self, connection, sleep=time.sleep, required_network_agents=None):
        self.connection = connection
        self.sleep = sleep
        self.required_network_agents = tuple(required_network_agents or ())

    @staticmethod
    def _exactly_one(resources, description):
        resources = list(resources)
        if len(resources) != 1:
            raise MappingError("expected exactly one {}".format(description))
        return resources[0]

    def _ironic_node(self, host):
        matches = [
            node
            for node in self.connection.baremetal.nodes(details=True)
            if getattr(node, "name", None) == host
        ]
        return self._exactly_one(matches, "Ironic Node named {}".format(host))

    def _nova_service(self, host):
        matches = [
            service
            for service in self.connection.compute.services(
                host=host, binary="nova-compute"
            )
            if getattr(service, "host", None) == host
            and getattr(service, "binary", None) == "nova-compute"
        ]
        return self._exactly_one(
            matches, "Nova nova-compute service for {}".format(host)
        )

    def _masakari_host(self, host):
        matches = []
        for segment in self.connection.instance_ha.segments():
            for item in self.connection.instance_ha.hosts(segment):
                if getattr(item, "name", None) == host:
                    matches.append((segment, item))
        return self._exactly_one(matches, "Masakari host named {}".format(host))

    def resolve_host(self, host):
        nova = self._nova_service(host)
        ironic = self._ironic_node(host)
        segment, masakari = self._masakari_host(host)
        return {
            "host": host,
            "nova_service_id": nova.id,
            "ironic_node_id": ironic.id,
            "masakari_segment_id": segment.id,
            "masakari_host_id": masakari.id,
        }

    def set_nova_service(self, host, enabled, reason="PowerOps workflow"):
        service = self._nova_service(host)
        if enabled:
            if getattr(service, "status", None) != "enabled":
                self.connection.compute.enable_service(service)
        elif getattr(service, "status", None) != "disabled":
            self.connection.compute.disable_service(
                service, disabled_reason=reason
            )
        return {"host": host, "enabled": bool(enabled)}

    def set_masakari_maintenance(self, host, enabled):
        segment, item = self._masakari_host(host)
        desired = bool(enabled)
        if bool(getattr(item, "on_maintenance", False)) != desired:
            self.connection.instance_ha.update_host(
                item, segment, on_maintenance=desired
            )
        return {"host": host, "on_maintenance": desired}

    @staticmethod
    def _server_host(server):
        return getattr(server, "hypervisor_hostname", None) or getattr(
            server, "OS-EXT-SRV-ATTR:hypervisor_hostname", None
        )

    def _servers_on_host(self, host):
        servers = self.connection.compute.servers(all_projects=True, host=host)
        return [item for item in servers if self._server_host(item) == host]

    def list_servers(self, host):
        return [
            {
                "id": item.id,
                "name": getattr(item, "name", item.id),
                "status": getattr(item, "status", "UNKNOWN"),
            }
            for item in self._servers_on_host(host)
        ]

    @staticmethod
    def _attempts(timeout, interval):
        if timeout <= 0 or interval <= 0:
            raise ValueError("timeout and interval must be positive")
        return max(1, int(math.ceil(float(timeout) / interval)) + 1)

    def drain_host(self, host, policy, timeout, interval):
        if policy not in self.INSTANCE_POLICIES:
            raise UnsupportedInstancePolicy(
                "unsupported instance policy: {}".format(policy)
            )
        initial = self._servers_on_host(host)
        if policy == "require_empty":
            if initial:
                raise HostNotEmpty("host {} still has servers".format(host))
            return {"host": host, "servers_on_source": []}

        if policy == "live_migrate":
            for item in initial:
                self.connection.compute.live_migrate_server(item)
            for attempt in range(self._attempts(timeout, interval)):
                remaining = self._servers_on_host(host)
                if not remaining:
                    return {"host": host, "servers_on_source": []}
                if any(getattr(item, "status", None) == "ERROR" for item in remaining):
                    raise HostNotEmpty("server migration entered ERROR")
                if attempt + 1 < self._attempts(timeout, interval):
                    self.sleep(interval)
            raise HostNotEmpty("servers did not leave host before timeout")

        for item in initial:
            self.connection.compute.stop_server(item)
        for attempt in range(self._attempts(timeout, interval)):
            remaining = self._servers_on_host(host)
            statuses = [getattr(item, "status", "UNKNOWN") for item in remaining]
            if all(status == "SHUTOFF" for status in statuses):
                return {"host": host, "remaining_statuses": statuses}
            if any(status == "ERROR" for status in statuses):
                raise HostNotEmpty("server stop entered ERROR")
            if attempt + 1 < self._attempts(timeout, interval):
                self.sleep(interval)
        raise HostNotEmpty("servers did not stop before timeout")

    def assert_host_empty(self, host, allow_shutoff=False):
        servers = self._servers_on_host(host)
        statuses = [getattr(item, "status", "UNKNOWN") for item in servers]
        if servers and not (
            allow_shutoff and all(status == "SHUTOFF" for status in statuses)
        ):
            raise HostNotEmpty("host {} is not safe for power off".format(host))
        return {"host": host, "remaining_statuses": statuses}

    def request_power(self, host, target, soft=False):
        if target not in self.POWER_TARGETS:
            raise UnsupportedPowerTarget(
                "unsupported power target: {}".format(target)
            )
        node = self._ironic_node(host)
        current = getattr(node, "power_state", None)
        pending = getattr(node, "target_power_state", None)
        effective_target = target
        if soft and target == "power off":
            effective_target = "soft power off"
        elif soft and target == "rebooting":
            effective_target = "soft rebooting"
        if current == target or pending == effective_target:
            return {
                "host": host,
                "power_state": current,
                "target_power_state": pending,
            }
        if pending and pending != effective_target:
            raise PowerTimeout("Ironic Node has a conflicting power target")
        self.connection.baremetal.set_node_power_state(
            node, effective_target, wait=False
        )
        return {
            "host": host,
            "power_state": current,
            "target_power_state": effective_target,
        }

    def wait_power(self, host, target, timeout, interval, stable_observations):
        if stable_observations < 2:
            raise ValueError("stable_observations must be at least 2")
        node = self._ironic_node(host)
        stable = 0
        for attempt in range(self._attempts(timeout, interval)):
            node = self.connection.baremetal.get_node(node)
            if getattr(node, "last_error", None):
                raise PowerTimeout("BMC reported an error")
            current = getattr(node, "power_state", None)
            pending = getattr(node, "target_power_state", None)
            compatible_pending = {target}
            if target == "power off":
                compatible_pending.add("soft power off")
            elif target == "rebooting":
                compatible_pending.add("soft rebooting")
            if pending and pending not in compatible_pending and current != target:
                raise PowerTimeout("Ironic Node has a conflicting power target")
            stable = stable + 1 if current == target else 0
            if stable >= stable_observations:
                return {
                    "host": host,
                    "power_state": current,
                    "target_power_state": pending,
                }
            if attempt + 1 < self._attempts(timeout, interval):
                self.sleep(interval)
        raise PowerTimeout("power state was not stable before timeout")

    def wait_nova_service(self, host, timeout, interval):
        for attempt in range(self._attempts(timeout, interval)):
            service = self._nova_service(host)
            if getattr(service, "state", None) == "up":
                return {"host": host, "state": "up"}
            if attempt + 1 < self._attempts(timeout, interval):
                self.sleep(interval)
        raise HostReturnNotSafe("Nova service did not become up before timeout")

    def verify_host_return(self, host, stale_domains_checked):
        if not stale_domains_checked:
            raise HostReturnNotSafe("stale domain check is required")
        node = self.connection.baremetal.get_node(self._ironic_node(host))
        if getattr(node, "power_state", None) != "power on":
            raise HostReturnNotSafe("Ironic power on is not confirmed")
        service = self._nova_service(host)
        if getattr(service, "state", None) != "up":
            raise HostReturnNotSafe("Nova service is not up")
        agents = list(self.connection.network.agents(host=host))
        for binary in self.required_network_agents:
            matches = [item for item in agents if getattr(item, "binary", None) == binary]
            if len(matches) != 1 or not bool(getattr(matches[0], "is_alive", False)):
                raise HostReturnNotSafe(
                    "required network agent {} is not healthy".format(binary)
                )
        return {"host": host, "ready": True, "stale_domains_checked": True}

    def fail_safe_host(self, host):
        self.set_nova_service(host, False, reason="PowerOps fail-safe")
        self.set_masakari_maintenance(host, True)
        return {
            "host": host,
            "nova_enabled": False,
            "on_maintenance": True,
        }

    def status(self, host):
        service = self._nova_service(host)
        node = self._ironic_node(host)
        _segment, masakari = self._masakari_host(host)
        return {
            "host": host,
            "nova_status": getattr(service, "status", None),
            "nova_state": getattr(service, "state", None),
            "power_state": getattr(node, "power_state", None),
            "target_power_state": getattr(node, "target_power_state", None),
            "masakari_on_maintenance": bool(
                getattr(masakari, "on_maintenance", False)
            ),
        }
