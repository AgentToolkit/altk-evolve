#!/usr/bin/env python3
"""
Save Entities Script
Reads entities from stdin JSON and writes each as a markdown file
in the entities directory, organized by type.

For skill-flow entities, automatically decomposes them into atomic skills
if they don't already exist, then references those atomic skills.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory.
# Every host installs the shared lib under lib/evolve-lite/ so multiple
# plugins can coexist side by side (e.g. .bob/lib/evolve-lite/).
_script = Path(__file__).resolve()
_lib = None
for _ancestor in _script.parents:
    _candidate = _ancestor / "lib" / "evolve-lite"
    if (_candidate / "entity_io.py").is_file():
        _lib = _candidate
        break
if _lib is None:
    raise ImportError(f"Cannot find plugin lib directory above {_script}")
sys.path.insert(0, str(_lib))
from entity_io import (  # noqa: E402
    find_entities_dir,
    get_default_entities_dir,
    load_all_entities,
    load_product_registry,
    write_entity_file,
    log as _log,
    slugify,
)


def log(message):
    _log("save", message)


log("Script started")


def normalize(text):
    """Normalize content for dedup comparison."""
    return " ".join(text.lower().split())


def extract_steps_from_flow(content):
    """Extract individual steps from a skill-flow content.
    
    Returns a list of step descriptions that could become atomic skills.
    """
    steps = []
    
    # Pattern 1: Numbered steps like "1) Step description. 2) Next step."
    # Match from number to the next number or end of string
    numbered_pattern = r'\d+\)\s*([^0-9]+?)(?=\s*\d+\)|$)'
    numbered_matches = re.findall(numbered_pattern, content, re.DOTALL)
    if numbered_matches:
        for match in numbered_matches:
            # Clean up the step text
            step = match.strip()
            # Remove trailing period if present
            step = step.rstrip('.')
            # Remove extra whitespace
            step = ' '.join(step.split())
            if step and len(step) > 10:
                steps.append(step)
    
    # Pattern 2: Steps separated by periods or semicolons (fallback)
    if not steps:
        # Split on period followed by capital letter or number
        sentence_pattern = r'[.;]\s*(?=[A-Z0-9])'
        sentences = re.split(sentence_pattern, content)
        steps.extend([s.strip() for s in sentences if len(s.strip()) > 20])
    
    return steps


def detect_product(content, trigger, trajectory_path="", entities_dir=None):
    """Detect which product/domain this entity belongs to.

    Uses the merged product registry from ``load_product_registry()``, which
    combines:
      1. Declared products in ``.bob/lib/evolve-lite/products.yaml`` — matched
         via explicit regex patterns.  Add a new entry here to register a product
         before any entities exist; its folder will be created on first save.
      2. Folder-only products discovered from existing entity subdirectories —
         matched by slug-token heuristic (no explicit patterns needed).

    Scoring:
      - Config products: count of matching explicit patterns (each hit = 1).
      - Folder-only products: count of whole-word slug-token hits, weighted by
        token count so multi-word slugs beat partial single-token matches.
    The highest-scoring product wins.  "general" is returned only when nothing
    scores above zero.

    Returns:
        str: A product slug, or "general" as the final fallback.
    """
    combined_text = f"{content} {trigger}".lower()

    registry = load_product_registry(entities_dir)
    if not registry:
        return "general"

    best_product = None
    best_score = 0

    for entry in registry:
        slug = entry["slug"]
        patterns = entry.get("patterns", [])

        if patterns:
            # Config entry: each matching pattern counts as one hit; weight by
            # number of patterns so richer entries edge out sparse ones.
            hits = sum(
                1 for p in patterns
                if re.search(p, combined_text, re.IGNORECASE)
            )
            weighted = hits * len(patterns)
        else:
            # Folder-only entry: fall back to slug-token heuristic.
            tokens = {t for t in slug.split("-") if len(t) > 1}
            hits = sum(
                1 for tok in tokens
                if re.search(rf"\b{re.escape(tok)}\b", combined_text)
            )
            weighted = hits * len(tokens)

        if weighted > best_score:
            best_score = weighted
            best_product = slug

    # "general" is the final fallback — only reached when nothing scored.
    return best_product if best_score > 0 else "general"


def is_common_sense_skill(content, trigger):
    """Determine if a skill is too basic/common-sense to save.
    
    Returns:
        bool: True if the skill should be filtered out
    """
    combined_text = f"{content} {trigger}".lower()
    
    # Patterns that indicate common-sense or well-known practices
    common_sense_patterns = [
        # Basic Python/programming
        r"^(create|write|make)\s+a\s+(file|directory|folder)$",
        r"^install\s+dependencies$",
        r"^activate\s+(the\s+)?virtual\s+environment$",
        r"^run\s+(the\s+)?(application|script|program)$",
        r"^import\s+(a\s+)?module$",
        r"^set\s+up\s+a\s+virtual\s+environment$",
        
        # Basic git operations
        r"^git\s+(add|commit|push|pull)$",
        r"^create\s+a\s+branch$",
        
        # Basic file operations
        r"^read\s+(a\s+)?file$",
        r"^write\s+to\s+(a\s+)?file$",
        r"^delete\s+(a\s+)?file$",
        
        # Too vague
        r"^when\s+performing\s+",
        r"^do\s+something$",
    ]
    
    # Check content length - very short content is likely too trivial
    if len(content.strip()) < 20:
        return True
    
    for pattern in common_sense_patterns:
        if re.search(pattern, combined_text):
            log(f"Filtered common-sense skill: {content[:60]}")
            return True
    
    return False


def mark_failure_derived(entity):
    """Mark entity if it's derived from a failure/error.
    
    Sets the derived_from_failure flag to "true" if the entity
    contains failure indicators.
    """
    content = entity.get("content", "").lower()
    rationale = entity.get("rationale", "").lower()
    trigger = entity.get("trigger", "").lower()
    
    # Check if derived from failure
    failure_indicators = [
        "error", "failed", "exception", "wrong", "incorrect",
        "retry", "workaround", "fix", "resolved", "token expired",
        "permission denied", "not found", "missing"
    ]
    
    has_failure_indicator = any(
        indicator in content or indicator in rationale or indicator in trigger
        for indicator in failure_indicators
    )
    
    if has_failure_indicator:
        entity["derived_from_failure"] = "true"


def create_atomic_skill_from_step(step, flow_trigger, flow_trajectory):
    """Create an atomic skill entity from a step description.
    
    Args:
        step: The step description
        flow_trigger: The parent skill-flow's trigger for context
        flow_trajectory: The trajectory to associate with this atomic skill
    
    Returns:
        A dict representing an atomic skill entity
    """
    step_lower = step.lower()

    # Derive a short verb-noun name (2-4 words) — strip leading action words
    # and code fragments, keep the core capability phrase.
    # Remove backtick-quoted commands entirely from the name.
    name_text = re.sub(r'`[^`]*`', '', step).strip()
    # Collapse whitespace
    name_text = ' '.join(name_text.split())
    # Take first 4 words maximum for a short slug-friendly name
    name_words = name_text.split()[:4]
    name = ' '.join(name_words).lower().rstrip('.,;:')

    # Build a situational trigger that describes context, not the command.
    if 'create' in step_lower or 'write' in step_lower:
        trigger = f"When a {step_lower.split('create')[-1].split('write')[-1].strip().split()[0] if step_lower.split('create')[-1].strip() else 'file'} needs to be created"
    elif 'activate' in step_lower or 'enable' in step_lower:
        trigger = "When the virtual environment needs to be activated before running CLI commands"
    elif 'ensure' in step_lower or 'verify' in step_lower or 'authenticate' in step_lower:
        trigger = "When CLI authentication needs to be confirmed before proceeding"
    elif 'import' in step_lower or 'upload' in step_lower:
        trigger = "When deploying or registering an artifact via the CLI"
    elif 'deploy' in step_lower:
        trigger = "When deploying a service or agent to a remote environment"
    else:
        # Generic fallback: use first clause of the step as situational context
        first_clause = step.split('.')[0].strip()
        trigger = f"When {first_clause.lower()}" if not first_clause.lower().startswith('when') else first_clause

    return {
        "type": "atomic-skill",
        "name": name,
        "trigger": trigger,
        "content": step,
        "rationale": f"Extracted as a reusable atomic capability from a skill-flow. Part of: {flow_trigger}",
        "trajectory": flow_trajectory
    }


def find_or_create_atomic_skills(flow_entity, entities_dir, existing_entities):
    """For a skill-flow, find or create the atomic skills it references.
    
    Args:
        flow_entity: The skill-flow entity dict
        entities_dir: Path to the entities directory
        existing_entities: List of existing entity dicts
    
    Returns:
        List of atomic skill slugs that this flow should reference
    """
    content = flow_entity.get("content", "")
    trigger = flow_entity.get("trigger", "")
    trajectory = flow_entity.get("trajectory", "")
    
    # Extract steps from the flow
    steps = extract_steps_from_flow(content)
    
    if not steps:
        log(f"No steps extracted from skill-flow: {content[:60]}")
        return []
    
    log(f"Extracted {len(steps)} steps from skill-flow")
    
    atomic_skill_refs = []
    existing_contents = {normalize(e["content"]) for e in existing_entities if e.get("content")}
    
    for step in steps:
        # Check if an atomic skill already exists for this step
        normalized_step = normalize(step)
        
        # Look for similar existing atomic skills
        found_existing = False
        for existing in existing_entities:
            if existing.get("type") == "atomic-skill":
                if normalized_step == normalize(existing.get("content", "")):
                    # Found exact match
                    found_existing = True
                    # Prefer name field for slug, matching write_entity_file behaviour
                    slug_source = existing.get("name") or existing.get("content", "")
                    slug = slugify(slug_source, product=existing.get("product"))
                    atomic_skill_refs.append(slug)
                    log(f"Found existing atomic skill: {slug}")
                    break
        
        if not found_existing:
            # Create new atomic skill
            atomic_skill = create_atomic_skill_from_step(step, trigger, trajectory)
            atomic_skill["owner"] = flow_entity.get("owner", "unknown")
            atomic_skill["visibility"] = "private"
            
            # Write the atomic skill
            path = write_entity_file(entities_dir, atomic_skill)
            # Prefer name field for slug, matching write_entity_file behaviour
            slug_source = atomic_skill.get("name") or atomic_skill.get("content", "")
            slug = slugify(slug_source, product=atomic_skill.get("product"))
            atomic_skill_refs.append(slug)
            
            # Add to existing contents to avoid duplicates in same batch
            existing_contents.add(normalized_step)
            existing_entities.append(atomic_skill)
            
            log(f"Created new atomic skill: {path}")
    
    return atomic_skill_refs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=None, help="Stamp owner on every entity written")
    args = parser.parse_args()

    try:
        input_data = json.load(sys.stdin)
        log(f"Received input with keys: {list(input_data.keys())}")
    except json.JSONDecodeError as e:
        log(f"Failed to parse JSON input: {e}")
        print(f"Error: Invalid JSON input - {e}", file=sys.stderr)
        sys.exit(1)

    new_entities = input_data.get("entities", [])
    if not isinstance(new_entities, list):
        log(f"Invalid entities payload type: {type(new_entities).__name__}")
        print("Error: `entities` must be a list.", file=sys.stderr)
        sys.exit(1)
    if not new_entities:
        log("No entities in input")
        print("No entities provided in input.", file=sys.stderr)
        sys.exit(0)

    log(f"Received {len(new_entities)} new entities")

    entities_dir = find_entities_dir()
    if entities_dir:
        entities_dir = entities_dir.resolve()
        log(f"Found existing dir: {entities_dir}")
        print(f"Using existing entities dir: {entities_dir}")
    else:
        entities_dir = get_default_entities_dir()
        log(f"Created new dir: {entities_dir}")
        print(f"Created new entities dir: {entities_dir}")

    existing_entities = load_all_entities(entities_dir)
    existing_contents = {normalize(e["content"]) for e in existing_entities if e.get("content")}
    log(f"Existing entities: {len(existing_entities)}")

    added_count = 0
    atomic_skills_created = 0
    filtered_count = 0
    
    for entity in new_entities:
        content = entity.get("content")
        if not content:
            log(f"Skipping entity without content: {entity}")
            continue
        if normalize(content) in existing_contents:
            log(f"Skipping duplicate: {content[:60]}")
            continue
        
        # Filter out common-sense skills
        trigger = entity.get("trigger", "")
        if is_common_sense_skill(content, trigger):
            filtered_count += 1
            continue

        # Detect and set product
        trajectory = entity.get("trajectory", "")
        product = detect_product(content, trigger, trajectory)
        entity["product"] = product
        
        # Mark if derived from failure
        mark_failure_derived(entity)
        
        # Stamp owner and visibility from the script, never from stdin.
        # Untrusted upstream input (a prompt-injected agent) must not be
        # able to spoof either field, so unconditionally overwrite.
        entity["owner"] = args.user or "unknown"
        entity["visibility"] = "private"
        
        # If this is a skill-flow, decompose it into atomic skills first
        if entity.get("type") == "skill-flow":
            log(f"Processing skill-flow: {content[:60]}")
            atomic_skill_refs = find_or_create_atomic_skills(
                entity, entities_dir, existing_entities
            )
            
            if atomic_skill_refs:
                # Add references to the skill-flow
                entity["atomic_skills"] = ", ".join(atomic_skill_refs)
                atomic_skills_created += len(atomic_skill_refs)
                log(f"Skill-flow references {len(atomic_skill_refs)} atomic skills")

        path = write_entity_file(entities_dir, entity)
        existing_contents.add(normalize(content))
        added_count += 1
        log(f"Wrote: {path}")

    total = len(existing_entities) + added_count
    log(f"Added {added_count} new entities ({atomic_skills_created} atomic skills auto-created, {filtered_count} filtered). Total: {total}")
    print(f"Added {added_count} new entity(ies). Total: {total}")
    if atomic_skills_created > 0:
        print(f"  → Auto-created {atomic_skills_created} atomic skill(s) from skill-flow decomposition")
    if filtered_count > 0:
        print(f"  → Filtered {filtered_count} common-sense skill(s)")
    print(f"Entities stored in: {entities_dir}")


if __name__ == "__main__":
    main()
