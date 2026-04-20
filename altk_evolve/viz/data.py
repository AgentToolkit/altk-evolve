"""Data loading for the viz server.

Reads entity markdown files and trajectory JSON files from a .evolve/ directory.
No external dependencies — uses only stdlib + the project's own parsing.
"""

import json
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Frontmatter parser (simple key: value, no nested structures needed)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict:
    result = {}
    for line in text.strip().splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            result[key.strip()] = value.strip()
        elif line.endswith(":"):
            result[line[:-1].strip()] = ""
    return result


def _parse_entity_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")

    fm_match = re.match(r"^---\n(.*?)\n---\n?(.*)", text, re.DOTALL)
    if fm_match:
        frontmatter = _parse_frontmatter(fm_match.group(1))
        body = fm_match.group(2).strip()
    else:
        frontmatter = {}
        body = text.strip()

    # Split content from ## Rationale section
    rationale_match = re.search(r"## Rationale\s*\n(.*?)(?=\n## |\Z)", body, re.DOTALL)
    rationale_idx = body.find("\n## Rationale")
    content = body[:rationale_idx].strip() if rationale_idx != -1 else body
    rationale = rationale_match.group(1).strip() if rationale_match else ""

    return {
        "slug": path.stem,
        "filename": path.name,
        "type": frontmatter.get("type", "guideline"),
        "trigger": frontmatter.get("trigger", ""),
        "trajectory": frontmatter.get("trajectory", ""),
        "content": content,
        "rationale": rationale,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_entities(evolve_dir: Path) -> list[dict]:
    """Load all entity files from <evolve_dir>/entities/."""
    entities_dir = evolve_dir / "entities"
    if not entities_dir.exists():
        return []

    entities = []
    for md_file in sorted(entities_dir.rglob("*.md")):
        try:
            entities.append(_parse_entity_file(md_file))
        except Exception:
            pass
    return entities


def _traj_filename(traj_path: str) -> str:
    """Normalize a trajectory field value to just the filename."""
    return Path(traj_path).name if traj_path else ""


def load_trajectories(evolve_dir: Path, entities: list[dict]) -> list[dict]:
    """Load trajectory summaries from <evolve_dir>/trajectories/."""
    trajectories_dir = evolve_dir / "trajectories"
    if not trajectories_dir.exists():
        return []

    # Index: trajectory filename -> list of entity slugs
    traj_index: dict[str, list[str]] = {}
    for entity in entities:
        fname = _traj_filename(entity.get("trajectory", ""))
        if fname:
            traj_index.setdefault(fname, []).append(entity["slug"])

    results = []
    for json_file in sorted(trajectories_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            fname = json_file.name
            linked = traj_index.get(fname, [])
            results.append(
                {
                    "filename": fname,
                    "model": data.get("model", ""),
                    "timestamp": data.get("timestamp", ""),
                    "message_count": len(data.get("messages", [])),
                    "guideline_count": len(linked),
                    "guidelines": linked,
                }
            )
        except Exception:
            pass
    return results


def load_trajectory_detail(evolve_dir: Path, filename: str, entities: list[dict]) -> dict | None:
    """Load a single trajectory with full messages."""
    traj_file = evolve_dir / "trajectories" / filename
    if not traj_file.exists():
        return None
    try:
        data = json.loads(traj_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    linked = [e["slug"] for e in entities if _traj_filename(e.get("trajectory", "")) == filename]

    return {
        "filename": filename,
        "model": data.get("model", ""),
        "timestamp": data.get("timestamp", ""),
        "messages": data.get("messages", []),
        "guidelines": linked,
    }


def load_entity_detail(evolve_dir: Path, slug: str) -> dict | None:
    """Load a single entity by slug."""
    entities_dir = evolve_dir / "entities"
    if not entities_dir.exists():
        return None
    matches = list(entities_dir.rglob(f"{slug}.md"))
    if len(matches) == 1:
        try:
            return _parse_entity_file(matches[0])
        except Exception:
            return None
    return None
