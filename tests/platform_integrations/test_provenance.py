"""Tests for skills/evolve-lite/provenance/scripts/provenance.py.

These exercise the rendered Claude provenance.py end to end (lib resolution only
works in the rendered tree). They cover the deterministic plumbing — recall-row
reading, entity resolution, the trajectory locator (BOTH legacy
``.evolve/trajectories/`` and the native ``~/.claude/projects/<slug>/`` paths),
dedup against existing influence rows, and the ``record`` writer. The semantic
verdict is agent-driven and is NOT tested here (there is no heuristic to test).
"""

import functools
import importlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from _hermes_loader import HERMES_PLUGIN_ROOT, HOST_STUBS, load_module

pytestmark = [pytest.mark.platform_integrations]

_REPO_ROOT = Path(__file__).parent.parent.parent
_PLUGIN_ROOT = _REPO_ROOT / "platform-integrations/claude/plugins/evolve-lite"
PROVENANCE_SCRIPT = _PLUGIN_ROOT / "skills/evolve-lite/provenance/scripts/provenance.py"
ENTITY_IO_SCRIPT = _PLUGIN_ROOT / "lib/evolve-lite/entity_io.py"


def _claude_slug(root: Path) -> str:
    """Mirror provenance.py / doctor.py slugging: non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(root))


@functools.lru_cache(maxsize=None)
def _hermes_backend():
    """The rendered Hermes provider's ``backend`` module — the writer of these files."""
    package = load_module("_hermes_provider_for_provenance", HERMES_PLUGIN_ROOT / "__init__.py", extra_syspath=[HOST_STUBS])
    return importlib.import_module(f"{package.__name__}.backend")


@functools.lru_cache(maxsize=None)
def _provenance_module():
    """The rendered provenance script, imported for its helpers.

    The tests below still drive it as a subprocess — that is what a host runs.
    This is only for reaching a single function directly, which a subprocess
    cannot do.
    """
    return load_module("_provenance_under_test", PROVENANCE_SCRIPT)


def _hermes_traj_name(session_id):
    """Capture filename for ``session_id``, from the provider that writes it.

    Deliberately not a third hand copy of the formula, unlike ``_claude_slug``:
    a copy here would make these tests agree with themselves while the shipped
    reader drifted away from the shipped writer, which is the one failure
    ``TestHermesFilenameParity`` exists to catch.
    """
    return _hermes_backend().trajectory_filename(session_id)


def write_hermes_capture(evolve_dir, filename, records):
    """Write a Hermes-shaped capture file: one JSON record per line."""
    path = Path(evolve_dir) / "trajectories" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def hermes_record(session_id, text):
    return {"ts": 1, "session_id": session_id, "messages": [{"role": "user", "content": text}]}


def run_provenance(mode, *, evolve_dir, home=None, cwd=None, stdin=None):
    env = {**os.environ}
    env["EVOLVE_DIR"] = str(evolve_dir)
    if home is not None:
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
    return subprocess.run(
        [sys.executable, str(PROVENANCE_SCRIPT), mode],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
    )


def parse_jsonl(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_audit(evolve_dir):
    path = Path(evolve_dir) / "audit.log"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_audit(evolve_dir, rows):
    path = Path(evolve_dir) / "audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def write_entity(evolve_dir, entity_id, body="Do the foo thing."):
    path = Path(evolve_dir) / "entities" / f"{entity_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {entity_id.split('/')[0]}\ntrigger: when foo\n---\n\n{body}\n", encoding="utf-8")
    return path


