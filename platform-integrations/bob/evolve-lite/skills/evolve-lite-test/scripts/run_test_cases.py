#!/usr/bin/env python3
"""
Test Case Runner for Evolve Lite Skills

Runs test cases against skills to validate they are discoverable, actionable,
complete, and compose correctly. Generates a test report with pass/fail results.
"""

import argparse
import json
import re
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
    _log("test-run", message)


def load_test_case(test_path):
    """Load a test case JSON file."""
    with open(test_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_skill(skill_path):
    """Load a skill entity from markdown file."""
    return markdown_to_entity(skill_path)


def normalize_text(text):
    """Normalize text for comparison."""
    return " ".join(text.lower().split())


def test_trigger_match(test_case, skill):
    """Test if skill trigger matches the scenario."""
    results = {
        "test_id": test_case["test_id"],
        "test_type": "trigger_match",
        "passed": False,
        "details": {}
    }
    
    trigger = skill.get("trigger", "").lower()
    user_request = test_case["input_context"]["user_request"].lower()
    
    # Simple keyword matching - check if key terms from trigger appear in request
    trigger_keywords = set(re.findall(r'\b\w+\b', trigger))
    # Remove common words
    common_words = {"when", "the", "a", "an", "to", "for", "with", "in", "on", "at", "by"}
    trigger_keywords = trigger_keywords - common_words
    
    request_keywords = set(re.findall(r'\b\w+\b', user_request))
    
    # Calculate match score
    if trigger_keywords:
        matches = trigger_keywords & request_keywords
        match_score = len(matches) / len(trigger_keywords)
    else:
        match_score = 0.0
    
    results["details"]["trigger"] = skill.get("trigger", "")
    results["details"]["user_request"] = test_case["input_context"]["user_request"][:100]
    results["details"]["match_score"] = match_score
    results["details"]["matched_keywords"] = list(matches) if trigger_keywords else []
    
    # Pass if at least 30% of trigger keywords match
    results["passed"] = match_score >= 0.3
    
    if results["passed"]:
        results["message"] = f"Trigger matches scenario (score: {match_score:.2f})"
    else:
        results["message"] = f"Trigger does not match scenario (score: {match_score:.2f})"
    
    return results


def test_content_completeness(test_case, skill):
    """Test if skill content is complete and actionable."""
    results = {
        "test_id": test_case["test_id"],
        "test_type": "content_completeness",
        "passed": False,
        "details": {}
    }
    
    content = skill.get("content", "")
    rationale = skill.get("rationale", "")
    
    checks = {
        "has_content": len(content) > 20,
        "has_rationale": len(rationale) > 10,
        "has_steps": bool(re.search(r'\d+\)', content)) or bool(re.search(r'[.;]\s*[A-Z]', content)),
        "has_commands": bool(re.search(r'`[^`]+`', content)) or bool(re.search(r'orchestrate|python|pip|npm', content, re.IGNORECASE)),
        "content_length_adequate": len(content) > 50
    }
    
    results["details"]["checks"] = checks
    results["details"]["content_length"] = len(content)
    results["details"]["has_rationale"] = bool(rationale)
    
    # Pass if most checks pass
    passed_checks = sum(checks.values())
    total_checks = len(checks)
    pass_rate = passed_checks / total_checks
    
    results["passed"] = pass_rate >= 0.6
    results["details"]["pass_rate"] = pass_rate
    
    if results["passed"]:
        results["message"] = f"Content is complete ({passed_checks}/{total_checks} checks passed)"
    else:
        results["message"] = f"Content may be incomplete ({passed_checks}/{total_checks} checks passed)"
    
    return results


def test_skill_composition(test_case, skill, entities_dir):
    """Test if skill-flow properly references atomic skills."""
    results = {
        "test_id": test_case["test_id"],
        "test_type": "skill_composition",
        "passed": False,
        "details": {}
    }
    
    if skill.get("type") != "skill-flow":
        results["passed"] = True
        results["message"] = "Not a skill-flow, composition test not applicable"
        return results
    
    atomic_skills = skill.get("atomic_skills", "")
    if not atomic_skills:
        results["passed"] = False
        results["message"] = "Skill-flow has no atomic_skills references"
        results["details"]["has_references"] = False
        return results
    
    atomic_skill_list = [s.strip() for s in atomic_skills.split(",")]
    
    # Check if atomic skills exist
    existing = []
    missing = []
    
    for atomic_skill_slug in atomic_skill_list:
        atomic_skill_path = Path(entities_dir) / "atomic-skill" / f"{atomic_skill_slug}.md"
        if atomic_skill_path.exists():
            existing.append(atomic_skill_slug)
        else:
            missing.append(atomic_skill_slug)
    
    results["details"]["atomic_skills_referenced"] = atomic_skill_list
    results["details"]["atomic_skills_existing"] = existing
    results["details"]["atomic_skills_missing"] = missing
    results["details"]["all_exist"] = len(missing) == 0
    
    results["passed"] = len(missing) == 0
    
    if results["passed"]:
        results["message"] = f"All {len(existing)} atomic skills exist"
    else:
        results["message"] = f"Missing {len(missing)} atomic skill(s): {', '.join(missing)}"
    
    return results


def test_trajectory_replay(test_case, skill):
    """Test if skill would work when replaying the trajectory."""
    results = {
        "test_id": test_case["test_id"],
        "test_type": "trajectory_replay",
        "passed": False,
        "details": {}
    }
    
    # This is a heuristic test - check if skill content relates to tools used
    content = skill.get("content", "").lower()
    tools_used = test_case["input_context"].get("tools_used", [])
    
    # Check if skill mentions relevant tools or actions
    tool_mentions = 0
    for tool in tools_used:
        if tool.lower() in content:
            tool_mentions += 1
    
    results["details"]["tools_used"] = tools_used
    results["details"]["tools_mentioned"] = tool_mentions
    
    # Also check if skill type matches the trajectory context
    skill_type = skill.get("type", "")
    user_request = test_case["input_context"]["user_request"].lower()
    
    type_appropriate = True
    if skill_type == "guideline":
        # Guidelines should be simple preferences
        type_appropriate = len(skill.get("content", "")) < 200
    elif skill_type == "skill-flow":
        # Skill-flows should have multiple steps
        type_appropriate = bool(re.search(r'\d+\)', skill.get("content", "")))
    
    results["details"]["type_appropriate"] = type_appropriate
    
    # Pass if tools are mentioned or type is appropriate
    results["passed"] = (tool_mentions > 0 or type_appropriate)
    
    if results["passed"]:
        results["message"] = "Skill appears applicable to trajectory"
    else:
        results["message"] = "Skill may not apply well to trajectory"
    
    return results


def run_test_case(test_case, entities_dir):
    """Run a single test case."""
    log(f"Running test: {test_case['test_id']}")
    
    skill_path = Path(test_case["skill_path"])
    if not skill_path.exists():
        return {
            "test_id": test_case["test_id"],
            "test_type": test_case["test_type"],
            "passed": False,
            "message": f"Skill file not found: {skill_path}",
            "details": {}
        }
    
    try:
        skill = load_skill(skill_path)
    except Exception as e:
        return {
            "test_id": test_case["test_id"],
            "test_type": test_case["test_type"],
            "passed": False,
            "message": f"Error loading skill: {e}",
            "details": {}
        }
    
    # Run appropriate test based on test type
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


def generate_report(test_results, output_path):
    """Generate a test report."""
    total_tests = len(test_results)
    passed_tests = sum(1 for r in test_results if r["passed"])
    failed_tests = total_tests - passed_tests
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "pass_rate": passed_tests / total_tests if total_tests > 0 else 0.0
        },
        "results": test_results,
        "by_test_type": {}
    }
    
    # Group by test type
    for result in test_results:
        test_type = result["test_type"]
        if test_type not in report["by_test_type"]:
            report["by_test_type"][test_type] = {
                "total": 0,
                "passed": 0,
                "failed": 0
            }
        report["by_test_type"][test_type]["total"] += 1
        if result["passed"]:
            report["by_test_type"][test_type]["passed"] += 1
        else:
            report["by_test_type"][test_type]["failed"] += 1
    
    # Save report
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Run test cases for skills"
    )
    parser.add_argument(
        "--test-dir",
        required=True,
        help="Directory containing test case JSON files"
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Output path for test report (default: test_report.json in test dir)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed results"
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
    test_files = list(test_dir.glob("*.json"))
    if not test_files:
        print(f"No test case files found in {test_dir}", file=sys.stderr)
        sys.exit(1)
    
    print(f"Running {len(test_files)} test case(s)...\n")
    
    test_results = []
    
    for test_file in test_files:
        try:
            test_case = load_test_case(test_file)
            result = run_test_case(test_case, entities_dir)
            test_results.append(result)
            
            status = "✓ PASS" if result["passed"] else "✗ FAIL"
            print(f"{status} {result['test_id']}")
            if args.verbose or not result["passed"]:
                print(f"     {result['message']}")
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
        report_path = reports_dir / f"test_report_{timestamp}.json"
    
    report = generate_report(test_results, report_path)
    
    # Print summary
    print(f"\n{'='*60}")
    print("TEST SUMMARY")
    print(f"{'='*60}")
    print(f"Total tests:  {report['summary']['total_tests']}")
    print(f"Passed:       {report['summary']['passed']} ({report['summary']['pass_rate']*100:.1f}%)")
    print(f"Failed:       {report['summary']['failed']}")
    print(f"\nReport saved to: {report_path}")
    
    # Exit with error code if any tests failed
    sys.exit(0 if report['summary']['failed'] == 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob
