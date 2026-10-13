#!/usr/bin/env python3
"""
Test Case Generator for Evolve Lite Skills

Analyzes conversation trajectories and generates test cases for each skill
that was learned from those trajectories. Test cases validate that skills
are discoverable, actionable, complete, and compose correctly.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Walk up from the script location to find the installed plugin lib directory
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
    get_evolve_dir,
    markdown_to_entity,
    log as _log,
)


def log(message):
    _log("test-gen", message)


def load_trajectory(trajectory_path):
    """Load a trajectory JSON file."""
    with open(trajectory_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def find_skills_from_trajectory(trajectory_path, entities_dir):
    """Find all skills that reference this trajectory."""
    # Get both absolute and relative paths for matching
    trajectory_path_abs = str(Path(trajectory_path).resolve())
    
    # Try to get relative path, fall back to the path as-is
    try:
        trajectory_path_rel = str(Path(trajectory_path).resolve().relative_to(Path.cwd()))
    except ValueError:
        trajectory_path_rel = str(trajectory_path)
    
    trajectory_name = Path(trajectory_path).name
    
    skills = []
    
    entities_dir = Path(entities_dir)
    for md_file in entities_dir.glob("**/*.md"):
        if md_file.is_symlink() or ".git" in md_file.parts:
            continue
        
        try:
            entity = markdown_to_entity(md_file)
            entity_traj = entity.get("trajectory", "")
            
            # Match by absolute path, relative path, or filename
            if entity_traj and (
                trajectory_path_abs in entity_traj or
                trajectory_path_rel in entity_traj or
                trajectory_name in entity_traj
            ):
                skills.append({
                    "path": str(md_file),
                    "entity": entity,
                    "type": entity.get("type", "unknown")
                })
        except Exception as e:
            log(f"Error reading {md_file}: {e}")
    
    return skills


def extract_user_request(trajectory):
    """Extract the initial user request from a trajectory."""
    messages = trajectory.get("messages", [])
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if content and isinstance(content, str):
                return content
    return "Unknown request"


def extract_context_from_trajectory(trajectory):
    """Extract relevant context from the trajectory."""
    metadata = trajectory.get("metadata", {})
    
    context = {
        "model": trajectory.get("model", "unknown"),
        "session_id": trajectory.get("session_id", "unknown"),
        "timestamp": trajectory.get("timestamp", "unknown"),
        "project_root": metadata.get("project_root", "unknown"),
        "mode": metadata.get("mode", "unknown")
    }
    
    # Extract environment details from messages
    messages = trajectory.get("messages", [])
    for msg in messages:
        if msg.get("role") == "user" and "environment" in msg.get("content", "").lower():
            context["has_environment_details"] = True
            break
    
    return context


def extract_tools_used(trajectory):
    """Extract tools that were used in the trajectory."""
    tools = set()
    messages = trajectory.get("messages", [])
    
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tool_call in msg.get("tool_calls", []):
                if tool_call.get("function"):
                    tools.add(tool_call["function"].get("name", "unknown"))
    
    return list(tools)


def generate_trigger_match_test(skill, trajectory, trajectory_path):
    """Generate a trigger match test case."""
    user_request = extract_user_request(trajectory)
    context = extract_context_from_trajectory(trajectory)
    
    test_case = {
        "test_id": f"trigger_match_{Path(skill['path']).stem}_{Path(trajectory_path).stem}",
        "test_type": "trigger_match",
        "skill_type": skill["type"],
        "skill_path": skill["path"],
        "skill_trigger": skill["entity"].get("trigger", ""),
        "scenario": f"User request: {user_request[:100]}...",
        "expected_trigger_match": True,
        "input_context": {
            "user_request": user_request,
            "environment": context,
            "tools_available": extract_tools_used(trajectory)
        },
        "validation_criteria": [
            "Skill trigger matches the user request context",
            "Skill would be recalled during similar scenarios",
            "Trigger is specific enough to avoid false positives"
        ],
        "trajectory_source": str(trajectory_path),
        "created_at": datetime.now().isoformat()
    }
    
    return test_case


def generate_content_completeness_test(skill, trajectory, trajectory_path):
    """Generate a content completeness test case."""
    user_request = extract_user_request(trajectory)
    tools_used = extract_tools_used(trajectory)
    
    test_case = {
        "test_id": f"content_complete_{Path(skill['path']).stem}_{Path(trajectory_path).stem}",
        "test_type": "content_completeness",
        "skill_type": skill["type"],
        "skill_path": skill["path"],
        "skill_content": skill["entity"].get("content", "")[:200] + "...",
        "scenario": "Validating skill provides sufficient guidance",
        "input_context": {
            "user_request": user_request,
            "tools_used_in_trajectory": tools_used
        },
        "validation_criteria": [
            "All necessary steps are included",
            "Commands or actions are clear and executable",
            "Prerequisites are stated",
            "Expected outcomes are defined",
            "Rationale explains why this approach works"
        ],
        "trajectory_source": str(trajectory_path),
        "created_at": datetime.now().isoformat()
    }
    
    return test_case


def generate_skill_composition_test(skill, trajectory, trajectory_path, entities_dir):
    """Generate a skill composition test for skill-flows."""
    if skill["type"] != "skill-flow":
        return None
    
    atomic_skills = skill["entity"].get("atomic_skills", "")
    if not atomic_skills:
        return None
    
    atomic_skill_list = [s.strip() for s in atomic_skills.split(",")]
    
    # Check if atomic skills exist
    existing_atomic_skills = []
    missing_atomic_skills = []
    
    for atomic_skill_slug in atomic_skill_list:
        atomic_skill_path = Path(entities_dir) / "atomic-skill" / f"{atomic_skill_slug}.md"
        if atomic_skill_path.exists():
            existing_atomic_skills.append(atomic_skill_slug)
        else:
            missing_atomic_skills.append(atomic_skill_slug)
    
    test_case = {
        "test_id": f"composition_{Path(skill['path']).stem}_{Path(trajectory_path).stem}",
        "test_type": "skill_composition",
        "skill_type": skill["type"],
        "skill_path": skill["path"],
        "scenario": "Validating skill-flow properly references atomic skills",
        "atomic_skills_referenced": atomic_skill_list,
        "atomic_skills_existing": existing_atomic_skills,
        "atomic_skills_missing": missing_atomic_skills,
        "validation_criteria": [
            "All referenced atomic skills exist",
            "Atomic skills are in correct order",
            "No circular dependencies",
            "Atomic skill content matches flow steps"
        ],
        "expected_outcome": {
            "all_atomic_skills_exist": len(missing_atomic_skills) == 0,
            "composition_is_valid": True
        },
        "trajectory_source": str(trajectory_path),
        "created_at": datetime.now().isoformat()
    }
    
    return test_case


def generate_trajectory_replay_test(skill, trajectory, trajectory_path):
    """Generate a trajectory replay test case."""
    user_request = extract_user_request(trajectory)
    context = extract_context_from_trajectory(trajectory)
    tools_used = extract_tools_used(trajectory)
    
    test_case = {
        "test_id": f"replay_{Path(skill['path']).stem}_{Path(trajectory_path).stem}",
        "test_type": "trajectory_replay",
        "skill_type": skill["type"],
        "skill_path": skill["path"],
        "scenario": "Replaying trajectory with skill guidance",
        "input_context": {
            "user_request": user_request,
            "environment": context,
            "tools_used": tools_used
        },
        "validation_criteria": [
            "Skill would be recalled at appropriate time",
            "Skill guidance matches actions taken",
            "Following skill would achieve same outcome",
            "Skill prevents errors encountered in trajectory"
        ],
        "expected_outcome": {
            "skill_recalled": True,
            "skill_applied": True,
            "outcome_matches": True
        },
        "trajectory_source": str(trajectory_path),
        "created_at": datetime.now().isoformat()
    }
    
    return test_case


def generate_test_cases_for_skill(skill, trajectory, trajectory_path, entities_dir):
    """Generate all test cases for a single skill."""
    test_cases = []
    
    # Generate trigger match test
    test_cases.append(generate_trigger_match_test(skill, trajectory, trajectory_path))
    
    # Generate content completeness test
    test_cases.append(generate_content_completeness_test(skill, trajectory, trajectory_path))
    
    # Generate composition test for skill-flows
    composition_test = generate_skill_composition_test(skill, trajectory, trajectory_path, entities_dir)
    if composition_test:
        test_cases.append(composition_test)
    
    # Generate trajectory replay test
    test_cases.append(generate_trajectory_replay_test(skill, trajectory, trajectory_path))
    
    return test_cases


def save_test_cases(test_cases, output_dir):
    """Save test cases to JSON files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    saved_files = []
    for test_case in test_cases:
        filename = f"{test_case['test_id']}.json"
        output_path = output_dir / filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(test_case, f, indent=2)
        
        saved_files.append(output_path)
        log(f"Saved test case: {output_path}")
    
    return saved_files


