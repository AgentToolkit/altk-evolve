import json

from altk_forge.cli import validate as validate_cli


def test_main_exits_non_zero_on_an_invalid_file(tmp_path, trajectory, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(trajectory))
    bad.write_text(json.dumps({**trajectory, "schema_version": "1.0"}))
    try:
        validate_cli.main([str(good), str(bad)])
    except SystemExit as e:
        assert e.code == 1
    out = capsys.readouterr().out
    assert "ok   " in out and "FAIL" in out
