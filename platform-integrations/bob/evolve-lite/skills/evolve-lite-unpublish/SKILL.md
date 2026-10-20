---
name: evolve-lite:unpublish
description: Remove your published entity from a write-scope repository and restore a private copy.
---

# Unpublish an Entity

Read `evolve.config.yaml`. Select a write-scope repo and a published
`{type}/{filename}` owned by `identity.user`. Ask which repo if ambiguous.
Explain that unpublishing removes the current shared copy after pushing; it
does not erase git history or copies already downloaded by subscribers.

Run:

```bash
python3 .bob/skills/evolve-lite-unpublish/scripts/unpublish.py --entity "{filename}" --type "{type}" --repo "{repo}" --user "{identity.user}"
```

The script restores a private entity before removing the shared file. It refuses
unknown or different owners, and refuses to overwrite an existing private
mirror. If a mirror exists, compare the two and reconcile them with the user
before retrying; do not delete either copy blindly.

Stage only the selected deletion and publish it:

```bash
git -C ".evolve/entities/subscribed/{repo}" add -- "{type}/{filename}"
git -C ".evolve/entities/subscribed/{repo}" commit -m "[evolve] unpublish: {filename}"
git -C ".evolve/entities/subscribed/{repo}" push origin "{branch}"
```

Use the configured branch (default `main`). For a rejected push follow the
non-fast-forward recovery workflow in `evolve-lite:publish`. Report
success only after pushing. Retain the restored private copy on push failure.
