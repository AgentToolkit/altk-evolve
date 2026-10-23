"""Integrations: connections. direction=input: a trace source (Langfuse, a folder of JSON files, Phoenix);
direction=output: a memory (altk-evolve) that trajectories are sent to. Stored in .data/integrations.json (0600)."""

import os
from pathlib import Path

from ..core.clock import now
from ..core.errors import InputError
from ..core.ids import unique_id
from ..core.paths import DATA, load_env
from ..core.store import LOCK, read_json, write_json
from ..memory.evolve import Evolve
from ..sources.filesystem import Filesystem, exclude_patterns
from ..sources.http import SourceError
from ..sources.langfuse import Langfuse
from ..sources.phoenix import Phoenix
from .kinds import KINDS, MEMORY_FIELDS
from .state import all_pipelines, pipeline_integrations

INTEGRATIONS = DATA / "integrations.json"


def all_integrations():
    return read_json(INTEGRATIONS, [])


def get_integration(integration_id):
    return next((i for i in all_integrations() if i["id"] == integration_id), None)


def public_integration(i):
    """The integration without its secrets; a secret shows only its last 4 characters."""
    k = KINDS[i["kind"]]
    out = {k2: v for k2, v in i.items() if k2 not in k["secret"]}
    for f in k["secret"]:
        out[f"{f}_hint"] = f"…{i[f][-4:]}" if i.get(f) else None
    out["direction"] = k["direction"]
    out["used_by"] = [p["id"] for p in all_pipelines() if i["id"] in pipeline_integrations(p)]
    return out


def list_integrations():
    return [public_integration(i) for i in all_integrations()]


def save_integration(data, integration_id=None):
    kind = data.get("kind")
    if kind not in KINDS:
        raise InputError(f"Unknown kind {kind!r}; one of {', '.join(KINDS)}")
    k = KINDS[kind]
    with LOCK:
        items = all_integrations()
        current = next((i for i in items if i["id"] == integration_id), None) if integration_id else None
        if integration_id and not current:
            raise InputError(f"No integration {integration_id}")
        if current and current["kind"] != kind:
            raise InputError("The type of an integration cannot change")
        item = dict(current or {"id": unique_id(data.get("name") or kind, {i["id"] for i in items}), "created_at": now()})
        item.update(kind=kind, name=(data.get("name") or "").strip() or k["label"])
        for f in k["fields"]:
            value = data.get(f)
            if f in k.get("bool", []):
                item[f] = value in (True, "true", "on", "1", 1)
                continue
            value = value.strip() if isinstance(value, str) else "" if value is None else str(value)
            if f in k["secret"] and not value and current:
                continue  # an empty secret on edit keeps the stored one
            item[f] = value
        missing = [f for f in k["fields"] if f not in k.get("optional", []) and not item.get(f)]
        if missing:
            raise InputError(f"Missing {', '.join(missing)}")
        if kind == "filesystem":
            folder = Path(os.path.expanduser(item["path"]))
            if not folder.is_absolute():
                raise InputError("The folder must be an absolute path (or start with ~)")
            if not folder.is_dir():
                raise InputError(f"No folder {folder} on this machine")
            item["path"] = str(folder.resolve())
            item["exclude"] = ", ".join(exclude_patterns(item.get("exclude")))
        item["updated_at"] = now()
        items = [item if i["id"] == item["id"] else i for i in items] if current else items + [item]
        write_json(INTEGRATIONS, items, private=True)
    return public_integration(item)


def delete_integration(integration_id):
    with LOCK:
        items = all_integrations()
        if not any(i["id"] == integration_id for i in items):
            raise InputError(f"No integration {integration_id}")
        users = [p["id"] for p in all_pipelines() if integration_id in pipeline_integrations(p)]
        if users:
            raise InputError(f"Used by pipeline {', '.join(users)}; change or delete those first")
        write_json(INTEGRATIONS, [i for i in items if i["id"] != integration_id], private=True)


def browse_folder(path=None):
    """A folder on this machine for the folder picker: its subfolders (hidden ones too) and how many JSON files it
    holds directly. No path: the home folder."""
    folder = Path(os.path.expanduser(path or "~"))
    if not folder.is_absolute():
        raise InputError("Give an absolute path")
    folder = folder.resolve()
    if not folder.is_dir():
        raise InputError(f"No folder {folder}")
    folders, files = [], 0
    try:
        for e in os.scandir(folder):
            try:
                if e.is_dir():
                    folders.append(e.name)
                elif e.is_file() and e.name.lower().endswith(".json"):
                    files += 1
            except OSError:
                continue
    except PermissionError:
        raise InputError(f"No permission to read {folder}") from None
    parent = folder.parent
    return {
        "path": str(folder),
        "parent": str(parent) if parent != folder else None,
        "home": str(Path.home()),
        "folders": sorted(folders, key=str.lower),
        "json_files": files,
    }


def client_for(integration_id, pipeline=None):
    """A client for a connection; for altk-evolve, `pipeline` supplies how its trajectories are stored."""
    i = get_integration(integration_id)
    if not i:
        raise InputError(f"No integration {integration_id}")
    if i["kind"] == "langfuse":
        return Langfuse(i["host"], i["public_key"], i["secret_key"])
    if i["kind"] == "filesystem":
        return Filesystem(i["path"], i.get("recursive"), i.get("exclude"))
    if i["kind"] == "phoenix":
        return Phoenix(i["host"], i.get("api_key"), i.get("project"))
    if i["kind"] == "altk_evolve":
        mem = (pipeline or {}).get("memory") or {}
        settings = {f: mem.get(f) for f in MEMORY_FIELDS}
        settings["processing_profile"] = settings.get("processing_profile") or (pipeline and f"forge__{pipeline['id']}")
        return Evolve(i["host"], i.get("api_key"), **settings)
    raise InputError(f"Cannot connect to {i['kind']}")


def test_integration(integration_id):
    try:
        return {"ok": True, "message": client_for(integration_id).check()}
    except (SourceError, InputError) as e:
        return {"ok": False, "message": str(e)}


def seed_from_env():
    """First run: turn the Langfuse connection in .env into an integration."""
    if INTEGRATIONS.exists():
        return
    load_env()
    keys = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    if all(os.environ.get(k) for k in keys):
        save_integration(
            {
                "kind": "langfuse",
                "name": "Langfuse (from .env)",
                "host": os.environ["LANGFUSE_HOST"],
                "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
                "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            }
        )
