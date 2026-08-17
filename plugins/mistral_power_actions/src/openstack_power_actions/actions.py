"""Mistral actions for planned compute-host power operations."""

import uuid
from urllib import parse

from mistral_lib import actions as mistral_actions
from oslo_config import cfg
from oslo_log import log as logging
import redis

from openstack_power_actions import clients
from openstack_power_actions.locks import RedisHostLock
from openstack_power_actions.operations import PowerOperations, PowerOpsError


LOG = logging.getLogger(__name__)
clients.register_opts()


def _operations():
    return PowerOperations(clients.connection_from_conf(cfg.CONF))


def _sentinel_endpoint(value):
    parsed = parse.urlsplit("//{}".format(value))
    if not parsed.hostname or parsed.port is None:
        raise ValueError("invalid Redis Sentinel endpoint")
    return parsed.hostname, parsed.port


def _redis_client():
    options = cfg.CONF.powerops
    if options.redis_sentinel_hosts:
        sentinel = redis.sentinel.Sentinel(
            [_sentinel_endpoint(item) for item in options.redis_sentinel_hosts],
            socket_timeout=options.redis_sentinel_socket_timeout,
        )
        return sentinel.master_for(
            options.redis_master_name,
            password=options.redis_password,
            db=options.redis_db,
            decode_responses=True,
        )
    if not options.redis_url:
        raise ValueError("Redis Sentinel hosts or redis_url are required")
    return redis.Redis.from_url(options.redis_url, decode_responses=True)


def _lock(host, owner, ttl=None):
    ttl_seconds = ttl if ttl is not None else cfg.CONF.powerops.lock_ttl
    return RedisHostLock(_redis_client(), host, ttl_seconds, owner)


def _owner(action_ctx):
    if isinstance(action_ctx, dict):
        value = action_ctx.get("workflow_execution_id") or action_ctx.get(
            "execution_id"
        )
    else:
        value = getattr(action_ctx, "workflow_execution_id", None) or getattr(
            action_ctx, "execution_id", None
        )
    if not value:
        raise ValueError("workflow execution id is required for host lock")
    return str(value)


def _expected(callable_):
    try:
        return callable_()
    except (PowerOpsError, ValueError) as exc:
        return mistral_actions.Result(error=str(exc))


class AcquireHostLockAction(mistral_actions.Action):
    def __init__(self, host, ttl=None):
        self.host = host
        self.ttl = ttl

    def run(self, action_ctx=None):
        def acquire():
            owner = _owner(action_ctx)
            if not _lock(self.host, owner, self.ttl).acquire():
                raise ValueError(
                    "host operation lock is already held: {}".format(self.host)
                )
            return {"host": self.host, "lock_owner": owner}

        return _expected(acquire)


class RefreshHostLockAction(mistral_actions.Action):
    def __init__(self, host, ttl=None):
        self.host = host
        self.ttl = ttl

    def run(self, action_ctx=None):
        def refresh():
            owner = _owner(action_ctx)
            if not _lock(self.host, owner, self.ttl).refresh():
                raise ValueError("host operation lock ownership was lost")
            return {"host": self.host, "lock_owner": owner, "refreshed": True}

        return _expected(refresh)


class ReleaseHostLockAction(mistral_actions.Action):
    def __init__(self, host, ttl=None):
        self.host = host
        self.ttl = ttl

    def run(self, action_ctx=None):
        def release():
            owner = _owner(action_ctx)
            if not _lock(self.host, owner, self.ttl).release():
                raise ValueError("host operation lock ownership was lost")
            return {"host": self.host, "lock_owner": owner, "released": True}

        return _expected(release)


class ResolveHostAction(mistral_actions.Action):
    def __init__(self, host):
        self.host = host

    def run(self, action_ctx=None):
        return _expected(lambda: _operations().resolve_host(self.host))


class SetNovaServiceAction(mistral_actions.Action):
    def __init__(self, host, enabled, reason="PowerOps workflow"):
        self.host = host
        self.enabled = enabled
        self.reason = reason

    def run(self, action_ctx=None):
        return _expected(
            lambda: _operations().set_nova_service(
                self.host, self.enabled, self.reason
            )
        )


