from openstack_power_actions.locks import RedisHostLock


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def set(self, key, value, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = px
        return True

    def eval(self, script, key_count, key, owner, *args):
        assert key_count == 1
        if self.values.get(key) != owner:
            return 0
        if "pexpire" in script:
            self.ttls[key] = args[0]
            return 1
        if "del" in script:
            self.values.pop(key, None)
            self.ttls.pop(key, None)
            return 1
        raise AssertionError("unexpected Lua script")


def test_release_uses_owner_token():
    redis = FakeRedis()
    lock = RedisHostLock(redis, "compute-01", ttl_seconds=60, owner="execution-1")
    assert lock.acquire() is True
    assert lock.release() is True
    assert redis.values == {}


def test_wrong_owner_cannot_release():
    redis = FakeRedis()
    first = RedisHostLock(redis, "compute-01", 60, "execution-1")
    second = RedisHostLock(redis, "compute-01", 60, "execution-2")
    assert first.acquire() is True
    assert second.release() is False
    assert redis.values["powerops:host:compute-01"] == "execution-1"


def test_refresh_extends_only_the_owner_lease():
    redis = FakeRedis()
    first = RedisHostLock(redis, "compute-01", 60, "execution-1")
    second = RedisHostLock(redis, "compute-01", 120, "execution-2")
    assert first.acquire() is True
    assert second.refresh() is False
    assert first.refresh() is True
    assert redis.ttls[first.key] == 60000


def test_lock_rejects_empty_host_or_owner():
    redis = FakeRedis()
    for host, owner in (("", "execution-1"), ("compute-01", "")):
        try:
            RedisHostLock(redis, host, 60, owner)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid lock identity was accepted")
