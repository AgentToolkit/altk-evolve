# Platform Integrations Testing - Agent Guide

## Purpose

Tests for `platform-integrations/install.sh` to ensure the installer:
1. **NEVER overwrites existing user data** (critical requirement)
2. Is idempotent (can be run multiple times safely)
3. Properly installs/uninstalls for Bob, Roo, and Claude platforms

## Critical Requirement

**The installer must NEVER overwrite or corrupt existing user skills, commands, modes, or configurations.**

This is the most important requirement. Users may have custom skills, commands, and modes that they've created. The installer must:
- Detect existing user content
- Preserve it completely during installation
- Add Evolve content alongside (not replacing) user content
- Use sentinel comments in YAML files to mark Evolve-managed sections
- Use JSON key upserts to add Evolve config without touching user config

## Test Structure

```
tests/platform_integrations/
├── AGENTS.md                    # This file
├── conftest.py                  # Fixtures and helpers
├── test_preservation.py         # CRITICAL: User data preservation tests
└── test_idempotency.py         # Idempotency tests
```

## Running Tests

```bash
# Run all platform integration tests
uv run pytest tests/platform_integrations/ -v

# Run only preservation tests (most critical)
uv run pytest tests/platform_integrations/test_preservation.py -v

# Run only idempotency tests
uv run pytest tests/platform_integrations/test_idempotency.py -v

# Run with marker
uv run pytest -m platform_integrations -v

# Run specific test
uv run pytest tests/platform_integrations/test_preservation.py::TestBobPreservation::test_preserves_existing_skills -v
```

## What Gets Tested

### Preservation Tests (test_preservation.py)
These verify that existing user data is NEVER overwritten:

**Bob Platform:**
- User's custom skills in `.bob/skills/` are preserved
- User's custom commands in `.bob/commands/` are preserved
- User's custom modes in `.bob/custom_modes.yaml` are preserved
- User's MCP servers in `.bob/mcp.json` are preserved
- All preservation works when installing multiple platforms

### Idempotency Tests (test_idempotency.py)
These verify that running install multiple times is safe:

**Bob Platform:**
- Multiple lite mode installs produce identical results
- Multiple full mode installs produce identical results (no duplicate MCP entries)
- Installing after partial uninstall restores missing components

**Uninstall/Install Cycles:**
- Uninstalling and reinstalling works correctly
- User content remains intact through the cycle

## Available Fixtures

### Core Fixtures (from conftest.py)

```python
@pytest.mark.platform_integrations
def test_example(temp_project_dir, install_runner, file_assertions):
    """
    temp_project_dir: Isolated temporary directory (auto-cleanup)
    install_runner: Helper to run install.sh commands
    file_assertions: Helper methods for file assertions
    """
    pass
```

### Platform Fixtures

```python
@pytest.mark.platform_integrations
def test_example(bob_fixtures):
    """
    bob_fixtures: Create Bob platform test data
    """
    pass
```

## Common Usage Patterns

### Running install.sh

```python
# Install Bob lite mode
install_runner.run("install", platform="bob", mode="lite")

# Install Bob full mode (includes MCP server)
install_runner.run("install", platform="bob", mode="full")

# Install Codex
install_runner.run("install", platform="codex")

# Install all platforms
install_runner.run("install", platform="all")

# Uninstall
install_runner.run("uninstall", platform="bob")

# Dry run (no changes made)
install_runner.run("install", platform="bob", dry_run=True)

# Expect failure
install_runner.run("install", platform="bob", expect_success=False)
```

### Creating Test Data

```python
# Bob fixtures
bob_fixtures.create_existing_skill(temp_project_dir, "my-skill")
bob_fixtures.create_existing_command(temp_project_dir, "my-command")
bob_fixtures.create_existing_custom_modes(temp_project_dir)
bob_fixtures.create_existing_mcp_config(temp_project_dir)
```

### File Assertions

```python
# Check existence
file_assertions.assert_file_exists(path, "optional message")
file_assertions.assert_dir_exists(path)
file_assertions.assert_file_not_exists(path)
file_assertions.assert_dir_not_exists(path)

# Check content unchanged
original_content = path.read_text()
# ... do something ...
file_assertions.assert_file_unchanged(path, original_content)

# Check JSON validity and keys
file_assertions.assert_valid_json(path)
file_assertions.assert_json_has_key(path, ["mcpServers", "evolve"])
file_assertions.assert_json_not_has_key(path, ["mcpServers", "evolve"])

# Read/write JSON
data = file_assertions.read_json(path)
file_assertions.write_json(path, data)

# Check YAML sentinel blocks
file_assertions.assert_sentinel_block_exists(path, "evolve-lite")
file_assertions.assert_sentinel_block_not_exists(path, "evolve-lite")
```

## Writing New Tests

### Template

