import json
import os

import pytest

from altk_forge.sources import filesystem, http


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.json").write_text(json.dumps({"x": 1}))
    (tmp_path / "metadata.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("not a trace")
    (tmp_path / "sub" / "b.json").write_text(json.dumps({"x": 2}))
    (tmp_path / "sub" / "bad.json").write_text("{not json")
    os.utime(tmp_path / "a.json", (1_700_000_000, 1_700_000_000))
    return tmp_path


def test_filesystem_lists_json_files_and_honours_excludes(folder):
    fs = filesystem.Filesystem(str(folder), exclude="metadata.json")
    assert [t["name"] for t in fs.list_traces()] == ["a.json"]
    fs = filesystem.Filesystem(str(folder), recursive=True, exclude="metadata.json, sub/bad.json")
    assert sorted(t["name"] for t in fs.list_traces()) == ["a.json", "sub/b.json"]
    assert "2 JSON files" in fs.check() and "subfolders" in fs.check()


def test_filesystem_trace_ids_are_stable_and_safe():
    tid = filesystem.Filesystem.trace_id("sub dir/run #1.json")
    assert tid == filesystem.Filesystem.trace_id("sub dir/run #1.json")
    assert tid.startswith("sub_dir_run_1-") and len(tid) <= 128
    assert filesystem.Filesystem.trace_id("a.json") != filesystem.Filesystem.trace_id("b.json")


def test_filesystem_since_and_trace_envelope(folder):
    fs = filesystem.Filesystem(str(folder), recursive=True)
    newer = fs.list_traces(since="2024-01-01T00:00:00.000Z")
    assert "a.json" not in [t["name"] for t in newer]  # modified in 2023
    tid = filesystem.Filesystem.trace_id("a.json")
    t = fs.trace(tid)
    assert t["content"] == {"x": 1} and t["path"] == "a.json" and t["uri"].startswith("file://")


def test_filesystem_bad_files_raise_file_trace_errors(folder):
    fs = filesystem.Filesystem(str(folder), recursive=True)
    with pytest.raises(filesystem.FileTraceError, match="is not JSON"):
        fs.trace(filesystem.Filesystem.trace_id("sub/bad.json"))
    with pytest.raises(filesystem.FileTraceError, match="any more"):
        fs.trace("unknown")


def test_filesystem_missing_folder(tmp_path):
    with pytest.raises(http.SourceError, match="No folder"):
        filesystem.Filesystem(str(tmp_path / "gone")).check()


def test_exclude_patterns():
    assert filesystem.exclude_patterns("a.json, b*.json\nsub/*.json") == ["a.json", "b*.json", "sub/*.json"]
    assert filesystem.exclude_patterns(["x", " ", ""]) == ["x"]
    assert filesystem.exclude_patterns(None) == []
