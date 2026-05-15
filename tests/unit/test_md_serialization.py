"""Tests for altk_evolve.backend._md_serialization (Phase 1)."""

from datetime import datetime, timezone

import pytest
from ulid import ULID

from altk_evolve.backend._md_serialization import (
    _isoformat_utc,
    _parse_isoformat,
    deserialize_entity,
    entity_path_for,
    is_valid_ulid,
    new_ulid,
    parse_md_file,
    serialize_entity,
)
from altk_evolve.schema.core import RecordedEntity


pytestmark = pytest.mark.unit


# Deterministic ULID for tests that need a stable value (all-zero bytes).
_ZERO_ULID = str(ULID.from_bytes(b"\x00" * 16))


def _ts() -> datetime:
    return datetime(2026, 5, 15, 14, 22, 0, tzinfo=timezone.utc)


# ── ULID helpers (thin wrappers around python-ulid) ────────────────────────


class TestNewULID:
    def test_length_is_26(self) -> None:
        assert len(new_ulid()) == 26

    def test_returns_valid_ulid_string(self) -> None:
        # Library accepts what we generate.
        ULID.from_str(new_ulid())

    def test_uniqueness(self) -> None:
        ids = {new_ulid() for _ in range(1000)}
        assert len(ids) == 1000


class TestIsValidULID:
    def test_accepts_generated(self) -> None:
        assert is_valid_ulid(new_ulid())

    def test_rejects_wrong_length(self) -> None:
        assert not is_valid_ulid("0" * 25)
        assert not is_valid_ulid("0" * 27)

    def test_rejects_lowercase(self) -> None:
        # ULIDs are uppercase Crockford base32.
        assert not is_valid_ulid("a" * 26)

    def test_rejects_garbage(self) -> None:
        assert not is_valid_ulid("definitely not a ulid")
        assert not is_valid_ulid("")


# ── ISO datetime helpers ───────────────────────────────────────────────────


class TestDatetimeHelpers:
    def test_isoformat_emits_z_suffix(self) -> None:
        s = _isoformat_utc(_ts())
        assert s == "2026-05-15T14:22:00Z"

    def test_isoformat_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _isoformat_utc(datetime(2026, 5, 15, 14, 22, 0))

    def test_round_trip_z_suffix(self) -> None:
        roundtripped = _parse_isoformat(_isoformat_utc(_ts()))
        assert roundtripped == _ts()

    def test_parse_accepts_explicit_offset(self) -> None:
        parsed = _parse_isoformat("2026-05-15T14:22:00+00:00")
        assert parsed == _ts()


# ── frontmatter serialization ──────────────────────────────────────────────


class TestSerializeEntity:
    def _entity(self, *, content: str | list | dict = "guideline body text", entity_id: str | None = None) -> RecordedEntity:
        return RecordedEntity(
            id=entity_id or _ZERO_ULID,
            type="guideline",
            content=content,
            metadata={"category": "recovery", "trigger": "auth_failed_401"},
            created_at=_ts(),
        )

    def test_emits_frontmatter_delimiters(self) -> None:
        out = serialize_entity(self._entity(), namespace_id="default")
        assert out.startswith("---\n")
        assert "\n---\n" in out

    def test_string_content_lands_in_body(self) -> None:
        out = serialize_entity(self._entity(content="my body"), namespace_id="default")
        body_start = out.index("---\n", 4) + len("---\n")
        body = out[body_start:].strip()
        assert body == "my body"

    def test_list_content_goes_into_frontmatter(self) -> None:
        out = serialize_entity(self._entity(content=["a", "b"]), namespace_id="default")
        # body should be empty
        body_start = out.index("---\n", 4) + len("---\n")
        body = out[body_start:].strip()
        assert body == ""
        # content should be in YAML
        assert "content:" in out
        assert "- a" in out

    def test_includes_required_fields(self) -> None:
        out = serialize_entity(self._entity(), namespace_id="default")
        for field in ["schema:", "stable_id:", "type:", "namespace:", "authority:", "created_at:"]:
            assert field in out, f"missing field marker {field}"

    def test_extra_frontmatter_is_merged(self) -> None:
        out = serialize_entity(
            self._entity(),
            namespace_id="default",
            extra_frontmatter={"index_generation": 42, "trigger": "auth_failed_401"},
        )
        assert "index_generation: 42" in out
        assert "trigger: auth_failed_401" in out

    def test_authority_default_is_generated(self) -> None:
        out = serialize_entity(self._entity(), namespace_id="default")
        assert "authority: generated" in out

    def test_authority_can_be_overridden(self) -> None:
        out = serialize_entity(
            self._entity(),
            namespace_id="default",
            authority="authoritative",
        )
        assert "authority: authoritative" in out


