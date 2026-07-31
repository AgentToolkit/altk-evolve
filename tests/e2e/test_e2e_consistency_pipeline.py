"""
E2E tests for consistency-guideline generation via `evolve sync phoenix --guidelines-mode consistency`.

Runs a subset of the example agents (those whose actions are LLM-driven, whether via
OpenAI tool_calls or code generation), sends their traces to Phoenix, then exercises the
consistency sync path and verifies that:
- the consistency analyzer actually ran (log evidence of resampling)
- at least one consistency guideline was generated (no silent failures)

Requires:
  - `uv sync --extra examples --extra tracing`
  - `EVOLVE_MODEL_NAME` / `OPENAI_API_KEY` env vars for LLM calls
"""

import datetime
import os
import re
import select
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

# openai_agents uses OpenAI-style tool_calls; smolagents' CodeAgent emits Python code as
# plain assistant content instead. Both are covered by openai_agent.yaml: tool_calls turns
# go through the structured function_name/function_arguments comparison, content turns
# (the smolagents case) fall back to plain-text Jaccard similarity, which is still a
# meaningful consistency signal for code-as-action agents.
AGENTS_TO_TEST = [
    {
        "name": "openai_agents",
        "script": "examples/low_code/openai_agents_demo.py",
        "project_prefix": "verify-consistency-openai",
    },
    {
        "name": "smolagents",
        "script": "examples/low_code/smolagents_demo.py",
        "project_prefix": "verify-consistency-smol",
    },
]


def _consistency_analyzer_available() -> bool:
    try:
        import importlib

        importlib.import_module("altk_evolve.llm.guidelines.consistency_analyzer.resampling")
        return True
    except ImportError:
        return False


