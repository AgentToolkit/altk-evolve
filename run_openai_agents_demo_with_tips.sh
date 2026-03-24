#!/bin/bash

# Script to run kaizen OpenAI Agents demo with LiteLLM proxy
# This demonstrates kaizen's automatic tracing with multi-step agent interactions
# AND generates guidelines/tips from the trajectory

set -e  # Exit on error

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Generate timestamp for unique filenames (format: HHMMSS)
TIMESTAMP=$(date +%H%M%S)

echo "=========================================="
echo "Running Kaizen OpenAI Agents Demo"
echo "with Automatic Tip Generation"
echo "=========================================="
echo ""

# Change to kaizen directory
cd "$SCRIPT_DIR"

# Activate virtual environment
echo "Activating virtual environment..."
source .venv/bin/activate

# Load environment variables
echo "Loading environment variables from .env..."
source .env

# Set kaizen tracing and model
export KAIZEN_AUTO_ENABLED=true
export KAIZEN_EXAMPLE_AGENT_MODEL="Azure/gpt-4o"

echo ""
echo "Configuration:"
echo "  - KAIZEN_AUTO_ENABLED: $KAIZEN_AUTO_ENABLED"
echo "  - Model: $KAIZEN_EXAMPLE_AGENT_MODEL"
echo "  - Base URL: $OPENAI_BASE_URL"
echo "  - Phoenix UI: http://localhost:6006"
echo "  - Namespace: ${KAIZEN_NAMESPACE_ID:-kaizen}"
echo ""
echo "Running OpenAI Agents demo..."
echo "Task: What is (10 * 2) + 5?"
echo "Tools: add(), multiply()"
echo "=========================================="
echo ""

# Run the example
uv run python examples/low_code/openai_agents_demo.py

echo ""
echo "=========================================="
echo "Step 1: Extracting trajectory from Phoenix..."
echo "=========================================="
echo ""

# Extract the most recent trajectory (limit 1) with timestamped filename
TRAJECTORY_FILE="trajectory_openai_agents_${TIMESTAMP}.json"
python3 extract_trajectories.py --limit 1 -o "$TRAJECTORY_FILE" --pretty --project kaizen-agent

if [ -f "$TRAJECTORY_FILE" ]; then
    echo ""
    echo "✅ Trajectory extracted to: $TRAJECTORY_FILE"
    echo ""
    echo "Trajectory preview (first 50 lines):"
    head -n 50 "$TRAJECTORY_FILE"
    echo ""
    echo "..."
fi

echo ""
echo "=========================================="
echo "Step 2: Syncing trajectory to Kaizen and generating tips..."
echo "=========================================="
echo ""

# Sync from Phoenix to generate tips
# This will:
# 1. Fetch the trajectory from Phoenix
# 2. Generate guidelines/tips using LLM
# 3. Store them in the Kaizen namespace with conflict resolution
uv run python -m kaizen.cli.cli sync phoenix \
  --project kaizen-agent \
  --limit 1 \
  --namespace "${KAIZEN_NAMESPACE_ID:-kaizen}"

echo ""
echo "=========================================="
echo "Step 3: Exporting generated guidelines to JSON..."
echo "=========================================="
echo ""

# Export guidelines to JSON file using Python with timestamped filename
TIPS_FILE="generated_tips_openai_agents_${TIMESTAMP}.json"
uv run python3 << EOF
import json
import sys
import os

from kaizen.frontend.client.kaizen_client import KaizenClient

# Get namespace from environment or use default
namespace_id = os.environ.get("KAIZEN_NAMESPACE_ID", "kaizen")

try:
    # Initialize Kaizen client
    client = KaizenClient()
    
    # Search for all guideline entities
    results = client.backend.search_entities(
        namespace_id=namespace_id,
        query="",  # Empty query to get all
        filters={"type": "guideline"},
        limit=100
    )
    
    # Format guidelines for output
    guidelines = []
    for entity in results:
        guidelines.append({
            "id": entity.id,
            "content": entity.content,
            "type": entity.type,
            "metadata": entity.metadata or {},
            "created_at": entity.created_at.isoformat() if entity.created_at else None
        })
    
    # Write to file with timestamp
    output_file = "${TIPS_FILE}"
    with open(output_file, 'w') as f:
        json.dump(guidelines, f, indent=2)
    
    print(f"✅ Exported {len(guidelines)} guidelines to: {output_file}")
    
except Exception as e:
    print(f"❌ Error exporting guidelines: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
EOF

echo ""
echo "=========================================="
echo "Step 4: Viewing generated guidelines..."
echo "=========================================="
echo ""

# List the generated guidelines
echo "Guidelines in namespace '${KAIZEN_NAMESPACE_ID:-kaizen}':"
uv run kaizen entities list "${KAIZEN_NAMESPACE_ID:-kaizen}" --type guideline

if [ -f "$TIPS_FILE" ]; then
    echo ""
    echo "Guidelines JSON preview:"
    head -n 30 "$TIPS_FILE"
    echo ""
    echo "..."
fi

echo ""
echo "=========================================="
echo "✅ OpenAI Agents demo completed!"
echo "=========================================="
echo ""
echo "Summary:"
echo "  - Agent execution: ✅ Complete"
echo "  - Trajectory extraction: ✅ Saved to $TRAJECTORY_FILE"
echo "  - Tip generation: ✅ Guidelines stored in Kaizen"
echo "  - Tips export: ✅ Saved to $TIPS_FILE"
echo ""
echo "Next steps:"
echo "  1. View traces at: http://localhost:6006"
echo "  2. View trajectory: cat $TRAJECTORY_FILE"
echo "  3. View generated tips: cat $TIPS_FILE"
echo "  4. Search guidelines: uv run kaizen entities search ${KAIZEN_NAMESPACE_ID:-kaizen} 'your query'"
echo "  5. List all guidelines: uv run kaizen entities list ${KAIZEN_NAMESPACE_ID:-kaizen} --type guideline"
echo "=========================================="

# Made with Bob