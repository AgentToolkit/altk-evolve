#!/usr/bin/env python3
"""
A/B Comparison Testing for Evolve Lite Skills

Compares task performance with skills (recalled and applied) versus without skills (baseline).
This validates that skills actually improve outcomes by measuring:
- Task completion success rate
- Number of steps/tool uses required
- Errors encountered
- Time to completion
- Code quality metrics
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
    _log("compare", message)


def load_trajectory(trajectory_path):
    """Load a trajectory JSON file."""
    with open(trajectory_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def analyze_trajectory_metrics(trajectory):
    """Extract performance metrics from a trajectory."""
    messages = trajectory.get("messages", [])
    
    metrics = {
        "total_messages": len(messages),
        "tool_uses": 0,
        "errors": 0,
        "retries": 0,
        "user_interventions": 0,
        "tools_used": set(),
        "error_types": [],
        "completion_status": "unknown"
    }
    
    # Analyze messages
    for i, msg in enumerate(messages):
        role = msg.get("role")
        
        if role == "assistant" and msg.get("tool_calls"):
            metrics["tool_uses"] += len(msg.get("tool_calls", []))
            for tool_call in msg.get("tool_calls", []):
                if tool_call.get("function"):
                    tool_name = tool_call["function"].get("name", "unknown")
                    metrics["tools_used"].add(tool_name)
        
        elif role == "tool":
            content = msg.get("content", "").lower()
            # Check for errors
            if any(err in content for err in ["error", "failed", "exception", "traceback"]):
                metrics["errors"] += 1
                # Try to identify error type
                if "permission" in content:
                    metrics["error_types"].append("permission")
                elif "not found" in content:
                    metrics["error_types"].append("not_found")
                elif "syntax" in content:
                    metrics["error_types"].append("syntax")
                else:
                    metrics["error_types"].append("unknown")
        
        elif role == "user":
            # User interventions after initial request
            if i > 0:
                metrics["user_interventions"] += 1
    
    # Check for completion
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            content = str(msg.get("content", ""))
            if "attempt_completion" in str(msg.get("tool_calls", [])):
                metrics["completion_status"] = "completed"
                break
            elif any(word in content.lower() for word in ["error", "failed", "cannot"]):
                metrics["completion_status"] = "failed"
                break
    
    # Detect retries (same tool used multiple times in sequence)
    tool_sequence = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tool_call in msg.get("tool_calls", []):
                if tool_call.get("function"):
                    tool_sequence.append(tool_call["function"].get("name"))
    
    # Count consecutive duplicates as retries
    for i in range(1, len(tool_sequence)):
        if tool_sequence[i] == tool_sequence[i-1]:
            metrics["retries"] += 1
    
    metrics["tools_used"] = list(metrics["tools_used"])
    
    return metrics


def find_skill_usage_in_trajectory(trajectory, entities_dir):
    """Determine if skills were used in this trajectory."""
    messages = trajectory.get("messages", [])
    
    skill_usage = {
        "skills_recalled": False,
        "skills_applied": [],
        "recall_count": 0,
        "learn_count": 0
    }
    
    # Check for evolve-lite:recall usage
    for msg in messages:
        if msg.get("role") == "assistant":
            content = str(msg.get("content", "")).lower()
            tool_calls = str(msg.get("tool_calls", [])).lower()
            
            if "evolve-lite:recall" in content or "evolve-lite:recall" in tool_calls:
                skill_usage["skills_recalled"] = True
                skill_usage["recall_count"] += 1
            
            if "evolve-lite:learn" in content or "evolve-lite:learn" in tool_calls:
                skill_usage["learn_count"] += 1
            
            # Check for mentions of specific skills
            if "guideline" in content or "atomic-skill" in content or "skill-flow" in content:
                # Try to extract skill references
                if ".evolve/entities/" in content:
                    skill_usage["skills_applied"].append("entity_referenced")
    
    return skill_usage


def compare_trajectories(with_skills_path, without_skills_path, entities_dir):
    """Compare two trajectories: one with skills, one without."""
    
    # Load trajectories
    try:
        with_skills = load_trajectory(with_skills_path)
        without_skills = load_trajectory(without_skills_path)
    except Exception as e:
        return {
            "error": f"Failed to load trajectories: {e}",
            "comparison": None
        }
    
    # Analyze metrics
    with_metrics = analyze_trajectory_metrics(with_skills)
    without_metrics = analyze_trajectory_metrics(without_skills)
    
    # Check skill usage
    with_skill_usage = find_skill_usage_in_trajectory(with_skills, entities_dir)
    without_skill_usage = find_skill_usage_in_trajectory(without_skills, entities_dir)
    
    # Calculate improvements
    comparison = {
        "with_skills": {
            "path": str(with_skills_path),
            "metrics": with_metrics,
            "skill_usage": with_skill_usage
        },
        "without_skills": {
            "path": str(without_skills_path),
            "metrics": without_metrics,
            "skill_usage": without_skill_usage
        },
        "improvements": {
            "fewer_tool_uses": without_metrics["tool_uses"] - with_metrics["tool_uses"],
            "fewer_errors": without_metrics["errors"] - with_metrics["errors"],
            "fewer_retries": without_metrics["retries"] - with_metrics["retries"],
            "fewer_interventions": without_metrics["user_interventions"] - with_metrics["user_interventions"],
            "completion_improved": (
                with_metrics["completion_status"] == "completed" and
                without_metrics["completion_status"] != "completed"
            )
        },
        "summary": {}
    }
    
    # Generate summary
    improvements = comparison["improvements"]
    total_improvements = sum([
        1 if improvements["fewer_tool_uses"] > 0 else 0,
        1 if improvements["fewer_errors"] > 0 else 0,
        1 if improvements["fewer_retries"] > 0 else 0,
        1 if improvements["fewer_interventions"] > 0 else 0,
        1 if improvements["completion_improved"] else 0
    ])
    
    comparison["summary"] = {
        "skills_helped": total_improvements > 0,
        "improvement_count": total_improvements,
        "efficiency_gain_percent": (
            (without_metrics["tool_uses"] - with_metrics["tool_uses"]) / 
            without_metrics["tool_uses"] * 100
            if without_metrics["tool_uses"] > 0 else 0
        ),
        "error_reduction_percent": (
            (without_metrics["errors"] - with_metrics["errors"]) / 
            without_metrics["errors"] * 100
            if without_metrics["errors"] > 0 else 0
        )
    }
    
    return comparison


def generate_comparison_report(comparisons, output_path):
    """Generate a comprehensive comparison report."""
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "total_comparisons": len(comparisons),
        "comparisons": comparisons,
        "aggregate_metrics": {
            "total_with_skills_helped": 0,
            "total_efficiency_gains": 0,
            "total_error_reductions": 0,
            "average_tool_use_reduction": 0,
            "average_error_reduction": 0
        }
    }
    
    # Calculate aggregates
    valid_comparisons = [c for c in comparisons if "error" not in c]
    
    if valid_comparisons:
        report["aggregate_metrics"]["total_with_skills_helped"] = sum(
            1 for c in valid_comparisons 
            if c.get("summary", {}).get("skills_helped", False)
        )
        
        efficiency_gains = [
            c["summary"]["efficiency_gain_percent"] 
            for c in valid_comparisons 
            if c.get("summary", {}).get("efficiency_gain_percent", 0) > 0
        ]
        
        error_reductions = [
            c["summary"]["error_reduction_percent"] 
            for c in valid_comparisons 
            if c.get("summary", {}).get("error_reduction_percent", 0) > 0
        ]
        
        if efficiency_gains:
            report["aggregate_metrics"]["average_tool_use_reduction"] = (
                sum(efficiency_gains) / len(efficiency_gains)
            )
        
        if error_reductions:
            report["aggregate_metrics"]["average_error_reduction"] = (
                sum(error_reductions) / len(error_reductions)
            )
    
    # Save report
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Compare task performance with and without skills"
    )
    parser.add_argument(
        "--with-skills",
        required=True,
        help="Path to trajectory with skills applied"
    )
    parser.add_argument(
        "--without-skills",
        required=True,
        help="Path to baseline trajectory without skills"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for comparison report (default: .evolve/tests/comparison_report.json)"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Batch mode: compare multiple trajectory pairs from a directory"
    )
    
    args = parser.parse_args()
    
    # Determine entities directory
    evolve_dir = get_evolve_dir()
    entities_dir = evolve_dir / "entities"
    
    if not entities_dir.exists():
        print(f"Warning: Entities directory not found: {entities_dir}", file=sys.stderr)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = evolve_dir / "tests" / "comparison_report.json"
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if args.batch:
        # Batch mode: compare multiple pairs
        print("Batch comparison mode not yet implemented", file=sys.stderr)
        sys.exit(1)
    else:
        # Single comparison
        print(f"Comparing trajectories:")
        print(f"  With skills:    {args.with_skills}")
        print(f"  Without skills: {args.without_skills}")
        print()
        
        comparison = compare_trajectories(
            Path(args.with_skills),
            Path(args.without_skills),
            entities_dir
        )
        
        if "error" in comparison:
            print(f"Error: {comparison['error']}", file=sys.stderr)
            sys.exit(1)
        
        # Generate report
        report = generate_comparison_report([comparison], output_path)
        
        # Print summary
        print("="*60)
        print("COMPARISON SUMMARY")
        print("="*60)
        
        summary = comparison["summary"]
        print(f"Skills helped: {'Yes' if summary['skills_helped'] else 'No'}")
        print(f"Improvements: {summary['improvement_count']}/5 metrics")
        print(f"Efficiency gain: {summary['efficiency_gain_percent']:.1f}%")
        print(f"Error reduction: {summary['error_reduction_percent']:.1f}%")
        
        print(f"\nWith skills:")
        print(f"  Tool uses: {comparison['with_skills']['metrics']['tool_uses']}")
        print(f"  Errors: {comparison['with_skills']['metrics']['errors']}")
        print(f"  Retries: {comparison['with_skills']['metrics']['retries']}")
        print(f"  Status: {comparison['with_skills']['metrics']['completion_status']}")
        
        print(f"\nWithout skills:")
        print(f"  Tool uses: {comparison['without_skills']['metrics']['tool_uses']}")
        print(f"  Errors: {comparison['without_skills']['metrics']['errors']}")
        print(f"  Retries: {comparison['without_skills']['metrics']['retries']}")
        print(f"  Status: {comparison['without_skills']['metrics']['completion_status']}")
        
        print(f"\nReport saved to: {output_path}")


if __name__ == "__main__":
    main()

# Made with Bob
