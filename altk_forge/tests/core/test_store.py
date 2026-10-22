import json

from altk_forge.core.store import read_json, write_json


def test_read_json_of_a_missing_file_is_the_default(tmp_path):
    assert read_json(tmp_path / "none.json") is None
    assert read_json(tmp_path / "none.json", {"traces": {}}) == {"traces": {}}


def test_write_json_creates_folders_and_reads_back(tmp_path):
    path = tmp_path / "a" / "b" / "value.json"
    write_json(path, {"ü": [1, 2]})
    assert read_json(path) == {"ü": [1, 2]}
    assert path.read_text().endswith("\n")
    assert "ü" in path.read_text()  # not escaped: the files stay readable


def test_write_json_leaves_no_temporary_file(tmp_path):
    write_json(tmp_path / "value.json", [1])
    write_json(tmp_path / "value.json", [2])
    assert [p.name for p in tmp_path.iterdir()] == ["value.json"]
    assert json.loads((tmp_path / "value.json").read_text()) == [2]


def test_private_files_are_readable_by_their_owner_only(tmp_path):
    write_json(tmp_path / "secret.json", {"key": "x"}, private=True)
    write_json(tmp_path / "public.json", {})
    assert (tmp_path / "secret.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "public.json").stat().st_mode & 0o077 != 0
