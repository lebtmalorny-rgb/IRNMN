from pathlib import Path
import re
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs/powerops"
RUNBOOK = DOCS / "POWEROPS-INSTALL.md"
REQUIRED_CHAPTERS = [
    "Подготовка checkout и deployment host",
    "Итоговое дерево файлов",
    "Inventory",
    "Globals и BMC records",
    "PVS/SberLinux registry fragment",
    "Service overrides",
    "Сборка и публикация images",
    "Kolla-Ansible prechecks",
    "Deploy и reconfigure",
    "Ironic enrollment и HA validation",
    "Masakari emergency fencing validation",
    "Mistral planned workflow validation",
    "Диагностика",
    "Rollback и decommission",
]


def _runbook():
    return RUNBOOK.read_text()


def _shell_commands(markdown):
    commands = []
    in_shell = False
    pending = ""
    for line in markdown.splitlines():
        if line.startswith("```"):
            if in_shell and pending:
                commands.append(pending)
                pending = ""
            in_shell = line.strip() in {"```bash", "```shell"}
            continue
        if not in_shell:
            continue
        stripped = line.strip()
        if pending:
            pending += " " + stripped.rstrip("\\").strip()
            if not stripped.endswith("\\"):
                commands.append(pending)
                pending = ""
        elif stripped.startswith("kolla-ansible"):
            pending = stripped.rstrip("\\").strip()
            if not stripped.endswith("\\"):
                commands.append(pending)
                pending = ""
    return commands


def test_runbook_contains_every_required_chapter_and_no_placeholders():
    source = _runbook()
    for chapter in REQUIRED_CHAPTERS:
        assert "## {}".format(chapter) in source
    for marker in ("TODO", "TBD", "FIXME", "<...>", "…"):
        assert marker not in source


def test_every_local_markdown_link_exists():
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", _runbook())
    local = [link for link in links if not link.startswith(("http://", "https://", "#"))]
    assert local
    missing = [link for link in local if not (DOCS / link).resolve().exists()]
    assert missing == []


def test_diagrams_are_valid_editable_svg_and_png():
    svg = DOCS / "ironic-ha-power-workflows.svg"
    png = DOCS / "ironic-ha-power-workflows.png"
    ET.parse(svg)
    assert png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_every_documented_kolla_command_is_checkout_explicit():
    commands = _shell_commands(_runbook())
    assert commands
    for command in commands:
        assert '--configdir "$PWD/etc/kolla"' in command
        assert '-i "$PWD/etc/kolla/inventory"' in command


def test_live_operations_are_explicitly_not_run():
    source = _runbook()
    assert source.count("NOT RUN IN THIS WORKSPACE") >= 8
    assert "publish-images" in source
    assert "No target deployment was authorized" in source
