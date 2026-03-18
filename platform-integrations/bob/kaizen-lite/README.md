# Kaizen Lite Skills for Bob

This directory contains Bob-compatible skills and custom modes for the Kaizen learning system.

## Quick Install

To install all skills and modes:

```bash
./platform-integrations/bob/kaizen-lite/install.sh [target_dir]
```

The script can be run from anywhere in the project. If no `target_dir` is provided, it defaults to `.bob`.

## What Gets Installed

### Skills
- **kaizen-learn**: Extract and save learnings from completed tasks
- **kaizen-recall**: Retrieve relevant guidelines before starting tasks

### Custom Modes
- **kaizen-lite**: A learning mode that automatically recalls guidelines at the start and saves learnings at the end of every task

## Installation Details

The `install.sh` script:
1. Copies all skill directories from the local `skills/` folder to the `skills/` subdirectory in your target
2. Merges this directory's `custom_modes.yaml` into your target's `custom_modes.yaml` (preserving existing custom modes)
3. Creates timestamped backups of existing files in the target directory
4. Verifies the installation

### After Installation

1. **Restart your agent** to load the new skills and modes
2. **Verify** skills appear in the skill menu
3. **Test** the kaizen-lite mode

## Directory Structure

```text
platform-integrations/bob/kaizen-lite/
├── install.sh              # Installation script
├── custom_modes.yaml       # Custom modes configuration
├── README.md               # This file
└── skills/                 # Skills directory
    ├── kaizen-learn/       # Learning skill
    │   ├── SKILL.md
    │   └── scripts/
    │       └── save.py
    └── kaizen-recall/      # Recall skill
        ├── SKILL.md
        └── scripts/
            └── get.py
```

## Usage

### Kaizen-Recall Skill

Retrieve guidelines before starting a task:

```bash
python3 .bob/skills/kaizen-recall/scripts/get.py --type guideline --task "your task description"
```

### Kaizen-Learn Skill

Save learnings after completing a task:

```bash
printf '{"entities": [...]}' | python3 .bob/skills/kaizen-learn/scripts/save.py
```

See individual SKILL.md files for detailed usage instructions.

## Kaizen-Lite Mode

The kaizen-lite mode enforces a mandatory workflow:

1. **Recall**: Retrieve guidelines at the start
2. **Work**: Complete the user's request
3. **Learn**: Save learnings before completion

This ensures continuous improvement through every interaction.

## Backup Files

The installation script creates timestamped backups:
```text
.bob/custom_modes.yaml.backup.YYYYMMDD_HHMMSS
```

To restore from a backup:
```bash
cp .bob/custom_modes.yaml.backup.YYYYMMDD_HHMMSS .bob/custom_modes.yaml
```

## Development

To add new skills:
1. Create a new directory in `platform-integrations/bob/kaizen-lite/skills/`
2. Add a `SKILL.md` file describing the skill
3. Add any scripts in a `scripts/` subdirectory
4. Run `./platform-integrations/bob/kaizen-lite/install.sh` to install

## Related Documentation

- [Kaizen Main README](../README.md)
- [Kaizen CLI Documentation](../CLI.md)
- [Kaizen Configuration](../CONFIGURATION.md)