class TestCandidatesLegacyTrajectory:
    def test_resolves_entity_and_legacy_trajectory(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-1", "entities": ["feedback/foo"]}])
        write_entity(evolve_dir, "feedback/foo")
        traj = evolve_dir / "trajectories" / "claude-transcript_sid-1.jsonl"
        traj.parent.mkdir(parents=True)
        traj.write_text('{"type":"user","content":"hi"}\n', encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["session_id"] == "sid-1"
        assert cand["entity_id"] == "feedback/foo"
        assert "Do the foo thing." in cand["entity_excerpt"]
        assert cand["trajectory_path"] == str(traj)
        assert "hi" in cand["trajectory_excerpt"]
        assert "missing" not in cand


class TestCandidatesHermesTrajectory:
    """The Hermes memory provider writes ``trajectories/<sanitized-sid>-<digest>.jsonl``.

    None of the other resolution steps match that shape, so before it was added
    every Hermes recall row resolved to ``missing: ["trajectory"]`` — provenance
    ran, reported nothing, and looked like it had simply found no influence.
    """

    def test_locates_a_hermes_trajectory(self, tmp_path):
        session_id = "20260912_193549_752cc6c7"
        home = tmp_path / "home"
        home.mkdir()
        evolve_dir = tmp_path / "hermes" / "evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": session_id, "entities": ["guideline/foo"]}])
        write_entity(evolve_dir, "guideline/foo")
        traj = write_hermes_capture(evolve_dir, _hermes_traj_name(session_id), [hermes_record(session_id, "hi")])

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["trajectory_path"] == str(traj)
        assert "missing" not in candidates[0]


class TestCandidatesHermesSessionCollisions:
    """Sanitizing a session id into a filename is many-to-one.

    ``a/b`` and ``a:b`` both fold to ``a_b``, so naming a capture file after the
    fold let two sessions share one — and provenance then credited one session
    with the other's trajectory. The provider suffixes a digest of the original
    id; these pin that the locator computes the same name and so reads one
    session's file and no other's.
    """

    @staticmethod
    def _seed(tmp_path, session_ids):
        home = tmp_path / "home"
        home.mkdir()
        evolve_dir = tmp_path / "hermes" / "evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(
            evolve_dir,
            [{"event": "recall", "session_id": sid, "entities": ["guideline/foo"]} for sid in session_ids],
        )
        write_entity(evolve_dir, "guideline/foo")
        return home, evolve_dir

    def test_locates_a_digest_suffixed_trajectory(self, tmp_path):
        home, evolve_dir = self._seed(tmp_path, ["discord/42"])
        traj = write_hermes_capture(evolve_dir, _hermes_traj_name("discord/42"), [hermes_record("discord/42", "hi")])

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert candidates[0]["trajectory_path"] == str(traj)
        assert "missing" not in candidates[0]

    def test_ids_that_sanitize_alike_resolve_to_their_own_file(self, tmp_path):
        home, evolve_dir = self._seed(tmp_path, ["a/b", "a:b"])
        slash = write_hermes_capture(evolve_dir, _hermes_traj_name("a/b"), [hermes_record("a/b", "slash work")])
        colon = write_hermes_capture(evolve_dir, _hermes_traj_name("a:b"), [hermes_record("a:b", "colon work")])

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        by_session = {c["session_id"]: c for c in parse_jsonl(result.stdout)}
        assert by_session["a/b"]["trajectory_path"] == str(slash)
        assert by_session["a:b"]["trajectory_path"] == str(colon)
        assert "colon work" not in by_session["a/b"]["trajectory_excerpt"]
        assert "slash work" not in by_session["a:b"]["trajectory_excerpt"]

    def test_the_plain_folded_name_is_not_read(self, tmp_path):
        """The fold is not a filename the provider writes, so it is not ownership
        either: a file sitting at ``a_b.jsonl`` could be ``a/b``'s or ``a:b``'s.
        Reporting the trajectory missing is the honest answer."""
        home, evolve_dir = self._seed(tmp_path, ["a/b"])
        write_hermes_capture(evolve_dir, "a_b.jsonl", [hermes_record("a:b", "not yours")])

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert candidates[0]["trajectory_path"] is None
        assert candidates[0]["missing"] == ["trajectory"]

    def test_one_unencodable_id_does_not_take_the_healthy_sessions_with_it(self, tmp_path):
        """Computing the capture name must not be able to abort the run.

        A lone surrogate survives ``json.dumps`` into audit.log, and the reader
        reads the id back as an ordinary string. Taking its SHA-256 then raises
        ``UnicodeEncodeError``, which would propagate out of ``build_candidates``
        and lose every *other* session's candidate along with it. The provider
        could not name a file for that id either, so the honest result is one
        missing trajectory and the rest of the run intact.
        """
        home, evolve_dir = self._seed(tmp_path, ["s\ud800x", "healthy/1"])
        traj = write_hermes_capture(evolve_dir, _hermes_traj_name("healthy/1"), [hermes_record("healthy/1", "hi")])

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        by_session = {c["session_id"]: c for c in parse_jsonl(result.stdout)}
        assert by_session["healthy/1"]["trajectory_path"] == str(traj)
        assert by_session["s\ud800x"]["trajectory_path"] is None
        assert by_session["s\ud800x"]["missing"] == ["trajectory"]


