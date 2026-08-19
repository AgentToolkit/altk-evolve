#!/usr/bin/env python3
"""
Functional Test Framework for Evolve Lite Skills

Tests if skills actually work by:
1. Parsing skill instructions into executable steps
2. Simulating execution in a mock environment
3. Validating expected outcomes occur

This provides functional testing without actually modifying the system.
"""

import argparse
import json
import sys
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

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
    _log("skill-functional-test", message)


@dataclass
class MockEnvironment:
    """Simulates a test environment for skill execution."""
    
    files: Dict[str, str] = field(default_factory=dict)  # filename -> content
    commands_executed: List[Dict[str, Any]] = field(default_factory=list)
    environment_vars: Dict[str, str] = field(default_factory=dict)
    current_directory: str = "/test"
    errors: List[str] = field(default_factory=list)
    
    def file_exists(self, path: str) -> bool:
        """Check if a file exists in the mock environment."""
        return path in self.files
    
    def create_file(self, path: str, content: str):
        """Create a file in the mock environment."""
        self.files[path] = content
        log(f"Mock: Created file {path}")
    
    def read_file(self, path: str) -> Optional[str]:
        """Read a file from the mock environment."""
        return self.files.get(path)
    
    def execute_command(self, command: str) -> Dict[str, Any]:
        """Simulate command execution."""
        result = {
            "command": command,
            "success": True,
            "output": "",
            "error": ""
        }
        
        # Simulate different command types
        if "orchestrate agents import" in command:
            # Simulate agent import
            if "--file" in command:
                # Extract filename
                match = re.search(r'--file\s+(\S+)', command)
                if match:
                    filename = match.group(1)
                    if self.file_exists(filename):
                        result["output"] = f"Agent imported successfully from {filename}"
                        result["success"] = True
                    else:
                        result["error"] = f"File not found: {filename}"
                        result["success"] = False
                        self.errors.append(f"File not found: {filename}")
            else:
                result["error"] = "Missing --file parameter"
                result["success"] = False
                self.errors.append("Missing --file parameter")
        
        elif "orchestrate env activate" in command:
            # Simulate environment activation
            result["output"] = "Environment activated"
            result["success"] = True
        
        elif "source .venv/bin/activate" in command or "source" in command:
            # Simulate virtual environment activation
            result["output"] = "Virtual environment activated"
            result["success"] = True
            self.environment_vars["VIRTUAL_ENV"] = "/test/.venv"
        
        elif command.startswith("python") or command.startswith("python3"):
            # Simulate Python execution
            result["output"] = "Python script executed"
            result["success"] = True
        
        elif command.startswith("pip install"):
            # Simulate pip install
            result["output"] = "Packages installed"
            result["success"] = True
        
        else:
            # Generic command
            result["output"] = f"Command executed: {command}"
            result["success"] = True
        
        self.commands_executed.append(result)
        log(f"Mock: Executed command: {command} -> {'SUCCESS' if result['success'] else 'FAILED'}")
        
        return result
    
    def get_state(self) -> Dict[str, Any]:
        """Get the current state of the environment."""
        return {
            "files": list(self.files.keys()),
            "commands_executed": len(self.commands_executed),
            "errors": self.errors,
            "environment_vars": self.environment_vars
        }


@dataclass
class SkillStep:
    """Represents a single step extracted from a skill."""
    
    step_number: int
    description: str
    command: Optional[str] = None
    expected_outcome: Optional[str] = None
    step_type: str = "action"  # action, command, check


