#!/usr/bin/env python3
"""
Trajectory Extractor
Automatically extracts trajectories from Bob's task logs instead of requiring manual conversation copying.
"""

import json
import os
from datetime import datetime
from pathlib import Path


def get_bob_tasks_dir():
    """Get the Bob tasks directory path."""
    home = Path.home()
    return home / "Library" / "Application Support" / "IBM Bob" / "User" / "globalStorage" / "ibm.bob-code" / "tasks"


def get_latest_task_dir():
    """Get the most recently modified task directory from Bob's logs."""
    tasks_dir = get_bob_tasks_dir()
    
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Bob tasks directory not found: {tasks_dir}")
    
    # Get all task directories (UUIDs)
    task_dirs = [d for d in tasks_dir.iterdir() if d.is_dir()]
    
    if not task_dirs:
        raise FileNotFoundError("No task directories found in Bob's tasks directory")
    
    # Sort by modification time, most recent first
    task_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    
    return task_dirs[0]


def extract_trajectory_from_bob_log(task_dir_path=None):
    """Extract trajectory from Bob's task log.
    
    Args:
        task_dir_path: Optional path to specific task directory.
                      If None, uses most recent task.
    
    Returns:
        dict: Trajectory in standard format with messages array
    """
    if task_dir_path is None:
        task_dir_path = get_latest_task_dir()
    
    task_dir_path = Path(task_dir_path)
    
    # Read the API conversation history file
    api_history_file = task_dir_path / "api_conversation_history.json"
    if not api_history_file.exists():
        raise FileNotFoundError(f"API conversation history not found: {api_history_file}")
    
    with open(api_history_file, 'r', encoding='utf-8') as f:
        messages = json.load(f)
    
    # Read task metadata for additional context
    metadata_file = task_dir_path / "task_metadata.json"
    metadata = {}
    if metadata_file.exists():
        with open(metadata_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    
    # Messages are already in OpenAI chat completion format (it's a list)
    if not isinstance(messages, list):
        raise ValueError(f"Expected messages to be a list, got {type(messages)}")
    
    # Build trajectory envelope
    trajectory = {
        'model': metadata.get('model', 'unknown'),
        'session_id': task_dir_path.name,  # Use the UUID directory name
        'timestamp': datetime.fromtimestamp(task_dir_path.stat().st_mtime).isoformat(),
        'source': 'bob_task_log',
        'messages': messages,
        'metadata': {
            'task_dir': str(task_dir_path),
            'mode': metadata.get('mode'),
            'project_root': metadata.get('cwd'),
        }
    }
    
    return trajectory


def save_trajectory_from_bob(output_dir=None, task_dir_path=None):
    """Extract Bob task and save as trajectory.
    
    Args:
        output_dir: Directory to save trajectory. Defaults to .evolve/trajectories/
        task_dir_path: Optional specific task directory to extract. If None, uses latest.
    
    Returns:
        Path: Path to saved trajectory file
    """
    if output_dir is None:
        # Use EVOLVE_DIR if set, otherwise .evolve
        evolve_dir = os.environ.get("EVOLVE_DIR", ".evolve")
        output_dir = Path(evolve_dir) / 'trajectories'
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract trajectory from Bob
    trajectory = extract_trajectory_from_bob_log(task_dir_path)
    
    # Generate filename with session ID for provenance tracking
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    session_id = trajectory['session_id']
    
    # Sanitize session_id for filename
    safe_session_id = "".join(c if c.isalnum() or c in "._-" else "-" for c in session_id)[:64]
    
    filename = f"trajectory_{timestamp}_{safe_session_id}.json"
    output_path = output_dir / filename
    
    # Save trajectory
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(trajectory, f, indent=2)
    
    return output_path

# Made with Bob
