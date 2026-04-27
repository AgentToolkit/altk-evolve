"""Tests for evolve-lite lib/config.py — minimal YAML reader/writer."""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / "platform-integrations/claude/plugins/evolve-lite/lib"),
)
import config as cfg_module

pytestmark = pytest.mark.platform_integrations


class TestCast:
    """_cast() converts YAML scalar strings to Python types."""

    def test_true_variants(self):
        assert cfg_module._cast("true") is True
        assert cfg_module._cast("True") is True
        assert cfg_module._cast("yes") is True

    def test_false_variants(self):
        assert cfg_module._cast("false") is False
        assert cfg_module._cast("False") is False
        assert cfg_module._cast("no") is False

    def test_null_variants(self):
        assert cfg_module._cast("null") is None
        assert cfg_module._cast("~") is None
        assert cfg_module._cast("") is None

    def test_integer(self):
        assert cfg_module._cast("0") == 0
        assert cfg_module._cast("42") == 42
        assert cfg_module._cast("-7") == -7

    def test_float(self):
        assert cfg_module._cast("3.14") == pytest.approx(3.14)

    def test_empty_list_literal(self):
        assert cfg_module._cast("[]") == []

    def test_double_quoted_string(self):
        assert cfg_module._cast('"hello world"') == "hello world"

    def test_single_quoted_string(self):
        assert cfg_module._cast("'single quoted'") == "single quoted"

    def test_plain_string_passthrough(self):
        remote = "git@github.com:alice/evolve.git"
        assert cfg_module._cast(remote) == remote


class TestParseYaml:
    """_parse_yaml() reads the minimal YAML subset used by evolve.config.yaml."""

    def test_simple_scalar(self):
        result = cfg_module._parse_yaml("key: value\n")
        assert result["key"] == "value"

    def test_integer_scalar(self):
        result = cfg_module._parse_yaml("port: 8080\n")
        assert result["port"] == 8080

    def test_comment_stripped(self):
        result = cfg_module._parse_yaml("key: value # inline comment\n")
        assert result["key"] == "value"

    def test_nested_mapping(self):
        text = "identity:\n  user: alice\n  email: alice@example.com\n"
        result = cfg_module._parse_yaml(text)
        assert result["identity"]["user"] == "alice"
        assert result["identity"]["email"] == "alice@example.com"

    def test_boolean_nested(self):
        text = "sync:\n  on_session_start: true\n"
        result = cfg_module._parse_yaml(text)
        assert result["sync"]["on_session_start"] is True

    def test_list_of_dicts(self):
        text = "repos:\n  - name: bob\n    scope: read\n    remote: git@github.com:bob/evolve.git\n    branch: main\n"
        result = cfg_module._parse_yaml(text)
        repos = result["repos"]
        assert isinstance(repos, list)
        assert len(repos) == 1
        assert repos[0]["name"] == "bob"
        assert repos[0]["branch"] == "main"

    def test_multiple_repos(self):
        text = (
            "repos:\n"
            "  - name: alice\n    scope: read\n"
            "    remote: git@github.com:alice/evolve.git\n    branch: main\n"
            "  - name: bob\n    scope: read\n"
            "    remote: git@github.com:bob/evolve.git\n    branch: main\n"
        )
        result = cfg_module._parse_yaml(text)
        assert len(result["repos"]) == 2

    def test_empty_input(self):
        assert cfg_module._parse_yaml("") == {}

    def test_blank_lines_ignored(self):
        result = cfg_module._parse_yaml("\n\nkey: value\n\n")
        assert result["key"] == "value"


class TestLoadConfig:
    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        assert cfg_module.load_config(str(tmp_path)) == {}

    def test_parses_existing_file(self, tmp_path):
        (tmp_path / "evolve.config.yaml").write_text("identity:\n  user: alice\n")
        result = cfg_module.load_config(str(tmp_path))
        assert result["identity"]["user"] == "alice"


class TestSaveConfig:
    def test_creates_file(self, tmp_path):
        cfg_module.save_config({"key": "val"}, str(tmp_path))
        assert (tmp_path / "evolve.config.yaml").exists()

    def test_content_is_readable(self, tmp_path):
        cfg_module.save_config({"key": "val"}, str(tmp_path))
        text = (tmp_path / "evolve.config.yaml").read_text()
        assert "key: val" in text


class TestRoundtrip:
    def test_full_config_roundtrip(self, tmp_path):
        original = {
            "identity": {"user": "alice"},
            "repos": [
                {
                    "name": "memory",
                    "scope": "write",
                    "remote": "git@github.com:alice/evolve.git",
                    "branch": "main",
                    "notes": "public memory",
                },
                {
                    "name": "bob",
                    "scope": "read",
                    "remote": "git@github.com:bob/evolve.git",
                    "branch": "main",
                    "notes": "",
                },
            ],
            "sync": {"on_session_start": True},
        }
        cfg_module.save_config(original, str(tmp_path))
        loaded = cfg_module.load_config(str(tmp_path))

        assert loaded["identity"]["user"] == "alice"
        assert loaded["sync"]["on_session_start"] is True
        repos = loaded["repos"]
        assert repos[0]["scope"] == "write"
        assert repos[0]["notes"] == "public memory"
        assert repos[1]["name"] == "bob"

    def test_empty_repos_roundtrip(self, temp_project_dir):
        cfg_module.save_config({"repos": []}, str(temp_project_dir))
        loaded = cfg_module.load_config(str(temp_project_dir))
        assert loaded["repos"] == []


