"""Unit tests for the evolve doctor diagnostic (doctor.py).

The doctor checks whether Claude's ``@.evolve/EVOLVE.md`` import is actually
reaching sessions, by extracting the canary token from the installed EVOLVE.md
and grepping recent Claude project transcripts for it.

We exercise the importable ``diagnose(root, home)`` core directly. doctor.py
resolves the shared lib by parent-walking to ``lib/evolve-lite/`` — that only
works in the rendered tree, so we import the RENDERED Claude copy (same
constraint adapt_memory.py has).
"""

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_DOCTOR = (
    Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite" / "skills/evolve-lite/doctor/scripts/doctor.py"
)

# The canary token the installed EVOLVE.md carries. Kept here ONLY for fixture
# construction; doctor.py itself extracts it from the file via regex.
_CANARY = "EVOLVE_IMPORT_CANARY_v1"
_IMPORT_LINE = "@.evolve/EVOLVE.md"


def _load_doctor():
    spec = importlib.util.spec_from_file_location("evolve_doctor", _DOCTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clear_evolve_dir(monkeypatch):
    """doctor.py honors $EVOLVE_DIR; clear it so tests resolve .evolve under the
    temp project root, not a developer's ambient override."""
    monkeypatch.delenv("EVOLVE_DIR", raising=False)


@pytest.fixture
def doctor():
    return _load_doctor()


def _make_project(root, *, claude_md=True, evolve_md=True, canary=True):
    """Build a fake project tree under `root`."""
    root.mkdir(parents=True, exist_ok=True)
    if claude_md:
        (root / "CLAUDE.md").write_text(f"# Project rules\n\n{_IMPORT_LINE}\n", encoding="utf-8")
    else:
        (root / "CLAUDE.md").write_text("# Project rules\n", encoding="utf-8")
    if evolve_md:
        evolve_dir = root / ".evolve"
        evolve_dir.mkdir(parents=True, exist_ok=True)
        body = "# Evolve\n"
        if canary:
            body = f"<!-- evolve-import-canary {_CANARY} -->\n" + body
        (evolve_dir / "EVOLVE.md").write_text(body, encoding="utf-8")


def _slug(root):
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


def _write_transcript(home, root, *, with_canary):
    proj = home / ".claude" / "projects" / _slug(root)
    proj.mkdir(parents=True, exist_ok=True)
    content = '{"role":"user","content":"hello"}\n'
    if with_canary:
        content += '{"role":"system","content":"' + _CANARY + '"}\n'
    (proj / "session.jsonl").write_text(content, encoding="utf-8")


def test_ok_when_canary_in_transcript(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root)
    _write_transcript(home, root, with_canary=True)

    code, message = doctor.diagnose(root, home)
    assert code == "OK", message


def test_import_disabled_when_transcript_lacks_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root)
    _write_transcript(home, root, with_canary=False)

    code, message = doctor.diagnose(root, home)
    assert code == "IMPORT_DISABLED", message
    # The exact project root must appear in the remediation.
    assert str(root) in message


def test_not_installed_when_no_import_line(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root, claude_md=False)
    _write_transcript(home, root, with_canary=True)

    code, _ = doctor.diagnose(root, home)
    assert code == "NOT_INSTALLED"


def test_not_installed_when_evolve_md_missing(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root, evolve_md=False)
    _write_transcript(home, root, with_canary=True)

    code, _ = doctor.diagnose(root, home)
    assert code == "NOT_INSTALLED"


def test_stale_evolve_md_when_no_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root, canary=False)
    _write_transcript(home, root, with_canary=False)

    code, _ = doctor.diagnose(root, home)
    assert code == "STALE_EVOLVE_MD"


def test_unknown_when_no_transcripts(doctor, tmp_path):
    root = tmp_path / "proj"
    home = tmp_path / "home"
    home.mkdir()
    _make_project(root)
    # No transcript written.

    code, _ = doctor.diagnose(root, home)
    assert code == "UNKNOWN"