class TestHermesFilenameParity:
    """The writer's filename formula and the shipped reader's, pinned together.

    A capture filename is produced by the Hermes bundle's ``backend.py`` and
    recomputed by this skill's ``provenance.py``. They cannot share code: the
    rendered skill script ships to hosts that have no provider to import, so the
    formula is maintained by hand in both, each with a comment telling the next
    person to keep them in sync.

    Nothing else in the suite compares them. The reader tests build their
    fixtures from the reader's own formula, and ``build_plugins.py check``
    compares the rendered copies to each other rather than the writer to the
    reader — so changing ``_TRAJECTORY_STEM_CHARS`` alone used to ship green
    while the shipped provenance script quietly stopped locating any capture for
    a realistic id. These are the tests that go red instead.
    """

    # Realistic ids, plus every shape that tempted one side into a shortcut: the
    # falsy ones (where the fallback lives), the long and shared-prefix ones (what
    # a stem-length change breaks), and a traversal attempt.
    @pytest.mark.parametrize(
        "session_id",
        [
            "",
            None,
            "session",
            "a/b",
            "a:b",
            "discord/42",
            "20260912_193549_752cc6c7",
            "x" * 300,
            "x" * 40 + "a",
            "x" * 40 + "b",
            "séance/✓",
            "../../etc/passwd",
        ],
        ids=repr,
    )
    def test_the_reader_computes_the_name_the_writer_wrote(self, session_id):
        assert _provenance_module()._hermes_trajectory_name(session_id) == _hermes_traj_name(session_id)

    def test_both_sides_decline_an_id_with_no_utf8_encoding(self):
        """Agreeing that there is no name is parity too.

        The writer raises and its caller fails soft, so nothing reaches disk; the
        reader returns ``None`` and falls through to the next locator. What must
        not happen is one side naming a file the other never wrote — or the reader
        raising where the writer shrugged (see
        ``test_one_unencodable_id_does_not_take_the_healthy_sessions_with_it``).
        """
        with pytest.raises(UnicodeEncodeError):
            _hermes_traj_name("s\ud800x")
        assert _provenance_module()._hermes_trajectory_name("s\ud800x") is None

    def test_the_two_stem_limits_are_the_same_number(self):
        """Restated as a constant on each side, so compare the constants directly.

        The parametrized cases above catch a drift only for ids longer than the
        smaller of the two limits. This catches it outright.
        """
        assert _provenance_module()._HERMES_STEM_CHARS == _hermes_backend()._TRAJECTORY_STEM_CHARS