def main():
    parser = argparse.ArgumentParser(
        description="Generate test cases for skills from trajectories"
    )
    parser.add_argument(
        "--trajectory",
        help="Path to specific trajectory file"
    )
    parser.add_argument(
        "--all-trajectories",
        action="store_true",
        help="Generate tests for all trajectories"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for test cases (default: .evolve/tests/)"
    )
    
    args = parser.parse_args()
    
    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        evolve_dir = get_evolve_dir()
        output_dir = evolve_dir / "tests" / "cases"
    
    # Determine entities directory
    evolve_dir = get_evolve_dir()
    entities_dir = evolve_dir / "entities"
    
    if not entities_dir.exists():
        print(f"Error: Entities directory not found: {entities_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine trajectories to process
    trajectories_dir = evolve_dir / "trajectories"
    
    if args.all_trajectories:
        if not trajectories_dir.exists():
            print(f"Error: Trajectories directory not found: {trajectories_dir}", file=sys.stderr)
            sys.exit(1)
        trajectory_files = list(trajectories_dir.glob("*.json"))
    elif args.trajectory:
        trajectory_files = [Path(args.trajectory)]
    else:
        print("Error: Must specify --trajectory or --all-trajectories", file=sys.stderr)
        sys.exit(1)
    
    if not trajectory_files:
        print("No trajectory files found", file=sys.stderr)
        sys.exit(1)
    
    print(f"Processing {len(trajectory_files)} trajectory file(s)...")
    
    all_test_cases = []
    
    for trajectory_path in trajectory_files:
        log(f"Processing trajectory: {trajectory_path}")
        print(f"\nProcessing: {trajectory_path.name}")
        
        try:
            trajectory = load_trajectory(trajectory_path)
        except Exception as e:
            print(f"  Error loading trajectory: {e}", file=sys.stderr)
            continue
        
        # Find skills from this trajectory
        skills = find_skills_from_trajectory(trajectory_path, entities_dir)
        print(f"  Found {len(skills)} skill(s) from this trajectory")
        
        for skill in skills:
            print(f"    Generating tests for: {Path(skill['path']).name}")
            test_cases = generate_test_cases_for_skill(
                skill, trajectory, trajectory_path, entities_dir
            )
            all_test_cases.extend(test_cases)
            print(f"      Generated {len(test_cases)} test case(s)")
    
    # Save all test cases
    print(f"\nSaving {len(all_test_cases)} test case(s) to {output_dir}")
    saved_files = save_test_cases(all_test_cases, output_dir)
    
    print(f"\n✓ Generated {len(all_test_cases)} test case(s)")
    print(f"✓ Saved to: {output_dir}")
    print(f"\nTest files:")
    for f in saved_files:
        print(f"  - {f.name}")


if __name__ == "__main__":
    main()

# Made with Bob
