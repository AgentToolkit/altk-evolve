# Default: list available targets
default:
    @just --list

image := "claude-sandbox"
env_file := "sandbox/myenv"
sandbox_dir := "sandbox"
workspace := "demo/workspace"
trace := "false"
learn := "false"

# Build the sandbox Docker image
sandbox-build:
    docker build -t {{image}} {{sandbox_dir}}

# Copy sample.env to myenv if it doesn't already exist
sandbox-setup:
    @if [ ! -f {{env_file}} ]; then \
        cp sandbox/sample.env {{env_file}}; \
        echo "Created {{env_file}} — edit it and set your ANTHROPIC_API_KEY"; \
    else \
        echo "{{env_file}} already exists, skipping"; \
    fi

# Run an interactive Claude Code shell in the sandbox
sandbox-run:
    docker run --rm -it --env-file {{env_file}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/plugins":/plugins {{image}}

# Run a one-shot prompt in the sandbox (trace=true to summarize session, learn=true to run /kaizen:learn)
sandbox-prompt prompt:
    #!/usr/bin/env sh
    export SANDBOX_PROMPT="$(cat <<'PROMPT_EOF'
    {{prompt}}
    PROMPT_EOF
    )"
    TRACE_CMD=""
    LEARN_CMD=""
    if [ "{{trace}}" = "true" ]; then
        TRACE_CMD="
            echo; echo; echo Summarizing the session...; echo
            claude --plugin-dir /plugins/kaizen/ --dangerously-skip-permissions --no-session-persistence -p 'tell me what happened in the newest json file in /home/sandbox/.claude/projects/-workspace/'
        "
    fi
    if [ "{{learn}}" = "true" ]; then
        LEARN_CMD="
            echo; echo; echo Learning...; echo
            claude --plugin-dir /plugins/kaizen/ --dangerously-skip-permissions --continue -p '/kaizen:learn'
        "
    fi
    docker run --rm -it --env SANDBOX_PROMPT --env-file {{env_file}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/plugins":/plugins {{image}} sh -c "
        claude --plugin-dir /plugins/kaizen/ --dangerously-skip-permissions -p \"\$SANDBOX_PROMPT\"
        $TRACE_CMD
        $LEARN_CMD
    "

# Smoke-test that Claude Code is installed and working
sandbox-test:
    docker run --rm --env-file {{env_file}} {{image}} claude -p "who are you"

# Remove the sandbox Docker image
sandbox-clean:
    docker rmi {{image}}
