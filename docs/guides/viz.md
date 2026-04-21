# Evolve Viz

Evolve Viz is a local web server for browsing the entities and trajectories stored in a `.evolve/` directory. It lets you navigate from a guideline to the conversation that produced it, and from a trajectory to the guidelines extracted from it.

## Starting the server

```bash
evolve viz serve
```

By default this looks for `.evolve/` in the current directory and opens the browser automatically at `http://localhost:7891`.

```bash
# Custom directory or port
evolve viz serve --evolve-dir /path/to/project/.evolve --port 8080

# Don't open the browser automatically
evolve viz serve --no-browser
```

Press `Ctrl+C` to stop.

## Layout

The UI is a master-detail split pane:

- **Left sidebar** — two scrollable lists: Trajectories (newest first) and Guidelines
- **Right panel** — detail view for whichever item is selected

## Trajectories

Click any trajectory in the sidebar to open its detail view:

- Timestamp, model, message count
- Pills listing every guideline extracted from that session — click a pill to jump to that guideline
- Full chat transcript with user messages, assistant responses (including thinking blocks), and tool calls (click to expand args and result)

## Guidelines

Click any guideline in the sidebar to open its detail view:

- Content, rationale, and trigger context
- **Source trajectory** link — click to jump directly to the conversation that produced this guideline

## URLs and navigation

The URL reflects the current view using hash routing:

| View | URL |
|------|-----|
| Trajectory | `http://localhost:7891/#t/trajectory_2026-04-14T10-30-00.json` |
| Guideline  | `http://localhost:7891/#e/my-guideline-slug` |

This means:

- URLs are **bookmarkable** — paste one into a browser tab and it opens to that item
- Browser **back / forward** works as expected
- Right-click any sidebar item or link → **Open in New Tab**

## Data directory

The server reads from two subdirectories of `--evolve-dir`:

| Path | Contents |
|------|----------|
| `entities/` | Guideline markdown files (any depth) |
| `trajectories/` | Trajectory JSON files |

Entities are linked to their source trajectory via the `trajectory` frontmatter field, which the [learn skill](../integrations/claude/evolve-lite.md) sets automatically when saving guidelines.

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--evolve-dir, -d` | `.evolve` | Path to the `.evolve` directory |
| `--port, -p` | `7891` | Port to serve on |
| `--no-browser` | — | Skip opening the browser automatically |
