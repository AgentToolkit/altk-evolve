"""Unit tests for the evolve doctor diagnostic (doctor.py).

The doctor checks whether Claude's ``@.evolve/EVOLVE.md`` import is actually
reaching sessions, by comparing the canary token the session reports seeing in
its own context with the one in the installed EVOLVE.md.

We exercise the importable ``diagnose(root, home)`` core directly. doctor.py
resolves the shared lib by parent-walking to ``lib/evolve-lite/`` — that only
works in the rendered tree, so we import the RENDERED Claude copy (same
constraint adapt_memory.py has).
"""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform_integrations

_DOCTOR = (
    Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite" / "skills/evolve-lite/doctor/scripts/doctor.py"
)

# The canary token the installed EVOLVE.md carries. Kept here ONLY for fixture
# construction; doctor.py itself extracts it from the file via regex.
_CANARY = "EVOLVE_IMPORT_CANARY_v2"
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


def _make_project(root, *, claude_md=True, evolve_md=True, canary="line"):
    """Build a fake project tree under `root`. `canary` is "line" (current
    visible canary), "comment" (pre-v2 HTML-comment canary) or None."""
    root.mkdir(parents=True, exist_ok=True)
    if claude_md:
        (root / "CLAUDE.md").write_text(f"# Project rules\n\n{_IMPORT_LINE}\n", encoding="utf-8")
    else:
        (root / "CLAUDE.md").write_text("# Project rules\n", encoding="utf-8")
    if evolve_md:
        evolve_dir = root / ".evolve"
        evolve_dir.mkdir(parents=True, exist_ok=True)
        body = "# Evolve\n"
        if canary == "line":
            body += f"\nEvolve import canary: {_CANARY} (read only by /evolve-lite:doctor; ignore it otherwise)\n"
        elif canary == "comment":
            body = "<!-- evolve-import-canary EVOLVE_IMPORT_CANARY_v1 -->\n" + body
        (evolve_dir / "EVOLVE.md").write_text(body, encoding="utf-8")


def test_ok_when_session_saw_installed_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root)

    code, message = doctor.diagnose(root, _CANARY)
    assert code == "OK", message


def test_import_disabled_when_session_saw_no_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root)

    code, message = doctor.diagnose(root, False)
    assert code == "IMPORT_DISABLED", message
    # The exact project root must appear in the remediation.
    assert str(root) in message


def test_stale_when_session_saw_a_different_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root)

    code, message = doctor.diagnose(root, "EVOLVE_IMPORT_CANARY_v1")
    assert code == "STALE_EVOLVE_MD", message


def test_unknown_when_session_result_missing(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root)

    code, _ = doctor.diagnose(root, None)
    assert code == "UNKNOWN"


def test_not_installed_when_no_import_line(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root, claude_md=False)

    code, _ = doctor.diagnose(root, _CANARY)
    assert code == "NOT_INSTALLED"


def test_not_installed_when_evolve_md_missing(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root, evolve_md=False)

    code, _ = doctor.diagnose(root, _CANARY)
    assert code == "NOT_INSTALLED"


def test_stale_evolve_md_when_no_canary(doctor, tmp_path):
    root = tmp_path / "proj"
    _make_project(root, canary=None)

    code, _ = doctor.diagnose(root, False)
    assert code == "STALE_EVOLVE_MD"


def test_stale_evolve_md_when_canary_only_in_html_comment(doctor, tmp_path):
    """Pre-v2 installs hid the canary in an HTML comment, which Claude strips
    before the model sees it — the session could never report it, so refresh."""
    root = tmp_path / "proj"
    _make_project(root, canary="comment")

    code, _ = doctor.diagnose(root, "EVOLVE_IMPORT_CANARY_v1")
    assert code == "STALE_EVOLVE_MD"


def test_rendered_evolve_md_canary_matches_doctor(doctor):
    """The canary the template ships must be the one the doctor's regex extracts."""
    rendered = _DOCTOR.parents[4] / "EVOLVE.md"
    match = doctor._CANARY_RE.search(rendered.read_text(encoding="utf-8"))
    assert match, f"no visible canary line in {rendered}"
    assert match.group(1) == _CANARY