class SetMasakariMaintenanceAction(mistral_actions.Action):
    def __init__(self, host, enabled):
        self.host = host
        self.enabled = enabled

    def run(self, action_ctx=None):
        return _expected(
            lambda: _operations().set_masakari_maintenance(
                self.host, self.enabled
            )
        )


class DrainHostAction(mistral_actions.Action):
    def __init__(self, host, policy, timeout=None, interval=None):
        self.host = host
        self.policy = policy
        self.timeout = timeout
        self.interval = interval

    def run(self, action_ctx=None):
        timeout = self.timeout or cfg.CONF.powerops.drain_timeout
        interval = self.interval or cfg.CONF.powerops.poll_interval
        return _expected(
            lambda: _operations().drain_host(
                self.host, self.policy, timeout, interval
            )
        )


class AssertHostEmptyAction(mistral_actions.Action):
    def __init__(self, host, allow_shutoff=False):
        self.host = host
        self.allow_shutoff = allow_shutoff

    def run(self, action_ctx=None):
        return _expected(
            lambda: _operations().assert_host_empty(
                self.host, self.allow_shutoff
            )
        )


class IronicPowerAction(mistral_actions.Action):
    def __init__(self, host, target, soft=False):
        self.host = host
        self.target = target
        self.soft = soft

    def run(self, action_ctx=None):
        return _expected(
            lambda: _operations().request_power(
                self.host, self.target, self.soft
            )
        )


class WaitPowerAction(mistral_actions.Action):
    def __init__(
        self, host, target, timeout=None, interval=None, stable_observations=None
    ):
        self.host = host
        self.target = target
        self.timeout = timeout
        self.interval = interval
        self.stable_observations = stable_observations

    def run(self, action_ctx=None):
        timeout = self.timeout or cfg.CONF.powerops.power_timeout
        interval = self.interval or cfg.CONF.powerops.poll_interval
        observations = (
            self.stable_observations
            or cfg.CONF.powerops.stable_observations
        )
        return _expected(
            lambda: _operations().wait_power(
                self.host, self.target, timeout, interval, observations
            )
        )


class WaitNovaServiceAction(mistral_actions.Action):
    def __init__(self, host, timeout=None, interval=None):
        self.host = host
        self.timeout = timeout
        self.interval = interval

    def run(self, action_ctx=None):
        timeout = self.timeout or cfg.CONF.powerops.power_timeout
        interval = self.interval or cfg.CONF.powerops.poll_interval
        return _expected(
            lambda: _operations().wait_nova_service(
                self.host, timeout, interval
            )
        )


class VerifyHostReturnAction(mistral_actions.Action):
    def __init__(self, host, stale_domains_checked=False):
        self.host = host
        self.stale_domains_checked = stale_domains_checked

    def run(self, action_ctx=None):
        return _expected(
            lambda: _operations().verify_host_return(
                self.host, self.stale_domains_checked
            )
        )


class PowerStatusAction(mistral_actions.Action):
    def __init__(self, host):
        self.host = host

    def run(self, action_ctx=None):
        return _expected(lambda: _operations().status(self.host))


class AuditEventAction(mistral_actions.Action):
    def __init__(self, host, event, outcome):
        self.host = host
        self.event = event
        self.outcome = outcome

    def run(self, action_ctx=None):
        event_id = str(uuid.uuid4())
        LOG.info(
            "powerops_audit event_id=%s host=%s event=%s outcome=%s",
            event_id,
            self.host,
            self.event,
            self.outcome,
        )
        return {
            "event_id": event_id,
            "host": self.host,
            "event": self.event,
            "outcome": self.outcome,
        }


class FailSafeHostAction(mistral_actions.Action):
    def __init__(self, host):
        self.host = host

    def run(self, action_ctx=None):
        return _expected(lambda: _operations().fail_safe_host(self.host))
