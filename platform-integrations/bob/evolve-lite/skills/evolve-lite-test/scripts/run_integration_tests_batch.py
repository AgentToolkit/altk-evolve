#!/usr/bin/env python3
"""
Batch Integration Test Runner for Evolve Lite Skills

Runs multiple integration tests and generates a summary report.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
import subprocess

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
    _log("integration-batch", message)


def find_scenarios(scenarios_dir: Path) -> List[Path]:
    """Find all scenario JSON files in the scenarios directory."""
    return list(scenarios_dir.glob("*.json"))


def find_matching_trajectory(scenario: Dict[str, Any], trajectories_dir: Path) -> Path:
    """Find a trajectory that matches the scenario."""
    # For now, just return the most recent trajectory
    # In the future, could match based on user_request or other criteria
    trajectories = sorted(trajectories_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if trajectories:
        return trajectories[0]
    return None


def run_single_test(scenario_path: Path, trajectory_path: Path, output_dir: Path) -> Dict[str, Any]:
    """Run a single integration test."""
    log(f"Running test: {scenario_path.stem}")
    
    output_path = output_dir / f"{scenario_path.stem}_result.json"
    
    # Run the integration test script
    cmd = [
        sys.executable,
        str(Path(__file__).parent / "run_integration_test.py"),
        "--scenario", str(scenario_path),
        "--trajectory", str(trajectory_path),
        "--output", str(output_path)
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        # Load the result
        if output_path.exists():
            with open(output_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {
                "error": "Test output not found",
                "scenario_path": str(scenario_path),
                "passed": False
            }
    except subprocess.TimeoutExpired:
        return {
            "error": "Test timed out",
            "scenario_path": str(scenario_path),
            "passed": False
        }
    except Exception as e:
        return {
            "error": str(e),
            "scenario_path": str(scenario_path),
            "passed": False
        }


def generate_batch_report(results: List[Dict[str, Any]], output_path: Path):
    """Generate a summary report for all tests."""
    
    total_tests = len(results)
    passed_tests = sum(1 for r in results if r.get("passed", False))
    failed_tests = total_tests - passed_tests
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "pass_rate": passed_tests / total_tests if total_tests > 0 else 0
        },
        "results": results,
        "by_scenario": {}
    }
    
    # Group by scenario
    for result in results:
        scenario_id = result.get("test_id", "unknown")
        report["by_scenario"][scenario_id] = {
            "passed": result.get("passed", False),
            "skills_recalled": result.get("skill_analysis", {}).get("skills_recalled", False),
            "tool_uses": result.get("metrics", {}).get("tool_uses", 0),
            "errors": result.get("metrics", {}).get("errors", 0),
            "completion_status": result.get("metrics", {}).get("completion_status", "unknown")
        }
    
    # Save report
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Run multiple integration tests in batch"
    )
    parser.add_argument(
        "--scenarios-dir",
        default=None,
        help="Directory containing scenario JSON files (default: .evolve/tests/integration/scenarios/)"
    )
    parser.add_argument(
        "--trajectories-dir",
        default=None,
        help="Directory containing trajectory JSON files (default: .evolve/trajectories/)"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output path for batch report (default: .evolve/tests/integration/batch_report.json)"
    )
    parser.add_argument(
        "--scenario",
        action="append",
        help="Specific scenario file(s) to test (can be used multiple times)"
    )
    parser.add_argument(
        "--trajectory",
        help="Specific trajectory to use for all tests"
    )
    
    args = parser.parse_args()
    
    evolve_dir = get_evolve_dir()
    
    # Determine directories
    if args.scenarios_dir:
        scenarios_dir = Path(args.scenarios_dir)
    else:
        scenarios_dir = evolve_dir / "tests" / "integration" / "scenarios"
    
    if args.trajectories_dir:
        trajectories_dir = Path(args.trajectories_dir)
    else:
        trajectories_dir = evolve_dir / "trajectories"
    
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = evolve_dir / "tests" / "integration" / "batch_report.json"
    
    results_dir = evolve_dir / "tests" / "integration" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # Find scenarios to test
    if args.scenario:
        scenario_paths = [Path(s) for s in args.scenario]
    else:
        scenario_paths = find_scenarios(scenarios_dir)
    
    if not scenario_paths:
        print("No scenarios found to test", file=sys.stderr)
        sys.exit(1)
    
    print(f"Running {len(scenario_paths)} integration test(s)...")
    print()
    
    # Run tests
    results = []
    for i, scenario_path in enumerate(scenario_paths, 1):
        print(f"[{i}/{len(scenario_paths)}] Testing: {scenario_path.stem}")
        
        # Determine trajectory to use
        if args.trajectory:
            trajectory_path = Path(args.trajectory)
        else:
            # Load scenario to potentially match trajectory
            with open(scenario_path, 'r', encoding='utf-8') as f:
                scenario = json.load(f)
            trajectory_path = find_matching_trajectory(scenario, trajectories_dir)
        
        if not trajectory_path or not trajectory_path.exists():
            print(f"  ⚠️  No trajectory found, skipping")
            results.append({
                "error": "No trajectory found",
                "scenario_path": str(scenario_path),
                "passed": False
            })
            continue
        
        print(f"  Using trajectory: {trajectory_path.name}")
        
        # Run test
        result = run_single_test(scenario_path, trajectory_path, results_dir)
        results.append(result)
        
        # Print result
        if result.get("passed"):
            print(f"  ✅ PASSED")
        else:
            print(f"  ❌ FAILED")
            if "error" in result:
                print(f"     Error: {result['error']}")
        print()
    
    # Generate batch report
    print("Generating batch report...")
    report = generate_batch_report(results, output_path)
    
    # Print summary
    print("="*60)
    print("BATCH TEST SUMMARY")
    print("="*60)
    print(f"Total tests: {report['summary']['total_tests']}")
    print(f"Passed: {report['summary']['passed']}")
    print(f"Failed: {report['summary']['failed']}")
    print(f"Pass rate: {report['summary']['pass_rate']:.1%}")
    print()
    
    # Print per-scenario summary
    print("Results by scenario:")
    for scenario_id, scenario_result in report["by_scenario"].items():
        status = "✅ PASS" if scenario_result["passed"] else "❌ FAIL"
        print(f"  {status} {scenario_id}")
        if scenario_result["skills_recalled"]:
            print(f"       Skills recalled: Yes")
        else:
            print(f"       Skills recalled: No (this is why it failed)")
    print()
    
    print(f"Report saved to: {output_path}")
    
    # Exit with appropriate code
    sys.exit(0 if report['summary']['failed'] == 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob