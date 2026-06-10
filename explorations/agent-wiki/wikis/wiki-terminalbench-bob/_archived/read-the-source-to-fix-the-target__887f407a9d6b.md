---
id: 887f407a9d6b
type: guideline
trigger: Deciding how far to upgrade a library after a version-mismatch error in code you can inspect.
agent: bob
tags: [pandas, version-mismatch, diagnosis, source-reading, upgrade]
sources:
  - trajectories/be7c0ea4-openai-chat-completions.analysis.json
related_summary: summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md
---

# Read the source to fix the target version

Before choosing an upgrade target, read the failing module to inventory exactly which newer-version features it uses, then pick the version that introduced all of them. Here reading data_processor.py revealed `dtype_backend="pyarrow"`, `date_format="mixed"`, and copy-on-write usage — all pandas 2.0 features — confirming that `pandas>=2.0.0` (not some smaller bump) was the correct floor. Let the code's actual feature set, not the single error keyword, set the minimum version.

## Rationale

The first error names only one missing keyword, but the code may depend on several features from the same release wave; upgrading just past the first keyword can leave a second feature still unsupported and trigger another failure. Reading the source surfaces the full requirement set in one pass so the chosen version clears every dependency at once.

## Sources

- [trajectory summary](../summaries/be7c0ea4-f906-483a-82bc-4301ae3ef919.md)
- [normalized JSON](trajectories/be7c0ea4-openai-chat-completions.analysis.json)
