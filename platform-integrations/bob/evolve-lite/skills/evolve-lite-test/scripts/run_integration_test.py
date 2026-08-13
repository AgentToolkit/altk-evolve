#!/usr/bin/env python3
"""
Integration Test Runner for Evolve Lite Skills

Executes real scenarios and verifies that skills are recalled and improve outcomes.
This is different from static validation - it actually runs Bob with test scenarios.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

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
    log as _log,
)


def log(message):
    _log("integration-test", message)


def load_scenario(scenario_path: Path) -> Dict[str, Any]:
    """Load a test scenario definition."""
    with open(scenario_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_trajectory(trajectory_path: Path) -> Dict[str, Any]:
    """Load a trajectory JSON file."""
    with open(trajectory_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def analyze_trajectory_for_skills(trajectory: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a trajectory to see if skills were recalled and used."""
    messages = trajectory.get("messages", [])
    
    analysis = {
        "skills_recalled": False,
        "recall_count": 0,
        "skills_mentioned": [],
        "recall_messages": []
    }
    
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            content = str(msg.get("content", "")).lower()
            
            # Check for recall skill usage
            if "evolve-lite:recall" in content or "use_skill" in str(msg.get("tool_calls", [])):
                analysis["skills_recalled"] = True
                analysis["recall_count"] += 1
                analysis["recall_messages"].append({
                    "index": i,
                    "content_preview": content[:200]
                })
            
            # Check for entity mentions
            if ".evolve/entities/" in content:
                # Try to extract entity paths
                import re
                entity_paths = re.findall(r'\.evolve/entities/[^\s\)]+\.md', content)
                analysis["skills_mentioned"].extend(entity_paths)
            
            # Check for "Recall complete" messages
            if "recall complete" in content:
                analysis["recall_messages"].append({
                    "index": i,
                    "type": "completion",
                    "content_preview": content[:200]
                })
    
    analysis["skills_mentioned"] = list(set(analysis["skills_mentioned"]))
    return analysis


def extract_trajectory_metrics(trajectory: Dict[str, Any]) -> Dict[str, Any]:
    """Extract performance metrics from a trajectory."""
    messages = trajectory.get("messages", [])
    
    metrics = {
        "total_messages": len(messages),
        "tool_uses": 0,
        "errors": 0,
        "completion_status": "unknown",
        "tools_used": set(),
        "duration_estimate": None
    }
    
    # Count tool uses and errors
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            metrics["tool_uses"] += len(msg.get("tool_calls", []))
            for tool_call in msg.get("tool_calls", []):
                if tool_call.get("function"):
                    metrics["tools_used"].add(tool_call["function"].get("name", "unknown"))
        
        elif msg.get("role") == "tool":
            content = msg.get("content", "").lower()
            if any(err in content for err in ["error", "failed", "exception"]):
                metrics["errors"] += 1
    
    # Check completion status
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            if "attempt_completion" in str(msg.get("tool_calls", [])):
                metrics["completion_status"] = "completed"
                break
            elif any(word in str(msg.get("content", "")).lower() for word in ["error", "failed", "cannot"]):
                metrics["completion_status"] = "failed"
                break
    
    metrics["tools_used"] = list(metrics["tools_used"])
    
    # Estimate duration from timestamps if available
    if len(messages) >= 2:
        try:
            first_time = messages[0].get("timestamp")
            last_time = messages[-1].get("timestamp")
            if first_time and last_time:
                from datetime import datetime
                start = datetime.fromisoformat(first_time.replace('Z', '+00:00'))
                end = datetime.fromisoformat(last_time.replace('Z', '+00:00'))
                metrics["duration_estimate"] = (end - start).total_seconds()
        except Exception:
            pass
    
    return metrics


def validate_scenario_outcome(
    scenario: Dict[str, Any],
    trajectory: Dict[str, Any],
    skill_analysis: Dict[str, Any],
    metrics: Dict[str, Any]
) -> Dict[str, Any]:
    """Validate that the scenario outcome meets success criteria."""
    
    success_criteria = scenario.get("success_criteria", {})
    expected_skills = scenario.get("expected_skills", [])
    
    validation = {
        "passed": True,
        "checks": {},
        "failures": []
    }
    
    # Check if expected skills were recalled
    if expected_skills:
        skills_found = []
        for expected_skill in expected_skills:
            found = any(expected_skill in mentioned for mentioned in skill_analysis["skills_mentioned"])
            skills_found.append(found)
            validation["checks"][f"skill_recalled_{expected_skill}"] = found
            if not found:
                validation["passed"] = False
                validation["failures"].append(f"Expected skill not recalled: {expected_skill}")
        
        validation["checks"]["all_expected_skills_recalled"] = all(skills_found)
    
    # Check completion status
    expected_status = success_criteria.get("completion_status")
    if expected_status:
        status_match = metrics["completion_status"] == expected_status
        validation["checks"]["completion_status"] = status_match
        if not status_match:
            validation["passed"] = False
            validation["failures"].append(
                f"Completion status mismatch: expected {expected_status}, got {metrics['completion_status']}"
            )
    
    # Check max tool uses
    max_tool_uses = success_criteria.get("max_tool_uses")
    if max_tool_uses:
        within_limit = metrics["tool_uses"] <= max_tool_uses
        validation["checks"]["tool_uses_within_limit"] = within_limit
        if not within_limit:
            validation["passed"] = False
            validation["failures"].append(
                f"Too many tool uses: {metrics['tool_uses']} > {max_tool_uses}"
            )
    
    # Check max errors
    max_errors = success_criteria.get("max_errors")
    if max_errors is not None:
        within_limit = metrics["errors"] <= max_errors
        validation["checks"]["errors_within_limit"] = within_limit
        if not within_limit:
            validation["passed"] = False
            validation["failures"].append(
                f"Too many errors: {metrics['errors']} > {max_errors}"
            )
    
    # Check required files exist
    required_files = success_criteria.get("required_files", [])
    if required_files:
        for required_file in required_files:
            file_path = Path(required_file)
            exists = file_path.exists()
            validation["checks"][f"file_exists_{required_file}"] = exists
            if not exists:
                validation["passed"] = False
                validation["failures"].append(f"Required file not found: {required_file}")
    
    return validation