```python
import pytest

@pytest.mark.platform_integrations
class TestMyFeature:
    """Test description."""
    
    def test_specific_behavior(
        self, temp_project_dir, install_runner, bob_fixtures, file_assertions
    ):
        """Test that specific behavior works correctly."""
        # Setup: Create existing user data
        custom_skill = bob_fixtures.create_existing_skill(temp_project_dir)
        original_content = (custom_skill / "SKILL.md").read_text()
        
        # Action: Run install
        install_runner.run("install", platform="bob")
        
        # Assert: User data preserved
        file_assertions.assert_file_unchanged(
            custom_skill / "SKILL.md", 
            original_content
        )
        
        # Assert: Evolve installed
        bob_dir = temp_project_dir / ".bob"
        file_assertions.assert_dir_exists(bob_dir / "skills" / "evolve-lite:learn")
```

### Rules for New Tests

1. **Always use `@pytest.mark.platform_integrations` marker**
2. **All file operations must use `temp_project_dir`** - Never touch real files
3. **Use `install_runner` to execute install.sh** - Don't run subprocess directly
4. **Use fixtures to create test data** - Don't manually create files
5. **Use `file_assertions` for common checks** - Don't write custom assertions

## How install.sh Works

### File Operation Strategies

**JSON Files (mcp.json):**
- Atomic read-modify-write using temp files
- Key upsert: Navigate nested keys, set leaf value
- Array upsert: Find by slug, replace in-place or append
- Uses `os.replace()` for atomic writes on POSIX

**YAML Files (custom_modes.yaml):**
- Uses sentinel comment blocks to mark Evolve-managed sections
- Format: `# >>>evolve:slug<<<` ... content ... `# <<<evolve:slug<<<`
- Install: Replace content between sentinels if exists, append if not
- Uninstall: Remove lines between sentinels (inclusive)

**Directory Copies:**
- Uses `shutil.copytree(..., dirs_exist_ok=True)` for idempotency
- Overwrites Evolve files but leaves user files untouched

### Platform-Specific Behavior

**Bob Lite Mode:**
1. Copy `skills/evolve-lite:learn/` → `.bob/skills/evolve-lite:learn/`
2. Copy `skills/evolve-lite:recall/` → `.bob/skills/evolve-lite:recall/`
3. Copy `commands/` → `.bob/commands/`
4. Merge `custom_modes.yaml` using sentinel blocks

**Codex Lite Mode:**
(See `install_codex()` in install.sh for implementation details)

1. Copy plugin: `platform-integrations/codex/plugins/evolve-lite/` → `<target_dir>/plugins/evolve-lite/`
2. Copy shared lib: `platform-integrations/claude/plugins/evolve-lite/lib/` → `<target_dir>/plugins/evolve-lite/lib/`
3. Register plugin in marketplace: Upsert entry in `<target_dir>/.agents/plugins/marketplace.json`
4. Register UserPromptSubmit hook: Upsert hook in `<target_dir>/.codex/hooks.json` for automatic recall
5. **Note:** Automatic recall requires enabling hooks in `~/.codex/config.toml`:
   ```toml
   [features]
   codex_hooks = true
   ```
   If hooks are not enabled, invoke the `evolve-lite:recall` skill manually.

## Important Notes

### Temporary Directories
- All tests run in isolated temporary directories via pytest's `tmp_path`
- Each test gets its own fresh directory
- Automatic cleanup after test completion
- Never contaminate the repo or interfere with other tests

### Test Isolation
- Tests are completely isolated from each other
- No shared state between tests
- Safe to run in parallel (if needed)

### Success Criteria
- All preservation tests must pass (100% - non-negotiable)
- All idempotency tests must pass (100%)
- Tests should complete in < 30 seconds

## Debugging

```bash
# Verbose output
uv run pytest tests/platform_integrations/ -v -s

# Very verbose output
uv run pytest tests/platform_integrations/ -vv -s

# Debug specific test
uv run pytest tests/platform_integrations/test_preservation.py::TestBobPreservation::test_preserves_existing_skills -vv -s

# Add breakpoint in test
def test_something(temp_project_dir):
    print(f"Temp dir: {temp_project_dir}")
    import pdb; pdb.set_trace()
```

## Adding New Test Categories

If you need to add new test files:

1. Create `test_*.py` in `tests/platform_integrations/`
2. Use `@pytest.mark.platform_integrations` on all test classes/functions
3. Import and use fixtures from `conftest.py`
4. Follow patterns in existing test files
5. Ensure all tests use `temp_project_dir` for isolation
6. Update this AGENTS.md with new test category description

## Key Takeaways

1. **Preservation is critical** - User data must never be overwritten
2. **Use temporary directories** - All tests run in isolated temp dirs
3. **Use provided fixtures** - Don't create test data manually
4. **Tests are isolated** - Each test gets its own clean environment
5. **Idempotency matters** - Running install multiple times must be safe