class TestNormalizeRepos:
    def test_modern_config_pass_through(self):
        cfg = {
            "repos": [
                {
                    "name": "memory",
                    "scope": "write",
                    "remote": "git@github.com:alice/evolve.git",
                    "branch": "main",
                    "notes": "shared",
                },
            ]
        }
        repos = cfg_module.normalize_repos(cfg)
        assert len(repos) == 1
        assert repos[0]["scope"] == "write"
        assert repos[0]["notes"] == "shared"

    def test_invalid_scope_entries_dropped(self, capsys):
        cfg = {
            "repos": [
                {"name": "x", "scope": "weird", "remote": "git@x:y/z.git"},
                {"name": "y", "scope": "write", "remote": "git@x:y/y.git"},
            ]
        }
        repos = cfg_module.normalize_repos(cfg)
        assert [r["name"] for r in repos] == ["y"]
        assert "unknown scope" in capsys.readouterr().err

    def test_scope_whitespace_tolerated(self):
        cfg = {"repos": [{"name": "x", "scope": " write ", "remote": "git@x:y/z.git"}]}
        repos = cfg_module.normalize_repos(cfg)
        assert len(repos) == 1
        assert repos[0]["scope"] == "write"

    def test_missing_scope_defaults_to_read(self):
        cfg = {"repos": [{"name": "x", "remote": "git@x:y/z.git"}]}
        repos = cfg_module.normalize_repos(cfg)
        assert len(repos) == 1
        assert repos[0]["scope"] == "read"
        assert repos[0]["name"] == "x"
        assert repos[0]["remote"] == "git@x:y/z.git"

    def test_returns_empty_for_missing_or_non_list_repos(self):
        assert cfg_module.normalize_repos({}) == []
        assert cfg_module.normalize_repos({"repos": "not a list"}) == []
        assert cfg_module.normalize_repos(None) == []

    def test_entries_missing_required_fields_dropped(self):
        cfg = {
            "repos": [
                {"name": "ok", "remote": "git@x:y/z.git"},
                {"name": "", "remote": "git@x:y/z.git"},
                {"name": "no-remote"},
                "garbage",
            ]
        }
        repos = cfg_module.normalize_repos(cfg)
        assert [r["name"] for r in repos] == ["ok"]

    def test_duplicate_names_deduplicated(self):
        cfg = {
            "repos": [
                {"name": "same", "remote": "git@x:y/a.git"},
                {"name": "same", "remote": "git@x:y/b.git"},
            ]
        }
        repos = cfg_module.normalize_repos(cfg)
        assert len(repos) == 1
        assert repos[0]["remote"] == "git@x:y/a.git"


class TestSetRepos:
    def test_replaces_repos_list(self):
        cfg = {"repos": [{"name": "old", "scope": "read", "remote": "git@x:y/o.git"}]}
        cfg_module.set_repos(cfg, [{"name": "new", "scope": "write", "remote": "git@x:y/n.git"}])
        assert [r["name"] for r in cfg["repos"]] == ["new"]

    def test_sanitizes_and_dedupes(self):
        cfg = {}
        cfg_module.set_repos(
            cfg,
            [
                {"name": "bad"},  # missing remote → dropped
                {"name": "ok", "remote": "git@x:y/a.git"},
                {"name": "ok", "remote": "git@x:y/b.git"},  # duplicate → dropped
            ],
        )
        assert len(cfg["repos"]) == 1
        assert cfg["repos"][0]["name"] == "ok"


class TestWriteAndReadRepos:
    def test_write_and_read_split(self):
        cfg = {
            "repos": [
                {"name": "a", "scope": "write", "remote": "git@x:y/a.git"},
                {"name": "b", "scope": "read", "remote": "git@x:y/b.git"},
                {"name": "c", "scope": "write", "remote": "git@x:y/c.git"},
            ]
        }
        assert [r["name"] for r in cfg_module.write_repos(cfg)] == ["a", "c"]
        assert [r["name"] for r in cfg_module.read_repos(cfg)] == ["b"]


class TestGetRepo:
    def test_returns_repo_by_name(self):
        cfg = {"repos": [{"name": "bob", "scope": "read", "remote": "git@x:y/z.git"}]}
        repo = cfg_module.get_repo(cfg, "bob")
        assert repo is not None
        assert repo["name"] == "bob"

    def test_returns_none_when_missing(self):
        assert cfg_module.get_repo({"repos": []}, "missing") is None
        assert cfg_module.get_repo({}, "missing") is None


class TestIsValidRepoName:
    def test_accepts_safe_names(self):
        for name in ["alice", "alice-bob", "alice_bob", "alice.bob", "a1", "AB_C"]:
            assert cfg_module.is_valid_repo_name(name)

    def test_rejects_unsafe_names(self):
        for name in [
            "",
            ".",
            "..",
            "alice/bob",
            "alice bob",
            "alice:bob",
            "../evil",
            "-rf",
            "alice\\bob",
            None,
            0,
            [],
        ]:
            assert not cfg_module.is_valid_repo_name(name)