def run_integration_test(
    scenario_path: Path,
    trajectory_path: Path,
    output_path: Path
) -> Dict[str, Any]:
    """Run an integration test for a scenario."""
    
    log(f"Running integration test for scenario: {scenario_path}")
    
    # Load scenario
    try:
        scenario = load_scenario(scenario_path)
    except Exception as e:
        return {
            "error": f"Failed to load scenario: {e}",
            "scenario_path": str(scenario_path)
        }
    
    # Load trajectory
    try:
        trajectory = load_trajectory(trajectory_path)
    except Exception as e:
        return {
            "error": f"Failed to load trajectory: {e}",
            "trajectory_path": str(trajectory_path)
        }
    
    # Analyze trajectory for skill usage
    skill_analysis = analyze_trajectory_for_skills(trajectory)
    
    # Extract metrics
    metrics = extract_trajectory_metrics(trajectory)
    
    # Validate outcome
    validation = validate_scenario_outcome(scenario, trajectory, skill_analysis, metrics)
    
    # Build result
    result = {
        "test_id": scenario.get("scenario_id", "unknown"),
        "scenario_path": str(scenario_path),
        "trajectory_path": str(trajectory_path),
        "timestamp": datetime.now().isoformat(),
        "scenario": scenario,
        "skill_analysis": skill_analysis,
        "metrics": metrics,
        "validation": validation,
        "passed": validation["passed"]
    }
    
    # Save result
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    
    log(f"Test result saved to: {output_path}")
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run integration tests for Evolve Lite skills"
    )
    parser.add_argument(
        "--scenario",
        required=True,
        help="Path to scenario definition JSON file"
    )
    parser.add_argument(
        "--trajectory",
        required=True,
        help="Path to trajectory JSON file to analyze"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for test result (default: .evolve/tests/integration/results/<scenario_id>.json)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed output"
    )
    
    args = parser.parse_args()
    
    scenario_path = Path(args.scenario)
    trajectory_path = Path(args.trajectory)
    
    if not scenario_path.exists():
        print(f"Error: Scenario file not found: {scenario_path}", file=sys.stderr)
        sys.exit(1)
    
    if not trajectory_path.exists():
        print(f"Error: Trajectory file not found: {trajectory_path}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        evolve_dir = get_evolve_dir()
        scenario_id = scenario_path.stem
        output_path = evolve_dir / "tests" / "integration" / "results" / f"{scenario_id}_result.json"
    
    # Run test
    print(f"Running integration test...")
    print(f"  Scenario: {scenario_path.name}")
    print(f"  Trajectory: {trajectory_path.name}")
    print()
    
    result = run_integration_test(scenario_path, trajectory_path, output_path)
    
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    
    # Print summary
    print("="*60)
    print("INTEGRATION TEST RESULT")
    print("="*60)
    print(f"Test ID: {result['test_id']}")
    print(f"Status: {'PASSED' if result['passed'] else 'FAILED'}")
    print()
    
    # Print skill analysis
    skill_analysis = result["skill_analysis"]
    print("Skill Usage:")
    print(f"  Skills recalled: {skill_analysis['skills_recalled']}")
    print(f"  Recall count: {skill_analysis['recall_count']}")
    print(f"  Skills mentioned: {len(skill_analysis['skills_mentioned'])}")
    if skill_analysis['skills_mentioned']:
        for skill in skill_analysis['skills_mentioned']:
            print(f"    - {skill}")
    print()
    
    # Print metrics
    metrics = result["metrics"]
    print("Metrics:")
    print(f"  Tool uses: {metrics['tool_uses']}")
    print(f"  Errors: {metrics['errors']}")
    print(f"  Completion: {metrics['completion_status']}")
    print(f"  Tools used: {', '.join(metrics['tools_used'][:5])}")
    if len(metrics['tools_used']) > 5:
        print(f"    ... and {len(metrics['tools_used']) - 5} more")
    print()
    
    # Print validation
    validation = result["validation"]
    print("Validation:")
    print(f"  Checks passed: {sum(1 for v in validation['checks'].values() if v)}/{len(validation['checks'])}")
    if validation['failures']:
        print("  Failures:")
        for failure in validation['failures']:
            print(f"    - {failure}")
    print()
    
    print(f"Result saved to: {output_path}")
    
    if args.verbose:
        print("\nDetailed Result:")
        print(json.dumps(result, indent=2))
    
    sys.exit(0 if result['passed'] else 1)


if __name__ == "__main__":
    main()

# Made with Bob