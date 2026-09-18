#!/usr/bin/env python3
"""
Unit Test Framework for Evolve Lite Skills

Tests individual skills in isolation:
1. Mock trigger matching - simulates which skills would be recalled
2. Skill content validation - verifies skills provide correct guidance
3. Unit test style - one test per skill/scenario combination
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Set
import re

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
    _log("skill-unit-test", message)


def extract_keywords(text: str) -> Set[str]:
    """Extract meaningful keywords from text."""
    # Remove common words
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 
                  'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'be',
                  'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                  'would', 'should', 'could', 'may', 'might', 'must', 'can', 'this',
                  'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}
    
    # Extract words (lowercase, alphanumeric)
    words = re.findall(r'\b[a-z0-9]+\b', text.lower())
    
    # Filter out stop words and short words
    keywords = {w for w in words if w not in stop_words and len(w) > 2}
    
    return keywords


def mock_trigger_match(skill_trigger: str, user_request: str, threshold: float = 0.3) -> Dict[str, Any]:
    """
    Mock trigger matching algorithm.
    Simulates how Bob might match skill triggers to user requests.
    
    Returns match score and matched keywords.
    """
    trigger_keywords = extract_keywords(skill_trigger)
    request_keywords = extract_keywords(user_request)
    
    # Find overlapping keywords
    matched_keywords = trigger_keywords & request_keywords
    
    # Calculate match score (Jaccard similarity)
    if not trigger_keywords:
        match_score = 0.0
    else:
        match_score = len(matched_keywords) / len(trigger_keywords)
    
    return {
        "matched": match_score >= threshold,
        "match_score": match_score,
        "matched_keywords": list(matched_keywords),
        "trigger_keywords": list(trigger_keywords),
        "request_keywords": list(request_keywords)
    }


def validate_skill_content(skill: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate that a skill's content provides appropriate guidance for the scenario.
    """
    content = skill.get("content", "")
    skill_type = skill.get("type", "unknown")
    
    validation = {
        "has_content": bool(content and len(content) > 20),
        "has_rationale": "## Rationale" in content or "## rationale" in content.lower(),
        "has_steps": any(marker in content for marker in ["1.", "2.", "3.", "- ", "* "]),
        "has_commands": "`" in content or "```" in content,
        "content_length": len(content),
        "addresses_scenario": False
    }
    
    # Check if content addresses the scenario
    scenario_keywords = extract_keywords(scenario.get("user_request", ""))
    content_keywords = extract_keywords(content)
    overlap = scenario_keywords & content_keywords
    validation["addresses_scenario"] = len(overlap) > 0
    validation["scenario_keyword_overlap"] = list(overlap)
    
    # Type-specific validation
    if skill_type == "skill-flow":
        validation["has_atomic_skills"] = bool(skill.get("atomic_skills"))
        if validation["has_atomic_skills"]:
            validation["atomic_skills_list"] = [s.strip() for s in skill.get("atomic_skills", "").split(",")]
    
    # Calculate overall score
    checks = [
        validation["has_content"],
        validation["has_rationale"],
        validation["has_steps"] or validation["has_commands"],
        validation["addresses_scenario"]
    ]
    validation["completeness_score"] = sum(checks) / len(checks)
    validation["is_complete"] = validation["completeness_score"] >= 0.75
    
    return validation


def load_skill(skill_path: Path) -> Dict[str, Any]:
    """Load a skill from a markdown file."""
    return markdown_to_entity(skill_path)


def load_all_skills(entities_dir: Path) -> List[Dict[str, Any]]:
    """Load all skills from the entities directory."""
    skills = []
    
    for md_file in entities_dir.glob("**/*.md"):
        if md_file.is_symlink() or ".git" in md_file.parts:
            continue
        
        try:
            skill = load_skill(md_file)
            skill["path"] = str(md_file)
            skills.append(skill)
        except Exception as e:
            log(f"Error loading {md_file}: {e}")
    
    return skills


