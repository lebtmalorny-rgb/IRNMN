"""Masakari TaskFlow task that confirms physical fencing through Ironic."""

import math
import time

import masakari.conf
from masakari.engine.drivers.taskflow import base
from masakari import exception
from oslo_log import log as logging

from masakari_ironic_fence import config


LOG = logging.getLogger(__name__)
config.register_opts()


class IronicFenceTask(base.MasakariTask):
    """Power off a failed host and require stable physical OFF evidence."""

    default_provides = "ironic_fence_result"

    def __init__(self, context, novaclient, **kwargs):
        self._connection_factory = kwargs.pop(
            "connection_factory", config.connection_from_conf
        )
        self._sleep = kwargs.pop("sleep", time.sleep)
        self._conf = kwargs.pop("conf", masakari.conf.CONF)
        kwargs["requires"] = ["host_name"]
        kwargs["provides"] = self.default_provides
        super().__init__(context, novaclient, **kwargs)

    @staticmethod
    def _fail(message):
        raise exception.HostRecoveryFailureException(
            message="Ironic fencing failed: {}".format(message)
        )

    def _exact_node(self, connection, host_name):
        matches = [
            node
            for node in connection.baremetal.nodes(details=True)
            if getattr(node, "name", None) == host_name
        ]
        if len(matches) != 1:
            self._fail(
                "expected exactly one Ironic Node named {}".format(host_name)
            )
        return matches[0]

    def execute(self, host_name):
        connection = self._connection_factory()
        node = self._exact_node(connection, host_name)
        current = getattr(node, "power_state", None)
        pending = getattr(node, "target_power_state", None)
        if pending and pending != "power off" and current != "power off":
            self._fail("Ironic Node has a conflicting power target")
        if current != "power off" and pending != "power off":
            connection.baremetal.set_node_power_state(
                node, "power off", wait=False
            )

        options = self._conf.powerops_ironic
        attempts = max(
            1,
            int(
                math.ceil(
                    float(options.power_timeout) / options.poll_interval
                )
            )
            + 1,
        )
        stable = 0
        for attempt in range(attempts):
            node = connection.baremetal.get_node(node)
            if getattr(node, "last_error", None):
                self._fail("BMC reported an error")
            current = getattr(node, "power_state", None)
            pending = getattr(node, "target_power_state", None)
            if pending and pending != "power off" and current != "power off":
                self._fail("Ironic Node has a conflicting power target")
            stable = stable + 1 if current == "power off" else 0
            if stable >= options.stable_off_observations:
                LOG.info(
                    "Ironic fencing confirmed for host %s after %s stable observations",
                    host_name,
                    stable,
                )
                return {
                    "host": host_name,
                    "node_uuid": node.id,
                    "power_state": "power off",
                    "stable_observations": stable,
                }
            if attempt + 1 < attempts:
                self._sleep(options.poll_interval)
        self._fail("physical power off was not confirmed before timeout")

    def revert(self, host_name, result=None, flow_failures=None):
        LOG.error(
            "Fencing failed for host %s; automatic power-on is forbidden",
            host_name,
        )
