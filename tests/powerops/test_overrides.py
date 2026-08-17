import configparser
from pathlib import Path

from jinja2 import Environment
from oslo_config import types


ROOT = Path(__file__).resolve().parents[2]


def _load_ini(relative_path):
    parser = configparser.ConfigParser(interpolation=None)
    loaded = parser.read(ROOT / relative_path)
    assert loaded == [str(ROOT / relative_path)]
    return parser


def test_ironic_is_power_only():
    ironic = _load_ini("etc/kolla/config/ironic.conf")
    assert ironic["DEFAULT"]["enabled_hardware_types"] == "redfish,ipmi"
    assert ironic["DEFAULT"]["enabled_power_interfaces"] == "redfish,ipmitool"
    assert (
        ironic["DEFAULT"]["enabled_management_interfaces"]
        == "redfish,ipmitool"
    )
    assert ironic["DEFAULT"]["enabled_network_interfaces"] == "noop"
    assert ironic["conductor"].getboolean("automated_clean") is False
    assert "enabled_boot_interfaces" not in ironic["DEFAULT"]
    assert "enabled_deploy_interfaces" not in ironic["DEFAULT"]


def _ordered_flow(masakari, option):
    parser = types.Dict(
        bounds=False,
        value_type=types.List(
            bounds=True,
            item_type=types.String(quotes=True),
        ),
    )
    flow = parser(masakari["taskflow_driver_recovery_flows"][option])
    return flow["pre"] + flow["main"] + flow["post"]


def test_fencing_precedes_preparation_and_evacuation_in_both_host_flows():
    masakari = _load_ini("etc/kolla/config/masakari/masakari-engine.conf")
    for option in (
        "host_auto_failure_recovery_tasks",
        "host_rh_failure_recovery_tasks",
    ):
        ordered = _ordered_flow(masakari, option)
        assert ordered[:2] == ["disable_compute_service_task", "ironic_fence"]
        assert ordered.index("ironic_fence") < ordered.index(
            "prepare_HA_enabled_instances_task"
        )
        assert ordered.index("ironic_fence") < ordered.index(
            "evacuate_instances_task"
        )


def test_masakari_fence_confirmation_is_stable_and_bounded():
    masakari = _load_ini("etc/kolla/config/masakari/masakari-engine.conf")
    options = masakari["powerops_ironic"]
    assert options.getint("power_timeout") == 180
    assert options.getint("poll_interval") == 5
    assert options.getint("stable_off_observations") == 3


def test_mistral_executor_has_owner_lock_and_polling_settings():
    path = ROOT / "etc/kolla/config/mistral/mistral-executor.conf"
    Environment().parse(path.read_text())
    mistral = _load_ini("etc/kolla/config/mistral/mistral-executor.conf")
    options = mistral["powerops"]
    assert "groups['redis']" in options["redis_sentinel_hosts"]
    assert options["redis_master_name"] == "kolla"
    assert options["redis_password"] == "{{ redis_master_password }}"
    assert options.getint("redis_db") == 4
    assert options["region_name"] == "RegionOne"
    assert options["interface"] == "internal"
    assert options.getboolean("verify") is True
    assert options.getint("lock_ttl") == 900
    assert options.getint("drain_timeout") == 600
    assert options.getint("power_timeout") == 180
    assert options.getint("poll_interval") == 5
    assert options.getint("stable_observations") == 3