def run_unit_test(skill: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a unit test for a skill against a scenario.
    
    Tests:
    1. Trigger matching - would this skill be recalled?
    2. Content validation - does the skill provide appropriate guidance?
    """
    user_request = scenario.get("user_request", "")
    skill_trigger = skill.get("trigger", "")
    skill_path = skill.get("path", "unknown")
    skill_type = skill.get("type", "unknown")
    
    # Test 1: Mock trigger matching
    trigger_match = mock_trigger_match(skill_trigger, user_request)
    
    # Test 2: Content validation
    content_validation = validate_skill_content(skill, scenario)
    
    # Determine if test passes
    passed = trigger_match["matched"] and content_validation["is_complete"]
    
    result = {
        "test_id": f"unit_{Path(skill_path).stem}_{scenario.get('scenario_id', 'unknown')}",
        "skill_path": skill_path,
        "skill_type": skill_type,
        "skill_trigger": skill_trigger,
        "scenario": scenario,
        "trigger_match": trigger_match,
        "content_validation": content_validation,
        "passed": passed,
        "timestamp": datetime.now().isoformat()
    }
    
    return result


def run_all_unit_tests(
    skills: List[Dict[str, Any]],
    scenarios: List[Dict[str, Any]],
    output_dir: Path
) -> List[Dict[str, Any]]:
    """Run unit tests for all skill/scenario combinations."""
    results = []
    
    for scenario in scenarios:
        log(f"Testing scenario: {scenario.get('scenario_id')}")
        
        # Find skills that should match this scenario
        expected_skills = scenario.get("expected_skills", [])
        
        for skill in skills:
            skill_slug = Path(skill["path"]).stem
            
            # Only test if this skill is expected for this scenario
            if expected_skills and skill_slug not in expected_skills:
                continue
            
            result = run_unit_test(skill, scenario)
            results.append(result)
            
            # Save individual result
            result_file = output_dir / f"{result['test_id']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
    
    return results


def generate_unit_test_report(results: List[Dict[str, Any]], output_path: Path):
    """Generate a summary report for all unit tests."""
    total_tests = len(results)
    passed_tests = sum(1 for r in results if r["passed"])
    failed_tests = total_tests - passed_tests
    
    # Group by failure reason
    trigger_failures = sum(1 for r in results if not r["trigger_match"]["matched"])
    content_failures = sum(1 for r in results if not r["content_validation"]["is_complete"])
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "pass_rate": passed_tests / total_tests if total_tests > 0 else 0
        },
        "failure_analysis": {
            "trigger_match_failures": trigger_failures,
            "content_validation_failures": content_failures
        },
        "results": results,
        "by_skill": {}
    }
    
    # Group by skill
    for result in results:
        skill_path = result["skill_path"]
        if skill_path not in report["by_skill"]:
            report["by_skill"][skill_path] = {
                "total_tests": 0,
                "passed": 0,
                "failed": 0
            }
        
        report["by_skill"][skill_path]["total_tests"] += 1
        if result["passed"]:
            report["by_skill"][skill_path]["passed"] += 1
        else:
            report["by_skill"][skill_path]["failed"] += 1
    
    # Save report
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Run unit tests for Evolve Lite skills"
    )
    parser.add_argument(
        "--scenarios-dir",
        default=None,
        help="Directory containing scenario JSON files"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for test results"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed output"
    )
    
    args = parser.parse_args()
    
    evolve_dir = get_evolve_dir()
    entities_dir = evolve_dir / "entities"
    
    if not entities_dir.exists():
        print(f"Error: Entities directory not found: {entities_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine scenarios directory
    if args.scenarios_dir:
        scenarios_dir = Path(args.scenarios_dir)
    else:
        scenarios_dir = evolve_dir / "tests" / "integration" / "scenarios"
    
    if not scenarios_dir.exists():
        print(f"Error: Scenarios directory not found: {scenarios_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = evolve_dir / "tests" / "unit" / "results"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load skills
    print("Loading skills...")
    skills = load_all_skills(entities_dir)
    print(f"Loaded {len(skills)} skill(s)")
    
    # Load scenarios
    print("Loading scenarios...")
    scenarios = []
    for scenario_file in scenarios_dir.glob("*.json"):
        with open(scenario_file, 'r', encoding='utf-8') as f:
            scenarios.append(json.load(f))
    print(f"Loaded {len(scenarios)} scenario(s)")
    
    # Run unit tests
    print(f"\nRunning unit tests...")
    results = run_all_unit_tests(skills, scenarios, output_dir)
    
    # Generate report
    report_path = output_dir / "unit_test_report.json"
    report = generate_unit_test_report(results, report_path)
    
    # Print summary
    print("\n" + "="*60)
    print("UNIT TEST SUMMARY")
    print("="*60)
    print(f"Total tests: {report['summary']['total_tests']}")
    print(f"Passed: {report['summary']['passed']}")
    print(f"Failed: {report['summary']['failed']}")
    print(f"Pass rate: {report['summary']['pass_rate']:.1%}")
    print()
    
    print("Failure Analysis:")
    print(f"  Trigger match failures: {report['failure_analysis']['trigger_match_failures']}")
    print(f"  Content validation failures: {report['failure_analysis']['content_validation_failures']}")
    print()
    
    if args.verbose:
        print("Detailed Results:")
        for result in results:
            status = "✅ PASS" if result["passed"] else "❌ FAIL"
            print(f"\n{status} {result['test_id']}")
            print(f"  Skill: {Path(result['skill_path']).name}")
            print(f"  Trigger match: {result['trigger_match']['matched']} (score: {result['trigger_match']['match_score']:.2f})")
            print(f"  Content complete: {result['content_validation']['is_complete']} (score: {result['content_validation']['completeness_score']:.2f})")
            if not result["passed"]:
                if not result["trigger_match"]["matched"]:
                    print(f"    ⚠️  Trigger didn't match (matched keywords: {result['trigger_match']['matched_keywords']})")
                if not result["content_validation"]["is_complete"]:
                    print(f"    ⚠️  Content incomplete (missing: rationale={not result['content_validation']['has_rationale']}, steps={not result['content_validation']['has_steps']})")
    
    print(f"\nReport saved to: {report_path}")
    
    sys.exit(0 if report['summary']['failed'] == 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob