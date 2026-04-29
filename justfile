# Default: list available targets
default:
    @just --list

# Format staged files and commit — avoids pre-commit stash conflicts with ruff auto-fixes
commit message:
    uv run ruff format .
    git add -u
    git commit -m "{{message}}"

claude_image := "claude-sandbox"
codex_image := "evolve-codex-sandbox"
env_file := "sandbox/myenv"
sandbox_dir := "sandbox"
workspace := "demo/workspace"

# Build sandbox Docker image(s). Use target=claude or target=codex to build only one.
sandbox-build target="all":
    #!/usr/bin/env sh
    set -e
    if [ "{{target}}" != "all" ] && [ "{{target}}" != "claude" ] && [ "{{target}}" != "codex" ]; then
        echo "Error: target must be one of: all, claude, codex" >&2
        exit 1
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "claude" ]; then
        docker build --target claude -t {{claude_image}} {{sandbox_dir}}
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "codex" ]; then
        docker build --target codex -t {{codex_image}} {{sandbox_dir}}
    fi

# Copy sample.env to myenv if it doesn't already exist
sandbox-setup:
    @if [ ! -f {{env_file}} ]; then \
        cp sandbox/sample.env {{env_file}}; \
        echo "Created {{env_file}} — edit it and set your API keys"; \
    else \
        echo "{{env_file}} already exists, skipping"; \
    fi

# Remove sandbox Docker image(s). Use target=claude or target=codex to remove only one.
sandbox-clean target="all":
    #!/usr/bin/env sh
    if [ "{{target}}" != "all" ] && [ "{{target}}" != "claude" ] && [ "{{target}}" != "codex" ]; then
        echo "Error: target must be one of: all, claude, codex" >&2
        exit 1
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "claude" ]; then
        docker rmi {{claude_image}} || true
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "codex" ]; then
        docker rmi {{codex_image}} || true
    fi

# Run an interactive Claude Code shell in the sandbox
claude-run:
    docker run --rm -it --env-file {{env_file}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/platform-integrations/claude/plugins":/plugins {{claude_image}}

# Run a one-shot prompt in the sandbox
claude-prompt prompt:
    #!/usr/bin/env sh
    export SANDBOX_PROMPT="$(cat <<'PROMPT_EOF'
    {{prompt}}
    PROMPT_EOF
    )"
    docker run --rm -it --env SANDBOX_PROMPT --env-file {{env_file}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/platform-integrations/claude/plugins":/plugins {{claude_image}} sh -c "
        claude --plugin-dir /plugins/evolve-lite/ --dangerously-skip-permissions -p \"\$SANDBOX_PROMPT\"
    "

# Smoke-test that Claude Code is installed and working
claude-test:
    docker run --rm --env-file {{env_file}} {{claude_image}} claude -p "who are you"

# Run an interactive Codex shell in the sandbox
codex-run:
    docker run --rm -it --env-file {{env_file}} -v "$(cd {{workspace}} && pwd)":/workspace {{codex_image}}

# Smoke-test that Codex is installed and working
codex-test:
    docker run --rm --env-file {{env_file}} {{codex_image}} codex exec --skip-git-repo-check "who are you"
