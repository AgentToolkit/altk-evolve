# Sandbox

Docker-based sandboxes for running Claude Code and Codex in isolated environments with Evolve Lite support.

## Build

Build both images from the repository root:

```bash
just sandbox-build
```

To build only one:

```bash
just sandbox-build claude
just sandbox-build codex
```

## Setup

Copy the sample env file and fill in your API keys:

```bash
just sandbox-setup
```

Then edit `sandbox/myenv` and set:

- `ANTHROPIC_API_KEY` — required for Claude Code
- `OPENAI_API_KEY` — required for Codex (or use device auth, see below)

## Claude Code

### Interactive shell

```bash
just claude-run
```

This mounts `demo/workspace` as `/workspace` and your local `platform-integrations/claude/plugins` as `/plugins`, then drops you into a bash shell. From there:

```bash
claude --plugin-dir /plugins/evolve-lite --dangerously-skip-permissions
```

### One-shot prompt

```bash
just claude-prompt 'your prompt here'
```

Optional flags:

- `trace=true` — summarizes the session after the prompt completes
- `learn=true` — runs `/evolve-lite:learn` to extract entities from the session

Example with both:

```bash
just trace=true learn=true claude-prompt 'where was the photo @sample.jpg taken. use exif metadata'
```

### Smoke test

```bash
just claude-test
```

See [docs/integrations/evolve-lite.md](../docs/integrations/evolve-lite.md) for the full learn/recall walkthrough.

## Codex

### Interactive shell

```bash
just codex-run
```

This mounts the full repository at `/workspace` and starts Codex in `demo/workspace`. By default, the Codex home lives only inside the container for that run.

On first run, authenticate via device auth:

```bash
codex login --device-auth
```

Or pass your API key directly:

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

Then start Codex:

```bash
codex
```

### Installing the Evolve Lite plugin

Once inside Codex:

1. Run `/plugins`
2. Open `Evolve Local Plugins`
3. Install `evolve`
4. Start a new thread in `/workspace`

The plugin manifest is at `demo/workspace/plugins/evolve-lite/.codex-plugin/plugin.json`. Entities are read from and written to `demo/workspace/.evolve/entities/`.

### Smoke test

```bash
just codex-test
```

### Reusing existing Codex state

If you want Codex to reuse your normal host state, mount your main `~/.codex` at `/codex-home` when starting the container.

See [docs/integrations/evolve-lite-codex.md](../docs/integrations/evolve-lite-codex.md) for the full Codex + Evolve Lite walkthrough.

## Cleanup

Remove both images:

```bash
just sandbox-clean
```

To remove only one:

```bash
just sandbox-clean claude
just sandbox-clean codex
```
