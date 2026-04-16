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
        text = (
            "subscriptions:\n"
            "  - name: bob\n"
            "    remote: git@github.com:bob/evolve.git\n"
            "    branch: main\n"
        )
        result = cfg_module._parse_yaml(text)
        subs = result["subscriptions"]
        assert isinstance(subs, list)
        assert len(subs) == 1
        assert subs[0]["name"] == "bob"
        assert subs[0]["branch"] == "main"

    def test_multiple_subscriptions(self):
        text = (
            "subscriptions:\n"
            "  - name: alice\n"
            "    remote: git@github.com:alice/evolve.git\n"
            "    branch: main\n"
            "  - name: bob\n"
            "    remote: git@github.com:bob/evolve.git\n"
            "    branch: main\n"
        )
        result = cfg_module._parse_yaml(text)
        assert len(result["subscriptions"]) == 2

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
            "public_repo": {"remote": "git@github.com:alice/evolve.git", "branch": "main"},
            "subscriptions": [
                {"name": "bob", "remote": "git@github.com:bob/evolve.git", "branch": "main"}
            ],
            "sync": {"on_session_start": True},
        }
        cfg_module.save_config(original, str(tmp_path))
        loaded = cfg_module.load_config(str(tmp_path))

        assert loaded["identity"]["user"] == "alice"
        assert loaded["public_repo"]["branch"] == "main"
        assert loaded["sync"]["on_session_start"] is True
        assert loaded["subscriptions"][0]["name"] == "bob"

    def test_empty_subscriptions_roundtrip(self, tmp_path):
        cfg_module.save_config({"subscriptions": []}, str(tmp_path))
        loaded = cfg_module.load_config(str(tmp_path))
        assert loaded["subscriptions"] == []
