"""Configuration and OpenStack connection factory for Ironic fencing."""

import masakari.conf
import openstack.connection
from oslo_config import cfg


POWEROPS_IRONIC_GROUP = cfg.OptGroup("powerops_ironic")
POWEROPS_IRONIC_OPTS = [
    cfg.IntOpt("power_timeout", default=180, min=30),
    cfg.IntOpt("poll_interval", default=5, min=1),
    cfg.IntOpt("stable_off_observations", default=3, min=2),
]


def register_opts(conf=masakari.conf.CONF):
    try:
        conf.register_group(POWEROPS_IRONIC_GROUP)
    except cfg.DuplicateOptError:
        pass
    for option in POWEROPS_IRONIC_OPTS:
        try:
            conf.register_opt(option, group=POWEROPS_IRONIC_GROUP)
        except cfg.DuplicateOptError:
            pass


def connection_from_conf(conf=masakari.conf.CONF):
    verify = False if conf.nova_api_insecure else (
        conf.nova_ca_certificates_file or True
    )
    return openstack.connection.Connection(
        auth_url=conf.os_privileged_user_auth_url,
        username=conf.os_privileged_user_name,
        password=conf.os_privileged_user_password,
        project_name=conf.os_privileged_user_tenant,
        user_domain_name=conf.os_user_domain_name,
        project_domain_name=conf.os_project_domain_name,
        region_name=conf.os_region_name,
        interface="internal",
        verify=verify,
    )