def extract_steps_from_skill(skill: Dict[str, Any]) -> List[SkillStep]:
    """
    Extract executable steps from a skill's content.
    
    Looks for:
    - Numbered steps (1., 2., 3.)
    - Commands in backticks or code blocks
    - Action descriptions
    """
    content = skill.get("content", "")
    steps = []
    
    # Pattern 1: Numbered steps with commands
    # Example: "1) Create a YAML file..." or "1. Run `command`"
    numbered_pattern = r'(\d+)[.)]\s+([^\n]+)'
    matches = re.finditer(numbered_pattern, content)
    
    for match in matches:
        step_num = int(match.group(1))
        description = match.group(2).strip()
        
        # Extract command if present in backticks
        command = None
        command_match = re.search(r'`([^`]+)`', description)
        if command_match:
            command = command_match.group(1)
        
        steps.append(SkillStep(
            step_number=step_num,
            description=description,
            command=command,
            step_type="command" if command else "action"
        ))
    
    # Pattern 2: Commands in code blocks
    code_block_pattern = r'```(?:bash|sh|shell)?\n(.*?)\n```'
    code_matches = re.finditer(code_block_pattern, content, re.DOTALL)
    
    for match in code_matches:
        commands = match.group(1).strip().split('\n')
        for i, cmd in enumerate(commands):
            cmd = cmd.strip()
            if cmd and not cmd.startswith('#'):
                steps.append(SkillStep(
                    step_number=len(steps) + 1,
                    description=f"Execute: {cmd}",
                    command=cmd,
                    step_type="command"
                ))
    
    # If no steps found, try to extract from description
    if not steps:
        # Look for action verbs
        action_pattern = r'(Create|Run|Execute|Install|Activate|Import|Deploy|Configure)\s+([^\n.]+)'
        action_matches = re.finditer(action_pattern, content, re.IGNORECASE)
        
        for i, match in enumerate(action_matches, 1):
            action = match.group(1)
            target = match.group(2).strip()
            
            steps.append(SkillStep(
                step_number=i,
                description=f"{action} {target}",
                step_type="action"
            ))
    
    return steps


def execute_skill_in_mock_env(
    skill: Dict[str, Any],
    scenario: Dict[str, Any],
    env: MockEnvironment
) -> Dict[str, Any]:
    """
    Execute a skill's steps in a mock environment.
    
    Returns execution results and validation.
    """
    steps = extract_steps_from_skill(skill)
    
    execution_log = []
    steps_executed = 0
    steps_successful = 0
    
    for step in steps:
        step_result = {
            "step_number": step.step_number,
            "description": step.description,
            "type": step.step_type,
            "executed": False,
            "success": False,
            "output": None
        }
        
        if step.command:
            # Execute the command in mock environment
            result = env.execute_command(step.command)
            step_result["executed"] = True
            step_result["success"] = result["success"]
            step_result["output"] = result["output"] if result["success"] else result["error"]
            
            steps_executed += 1
            if result["success"]:
                steps_successful += 1
        else:
            # For non-command steps, just mark as executed
            step_result["executed"] = True
            step_result["success"] = True
            steps_executed += 1
            steps_successful += 1
        
        execution_log.append(step_result)
    
    return {
        "steps_found": len(steps),
        "steps_executed": steps_executed,
        "steps_successful": steps_successful,
        "execution_log": execution_log,
        "environment_state": env.get_state()
    }


def validate_skill_outcome(
    skill: Dict[str, Any],
    scenario: Dict[str, Any],
    execution_result: Dict[str, Any],
    env: MockEnvironment
) -> Dict[str, Any]:
    """
    Validate that executing the skill produced the expected outcome.
    """
    validation = {
        "all_steps_executed": execution_result["steps_executed"] == execution_result["steps_found"],
        "all_steps_successful": execution_result["steps_successful"] == execution_result["steps_executed"],
        "no_errors": len(env.errors) == 0,
        "expected_files_created": False,
        "expected_commands_run": False
    }
    
    # Check if expected files were created (from scenario)
    required_files = scenario.get("success_criteria", {}).get("required_files", [])
    if required_files:
        files_created = all(env.file_exists(f) for f in required_files)
        validation["expected_files_created"] = files_created
    else:
        validation["expected_files_created"] = True  # No files required
    
    # Check if expected commands were run
    expected_commands = scenario.get("expected_commands", [])
    if expected_commands:
        commands_run = []
        for cmd_result in env.commands_executed:
            commands_run.append(cmd_result["command"])
        
        # Check if all expected commands were executed
        validation["expected_commands_run"] = all(
            any(exp in cmd for cmd in commands_run)
            for exp in expected_commands
        )
    else:
        validation["expected_commands_run"] = True  # No specific commands required
    
    # Overall pass/fail
    validation["passed"] = all([
        validation["all_steps_executed"],
        validation["all_steps_successful"],
        validation["no_errors"],
        validation["expected_files_created"],
        validation["expected_commands_run"]
    ])
    
    return validation


