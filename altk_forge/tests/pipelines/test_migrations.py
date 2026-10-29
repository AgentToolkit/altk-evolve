import json

from altk_forge.core.store import write_json
from altk_forge.pipelines import integrations
from altk_forge.pipelines.integrations import get_integration
from altk_forge.pipelines.migrations import migrate_memory_settings
from altk_forge.pipelines.state import get_pipeline


def test_migrate_memory_settings_moves_them_to_the_pipeline(pipeline):
    items = json.loads(integrations.INTEGRATIONS.read_text())
    for i in items:
        if i["kind"] == "altk_evolve":
            i["guidelines_mode"] = "all"
    write_json(integrations.INTEGRATIONS, items, private=True)
    assert migrate_memory_settings() == ["qa-bot"]
    assert get_pipeline("qa-bot")["memory"] == {"guidelines_mode": "all"}
    assert "guidelines_mode" not in get_integration(pipeline["output_id"])
