import subprocess
import time
import re
import os
import datetime
import pytest
from kaizen.config.phoenix import phoenix_settings

# Configuration
PHOENIX_URL = phoenix_settings.url
# Use a session-scope timestamp or generate per test? 
# Per-test ensures no collisions even if run in parallel (though these should satisfy sequential)
TIMESTAMP = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

AGENTS_TO_TEST = [
    {
        "name": "smolagents",
        "script": "examples/low_code/smolagents_demo.py",
        "project_prefix": "verify-smolagents"
    },
    {
        "name": "openai_agents", 
        "script": "examples/low_code/openai_agents_demo.py",
        "project_prefix": "verify-openai"
    },
    {
        "name": "manual_phoenix",
        "script": "examples/low_code/manual_phoenix_demo.py",
        "project_prefix": "verify-manual"
    },
    {
        "name": "simple_openai",
        "script": "examples/low_code/simple_openai.py",
        "project_prefix": "verify-simple-openai"
    }
]

@pytest.mark.skipif(os.getenv("KAIZEN_E2E") != "true", reason="E2E tests disabled unless KAIZEN_E2E=true")
@pytest.mark.parametrize("agent_config", AGENTS_TO_TEST, ids=[a["name"] for a in AGENTS_TO_TEST])
def test_e2e_pipeline_agent(agent_config):
    """
    Runs the full E2E pipeline for a specific agent configuration:
    1. Executing the agent script
    2. Verifying traces in Phoenix associated with a unique project name
    3. Running Kaizen Sync to verify tip generation
    """
    agent_name = agent_config["name"]
    script_path = agent_config["script"]
    
    # Generate unique project name for this run
    # Using a fresh timestamp per run to avoid collisions if tests run slowly
    current_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"{agent_config['project_prefix']}-{current_timestamp}"
    
    print("\n==================================================")
    print(f" TESTING AGENT: {agent_name}")
    print(f" Script: {script_path}")
    print(f" Project: {project_name}")
    print("==================================================")

    # --- Step 1: Run Agent ---
    print("\n--- Step 1: Running Agent ---")
    start_time = time.time()
    env = os.environ.copy()
    env["KAIZEN_AUTO_ENABLED"] = "true"
    # Important: Set project name for auto-instrumentation
    # kaizen.auto prioritizes KAIZEN_TRACING_PROJECT over PHOENIX_PROJECT_NAME
    env["KAIZEN_TRACING_PROJECT"] = project_name
    env["PHOENIX_PROJECT_NAME"] = project_name
    
    # Ensure script exists
    if not os.path.exists(script_path):
        pytest.fail(f"Script not found: {script_path}")

    result = subprocess.run(
        ["uv", "run", "python", script_path],
        env=env,
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print(f"❌ Agent failed with exit code {result.returncode}")
        print("STDERR:", result.stderr)
        print("STDOUT:", result.stdout)
        pytest.fail(f"Agent execution failed: {result.stderr}")
    
    print(f"✅ Agent finished in {time.time() - start_time:.2f}s")

    # --- Step 2: Verify Traces ---
    print(f"\n--- Step 2: Verifying Phoenix Traces ({project_name}) ---")
    
    # Wait briefly for traces to be flushed/indexed
    time.sleep(2) 
    
    check_script = f"""
import phoenix as px
import sys
try:
    c = px.Client(endpoint='{PHOENIX_URL}')
    df = c.get_spans_dataframe(project_name='{project_name}')
    if df is not None and not df.empty:
        print(f"FOUND_TRACES:{{len(df)}}")
    else:
        print("NO_TRACES")
except Exception as e:
    print(f"ERROR:{{e}}")
"""
    result = subprocess.run(
        ["uv", "run", "python", "-c", check_script],
        capture_output=True,
        text=True
    )
    
    output = result.stdout + result.stderr
    if "FOUND_TRACES" in output:
        count = output.split("FOUND_TRACES:")[1].split()[0]
        print(f"✅ Found {count} traces in project '{project_name}'")
    else:
        print(f"❌ No traces found in project '{project_name}'")
        print(f"Debug Output: {output}")
        pytest.fail(f"No traces found in Phoenix project {project_name}. Debug: {output}")

    # --- Step 3: Sync & Generate Tips ---
    print("\n--- Step 3: Running Kaizen Sync & Monitoring ---")
    sync_command = [
        "uv", "run", "python", "-m", "kaizen.frontend.cli.cli", 
        "sync", "phoenix", 
        "--project", project_name,
        "--include-errors",
        "--limit", "500"
    ]
    print(f"Command: {' '.join(sync_command)}")
    
    process = subprocess.Popen(
        sync_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, # Merge stderr to monitor everything
        text=True,
        bufsize=1,
        universal_newlines=True
    )
    
    tips_found = False
    sync_start = time.time()
    timeout = 120 # 2 minute timeout for sync
    
    try:
        while True:
            if time.time() - sync_start > timeout:
                print("❌ Timeout waiting for tips generation")
                break
                
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            
            if line:
                line_stripped = line.strip()
                # print(f"[Sync] {line_stripped}") # Optional: verbose logging
                
                # Check target log pattern
                match = re.search(r"generated (\d+) tips", line_stripped)
                if match:
                    count = match.group(1)
                    print(f"\n✅ SUCCESS: Generated {count} tips!")
                    tips_found = True
                    break
    finally:
        if process.poll() is None:
            print("Stopping sync process...")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    if not tips_found:
        pytest.fail(f"Failed to detect tip generation for {agent_name} within {timeout}s.")
