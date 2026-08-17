import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON_SOURCE_ROOTS = (
    ROOT / "ansible/roles/powerops/library",
    ROOT / "plugins/masakari_ironic_fence/src",
    ROOT / "plugins/masakari_ironic_fence/tests",
    ROOT / "plugins/mistral_power_actions/src",
    ROOT / "plugins/mistral_power_actions/tests",
    ROOT / "tests/powerops",
)


def powerops_python_sources():
    sources = [ROOT / "tools/powerops_cli.py"]
    for source_root in PYTHON_SOURCE_ROOTS:
        sources.extend(source_root.rglob("*.py"))
    return sorted(set(sources))


def test_every_powerops_python_source_parses_as_python_3_9():
    sources = powerops_python_sources()

    assert len(sources) >= 25
    for path in sources:
        ast.parse(
            path.read_text(),
            filename=str(path.relative_to(ROOT)),
            feature_version=(3, 9),
        )
