"""Durable reports must remain safe after a later deletion."""

import pytest

from altk_evolve.retention.reports import audit_payload

pytestmark = pytest.mark.unit


def test_every_audit_outcome_omits_memory_titles_and_content():
    report = {
        bucket: [{"entity_id": "1", "content": "secret", "title": "secret", "metadata": {"title": "secret"}}]
        for bucket in ("flagged", "deleted", "skipped")
    }
    audit = audit_payload(report)
    assert "secret" not in str(audit)
    assert all(audit[bucket] == [{"entity_id": "1"}] for bucket in report)
