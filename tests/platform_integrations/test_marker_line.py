"""
Focused unit tests for FileOps.inject_marker_line / remove_marker_line.

These two generic helpers manage a SINGLE greppable "managed" line in a text
file (the Codex installer uses them to point ~/.codex/AGENTS.md at the on-disk
EVOLVE.md copy; the Claude phase will reuse them). The FileOps class lives
inside the install.sh heredoc, so we extract and exec that Python source into a
throwaway namespace to test the methods in isolation, with no subprocess.
"""

import re
from pathlib import Path

import pytest


MARKER = "<!-- evolve-lite:managed -->"
LINE = f"Read ~/.codex/evolve-lite/EVOLVE.md and follow it. {MARKER}"


@pytest.fixture(scope="module")
def file_ops():
    """Extract the embedded Python from install.sh and return a fresh FileOps()."""
    repo_root = Path(__file__).parent.parent.parent
    script = (repo_root / "platform-integrations" / "install.sh").read_text()
    m = re.search(r"<<'PYEOF'\n(.*)\nPYEOF", script, re.DOTALL)
    assert m, "Could not locate the embedded Python heredoc in install.sh"
    ns = {}
    # Give the module a benign argv so its top-level `sys.argv[1]` read succeeds.
    code = "import sys\nsys.argv = ['install.sh', '', 'status']\n" + m.group(1)
    # Strip the `if __name__ == '__main__': main()` trailer so exec doesn't run the CLI.
    code = code.replace('if __name__ == "__main__":\n    main()', "")
    exec(compile(code, "install.sh:PYEOF", "exec"), ns)
    return ns["FileOps"]()


@pytest.mark.platform_integrations
class TestInjectMarkerLine:
    def test_creates_file_and_parents_when_missing(self, file_ops, tmp_path):
        path = tmp_path / "nested" / "AGENTS.md"
        file_ops.inject_marker_line(path, MARKER, LINE)
        assert path.read_text() == LINE + "\n"

    def test_appends_with_blank_line_when_content_present(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text("# My instructions\n\nPrefer ripgrep.\n")
        file_ops.inject_marker_line(path, MARKER, LINE)
        text = path.read_text()
        # Original content preserved, exactly one managed line, separated by a blank line.
        assert text.startswith("# My instructions\n\nPrefer ripgrep.\n\n")
        assert text.count(MARKER) == 1
        assert text.endswith(LINE + "\n")

    def test_replaces_existing_managed_line_in_place(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        old_line = f"Stale pointer to /old/path. {MARKER}"
        path.write_text(f"# Top\n{old_line}\n# Bottom\n")
        file_ops.inject_marker_line(path, MARKER, LINE)
        text = path.read_text()
        # The whole stale line is replaced; surrounding content untouched.
        assert old_line not in text
        assert text.count(MARKER) == 1
        assert LINE in text
        assert "# Top" in text and "# Bottom" in text
        # No line was added or removed (still 3 lines).
        assert text.splitlines() == ["# Top", LINE, "# Bottom"]

    def test_idempotent_across_repeats(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text("# Existing\n")
        file_ops.inject_marker_line(path, MARKER, LINE)
        first = path.read_text()
        file_ops.inject_marker_line(path, MARKER, LINE)
        file_ops.inject_marker_line(path, MARKER, LINE)
        assert path.read_text() == first
        assert path.read_text().count(MARKER) == 1

    def test_rejects_line_without_marker(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        with pytest.raises(ValueError):
            file_ops.inject_marker_line(path, MARKER, "no marker here")


@pytest.mark.platform_integrations
class TestRemoveMarkerLine:
    def test_no_op_when_file_missing(self, file_ops, tmp_path):
        path = tmp_path / "missing.md"
        file_ops.remove_marker_line(path, MARKER)  # must not raise
        assert not path.exists()

    def test_removes_managed_line_preserving_other_lines(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text(f"# Top\n\n{LINE}\n\n# Bottom\n")
        file_ops.remove_marker_line(path, MARKER)
        text = path.read_text()
        assert MARKER not in text
        assert "# Top" in text and "# Bottom" in text
        # No doubled blank-line gap left where the managed line used to be.
        assert "\n\n\n" not in text

    def test_removes_only_marker_lines(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        path.write_text(f"keep me\n{LINE}\nkeep me too\n")
        file_ops.remove_marker_line(path, MARKER)
        assert path.read_text().splitlines() == ["keep me", "keep me too"]

    def test_inject_then_remove_round_trips(self, file_ops, tmp_path):
        path = tmp_path / "AGENTS.md"
        original = "# My instructions\n\nPrefer ripgrep.\n"
        path.write_text(original)
        file_ops.inject_marker_line(path, MARKER, LINE)
        file_ops.remove_marker_line(path, MARKER)
        text = path.read_text()
        assert MARKER not in text
        assert "# My instructions" in text and "Prefer ripgrep." in text
        assert "\n\n\n" not in text
