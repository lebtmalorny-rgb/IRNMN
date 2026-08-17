from types import SimpleNamespace

import pytest

from openstack_power_actions import actions as power_actions
from openstack_power_actions.operations import (
    HostReturnNotSafe,
    UnsupportedInstancePolicy,
    UnsupportedPowerTarget,
)


class FakeOperations:
    def __init__(self):
        self.calls = []

    def request_power(self, host, target, soft=False):
        self.calls.append(("request_power", host, target, soft))
        if target == "evacuate":
            raise UnsupportedPowerTarget("unsupported power target: evacuate")
        return {"host": host, "target_power_state": target}

    def drain_host(self, host, policy, timeout, interval):
        self.calls.append(("drain_host", host, policy, timeout, interval))
        if policy == "evacuate":
            raise UnsupportedInstancePolicy("unsupported instance policy: evacuate")
        return {"host": host, "policy": policy}

    def verify_host_return(self, host, stale_domains_checked):
        self.calls.append(("verify_host_return", host, stale_domains_checked))
        if not stale_domains_checked:
            raise HostReturnNotSafe("stale domain check is required")
        return {"host": host, "ready": True}

    def fail_safe_host(self, host):
        self.calls.append(("fail_safe_host", host))
        return {
            "host": host,
            "nova_enabled": False,
            "on_maintenance": True,
        }

    def status(self, host):
        self.calls.append(("status", host))
        return {"host": host, "power_state": "power off"}


@pytest.fixture
def operations(monkeypatch):
    fake = FakeOperations()
    monkeypatch.setattr(power_actions, "_operations", lambda: fake)
    return fake


def test_ironic_power_action_never_accepts_evacuation_target(operations):
    action = power_actions.IronicPowerAction("compute-01", "evacuate")
    result = action.run()
    assert result.is_error()
    assert result.error == "unsupported power target: evacuate"


def test_drain_rejects_unknown_instance_policy(operations):
    action = power_actions.DrainHostAction("compute-01", "evacuate")
    result = action.run()
    assert result.is_error()
    assert result.error == "unsupported instance policy: evacuate"


def test_return_requires_stale_domain_confirmation(operations):
    action = power_actions.VerifyHostReturnAction(
        "compute-01", stale_domains_checked=False
    )
    result = action.run()
    assert result.is_error()
    assert result.error == "stale domain check is required"


def test_fail_safe_disables_and_sets_maintenance(operations):
    action = power_actions.FailSafeHostAction("compute-01")
    assert action.run() == {
        "host": "compute-01",
        "nova_enabled": False,
        "on_maintenance": True,
    }
    assert operations.calls == [("fail_safe_host", "compute-01")]


class FakeLock:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.calls = []

    def acquire(self):
        self.calls.append("acquire")
        return self.acquired

    def refresh(self):
        self.calls.append("refresh")
        return True

    def release(self):
        self.calls.append("release")
        return True


def test_host_lock_uses_workflow_execution_id_as_owner(monkeypatch):
    captured = {}
    fake = FakeLock()

    def lock_factory(host, owner, ttl=None):
        captured.update(host=host, owner=owner, ttl=ttl)
        return fake

    monkeypatch.setattr(power_actions, "_lock", lock_factory)
    context = {"workflow_execution_id": "workflow-execution-1"}
    result = power_actions.AcquireHostLockAction("compute-01", ttl=120).run(context)
    assert result == {"host": "compute-01", "lock_owner": "workflow-execution-1"}
    assert captured == {
        "host": "compute-01",
        "owner": "workflow-execution-1",
        "ttl": 120,
    }


def test_failed_lock_acquisition_returns_error_without_host_mutation(monkeypatch):
    fake = FakeLock(acquired=False)
    monkeypatch.setattr(power_actions, "_lock", lambda host, owner, ttl=None: fake)
    result = power_actions.AcquireHostLockAction("compute-01").run(
        SimpleNamespace(execution_id="execution-2")
    )
    assert result.is_error()
    assert result.error == "host operation lock is already held: compute-01"


def test_redis_client_uses_kolla_sentinel_topology(monkeypatch):
    captured = {}

    class FakeSentinel:
        def __init__(self, endpoints, socket_timeout):
            captured["endpoints"] = endpoints
            captured["socket_timeout"] = socket_timeout

        def master_for(self, name, **kwargs):
            captured["master_name"] = name
            captured["master_kwargs"] = kwargs
            return "redis-master-client"

    monkeypatch.setattr(power_actions.redis.sentinel, "Sentinel", FakeSentinel)
    monkeypatch.setattr(
        power_actions.cfg,
        "CONF",
        SimpleNamespace(
            powerops=SimpleNamespace(
                redis_sentinel_hosts=[
                    "192.0.2.11:26379",
                    "[2001:db8::12]:26379",
                ],
                redis_sentinel_socket_timeout=5,
                redis_master_name="kolla",
                redis_password="redis-secret",
                redis_db=4,
                redis_url=None,
            )
        ),
    )

    assert power_actions._redis_client() == "redis-master-client"
    assert captured == {
        "endpoints": [("192.0.2.11", 26379), ("2001:db8::12", 26379)],
        "socket_timeout": 5,
        "master_name": "kolla",
        "master_kwargs": {
            "password": "redis-secret",
            "db": 4,
            "decode_responses": True,
        },
    }


def test_redis_client_can_use_explicit_direct_fallback(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        power_actions.redis.Redis,
        "from_url",
        lambda url, **kwargs: captured.update(url=url, **kwargs) or "direct-client",
    )
    monkeypatch.setattr(
        power_actions.cfg,
        "CONF",
        SimpleNamespace(
            powerops=SimpleNamespace(
                redis_sentinel_hosts=[],
                redis_url="redis://redis.example:6379/4",
            )
        ),
    )

    assert power_actions._redis_client() == "direct-client"
    assert captured == {
        "url": "redis://redis.example:6379/4",
        "decode_responses": True,
    }


def test_unexpected_operation_exception_is_not_hidden(monkeypatch):
    class BrokenOperations:
        def status(self, host):
            raise RuntimeError("programming defect")

    monkeypatch.setattr(power_actions, "_operations", BrokenOperations)
    with pytest.raises(RuntimeError, match="programming defect"):
        power_actions.PowerStatusAction("compute-01").run()


def test_audit_action_accepts_only_explicit_safe_fields(caplog):
    result = power_actions.AuditEventAction(
        "compute-01", "planned_power_off", "success"
    ).run()
    assert result["host"] == "compute-01"
    assert result["event"] == "planned_power_off"
    assert "event_id" in result
    assert "password" not in caplog.text.lower()
