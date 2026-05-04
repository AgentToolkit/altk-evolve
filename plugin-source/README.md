# plugin-source/

Canonical source-of-truth for the per-platform plugin code under `platform-integrations/`.

Edit files **here**, not under `platform-integrations/`. The latter is generated
output, kept under version control for PR review and agent comprehension, and
enforced byte-for-byte against this directory by `just check-plugins-rendered`.

## Layout

- `MANIFEST.toml` — declares the configured platforms (each with a target
  `plugin_root` under `platform-integrations/`) and the list of files to render.
- `lib/` — Python helpers shared by skill scripts. Copied verbatim to each
  platform's plugin tree.
- *(future)* `skills/<name>/` — canonical skill content (templates rendered
  per-platform).
- *(future)* `platforms/<name>/` — per-platform overlay files for content that
  exists on only one platform or whose prose is too divergent to template.

## Workflow

Edit a source file → run `just compile-plugins` → commit both the source change
and the regenerated output. The pre-commit hook re-renders automatically.

To verify that the committed output is up-to-date with the source:

    just check-plugins-rendered

CI runs the same check.
