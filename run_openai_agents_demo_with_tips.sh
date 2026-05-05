#!/bin/bash

# Script to run Evolve OpenAI Agents demo with LiteLLM proxy.
# Demonstrates Evolve's automatic tracing with multi-step agent interactions
# AND generates guidelines from the trajectory.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TIMESTAMP=$(date +%H%M%S)

echo "=========================================="
echo "Running Evolve OpenAI Agents Demo"
echo "with Automatic Guideline Generation"
echo "=========================================="
echo ""

cd "$SCRIPT_DIR"

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Loading environment variables from .env..."
source .env

export EVOLVE_AUTO_ENABLED=true
export EVOLVE_EXAMPLE_AGENT_MODEL="Azure/gpt-4o"

echo ""
echo "Configuration:"
echo "  - EVOLVE_AUTO_ENABLED: $EVOLVE_AUTO_ENABLED"
echo "  - Model: $EVOLVE_EXAMPLE_AGENT_MODEL"
echo "  - Base URL: $OPENAI_BASE_URL"
echo "  - Phoenix UI: http://localhost:6006"
echo "  - Namespace: ${EVOLVE_NAMESPACE_ID:-evolve}"
echo ""
echo "Running OpenAI Agents demo..."
echo "Task: What is (10 * 2) + 5?"
echo "Tools: add(), multiply()"
echo "=========================================="
echo ""

uv run python examples/low_code/openai_agents_demo.py

echo ""
echo "=========================================="
echo "Step 1: Extracting trajectory from Phoenix..."
echo "=========================================="
echo ""

TRAJECTORY_FILE="trajectory_openai_agents_${TIMESTAMP}.json"
python3 scripts/extract_trajectories.py --limit 1 -o "$TRAJECTORY_FILE" --pretty --project evolve-agent

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
echo "Step 2: Syncing trajectory to Evolve and generating guidelines..."
echo "=========================================="
echo ""

# Pass --consistency to use the resampling-based consistency guideline generator
# instead of the default generate_guidelines flow (requires the `consistency` extra).
uv run evolve sync phoenix \
  --project evolve-agent \
  --limit 1 \
  --namespace "${EVOLVE_NAMESPACE_ID:-evolve}"

echo ""
echo "=========================================="
echo "Step 3: Exporting generated guidelines to JSON..."
echo "=========================================="
echo ""

GUIDELINES_FILE="generated_guidelines_openai_agents_${TIMESTAMP}.json"
uv run python3 << EOF
import json
import os
import sys

from altk_evolve.frontend.client.evolve_client import EvolveClient

namespace_id = os.environ.get("EVOLVE_NAMESPACE_ID", "evolve")

try:
    client = EvolveClient()

    results = client.search_entities(
        namespace_id=namespace_id,
        query="",
        filters={"type": "guideline"},
        limit=100,
    )

    guidelines = [
        {
            "id": entity.id,
            "content": entity.content,
            "type": entity.type,
            "metadata": entity.metadata or {},
            "created_at": entity.created_at.isoformat() if entity.created_at else None,
        }
        for entity in results
    ]

    output_file = "${GUIDELINES_FILE}"
    with open(output_file, "w") as f:
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

echo "Guidelines in namespace '${EVOLVE_NAMESPACE_ID:-evolve}':"
uv run evolve entities list "${EVOLVE_NAMESPACE_ID:-evolve}" --type guideline

if [ -f "$GUIDELINES_FILE" ]; then
    echo ""
    echo "Guidelines JSON preview:"
    head -n 30 "$GUIDELINES_FILE"
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
echo "  - Guideline generation: ✅ Stored in Evolve"
echo "  - Guidelines export: ✅ Saved to $GUIDELINES_FILE"
echo ""
echo "Next steps:"
echo "  1. View traces at: http://localhost:6006"
echo "  2. View trajectory: cat $TRAJECTORY_FILE"
echo "  3. View generated guidelines: cat $GUIDELINES_FILE"
echo "  4. Search guidelines: uv run evolve entities search ${EVOLVE_NAMESPACE_ID:-evolve} 'your query'"
echo "  5. List all guidelines: uv run evolve entities list ${EVOLVE_NAMESPACE_ID:-evolve} --type guideline"
echo "=========================================="
