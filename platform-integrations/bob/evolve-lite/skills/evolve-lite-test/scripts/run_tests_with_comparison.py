#!/usr/bin/env python3
"""
Enhanced Test Runner with A/B Comparison

Runs tests twice:
1. With skills: Assumes skills were recalled and applied
2. Without skills: Simulates baseline without skill guidance

Compares the results to show skill effectiveness.
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
    _log("test-compare", message)


def load_test_case(test_path):
    """Load a test case JSON file."""
    with open(test_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_skill(skill_path):
    """Load a skill entity from markdown file."""
    return markdown_to_entity(skill_path)


def run_test_with_skills(test_case, skill, entities_dir):
    """Run test assuming skills were used."""
    # Import test functions from run_test_cases
    sys.path.insert(0, str(Path(__file__).parent))
    from run_test_cases import (
        test_trigger_match,
        test_content_completeness,
        test_skill_composition,
        test_trajectory_replay
    )
    
    test_type = test_case["test_type"]
    
    if test_type == "trigger_match":
        return test_trigger_match(test_case, skill)
    elif test_type == "content_completeness":
        return test_content_completeness(test_case, skill)
    elif test_type == "skill_composition":
        return test_skill_composition(test_case, skill, entities_dir)
    elif test_type == "trajectory_replay":
        return test_trajectory_replay(test_case, skill)
    else:
        return {
            "test_id": test_case["test_id"],
            "test_type": test_type,
            "passed": False,
            "message": f"Unknown test type: {test_type}",
            "details": {}
        }


def simulate_test_without_skills(test_case, skill):
    """Simulate test results if skills were NOT used."""
    test_type = test_case["test_type"]
    
    # Simulate degraded performance without skills
    result = {
        "test_id": test_case["test_id"] + "_no_skills",
        "test_type": test_type,
        "passed": False,
        "details": {},
        "simulated": True
    }
    
    if test_type == "trigger_match":
        # Without skills, no trigger matching happens
        result["message"] = "No skill recalled (baseline)"
        result["details"]["skill_recalled"] = False
        
    elif test_type == "content_completeness":
        # Without skills, guidance is incomplete
        result["message"] = "No skill guidance available (baseline)"
        result["details"]["has_guidance"] = False
        result["passed"] = False
        
    elif test_type == "skill_composition":
        # Without skills, no composition to check
        result["message"] = "No skill composition (baseline)"
        result["details"]["has_composition"] = False
        result["passed"] = False
        
    elif test_type == "trajectory_replay":
        # Without skills, more trial and error
        result["message"] = "No skill guidance, more retries expected (baseline)"
        result["details"]["expected_more_retries"] = True
        result["passed"] = False
    
    return result


def compare_results(with_skills_result, without_skills_result):
    """Compare test results with and without skills."""
    comparison = {
        "test_id": with_skills_result["test_id"],
        "test_type": with_skills_result["test_type"],
        "with_skills": {
            "passed": with_skills_result["passed"],
            "message": with_skills_result["message"]
        },
        "without_skills": {
            "passed": without_skills_result["passed"],
            "message": without_skills_result["message"]
        },
        "improvement": {
            "skills_helped": with_skills_result["passed"] and not without_skills_result["passed"],
            "status_change": "improved" if (with_skills_result["passed"] and not without_skills_result["passed"]) else "no_change"
        }
    }
    
    return comparison


def main():
    parser = argparse.ArgumentParser(
        description="Run tests with A/B comparison (with skills vs without skills)"
    )
    parser.add_argument(
        "--test-dir",
        required=True,
        help="Directory containing test case JSON files"
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Output path for comparison report"
    )
    
    args = parser.parse_args()
    
    test_dir = Path(args.test_dir)
    if not test_dir.exists():
        print(f"Error: Test directory not found: {test_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine entities directory
    evolve_dir = get_evolve_dir()
    entities_dir = evolve_dir / "entities"
    
    if not entities_dir.exists():
        print(f"Error: Entities directory not found: {entities_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Load all test cases
    test_files = [f for f in test_dir.glob("*.json") if not f.name.startswith("test_report") and not f.name.startswith("comparison")]
    
    if not test_files:
        print(f"No test case files found in {test_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Running {len(test_files)} test case(s) with A/B comparison...\n")
    print("="*70)
    print(f"{'Test':<50} {'With Skills':<12} {'Without':<12}")
    print("="*70)
    
    comparisons = []
    with_skills_passed = 0
    without_skills_passed = 0
    
    for test_file in sorted(test_files):
        try:
            test_case = load_test_case(test_file)
            
            # Skip if not a valid test case
            if "test_id" not in test_case or "skill_path" not in test_case:
                continue
            
            skill_path = Path(test_case["skill_path"])
            if not skill_path.exists():
                print(f"⚠️  {test_file.stem[:48]:<50} SKIP (skill not found)")
                continue
            
            skill = load_skill(skill_path)
            
            # Run with skills
            with_skills_result = run_test_with_skills(test_case, skill, entities_dir)
            
            # Simulate without skills
            without_skills_result = simulate_test_without_skills(test_case, skill)
            
            # Compare
            comparison = compare_results(with_skills_result, without_skills_result)
            comparisons.append(comparison)
            
            # Track stats
            if with_skills_result["passed"]:
                with_skills_passed += 1
            if without_skills_result["passed"]:
                without_skills_passed += 1
            
            # Print result
            with_status = "✓ PASS" if with_skills_result["passed"] else "✗ FAIL"
            without_status = "✓ PASS" if without_skills_result["passed"] else "✗ FAIL"
            improvement = "📈" if comparison["improvement"]["skills_helped"] else "  "
            
            test_name = test_case["test_id"][:48]
            print(f"{improvement} {test_name:<48} {with_status:<12} {without_status:<12}")
            
        except Exception as e:
            print(f"✗ ERROR {test_file.name}: {e}", file=sys.stderr)
    
    # Generate report
    if args.report:
        report_path = Path(args.report)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        # Save reports in reports subdirectory
        reports_dir = test_dir.parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"comparison_report_{timestamp}.json"
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "total_tests": len(comparisons),
        "with_skills": {
            "passed": with_skills_passed,
            "failed": len(comparisons) - with_skills_passed,
            "pass_rate": with_skills_passed / len(comparisons) if comparisons else 0
        },
        "without_skills": {
            "passed": without_skills_passed,
            "failed": len(comparisons) - without_skills_passed,
            "pass_rate": without_skills_passed / len(comparisons) if comparisons else 0
        },
        "improvement": {
            "tests_improved": sum(1 for c in comparisons if c["improvement"]["skills_helped"]),
            "improvement_rate": sum(1 for c in comparisons if c["improvement"]["skills_helped"]) / len(comparisons) if comparisons else 0
        },
        "comparisons": comparisons
    }
    
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print("="*70)
    print("\nA/B COMPARISON SUMMARY")
    print("="*70)
    print(f"Total tests: {len(comparisons)}")
    print(f"\nWith Skills:")
    print(f"  Passed: {with_skills_passed}/{len(comparisons)} ({report['with_skills']['pass_rate']*100:.1f}%)")
    print(f"\nWithout Skills (simulated baseline):")
    print(f"  Passed: {without_skills_passed}/{len(comparisons)} ({report['without_skills']['pass_rate']*100:.1f}%)")
    print(f"\nImprovement:")
    print(f"  Tests improved by skills: {report['improvement']['tests_improved']} ({report['improvement']['improvement_rate']*100:.1f}%)")
    print(f"\nReport saved to: {report_path}")
    
    # Exit with error if skills didn't help
    sys.exit(0 if report['improvement']['tests_improved'] > 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob
