"""Owner-safe Redis host operation locks."""


REFRESH_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RedisHostLock:
    def __init__(self, client, host, ttl_seconds, owner):
        if not host or not owner:
            raise ValueError("host and owner are required")
        if int(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.client = client
        self.key = "powerops:host:{}".format(host)
        self.ttl_ms = int(ttl_seconds * 1000)
        self.owner = owner

    def acquire(self):
        return bool(
            self.client.set(
                self.key, self.owner, nx=True, px=self.ttl_ms
            )
        )

    def refresh(self):
        return bool(
            self.client.eval(
                REFRESH_SCRIPT, 1, self.key, self.owner, self.ttl_ms
            )
        )

    def release(self):
        return bool(self.client.eval(RELEASE_SCRIPT, 1, self.key, self.owner))
