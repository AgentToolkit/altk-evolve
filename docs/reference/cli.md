# Evolve CLI

Evolve includes a command-line interface for managing namespaces and entities directly.

## Installation

The CLI is installed automatically with the package:

```bash
uv sync
```

## Commands

### Namespace Management

```bash
# List all namespaces
evolve namespaces list

# Create a new namespace
evolve namespaces create my_namespace

# Show namespace details
evolve namespaces info my_namespace

# Delete a namespace (prompts for confirmation)
evolve namespaces delete my_namespace

# Delete without confirmation
evolve namespaces delete my_namespace --force
```

### Entity Management

```bash
# List all entities in a namespace
evolve entities list my_namespace

# List entities filtered by type
evolve entities list my_namespace --type guideline

# Add an entity
evolve entities add my_namespace --content "Always write tests first" --type guideline

# Add entity without LLM-based conflict resolution (faster, no OpenAI key needed)
evolve entities add my_namespace --content "Use descriptive names" --type guideline --no-conflict-resolution

# Add entity with metadata (JSON format)
evolve entities add my_namespace --content "Check return values" --type guideline --metadata '{"source": "code-review"}'

# Search entities using semantic similarity
evolve entities search my_namespace "testing best practices"

# Search with type filter
evolve entities search my_namespace "error handling" --type guideline

# Show full details of an entity
evolve entities show my_namespace 12345

# Delete an entity
evolve entities delete my_namespace 12345
```

### Data Retention

Manage policies, rules, and schedules directly in Evolve:

```bash
evolve retention policies create standard --namespace my-service
evolve retention policies rules add standard --name old-memories --namespace my-service --max-age-days 90 --action delete

# Immediate execution of the stored policy (dry run by default).
evolve retention run standard --namespace my-service --actor alice
evolve retention run standard --namespace my-service --actor alice --apply

# Create a schedule for the running Evolve service.
evolve retention schedules create nightly --namespace my-service --actor alice --policy standard --schedule '0 2 * * *' --time-zone America/Los_Angeles
evolve retention schedules stop nightly --namespace my-service --actor alice --revision 1
evolve retention schedules start nightly --namespace my-service --actor alice --revision 2
```

`policies` supports `create`, `list`, `show`, `update`, and `delete`.
`policies rules` supports `add`, `list`, `update`, and `remove`; select a rule with `--name`.
`schedules` supports `create`, `show`, `list`, `update`, `delete`, `start`, and `stop`. `show` includes the next five scheduled times alongside configuration, or no upcoming runs when suspended.
`jobs` supports `list`, `show`, `cancel`, and `recover`.

The Evolve service runs enabled schedules automatically. `start`/`stop` change persisted schedule state; stopping a schedule does not cancel admitted jobs.

Catalog operations require `--namespace`. Immediate runs and schedule writes require `--actor` for audit attribution. `run` takes a stored policy ID, and `--apply` enables mutations. Schedule updates take `--revision` and preserve omitted fields. Retention commands do not take policy or schedule files.

Reports contain actions, reasons, deciding rules, and evidence. See the
[Data Retention guide](../guides/retention.md) for rule semantics and the
[Scheduling guide](../guides/retention-scheduling.md) for command options, execution, and recovery.

### Skill Management

```bash
# Package all skills from default location (plugins/evolve/skills → dist/)
evolve skills package

# Preview what would be packaged (no files created)
evolve skills package --dry-run

# Package from a custom source directory
evolve skills package --source ./my-skills

# Package to a custom output directory
evolve skills package --output ./dist

# Remove existing .skill files before packaging
evolve skills package --clean

# Combine options
evolve skills package --source ./my-skills --output ./dist --clean
```

**Options:**
- `--source, -s`: Source directory containing skill folders (default: `plugins/evolve/skills`)
- `--output, -o`: Output directory for `.skill` files (default: `dist`)
- `--clean`: Remove existing `.skill` files in output directory before packaging
- `--dry-run`: Show what would be packaged without creating files

**Skill Requirements:**
- Each skill must be a directory containing a `SKILL.md` file
- The resulting `.skill` file is a ZIP archive with the skill directory as the top-level folder

## Examples

```bash
# Create a namespace for coding guidelines
uv run evolve namespaces create coding_guidelines

# Add some guidelines
uv run evolve entities add coding_guidelines \
  --content "Always handle errors explicitly" \
  --type guideline \
  --no-conflict-resolution

uv run evolve entities add coding_guidelines \
  --content "Write unit tests for all public functions" \
  --type guideline \
  --no-conflict-resolution

# Search for relevant guidelines
uv run evolve entities search coding_guidelines "error handling"

# List all guidelines
uv run evolve entities list coding_guidelines --type guideline

# Package skills for distribution
uv run evolve skills package --dry-run  # Preview first
uv run evolve skills package --clean    # Package with clean output
```

### Viz

```bash
# Start the local viz server (opens browser automatically)
evolve viz serve

# Custom .evolve directory and port
evolve viz serve --evolve-dir /path/to/.evolve --port 8080

# Don't open the browser
evolve viz serve --no-browser
```

**Options:**
- `--evolve-dir, -d`: Path to `.evolve` directory (default: `.evolve`)
- `--port, -p`: Port to serve on (default: `7891`)
- `--no-browser`: Skip opening the browser automatically

See the [Evolve Viz guide](../guides/viz.md) for full usage.

## Environment Variables

The CLI uses the same environment variables as the MCP server. See the [Configuration](README.md#configuration) section in the main README.
