from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_plugin_containerfiles_install_only_local_wheels():
    expected_users = {
        "docker/powerops/mistral/Containerfile": "USER mistral",
        "docker/powerops/masakari/Containerfile": "USER masakari",
    }
    for relative_path, expected_user in expected_users.items():
        source = (ROOT / relative_path).read_text()
        assert "ARG BASE_IMAGE" in source
        assert "FROM ${BASE_IMAGE}" in source
        assert "COPY dist/*.whl /tmp/powerops/" in source
        assert "/var/lib/kolla/venv/bin/pip install" in source
        assert "git clone" not in source
        assert "http://" not in source
        assert "https://" not in source
        assert source.rstrip().endswith(expected_user)


def test_globals_map_every_derived_image_to_kolla_runtime():
    data = yaml.safe_load((ROOT / "etc/kolla/globals.yml").read_text())
    expected = {
        "mistral-api": "mistral_api_image_full",
        "mistral-engine": "mistral_engine_image_full",
        "mistral-executor": "mistral_executor_image_full",
        "masakari-engine": "masakari_engine_image_full",
    }
    assert set(data["powerops_base_images"]) == set(expected)
    assert set(data["powerops_derived_images"]) == set(expected)
    for service, variable in expected.items():
        assert data[variable] == "{{ powerops_derived_images['%s'] }}" % service


def test_mistral_event_engine_remains_an_explicit_upstream_image():
    data = yaml.safe_load((ROOT / "etc/kolla/globals.yml").read_text())
    assert "mistral-event-engine" not in data["powerops_derived_images"]
    assert "mistral_event_engine_image_full" not in data
    template = (
        ROOT / "ansible/roles/mistral/templates/mistral-event-engine.json.j2"
    ).read_text()
    assert "mistral-server --server event-engine" in template


def test_pvs_fragment_supplies_matching_base_and_derived_image_maps():
    data = yaml.safe_load(
        (ROOT / "etc/kolla/globals-pvs-fragment.yml").read_text()
    )
    services = {
        "mistral-api",
        "mistral-engine",
        "mistral-executor",
        "masakari-engine",
    }
    assert set(data["powerops_base_images"]) == services
    assert set(data["powerops_derived_images"]) == services
    assert all(
        image.startswith("registry.example.invalid:5000/pvs/")
        for image in data["powerops_base_images"].values()
    )