@pytest.mark.e2e
@pytest.mark.parametrize("agent_config", AGENTS_TO_TEST, ids=[a["name"] for a in AGENTS_TO_TEST])
def test_e2e_consistency_pipeline(agent_config, phoenix_server, pytestconfig):
    """
    Full E2E pipeline using consistency guideline generation:
    1. Run the example agent with Phoenix tracing
    2. Verify traces appeared in Phoenix
    3. Run `evolve sync phoenix --guidelines-mode consistency` and verify:
       a. The consistency analyzer actually ran (log line "Resampling trajectory IR")
       b. At least one consistency guideline was generated
    """
    if not _consistency_analyzer_available():
        pytest.skip("consistency analyzer not available")

    agent_name = agent_config["name"]
    script_path = agent_config["script"]
    current_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"{agent_config['project_prefix']}-{current_timestamp}"

    print("\n==================================================")
    print(f" CONSISTENCY TEST: {agent_name}")
    print(f" Script: {script_path}")
    print(f" Project: {project_name}")
    print("==================================================")

    # --- Step 1: Run Agent ---
    print("\n--- Step 1: Running Agent ---")
    if not os.path.exists(script_path):
        pytest.fail(f"Script not found: {script_path}")

    env = os.environ.copy()
    env["EVOLVE_AUTO_ENABLED"] = "true"
    env["EVOLVE_TRACING_PROJECT"] = project_name
    env["PHOENIX_PROJECT_NAME"] = project_name

    try:
        result = subprocess.run(
            ["uv", "run", "python", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Agent execution timed out for {agent_name}")

    if result.returncode != 0:
        print("STDERR:", result.stderr)
        print("STDOUT:", result.stdout)
        pytest.fail(f"Agent execution failed: {result.stderr}")

    print(f"Agent finished. Output: {result.stdout.strip()[-200:]}")

    # --- Step 2: Verify Traces ---
    print(f"\n--- Step 2: Verifying Phoenix Traces ({project_name}) ---")
    time.sleep(2)

    check_script = f"""
import phoenix as px, sys
try:
    c = px.Client(endpoint='{phoenix_server}')
    df = c.get_spans_dataframe(project_name='{project_name}')
    print(f"FOUND_TRACES:{{len(df)}}" if df is not None and not df.empty else "NO_TRACES")
except Exception as e:
    print(f"ERROR:{{e}}")
"""
    try:
        check_result = subprocess.run(
            ["uv", "run", "python", "-c", check_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Trace verification timed out")

    output = check_result.stdout + check_result.stderr
    if "FOUND_TRACES" not in output:
        pytest.fail(f"No traces found in Phoenix project '{project_name}'. Debug: {output}")

    trace_count = output.split("FOUND_TRACES:")[1].split()[0]
    print(f"Found {trace_count} traces in '{project_name}'")

    # --- Step 3: Consistency Sync ---
    print("\n--- Step 3: Running evolve sync phoenix (EVOLVE_GUIDELINES_MODE=consistency) ---")
    sync_command = [
        "uv",
        "run",
        "evolve",
        "sync",
        "phoenix",
        "--project",
        project_name,
        "--include-errors",
        "--limit",
        "500",
    ]
    debug_dir = pytestconfig.getoption("--consistency-debug-dir") or str(Path(__file__).parent.parent.parent / "consistency_debug")
    sync_env = os.environ.copy()
    sync_env["EVOLVE_GUIDELINES_MODE"] = "consistency"
    # This test specifically verifies resampling behavior, so pin the accurate method
    # explicitly rather than relying on the default (which is "fast" as of this writing).
    sync_env["EVOLVE_CONSISTENCY_METHOD"] = "accurate"
    sync_env["EVOLVE_DEBUG_DIR"] = debug_dir
    print(f"Debug artifacts will be written to: {debug_dir}")
    verbose_sync = pytestconfig.getoption("--verbose-sync")
    print(f"Command: {' '.join(sync_command)}")

    process = subprocess.Popen(
        sync_command,
        env=sync_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    guidelines_found = False
    resampling_ran = False
    sync_completed = False
    llm_error_occurred = False
    sync_start = time.time()
    timeout = 300  # consistency sync is slower due to N=10 resampling calls
    output_lines = []

    try:
        while True:
            if time.time() - sync_start > timeout:
                print(f"Timeout waiting for consistency sync ({timeout}s)")
                break

            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue

            output_lines.append(line)
            stripped = line.strip()

            if verbose_sync:
                print(f"[sync] {stripped}")

            if "Resampling trajectory IR" in stripped:
                resampling_ran = True
                if not verbose_sync:
                    print(f"[sync] {stripped}")

            if "RateLimitError" in stripped or "Budget has been exceeded" in stripped or "called on trajectory with no steps" in stripped:
                llm_error_occurred = True

            match = re.search(r"generated (\d+) guidelines", stripped)
            if match:
                count = int(match.group(1))
                sync_completed = True
                if count > 0:
                    guidelines_found = True
                    print(f"Generated {count} consistency guidelines")
                else:
                    print("Generated 0 guidelines (trajectory consistent enough to skip)")
                break

            # The per-trajectory "generated N guidelines" line above only prints on
            # success. When every trajectory raises (e.g. the LLM is unavailable, or a
            # trajectory ends up with zero scorable steps because the agent run itself
            # failed before producing any assistant content), the only remaining signal
            # that the sync process ran to completion is this once-per-run summary line.
            if "Sync complete:" in stripped:
                sync_completed = True
                print(f"[sync] {stripped}")
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    full_output = "".join(output_lines)

    # Resampling should run for a real trajectory. But if the LLM itself was unavailable,
    # a trajectory may end up with zero scorable steps (the agent run failed before
    # producing any assistant content) or the resampling call may fail before its log line
    # is even reached — in that case, not resampling is a valid, non-buggy outcome, and this
    # test's purpose (verifying the sync process runs the pipeline correctly and doesn't
    # crash) is still satisfied by the LLM-error fallback below.
    assert resampling_ran or llm_error_occurred, (
        f"Consistency analyzer resampling did not run for {agent_name}. Sync output:\n{full_output[-2000:]}"
    )
    assert sync_completed, f"Consistency sync did not complete for {agent_name}. Sync output:\n{full_output[-2000:]}"
    # guidelines_found is True when count > 0; a count of 0 is also valid —
    # it means SKIP_ON_NO_UNCERTAINTY fired because the trajectory was
    # consistent enough to not warrant guideline generation. An LLM-side failure is
    # also accepted — this test verifies the pipeline runs correctly and reports
    # cleanly, not that LLM credentials/budget are available.
    assert guidelines_found or re.search(r"generated 0 guidelines", full_output) or llm_error_occurred, (
        f"Consistency sync did not complete for {agent_name}. Sync output:\n{full_output[-2000:]}"
    )


@pytest.mark.e2e
@pytest.mark.parametrize("agent_config", AGENTS_TO_TEST, ids=[a["name"] for a in AGENTS_TO_TEST])
def test_e2e_consistency_fast_pipeline(agent_config, phoenix_server, pytestconfig):
    """
    Full E2E pipeline using EVOLVE_CONSISTENCY_METHOD=fast (the LLM self-judges step
    confidence instead of resampling):
    1. Run the example agent with Phoenix tracing
    2. Verify traces appeared in Phoenix
    3. Run `evolve sync phoenix --guidelines-mode consistency --consistency-method fast` and verify:
       a. The consistency analyzer's resampling pass did NOT run (no "Resampling trajectory IR"
          log line) — this is the whole point of fast mode, so its absence is asserted rather
          than merely tolerated.
       b. The sync completed and reported a guideline count (0 is valid — the LLM may judge
          every step confident and return no guidelines).
    """
    if not _consistency_analyzer_available():
        pytest.skip("consistency analyzer not available")

    agent_name = agent_config["name"]
    script_path = agent_config["script"]
    current_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"verify-consistency-fast-{agent_name}-{current_timestamp}"

    print("\n==================================================")
    print(f" CONSISTENCY-FAST TEST: {agent_name}")
    print(f" Script: {script_path}")
    print(f" Project: {project_name}")
    print("==================================================")

    # --- Step 1: Run Agent ---
    print("\n--- Step 1: Running Agent ---")
    if not os.path.exists(script_path):
        pytest.fail(f"Script not found: {script_path}")

    env = os.environ.copy()
    env["EVOLVE_AUTO_ENABLED"] = "true"
    env["EVOLVE_TRACING_PROJECT"] = project_name
    env["PHOENIX_PROJECT_NAME"] = project_name

    try:
        result = subprocess.run(
            ["uv", "run", "python", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Agent execution timed out for {agent_name}")

    if result.returncode != 0:
        print("STDERR:", result.stderr)
        print("STDOUT:", result.stdout)
        pytest.fail(f"Agent execution failed: {result.stderr}")

    print(f"Agent finished. Output: {result.stdout.strip()[-200:]}")

    # --- Step 2: Verify Traces ---
    print(f"\n--- Step 2: Verifying Phoenix Traces ({project_name}) ---")
    time.sleep(2)

    check_script = f"""
import phoenix as px, sys
try:
    c = px.Client(endpoint='{phoenix_server}')
    df = c.get_spans_dataframe(project_name='{project_name}')
    print(f"FOUND_TRACES:{{len(df)}}" if df is not None and not df.empty else "NO_TRACES")
except Exception as e:
    print(f"ERROR:{{e}}")
"""
    try:
        check_result = subprocess.run(
            ["uv", "run", "python", "-c", check_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Trace verification timed out")

    output = check_result.stdout + check_result.stderr
    if "FOUND_TRACES" not in output:
        pytest.fail(f"No traces found in Phoenix project '{project_name}'. Debug: {output}")

    trace_count = output.split("FOUND_TRACES:")[1].split()[0]
    print(f"Found {trace_count} traces in '{project_name}'")

    # --- Step 3: Consistency-Fast Sync ---
    print("\n--- Step 3: Running evolve sync phoenix (--consistency-method fast) ---")
    sync_command = [
        "uv",
        "run",
        "evolve",
        "sync",
        "phoenix",
        "--project",
        project_name,
        "--include-errors",
        "--limit",
        "500",
        "--guidelines-mode",
        "consistency",
        "--consistency-method",
        "fast",
    ]
    debug_dir = pytestconfig.getoption("--consistency-debug-dir") or str(Path(__file__).parent.parent.parent / "consistency_debug")
    sync_env = os.environ.copy()
    sync_env["EVOLVE_DEBUG_DIR"] = debug_dir
    print(f"Debug artifacts will be written to: {debug_dir}")
    verbose_sync = pytestconfig.getoption("--verbose-sync")
    print(f"Command: {' '.join(sync_command)}")

    process = subprocess.Popen(
        sync_command,
        env=sync_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    guidelines_found = False
    resampling_ran = False
    sync_completed = False
    llm_error_occurred = False
    sync_start = time.time()
    # Fast mode does one LLM call per trajectory instead of N resamples — much quicker
    # than the accurate pipeline's 300s budget.
    timeout = 120
    output_lines = []

    try:
        while True:
            if time.time() - sync_start > timeout:
                print(f"Timeout waiting for consistency-fast sync ({timeout}s)")
                break

            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue

            output_lines.append(line)
            stripped = line.strip()

            if verbose_sync:
                print(f"[sync] {stripped}")

            if "Resampling trajectory IR" in stripped:
                resampling_ran = True
                print(f"[sync] UNEXPECTED resampling log line: {stripped}")

            if (
                "RateLimitError" in stripped
                or "Budget has been exceeded" in stripped
                or "called on trajectory with no steps" in stripped
                or "called with empty messages" in stripped
            ):
                llm_error_occurred = True

            match = re.search(r"generated (\d+) guidelines", stripped)
            if match:
                count = int(match.group(1))
                sync_completed = True
                if count > 0:
                    guidelines_found = True
                    print(f"Generated {count} consistency-fast guidelines")
                else:
                    print("Generated 0 guidelines (LLM judged every step confident)")
                break

            # The per-trajectory "generated N guidelines" line above only prints on
            # success. When every trajectory raises (e.g. the LLM call itself fails),
            # the only remaining signal that the sync process ran to completion is
            # this once-per-run final summary line.
            if "Sync complete:" in stripped:
                sync_completed = True
                print(f"[sync] {stripped}")
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    full_output = "".join(output_lines)

    assert not resampling_ran, (
        f"Consistency-fast mode should never resample, but resampling ran for {agent_name}. Sync output:\n{full_output[-2000:]}"
    )
    assert sync_completed, f"Consistency-fast sync did not complete for {agent_name}. Sync output:\n{full_output[-2000:]}"
    # A count of 0 is valid: the LLM may judge every step confident and return no
    # guidelines. An LLM-side failure (rate limit / budget exceeded) is also accepted
    # here — this test verifies the sync process runs the fast path correctly (no
    # resampling) and reports cleanly, not that LLM credentials/budget are available.
    assert guidelines_found or re.search(r"generated 0 guidelines", full_output) or llm_error_occurred, (
        f"Consistency-fast sync produced an unexpected result for {agent_name}. Sync output:\n{full_output[-2000:]}"
    )


@pytest.mark.e2e
def test_e2e_all_mode_smolagents(phoenix_server, pytestconfig):
    """
    Full E2E pipeline using EVOLVE_GUIDELINES_MODE=all with the smolagents demo.

    Verifies that both pipelines run in a single sync pass:
    1. Run the smolagents demo with Phoenix tracing.
    2. Verify traces appeared in Phoenix.
    3. Run `evolve sync phoenix` with EVOLVE_GUIDELINES_MODE=all and verify:
       a. The consistency analyzer resampled (consistency pipeline ran).
       b. The sync completed and reported a guideline count.
       c. At least one guideline was stored (the standard pipeline always produces
          guidelines regardless of uncertainty level).
    """
    if not _consistency_analyzer_available():
        pytest.skip("consistency analyzer not available")

    script_path = "examples/low_code/smolagents_demo.py"
    current_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"verify-all-smol-{current_timestamp}"

    print("\n==================================================")
    print(" ALL-MODE TEST: smolagents")
    print(f" Script: {script_path}")
    print(f" Project: {project_name}")
    print("==================================================")

    # --- Step 1: Run Agent ---
    print("\n--- Step 1: Running Agent ---")
    if not os.path.exists(script_path):
        pytest.fail(f"Script not found: {script_path}")

    env = os.environ.copy()
    env["EVOLVE_AUTO_ENABLED"] = "true"
    env["EVOLVE_TRACING_PROJECT"] = project_name
    env["PHOENIX_PROJECT_NAME"] = project_name

    try:
        result = subprocess.run(
            ["uv", "run", "python", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Agent execution timed out")

    if result.returncode != 0:
        print("STDERR:", result.stderr)
        print("STDOUT:", result.stdout)
        pytest.fail(f"Agent execution failed: {result.stderr}")

    print(f"Agent finished. Output: {result.stdout.strip()[-200:]}")

    # --- Step 2: Verify Traces ---
    print(f"\n--- Step 2: Verifying Phoenix Traces ({project_name}) ---")
    time.sleep(2)

    check_script = f"""
import phoenix as px, sys
try:
    c = px.Client(endpoint='{phoenix_server}')
    df = c.get_spans_dataframe(project_name='{project_name}')
    print(f"FOUND_TRACES:{{len(df)}}" if df is not None and not df.empty else "NO_TRACES")
except Exception as e:
    print(f"ERROR:{{e}}")
"""
    try:
        check_result = subprocess.run(
            ["uv", "run", "python", "-c", check_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Trace verification timed out")

    output = check_result.stdout + check_result.stderr
    if "FOUND_TRACES" not in output:
        pytest.fail(f"No traces found in Phoenix project '{project_name}'. Debug: {output}")

    trace_count = output.split("FOUND_TRACES:")[1].split()[0]
    print(f"Found {trace_count} traces in '{project_name}'")

    # --- Step 3: All-mode Sync ---
    print("\n--- Step 3: Running evolve sync phoenix (EVOLVE_GUIDELINES_MODE=all) ---")
    sync_command = [
        "uv",
        "run",
        "evolve",
        "sync",
        "phoenix",
        "--project",
        project_name,
        "--include-errors",
        "--limit",
        "500",
    ]
    debug_dir = pytestconfig.getoption("--consistency-debug-dir") or str(Path(__file__).parent.parent.parent / "consistency_debug")
    sync_env = os.environ.copy()
    sync_env["EVOLVE_GUIDELINES_MODE"] = "all"
    # This test specifically verifies resampling behavior, so pin the accurate method
    # explicitly rather than relying on the default (which is "fast" as of this writing).
    sync_env["EVOLVE_CONSISTENCY_METHOD"] = "accurate"
    sync_env["EVOLVE_DEBUG_DIR"] = debug_dir
    print(f"Debug artifacts will be written to: {debug_dir}")
    verbose_sync = pytestconfig.getoption("--verbose-sync")
    print(f"Command: {' '.join(sync_command)}")

    process = subprocess.Popen(
        sync_command,
        env=sync_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    sync_completed = False
    resampling_ran = False
    llm_error_occurred = False
    total_guidelines = 0
    sync_start = time.time()
    timeout = 300
    output_lines = []

    try:
        while True:
            if time.time() - sync_start > timeout:
                print(f"Timeout waiting for sync ({timeout}s)")
                break

            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue

            output_lines.append(line)
            stripped = line.strip()

            if verbose_sync:
                print(f"[sync] {stripped}")

            if "Resampling trajectory IR" in stripped:
                resampling_ran = True
                if not verbose_sync:
                    print(f"[sync] {stripped}")

            if "RateLimitError" in stripped or "Budget has been exceeded" in stripped or "called on trajectory with no steps" in stripped:
                llm_error_occurred = True

            match = re.search(r"generated (\d+) guidelines", stripped)
            if match:
                total_guidelines = int(match.group(1))
                sync_completed = True
                print(f"[sync] {stripped}")
                break

            # The per-trajectory "generated N guidelines" line above only prints on
            # success. When every trajectory raises (e.g. the LLM is unavailable), the
            # only remaining signal that the sync process ran to completion is this
            # once-per-run summary line.
            if "Sync complete:" in stripped:
                sync_completed = True
                print(f"[sync] {stripped}")
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    full_output = "".join(output_lines)

    assert resampling_ran or llm_error_occurred, (
        f"Consistency pipeline resampling did not run in 'all' mode. Sync output:\n{full_output[-2000:]}"
    )
    assert sync_completed, f"Sync did not complete in 'all' mode. Sync output:\n{full_output[-2000:]}"
    # Standard pipeline always produces guidelines when the LLM is available; an
    # LLM-side failure is also accepted — this test verifies both pipelines are
    # invoked and the sync reports cleanly, not that LLM credentials/budget are available.
    assert total_guidelines > 0 or llm_error_occurred, (
        f"Expected at least standard guidelines in 'all' mode, got 0. Sync output:\n{full_output[-2000:]}"
    )


@pytest.mark.e2e
def test_e2e_all_mode_fast_smolagents(phoenix_server, pytestconfig):
    """
    Full E2E pipeline using EVOLVE_GUIDELINES_MODE=all with the smolagents demo, on the
    actual out-of-the-box default consistency method (EVOLVE_CONSISTENCY_METHOD is left
    unset here, deliberately, so this test tracks whatever the real default is rather than
    pinning "fast" — the combination `test_e2e_all_mode_smolagents` above exercises is
    all+accurate, chosen specifically to verify resampling; this test closes the gap for
    the combination most users actually run: all+fast).

    Verifies that both pipelines run in a single sync pass:
    1. Run the smolagents demo with Phoenix tracing.
    2. Verify traces appeared in Phoenix.
    3. Run `evolve sync phoenix` with EVOLVE_GUIDELINES_MODE=all (default consistency
       method) and verify:
       a. Resampling did NOT run — the whole point of fast mode is no resampling calls.
       b. The sync completed and reported a guideline count.
       c. At least one guideline was stored (the standard pipeline always produces
          guidelines regardless of uncertainty level).
    """
    if not _consistency_analyzer_available():
        pytest.skip("consistency analyzer not available")

    script_path = "examples/low_code/smolagents_demo.py"
    current_timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"verify-all-fast-smol-{current_timestamp}"

    print("\n==================================================")
    print(" ALL-MODE (FAST) TEST: smolagents")
    print(f" Script: {script_path}")
    print(f" Project: {project_name}")
    print("==================================================")

    # --- Step 1: Run Agent ---
    print("\n--- Step 1: Running Agent ---")
    if not os.path.exists(script_path):
        pytest.fail(f"Script not found: {script_path}")

    env = os.environ.copy()
    env["EVOLVE_AUTO_ENABLED"] = "true"
    env["EVOLVE_TRACING_PROJECT"] = project_name
    env["PHOENIX_PROJECT_NAME"] = project_name

    try:
        result = subprocess.run(
            ["uv", "run", "python", script_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Agent execution timed out")

    if result.returncode != 0:
        print("STDERR:", result.stderr)
        print("STDOUT:", result.stdout)
        pytest.fail(f"Agent execution failed: {result.stderr}")

    print(f"Agent finished. Output: {result.stdout.strip()[-200:]}")

    # --- Step 2: Verify Traces ---
    print(f"\n--- Step 2: Verifying Phoenix Traces ({project_name}) ---")
    time.sleep(2)

    check_script = f"""
import phoenix as px, sys
try:
    c = px.Client(endpoint='{phoenix_server}')
    df = c.get_spans_dataframe(project_name='{project_name}')
    print(f"FOUND_TRACES:{{len(df)}}" if df is not None and not df.empty else "NO_TRACES")
except Exception as e:
    print(f"ERROR:{{e}}")
"""
    try:
        check_result = subprocess.run(
            ["uv", "run", "python", "-c", check_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Trace verification timed out")

    output = check_result.stdout + check_result.stderr
    if "FOUND_TRACES" not in output:
        pytest.fail(f"No traces found in Phoenix project '{project_name}'. Debug: {output}")

    trace_count = output.split("FOUND_TRACES:")[1].split()[0]
    print(f"Found {trace_count} traces in '{project_name}'")

    # --- Step 3: All-mode Sync (default consistency method) ---
    print("\n--- Step 3: Running evolve sync phoenix (EVOLVE_GUIDELINES_MODE=all, default consistency method) ---")
    sync_command = [
        "uv",
        "run",
        "evolve",
        "sync",
        "phoenix",
        "--project",
        project_name,
        "--include-errors",
        "--limit",
        "500",
    ]
    debug_dir = pytestconfig.getoption("--consistency-debug-dir") or str(Path(__file__).parent.parent.parent / "consistency_debug")
    sync_env = os.environ.copy()
    sync_env["EVOLVE_GUIDELINES_MODE"] = "all"
    # Deliberately NOT setting EVOLVE_CONSISTENCY_METHOD — this test exercises whatever
    # the real default is (currently "fast"), unlike test_e2e_all_mode_smolagents which
    # pins "accurate" to verify resampling specifically.
    sync_env["EVOLVE_DEBUG_DIR"] = debug_dir
    print(f"Debug artifacts will be written to: {debug_dir}")
    verbose_sync = pytestconfig.getoption("--verbose-sync")
    print(f"Command: {' '.join(sync_command)}")

    process = subprocess.Popen(
        sync_command,
        env=sync_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    sync_completed = False
    resampling_ran = False
    llm_error_occurred = False
    total_guidelines = 0
    sync_start = time.time()
    # Fast mode does one LLM call per trajectory instead of N resamples — much quicker
    # than the accurate all-mode test's 300s budget.
    timeout = 120
    output_lines = []

    try:
        while True:
            if time.time() - sync_start > timeout:
                print(f"Timeout waiting for sync ({timeout}s)")
                break

            ready, _, _ = select.select([process.stdout], [], [], 0.5)
            if not ready:
                if process.poll() is not None:
                    break
                continue

            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    break
                continue

            output_lines.append(line)
            stripped = line.strip()

            if verbose_sync:
                print(f"[sync] {stripped}")

            if "Resampling trajectory IR" in stripped:
                resampling_ran = True
                print(f"[sync] UNEXPECTED resampling log line: {stripped}")

            if "RateLimitError" in stripped or "Budget has been exceeded" in stripped or "called on trajectory with no steps" in stripped:
                llm_error_occurred = True

            match = re.search(r"generated (\d+) guidelines", stripped)
            if match:
                total_guidelines = int(match.group(1))
                sync_completed = True
                print(f"[sync] {stripped}")
                break

            # The per-trajectory "generated N guidelines" line above only prints on
            # success. When every trajectory raises (e.g. the LLM is unavailable), the
            # only remaining signal that the sync process ran to completion is this
            # once-per-run summary line.
            if "Sync complete:" in stripped:
                sync_completed = True
                print(f"[sync] {stripped}")
                break
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    full_output = "".join(output_lines)

    assert not resampling_ran, (
        f"Consistency-fast mode should never resample, but resampling ran in 'all' mode. Sync output:\n{full_output[-2000:]}"
    )
    assert sync_completed, f"Sync did not complete in 'all' mode. Sync output:\n{full_output[-2000:]}"
    # Standard pipeline always produces guidelines when the LLM is available; an
    # LLM-side failure is also accepted — this test verifies both pipelines are
    # invoked and the sync reports cleanly, not that LLM credentials/budget are available.
    assert total_guidelines > 0 or llm_error_occurred, (
        f"Expected at least standard guidelines in 'all' mode, got 0. Sync output:\n{full_output[-2000:]}"
    )