class TestParseMdFile:
    def test_parses_valid_blob(self) -> None:
        # Use a real-shape ULID (letters present) so YAML doesn't coerce to int.
        blob = "---\nstable_id: 01HXY3K2N5QPVWZ8ABCDEFGHJK\ntype: guideline\n---\nbody"  # pragma: allowlist secret
        fm, body = parse_md_file(blob)
        assert fm["stable_id"] == "01HXY3K2N5QPVWZ8ABCDEFGHJK"  # pragma: allowlist secret
        assert body == "body"

    def test_missing_opening_delim_errors(self) -> None:
        with pytest.raises(ValueError, match="opening frontmatter"):
            parse_md_file("no delimiters here")

    def test_missing_closing_delim_errors(self) -> None:
        with pytest.raises(ValueError, match="closing frontmatter"):
            parse_md_file("---\nstable_id: a\n# no closing delim\n")

    def test_non_mapping_frontmatter_errors(self) -> None:
        with pytest.raises(ValueError, match="must be a mapping"):
            parse_md_file("---\n- just a list\n---\nbody")


class TestRoundTrip:
    def _entity(self) -> RecordedEntity:
        return RecordedEntity(
            id=_ZERO_ULID,
            type="guideline",
            content="re-authenticate after token refresh failure",
            metadata={"category": "recovery", "trigger": "auth_failed_401"},
            created_at=_ts(),
        )

    def test_string_content_round_trip(self) -> None:
        original = self._entity()
        text = serialize_entity(original, namespace_id="default")
        parsed, _ = deserialize_entity(text)
        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.content == original.content
        assert parsed.created_at == original.created_at
        assert parsed.metadata == original.metadata

    def test_dict_content_round_trip(self) -> None:
        e = RecordedEntity(
            id=_ZERO_ULID,
            type="fact",
            content={"endpoint": "/v1/refunds", "params": ["idempotency_key"]},
            metadata={"domain": "payments_api"},
            created_at=_ts(),
        )
        text = serialize_entity(e, namespace_id="default", schema_version="fact/v1")
        parsed, fm = deserialize_entity(text)
        assert parsed.content == e.content
        assert fm["schema"] == "fact/v1"


class TestEntityPathFor:
    def test_minimal(self) -> None:
        path = entity_path_for(
            data_dir="evolve_memory",
            namespace_id="default",
            entity_type="guideline",
            stable_id="ABC",
        )
        assert path == "evolve_memory/guidelines/default/ABC.md"

    def test_pluralizes_singular_type(self) -> None:
        path = entity_path_for(
            data_dir="root",
            namespace_id="ns",
            entity_type="fact",
            stable_id="X",
        )
        assert path == "root/facts/ns/X.md"

    def test_keeps_already_plural(self) -> None:
        path = entity_path_for(
            data_dir="root",
            namespace_id="ns",
            entity_type="guidelines",
            stable_id="X",
        )
        assert path == "root/guidelines/ns/X.md"

    def test_with_phase4_authority_and_category(self) -> None:
        path = entity_path_for(
            data_dir="root",
            namespace_id="ns",
            entity_type="guideline",
            stable_id="X",
            authority="authoritative",
            category="recovery",
        )
        assert path == "root/guidelines/ns/authoritative/recovery/X.md"

    def test_with_canonical_facts(self) -> None:
        path = entity_path_for(
            data_dir="root",
            namespace_id="ns",
            entity_type="fact",
            stable_id="X",
            authority="canonical",
            domain="payments_api",
        )
        assert path == "root/facts/ns/canonical/payments_api/X.md"
