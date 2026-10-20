"""Import lessons: per-task lesson files -> guideline entities, without an LLM call.

Reads a directory of ``*.json`` lesson files, one per task::

    {"task_id": "0d8a4ee_1", "partition": "dev", "success": false, "difficulty": 2,
     "instruction_preview": "My name is: ... Task: ...",
     "lessons": [{"theme": "...", "polarity": "failure_fix", "evidence": "...", "lesson": "..."}]}

and writes each non-empty lesson as one guideline shaped like a mined one
(``source_task_id``, ``task_description``, ``support`` 1, ``evidence``,
``category``), so ``consolidate``, ``export`` and ``lineage`` treat both alike.
It matches the reference loader (``consolidate_lossless.py::_load_lessons``):
files in sorted name order, blank lessons skipped, evidence from the file's
``success`` rather than each lesson's polarity, support 1 per lesson.

Writes go through the client's backend with conflict resolution off, so memory
hooks run and no model is called. Each file is committed atomically together
with a checkpoint keyed on its ``task_id`` (and on ``scope``, when one is given),
the mechanism ``mine`` uses: a re-run skips tasks already imported, even after
``consolidate`` has merged their guidelines away or the file or its directory
was renamed, and a file whose content changed since its import is an error
rather than a second copy of its lessons.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity
from altk_evolve.schema.exceptions import NamespaceNotFoundException
from altk_evolve.schema.guidelines import DEFAULT_GUIDELINE_CATEGORY

from experiments.guideline_pipeline.adapters.appworld import task_from_prompt

# metadata.generation_method of every imported guideline (mined ones say "standard" or "consistency").
GENERATION_METHOD = "imported"
# Lessons carry no category; "strategy" is the library's own fallback for a stored guideline without one.
CATEGORY = DEFAULT_GUIDELINE_CATEGORY
# Optional per-lesson string fields, kept as metadata under these keys.
LESSON_FIELDS = {"theme": "lesson_theme", "polarity": "lesson_polarity", "evidence": "lesson_evidence"}


@dataclass(frozen=True)
class Lesson:
    index: int  # position in the file's lessons list, counting blank ones
    text: str
    extra: dict[str, str]


@dataclass(frozen=True)
class LessonFile:
    """One validated lesson file: its non-empty lessons and how many were blank."""

    name: str
    digest: str
    task_id: str
    partition: str
    success: bool
    difficulty: int | None
    instruction: str
    lessons: tuple[Lesson, ...]
    empty: int

    @property
    def evidence(self) -> str:
        return "success" if self.success else "failure"

    def entities(self, source: str) -> list[Entity]:
        base: dict[str, Any] = {
            "source_task_id": self.task_id,
            "task_description": self.instruction,
            "support": 1,
            "evidence": self.evidence,
            "category": CATEGORY,
            # The rest of a mined guideline's fields, empty: a lesson is only its text.
            "rationale": "",
            "trigger": "",
            "implementation_steps": [],
            "generation_method": GENERATION_METHOD,
            "partition": self.partition,
            **({} if self.difficulty is None else {"difficulty": self.difficulty}),
            "lesson_source": source,
            "source_file": self.name,
        }
        return [
            Entity(type="guideline", content=lesson.text, metadata={**base, "lesson_index": lesson.index, **lesson.extra})
            for lesson in self.lessons
        ]


def _string(data: dict, key: str, *, blank: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ValueError(f"{key} must be a non-empty string, not {value!r}")
    return value.strip()


def _lesson(index: int, item: Any) -> Lesson | None:
    """The lesson at index, or None when its text is blank; anything malformed is an error."""
    if not isinstance(item, dict):
        raise ValueError(f"lessons[{index}] must be an object, not {type(item).__name__}")
    text = item.get("lesson")
    if not isinstance(text, str):
        raise ValueError(f"lessons[{index}].lesson must be a string, not {text!r}")
    extra: dict[str, str] = {}
    for key, name in LESSON_FIELDS.items():
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"lessons[{index}].{key} must be a string, not {value!r}")
        if value is not None:
            extra[name] = value
    return Lesson(index, text.strip(), extra) if text.strip() else None


def task_description(preview: str) -> str:
    """The instruction as the AppWorld adapter reads one (task_from_prompt), else the preview itself.

    task_from_prompt starts at the "My name is:" line before the last "Task:", else up
    to TASK_CONTEXT (200) characters before it. A preview is the instruction's first
    200 characters, so it is kept whole, preamble included; only a longer text is cut.
    A preview without "Task:" is used whole.
    """
    return task_from_prompt(preview) or preview.strip()


def load_lesson_file(path: Path) -> LessonFile:
    """Parse and validate one lesson file; a ValueError names what is wrong (OSError if it can't be read)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"must hold a JSON object, not {type(data).__name__}")
    success = data.get("success")
    if not isinstance(success, bool):
        raise ValueError(f"success must be true or false, not {success!r}")
    difficulty = data.get("difficulty")
    if difficulty is not None and (not isinstance(difficulty, int) or isinstance(difficulty, bool)):
        raise ValueError(f"difficulty must be an integer, not {difficulty!r}")
    items = data.get("lessons")
    if not isinstance(items, list):
        raise ValueError(f"lessons must be a list, not {items!r}")
    parsed = [_lesson(index, item) for index, item in enumerate(items)]
    lessons = tuple(lesson for lesson in parsed if lesson is not None)
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    return LessonFile(
        name=path.name,
        digest=hashlib.sha256(canonical).hexdigest(),
        task_id=_string(data, "task_id"),
        partition=_string(data, "partition"),
        success=success,
        difficulty=difficulty,
        instruction=task_description(_string(data, "instruction_preview")),
        lessons=lessons,
        empty=len(items) - len(lessons),
    )


def load_lesson_dir(path: Path) -> list[LessonFile]:
    """Every ``*.json`` file directly in path, in sorted name order, all validated before any is used.

    Raises ValueError listing every invalid or unreadable file (and any task_id found
    in more than one file), so a bad input writes nothing; OSError if path itself
    can't be listed. Subdirectories are skipped, even one named ``*.json``.
    """
    if not path.is_dir():
        raise ValueError(f"--input must be a directory of lesson files: {path}")
    # iterdir rather than glob: glob reports an unreadable directory as an empty one.
    paths = sorted(child for child in path.iterdir() if child.name.endswith(".json") and not child.is_dir())
    if not paths:
        raise ValueError(f"no *.json lesson files in {path}")
    files: list[LessonFile] = []
    problems: list[str] = []
    for file_path in paths:
        try:
            files.append(load_lesson_file(file_path))
        except (ValueError, OSError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors; OSError: a broken link, say
            problems.append(f"{file_path.name}: {exc}")
    seen: dict[str, str] = {}
    for file in files:
        if file.task_id in seen:
            problems.append(f"{file.name}: task_id {file.task_id!r} is also in {seen[file.task_id]}")
        seen.setdefault(file.task_id, file.name)
    if problems:
        raise ValueError(f"{len(problems)} invalid lesson file(s) in {path}:\n  " + "\n  ".join(problems))
    return files


def checkpoint_key(scope: str | None, task_id: str) -> str:
    """The namespace checkpoint marking a task's lessons imported; distinct from mine's batch keys (a 6-item list).

    Keyed on the task, not the file or directory name, so a renamed copy is not imported
    again; scope (None by default) separates deliberate imports of the same tasks.
    """
    return hashlib.sha256(json.dumps(["import-lessons", scope, task_id], ensure_ascii=False).encode()).hexdigest()


@dataclass
class ImportReport:
    """Counts for one run. Lessons and empty cover every file read; imported only what this run wrote.

    dropped counts lessons a memory hook removed from a file that was otherwise committed.
    """

    dry_run: bool
    namespace_id: str
    source: str
    files: int = 0
    lessons: int = 0
    empty: int = 0
    imported: int = 0
    already_imported: int = 0
    already_imported_files: int = 0
    dropped: int = 0
    support: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        if self.dry_run:
            return (
                f"dry run ({self.source}): {self.files} files read, {self.lessons} lessons found, "
                f"{self.empty} skipped as empty; support total {self.support} (checkpoints not checked)"
            )
        return (
            f"import-lessons {self.namespace_id} ({self.source}): {self.files} files read, {self.imported} lessons imported, "
            f"{self.empty} skipped as empty, {self.already_imported} already imported ({self.already_imported_files} files), "
            f"{self.dropped} dropped by memory hooks, {len(self.failures)} files failed; support total {self.support}"
        )


def _changed_since_import(client: EvolveClient, namespace_id: str, scope: str | None, files: Sequence[LessonFile]) -> list[str]:
    try:
        client.get_namespace_details(namespace_id)
    except NamespaceNotFoundException:
        return []
    changed = []
    for file in files:
        recorded = client.backend.get_processing_checkpoint(namespace_id, checkpoint_key(scope, file.task_id))
        if recorded is not None and recorded.get("digest") != file.digest:
            changed.append(file.name)
    return changed


def import_lessons(
    files: Sequence[LessonFile], client: EvolveClient | None, *, namespace_id: str, source: str, scope: str | None = None
) -> ImportReport:
    """Write every file's non-empty lessons into namespace_id (created if missing); client=None is a dry run.

    source is the label recorded on each guideline. scope keys re-run detection: by
    default each task_id is imported once per namespace; another scope imports it again.
    A dry run reads no checkpoints, so it counts every lesson it read.

    Raises ValueError, before writing anything, for a backend without atomic writes
    or for a file whose content changed since its task was imported in this scope. A
    file that fails to write (a hook rejecting it, say) is reported and the run continues.
    """
    report = ImportReport(dry_run=client is None, namespace_id=namespace_id, source=source)
    report.files = len(files)
    report.lessons = sum(len(file.lessons) for file in files)
    report.empty = sum(file.empty for file in files)
    if client is None:
        report.support = report.lessons
        return report
    if not client.backend.supports_atomic_writes:
        raise ValueError(f"import-lessons needs a backend with atomic writes (filesystem or postgres), not {client.config.backend}")
    if changed := _changed_since_import(client, namespace_id, scope, files):
        where = "" if scope is None else f" as {scope!r}"
        raise ValueError(
            f"{len(changed)} lesson file(s) changed since their task was imported into {namespace_id!r}{where} "
            f"(use a new namespace or --source): {', '.join(changed)}"
        )
    client.ensure_namespace(namespace_id)
    for file in files:
        try:
            written = _import_file(client, namespace_id, source, scope, file)
        except Exception as exc:
            report.failures.append((file.name, f"{type(exc).__name__}: {exc}"))
            continue
        if written is None:
            report.already_imported += len(file.lessons)
            report.already_imported_files += 1
        else:
            report.imported += written
            report.support += written
            report.dropped += max(0, len(file.lessons) - written)
    return report


def _import_file(client: EvolveClient, namespace_id: str, source: str, scope: str | None, file: LessonFile) -> int | None:
    """Commit one file's guidelines and its checkpoint together; None if its task was already imported."""
    backend = client.backend
    key = checkpoint_key(scope, file.task_id)
    # Checked here, before the hooks run, so a re-run never sends imported lessons through them again.
    if backend.get_processing_checkpoint(namespace_id, key) is not None:
        return None
    marker = {
        "source": source,
        "scope": scope,
        "file": file.name,
        "task_id": file.task_id,
        "digest": file.digest,
        "lessons": len(file.lessons),
    }
    entities = file.entities(source)
    # prepare_updates runs the memory hooks; with conflict resolution off nothing reads the store or calls a model.
    prepared = [backend.prepare_updates(namespace_id, entities, enable_conflict_resolution=False)] if entities else []
    written = backend.commit_prepared(namespace_id, prepared, checkpoint=(key, marker))
    return None if written is None else sum(update.event == "ADD" for update in written)
