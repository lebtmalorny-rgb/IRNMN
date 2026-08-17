from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_kolla_2025_1_extension_points_exist():
    required = [
        ROOT / "ansible/site.yml",
        ROOT / "ansible/roles/ironic/tasks/config.yml",
        ROOT / "ansible/roles/masakari/tasks/config.yml",
        ROOT / "ansible/roles/masakari/defaults/main.yml",
        ROOT / "ansible/roles/masakari/templates/masakari-engine.json.j2",
        ROOT / "ansible/roles/mistral/tasks/config.yml",
        ROOT / "ansible/roles/mistral/defaults/main.yml",
        ROOT / "kolla_ansible/ansible.py",
    ]
    assert [str(path) for path in required if not path.is_file()] == []


def test_configdir_is_supported():
    source = (ROOT / "kolla_ansible/ansible.py").read_text()
    assert 'CONFIG_PATH_ENV = "KOLLA_CONFIG_PATH"' in source
    assert '"--configdir"' in source


def test_masakari_engine_loads_merged_masakari_conf_only():
    template = (
        ROOT / "ansible/roles/masakari/templates/masakari-engine.json.j2"
    ).read_text()
    config_task = (
        ROOT / "ansible/roles/masakari/tasks/config.yml"
    ).read_text()
    assert "masakari-engine --config-file /etc/masakari/masakari.conf" in template
    assert "node_custom_config }}/masakari/{{ service_name }}.conf" in config_task


def test_service_specific_image_overrides_exist():
    mistral = (ROOT / "ansible/roles/mistral/defaults/main.yml").read_text()
    masakari = (ROOT / "ansible/roles/masakari/defaults/main.yml").read_text()
    for variable in (
        "mistral_api_image_full",
        "mistral_engine_image_full",
        "mistral_executor_image_full",
    ):
        assert variable in mistral
    assert "masakari_engine_image_full" in masakari
