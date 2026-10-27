"""The import-lessons stage, on hand-written synthetic lesson files under fixtures/lessons."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import litellm
import pytest

import altk_evolve.backend.base as backend_base
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.llm.guidelines.clustering import stored_support
from altk_evolve.schema.core import Entity

from experiments.guideline_pipeline import __main__ as cli
from experiments.guideline_pipeline.guidelines import fetch_guidelines, guideline_rows
from experiments.guideline_pipeline.stages.import_lessons import import_lessons, load_lesson_dir, task_description

from experiments.guideline_pipeline.tests.test_commands import mock_consolidation

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "lessons"
# The fixtures: aaa0001_1 failed (a blank lesson, then one lesson), bbb0002_1 succeeded (two lessons), ccc0003_2 has none.
LESSONS = 3


@pytest.fixture
def lessons(tmp_path: Path) -> Path:
    """A writable copy of the fixtures, named as a model's lesson directory is."""
    return Path(shutil.copytree(FIXTURES, tmp_path / "guidelines_per_task_fake"))


@pytest.fixture(autouse=True)
def llm_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record and refuse every litellm.completion call, however the caller imported it.

    A refused call inside a file's write surfaces as a reported failure, so tests also
    assert on the recorded calls rather than rely on the AssertionError propagating.
    """
    original = litellm.completion
    calls: list[tuple] = []

    def refuse(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("import-lessons called an LLM")

    monkeypatch.setattr(litellm, "completion", refuse)
    for module in list(sys.modules.values()):  # modules that did `from litellm import completion`
        if getattr(module, "completion", None) is original:
            monkeypatch.setattr(module, "completion", refuse)
    return calls


def run_import(client: EvolveClient | None, path: Path, source: str = "fake", scope: str | None = None):
    return import_lessons(load_lesson_dir(path), client, namespace_id="memories", source=source, scope=scope)


def stored(client: EvolveClient) -> list[Entity]:
    return sorted(client.get_all_entities("memories", filters={"type": "guideline"}), key=lambda e: str(e.content))


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    code = cli.main(list(args))
    captured = capsys.readouterr()
    return code, captured.out.strip(), captured.err.strip()


def test_counts_and_support(client: EvolveClient, lessons: Path):
    report = run_import(client, lessons)

    assert (report.files, report.lessons, report.empty, report.imported, report.support) == (3, LESSONS, 1, LESSONS, LESSONS)
    assert report.failures == []
    assert report.summary() == (
        "import-lessons memories (fake): 3 files read, 3 lessons imported, 1 skipped as empty, "
        "0 already imported (0 files), 0 dropped by memory hooks, 0 files failed; support total 3"
    )
    rows, empty = guideline_rows(fetch_guidelines(client, "memories"))
    assert (len(rows), empty, sum(row.support for row in rows)) == (LESSONS, 0, LESSONS)
    assert [e.content for e in stored(client)] == [
        "Filter retrieved contacts by every task constraint before acting on them.",
        "Page through list APIs until an empty page is returned.",  # stripped, as the reference loader does
        "Return the answer in exactly the format the task asks for.",
    ]


def test_evidence_comes_from_the_file_outcome_not_the_lesson_polarity(client: EvolveClient, lessons: Path):
    run_import(client, lessons)

    evidence = {(e.metadata["source_task_id"], e.metadata["lesson_polarity"]): e.metadata["evidence"] for e in stored(client)}
    assert evidence == {
        ("aaa0001_1", "failure_fix"): "failure",
        ("bbb0002_1", "success_pattern"): "success",
        ("bbb0002_1", "failure_fix"): "success",  # a fix by its own account, but the task succeeded
    }


def test_metadata_matches_a_mined_guideline(client: EvolveClient, lessons: Path):
    run_import(client, lessons)

    entity = stored(client)[0]
    metadata = dict(entity.metadata)
    sources = metadata.pop("sources")
    assert [(s["task_id"], s["status"]) for s in sources] == [("aaa0001_1", "supporting")]
    assert {k: metadata[k] for k in ("provenance_incomplete", "memory_revision")} == {"provenance_incomplete": False, "memory_revision": 1}
    assert {k: v for k, v in metadata.items() if k not in ("provenance_incomplete", "memory_revision")} == {
        "source_task_id": "aaa0001_1",
        "task_description": (
            "My name is: Ada Example. My personal email is ada@example.com.\nTask: Send a message to my siblings who have no wallet account"
        ),
        "support": 1,
        "evidence": "failure",
        "category": "strategy",
        "rationale": "",
        "trigger": "",
        "implementation_steps": [],
        "generation_method": "imported",
        "partition": "dev",
        "difficulty": 2,
        "lesson_source": "fake",
        "source_file": "dev__aaa0001_1.json",
        "lesson_index": 1,
        "lesson_theme": "Filter contacts",
        "lesson_polarity": "failure_fix",
        "lesson_evidence": "The agent messaged every sibling, including those with a wallet account.",
    }
    # The index counts blank lessons (aaa0001_1's is first), so it points back at the lesson's place in its file.
    assert [e.metadata["lesson_index"] for e in stored(client)] == [1, 0, 1]


def test_import_makes_no_llm_call(client: EvolveClient, lessons: Path, llm_calls: list[tuple]):
    # Conflict resolution would be the call: it is what update_entities runs by default.
    report = run_import(client, lessons)
    run_import(client, lessons)

    assert (llm_calls, report.failures, report.imported) == ([], [], LESSONS)


def test_task_description_is_the_instruction_from_task_onwards():
    # As the AppWorld adapter reads it: the "My name is:" line just before "Task:" is kept.
    assert task_description("My name is: Ada.\nTask: Do it.") == "My name is: Ada.\nTask: Do it."
    # A 200-character preview is always kept whole, preamble included; only a longer text is cut.
    assert task_description("Using the APIs below, help the user.\nTask: Send $5 to Bo.") == (
        "Using the APIs below, help the user.\nTask: Send $5 to Bo."
    )
    assert task_description(("x" * 300) + " Task: Do it.") == ("x" * 199) + " Task: Do it."
    assert task_description("  Request $13 from Stacy.  ") == "Request $13 from Stacy."  # no "Task:", used whole


def test_rerun_imports_nothing_twice_even_after_consolidation(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    run_import(client, lessons)

    again = run_import(client, lessons)
    assert (again.imported, again.already_imported, again.already_imported_files, again.support) == (0, LESSONS, 3, 0)
    assert len(stored(client)) == LESSONS

    # The checkpoint, not the stored guidelines, records what was imported: merging them away doesn't re-import.
    mock_consolidation(monkeypatch)
    client.consolidate_guidelines("memories", threshold=0.5, mode="lossless")
    assert len(stored(client)) == 2
    assert run_import(client, lessons).imported == 0
    # The stored support is conserved; the distinct-task count drops, since bbb0002_1's two lessons merged.
    rows = guideline_rows(fetch_guidelines(client, "memories"))[0]
    assert sum(stored_support(row.entity) for row in rows) == LESSONS
    assert sum(row.support for row in rows) == 2

    # A new task is imported on its own; another scope (an explicit --source) is a separate import,
    # but a new source label alone is not.
    extra = json.loads((lessons / "train__bbb0002_1.json").read_text()) | {"task_id": "ddd0004_1"}
    (lessons / "train__ddd0004_1.json").write_text(json.dumps(extra))
    report = run_import(client, lessons)
    assert (report.imported, report.already_imported_files) == (2, 3)
    assert run_import(client, lessons, source="other").imported == 0
    assert run_import(client, lessons, source="other", scope="other").imported == LESSONS + 2


def test_renaming_a_file_or_its_directory_imports_nothing_twice(client: EvolveClient, lessons: Path):
    run_import(client, lessons, source=lessons.name)

    (lessons / "dev__aaa0001_1.json").rename(lessons / "dev__aaa0001_1.copy.json")
    renamed = run_import(client, lessons, source=lessons.name)
    assert (renamed.imported, renamed.already_imported_files) == (0, 3)
    moved = Path(shutil.move(lessons, lessons.with_name("lessons_v2")))
    report = run_import(client, moved, source=moved.name)  # the CLI's default label follows the directory
    assert (report.imported, report.already_imported_files) == (0, 3)
    assert sum(row.support for row in guideline_rows(fetch_guidelines(client, "memories"))[0]) == LESSONS


def test_a_file_changed_since_its_import_is_an_error(client: EvolveClient, lessons: Path):
    run_import(client, lessons)
    path = lessons / "train__bbb0002_1.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"success": False}))

    with pytest.raises(ValueError, match=r"1 lesson file\(s\) changed since their task was imported into 'memories' \("):
        run_import(client, lessons)
    assert len(stored(client)) == LESSONS
    # Renamed, it is still the same task, so still an error rather than a second copy.
    path.rename(lessons / "renamed.json")
    with pytest.raises(ValueError, match="renamed.json"):
        run_import(client, lessons)
    # Another scope is a separate import, so it takes the changed file, and is itself checked from then on.
    assert run_import(client, lessons, scope="mine").imported == LESSONS
    path = lessons / "renamed.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"success": True}))
    with pytest.raises(ValueError, match="imported into 'memories' as 'mine'"):
        run_import(client, lessons, scope="mine")


def test_a_failed_file_is_reported_and_retried_on_rerun(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    original = backend_base.dispatch_memory_pre_write

    def reject_aaa(backend, namespace_id, entities):  # a memory hook vetoing one file's writes
        if any(entity.metadata.get("source_task_id") == "aaa0001_1" for entity in entities):
            raise RuntimeError("write rejected by a hook")
        return original(backend, namespace_id, entities)

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", reject_aaa)
    report = run_import(client, lessons)
    assert report.failures == [("dev__aaa0001_1.json", "RuntimeError: write rejected by a hook")]
    assert (report.imported, report.support) == (2, 2)

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", original)
    retried = run_import(client, lessons)
    assert (retried.imported, retried.already_imported_files, retried.failures) == (1, 2, [])
    assert len(stored(client)) == LESSONS


def test_a_rerun_skips_imported_files_before_their_hooks_run(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    run_import(client, lessons)

    def reject(backend, namespace_id, entities):
        raise RuntimeError("hooks ran again on an imported file")

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", reject)
    again = run_import(client, lessons)
    assert (again.failures, again.already_imported_files) == ([], 3)


def test_lessons_a_hook_drops_are_counted(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    original = backend_base.dispatch_memory_pre_write

    def drop_bbb(backend, namespace_id, entities):
        return original(backend, namespace_id, [e for e in entities if e.metadata.get("source_task_id") != "bbb0002_1"])

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", drop_bbb)
    report = run_import(client, lessons)
    assert (report.imported, report.dropped, report.failures, report.support) == (1, 2, [], 1)
    assert "1 lessons imported" in report.summary() and "2 dropped by memory hooks" in report.summary()


def test_a_backend_without_atomic_writes_is_refused_before_any_write(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(type(client.backend), "supports_atomic_writes", False)

    with pytest.raises(ValueError, match="needs a backend with atomic writes"):
        run_import(client, lessons)
    assert client.all_namespaces() == []


def test_writes_pass_through_the_memory_hooks(client: EvolveClient, lessons: Path, monkeypatch: pytest.MonkeyPatch):
    original = backend_base.dispatch_memory_pre_write

    def redact(backend, namespace_id, entities):
        return original(backend, namespace_id, [e.model_copy(update={"content": f"[checked] {e.content}"}) for e in entities])

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", redact)
    run_import(client, lessons)
    assert all(str(e.content).startswith("[checked] ") for e in stored(client))


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ("{not json", "Expecting property name"),
        ("[]", "must hold a JSON object, not list"),
        ('{"partition": "dev", "success": true, "instruction_preview": "Task: x", "lessons": []}', "task_id must be a non-empty string"),
        ('{"task_id": "t", "partition": "dev", "success": "yes", "instruction_preview": "x", "lessons": []}', "success must be true"),
        ('{"task_id": "t", "partition": "dev", "success": true, "instruction_preview": " ", "lessons": []}', "instruction_preview must be"),
        ('{"task_id": "t", "partition": "dev", "success": true, "instruction_preview": "x", "lessons": {}}', "lessons must be a list"),
        ('{"task_id": "t", "partition": "dev", "success": true, "instruction_preview": "x", "lessons": ["a"]}', "lessons[0] must be an"),
        ('{"task_id": "t", "partition": "dev", "success": true, "instruction_preview": "x", "lessons": [{}]}', "lessons[0].lesson must be"),
        (
            '{"task_id": "t", "partition": "dev", "success": true, "instruction_preview": "x", "lessons": [{"lesson": "a", "theme": 3}]}',
            "lessons[0].theme must be a string",
        ),
        (
            '{"task_id": "t", "partition": "dev", "success": true, "difficulty": "2", "instruction_preview": "x", "lessons": []}',
            "difficulty",
        ),
    ],
)
def test_an_invalid_file_fails_the_whole_input_before_any_write(client: EvolveClient, lessons: Path, content: str, problem: str):
    (lessons / "zz_bad.json").write_text(content)

    with pytest.raises(ValueError, match="1 invalid lesson file") as error:
        load_lesson_dir(lessons)
    assert f"zz_bad.json: {problem}" in str(error.value)


def test_directory_problems(tmp_path: Path, lessons: Path):
    with pytest.raises(ValueError, match="must be a directory"):
        load_lesson_dir(tmp_path / "missing")
    with pytest.raises(ValueError, match="no \\*.json lesson files"):
        load_lesson_dir(tmp_path)
    shutil.copy(lessons / "dev__aaa0001_1.json", lessons / "copy.json")
    with pytest.raises(ValueError, match="dev__aaa0001_1.json: task_id 'aaa0001_1' is also in copy.json"):
        load_lesson_dir(lessons)
    (lessons / "copy.json").unlink()

    # A subdirectory is skipped, even one named *.json; an unreadable file is listed with the rest.
    (lessons / "archive.json").mkdir()
    assert [file.task_id for file in load_lesson_dir(lessons)] == ["aaa0001_1", "bbb0002_1", "ccc0003_2"]
    (lessons / "broken.json").symlink_to(lessons / "missing.json")
    (lessons / "zz_bad.json").write_text("[]")
    with pytest.raises(ValueError, match="2 invalid lesson file") as error:
        load_lesson_dir(lessons)
    assert "broken.json: [Errno 2]" in str(error.value) and "zz_bad.json: must hold a JSON object" in str(error.value)


def test_dry_run_counts_without_storage(lessons: Path):
    report = run_import(None, lessons)
    assert report.summary() == (
        "dry run (fake): 3 files read, 3 lessons found, 1 skipped as empty; support total 3 (checkpoints not checked)"
    )


def test_cli_imports_and_round_trips_through_export(
    client: EvolveClient, lessons: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(cli, "make_client", lambda: client)

    code, stdout, _ = run(capsys, "import-lessons", "--input", str(lessons), "--namespace", "memories")
    assert code == 0
    assert stdout.startswith("import-lessons memories (guidelines_per_task_fake): 3 files read, 3 lessons imported")
    assert stdout.endswith("support total 3")
    code, stdout, _ = run(capsys, "import-lessons", "--input", str(lessons), "--namespace", "memories")
    assert (code, "0 lessons imported" in stdout, "3 already imported (3 files)" in stdout) == (0, True, True)

    out = tmp_path / "playbook.json"
    code, stdout, _ = run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(out))
    assert code == 0 and "support sum=3 (stored 3) " in stdout
    entries = json.loads(out.read_text())["entries"]
    assert sum(entry["n"] for entry in entries) == LESSONS
    assert sorted(entry["e"] for entry in entries) == ["f", "s", "s"]

    # Single-source singletons resolve from the stored task_description, as mined ones do.
    index = tmp_path / "index.json"
    assert run(capsys, "export", "retrieval-index", "--namespace", "memories", "--out", str(index))[0] == 0
    singletons = json.loads(index.read_text())["singletons"]
    assert sorted(s["source_task"] for s in singletons) == ["aaa0001_1", "bbb0002_1", "bbb0002_1"]

    # Consolidation (mocked) conserves the imported (stored) support, which is what the parity check relies on;
    # the distinct-task sum drops to 2 because both merged lessons came from bbb0002_1.
    # Its own summary counts only the clusters it merged, so the namespace total comes from export.
    mock_consolidation(monkeypatch)
    code, stdout, _ = run(capsys, "consolidate", "--namespace", "memories", "--threshold", "0.5")
    assert (code, stdout.endswith("2 -> 1 guidelines, support 2 -> 2")) == (0, True)
    code, stdout, _ = run(capsys, "export", "playbook", "--namespace", "memories", "--out", str(out))
    assert code == 0 and stdout.startswith("playbook: 2 entries") and "support sum=2 (stored 3) " in stdout


def test_cli_dry_run_never_builds_a_client(lessons: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    def no_client() -> EvolveClient:
        raise AssertionError("dry run must not touch storage")

    monkeypatch.setattr(cli, "make_client", no_client)
    code, stdout, _ = run(capsys, "import-lessons", "--input", str(lessons), "--namespace", "memories", "--source", "ds", "--dry-run")
    assert (code, stdout) == (
        0,
        "dry run (ds): 3 files read, 3 lessons found, 1 skipped as empty; support total 3 (checkpoints not checked)",
    )


def test_cli_errors(
    client: EvolveClient, lessons: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(cli, "make_client", lambda: client)
    (lessons / "zz_bad.json").write_text("[]")

    code, _, stderr = run(capsys, "import-lessons", "--input", str(lessons), "--namespace", "memories")
    assert code == 2 and stderr.startswith("error: 1 invalid lesson file(s)") and "zz_bad.json: must hold a JSON object" in stderr
    assert client.all_namespaces() == []  # nothing was written, not even the namespace
    assert run(capsys, "import-lessons", "--input", str(tmp_path / "missing"), "--namespace", "memories")[0] == 2
    if os.geteuid() != 0:  # root ignores directory permissions
        unreadable = tmp_path / "unreadable"
        unreadable.mkdir(mode=0)
        try:
            code, _, stderr = run(capsys, "import-lessons", "--input", str(unreadable), "--namespace", "memories")
        finally:
            unreadable.chmod(0o755)
        assert code == 1 and "Permission denied" in stderr
    with pytest.raises(SystemExit):
        cli.main(["import-lessons", "--namespace", "memories"])  # --input is required
    capsys.readouterr()

    (lessons / "zz_bad.json").unlink()
    original = backend_base.dispatch_memory_pre_write

    def reject(backend, namespace_id, entities):
        raise RuntimeError("rejected")

    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", reject)
    code, stdout, stderr = run(capsys, "import-lessons", "--input", str(lessons), "--namespace", "memories")
    assert code == 1
    assert stderr.splitlines() == [
        "failed dev__aaa0001_1.json: RuntimeError: rejected",
        "failed train__bbb0002_1.json: RuntimeError: rejected",
    ]
    assert "0 lessons imported" in stdout and "2 files failed" in stdout
    monkeypatch.setattr(backend_base, "dispatch_memory_pre_write", original)