class TestCandidatesNativeTranscript:
    def test_locates_native_claude_transcript(self, tmp_path):
        # Sandbox a fake HOME and project root; the native locator builds
        # ~/.claude/projects/<slug>/<sid>.jsonl from the RESOLVED project root.
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "nat-1", "entities": ["feedback/bar"]}])
        write_entity(evolve_dir, "feedback/bar", body="bar guidance")

        project_root = evolve_dir.resolve().parent
        slug = _claude_slug(project_root)
        native = home / ".claude" / "projects" / slug / "nat-1.jsonl"
        native.parent.mkdir(parents=True)
        native.write_text('{"x":1}\n', encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        cand = candidates[0]
        assert cand["entity_id"] == "feedback/bar"
        assert cand["trajectory_path"] == str(native)
        assert "missing" not in cand


class TestCandidatesCodexTranscript:
    """Codex writes ~/.codex/sessions/<Y>/<M>/<D>/rollout-<ts>-<sid>.jsonl; the
    locator finds it by a recursive glob on the thread id."""

    def test_locates_native_codex_transcript(self, tmp_path):
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        sid = "019eb34f-f827-7311-b775-b749ae4fae72"
        write_audit(evolve_dir, [{"event": "recall", "session_id": sid, "entities": ["project/baz"]}])
        write_entity(evolve_dir, "project/baz", body="baz guidance")
        rollout = home / ".codex" / "sessions" / "2026" / "06" / "10" / f"rollout-2026-06-10T12-00-{sid}.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text('{"x":1}\n', encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["entity_id"] == "project/baz"
        assert candidates[0]["trajectory_path"] == str(rollout)
        assert "missing" not in candidates[0]


class TestCandidatesBobTranscript:
    """Bob writes ~/.bob/tmp/<projecthash>/chats/session-<ts>-<sid8>.json with a
    real ``sessionId`` field; the locator matches the chat file by that id."""

    def _seed_bob_chat(self, home, *, sid, body_sid, project_hash="abc123hash", filename=None):
        fname = filename or f"session-2026-06-10T21-12-{sid.split('-')[0]}.json"
        chat = home / ".bob" / "tmp" / project_hash / "chats" / fname
        chat.parent.mkdir(parents=True)
        chat.write_text(json.dumps({"sessionId": body_sid, "messages": []}), encoding="utf-8")
        return chat

    def test_locates_native_bob_transcript(self, tmp_path):
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        sid = "d6484b2c-24f4-474c-8f43-36544e2dbcd8"
        write_audit(evolve_dir, [{"event": "recall", "session_id": sid, "entities": ["project/baz"]}])
        write_entity(evolve_dir, "project/baz", body="baz guidance")
        chat = self._seed_bob_chat(home, sid=sid, body_sid=sid)

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["entity_id"] == "project/baz"
        assert candidates[0]["trajectory_path"] == str(chat)
        assert "missing" not in candidates[0]

    def test_bob_sessionid_body_mismatch_not_matched(self, tmp_path):
        """A chat whose filename prefix collides but whose ``sessionId`` differs
        is NOT returned — the body field is authoritative (no false positive)."""
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        sid = "d6484b2c-24f4-474c-8f43-36544e2dbcd8"
        write_audit(evolve_dir, [{"event": "recall", "session_id": sid, "entities": ["project/baz"]}])
        write_entity(evolve_dir, "project/baz")
        # Same filename prefix d6484b2c, different sessionId in the body.
        self._seed_bob_chat(home, sid=sid, body_sid="ffffffff-0000-0000-0000-000000000000")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        candidates = parse_jsonl(result.stdout)
        assert candidates[0]["trajectory_path"] is None
        assert candidates[0]["missing"] == ["trajectory"]

    def test_bob_non_dict_chat_does_not_crash(self, tmp_path):
        """A chat whose filename prefix matches the sid_head prefilter but whose
        JSON is valid-but-NON-dict ([], null, a scalar) must not crash the run:
        ``.get`` is only called on a dict. It simply falls through (no match),
        so the trajectory is reported missing rather than raising."""
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        sid = "d6484b2c-24f4-474c-8f43-36544e2dbcd8"
        write_audit(evolve_dir, [{"event": "recall", "session_id": sid, "entities": ["project/baz"]}])
        write_entity(evolve_dir, "project/baz")
        # Filename prefix d6484b2c matches the sid_head prefilter, but the body
        # is a JSON array (non-dict) — previously crashed on .get("sessionId").
        chat = home / ".bob" / "tmp" / "abc123hash" / "chats" / "session-2026-06-10T21-12-d6484b2c.json"
        chat.parent.mkdir(parents=True)
        chat.write_text("[]", encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["trajectory_path"] is None
        assert candidates[0]["missing"] == ["trajectory"]


class TestCandidatesMissing:
    def test_missing_trajectory_still_emitted(self, tmp_path):
        # Empty HOME -> no native transcript, no legacy dir -> trajectory missing.
        home = tmp_path / "home"
        home.mkdir()
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-x", "entities": ["feedback/foo"]}])
        write_entity(evolve_dir, "feedback/foo")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["trajectory_path"] is None
        assert candidates[0]["missing"] == ["trajectory"]

    def test_missing_entity_still_emitted(self, tmp_path):
        home = tmp_path / "home"
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(evolve_dir, [{"event": "recall", "session_id": "sid-y", "entities": ["feedback/ghost"]}])
        traj = evolve_dir / "trajectories" / "claude-transcript_sid-y.jsonl"
        traj.parent.mkdir(parents=True)
        traj.write_text("{}\n", encoding="utf-8")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=home)
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        assert len(candidates) == 1
        assert candidates[0]["entity_excerpt"] is None
        assert candidates[0]["missing"] == ["entity"]


class TestCandidatesDedup:
    def test_skips_pairs_with_existing_influence_row(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        write_audit(
            evolve_dir,
            [
                {"event": "recall", "session_id": "sid-1", "entities": ["feedback/foo", "feedback/bar"]},
                {"event": "influence", "session_id": "sid-1", "entity": "feedback/foo", "verdict": "followed", "evidence": "x"},
            ],
        )
        write_entity(evolve_dir, "feedback/foo")
        write_entity(evolve_dir, "feedback/bar")

        result = run_provenance("candidates", evolve_dir=evolve_dir, home=tmp_path / "home")
        assert result.returncode == 0, result.stderr
        candidates = parse_jsonl(result.stdout)
        ids = {c["entity_id"] for c in candidates}
        # feedback/foo already assessed -> only feedback/bar remains.
        assert ids == {"feedback/bar"}


class TestRecord:
    def test_writes_valid_influence_row(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {
            "session_id": "sid-1",
            "entity": "feedback/foo",
            "verdict": "followed",
            "evidence": "Agent used the saved parser first.",
        }
        result = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        assert result.returncode == 0, result.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        row = events[0]
        assert row["event"] == "influence"
        assert row["session_id"] == "sid-1"
        assert row["entity"] == "feedback/foo"
        assert row["verdict"] == "followed"
        assert row["evidence"] == "Agent used the saved parser first."
        assert "ts" in row

    def test_rejects_invalid_verdict(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {"session_id": "sid-1", "entity": "feedback/foo", "verdict": "bogus", "evidence": "no"}
        result = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        assert result.returncode == 1
        assert "verdict" in result.stderr.lower()
        assert read_audit(evolve_dir) == []

    def test_record_dedups_existing_pair(self, tmp_path):
        evolve_dir = tmp_path / "proj" / ".evolve"
        evolve_dir.mkdir(parents=True)
        payload = {"session_id": "sid-1", "entity": "feedback/foo", "verdict": "followed", "evidence": "e"}
        first = run_provenance("record", evolve_dir=evolve_dir, stdin=json.dumps(payload))
        second = run_provenance(
            "record",
            evolve_dir=evolve_dir,
            stdin=json.dumps({**payload, "verdict": "contradicted", "evidence": "e2"}),
        )
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        events = read_audit(evolve_dir)
        assert len(events) == 1
        assert events[0]["verdict"] == "followed"


def _load_module(name, path, extra_syspath=None):
    """Load a module from an explicit file path via importlib.

    ``extra_syspath`` entries are prepended to ``sys.path`` for the duration of
    the import so a module whose top-level imports rely on a sibling lib dir
    (provenance.py does ``from entity_io import ...``) can resolve them.
    """
    import importlib.util

    added = list(extra_syspath or [])
    for entry in added:
        sys.path.insert(0, str(entry))
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for entry in added:
            try:
                sys.path.remove(str(entry))
            except ValueError:
                pass


class TestSlugAgreement:
    """Pin provenance._claude_transcript_slug to entity_io.claude_project_slug.

    The two implementations are hand-kept-in-sync (provenance.py's docstring
    admits "if you change one, change both"). This converts that footgun into a
    CI invariant. Both are loaded by file path via importlib so the rendered
    scripts — which do not import one another — can be compared directly.

    ``claude_project_slug`` resolves its argument to an absolute path before
    slugging, while ``_claude_transcript_slug`` slugs the (already-absolute)
    project root it is handed. To compare apples to apples we pass absolute
    paths and resolve them the same way before handing them to the provenance
    slug.
    """

    def test_slug_implementations_agree(self):
        lib_dir = PROVENANCE_SCRIPT.parent
        provenance = _load_module("_prov_slug", PROVENANCE_SCRIPT, extra_syspath=[ENTITY_IO_SCRIPT.parent, lib_dir])
        entity_io = _load_module("_entity_io_slug", ENTITY_IO_SCRIPT)

        samples = [
            "/Users/x/Documents/kaizen",
            "/tmp/evolve-smoke-test2",
            "/Users/x/My Documents/with spaces",
            "/Users/x/Documents/kaizen/",
            "/a/b/c.d/e_f",
        ]
        for raw in samples:
            resolved = Path(raw).resolve()
            assert provenance._claude_transcript_slug(resolved) == entity_io.claude_project_slug(raw), raw
