#!/usr/bin/env python3
"""
Show the execution plan for a skill - what steps would be extracted and executed.
This helps visualize how the functional test interprets skill content.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any
import argparse


def extract_steps_from_skill(content: str) -> List[Dict[str, Any]]:
    """
    Extract executable steps from skill content.
    This mimics the logic used in functional tests.
    """
    steps = []
    
    # Try to find numbered steps
    numbered_pattern = r'^\s*(\d+)[.)]\s+(.+?)(?=^\s*\d+[.)]|\Z)'
    matches = re.finditer(numbered_pattern, content, re.MULTILINE | re.DOTALL)
    
    for match in matches:
        step_num = int(match.group(1))
        step_content = match.group(2).strip()
        
        # Extract commands from this step
        commands = extract_commands(step_content)
        
        steps.append({
            'step_number': step_num,
            'description': step_content[:100] + ('...' if len(step_content) > 100 else ''),
            'full_content': step_content,
            'commands': commands,
            'has_commands': len(commands) > 0
        })
    
    # If no numbered steps found, treat entire content as one step
    if not steps:
        commands = extract_commands(content)
        steps.append({
            'step_number': 1,
            'description': content[:100] + ('...' if len(content) > 100 else ''),
            'full_content': content,
            'commands': commands,
            'has_commands': len(commands) > 0
        })
    
    return steps


def extract_commands(text: str) -> List[str]:
    """Extract commands from text (backticks or code blocks)."""
    commands = []
    
    # Extract from code blocks
    code_block_pattern = r'```(?:bash|sh|shell)?\s*\n(.*?)\n```'
    for match in re.finditer(code_block_pattern, text, re.DOTALL):
        command = match.group(1).strip()
        if command:
            commands.append(command)
    
    # Extract from backticks
    backtick_pattern = r'`([^`]+)`'
    for match in re.finditer(backtick_pattern, text):
        command = match.group(1).strip()
        # Only include if it looks like a command (has spaces or special chars)
        if ' ' in command or any(c in command for c in ['-', '/', '.']):
            commands.append(command)
    
    return commands


def load_skill(skill_path: Path) -> Dict[str, Any]:
    """Load skill content from markdown file."""
    content = skill_path.read_text()
    
    # Extract frontmatter
    frontmatter = {}
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            frontmatter_text = parts[1]
            for line in frontmatter_text.strip().split('\n'):
                if ':' in line:
                    key, value = line.split(':', 1)
                    frontmatter[key.strip()] = value.strip()
            content = parts[2].strip()
    
    return {
        'path': str(skill_path),
        'name': skill_path.stem,
        'type': frontmatter.get('type', 'unknown'),
        'content': content,
        'frontmatter': frontmatter
    }


def show_execution_plan(skill_path: Path, verbose: bool = False):
    """Show the execution plan for a skill."""
    skill = load_skill(skill_path)
    steps = extract_steps_from_skill(skill['content'])
    
    print(f"\n{'='*70}")
    print(f"EXECUTION PLAN: {skill['name']}")
    print(f"{'='*70}")
    print(f"Type: {skill['type']}")
    print(f"Path: {skill['path']}")
    print(f"\n{'─'*70}")
    print("SKILL CONTENT:")
    print(f"{'─'*70}")
    print(skill['content'][:500] + ('...' if len(skill['content']) > 500 else ''))
    
    print(f"\n{'─'*70}")
    print(f"EXTRACTED STEPS: {len(steps)}")
    print(f"{'─'*70}")
    
    total_commands = 0
    for step in steps:
        print(f"\n📋 Step {step['step_number']}")
        print(f"   Description: {step['description']}")
        print(f"   Commands found: {len(step['commands'])}")
        
        if step['commands']:
            for i, cmd in enumerate(step['commands'], 1):
                print(f"   └─ Command {i}: {cmd}")
                total_commands += 1
        else:
            print(f"   └─ ⚠️  No executable commands found")
        
        if verbose and step['full_content'] != step['description']:
            print(f"\n   Full content:")
            for line in step['full_content'].split('\n'):
                print(f"   │ {line}")
    
    print(f"\n{'─'*70}")
    print("EXECUTION SUMMARY:")
    print(f"{'─'*70}")
    print(f"Total steps: {len(steps)}")
    print(f"Total commands: {total_commands}")
    print(f"Steps with commands: {sum(1 for s in steps if s['has_commands'])}")
    print(f"Steps without commands: {sum(1 for s in steps if not s['has_commands'])}")
    
    # Determine if this would pass functional tests
    print(f"\n{'─'*70}")
    print("FUNCTIONAL TEST PREDICTION:")
    print(f"{'─'*70}")
    
    if total_commands == 0:
        print("❌ LIKELY TO FAIL: No executable commands found")
        print("   Reason: Skill content is too abstract or missing commands")
    elif len(steps) == 1 and total_commands > 1:
        print("⚠️  MAY FAIL: Multiple commands in single step")
        print("   Reason: Commands may not all be executed")
    elif total_commands > 0:
        print("✅ MAY PASS: Commands found and extractable")
        print("   Note: Actual pass depends on command execution success")
    
    print(f"\n{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Show execution plan for skills'
    )
    parser.add_argument(
        'skills',
        nargs='*',
        help='Skill file paths (if none provided, shows all skills)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show full step content'
    )
    parser.add_argument(
        '--type',
        choices=['atomic-skill', 'skill-flow', 'guideline'],
        help='Filter by skill type'
    )
    
    args = parser.parse_args()
    
    # Determine which skills to analyze
    if args.skills:
        skill_paths = [Path(s) for s in args.skills]
    else:
        # Find all skills
        evolve_dir = Path('.evolve/entities')
        skill_paths = []
        
        for skill_type in ['atomic-skill', 'skill-flow', 'guideline']:
            if args.type and skill_type != args.type:
                continue
            
            type_dir = evolve_dir / skill_type
            if type_dir.exists():
                skill_paths.extend(type_dir.glob('*.md'))
    
    if not skill_paths:
        print("No skills found!")
        return
    
    print(f"\nAnalyzing {len(skill_paths)} skill(s)...\n")
    
    for skill_path in sorted(skill_paths):
        if skill_path.exists():
            show_execution_plan(skill_path, args.verbose)
        else:
            print(f"⚠️  Skill not found: {skill_path}")


if __name__ == '__main__':
    main()

# Made with Bob
