import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PYTHON_SOURCE_ROOTS = (
    ROOT / "plugins",
    ROOT / "tools",
    ROOT / "ansible/library",
    ROOT / "ansible/roles/powerops",
)
DECLARATIVE_SOURCE_ROOTS = (
    ROOT / "etc/kolla",
    ROOT / "mistral",
    ROOT / "ansible/roles/powerops",
)
FORBIDDEN_IRONIC_CALLS = {
    "adopt_node",
    "clean_node",
    "inspect_node",
    "rebuild_node",
}
FORBIDDEN_PROVISION_STATES = {
    "active",
    "adopt",
    "clean",
    "inspect",
    "provide",
    "rebuild",
}
PROVISION_STATE_PATTERN = re.compile(
    r"(?:desired_|target_)?provision_state\s*[:=]\s*['\"]?"
    r"(active|adopt|clean|inspect|provide|rebuild)\b",
    re.IGNORECASE,
)


def _files(root, suffixes):
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and "tests" not in path.parts
        and "build" not in path.parts
        and "dist" not in path.parts
    ]


def _literal_argument(call):
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return call.args[1].value
    for keyword in call.keywords:
        if keyword.arg in {"target", "provision_state"} and isinstance(
            keyword.value, ast.Constant
        ):
            return keyword.value.value
    return None


def test_python_sources_only_allow_manage_provision_transition():
    violations = []
    for source_root in PYTHON_SOURCE_ROOTS:
        for path in _files(source_root, {".py"}):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(
                    node.func, ast.Attribute
                ):
                    continue
                method = node.func.attr
                if method in FORBIDDEN_IRONIC_CALLS:
                    violations.append("{}:{} {}".format(path, node.lineno, method))
                if method == "set_node_provision_state":
                    target = _literal_argument(node)
                    if target != "manage":
                        violations.append(
                            "{}:{} set_node_provision_state({!r})".format(
                                path, node.lineno, target
                            )
                        )
    assert violations == []


def test_declarative_sources_never_request_provisioning_states():
    violations = []
    for source_root in DECLARATIVE_SOURCE_ROOTS:
        for path in _files(source_root, {".conf", ".ini", ".yaml", ".yml"}):
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                match = PROVISION_STATE_PATTERN.search(line)
                if match and match.group(1).lower() in FORBIDDEN_PROVISION_STATES:
                    violations.append("{}:{} {}".format(path, line_number, line))
    assert violations == []


def test_mistral_redis_url_is_registered_as_secret():
    source = (
        ROOT
        / "plugins/mistral_power_actions/src/openstack_power_actions/clients.py"
    ).read_text()
    tree = ast.parse(source)
    redis_options = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "StrOpt"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "redis_url"
    ]
    assert len(redis_options) == 1
    secret = next(
        keyword.value
        for keyword in redis_options[0].keywords
        if keyword.arg == "secret"
    )
    assert isinstance(secret, ast.Constant) and secret.value is True
