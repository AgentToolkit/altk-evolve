# License checks with Trivy

This workflow replaces dependency-submission/dependency-review with installed
package and container scans, adapted from [CUGA PR #978](https://github.com/cuga-project/cuga-agent/pull/978).
Trivy **0.76.0** is downloaded from its official release with committed Linux
amd64/arm64 SHA-256 checksums verified against the release asset digests. There
is no privileged `pull_request_target` workflow, write token, or dependency
snapshot submission.

## What runs

Every pull request, main push, and manual dispatch runs policy/planning tests,
three frozen Python installation profiles, the frontend scan, and an aggregate
`license-check` job. Python profiles are minimal core, the convenience package,
and all workspace packages with all optional extras (including hooks, PII,
secrets, and optional backends). No development dependency group is installed.
New uv workspace packages and extras join the optional profile automatically;
standalone projects outside that workspace need an explicit scan profile before
being distributed. Frontend `npm ci --ignore-scripts` includes build dependencies.

The independent inventory reads installed Python wheel metadata and npm packages,
including scoped/nested dependencies. It records shipped license-file hashes.
Trivy runs with `--list-all-pkgs`; any installed package missing from its output,
an empty inventory, invalid report, failed installation, or scanner failure
fails the job even in report mode. Packages with missing/unknown license metadata
and absent license files remain explicit findings, rather than disappearing
because Trivy emitted no severity finding. SPDX `AND`, `OR`, and `WITH` are
evaluated against the explicit approved expressions; Trivy severity is not the
policy decision. The invalid `BSD-0-Clause` spelling from the CUGA example is
omitted, and no CUGA package exceptions are copied.

## Conditional image checks

Native Linux amd64 and arm64 core images are built and scanned only for changes
to dependency manifests/lockfiles, Dockerfiles/ignore files, shared build scripts
and configuration, workflow/actions, or license tooling/policy. Ordinary Python
code, tests, and documentation edits do not trigger PR/main image builds. These
jobs include OS/package metadata and bundled license text (`--license-full`),
plus independent Python inventory from the exact built image. Source/model-weight
provenance and redistribution obligations still require human review; a license
text alone does not establish the licensing of an entire component.

The planner uses a complete local `git diff --no-renames` with full commit IDs,
NUL-delimited paths, and no API 300-file limit. PRs compare event base/head SHAs;
main pushes compare `before..after`. Deleted and renamed manifests are included.
A missing, zero, or unavailable main-push baseline conservatively runs images.
A PR diff failure fails planning. Manual dispatch always runs images. There are
no workflow-level path filters: the aggregate always exists and rejects failed
planning, invalid plan output, cancelled jobs, and unexpected skipped scans.
Only an image skip explicitly selected by a successful plan is accepted.

**Merge prerequisite:** [PR #445](https://github.com/AgentToolkit/altk-evolve/pull/445)
repairs `Dockerfile.core`'s package-workspace copy and MCP/FastEmbed runtime.
Merge that prerequisite first. This PR deliberately does not duplicate its
Dockerfile changes. Image jobs on a base without that fix may fail correctly.

## Policy rollout and required checks

The initial mode is `report` (`LICENSE_ENFORCEMENT` Actions variable unset).
Policy findings appear in job summaries and uploaded JSON artifacts but do not
block on licensing classification yet. This is **not license clearance**.
Technical failures and inventory coverage gaps always block.

On PRs the policy/baseline comes from the exact base commit; a PR cannot approve
its own findings through its proposed policy. The first introduction has no base
policy, so only report mode may use the proposed policy, explicitly identified
as bootstrap output. A partial base policy or an attempt to bootstrap enforcement
fails. On main/release the current committed policy is used.

Review the first Linux findings for each profile and architecture. To accept a
finding, add its unchanged fingerprint to `baseline.json` with `reason` and
`reviewer` (and optionally ISO-date `expires`); fingerprints include version,
scope, license expression, and license-file evidence. Exceptions must include
name, exact version, scope, issue, reason, reviewer and expiry. Set `reviewed` to
true only after review, then set `LICENSE_ENFORCEMENT=enforce`. Missing/expired
acceptances fail enforcement. Use maintainer review for policy, baseline,
scanner, evaluator and workflows; repository rules must enforce that review.

Require the single `license-check` context after validating the first GitHub
Actions runs. The cheap policy tests and aggregate run on every event. Hosted
ARM runners and enough disk for the optional ML dependency profile must be
available. Local unit tests do not substitute for a complete Linux CI run.

## Release images

The release workflow builds each architecture once into a Docker archive,
scans it with the same pinned Trivy/policy, and collects evidence from that image.
Only after both architectures pass does publication load and push those same
archives, verifying archive/config identities and creating release tags from
the pushed digests. There is no rebuild after scanning. Build/scan jobs receive
no registry credentials; credentials are confined to publication. Report-mode
release scans still expose pending findings without claiming license approval.

## Local validation

```sh
uv run --no-project --with pytest==8.4.2 python -m pytest --noconftest -c /dev/null -p no:cacheprovider -v tests/test_license_ci.py tests/test_image_promotion.py
uv run --no-project python scripts/licenses/check.py prepare-python core /tmp/evolve-license-core
uv run --no-project python scripts/licenses/check.py inventory python /tmp/evolve-license-core /tmp/inventory.json
# On Linux, install the pinned scanner using scripts/licenses/install-trivy.sh.
trivy rootfs --scanners license --list-all-pkgs --format json --output /tmp/trivy.json /tmp/evolve-license-core
uv run --no-project python scripts/licenses/check.py evaluate --report /tmp/trivy.json --inventory /tmp/inventory.json --scope python-core --mode report --output /tmp/policy-result.json
```

Vulnerability scanning remains separate. This workflow does not replace legal
review, nor does it automatically approve existing copyleft or unknown licenses.