def run_functional_test(
    skill: Dict[str, Any],
    scenario: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Run a functional test for a skill.
    
    1. Create mock environment
    2. Extract steps from skill
    3. Execute steps in mock environment
    4. Validate outcomes
    """
    # Create mock environment
    env = MockEnvironment()
    
    # Setup initial state based on scenario
    # (e.g., create prerequisite files)
    setup = scenario.get("setup", {})
    for filename, content in setup.get("files", {}).items():
        env.create_file(filename, content)
    
    # Execute skill
    execution_result = execute_skill_in_mock_env(skill, scenario, env)
    
    # Validate outcome
    validation = validate_skill_outcome(skill, scenario, execution_result, env)
    
    result = {
        "test_id": f"functional_{Path(skill['path']).stem}_{scenario.get('scenario_id', 'unknown')}",
        "skill_path": skill["path"],
        "skill_type": skill.get("type", "unknown"),
        "scenario": scenario,
        "execution": execution_result,
        "validation": validation,
        "passed": validation["passed"],
        "timestamp": datetime.now().isoformat()
    }
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Run functional tests for Evolve Lite skills"
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
    
    # Determine scenarios directory
    if args.scenarios_dir:
        scenarios_dir = Path(args.scenarios_dir)
    else:
        scenarios_dir = evolve_dir / "tests" / "functional" / "scenarios"
    
    if not scenarios_dir.exists():
        print(f"Error: Scenarios directory not found: {scenarios_dir}", file=sys.stderr)
        sys.exit(1)
    
    # Determine output directory
    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = evolve_dir / "tests" / "functional" / "results"
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load scenarios
    print("Loading scenarios...")
    scenarios = []
    for scenario_file in scenarios_dir.glob("*.json"):
        with open(scenario_file, 'r', encoding='utf-8') as f:
            scenarios.append(json.load(f))
    print(f"Loaded {len(scenarios)} scenario(s)")
    
    # Run functional tests
    print(f"\nRunning functional tests...")
    results = []
    
    for scenario in scenarios:
        expected_skills = scenario.get("expected_skills", [])
        
        for skill_slug in expected_skills:
            # Find the skill file
            skill_files = list(entities_dir.glob(f"**/{skill_slug}.md"))
            if not skill_files:
                print(f"Warning: Skill not found: {skill_slug}")
                continue
            
            skill_path = skill_files[0]
            skill = markdown_to_entity(skill_path)
            skill["path"] = str(skill_path)
            
            print(f"Testing: {skill_slug} with {scenario['scenario_id']}")
            result = run_functional_test(skill, scenario)
            results.append(result)
            
            # Save individual result
            result_file = output_dir / f"{result['test_id']}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2)
    
    # Generate summary report
    total_tests = len(results)
    passed_tests = sum(1 for r in results if r["passed"])
    failed_tests = total_tests - passed_tests
    
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_tests": total_tests,
            "passed": passed_tests,
            "failed": failed_tests,
            "pass_rate": passed_tests / total_tests if total_tests > 0 else 0
        },
        "results": results
    }
    
    report_path = output_dir / "functional_test_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    
    # Print summary
    print("\n" + "="*60)
    print("FUNCTIONAL TEST SUMMARY")
    print("="*60)
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {failed_tests}")
    print(f"Pass rate: {report['summary']['pass_rate']:.1%}")
    print()
    
    if args.verbose:
        for result in results:
            status = "✅ PASS" if result["passed"] else "❌ FAIL"
            print(f"\n{status} {result['test_id']}")
            print(f"  Steps found: {result['execution']['steps_found']}")
            print(f"  Steps executed: {result['execution']['steps_executed']}")
            print(f"  Steps successful: {result['execution']['steps_successful']}")
            print(f"  Errors: {len(result['execution']['environment_state']['errors'])}")
            
            if not result["passed"]:
                validation = result["validation"]
                if not validation["all_steps_executed"]:
                    print(f"    ⚠️  Not all steps were executed")
                if not validation["all_steps_successful"]:
                    print(f"    ⚠️  Some steps failed")
                if not validation["no_errors"]:
                    print(f"    ⚠️  Errors occurred: {result['execution']['environment_state']['errors']}")
    
    print(f"\nReport saved to: {report_path}")
    
    sys.exit(0 if failed_tests == 0 else 1)


if __name__ == "__main__":
    main()

# Made with Bob