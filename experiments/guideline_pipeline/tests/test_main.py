"""CLI wiring: argument parsing, adapter lookup, exit codes and the printed report."""

from __future__ import annotations

from pathlib import Path

import pytest

from altk_evolve.frontend.client.evolve_client import EvolveClient

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.adapters import ADAPTERS
from experiments.guideline_pipeline.tests.fakes import FakeAdapter, profile

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ADAPTERS, "fake", FakeAdapter())


def argv(dataset: Path, *extra: str) -> list[str]:
    return ["mine", "--adapter", "fake", "--input", str(dataset), "--namespace", "memories", "--processing-profile", "echo", *extra]


def test_mine_command(client: EvolveClient, dataset: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main(argv(dataset, "--limit", "2")) == 0
    assert capsys.readouterr().out.strip() == (
        "profile echo r1: 2 processed, 0 skipped (checkpointed), 0 failed; 2 entities proposed, 2 updates (ADD=2)"
    )

    client.processing.put("echo", profile("v2", fail_on="task-3"), expected_revision=1)
    assert cli.main(argv(dataset)) == 1
    captured = capsys.readouterr()
    assert captured.err == "failed task-3: RuntimeError: processor failed\n"
    assert "0 processed, 2 skipped (checkpointed), 1 failed" in captured.out


def test_dry_run_never_builds_a_client(dataset: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def no_client() -> EvolveClient:
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(cli, "make_client", no_client)

    assert cli.main(argv(dataset, "--dry-run")) == 0
    assert capsys.readouterr().out.strip() == "dry run: 3 valid, 0 failed"


def test_usage_errors(client: EvolveClient, dataset: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    monkeypatch.setattr(cli, "make_client", lambda: client)

    assert cli.main([*argv(dataset), "--adapter", "nope"]) == 2
    assert "Unknown adapter 'nope' (available: fake)" in capsys.readouterr().err
    assert cli.main(argv(dataset, "--revision", "9")) == 2
    assert "error:" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(argv(dataset, "--limit", "0"))
