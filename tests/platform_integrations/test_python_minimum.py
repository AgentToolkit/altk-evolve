"""
The installer's Python gate must cover what the plugin scripts it installs need (#391).

install.sh only checks the interpreter once, at install time. The skill scripts
it ships then run under that same ``python3``. If a script uses an API newer
than the gate (``Path.is_relative_to`` is 3.9+), the install succeeds and the
skill fails later with an ``AttributeError``. These tests keep the two in step:
they scan the plugin scripts for version-gated syntax and APIs and assert the
gate in install.sh is at least the highest version found.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_INSTALL_SH = _REPO_ROOT / "platform-integrations" / "install.sh"
# Scripts the installer ships and the user's python3 runs: the rendered and
# static files under platform-integrations/, plus their plugin-source/ originals.
# Excluded: build_plugins.py (a dev tool run through uv), and the Hermes
# provider, which runs inside Hermes's own interpreter rather than python3.
_PLUGIN_SOURCE = _REPO_ROOT / "plugin-source"
_SCANNED_ROOTS = (_PLUGIN_SOURCE, _REPO_ROOT / "platform-integrations")
_DEV_ONLY = {"build_plugins.py"}
_OTHER_INTERPRETER = (_REPO_ROOT / "platform-integrations" / "hermes", _PLUGIN_SOURCE / "_hermes")

# Attribute names whose stdlib meaning needs at least this minor version of 3.x.
# Only names that are unambiguous in this codebase are listed; extend as needed.
_ATTRIBUTE_MINIMUM = {
    "is_relative_to": 9,  # pathlib.PurePath
    "with_stem": 9,  # pathlib.PurePath
    "removeprefix": 9,  # str / bytes
    "removesuffix": 9,  # str / bytes
    "UTC": 11,  # datetime.UTC (use datetime.timezone.utc, which works everywhere)
    "hardlink_to": 10,  # pathlib.Path
    "bit_count": 10,  # int
    "is_junction": 12,  # pathlib.Path
    "full_match": 13,  # pathlib.PurePath
}
_MODULE_MINIMUM = {
    "zoneinfo": 9,
    "graphlib": 9,
    "tomllib": 11,
}


def _gate_minor():
    """The minor version install.sh requires (MIN_PYTHON_MINOR)."""
    match = re.search(r"^MIN_PYTHON_MINOR=(\d+)$", _INSTALL_SH.read_text(), re.MULTILINE)
    assert match, "install.sh no longer defines MIN_PYTHON_MINOR"
    return int(match.group(1))


def _plugin_scripts():
    scripts = [
        p
        for root in _SCANNED_ROOTS
        for p in sorted(root.rglob("*.py"))
        if p.name not in _DEV_ONLY and "__pycache__" not in p.parts and not any(p.is_relative_to(o) for o in _OTHER_INTERPRETER)
    ]
    assert scripts, f"no plugin scripts found under {_SCANNED_ROOTS}"
    return scripts


def _required_minor(path):
    """Return (minor, reason) for the newest 3.x API or module `path` uses."""
    tree = ast.parse(path.read_text(), filename=str(path))
    best = (0, "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _ATTRIBUTE_MINIMUM:
            need = (_ATTRIBUTE_MINIMUM[node.attr], f".{node.attr} at line {node.lineno}")
        elif isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names if a.name.split(".")[0] in _MODULE_MINIMUM]
            need = max(((_MODULE_MINIMUM[m], f"import {m} at line {node.lineno}") for m in mods), default=(0, ""))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in _MODULE_MINIMUM:
            mod = node.module.split(".")[0]
            need = (_MODULE_MINIMUM[mod], f"from {mod} import at line {node.lineno}")
        else:
            continue
        best = max(best, need)
    return best


@pytest.mark.platform_integrations
class TestPythonMinimum:
    def test_messages_use_the_gate_constant(self):
        """Both error messages and the check itself come from MIN_PYTHON_MINOR, so they can't disagree."""
        text = _INSTALL_SH.read_text()
        block = text[text.index("# ─── Python check") : text.index("# ─── Source resolution")]
        assert "sys.version_info >= (3,${MIN_PYTHON_MINOR})" in block
        assert "Install Python 3.${MIN_PYTHON_MINOR}+" in block
        assert "python3 >= 3.${MIN_PYTHON_MINOR} is required" in block
        code = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
        assert not re.search(r"3\.\d", code.replace("3.${MIN_PYTHON_MINOR}", "")), "hard-coded version in the Python check"

    def test_gate_covers_what_plugin_scripts_need(self):
        gate = _gate_minor()
        needs = {str(p.relative_to(_REPO_ROOT)): _required_minor(p) for p in _plugin_scripts()}
        too_new = {path: f"3.{minor} ({why})" for path, (minor, why) in needs.items() if minor > gate}
        assert not too_new, f"install.sh accepts Python 3.{gate}, but these plugin scripts need newer: {too_new}"
        # Sanity check that the scan sees what prompted this test.
        assert max(minor for minor, _ in needs.values()) >= 9

    @pytest.mark.parametrize("path", _plugin_scripts(), ids=lambda p: str(p.relative_to(_REPO_ROOT)))
    def test_plugin_script_syntax_parses_at_the_gate_version(self, path):
        """No syntax newer than the gate (e.g. `match`, PEP 695 generics) in shipped scripts."""
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, _gate_minor()))


_FAKE_PYTHON3 = """#!/usr/bin/env bash
# python3 that reports $FAKE_PY_VERSION (e.g. 3.8.20) to install.sh's version check
# and delegates everything else to the real interpreter.
if [ "${1:-}" = "--version" ]; then echo "Python $FAKE_PY_VERSION"; exit 0; fi
if [ "${1:-}" = "-c" ]; then
  exec "$REAL_PYTHON" -c "import sys; sys.version_info = tuple(int(x) for x in '$FAKE_PY_VERSION'.split('.')); exec(sys.argv[1])" "$2"
fi
exec "$REAL_PYTHON" "$@"
"""


@pytest.fixture
def fake_python_path(tmp_path):
    bin_dir = tmp_path / "fakepy"
    bin_dir.mkdir()
    fake = bin_dir / "python3"
    fake.write_text(_FAKE_PYTHON3)
    fake.chmod(0o755)
    return bin_dir


def _run_status(fake_python_path, version, project_dir):
    env = {
        **os.environ,
        "PATH": f"{fake_python_path}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_PY_VERSION": version,
        "REAL_PYTHON": sys.executable,
    }
    return subprocess.run(
        ["bash", str(_INSTALL_SH), "status", "--dir", str(project_dir)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.platform_integrations
class TestPythonGate:
    def test_python_38_is_refused(self, fake_python_path, temp_project_dir):
        result = _run_status(fake_python_path, "3.8.20", temp_project_dir)

        assert result.returncode == 1, result.stdout
        assert "python3 >= 3.9 is required. Found: Python 3.8.20" in result.stderr

    def test_python_39_is_accepted(self, fake_python_path, temp_project_dir):
        result = _run_status(fake_python_path, "3.9.0", temp_project_dir)

        assert result.returncode == 0, result.stderr
        assert "Evolve installation status" in result.stdout
