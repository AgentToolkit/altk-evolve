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
bob_image := "evolve-bob-sandbox"
env_file := "sandbox/myenv"
bob_home := env_var_or_default("BOB_HOME", ".bob-sandbox-home")
bob_hostname := env_var_or_default("BOB_HOSTNAME", "evolve-bob-sandbox")
bob_sso_port := env_var_or_default("BOB_SSO_PORT", "47687")
sandbox_dir := "sandbox"
workspace := "demo/workspace"

# Build sandbox Docker image(s). Use target=claude, codex, or bob to build only one.
sandbox-build target="all":
    #!/usr/bin/env sh
    set -e
    if [ "{{target}}" != "all" ] && [ "{{target}}" != "claude" ] && [ "{{target}}" != "codex" ] && [ "{{target}}" != "bob" ]; then
        echo "Error: target must be one of: all, claude, codex, bob" >&2
        exit 1
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "claude" ]; then
        docker build --target claude -t {{claude_image}} {{sandbox_dir}}
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "codex" ]; then
        docker build --target codex -t {{codex_image}} {{sandbox_dir}}
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "bob" ]; then
        docker build --target bob -t {{bob_image}} {{sandbox_dir}}
    fi

# Copy sample.env to myenv if it doesn't already exist
sandbox-setup:
    @if [ ! -f {{env_file}} ]; then \
        cp sandbox/sample.env {{env_file}}; \
        echo "Created {{env_file}} — edit it and set your API keys"; \
    else \
        echo "{{env_file}} already exists, skipping"; \
    fi

# Remove sandbox Docker image(s). Use target=claude, codex, or bob to remove only one.
sandbox-clean target="all":
    #!/usr/bin/env sh
    if [ "{{target}}" != "all" ] && [ "{{target}}" != "claude" ] && [ "{{target}}" != "codex" ] && [ "{{target}}" != "bob" ]; then
        echo "Error: target must be one of: all, claude, codex, bob" >&2
        exit 1
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "claude" ]; then
        docker rmi {{claude_image}} || true
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "codex" ]; then
        docker rmi {{codex_image}} || true
    fi
    if [ "{{target}}" = "all" ] || [ "{{target}}" = "bob" ]; then
        docker rmi {{bob_image}} || true
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

# Run an interactive Bob shell in the sandbox
bob-run: _bob-force-sso
    mkdir -p {{bob_home}}
    docker run --rm -it --hostname {{bob_hostname}} --env BOB_SHELL_FORCE_FILE_STORAGE=true --env SSO_PORT={{bob_sso_port}} --publish 127.0.0.1:{{bob_sso_port}}:{{bob_sso_port}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/{{bob_home}}":/home/sandbox/.bob {{bob_image}}

# Authenticate Bob in the sandbox with browser SSO. Open the printed URL on the host.
bob-auth: _bob-force-sso  # pragma: allowlist secret
    mkdir -p {{bob_home}}
    docker run --rm -it --hostname {{bob_hostname}} --env BOB_SHELL_FORCE_FILE_STORAGE=true --env SSO_PORT={{bob_sso_port}} --publish 127.0.0.1:{{bob_sso_port}}:{{bob_sso_port}} -v "$(pwd)/{{bob_home}}":/home/sandbox/.bob {{bob_image}} bob --accept-license --auth-method sso
    just _bob-force-sso

# Run a one-shot prompt in the sandbox
bob-prompt prompt: _bob-force-sso
    #!/usr/bin/env sh
    export SANDBOX_PROMPT="$(cat <<'PROMPT_EOF'
    {{prompt}}
    PROMPT_EOF
    )"
    mkdir -p {{bob_home}}
    docker run --rm -it --hostname {{bob_hostname}} --env BOB_SHELL_FORCE_FILE_STORAGE=true --env SANDBOX_PROMPT --env SSO_PORT={{bob_sso_port}} --publish 127.0.0.1:{{bob_sso_port}}:{{bob_sso_port}} -v "$(cd {{workspace}} && pwd)":/workspace -v "$(pwd)/{{bob_home}}":/home/sandbox/.bob {{bob_image}} sh -c '
        bob -C /workspace --accept-license --auth-method sso --yolo -p "$SANDBOX_PROMPT"
    '

# Smoke-test that Bob is installed and working
bob-test: _bob-force-sso
    mkdir -p {{bob_home}}
    docker run --rm --hostname {{bob_hostname}} --env BOB_SHELL_FORCE_FILE_STORAGE=true --env SSO_PORT={{bob_sso_port}} --publish 127.0.0.1:{{bob_sso_port}}:{{bob_sso_port}} -v "$(pwd)/{{bob_home}}":/home/sandbox/.bob {{bob_image}} bob --accept-license --auth-method sso -p "who are you"

_bob-force-sso:
    mkdir -p {{bob_home}}
    node -e 'const fs = require("fs"); const path = "{{bob_home}}/settings.json"; const data = fs.existsSync(path) ? JSON.parse(fs.readFileSync(path, "utf8")) : {}; data.security ??= {}; data.security.auth ??= {}; data.security.auth.selectedType = "sso"; fs.writeFileSync(path, JSON.stringify(data, null, 2) + "\n");'

# Render plugin-source/ into platform-integrations/. Edit plugin-source/, then run this.
compile-plugins:
    uv run python plugin-source/build_plugins.py render

# Verify committed platform-integrations/ matches a fresh render of plugin-source/.
# CI and the pre-commit hook run this; nonzero exit means the source and output have drifted.
check-plugins-rendered:
    uv run python plugin-source/build_plugins.py check
