import json

import pytest

from altk_forge.builtin.files import build
from altk_forge.converters import writer
from altk_forge.core.errors import InputError
from altk_forge.core.paths import pipe_dir
from altk_forge.pipelines.manage import purge
from altk_forge.pipelines.originals import delete_originals, original, originals_dir, save_original
from altk_forge.pipelines.state import state_of
from altk_forge.pipelines.worker import WORKER
from altk_forge.sources.filesystem import Filesystem
from forge_helpers import answer_with


def test_a_file_from_a_folder_is_kept_byte_for_byte(pipeline, chat, traces):
    # Key order, spacing and number formatting are the file's own: a snapshot is not a re-serialised copy.
    exact = '{ "question":"Q1?",\n   "answer" : "A1", "n": 1.50 }\n'
    (traces / "run1.json").write_text(exact)
    WORKER.run_once("qa-bot")
    tid = Filesystem.trace_id("run1.json")
    data, name = original("qa-bot", tid)
    assert data == exact.encode() and name == "run1.json"
    source = json.loads((originals_dir("qa-bot", tid) / "source.json").read_text())
    assert source["path"] == "run1.json" and source["size"] == len(exact.encode())
    assert source["uri"].startswith("file://") and source["modified_at"]


def test_every_imported_trace_has_its_snapshot_next_to_its_trajectory(pipeline, chat):
    WORKER.run_once("qa-bot")
    root = pipe_dir("qa-bot")
    for tid in state_of("qa-bot")["traces"]:
        assert (root / "trajectories" / f"{tid}.trajectory.json").exists()
        assert original("qa-bot", tid) is not None


def test_a_trace_that_fails_to_convert_keeps_its_snapshot(pipeline, monkeypatch):
    monkeypatch.setattr(writer, "chat", lambda messages: answer_with("def convert(t, c): return {}"))
    run = WORKER.run_once("qa-bot")
    assert run["failed"] == 2
    assert all(original("qa-bot", tid) for tid in state_of("qa-bot")["traces"])


def test_a_langfuse_trace_is_kept_as_the_api_returned_it(data_dir, langfuse_trace, tmp_path):
    class LangfuseLike:  # no original(): the API response is the original
        pass

    save_original("p1", "trace-1", langfuse_trace, LangfuseLike())
    data, name = original("p1", "trace-1")
    assert name == "trace.langfuse.json"
    assert json.loads(data) == langfuse_trace
    # The snapshot is what the offline tools read: it converts like the trace itself.
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "trace-1.langfuse.json").write_bytes(data)
    built, _, failed = build(folder, tmp_path / "out", log=lambda _: None)
    assert (built, failed) == (1, [])


def test_downloading_a_trace_again_replaces_its_snapshot(data_dir):
    class Folder:
        def __init__(self, data, name):
            self.data, self.name = data, name

        def original(self, trace_id):
            return self.data, self.name

    save_original("p1", "t", {"path": "old.json"}, Folder(b"{}", "old.json"))
    save_original("p1", "t", {"path": "new.json"}, Folder(b"[1]", "new.json"))
    assert sorted(f.name for f in originals_dir("p1", "t").iterdir()) == ["new.json", "source.json"]
    assert original("p1", "t") == (b"[1]", "new.json")


def test_a_file_named_like_the_description_keeps_both(data_dir):
    class Folder:
        def original(self, trace_id):
            return b'{"a": 1}', "source.json"

    save_original("p1", "t", {"path": "source.json"}, Folder())
    assert original("p1", "t") == (b'{"a": 1}', "file.source.json")
    assert json.loads((originals_dir("p1", "t") / "source.json").read_text())["path"] == "source.json"


def test_trace_ids_with_dots_keep_their_snapshots_apart(data_dir, langfuse_trace):
    class LangfuseLike:
        pass

    save_original("p1", "a", {"id": "a"}, LangfuseLike())
    save_original("p1", "a.b", {"id": "a.b"}, LangfuseLike())
    assert json.loads(original("p1", "a")[0]) == {"id": "a"}
    assert delete_originals("p1", ["a"]) == 1
    assert original("p1", "a") is None
    assert json.loads(original("p1", "a.b")[0]) == {"id": "a.b"}


@pytest.mark.parametrize("trace_id", [".", "..", "../x", "a/b", ""])
def test_a_trace_id_cannot_name_another_folder(data_dir, trace_id):
    with pytest.raises(InputError, match="Invalid id"):
        originals_dir("p1", trace_id)


def test_purging_trajectories_deletes_their_snapshots(pipeline, chat):
    WORKER.run_once("qa-bot")
    first, second = sorted(state_of("qa-bot")["traces"])
    assert purge("qa-bot", memory=False, trajectories=True, trace_ids=[first])["traces"] == 1
    assert original("qa-bot", first) is None and original("qa-bot", second) is not None
    assert purge("qa-bot", memory=False, trajectories=True)["traces"] == 1
    assert not any(originals_dir("qa-bot").iterdir())
    # Forgotten: the next poll downloads the traces again, and keeps them again.
    WORKER.run_once("qa-bot")
    assert original("qa-bot", first) is not None


def test_a_pipeline_without_snapshots_has_none(data_dir):
    assert original("p1", "t") is None
    assert delete_originals("p1") == 0
