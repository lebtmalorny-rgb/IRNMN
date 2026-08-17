"""Client and configuration factories used by PowerOps actions."""

import openstack.connection
from oslo_config import cfg


POWEROPS_GROUP = cfg.OptGroup("powerops")
POWEROPS_OPTS = [
    cfg.StrOpt("redis_url", secret=True),
    cfg.ListOpt("redis_sentinel_hosts", default=[]),
    cfg.FloatOpt("redis_sentinel_socket_timeout", default=5.0, min=0.1),
    cfg.StrOpt("redis_master_name", default="kolla"),
    cfg.StrOpt("redis_password", secret=True),
    cfg.IntOpt("redis_db", default=4, min=0),
    cfg.StrOpt("region_name", default="RegionOne"),
    cfg.StrOpt("interface", default="internal"),
    cfg.BoolOpt("verify", default=True),
    cfg.IntOpt("lock_ttl", default=900, min=60),
    cfg.IntOpt("drain_timeout", default=600, min=30),
    cfg.IntOpt("power_timeout", default=180, min=30),
    cfg.IntOpt("poll_interval", default=5, min=1),
    cfg.IntOpt("stable_observations", default=3, min=2),
]


def register_opts(conf=cfg.CONF):
    try:
        conf.register_group(POWEROPS_GROUP)
    except cfg.DuplicateOptError:
        pass
    for option in POWEROPS_OPTS:
        try:
            conf.register_opt(option, group=POWEROPS_GROUP)
        except cfg.DuplicateOptError:
            pass


def connection_from_conf(conf):
    auth = conf.keystone_authtoken
    return openstack.connection.Connection(
        auth_url=auth.auth_url,
        username=auth.username,
        password=auth.password,
        project_name=auth.project_name,
        user_domain_name=auth.user_domain_name,
        project_domain_name=auth.project_domain_name,
        region_name=conf.powerops.region_name,
        interface=conf.powerops.interface,
        verify=conf.powerops.verify,
    )
